// M4 research benchmark.
//
// Compares the incremental prototype against a controlled "rebuild on every
// mutation" baseline built with the same code paths (CompactNow after each
// flush). The baseline is a proxy for the current whole-collection optimize,
// not a measurement of production zvec. It exists to show the scaling curve
// the architecture is designed to remove.
//
// Run: .work/build/m4_benchmark [sizes...] (set M4_BENCH_ROOT for data output)
// Output: one JSON object per case on stdout plus a markdown summary on stderr.

#include <atomic>
#include <fcntl.h>
#include <sys/resource.h>
#include <thread>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "m4/index.h"

#ifndef M4_REPO_TMP_DIR
#define M4_REPO_TMP_DIR ".tmp"
#endif

namespace fs = std::filesystem;
using m4::Document;
using m4::Index;
using m4::ScalarValue;

namespace {

double NowMs() {
  struct timespec ts {};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

double CpuMs() {
  struct timespec ts {};
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

struct IoCounters {
  uint64_t read_bytes{0};
  uint64_t write_bytes{0};
};

IoCounters ReadIo() {
  IoCounters out;
  std::ifstream in("/proc/self/io");
  std::string line;
  while (std::getline(in, line)) {
    if (line.rfind("read_bytes:", 0) == 0) {
      out.read_bytes = std::strtoull(line.c_str() + 11, nullptr, 10);
    } else if (line.rfind("write_bytes:", 0) == 0) {
      out.write_bytes = std::strtoull(line.c_str() + 12, nullptr, 10);
    }
  }
  return out;
}

uint64_t VmRssKb() {
  std::ifstream in("/proc/self/status");
  std::string line;
  while (std::getline(in, line)) {
    if (line.rfind("VmRSS:", 0) == 0) {
      return std::strtoull(line.c_str() + 6, nullptr, 10);
    }
  }
  return 0;
}

uint64_t PssNowKb() {
  std::ifstream in("/proc/self/smaps_rollup");
  std::string line;
  while (std::getline(in, line)) {
    if (line.rfind("Pss:", 0) == 0) {
      return std::strtoull(line.c_str() + 4, nullptr, 10);
    }
  }
  return 0;
}

class PssSampler {
 public:
  PssSampler() {
    peak_.store(PssNowKb());
    running_.store(true);
    thread_ = std::thread([this] {
      while (running_.load()) {
        const uint64_t p = PssNowKb();
        uint64_t prev = peak_.load();
        while (p > prev && !peak_.compare_exchange_weak(prev, p)) {
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
      }
    });
  }
  ~PssSampler() { Stop(); }
  void Stop() {
    if (running_.exchange(false)) {
      if (thread_.joinable()) thread_.join();
    }
  }
  uint64_t peak() const { return peak_.load(); }

 private:
  std::atomic<bool> running_{false};
  std::atomic<uint64_t> peak_{0};
  std::thread thread_;
};

uint64_t VmHwmKb() {
  std::ifstream in("/proc/self/status");
  std::string line;
  while (std::getline(in, line)) {
    if (line.rfind("VmHWM:", 0) == 0) {
      return std::strtoull(line.c_str() + 6, nullptr, 10);
    }
  }
  return 0;
}

std::string HostSnapshot() {
  std::ostringstream os;
  std::ifstream load("/proc/loadavg");
  std::string l;
  std::getline(load, l);
  os << "load=\"" << l << "\" ";
  std::ifstream mem("/proc/meminfo");
  std::string line;
  while (std::getline(mem, line)) {
    if (line.rfind("MemAvailable:", 0) == 0) {
      os << line << " ";
      break;
    }
  }
  std::ifstream psi("/proc/pressure/memory");
  while (std::getline(psi, line)) {
    os << line << " ";
  }
  return os.str();
}

size_t DirBytes(const std::string& dir) {
  size_t total = 0;
  std::error_code ec;
  for (const auto& e : fs::recursive_directory_iterator(dir, ec)) {
    if (e.is_regular_file(ec)) total += e.file_size(ec);
  }
  return total;
}

double Percentile(std::vector<double> v, double p) {
  if (v.empty()) return 0.0;
  std::sort(v.begin(), v.end());
  const size_t idx = static_cast<size_t>(std::min<double>(
      v.size() - 1, std::max(0.0, p * (v.size() - 1))));
  return v[idx];
}

struct CorpusDoc {
  std::string pk;
  std::string text;
  std::vector<ScalarValue> values;
};

std::string MakeText(std::mt19937* rng, int vocab, int min_len, int max_len) {
  std::uniform_int_distribution<int> len(min_len, max_len);
  const int n = len(*rng);
  std::string out;
  out.reserve(static_cast<size_t>(n) * 6);
  for (int i = 0; i < n; ++i) {
    int w = static_cast<int>((*rng)() % vocab);
    // Skew: most tokens come from a common head, similar to natural text.
    if ((*rng)() % 100 < 70) w = static_cast<int>((*rng)() % 128);
    if (i) out.push_back(' ');
    out += "w" + std::to_string(w);
  }
  return out;
}

CorpusDoc MakeDoc(std::mt19937* rng, int vocab, const std::string& pk) {
  CorpusDoc d;
  d.pk = pk;
  d.text = MakeText(rng, vocab, 20, 60);
  const char* statuses[] = {"active", "pending", "done"};
  const char* regions[] = {"a", "b", "c"};
  d.values.push_back(ScalarValue{"status", statuses[(*rng)() % 3]});
  d.values.push_back(ScalarValue{"region", regions[(*rng)() % 3]});
  return d;
}

Document ToDocument(const CorpusDoc& d) {
  Document doc;
  doc.text = d.text;
  doc.values = d.values;
  return doc;
}

struct CaseResult {
  std::string mode;
  size_t base_docs{0};
  size_t updates{0};
  double build_wall_ms{0};
  double build_cpu_ms{0};
  double update_wall_p50{0};
  double update_wall_p95{0};
  double update_cpu_p50{0};
  double update_cpu_p95{0};
  double update_wall_total{0};
  double update_cpu_total{0};
  uint64_t update_bytes{0};
  uint64_t total_phys_write_bytes{0};
  uint64_t prototype_write_bytes{0};
  double compaction_wall_total_ms{0};
  uint64_t compaction_merges_total{0};
  uint64_t update_logical_bytes{0};
  uint64_t disk_bytes{0};
  uint64_t runs{0};
  uint64_t compactions{0};
  uint64_t live_docs{0};
  uint64_t rss_kb{0};
  uint64_t hwm_kb{0};
  uint64_t pss_peak_kb{0};
  uint64_t pss_end_kb{0};
  double compaction_wall_ms{0};
  uint64_t compaction_merges{0};
  uint64_t compaction_input_docs{0};
  uint64_t compaction_output_docs{0};
  uint64_t compaction_peak_bytes{0};
  double query_p50{0};
  double query_p95{0};
  uint64_t query_hits{0};
  bool ok{true};
  std::string error;
};

CaseResult RunCase(size_t base_docs, int updates, bool rebuild_baseline,
                   const std::string& root, int vocab) {
  CaseResult r;
  r.mode = rebuild_baseline ? "rebuild_on_mutation" : "incremental";
  r.base_docs = base_docs;
  r.updates = updates;
  const std::string dir = root + "/" + r.mode + "-" + std::to_string(base_docs);
  fs::remove_all(dir);

  m4::Options opt;
  opt.max_delta_docs = 1000;
  opt.max_delta_bytes = 32ull << 20;
  opt.max_runs_per_level = 4;
  opt.max_total_runs = 12;
  opt.stats_log_threshold = 4ull << 20;
  opt.fsync_wal = false;

  const IoCounters io_at_start = ReadIo();
  std::mt19937 rng(20261017 + base_docs);
  std::vector<CorpusDoc> corpus;
  corpus.reserve(base_docs);
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  if (!s.ok) {
    r.ok = false;
    r.error = s.message;
    return r;
  }

  const double build_start = NowMs();
  const double build_cpu_start = CpuMs();
  for (size_t i = 0; i < base_docs; ++i) {
    CorpusDoc d = MakeDoc(&rng, vocab, "doc-" + std::to_string(i));
    corpus.push_back(d);
    m4::Status us = index->Upsert(d.pk, ToDocument(d));
    if (!us.ok) {
      r.ok = false;
      r.error = us.message;
      return r;
    }
  }
  s = index->Flush();
  if (!s.ok) {
    r.ok = false;
    r.error = s.message;
    return r;
  }
  r.build_wall_ms = NowMs() - build_start;
  r.build_cpu_ms = CpuMs() - build_cpu_start;

  // Start both modes from one run so update measurements are comparable.
  s = index->CompactNow();
  if (!s.ok) {
    r.ok = false;
    r.error = s.message;
    return r;
  }

  const auto stats_before = index->GetStats();
  const IoCounters io_before = ReadIo();
  std::vector<double> wall;
  std::vector<double> cpu;
  for (int u = 0; u < updates; ++u) {
    // Update an existing document in place (same key, new content).
    const size_t pick = static_cast<size_t>(rng() % corpus.size());
    CorpusDoc next = MakeDoc(&rng, vocab, corpus[pick].pk);
    corpus[pick] = next;
    const double w0 = NowMs();
    const double c0 = CpuMs();
    m4::Status us = index->Upsert(next.pk, ToDocument(next));
    if (!us.ok) {
      r.ok = false;
      r.error = us.message;
      return r;
    }
    us = index->Flush();
    if (!us.ok) {
      r.ok = false;
      r.error = us.message;
      return r;
    }
    if (rebuild_baseline) {
      us = index->CompactNow();
      if (!us.ok) {
        r.ok = false;
        r.error = us.message;
        return r;
      }
    }
    wall.push_back(NowMs() - w0);
    cpu.push_back(CpuMs() - c0);
  }
  const auto stats_after = index->GetStats();
  const IoCounters io_after = ReadIo();
  r.compaction_wall_ms =
      stats_after.compaction_wall_ms - stats_before.compaction_wall_ms;
  r.compaction_merges =
      stats_after.compaction_merges - stats_before.compaction_merges;
  r.compaction_input_docs = stats_after.compaction_input_docs -
                            stats_before.compaction_input_docs;
  r.compaction_output_docs = stats_after.compaction_output_docs -
                             stats_before.compaction_output_docs;
  r.compaction_peak_bytes = stats_after.compaction_peak_bytes;
  r.compaction_wall_total_ms = stats_after.compaction_wall_ms;
  r.compaction_merges_total = stats_after.compaction_merges;

  r.update_wall_p50 = Percentile(wall, 0.50);
  r.update_wall_p95 = Percentile(wall, 0.95);
  r.update_cpu_p50 = Percentile(cpu, 0.50);
  r.update_cpu_p95 = Percentile(cpu, 0.95);
  for (double x : wall) r.update_wall_total += x;
  for (double x : cpu) r.update_cpu_total += x;
  r.update_bytes =
      (io_after.write_bytes - io_before.write_bytes) +
      (stats_after.bytes_written - stats_before.bytes_written);
  // Prototype counter and /proc may both include the same writes; the counter
  // is reported separately in the raw stats line. Prefer /proc for phys IO.
  r.update_bytes = io_after.write_bytes - io_before.write_bytes;
  // /proc/self/io does not count tmpfs writes; use the prototype's write
  // counter, which covers segment/meta files and streaming temp sections.
  (void)io_at_start;
  r.prototype_write_bytes = stats_after.bytes_written;
  r.total_phys_write_bytes = io_after.write_bytes - io_at_start.write_bytes;
  if (r.update_bytes == 0) {
    r.update_bytes = stats_after.bytes_written - stats_before.bytes_written;
  }
  for (const auto& d : corpus) {
    r.update_logical_bytes += d.pk.size() + d.text.size();
    for (const auto& v : d.values) {
      r.update_logical_bytes += v.field.size() + v.value.size();
    }
  }

  // Query phase: single-term and two-term AND queries.
  std::vector<double> qlat;
  std::uniform_int_distribution<int> word(0, 255);
  for (int q = 0; q < 160; ++q) {
    const std::string term = "w" + std::to_string(word(rng));
    const std::string query =
        (q % 3 == 0) ? term + " AND w" + std::to_string(word(rng)) : term;
    m4::SearchResult sr;
    const double t0 = NowMs();
    m4::Status qs = index->SearchFts(query, 10, &sr);
    qlat.push_back(NowMs() - t0);
    if (!qs.ok) {
      r.ok = false;
      r.error = qs.message;
      return r;
    }
    r.query_hits += sr.hits.size();
  }
  r.query_p50 = Percentile(qlat, 0.50);
  r.query_p95 = Percentile(qlat, 0.95);

  r.disk_bytes = DirBytes(dir);
  const auto st = index->GetStats();
  r.runs = st.runs;
  r.compactions = st.compactions;
  r.live_docs = st.live_docs;
  r.rss_kb = VmRssKb();
  r.hwm_kb = VmHwmKb();
  r.pss_end_kb = PssNowKb();
  return r;
}

std::string JsonEscape(const std::string& s) {
  std::string out;
  for (char c : s) {
    if (c == '"' || c == '\\') out.push_back('\\');
    out.push_back(c);
  }
  return out;
}

void PrintJson(const CaseResult& r) {
  std::printf(
      "{\"mode\":\"%s\",\"base_docs\":%zu,\"updates\":%zu,"
      "\"ok\":%s,\"error\":\"%s\","
      "\"build_wall_ms\":%.1f,\"build_cpu_ms\":%.1f,"
      "\"update_wall_p50_ms\":%.3f,\"update_wall_p95_ms\":%.3f,"
      "\"update_cpu_p50_ms\":%.3f,\"update_cpu_p95_ms\":%.3f,"
      "\"update_wall_total_ms\":%.1f,\"update_cpu_total_ms\":%.1f,"
      "\"update_phys_write_bytes\":%llu,\"total_phys_write_bytes\":%llu,"
      "\"prototype_write_bytes\":%llu,\"logical_bytes\":%llu,"
      "\"disk_bytes\":%llu,\"runs\":%llu,\"compactions\":%llu,"
      "\"live_docs\":%llu,\"rss_kb\":%llu,\"hwm_kb\":%llu,"
      "\"pss_peak_kb\":%llu,\"pss_end_kb\":%llu,"
      "\"compaction_wall_ms\":%.1f,\"compaction_merges\":%llu,"
      "\"compaction_input_docs\":%llu,\"compaction_output_docs\":%llu,"
      "\"compaction_peak_bytes\":%llu,"
      "\"compaction_wall_total_ms\":%.1f,\"compaction_merges_total\":%llu,"
      "\"query_p50_ms\":%.3f,\"query_p95_ms\":%.3f,\"query_hits\":%llu}\n",
      r.mode.c_str(), r.base_docs, r.updates, r.ok ? "true" : "false",
      JsonEscape(r.error).c_str(), r.build_wall_ms, r.build_cpu_ms,
      r.update_wall_p50, r.update_wall_p95, r.update_cpu_p50,
      r.update_cpu_p95, r.update_wall_total, r.update_cpu_total,
      (unsigned long long)r.update_bytes,
      (unsigned long long)r.total_phys_write_bytes,
      (unsigned long long)r.prototype_write_bytes,
      (unsigned long long)r.update_logical_bytes,
      (unsigned long long)r.disk_bytes, (unsigned long long)r.runs,
      (unsigned long long)r.compactions, (unsigned long long)r.live_docs,
      (unsigned long long)r.rss_kb, (unsigned long long)r.hwm_kb,
      (unsigned long long)r.pss_peak_kb, (unsigned long long)r.pss_end_kb,
      r.compaction_wall_ms, (unsigned long long)r.compaction_merges,
      (unsigned long long)r.compaction_input_docs,
      (unsigned long long)r.compaction_output_docs,
      (unsigned long long)r.compaction_peak_bytes,
      r.compaction_wall_total_ms,
      (unsigned long long)r.compaction_merges_total,
      r.query_p50, r.query_p95, (unsigned long long)r.query_hits);
}


struct ChurnResult {
  size_t base_docs{0};
  size_t cycles{0};
  size_t ops_per_cycle{0};
  bool ok{false};
  std::string error;
  double build_wall_ms{0};
  double build_cpu_ms{0};
  double churn_wall_ms{0};
  double churn_cpu_ms{0};
  double total_cpu_ms{0};
  double reopen_ms{0};
  double query_p50{0};
  double query_p95{0};
  double query_p99{0};
  double post_compact_p50{0};
  double post_compact_p95{0};
  double post_compact_p99{0};
  std::vector<double> cycle_p95;
  std::vector<double> cycle_p99;
  std::vector<uint64_t> cycle_runs;
  std::vector<uint64_t> cycle_dead;
  std::vector<uint64_t> cycle_disk;
  std::vector<uint64_t> cycle_compactions;
  std::vector<double> cycle_runs_read;
  std::vector<double> cycle_run_parts;
  std::vector<double> cycle_postings;
  std::vector<double> cycle_dead_postings;
  std::vector<double> cycle_liveness;
  std::vector<double> cycle_candidates;
  std::vector<double> cycle_setup_ms;
  std::vector<double> cycle_exec_ms;
  std::vector<double> cycle_finalize_ms;
  std::vector<double> cycle_update_p95;
  std::vector<uint64_t> cycle_logical_writes;
  std::vector<uint64_t> cycle_physical_writes;
  double update_p50{0};
  double update_p95{0};
  std::string policy{"off"};
  uint64_t live_docs{0};
  uint64_t dead_docs{0};
  uint64_t runs{0};
  uint64_t compactions{0};
  uint64_t compaction_merges{0};
  uint64_t compaction_peak_bytes{0};
  double compaction_wall_ms{0};
  uint64_t logical_writes{0};
  uint64_t physical_writes{0};
  uint64_t total_logical_writes{0};
  uint64_t total_physical_writes{0};
  uint64_t disk_bytes{0};
  uint64_t segments_bytes{0};
  uint64_t tombstone_bytes{0};
  uint64_t stats_log_bytes{0};
  uint64_t wal_bytes{0};
  uint64_t query_hits{0};
  uint64_t pss_peak_kb{0};
  bool liveness_ok{false};
};

Document MakeChurnDoc(std::mt19937* rng, int vocab, const std::string& pk) {
  (void)pk;
  Document d;
  d.text = "common " + MakeText(rng, vocab, 20, 60);
  d.values.push_back(ScalarValue{"status", "active"});
  const char* regions[] = {"a", "b", "c"};
  d.values.push_back(ScalarValue{"region", regions[(*rng)() % 3]});
  return d;
}

ChurnResult RunChurn(size_t base_docs, int cycles, int ops_per_cycle,
                     const std::string& root, int vocab, int query_count) {
  ChurnResult r;
  r.ok = true;
  r.base_docs = base_docs;
  r.cycles = static_cast<size_t>(std::max(0, cycles));
  r.ops_per_cycle = static_cast<size_t>(std::max(0, ops_per_cycle));
  fs::remove_all(root);
  fs::create_directories(root);

  m4::Options opt;
  opt.max_delta_docs = 8192;
  opt.max_delta_bytes = 64ull << 20;
  opt.max_runs_per_level = 4;
  opt.max_total_runs = 12;
  opt.tombstone_compact_ratio = 0.30;
  opt.stats_log_threshold = 1ull << 20;
  // Large churn focuses on compaction/query behavior; WAL durability is
  // covered with fsync enabled by the correctness gate.
  opt.fsync_wal = false;
  const bool policy_on = std::getenv("M4_DEBT_POLICY") != nullptr;
  // Build the base with policy OFF; the comparison is about maintenance on a
  // steady-state corpus, not about changing bulk-load behavior.
  opt.debt_policy_enabled = false;
  if (const char* v = std::getenv("M4_DEBT_RATIO")) {
    opt.debt_compact_ratio = std::strtod(v, nullptr);
  }
  if (const char* v = std::getenv("M4_DEBT_MIN_RUNS")) {
    opt.debt_min_runs = static_cast<uint32_t>(std::strtoul(v, nullptr, 10));
  }
  if (const char* v = std::getenv("M4_DEBT_CAP")) {
    opt.debt_max_merge_docs = std::strtoull(v, nullptr, 10);
  }
  r.policy = policy_on ? "on" : "off";

  std::mt19937 rng(20261017);
  std::unique_ptr<Index> index;
  const IoCounters io_start = ReadIo();
  const double t0 = NowMs();
  const double c0 = CpuMs();
  m4::Status s = Index::Open(root, opt, &index);
  if (!s.ok) {
    r.error = "open: " + s.message;
    return r;
  }

  std::vector<std::string> live;
  live.reserve(base_docs);
  for (size_t i = 0; i < base_docs; ++i) {
    const std::string pk = "b-" + std::to_string(i);
    s = index->Upsert(pk, MakeChurnDoc(&rng, vocab, pk));
    if (!s.ok) {
      r.error = "base upsert: " + s.message;
      return r;
    }
    live.push_back(pk);
  }
  s = index->Flush();
  if (!s.ok) {
    r.error = "base flush: " + s.message;
    return r;
  }
  s = index->CompactNow();
  if (!s.ok) {
    r.error = "base compact: " + s.message;
    return r;
  }
  const double t1 = NowMs();
  const double c1 = CpuMs();
  r.build_wall_ms = t1 - t0;
  r.build_cpu_ms = c1 - c0;
  if (policy_on) {
    // Reopen after the base compaction so the policy only sees churn runs.
    index.reset();
    opt.debt_policy_enabled = true;
    s = Index::Open(root, opt, &index);
    if (!s.ok) {
      r.error = "policy reopen: " + s.message;
      r.ok = false;
      return r;
    }
  }
  const IoCounters io_before = ReadIo();
  const auto stats_before = index->GetStats();

  auto measure_queries = [&](uint64_t* hits, m4::QueryDiagnostics* avg) {
    std::vector<double> lat;
    lat.reserve(static_cast<size_t>(query_count));
    std::uniform_int_distribution<int> word(0, 127);
    m4::QueryDiagnostics total;
    for (int q = 0; q < query_count; ++q) {
      const std::string term = "w" + std::to_string(word(rng));
      const std::string query = (q % 4 == 0)
                                    ? "common AND " + term
                                    : term;
      m4::SearchResult sr;
      m4::QueryDiagnostics d;
      const double q0 = NowMs();
      m4::Status qs = index->SearchFts(query, 10, &sr, &d);
      lat.push_back(NowMs() - q0);
      if (!qs.ok) {
        r.ok = false;
        r.error = "query: " + qs.message;
        return;
      }
      total.runs_read += d.runs_read;
      total.run_term_parts += d.run_term_parts;
      total.postings_scanned += d.postings_scanned;
      total.dead_postings_filtered += d.dead_postings_filtered;
      total.liveness_lookups += d.liveness_lookups;
      total.candidates += d.candidates;
      total.setup_ms += d.setup_ms;
      total.execution_ms += d.execution_ms;
      total.finalize_ms += d.finalize_ms;
      total.examined += d.examined;
      if (hits) *hits += sr.hits.size();
    }
    if (avg) {
      *avg = total;
      const double denom = static_cast<double>(std::max(1, query_count));
      avg->runs_read = static_cast<uint64_t>(total.runs_read / denom);
      avg->run_term_parts = static_cast<uint64_t>(total.run_term_parts / denom);
      avg->postings_scanned =
          static_cast<uint64_t>(total.postings_scanned / denom);
      avg->dead_postings_filtered =
          static_cast<uint64_t>(total.dead_postings_filtered / denom);
      avg->liveness_lookups =
          static_cast<uint64_t>(total.liveness_lookups / denom);
      avg->candidates = static_cast<uint64_t>(total.candidates / denom);
      avg->examined = static_cast<uint64_t>(total.examined / denom);
      avg->setup_ms = total.setup_ms / denom;
      avg->execution_ms = total.execution_ms / denom;
      avg->finalize_ms = total.finalize_ms / denom;
    }
    r.query_p50 = Percentile(lat, 0.50);
    r.query_p95 = Percentile(lat, 0.95);
    r.query_p99 = Percentile(lat, 0.99);
  };

  auto check_liveness = [&](const char* label, bool full_count) {
    if (!full_count) return;
    m4::SearchResult sr;
    m4::Status qs = index->SearchFts("common", static_cast<uint32_t>(
                                               std::min<size_t>(live.size() + 16,
                                                                1000000)),
                                     &sr);
    if (!qs.ok || sr.hits.size() != live.size()) {
      r.ok = false;
      r.error = std::string(label) + ": common count " +
                std::to_string(sr.hits.size()) + " vs " +
                std::to_string(live.size());
      return;
    }
    r.liveness_ok = true;
  };

  uint64_t query_hits = 0;
  std::vector<double> update_lat;
  m4::QueryDiagnostics query_diag;
  measure_queries(&query_hits, &query_diag);
  check_liveness("initial", true);

  for (int cycle = 0; cycle < cycles && r.ok; ++cycle) {
    const auto cycle_stats_before = index->GetStats();
    const IoCounters cycle_io_before = ReadIo();
    const size_t update_lat_start = update_lat.size();
    const size_t deletes = static_cast<size_t>(ops_per_cycle) / 3;
    const size_t updates = static_cast<size_t>(ops_per_cycle) - deletes;
    uint64_t next_pk = static_cast<uint64_t>(base_docs) +
                       static_cast<uint64_t>(cycle) * deletes;
    for (size_t i = 0; i < deletes; ++i) {
      const size_t idx = static_cast<size_t>(rng() % live.size());
      const std::string old_pk = live[idx];
      s = index->Delete(old_pk);
      if (!s.ok) {
        r.error = "delete: " + s.message;
        r.ok = false;
        break;
      }
      live[idx] = live.back();
      live.pop_back();
      const std::string pk = "n-" + std::to_string(next_pk++);
      s = index->Upsert(pk, MakeChurnDoc(&rng, vocab, pk));
      if (!s.ok) {
        r.error = "insert: " + s.message;
        r.ok = false;
        break;
      }
      live.push_back(pk);
    }
    if (!r.ok) break;
    for (size_t i = 0; i < updates; ++i) {
      const std::string& pk = live[static_cast<size_t>(rng() % live.size())];
      const double u0 = NowMs();
      s = index->Upsert(pk, MakeChurnDoc(&rng, vocab, pk));
      update_lat.push_back(NowMs() - u0);
      if (!s.ok) {
        r.error = "update: " + s.message;
        r.ok = false;
        break;
      }
    }
    if (!r.ok) break;
    s = index->Flush();
    if (!s.ok) {
      r.error = "cycle flush: " + s.message;
      r.ok = false;
      break;
    }
    measure_queries(&query_hits, &query_diag);
    const auto st = index->GetStats();
    const auto cycle_stats_after = st;
    const IoCounters cycle_io_after = ReadIo();
    std::vector<double> cycle_updates(update_lat.begin() + update_lat_start,
                                      update_lat.end());
    r.cycle_update_p95.push_back(Percentile(cycle_updates, 0.95));
    r.cycle_logical_writes.push_back(
        cycle_stats_after.bytes_written - cycle_stats_before.bytes_written);
    r.cycle_physical_writes.push_back(
        cycle_io_after.write_bytes - cycle_io_before.write_bytes);
    r.cycle_runs_read.push_back(static_cast<double>(query_diag.runs_read));
    r.cycle_run_parts.push_back(static_cast<double>(query_diag.run_term_parts));
    r.cycle_postings.push_back(
        static_cast<double>(query_diag.postings_scanned));
    r.cycle_dead_postings.push_back(
        static_cast<double>(query_diag.dead_postings_filtered));
    r.cycle_liveness.push_back(
        static_cast<double>(query_diag.liveness_lookups));
    r.cycle_candidates.push_back(static_cast<double>(query_diag.candidates));
    r.cycle_setup_ms.push_back(query_diag.setup_ms);
    r.cycle_exec_ms.push_back(query_diag.execution_ms);
    r.cycle_finalize_ms.push_back(query_diag.finalize_ms);
    r.cycle_p95.push_back(r.query_p95);
    r.cycle_p99.push_back(r.query_p99);
    r.cycle_runs.push_back(st.runs);
    r.cycle_dead.push_back(st.dead_docs);
    r.cycle_disk.push_back(DirBytes(root));
    r.cycle_compactions.push_back(st.compactions);
    if ((cycle + 1) % 4 == 0) check_liveness("mid-churn", true);
  }

  const double t2 = NowMs();
  const double c2 = CpuMs();
  r.churn_wall_ms = t2 - t1;
  r.churn_cpu_ms = c2 - c1;
  r.total_cpu_ms = c2 - c0;
  if (r.ok) {
    // Drain compaction debt and verify the fully-merged state.
    const double m0 = NowMs();
    s = index->CompactNow();
    if (!s.ok) {
      r.error = "final compact: " + s.message;
      r.ok = false;
    } else {
      r.compaction_wall_ms = NowMs() - m0;
      check_liveness("final", true);
      measure_queries(&query_hits, &query_diag);
      r.post_compact_p50 = r.query_p50;
      r.post_compact_p95 = r.query_p95;
      r.post_compact_p99 = r.query_p99;
      const auto st = index->GetStats();
      if (st.dead_docs != 0 || st.runs != 1) {
        r.error = "final state: dead=" + std::to_string(st.dead_docs) +
                  " runs=" + std::to_string(st.runs);
        r.ok = false;
      }
    }
  }

  const auto stats_after = index->GetStats();
  const IoCounters io_after = ReadIo();
  r.live_docs = stats_after.live_docs;
  r.dead_docs = stats_after.dead_docs;
  r.runs = stats_after.runs;
  r.compactions = stats_after.compactions;
  r.compaction_merges = stats_after.compaction_merges;
  r.compaction_peak_bytes = stats_after.compaction_peak_bytes;
  if (r.compaction_wall_ms == 0) {
    r.compaction_wall_ms = stats_after.compaction_wall_ms -
                           stats_before.compaction_wall_ms;
  }
  r.logical_writes = stats_after.bytes_written - stats_before.bytes_written;
  r.physical_writes = io_after.write_bytes - io_before.write_bytes;
  r.total_logical_writes = stats_after.bytes_written;
  r.total_physical_writes = io_after.write_bytes - io_start.write_bytes;
  r.disk_bytes = DirBytes(root);
  r.segments_bytes = stats_after.segments_bytes;
  r.tombstone_bytes = stats_after.tombstone_bytes;
  r.stats_log_bytes = stats_after.stats_log_bytes;
  r.wal_bytes = stats_after.wal_bytes;
  r.query_hits = query_hits;
  r.update_p50 = Percentile(update_lat, 0.50);
  r.update_p95 = Percentile(update_lat, 0.95);

  index.reset();
  const double re0 = NowMs();
  std::unique_ptr<Index> reopened;
  s = Index::Open(root, opt, &reopened);
  r.reopen_ms = NowMs() - re0;
  if (!s.ok) {
    r.error = "reopen: " + s.message;
    r.ok = false;
  } else {
    const auto st = reopened->GetStats();
    if (st.live_docs != r.live_docs || st.dead_docs != 0) {
      r.error = "reopen state: live=" + std::to_string(st.live_docs) +
                " dead=" + std::to_string(st.dead_docs);
      r.ok = false;
    }
  }
  return r;
}

void PrintChurnJson(const ChurnResult& r) {
  auto arr = [](const std::vector<double>& v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); ++i) {
      if (i) s += ",";
      s += std::to_string(v[i]);
    }
    return s + "]";
  };
  auto arr_u64 = [](const std::vector<uint64_t>& v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); ++i) {
      if (i) s += ",";
      s += std::to_string(v[i]);
    }
    return s + "]";
  };
  std::printf(
      "{\"mode\":\"churn\",\"base_docs\":%zu,\"cycles\":%zu,"
      "\"ops_per_cycle\":%zu,\"ok\":%s,\"error\":\"%s\","
      "\"build_wall_ms\":%.1f,\"build_cpu_ms\":%.1f,"
      "\"churn_wall_ms\":%.1f,\"churn_cpu_ms\":%.1f,"
      "\"total_cpu_ms\":%.1f,\"reopen_ms\":%.1f,"
      "\"policy\":\"%s\","
      "\"query_p50_ms\":%.3f,\"query_p95_ms\":%.3f,\"query_p99_ms\":%.3f,"
      "\"post_compact_p50_ms\":%.3f,\"post_compact_p95_ms\":%.3f,"
      "\"post_compact_p99_ms\":%.3f,"
      "\"cycle_p95\":%s,\"cycle_p99\":%s,\"cycle_runs\":%s,"
      "\"cycle_dead\":%s,\"cycle_disk\":%s,\"cycle_compactions\":%s,"
      "\"cycle_runs_read\":%s,\"cycle_run_parts\":%s,"
      "\"cycle_postings\":%s,\"cycle_dead_postings\":%s,"
      "\"cycle_liveness\":%s,\"cycle_candidates\":%s,"
      "\"cycle_setup_ms\":%s,\"cycle_exec_ms\":%s,"
      "\"cycle_finalize_ms\":%s,"
      "\"cycle_update_p95\":%s,\"cycle_logical_writes\":%s,"
      "\"cycle_physical_writes\":%s,\"update_p50_ms\":%.3f,"
      "\"update_p95_ms\":%.3f,"
      "\"live_docs\":%llu,\"dead_docs\":%llu,\"runs\":%llu,"
      "\"compactions\":%llu,\"compaction_merges\":%llu,"
      "\"compaction_peak_bytes\":%llu,\"compaction_wall_ms\":%.1f,"
      "\"logical_writes\":%llu,\"physical_writes\":%llu,"
      "\"total_logical_writes\":%llu,\"total_physical_writes\":%llu,"
      "\"disk_bytes\":%llu,\"segments_bytes\":%llu,\"tombstone_bytes\":%llu,"
      "\"stats_log_bytes\":%llu,\"wal_bytes\":%llu,"
      "\"query_hits\":%llu,\"pss_peak_kb\":%llu,"
      "\"liveness_ok\":%s}\n",
      r.base_docs, r.cycles, r.ops_per_cycle, r.ok ? "true" : "false",
      JsonEscape(r.error).c_str(), r.build_wall_ms, r.build_cpu_ms,
      r.churn_wall_ms, r.churn_cpu_ms, r.total_cpu_ms, r.reopen_ms,
      r.policy.c_str(), r.query_p50, r.query_p95, r.query_p99,
      r.post_compact_p50, r.post_compact_p95, r.post_compact_p99,
      arr(r.cycle_p95).c_str(), arr(r.cycle_p99).c_str(),
      arr_u64(r.cycle_runs).c_str(), arr_u64(r.cycle_dead).c_str(),
      arr_u64(r.cycle_disk).c_str(), arr_u64(r.cycle_compactions).c_str(),
      arr(r.cycle_runs_read).c_str(), arr(r.cycle_run_parts).c_str(),
      arr(r.cycle_postings).c_str(),
      arr(r.cycle_dead_postings).c_str(),
      arr(r.cycle_liveness).c_str(), arr(r.cycle_candidates).c_str(),
      arr(r.cycle_setup_ms).c_str(), arr(r.cycle_exec_ms).c_str(),
      arr(r.cycle_finalize_ms).c_str(), arr(r.cycle_update_p95).c_str(),
      arr_u64(r.cycle_logical_writes).c_str(),
      arr_u64(r.cycle_physical_writes).c_str(), r.update_p50, r.update_p95,
      (unsigned long long)r.live_docs,
      (unsigned long long)r.dead_docs, (unsigned long long)r.runs,
      (unsigned long long)r.compactions,
      (unsigned long long)r.compaction_merges,
      (unsigned long long)r.compaction_peak_bytes, r.compaction_wall_ms,
      (unsigned long long)r.logical_writes,
      (unsigned long long)r.physical_writes,
      (unsigned long long)r.total_logical_writes,
      (unsigned long long)r.total_physical_writes,
      (unsigned long long)r.disk_bytes,
      (unsigned long long)r.segments_bytes,
      (unsigned long long)r.tombstone_bytes,
      (unsigned long long)r.stats_log_bytes,
      (unsigned long long)r.wal_bytes, (unsigned long long)r.query_hits,
      (unsigned long long)r.pss_peak_kb,
      r.liveness_ok ? "true" : "false");
}

} // namespace

int main(int argc, char** argv) {
  PssSampler sampler;
  if (argc >= 5 && std::string(argv[1]) == "--churn") {
    const size_t docs = std::strtoull(argv[2], nullptr, 10);
    const int cycles = std::atoi(argv[3]);
    const int ops = std::atoi(argv[4]);
    const char* root_env = std::getenv("M4_BENCH_ROOT");
    const std::string root = root_env ? root_env : (fs::path(M4_REPO_TMP_DIR) / "m4-research-churn").string();
    ChurnResult r = RunChurn(docs, cycles, ops, root, 5000, 160);
    r.pss_peak_kb = sampler.peak();
    PrintChurnJson(r);
    sampler.Stop();
    return r.ok ? 0 : 1;
  }
  if (argc >= 5 && std::string(argv[1]) == "--one") {
    const bool rebuild = std::string(argv[2]) == "rebuild";
    const size_t docs = std::strtoull(argv[3], nullptr, 10);
    const int updates = std::atoi(argv[4]);
    const char *root_env = std::getenv("M4_BENCH_ROOT");
    const std::string root =
        root_env
            ? root_env
            : (fs::path(M4_REPO_TMP_DIR) / "m4-research-bench-one").string();
    fs::remove_all(root);
    fs::create_directories(root);
    CaseResult r = RunCase(docs, updates, rebuild, root, 5000);
    r.pss_peak_kb = sampler.peak();
    PrintJson(r);
    sampler.Stop();
    return r.ok ? 0 : 1;
  }
  const std::string root =
      (fs::path(M4_REPO_TMP_DIR) / "m4-research-bench").string();
  fs::remove_all(root);
  fs::create_directories(root);
  std::fprintf(stderr, "host before: %s\n", HostSnapshot().c_str());

  std::vector<size_t> sizes = {10000, 50000, 150000};
  if (argc > 1) {
    sizes.clear();
    for (int i = 1; i < argc; ++i)
      sizes.push_back(std::strtoull(argv[i], nullptr, 10));
  }
  const int vocab = 5000;
  std::vector<CaseResult> results;
  for (size_t n : sizes) {
    CaseResult inc = RunCase(n, 12, false, root, vocab);
    inc.pss_peak_kb = sampler.peak();
    results.push_back(inc);
    PrintJson(inc);
    std::fprintf(stderr,
                 "incremental n=%zu ok=%d wall_p50=%.2fms cpu_p50=%.2fms\n", n,
                 inc.ok ? 1 : 0, inc.update_wall_p50, inc.update_cpu_p50);
    std::fflush(stdout);
  }
  // Rebuild baseline is intentionally limited to smaller sizes; it is
  // O(collection) per mutation by construction.
  for (size_t n : {size_t(10000), size_t(50000)}) {
    if (std::find(sizes.begin(), sizes.end(), n) == sizes.end()) continue;
    CaseResult reb = RunCase(n, 8, true, root, vocab);
    reb.pss_peak_kb = sampler.peak();
    results.push_back(reb);
    PrintJson(reb);
    std::fprintf(stderr, "rebuild n=%zu ok=%d wall_p50=%.2fms cpu_p50=%.2fms\n",
                 n, reb.ok ? 1 : 0, reb.update_wall_p50, reb.update_cpu_p50);
    std::fflush(stdout);
  }

  std::fprintf(stderr, "\n| mode | base docs | updates | update wall p50 | update CPU p50 | total CPU | phys write MB | disk MB | runs | query p95 |\n");
  std::fprintf(stderr, "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n");
  for (const auto& r : results) {
    std::fprintf(stderr,
                 "| %s | %zu | %zu | %.2f ms | %.2f ms | %.1f ms | %.1f | %.1f | %llu | %.3f ms |\n",
                 r.mode.c_str(), r.base_docs, r.updates, r.update_wall_p50,
                 r.update_cpu_p50, r.update_cpu_total,
                 r.update_bytes / 1e6, r.disk_bytes / 1e6,
                 (unsigned long long)r.runs, r.query_p95);
  }
  std::fprintf(stderr, "host after: %s\n", HostSnapshot().c_str());
  std::fprintf(stderr, "peak process RSS: %llu KiB\n",
               (unsigned long long)VmHwmKb());
  return 0;
}
