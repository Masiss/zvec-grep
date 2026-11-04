#include "../src/tokenizer.h"
#include "../src/util.h"
#include "m4/index.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <malloc.h>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <time.h>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

namespace {

double ElapsedMs(Clock::time_point start, Clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - start).count();
}

uint64_t BootNs() {
  timespec now{};
  if (::clock_gettime(CLOCK_BOOTTIME, &now) != 0)
    return 0;
  return static_cast<uint64_t>(now.tv_sec) * 1000000000ull +
         static_cast<uint64_t>(now.tv_nsec);
}

void EmitPhase(const std::string &arm, const char *phase, size_t ordinal = 0) {
  const uint64_t boot_ns = BootNs();
  if (boot_ns == 0)
    return;
  std::fprintf(stderr, "M4_PHASE %llu %s %s %zu\n",
               static_cast<unsigned long long>(boot_ns), arm.c_str(), phase,
               ordinal);
  std::fflush(stderr);
}

double Percentile(std::vector<double> values, double q) {
  if (values.empty())
    return 0.0;
  std::sort(values.begin(), values.end());
  const size_t rank = static_cast<size_t>(std::ceil(q * values.size()));
  return values[std::max<size_t>(1, rank) - 1];
}

void PrintOptionalPercentile(std::ostream &output,
                             const std::vector<double> &values, double q) {
  if (values.empty())
    output << "null";
  else
    output << Percentile(values, q);
}

std::string PercentileJson(const std::vector<double> &values, double q) {
  return values.empty() ? "null" : std::to_string(Percentile(values, q));
}

fs::path NativeCachePathForArmRoot(const fs::path &arm_root) {
  return arm_root.parent_path().parent_path() / "native-cache";
}

int NativeCachePathRegressionTest() {
  const fs::path workspace = "/m4-test/operation/workspace";
  const fs::path expected = workspace / "native-cache";
  for (const char *arm : {"background_off", "background_on"}) {
    const fs::path arm_root = workspace / "root" / arm;
    if (NativeCachePathForArmRoot(arm_root).lexically_normal() !=
        expected.lexically_normal()) {
      std::cerr << "native-cache path escaped the operation workspace for "
                << arm << '\n';
      return 1;
    }
  }
  std::cout << "native_cache_path_arms=2 failures=0\n";
  return 0;
}

struct ProcessMemory {
  bool valid{false};
  uint64_t rss_kb{0};
  uint64_t pss_kb{0};
  uint64_t private_kb{0};
  uint64_t anonymous_kb{0};
  uint64_t allocator_live_bytes{0};
  uint64_t allocator_free_bytes{0};
  uint64_t allocator_mmap_bytes{0};
};

ProcessMemory ReadProcessMemory() {
  std::ifstream input("/proc/self/smaps_rollup");
  ProcessMemory memory;
  bool have_rss = false;
  bool have_pss = false;
  bool have_private_clean = false;
  bool have_private_dirty = false;
  bool have_anonymous = false;
  uint64_t private_clean = 0;
  uint64_t private_dirty = 0;
  std::string line;
  while (std::getline(input, line)) {
    std::istringstream fields(line);
    std::string key;
    uint64_t value = 0;
    std::string unit;
    if (!(fields >> key >> value >> unit) || unit != "kB")
      continue;
    if (key == "Rss:") {
      memory.rss_kb = value;
      have_rss = true;
    } else if (key == "Pss:") {
      memory.pss_kb = value;
      have_pss = true;
    } else if (key == "Private_Clean:") {
      private_clean = value;
      have_private_clean = true;
    } else if (key == "Private_Dirty:") {
      private_dirty = value;
      have_private_dirty = true;
    } else if (key == "Anonymous:") {
      memory.anonymous_kb = value;
      have_anonymous = true;
    }
  }
  memory.private_kb = private_clean + private_dirty;
  const struct mallinfo2 allocator = ::mallinfo2();
  memory.allocator_live_bytes = allocator.uordblks + allocator.hblkhd;
  memory.allocator_free_bytes = allocator.fordblks;
  memory.allocator_mmap_bytes = allocator.hblkhd;
  memory.valid = have_rss && have_pss && have_private_clean &&
                 have_private_dirty && have_anonymous;
  return memory;
}

struct CacheChurnVersion {
  std::string identity;
  bool initialization_succeeded{false};
  bool snapshot_used{false};
  bool tokenization_succeeded{false};
  double initialization_ms{0.0};
  ProcessMemory live_memory;
  ProcessMemory released_memory;
};

struct CacheChurnTrace {
  ProcessMemory baseline;
  std::vector<CacheChurnVersion> versions;
  size_t failures{0};
  bool concurrent_open{false};
};

void PrintProcessMemory(std::ostream &output, const ProcessMemory &memory) {
  output << "{\"valid\":" << (memory.valid ? "true" : "false")
         << ",\"rss_kb\":" << memory.rss_kb << ",\"pss_kb\":" << memory.pss_kb
         << ",\"private_kb\":" << memory.private_kb
         << ",\"anonymous_kb\":" << memory.anonymous_kb
         << ",\"allocator_live_bytes\":" << memory.allocator_live_bytes
         << ",\"allocator_free_bytes\":" << memory.allocator_free_bytes
         << ",\"allocator_mmap_bytes\":" << memory.allocator_mmap_bytes << '}';
}

void PrintCacheChurnTrace(std::ostream &output, const CacheChurnTrace &trace) {
  output << "{\"concurrent_open\":"
         << (trace.concurrent_open ? "true" : "false") << ",\"baseline\":";
  PrintProcessMemory(output, trace.baseline);
  output << ",\"versions\":[";
  for (size_t i = 0; i < trace.versions.size(); ++i) {
    if (i != 0)
      output << ',';
    const auto &version = trace.versions[i];
    output << "{\"index\":" << i << ",\"identity_sha256\":\""
           << version.identity << "\",\"initialization_succeeded\":"
           << (version.initialization_succeeded ? "true" : "false")
           << ",\"snapshot_used\":"
           << (version.snapshot_used ? "true" : "false")
           << ",\"tokenization_succeeded\":"
           << (version.tokenization_succeeded ? "true" : "false")
           << ",\"initialization_ms\":" << version.initialization_ms
           << ",\"live_memory\":";
    PrintProcessMemory(output, version.live_memory);
    output << ",\"released_memory\":";
    PrintProcessMemory(output, version.released_memory);
    output << '}';
  }
  output << "],\"failures\":" << trace.failures << '}';
}

struct FlushReaderSample {
  uint64_t start_boot_ns{0};
  uint64_t end_boot_ns{0};
  double wall_ms{0.0};
  double mutex_wait_ms{0.0};
  double execution_cpu_ms{0.0};
  bool ok{false};
};

struct FlushReaderTrace {
  uint64_t flush_start_boot_ns{0};
  uint64_t flush_end_boot_ns{0};
  size_t pending_delta_docs{0};
  bool flush_succeeded{false};
  size_t failures{0};
  std::vector<std::vector<FlushReaderSample>> readers;
};

struct ArmResult {
  std::string name;
  bool flush_metadata_outside_query_lock{true};
  std::string writer_api;
  size_t writer_api_calls{0};
  size_t logical_upserts{0};
  size_t writer_group_commit_max_batch_size{0};
  uint32_t writer_group_commit_max_delay_us{0};
  bool writer_group_commit_per_upsert_records{false};
  uint64_t concurrent_wal_fsyncs{0};
  double compaction_ms{0.0};
  m4::CompactionDiagnostics compaction_diagnostics;
  m4::CompactionDiagnostics post_writer_flush_diagnostics;
  std::string compaction_status;
  std::vector<double> writer_ms;
  std::vector<double> writer_queue_wait_ms;
  std::vector<double> writer_group_commit_size_samples;
  std::vector<double> normalize_ms;
  std::vector<double> normalize_cpu_ms;
  std::vector<double> state_wait_ms;
  std::vector<double> state_wait_cpu_ms;
  std::vector<double> state_hold_ms;
  std::vector<double> state_hold_cpu_ms;
  std::vector<double> wal_encode_ms;
  std::vector<double> wal_encode_cpu_ms;
  std::vector<double> wal_append_ms;
  std::vector<double> wal_append_cpu_ms;
  std::vector<double> wal_write_ms;
  std::vector<double> wal_fsync_ms;
  std::vector<double> query_wait_ms;
  std::vector<double> apply_ms;
  std::vector<double> auto_flush_ms;
  std::vector<double> reader_ms;
  std::vector<double> reader_mutex_wait_ms;
  FlushReaderTrace reader_through_flush;
  uint64_t total_bytes_written{0};
  uint64_t segment_bytes_written{0};
  uint64_t total_wal_fsyncs{0};
  size_t writer_failures{0};
  size_t reader_failures{0};
  size_t durability_failures{0};
  uint64_t runs_after_flush{0};
  uint64_t fixture_runs{0};
  uint64_t compaction_merges{0};
  uint64_t compaction_input_docs{0};
  uint64_t compaction_output_docs{0};
  double compaction_wall_total_ms{0.0};
  double background_wait_ms{0.0};
  bool background_compaction_enabled{false};
  uint64_t compaction_tracked_peak_bytes{0};
};

FlushReaderTrace RunReadersThroughFlush(m4::Index *index,
                                        const std::string &arm,
                                        m4::CompactionDiagnostics *diagnostics,
                                        size_t pending_delta_docs = 0) {
  constexpr size_t kReaderCount = 2;
  constexpr size_t kMinimumBeforeAfter = 2;
  constexpr size_t kMaximumQueriesPerReader = 512;
  FlushReaderTrace trace;
  trace.pending_delta_docs = pending_delta_docs;
  trace.readers.resize(kReaderCount);
  std::vector<size_t> before_completed(kReaderCount, 0);
  std::vector<size_t> after_completed(kReaderCount, 0);
  std::mutex mutex;
  std::condition_variable changed;
  std::atomic<int> phase{0};
  std::atomic<bool> stop{false};
  std::atomic<size_t> failures{0};
  std::vector<std::thread> readers;
  readers.reserve(kReaderCount);
  for (size_t reader = 0; reader < kReaderCount; ++reader) {
    readers.emplace_back([&, reader] {
      while (!stop.load(std::memory_order_acquire) &&
             trace.readers[reader].size() < kMaximumQueriesPerReader) {
        m4::SearchResult result;
        m4::QueryDiagnostics query;
        query.collect_counters = false;
        query.capture_timing = true;
        const uint64_t start_ns = BootNs();
        const auto start = Clock::now();
        const m4::Status status =
            index->SearchFts("needle alpha", 32, &result, &query);
        const auto end = Clock::now();
        const uint64_t end_ns = BootNs();
        FlushReaderSample sample{start_ns,
                                 end_ns,
                                 ElapsedMs(start, end),
                                 query.mutex_wait_ms,
                                 query.execution_cpu_ms,
                                 status.ok && !result.hits.empty()};
        if (!sample.ok)
          failures.fetch_add(1, std::memory_order_relaxed);
        {
          std::lock_guard<std::mutex> lock(mutex);
          trace.readers[reader].push_back(sample);
          const int observed_phase = phase.load(std::memory_order_acquire);
          if (observed_phase == 0 && sample.ok)
            ++before_completed[reader];
          if (observed_phase == 2 && sample.ok &&
              start_ns >= trace.flush_end_boot_ns) {
            ++after_completed[reader];
          }
        }
        changed.notify_all();
      }
    });
  }

  const auto before_deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(15);
  bool readers_ready = false;
  {
    std::unique_lock<std::mutex> lock(mutex);
    readers_ready = changed.wait_until(lock, before_deadline, [&] {
      return std::all_of(
          before_completed.begin(), before_completed.end(),
          [](size_t count) { return count >= kMinimumBeforeAfter; });
    });
  }
  EmitPhase(arm, readers_ready ? "flush_readers_pre_ready"
                               : "flush_readers_pre_timeout");
  phase.store(1, std::memory_order_release);
  trace.flush_start_boot_ns = BootNs();
  EmitPhase(arm, "reader_through_flush_start");
  const m4::Status flush_status = index->Flush(diagnostics);
  trace.flush_succeeded = flush_status.ok;
  trace.flush_end_boot_ns = BootNs();
  phase.store(2, std::memory_order_release);
  changed.notify_all();
  EmitPhase(arm, flush_status.ok ? "reader_through_flush_complete"
                                 : "reader_through_flush_failed");
  bool readers_finished = false;
  {
    const auto after_deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(15);
    std::unique_lock<std::mutex> lock(mutex);
    readers_finished = changed.wait_until(lock, after_deadline, [&] {
      return std::all_of(
          after_completed.begin(), after_completed.end(),
          [](size_t count) { return count >= kMinimumBeforeAfter; });
    });
  }
  stop.store(true, std::memory_order_release);
  changed.notify_all();
  for (auto &reader : readers)
    reader.join();

  trace.failures = failures.load(std::memory_order_relaxed);
  if (!readers_ready || !readers_finished || !flush_status.ok ||
      trace.flush_start_boot_ns == 0 ||
      trace.flush_end_boot_ns <= trace.flush_start_boot_ns) {
    ++trace.failures;
  }
  for (size_t reader = 0; reader < kReaderCount; ++reader) {
    size_t before = 0;
    size_t overlap = 0;
    size_t after = 0;
    for (const auto &sample : trace.readers[reader]) {
      if (!sample.ok)
        continue;
      if (sample.end_boot_ns <= trace.flush_start_boot_ns)
        ++before;
      if (sample.start_boot_ns < trace.flush_end_boot_ns &&
          sample.end_boot_ns > trace.flush_start_boot_ns)
        ++overlap;
      if (sample.start_boot_ns >= trace.flush_end_boot_ns)
        ++after;
    }
    if (before < kMinimumBeforeAfter || overlap == 0 ||
        after < kMinimumBeforeAfter) {
      ++trace.failures;
    }
    EmitPhase(arm, "flush_reader_coverage", reader);
  }
  return trace;
}

m4::Status SeedIndex(m4::Index *index, size_t documents,
                     const std::string &arm) {
  constexpr size_t kDocsPerRun = 1024;
  constexpr size_t kBatch = 128;
  const std::string body =
      "needle alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
      "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega ";
  for (size_t run = 0; run < documents; run += kDocsPerRun) {
    EmitPhase(arm, "seed_run_start", run / kDocsPerRun);
    const size_t run_end = std::min(documents, run + kDocsPerRun);
    for (size_t batch_start = run; batch_start < run_end;
         batch_start += kBatch) {
      const size_t batch_end = std::min(run_end, batch_start + kBatch);
      std::vector<m4::Mutation> mutations;
      mutations.reserve(batch_end - batch_start);
      for (size_t i = batch_start; i < batch_end; ++i) {
        m4::Document document;
        document.text = body + " seedbucket" + std::to_string(i % 64);
        mutations.emplace_back(m4::MutationKind::UPSERT,
                               "seed-" + std::to_string(i),
                               std::move(document));
      }
      const m4::Status status = index->ApplyBatch(mutations);
      if (!status.ok)
        return status;
    }
    const m4::Status status = index->Flush();
    if (!status.ok)
      return status;
    EmitPhase(arm, "seed_run_complete", run / kDocsPerRun);
  }
  return m4::Status::OK();
}

CacheChurnTrace RunAnalyzerCacheChurn(const std::string &dictionary_dir) {
  CacheChurnTrace trace;
  const fs::path dir(dictionary_dir);
  auto read_file = [](const fs::path &path) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
      throw std::runtime_error("cannot read " + path.string());
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
  };
  std::string base_dictionary;
  std::string hmm_model;
  try {
    base_dictionary = read_file(dir / "jieba.dict.utf8");
    hmm_model = read_file(dir / "hmm_model.utf8");
  } catch (const std::exception &error) {
    std::cerr << "cache-churn input error: " << error.what() << '\n';
    trace.failures = 1;
    return trace;
  }

  trace.baseline = ReadProcessMemory();
  if (!trace.baseline.valid) {
    std::cerr
        << "cache-churn could not read /proc/self/smaps_rollup baseline\n";
    trace.failures = 1;
    return trace;
  }

  // Two distinct content identities isolate process-lifetime retention without
  // growing the normal indexed corpus or changing any resource caps.
  constexpr size_t kVersions = 2;
  for (size_t generation = 0; generation < kVersions; ++generation) {
    CacheChurnVersion version;
    const std::string unique_word =
        "m4churnunique" + std::to_string(generation);
    try {
      {
        std::string dictionary_contents =
            base_dictionary + unique_word + " 1000000 n\n";
        const std::string identity_material =
            "m4-churn-v1|jieba.dict.utf8:" + m4::Sha256(dictionary_contents) +
            "|hmm_model.utf8:" + m4::Sha256(hmm_model);
        version.identity = m4::Sha256(identity_material);
        std::vector<m4::AnalyzerDictionaryFileSnapshot> snapshots;
        snapshots.reserve(2);
        snapshots.push_back(
            {"jieba.dict.utf8", std::move(dictionary_contents)});
        snapshots.push_back({"hmm_model.utf8", hmm_model});

        m4::Analyzer analyzer;
        m4::AnalyzerInitDiagnostics diagnostics;
        bool cache_hit = false;
        const auto started = Clock::now();
        const m4::Status status =
            analyzer.Init("jieba", dictionary_dir, "", &cache_hit, &diagnostics,
                          &snapshots, version.identity);
        version.initialization_ms = ElapsedMs(started, Clock::now());
        version.initialization_succeeded =
            status.ok && diagnostics.initialization_succeeded && !cache_hit;
        version.snapshot_used = diagnostics.dictionary_snapshot_used;
        if (status.ok) {
          const auto tokens = analyzer.Tokenize(unique_word);
          version.tokenization_succeeded =
              tokens.size() == 1 && tokens.front().term == unique_word;
        }
        version.live_memory = ReadProcessMemory();
      }
      version.released_memory = ReadProcessMemory();
    } catch (const std::exception &error) {
      std::cerr << "cache-churn generation " << generation
                << " failed: " << error.what() << '\n';
      ++trace.failures;
    }
    if (!version.initialization_succeeded || !version.snapshot_used ||
        !version.tokenization_succeeded || !version.live_memory.valid ||
        !version.released_memory.valid || version.identity.empty()) {
      ++trace.failures;
    }
    trace.versions.push_back(std::move(version));
    if (trace.failures != 0)
      break;
  }
  return trace;
}

CacheChurnTrace
RunConcurrentAnalyzerCacheChurn(const std::string &dictionary_dir) {
  CacheChurnTrace trace;
  trace.concurrent_open = true;
  constexpr size_t kVersions = 2;
  trace.versions.resize(kVersions);
  const fs::path dir(dictionary_dir);
  auto read_file = [](const fs::path &path) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
      throw std::runtime_error("cannot read " + path.string());
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
  };
  std::string base_dictionary;
  std::string hmm_model;
  try {
    base_dictionary = read_file(dir / "jieba.dict.utf8");
    hmm_model = read_file(dir / "hmm_model.utf8");
  } catch (const std::exception &error) {
    std::cerr << "concurrent cache-churn input error: " << error.what() << '\n';
    trace.failures = 1;
    return trace;
  }

  std::vector<std::string> unique_words(kVersions);
  std::vector<std::vector<m4::AnalyzerDictionaryFileSnapshot>> snapshots(
      kVersions);
  for (size_t generation = 0; generation < kVersions; ++generation) {
    auto &version = trace.versions[generation];
    unique_words[generation] = "m4concurrentchurn" + std::to_string(generation);
    std::string dictionary_contents =
        base_dictionary + unique_words[generation] + " 1000000 n\n";
    const std::string identity_material =
        "m4-churn-v1|jieba.dict.utf8:" + m4::Sha256(dictionary_contents) +
        "|hmm_model.utf8:" + m4::Sha256(hmm_model);
    version.identity = m4::Sha256(identity_material);
    snapshots[generation].reserve(2);
    snapshots[generation].push_back(
        {"jieba.dict.utf8", std::move(dictionary_contents)});
    snapshots[generation].push_back({"hmm_model.utf8", hmm_model});
  }

  trace.baseline = ReadProcessMemory();
  if (!trace.baseline.valid) {
    std::cerr
        << "concurrent cache-churn could not read smaps_rollup baseline\n";
    trace.failures = 1;
    return trace;
  }

  std::vector<std::unique_ptr<m4::Analyzer>> analyzers(kVersions);
  std::vector<std::thread> workers;
  workers.reserve(kVersions);
  std::mutex gate_mutex;
  std::condition_variable gate_changed;
  size_t ready = 0;
  bool release_workers = false;
  std::atomic<size_t> worker_failures{0};
  EmitPhase("analyzer_churn", "concurrent_open_start");
  for (size_t generation = 0; generation < kVersions; ++generation) {
    workers.emplace_back([&, generation] {
      auto &version = trace.versions[generation];
      EmitPhase("analyzer_churn", "version_open_start", generation);
      const auto started = Clock::now();
      try {
        auto analyzer = std::make_unique<m4::Analyzer>();
        m4::AnalyzerInitDiagnostics diagnostics;
        bool cache_hit = false;
        const m4::Status status = analyzer->Init(
            "jieba", dictionary_dir, "", &cache_hit, &diagnostics,
            &snapshots[generation], version.identity);
        version.initialization_ms = ElapsedMs(started, Clock::now());
        version.initialization_succeeded =
            status.ok && diagnostics.initialization_succeeded && !cache_hit;
        version.snapshot_used = diagnostics.dictionary_snapshot_used;
        if (status.ok) {
          const auto tokens = analyzer->Tokenize(unique_words[generation]);
          version.tokenization_succeeded =
              tokens.size() == 1 &&
              tokens.front().term == unique_words[generation];
        }
        analyzers[generation] = std::move(analyzer);
        EmitPhase("analyzer_churn", "version_open_complete", generation);
        if (!version.initialization_succeeded || !version.snapshot_used ||
            !version.tokenization_succeeded) {
          worker_failures.fetch_add(1);
        }
      } catch (const std::exception &error) {
        std::cerr << "concurrent cache-churn generation " << generation
                  << " failed: " << error.what() << '\n';
        worker_failures.fetch_add(1);
      }
      std::unique_lock<std::mutex> lock(gate_mutex);
      ++ready;
      gate_changed.notify_all();
      gate_changed.wait(lock, [&] { return release_workers; });
    });
  }

  {
    std::unique_lock<std::mutex> lock(gate_mutex);
    gate_changed.wait(lock, [&] { return ready == kVersions; });
    const ProcessMemory live = ReadProcessMemory();
    for (auto &version : trace.versions)
      version.live_memory = live;
    release_workers = true;
  }
  gate_changed.notify_all();
  for (auto &worker : workers)
    worker.join();
  trace.failures += worker_failures.load();
  EmitPhase("analyzer_churn", "concurrent_open_ready");

  for (size_t generation = 0; generation < kVersions; ++generation) {
    analyzers[generation].reset();
    trace.versions[generation].released_memory = ReadProcessMemory();
    if (!trace.versions[generation].live_memory.valid ||
        !trace.versions[generation].released_memory.valid ||
        trace.versions[generation].identity.empty()) {
      ++trace.failures;
    }
  }
  EmitPhase("analyzer_churn", "concurrent_open_released");
  return trace;
}

ArmResult RunArm(const fs::path &root, const std::string &name,
                 const std::string &dict_dir, size_t documents, size_t writers,
                 size_t ops_per_writer, bool stage_flush_metadata = true,
                 bool reader_through_flush = false,
                 bool pending_flush_payload = false,
                 bool background_compaction_pair = false) {
  EmitPhase(name, "arm_start");
  fs::remove_all(root);
  fs::create_directories(root);
  m4::Options options;
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dict_dir;
  options.max_delta_docs =
      static_cast<uint32_t>(documents + writers * ops_per_writer + 1);
  options.max_delta_bytes = 256ull << 20;
  options.tombstone_compact_ratio = 2.0;
  options.compaction_memory_limit_bytes = 256ull << 20;
  if (background_compaction_pair) {
    options.max_delta_docs = 8;
    options.max_total_runs = 4;
    options.max_runs_per_level = 4;
    options.background_compaction_enabled = name == "background_on";
  }
  options.flush_metadata_outside_query_lock = stage_flush_metadata;
  const bool group_commit_per_upsert = name == "group_commit_per_upsert";
  const bool group_commit = name == "group_commit" ||
                            name == "group_commit_snapshot_rebase" ||
                            group_commit_per_upsert;
  if (group_commit) {
    options.writer_group_commit_max_batch_size = 4;
    options.writer_group_commit_max_delay_us = 500;
    options.writer_group_commit_per_upsert_records = group_commit_per_upsert;
  }

  ArmResult result;
  result.name = name;
  result.flush_metadata_outside_query_lock = stage_flush_metadata;
  result.background_compaction_enabled = options.background_compaction_enabled;
  const bool batch_api = name == "apply_batch";
  result.writer_api = batch_api ? "ApplyBatch"
                      : group_commit_per_upsert
                          ? "UpsertGroupCommitSeparateWalFrames"
                      : group_commit ? "UpsertGroupCommitAtomicBatch"
                                     : "Upsert";
  result.writer_api_calls = batch_api ? writers : writers * ops_per_writer;
  result.logical_upserts = writers * ops_per_writer;
  result.writer_group_commit_max_batch_size =
      options.writer_group_commit_max_batch_size;
  result.writer_group_commit_max_delay_us =
      group_commit ? options.writer_group_commit_max_delay_us : 0;
  result.writer_group_commit_per_upsert_records =
      options.writer_group_commit_per_upsert_records;
  std::unique_ptr<m4::Index> index;
  m4::Status status;
  if (background_compaction_pair) {
    // Build the same uncompacted eight-run fixture for both arms with policy
    // thresholds temporarily raised, then reopen under the measured cap. This
    // isolates maintenance timing from fixture construction.
    m4::Options fixture_options = options;
    fixture_options.background_compaction_enabled = false;
    fixture_options.max_delta_docs =
        static_cast<uint32_t>(documents + writers * ops_per_writer + 1);
    fixture_options.max_total_runs = 32;
    fixture_options.max_runs_per_level = 32;
    EmitPhase(name, "fixture_open_start");
    status = m4::Index::Open(root.string(), fixture_options, &index);
    if (status.ok) {
      status = SeedIndex(index.get(), documents, name + "_fixture");
      if (status.ok && !fs::is_directory(NativeCachePathForArmRoot(root))) {
        status = m4::Status::Error("native analyzer cache was not created "
                                   "inside the operation workspace");
      }
      if (status.ok)
        result.fixture_runs = index->GetStats().runs;
      index.reset();
    }
    EmitPhase(name, status.ok ? "fixture_ready" : "fixture_failed");
    if (!status.ok || result.fixture_runs != documents / 1024) {
      result.compaction_status = "fixture-error:" + status.message;
      ++result.writer_failures;
      EmitPhase(name, "arm_end");
      return result;
    }
  }
  EmitPhase(name, "open_start");
  status = m4::Index::Open(root.string(), options, &index);
  EmitPhase(name, status.ok ? "open_complete" : "open_failed");
  if (!status.ok) {
    result.compaction_status = "open-error:" + status.message;
    ++result.writer_failures;
    EmitPhase(name, "arm_end");
    return result;
  }
  if (!background_compaction_pair) {
    EmitPhase(name, "seed_start");
    status = SeedIndex(index.get(), documents, name);
    EmitPhase(name, status.ok ? "seed_complete" : "seed_failed");
    if (!status.ok) {
      result.compaction_status = "seed-error:" + status.message;
      ++result.writer_failures;
      EmitPhase(name, "arm_end");
      return result;
    }
  }
  // Prepare identical API inputs before the start barrier so client-side string
  // and vector construction is not part of either arm's concurrent interval.
  std::vector<std::vector<m4::Mutation>> prepared_writes(writers);
  for (size_t writer = 0; writer < writers; ++writer) {
    auto &mutations = prepared_writes[writer];
    mutations.reserve(ops_per_writer);
    for (size_t op = 0; op < ops_per_writer; ++op) {
      m4::Document document;
      document.text = "writerterm alpha " + std::to_string(writer) + " " +
                      std::to_string(op) + " needle";
      const std::string pk =
          "writer-" + std::to_string(writer) + "-" + std::to_string(op);
      mutations.emplace_back(m4::MutationKind::UPSERT, pk, std::move(document));
    }
  }

  std::mutex start_mutex;
  std::condition_variable start_cv;
  bool start = false;
  auto wait_start = [&] {
    std::unique_lock<std::mutex> lock(start_mutex);
    start_cv.wait(lock, [&] { return start; });
  };
  m4::Status compaction;
  m4::CompactionDiagnostics compaction_diagnostics;
  Clock::time_point compaction_start;
  Clock::time_point compaction_end;
  std::thread compactor([&] {
    wait_start();
    compaction_start = Clock::now();
    if (background_compaction_pair) {
      EmitPhase(name, "background_wait_start");
      compaction = index->WaitForBackgroundCompaction();
      EmitPhase(name, compaction.ok ? "background_wait_complete"
                                    : "background_wait_failed");
    } else {
      EmitPhase(name, "compactor_start");
      if (name == "snapshot") {
        compaction =
            index->CompactNowSnapshotPrototype(&compaction_diagnostics);
      } else if (name == "snapshot_rebase" ||
                 name == "group_commit_snapshot_rebase") {
        compaction =
            index->CompactNowSnapshotRebasePrototype(&compaction_diagnostics);
      } else {
        compaction = index->CompactNow(&compaction_diagnostics);
      }
      EmitPhase(name,
                compaction.ok ? "compactor_complete" : "compactor_failed");
    }
    compaction_end = Clock::now();
  });

  std::vector<std::vector<double>> writer_samples(writers),
      writer_queue_wait_samples(writers), writer_group_size_samples(writers);
  std::vector<std::vector<double>> normalize_samples(writers),
      normalize_cpu_samples(writers), state_wait_samples(writers),
      state_wait_cpu_samples(writers), state_hold_samples(writers),
      state_hold_cpu_samples(writers), wal_encode_samples(writers),
      wal_encode_cpu_samples(writers), wal_append_samples(writers),
      wal_append_cpu_samples(writers), wal_write_samples(writers),
      wal_fsync_samples(writers), query_wait_samples(writers),
      apply_samples(writers), auto_flush_samples(writers);
  std::atomic<size_t> writer_failures{0};
  std::vector<std::thread> writer_threads;
  writer_threads.reserve(writers);
  for (size_t writer = 0; writer < writers; ++writer) {
    writer_threads.emplace_back([&, writer] {
      wait_start();
      EmitPhase(name, "writer_start", writer);
      auto &samples = writer_samples[writer];
      samples.reserve(ops_per_writer);
      if (name == "apply_batch") {
        m4::WriteDiagnostics diagnostics;
        const auto begin = Clock::now();
        const m4::Status write_status =
            index->ApplyBatch(prepared_writes[writer], &diagnostics);
        samples.push_back(ElapsedMs(begin, Clock::now()));
        normalize_samples[writer].push_back(diagnostics.normalize_ms);
        normalize_cpu_samples[writer].push_back(diagnostics.normalize_cpu_ms);
        state_wait_samples[writer].push_back(diagnostics.state_mutex_wait_ms);
        state_wait_cpu_samples[writer].push_back(
            diagnostics.state_mutex_wait_cpu_ms);
        state_hold_samples[writer].push_back(diagnostics.state_mutex_hold_ms);
        state_hold_cpu_samples[writer].push_back(
            diagnostics.state_mutex_hold_cpu_ms);
        wal_encode_samples[writer].push_back(diagnostics.wal_encode_ms);
        wal_encode_cpu_samples[writer].push_back(diagnostics.wal_encode_cpu_ms);
        wal_append_samples[writer].push_back(diagnostics.wal_append_ms);
        wal_append_cpu_samples[writer].push_back(diagnostics.wal_append_cpu_ms);
        wal_write_samples[writer].push_back(diagnostics.wal_write_ms);
        wal_fsync_samples[writer].push_back(diagnostics.wal_fsync_ms);
        query_wait_samples[writer].push_back(diagnostics.query_mutex_wait_ms);
        apply_samples[writer].push_back(diagnostics.apply_ms);
        auto_flush_samples[writer].push_back(diagnostics.auto_flush_ms);
        if (!write_status.ok) {
          writer_failures.fetch_add(1, std::memory_order_relaxed);
        }
        EmitPhase(name, "writer_complete", writer);
        return;
      }
      for (size_t op = 0; op < ops_per_writer; ++op) {
        const m4::Mutation &mutation = prepared_writes[writer][op];
        m4::WriteDiagnostics diagnostics;
        const auto begin = Clock::now();
        const m4::Status write_status =
            index->Upsert(mutation.pk, mutation.document, &diagnostics);
        samples.push_back(ElapsedMs(begin, Clock::now()));
        if (group_commit) {
          writer_queue_wait_samples[writer].push_back(
              diagnostics.writer_queue_wait_ms);
          writer_group_size_samples[writer].push_back(
              static_cast<double>(diagnostics.writer_group_commit_size));
        } else {
          normalize_samples[writer].push_back(diagnostics.normalize_ms);
          normalize_cpu_samples[writer].push_back(diagnostics.normalize_cpu_ms);
          state_wait_samples[writer].push_back(diagnostics.state_mutex_wait_ms);
          state_wait_cpu_samples[writer].push_back(
              diagnostics.state_mutex_wait_cpu_ms);
          state_hold_samples[writer].push_back(diagnostics.state_mutex_hold_ms);
          state_hold_cpu_samples[writer].push_back(
              diagnostics.state_mutex_hold_cpu_ms);
          wal_encode_samples[writer].push_back(diagnostics.wal_encode_ms);
          wal_encode_cpu_samples[writer].push_back(
              diagnostics.wal_encode_cpu_ms);
          wal_append_samples[writer].push_back(diagnostics.wal_append_ms);
          wal_append_cpu_samples[writer].push_back(
              diagnostics.wal_append_cpu_ms);
          wal_write_samples[writer].push_back(diagnostics.wal_write_ms);
          wal_fsync_samples[writer].push_back(diagnostics.wal_fsync_ms);
          query_wait_samples[writer].push_back(diagnostics.query_mutex_wait_ms);
          apply_samples[writer].push_back(diagnostics.apply_ms);
          auto_flush_samples[writer].push_back(diagnostics.auto_flush_ms);
        }
        if (!write_status.ok) {
          writer_failures.fetch_add(1, std::memory_order_relaxed);
        }
      }
      EmitPhase(name, "writer_complete", writer);
    });
  }

  constexpr size_t kQueriesPerReader = 32;
  std::vector<std::vector<double>> reader_samples(2),
      reader_mutex_wait_samples(2);
  std::atomic<size_t> reader_failures{0};
  std::vector<std::thread> readers;
  for (size_t reader = 0; reader < reader_samples.size(); ++reader) {
    readers.emplace_back([&, reader] {
      wait_start();
      EmitPhase(name, "reader_start", reader);
      auto &samples = reader_samples[reader];
      auto &mutex_waits = reader_mutex_wait_samples[reader];
      samples.reserve(kQueriesPerReader);
      mutex_waits.reserve(kQueriesPerReader);
      for (size_t query = 0; query < kQueriesPerReader; ++query) {
        m4::SearchResult output;
        m4::QueryDiagnostics diagnostics;
        diagnostics.collect_counters = false;
        diagnostics.capture_timing = true;
        const auto begin = Clock::now();
        const m4::Status query_status =
            index->SearchFts("needle alpha", 32, &output, &diagnostics);
        samples.push_back(ElapsedMs(begin, Clock::now()));
        mutex_waits.push_back(diagnostics.mutex_wait_ms);
        if (!query_status.ok || output.hits.empty()) {
          reader_failures.fetch_add(1, std::memory_order_relaxed);
        }
      }
      EmitPhase(name, "reader_complete", reader);
    });
  }
  const uint64_t wal_fsyncs_before = index->GetStats().wal_fsyncs;
  EmitPhase(name, "concurrent_start");
  {
    std::lock_guard<std::mutex> lock(start_mutex);
    start = true;
  }
  start_cv.notify_all();
  for (auto &thread : writer_threads)
    thread.join();
  for (auto &thread : readers)
    thread.join();
  compactor.join();
  EmitPhase(name, "concurrent_complete");
  const uint64_t wal_fsyncs_after = index->GetStats().wal_fsyncs;
  result.concurrent_wal_fsyncs = wal_fsyncs_after - wal_fsyncs_before;
  result.writer_failures = writer_failures.load(std::memory_order_relaxed);
  result.reader_failures = reader_failures.load(std::memory_order_relaxed);
  result.compaction_ms = background_compaction_pair
                             ? 0.0
                             : ElapsedMs(compaction_start, compaction_end);
  result.background_wait_ms = background_compaction_pair
                                  ? ElapsedMs(compaction_start, compaction_end)
                                  : 0.0;
  result.compaction_diagnostics = compaction_diagnostics;
  result.compaction_status = compaction.ok ? "ok" : compaction.message;
  auto append_samples = [](std::vector<double> *destination,
                           std::vector<std::vector<double>> &sources) {
    for (auto &samples : sources) {
      destination->insert(destination->end(), samples.begin(), samples.end());
    }
  };
  append_samples(&result.writer_ms, writer_samples);
  append_samples(&result.writer_queue_wait_ms, writer_queue_wait_samples);
  if (group_commit) {
    append_samples(&result.writer_group_commit_size_samples,
                   writer_group_size_samples);
  }
  append_samples(&result.normalize_ms, normalize_samples);
  append_samples(&result.normalize_cpu_ms, normalize_cpu_samples);
  append_samples(&result.state_wait_ms, state_wait_samples);
  append_samples(&result.state_wait_cpu_ms, state_wait_cpu_samples);
  append_samples(&result.state_hold_ms, state_hold_samples);
  append_samples(&result.state_hold_cpu_ms, state_hold_cpu_samples);
  append_samples(&result.wal_encode_ms, wal_encode_samples);
  append_samples(&result.wal_encode_cpu_ms, wal_encode_cpu_samples);
  append_samples(&result.wal_append_ms, wal_append_samples);
  append_samples(&result.wal_append_cpu_ms, wal_append_cpu_samples);
  append_samples(&result.wal_write_ms, wal_write_samples);
  append_samples(&result.wal_fsync_ms, wal_fsync_samples);
  append_samples(&result.query_wait_ms, query_wait_samples);
  append_samples(&result.apply_ms, apply_samples);
  append_samples(&result.auto_flush_ms, auto_flush_samples);
  append_samples(&result.reader_ms, reader_samples);
  append_samples(&result.reader_mutex_wait_ms, reader_mutex_wait_samples);
  const bool expected_abort =
      name == "snapshot" && !compaction.ok &&
      compaction.message.find("superseded") != std::string::npos;
  if (!compaction.ok && !expected_abort)
    ++result.writer_failures;

  EmitPhase(name, "post_writer_flush_start");
  if (reader_through_flush) {
    constexpr size_t kPendingFlushDocs = 256;
    size_t pending_delta_docs = 0;
    if (pending_flush_payload) {
      std::vector<m4::Mutation> mutations;
      mutations.reserve(kPendingFlushDocs);
      for (size_t i = 0; i < kPendingFlushDocs; ++i) {
        m4::Document document;
        document.text = "flushmarker needle alpha " + std::to_string(i);
        mutations.emplace_back(m4::MutationKind::UPSERT,
                               "flush-pending-" + std::to_string(i),
                               std::move(document));
      }
      status = index->ApplyBatch(mutations);
      if (status.ok)
        pending_delta_docs = kPendingFlushDocs;
      else
        ++result.writer_failures;
      EmitPhase(name, status.ok ? "pending_flush_delta_ready"
                                : "pending_flush_delta_failed");
    }
    result.reader_through_flush = RunReadersThroughFlush(
        index.get(), name, &result.post_writer_flush_diagnostics,
        pending_delta_docs);
    result.reader_failures += result.reader_through_flush.failures;
    status = result.reader_through_flush.flush_succeeded
                 ? m4::Status::OK()
                 : m4::Status::Error("reader-through-flush operation failed");
  } else {
    status = index->Flush(&result.post_writer_flush_diagnostics);
    EmitPhase(name, status.ok ? "post_writer_flush_complete"
                              : "post_writer_flush_failed");
  }
  if (background_compaction_pair && name == "background_on" && status.ok) {
    EmitPhase(name, "post_flush_background_wait_start");
    const auto wait_start = Clock::now();
    const m4::Status background_status = index->WaitForBackgroundCompaction();
    result.background_wait_ms += ElapsedMs(wait_start, Clock::now());
    if (!background_status.ok) {
      compaction = background_status;
      status = background_status;
    }
    EmitPhase(name, background_status.ok ? "post_flush_background_wait_complete"
                                         : "post_flush_background_wait_failed");
  }
  if (!status.ok)
    ++result.writer_failures;
  result.compaction_status = compaction.ok ? "ok" : compaction.message;
  m4::SearchResult output;
  status =
      index->SearchFts("writerterm", writers * ops_per_writer + 1, &output);
  if (!status.ok || output.hits.size() != writers * ops_per_writer) {
    ++result.durability_failures;
  }
  const auto stats_after_flush = index->GetStats();
  result.runs_after_flush = stats_after_flush.runs;
  result.compaction_merges = stats_after_flush.compaction_merges;
  result.compaction_input_docs = stats_after_flush.compaction_input_docs;
  result.compaction_output_docs = stats_after_flush.compaction_output_docs;
  result.compaction_wall_total_ms = stats_after_flush.compaction_wall_ms;
  result.compaction_tracked_peak_bytes =
      stats_after_flush.compaction_peak_bytes;
  result.total_bytes_written = stats_after_flush.bytes_written;
  result.segment_bytes_written = stats_after_flush.segment_bytes_written;
  result.total_wal_fsyncs = stats_after_flush.wal_fsyncs;
  index.reset();

  std::unique_ptr<m4::Index> reopened;
  m4::Options reopen_options = options;
  if (background_compaction_pair)
    reopen_options.background_compaction_enabled = false;
  EmitPhase(name, "reopen_start");
  status = m4::Index::Open(root.string(), reopen_options, &reopened);
  EmitPhase(name, status.ok ? "reopen_complete" : "reopen_failed");
  if (!status.ok) {
    ++result.durability_failures;
  } else {
    status = reopened->SearchFts("writerterm", writers * ops_per_writer + 1,
                                 &output);
    if (!status.ok || output.hits.size() != writers * ops_per_writer) {
      ++result.durability_failures;
    }
    if (pending_flush_payload) {
      status = reopened->SearchFts("flushmarker", 512, &output);
      if (!status.ok || output.hits.size() != 256) {
        ++result.durability_failures;
      }
    }
  }
  EmitPhase(name, "arm_end");
  return result;
}

void PrintValues(const std::vector<double> &values) {
  std::cout << '[';
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}

void PrintFlushDiagnostics(const m4::CompactionDiagnostics &diagnostics) {
  std::cout
      << "{\"total_ms\":" << diagnostics.total_ms
      << ",\"total_cpu_ms\":" << diagnostics.total_cpu_ms
      << ",\"state_mutex_wait_ms\":" << diagnostics.state_mutex_wait_ms
      << ",\"state_mutex_wait_cpu_ms\":" << diagnostics.state_mutex_wait_cpu_ms
      << ",\"state_mutex_hold_ms\":" << diagnostics.state_mutex_hold_ms
      << ",\"state_mutex_hold_cpu_ms\":" << diagnostics.state_mutex_hold_cpu_ms
      << ",\"query_mutex_wait_ms\":" << diagnostics.query_mutex_wait_ms
      << ",\"query_mutex_wait_cpu_ms\":" << diagnostics.query_mutex_wait_cpu_ms
      << ",\"pending_flush_ms\":" << diagnostics.pending_flush_ms
      << ",\"pending_flush_cpu_ms\":" << diagnostics.pending_flush_cpu_ms
      << ",\"flush_stage_timing\":{\"schema_version\":2"
      << ",\"segment_build_ms\":" << diagnostics.flush_segment_build_ms
      << ",\"segment_build_cpu_ms\":" << diagnostics.flush_segment_build_cpu_ms
      << ",\"segment_write_ms\":" << diagnostics.flush_segment_write_ms
      << ",\"segment_write_cpu_ms\":" << diagnostics.flush_segment_write_cpu_ms
      << ",\"segment_open_ms\":" << diagnostics.flush_segment_open_ms
      << ",\"segment_open_cpu_ms\":" << diagnostics.flush_segment_open_cpu_ms
      << ",\"query_mutex_wait_ms\":" << diagnostics.flush_query_mutex_wait_ms
      << ",\"query_mutex_wait_cpu_ms\":"
      << diagnostics.flush_query_mutex_wait_cpu_ms
      << ",\"query_mutex_hold_ms\":" << diagnostics.flush_query_mutex_hold_ms
      << ",\"query_mutex_hold_cpu_ms\":"
      << diagnostics.flush_query_mutex_hold_cpu_ms
      << ",\"stats_append_ms\":" << diagnostics.flush_stats_append_ms
      << ",\"stats_append_cpu_ms\":" << diagnostics.flush_stats_append_cpu_ms
      << ",\"tombstone_write_ms\":" << diagnostics.flush_tombstone_write_ms
      << ",\"tombstone_write_cpu_ms\":"
      << diagnostics.flush_tombstone_write_cpu_ms
      << ",\"manifest_write_ms\":" << diagnostics.flush_manifest_write_ms
      << ",\"manifest_write_cpu_ms\":"
      << diagnostics.flush_manifest_write_cpu_ms
      << ",\"wal_reset_ms\":" << diagnostics.flush_wal_reset_ms
      << ",\"wal_reset_cpu_ms\":" << diagnostics.flush_wal_reset_cpu_ms
      << ",\"gc_ms\":" << diagnostics.flush_gc_ms
      << ",\"gc_cpu_ms\":" << diagnostics.flush_gc_cpu_ms
      << ",\"delta_reset_ms\":" << diagnostics.flush_delta_reset_ms
      << ",\"delta_reset_cpu_ms\":" << diagnostics.flush_delta_reset_cpu_ms
      << "}}";
}

void PrintArm(const ArmResult &arm, bool last) {
  const auto &compaction = arm.compaction_diagnostics;
  std::cout << "{\"name\":\"" << arm.name
            << "\",\"flush_metadata_outside_query_lock\":"
            << (arm.flush_metadata_outside_query_lock ? "true" : "false")
            << ",\"writer_api\":\"" << arm.writer_api
            << "\",\"writer_api_calls\":" << arm.writer_api_calls
            << ",\"logical_upserts\":" << arm.logical_upserts
            << ",\"writer_group_commit_max_batch_size\":"
            << arm.writer_group_commit_max_batch_size
            << ",\"writer_group_commit_max_delay_us\":"
            << arm.writer_group_commit_max_delay_us
            << ",\"writer_group_commit_per_upsert_records\":"
            << (arm.writer_group_commit_per_upsert_records ? "true" : "false")
            << ",\"concurrent_wal_fsyncs\":" << arm.concurrent_wal_fsyncs
            << ",\"compaction_ms\":" << arm.compaction_ms
            << ",\"compaction_status\":\"" << arm.compaction_status
            << "\",\"compaction_diagnostics\":{";
  std::cout
      << "\"total_ms\":" << compaction.total_ms
      << ",\"total_cpu_ms\":" << compaction.total_cpu_ms
      << ",\"state_mutex_wait_ms\":" << compaction.state_mutex_wait_ms
      << ",\"state_mutex_wait_cpu_ms\":" << compaction.state_mutex_wait_cpu_ms
      << ",\"state_mutex_hold_ms\":" << compaction.state_mutex_hold_ms
      << ",\"state_mutex_hold_cpu_ms\":" << compaction.state_mutex_hold_cpu_ms
      << ",\"query_mutex_wait_ms\":" << compaction.query_mutex_wait_ms
      << ",\"query_mutex_wait_cpu_ms\":" << compaction.query_mutex_wait_cpu_ms
      << ",\"pending_flush_ms\":" << compaction.pending_flush_ms
      << ",\"pending_flush_cpu_ms\":" << compaction.pending_flush_cpu_ms
      << ",\"merge_prepare_ms\":" << compaction.merge_prepare_ms
      << ",\"merge_prepare_cpu_ms\":" << compaction.merge_prepare_cpu_ms
      << ",\"merge_build_ms\":" << compaction.merge_build_ms
      << ",\"merge_build_cpu_ms\":" << compaction.merge_build_cpu_ms
      << ",\"publish_state_mutex_wait_ms\":"
      << compaction.publish_state_mutex_wait_ms
      << ",\"publish_state_mutex_wait_cpu_ms\":"
      << compaction.publish_state_mutex_wait_cpu_ms
      << ",\"publish_query_mutex_wait_ms\":"
      << compaction.publish_query_mutex_wait_ms
      << ",\"publish_query_mutex_wait_cpu_ms\":"
      << compaction.publish_query_mutex_wait_cpu_ms
      << ",\"publication_ms\":" << compaction.publication_ms
      << ",\"publication_cpu_ms\":" << compaction.publication_cpu_ms
      << ",\"flush_stage_timing\":{\"schema_version\":2"
      << ",\"segment_build_ms\":" << compaction.flush_segment_build_ms
      << ",\"segment_build_cpu_ms\":" << compaction.flush_segment_build_cpu_ms
      << ",\"segment_write_ms\":" << compaction.flush_segment_write_ms
      << ",\"segment_write_cpu_ms\":" << compaction.flush_segment_write_cpu_ms
      << ",\"segment_open_ms\":" << compaction.flush_segment_open_ms
      << ",\"segment_open_cpu_ms\":" << compaction.flush_segment_open_cpu_ms
      << ",\"query_mutex_wait_ms\":" << compaction.flush_query_mutex_wait_ms
      << ",\"query_mutex_wait_cpu_ms\":"
      << compaction.flush_query_mutex_wait_cpu_ms
      << ",\"query_mutex_hold_ms\":" << compaction.flush_query_mutex_hold_ms
      << ",\"query_mutex_hold_cpu_ms\":"
      << compaction.flush_query_mutex_hold_cpu_ms
      << ",\"stats_append_ms\":" << compaction.flush_stats_append_ms
      << ",\"stats_append_cpu_ms\":" << compaction.flush_stats_append_cpu_ms
      << ",\"tombstone_write_ms\":" << compaction.flush_tombstone_write_ms
      << ",\"tombstone_write_cpu_ms\":"
      << compaction.flush_tombstone_write_cpu_ms
      << ",\"manifest_write_ms\":" << compaction.flush_manifest_write_ms
      << ",\"manifest_write_cpu_ms\":" << compaction.flush_manifest_write_cpu_ms
      << ",\"wal_reset_ms\":" << compaction.flush_wal_reset_ms
      << ",\"wal_reset_cpu_ms\":" << compaction.flush_wal_reset_cpu_ms
      << ",\"gc_ms\":" << compaction.flush_gc_ms
      << ",\"gc_cpu_ms\":" << compaction.flush_gc_cpu_ms
      << ",\"delta_reset_ms\":" << compaction.flush_delta_reset_ms
      << ",\"delta_reset_cpu_ms\":" << compaction.flush_delta_reset_cpu_ms
      << "}},\"post_writer_flush_diagnostics\":";
  PrintFlushDiagnostics(arm.post_writer_flush_diagnostics);
  std::cout << ",\"writer_samples\":";
  PrintValues(arm.writer_ms);
  std::cout << ",\"writer_queue_wait_samples\":";
  PrintValues(arm.writer_queue_wait_ms);
  std::cout << ",\"writer_queue_wait_p95_ms\":";
  PrintOptionalPercentile(std::cout, arm.writer_queue_wait_ms, 0.95);
  std::cout << ",\"writer_group_commit_size_samples\":";
  PrintValues(arm.writer_group_commit_size_samples);
  std::cout << ",\"writer_p50_ms\":" << PercentileJson(arm.writer_ms, 0.50)
            << ",\"writer_p95_ms\":" << PercentileJson(arm.writer_ms, 0.95)
            << ",\"writer_max_ms\":" << PercentileJson(arm.writer_ms, 1.0)
            << ",\"normalize_samples\":";
  PrintValues(arm.normalize_ms);
  std::cout << ",\"normalize_p95_ms\":"
            << PercentileJson(arm.normalize_ms, 0.95)
            << ",\"normalize_cpu_samples\":";
  PrintValues(arm.normalize_cpu_ms);
  std::cout << ",\"state_wait_samples\":";
  PrintValues(arm.state_wait_ms);
  std::cout << ",\"state_wait_p95_ms\":"
            << PercentileJson(arm.state_wait_ms, 0.95)
            << ",\"state_wait_cpu_samples\":";
  PrintValues(arm.state_wait_cpu_ms);
  std::cout << ",\"state_hold_samples\":";
  PrintValues(arm.state_hold_ms);
  std::cout << ",\"state_hold_p95_ms\":"
            << PercentileJson(arm.state_hold_ms, 0.95)
            << ",\"state_hold_cpu_samples\":";
  PrintValues(arm.state_hold_cpu_ms);
  std::cout << ",\"wal_encode_samples\":";
  PrintValues(arm.wal_encode_ms);
  std::cout << ",\"wal_encode_p95_ms\":"
            << PercentileJson(arm.wal_encode_ms, 0.95)
            << ",\"wal_encode_cpu_samples\":";
  PrintValues(arm.wal_encode_cpu_ms);
  std::cout << ",\"wal_append_samples\":";
  PrintValues(arm.wal_append_ms);
  std::cout << ",\"wal_append_p95_ms\":"
            << PercentileJson(arm.wal_append_ms, 0.95)
            << ",\"wal_append_cpu_samples\":";
  PrintValues(arm.wal_append_cpu_ms);
  std::cout << ",\"wal_write_samples\":";
  PrintValues(arm.wal_write_ms);
  std::cout << ",\"wal_fsync_samples\":";
  PrintValues(arm.wal_fsync_ms);
  std::cout << ",\"query_wait_samples\":";
  PrintValues(arm.query_wait_ms);
  std::cout << ",\"query_wait_p95_ms\":"
            << PercentileJson(arm.query_wait_ms, 0.95) << ",\"apply_samples\":";
  PrintValues(arm.apply_ms);
  std::cout << ",\"apply_p95_ms\":" << PercentileJson(arm.apply_ms, 0.95)
            << ",\"auto_flush_samples\":";
  PrintValues(arm.auto_flush_ms);
  std::cout << ",\"auto_flush_p95_ms\":"
            << PercentileJson(arm.auto_flush_ms, 0.95)
            << ",\"reader_samples\":";
  PrintValues(arm.reader_ms);
  std::cout << ",\"reader_mutex_wait_samples\":";
  PrintValues(arm.reader_mutex_wait_ms);
  std::cout << ",\"reader_mutex_wait_p95_ms\":"
            << PercentileJson(arm.reader_mutex_wait_ms, 0.95)
            << ",\"reader_p50_ms\":" << PercentileJson(arm.reader_ms, 0.50)
            << ",\"reader_p95_ms\":" << PercentileJson(arm.reader_ms, 0.95)
            << ",\"writer_failures\":" << arm.writer_failures
            << ",\"reader_failures\":" << arm.reader_failures
            << ",\"durability_failures\":" << arm.durability_failures
            << ",\"runs_after_flush\":" << arm.runs_after_flush
            << ",\"fixture_runs\":" << arm.fixture_runs
            << ",\"background_compaction_enabled\":"
            << (arm.background_compaction_enabled ? "true" : "false")
            << ",\"background_wait_ms\":" << arm.background_wait_ms
            << ",\"compaction_merges\":" << arm.compaction_merges
            << ",\"compaction_input_docs\":" << arm.compaction_input_docs
            << ",\"compaction_output_docs\":" << arm.compaction_output_docs
            << ",\"compaction_wall_total_ms\":" << arm.compaction_wall_total_ms
            << ",\"total_bytes_written\":" << arm.total_bytes_written
            << ",\"segment_bytes_written\":" << arm.segment_bytes_written
            << ",\"total_wal_fsyncs\":" << arm.total_wal_fsyncs
            << ",\"compaction_tracked_peak_bytes\":"
            << arm.compaction_tracked_peak_bytes
            << ",\"reader_through_flush\":{\"flush_start_boot_ns\":"
            << arm.reader_through_flush.flush_start_boot_ns
            << ",\"flush_end_boot_ns\":"
            << arm.reader_through_flush.flush_end_boot_ns
            << ",\"pending_delta_docs\":"
            << arm.reader_through_flush.pending_delta_docs
            << ",\"flush_succeeded\":"
            << (arm.reader_through_flush.flush_succeeded ? "true" : "false")
            << ",\"readers\":[";
  for (size_t reader = 0; reader < arm.reader_through_flush.readers.size();
       ++reader) {
    if (reader)
      std::cout << ',';
    std::cout << "{\"reader_id\":" << reader << ",\"samples\":[";
    const auto &samples = arm.reader_through_flush.readers[reader];
    for (size_t sample = 0; sample < samples.size(); ++sample) {
      if (sample)
        std::cout << ',';
      const auto &item = samples[sample];
      std::cout << "{\"start_boot_ns\":" << item.start_boot_ns
                << ",\"end_boot_ns\":" << item.end_boot_ns
                << ",\"wall_ms\":" << item.wall_ms
                << ",\"mutex_wait_ms\":" << item.mutex_wait_ms
                << ",\"execution_cpu_ms\":" << item.execution_cpu_ms
                << ",\"ok\":" << (item.ok ? "true" : "false") << '}';
    }
    std::cout << "]}";
  }
  std::cout << "],\"failures\":" << arm.reader_through_flush.failures << "}}"
            << (last ? "\n" : ",");
}

} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "--self-test-native-cache-path")
    return NativeCachePathRegressionTest();
  if (argc < 6 || argc > 7) {
    std::cerr << "usage: m4_compaction_writer_contention <root> <jieba-dict> "
                 "<workload-mode> <documents> <writer-operations> "
                 "[background_off_first|background_on_first]\n";
    return 2;
  }
  const fs::path root(argv[1]);
  const std::string dict_dir(argv[2]);
  const std::string workload_mode(argv[3]);
  const bool background_compaction_pair_mode =
      workload_mode == "--m4-background-compaction-pair";
  const std::string background_arm_order = argc == 7 ? argv[6] : "";
  if ((background_compaction_pair_mode &&
       (argc != 7 || (background_arm_order != "background_off_first" &&
                      background_arm_order != "background_on_first"))) ||
      (!background_compaction_pair_mode && argc != 6)) {
    std::cerr << "invalid background-compaction arm order\n";
    return 2;
  }
  const bool cache_churn_mode =
      workload_mode == "--m4-compaction-contention-cache-churn-v2";
  const bool concurrent_cache_churn_mode =
      workload_mode == "--m4-compaction-contention-cache-churn-concurrent-v2";
  const bool flush_reader_staging_mode =
      workload_mode == "--m4-flush-reader-staging-pair";
  const bool group_commit_flush_reader_mode =
      workload_mode == "--m4-group-commit-flush-reader-pair";
  const bool group_commit_pending_flush_reader_mode =
      workload_mode == "--m4-group-commit-pending-flush-reader-pair";
  const bool writer_group_commit_rebase_mode =
      workload_mode == "--m4-writer-group-commit-snapshot-rebase";
  const bool writer_group_commit_per_upsert_mode =
      workload_mode == "--m4-writer-group-commit-per-upsert";
  const bool writer_group_commit_mode =
      workload_mode == "--m4-writer-group-commit" ||
      writer_group_commit_rebase_mode;
  if (background_compaction_pair_mode) {
    const char *native_tmp = std::getenv("M4_REPO_TMP_DIR");
    const fs::path expected_native_tmp = root.parent_path() / "native-cache";
    if (!native_tmp || !fs::path(native_tmp).is_absolute() ||
        fs::path(native_tmp).lexically_normal() !=
            expected_native_tmp.lexically_normal()) {
      std::cerr << "background compaction requires native scratch inside the "
                   "operation workspace\n";
      return 2;
    }
  }
  if (!fs::is_directory(dict_dir) ||
      (workload_mode != "--m4-compaction-contention" && !cache_churn_mode &&
       !concurrent_cache_churn_mode && !writer_group_commit_mode &&
       !writer_group_commit_per_upsert_mode && !flush_reader_staging_mode &&
       !group_commit_flush_reader_mode &&
       !group_commit_pending_flush_reader_mode &&
       !background_compaction_pair_mode)) {
    std::cerr << "missing Jieba dictionary or invalid workload mode\n";
    return 2;
  }
  const size_t documents = std::stoull(argv[4]);
  const size_t writer_operation_count = std::stoull(argv[5]);
  constexpr size_t writers = 4;
  if (documents < 1024 || documents % 1024 != 0 ||
      writer_operation_count == 0 || writer_operation_count % writers != 0 ||
      writer_operation_count / writers > 64) {
    std::cerr << "workload dimensions outside bounded experimental limits\n";
    return 2;
  }
  const size_t ops_per_writer = writer_operation_count / writers;

  std::vector<std::string> order =
      background_compaction_pair_mode
          ? (background_arm_order == "background_off_first"
                 ? std::vector<std::string>{"background_off", "background_on"}
                 : std::vector<std::string>{"background_on", "background_off"})
      : flush_reader_staging_mode
          ? std::vector<std::string>{"flush_baseline", "flush_candidate"}
      : (group_commit_flush_reader_mode ||
         group_commit_pending_flush_reader_mode)
          ? std::vector<std::string>{"default", "group_commit"}
      : writer_group_commit_rebase_mode
          ? std::vector<std::string>{"snapshot_rebase",
                                     "group_commit_snapshot_rebase"}
      : writer_group_commit_per_upsert_mode
          ? std::vector<std::string>{"default", "group_commit_per_upsert"}
      : writer_group_commit_mode
          ? std::vector<std::string>{"default", "group_commit"}
          : std::vector<std::string>{"default", "snapshot", "snapshot_rebase",
                                     "apply_batch"};
  if (!background_compaction_pair_mode &&
      std::hash<std::string>{}(root.string()) % 2 != 0) {
    std::reverse(order.begin(), order.end());
  }
  std::vector<ArmResult> results;
  for (const auto &name : order) {
    const bool stage_flush = name != "flush_baseline";
    results.push_back(RunArm(root / name, name, dict_dir, documents, writers,
                             ops_per_writer, stage_flush,
                             flush_reader_staging_mode ||
                                 group_commit_flush_reader_mode ||
                                 group_commit_pending_flush_reader_mode,
                             group_commit_pending_flush_reader_mode,
                             background_compaction_pair_mode));
  }
  CacheChurnTrace churn_trace;
  if (cache_churn_mode)
    churn_trace = RunAnalyzerCacheChurn(dict_dir);
  else if (concurrent_cache_churn_mode) {
    churn_trace = RunConcurrentAnalyzerCacheChurn(dict_dir);
  }
  size_t failures = churn_trace.failures;
  for (const auto &result : results) {
    failures += result.writer_failures + result.reader_failures +
                result.durability_failures;
  }
  const unsigned timing_schema = background_compaction_pair_mode          ? 13
                                 : writer_group_commit_per_upsert_mode    ? 12
                                 : group_commit_pending_flush_reader_mode ? 11
                                 : group_commit_flush_reader_mode         ? 10
                                 : flush_reader_staging_mode              ? 9
                                                                          : 8;
  std::cout
      << "{\"mode\":\"m4_compaction_contention\",\"timing_schema_version\":"
      << timing_schema << ",\"documents\":" << documents
      << ",\"writers\":" << writers << ",\"ops_per_writer\":" << ops_per_writer
      << ",\"rounds\":" << writer_operation_count << ",\"workload_mode\":\""
      << (background_compaction_pair_mode ? "background_compaction_pair"
          : group_commit_pending_flush_reader_mode
              ? "group_commit_pending_flush_reader_pair"
          : group_commit_flush_reader_mode ? "group_commit_flush_reader_pair"
          : flush_reader_staging_mode      ? "flush_reader_staging_pair"
          : writer_group_commit_rebase_mode
              ? "writer_group_commit_snapshot_rebase"
          : writer_group_commit_per_upsert_mode
              ? "writer_group_commit_per_upsert"
          : writer_group_commit_mode ? "writer_group_commit"
                                     : "contention")
      << "\",\"writer_operations\":" << writer_operation_count
      << ",\"reader_queries\":" << 2 * 32
      << ",\"coherence_errors\":0,\"failures\":" << failures
      << ",\"arm_order\":[";
  for (size_t i = 0; i < order.size(); ++i) {
    if (i != 0)
      std::cout << ',';
    std::cout << '\"' << order[i] << '\"';
  }
  std::cout << "],\"arms\":[\n";
  for (size_t i = 0; i < results.size(); ++i) {
    PrintArm(results[i], i + 1 == results.size());
  }
  std::cout << ']';
  if (cache_churn_mode || concurrent_cache_churn_mode) {
    std::cout << ",\"analyzer_churn_trace\":";
    PrintCacheChurnTrace(std::cout, churn_trace);
  }
  std::cout << "}\n";
  return failures == 0 ? 0 : 1;
}
