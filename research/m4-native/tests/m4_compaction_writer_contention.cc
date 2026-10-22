#include "m4/index.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

namespace {

double ElapsedMs(Clock::time_point start, Clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - start).count();
}

double Percentile(std::vector<double> values, double q) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  const size_t rank = static_cast<size_t>(std::ceil(q * values.size()));
  return values[std::max<size_t>(1, rank) - 1];
}

struct ArmResult {
  std::string name;
  std::string writer_api;
  size_t writer_api_calls{0};
  size_t logical_upserts{0};
  double compaction_ms{0.0};
  m4::CompactionDiagnostics compaction_diagnostics;
  std::string compaction_status;
  std::vector<double> writer_ms;
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
  std::vector<double> query_wait_ms;
  std::vector<double> apply_ms;
  std::vector<double> auto_flush_ms;
  std::vector<double> reader_ms;
  std::vector<double> reader_mutex_wait_ms;
  size_t writer_failures{0};
  size_t reader_failures{0};
  size_t durability_failures{0};
  uint64_t runs_after_flush{0};
};

m4::Status SeedIndex(m4::Index* index, size_t documents) {
  constexpr size_t kDocsPerRun = 1024;
  constexpr size_t kBatch = 128;
  const std::string body =
      "needle alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
      "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega ";
  for (size_t run = 0; run < documents; run += kDocsPerRun) {
    const size_t run_end = std::min(documents, run + kDocsPerRun);
    for (size_t batch_start = run; batch_start < run_end; batch_start += kBatch) {
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
      if (!status.ok) return status;
    }
    const m4::Status status = index->Flush();
    if (!status.ok) return status;
  }
  return m4::Status::OK();
}

ArmResult RunArm(const fs::path& root, const std::string& name,
                 const std::string& dict_dir, size_t documents,
                 size_t writers, size_t ops_per_writer) {
  fs::remove_all(root);
  fs::create_directories(root);
  m4::Options options;
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dict_dir;
  options.max_delta_docs = static_cast<uint32_t>(documents + writers * ops_per_writer + 1);
  options.max_delta_bytes = 256ull << 20;
  options.tombstone_compact_ratio = 2.0;
  options.compaction_memory_limit_bytes = 256ull << 20;

  ArmResult result;
  result.name = name;
  const bool batch_api = name == "apply_batch";
  result.writer_api = batch_api ? "ApplyBatch" : "Upsert";
  result.writer_api_calls = batch_api ? writers : writers * ops_per_writer;
  result.logical_upserts = writers * ops_per_writer;
  std::unique_ptr<m4::Index> index;
  m4::Status status = m4::Index::Open(root.string(), options, &index);
  if (!status.ok) {
    result.compaction_status = "open-error:" + status.message;
    ++result.writer_failures;
    return result;
  }
  status = SeedIndex(index.get(), documents);
  if (!status.ok) {
    result.compaction_status = "seed-error:" + status.message;
    ++result.writer_failures;
    return result;
  }
  // Prepare identical API inputs before the start barrier so client-side string
  // and vector construction is not part of either arm's concurrent interval.
  std::vector<std::vector<m4::Mutation>> prepared_writes(writers);
  for (size_t writer = 0; writer < writers; ++writer) {
    auto& mutations = prepared_writes[writer];
    mutations.reserve(ops_per_writer);
    for (size_t op = 0; op < ops_per_writer; ++op) {
      m4::Document document;
      document.text = "writerterm alpha " + std::to_string(writer) + " " +
                      std::to_string(op) + " needle";
      const std::string pk = "writer-" + std::to_string(writer) + "-" +
                             std::to_string(op);
      mutations.emplace_back(m4::MutationKind::UPSERT, pk,
                             std::move(document));
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
    if (name == "snapshot") {
      compaction =
          index->CompactNowSnapshotPrototype(&compaction_diagnostics);
    } else if (name == "snapshot_rebase") {
      compaction = index->CompactNowSnapshotRebasePrototype(
          &compaction_diagnostics);
    } else {
      compaction = index->CompactNow(&compaction_diagnostics);
    }
    compaction_end = Clock::now();
  });

  std::vector<std::vector<double>> writer_samples(writers);
  std::vector<std::vector<double>> normalize_samples(writers),
      normalize_cpu_samples(writers), state_wait_samples(writers),
      state_wait_cpu_samples(writers), state_hold_samples(writers),
      state_hold_cpu_samples(writers), wal_encode_samples(writers),
      wal_encode_cpu_samples(writers), wal_append_samples(writers),
      wal_append_cpu_samples(writers), query_wait_samples(writers),
      apply_samples(writers), auto_flush_samples(writers);
  std::atomic<size_t> writer_failures{0};
  std::vector<std::thread> writer_threads;
  writer_threads.reserve(writers);
  for (size_t writer = 0; writer < writers; ++writer) {
    writer_threads.emplace_back([&, writer] {
      wait_start();
      auto& samples = writer_samples[writer];
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
        wal_append_cpu_samples[writer].push_back(
            diagnostics.wal_append_cpu_ms);
        query_wait_samples[writer].push_back(diagnostics.query_mutex_wait_ms);
        apply_samples[writer].push_back(diagnostics.apply_ms);
        auto_flush_samples[writer].push_back(diagnostics.auto_flush_ms);
        if (!write_status.ok) {
          writer_failures.fetch_add(1, std::memory_order_relaxed);
        }
        return;
      }
      for (size_t op = 0; op < ops_per_writer; ++op) {
        const m4::Mutation& mutation = prepared_writes[writer][op];
        m4::WriteDiagnostics diagnostics;
        const auto begin = Clock::now();
        const m4::Status write_status =
            index->Upsert(mutation.pk, mutation.document, &diagnostics);
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
        wal_append_cpu_samples[writer].push_back(
            diagnostics.wal_append_cpu_ms);
        query_wait_samples[writer].push_back(diagnostics.query_mutex_wait_ms);
        apply_samples[writer].push_back(diagnostics.apply_ms);
        auto_flush_samples[writer].push_back(diagnostics.auto_flush_ms);
        if (!write_status.ok) {
          writer_failures.fetch_add(1, std::memory_order_relaxed);
        }
      }
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
      auto& samples = reader_samples[reader];
      auto& mutex_waits = reader_mutex_wait_samples[reader];
      samples.reserve(kQueriesPerReader);
      mutex_waits.reserve(kQueriesPerReader);
      for (size_t query = 0; query < kQueriesPerReader; ++query) {
        m4::SearchResult output;
        m4::QueryDiagnostics diagnostics;
        diagnostics.collect_counters = false;
        diagnostics.capture_timing = true;
        const auto begin = Clock::now();
        const m4::Status query_status = index->SearchFts(
            "needle alpha", 32, &output, &diagnostics);
        samples.push_back(ElapsedMs(begin, Clock::now()));
        mutex_waits.push_back(diagnostics.mutex_wait_ms);
        if (!query_status.ok || output.hits.empty()) {
          reader_failures.fetch_add(1, std::memory_order_relaxed);
        }
      }
    });
  }
  {
    std::lock_guard<std::mutex> lock(start_mutex);
    start = true;
  }
  start_cv.notify_all();
  for (auto& thread : writer_threads) thread.join();
  for (auto& thread : readers) thread.join();
  compactor.join();
  result.writer_failures = writer_failures.load(std::memory_order_relaxed);
  result.reader_failures = reader_failures.load(std::memory_order_relaxed);
  result.compaction_ms = ElapsedMs(compaction_start, compaction_end);
  result.compaction_diagnostics = compaction_diagnostics;
  result.compaction_status = compaction.ok ? "ok" : compaction.message;
  auto append_samples = [](std::vector<double>* destination,
                           std::vector<std::vector<double>>& sources) {
    for (auto& samples : sources) {
      destination->insert(destination->end(), samples.begin(), samples.end());
    }
  };
  append_samples(&result.writer_ms, writer_samples);
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
  append_samples(&result.query_wait_ms, query_wait_samples);
  append_samples(&result.apply_ms, apply_samples);
  append_samples(&result.auto_flush_ms, auto_flush_samples);
  append_samples(&result.reader_ms, reader_samples);
  append_samples(&result.reader_mutex_wait_ms, reader_mutex_wait_samples);
  const bool expected_abort = name == "snapshot" && !compaction.ok &&
      compaction.message.find("superseded") != std::string::npos;
  if (!compaction.ok && !expected_abort) ++result.writer_failures;

  status = index->Flush();
  if (!status.ok) ++result.writer_failures;
  m4::SearchResult output;
  status = index->SearchFts("writerterm", writers * ops_per_writer + 1,
                            &output);
  if (!status.ok || output.hits.size() != writers * ops_per_writer) {
    ++result.durability_failures;
  }
  result.runs_after_flush = index->GetStats().runs;
  index.reset();

  std::unique_ptr<m4::Index> reopened;
  status = m4::Index::Open(root.string(), options, &reopened);
  if (!status.ok) {
    ++result.durability_failures;
  } else {
    status = reopened->SearchFts("writerterm", writers * ops_per_writer + 1,
                                 &output);
    if (!status.ok || output.hits.size() != writers * ops_per_writer) {
      ++result.durability_failures;
    }
  }
  return result;
}

void PrintValues(const std::vector<double>& values) {
  std::cout << '[';
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}

void PrintArm(const ArmResult& arm, bool last) {
  const auto& compaction = arm.compaction_diagnostics;
  std::cout << "{\"name\":\"" << arm.name << "\",\"writer_api\":\""
            << arm.writer_api << "\",\"writer_api_calls\":"
            << arm.writer_api_calls << ",\"logical_upserts\":"
            << arm.logical_upserts << ",\"compaction_ms\":"
            << arm.compaction_ms << ",\"compaction_status\":\""
            << arm.compaction_status << "\",\"compaction_diagnostics\":{";
  std::cout << "\"total_ms\":" << compaction.total_ms
            << ",\"total_cpu_ms\":" << compaction.total_cpu_ms
            << ",\"state_mutex_wait_ms\":" << compaction.state_mutex_wait_ms
            << ",\"state_mutex_wait_cpu_ms\":"
            << compaction.state_mutex_wait_cpu_ms
            << ",\"state_mutex_hold_ms\":"
            << compaction.state_mutex_hold_ms
            << ",\"state_mutex_hold_cpu_ms\":"
            << compaction.state_mutex_hold_cpu_ms
            << ",\"query_mutex_wait_ms\":" << compaction.query_mutex_wait_ms
            << ",\"query_mutex_wait_cpu_ms\":"
            << compaction.query_mutex_wait_cpu_ms
            << ",\"pending_flush_ms\":" << compaction.pending_flush_ms
            << ",\"pending_flush_cpu_ms\":"
            << compaction.pending_flush_cpu_ms
            << ",\"merge_prepare_ms\":" << compaction.merge_prepare_ms
            << ",\"merge_prepare_cpu_ms\":"
            << compaction.merge_prepare_cpu_ms
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
            << ",\"publication_cpu_ms\":"
            << compaction.publication_cpu_ms
            << ",\"flush_stage_timing\":{\"schema_version\":1"
            << ",\"segment_build_ms\":"
            << compaction.flush_segment_build_ms
            << ",\"segment_build_cpu_ms\":"
            << compaction.flush_segment_build_cpu_ms
            << ",\"segment_write_ms\":"
            << compaction.flush_segment_write_ms
            << ",\"segment_write_cpu_ms\":"
            << compaction.flush_segment_write_cpu_ms
            << ",\"segment_open_ms\":"
            << compaction.flush_segment_open_ms
            << ",\"segment_open_cpu_ms\":"
            << compaction.flush_segment_open_cpu_ms
            << ",\"query_mutex_wait_ms\":"
            << compaction.flush_query_mutex_wait_ms
            << ",\"query_mutex_wait_cpu_ms\":"
            << compaction.flush_query_mutex_wait_cpu_ms
            << ",\"stats_append_ms\":"
            << compaction.flush_stats_append_ms
            << ",\"stats_append_cpu_ms\":"
            << compaction.flush_stats_append_cpu_ms
            << ",\"tombstone_write_ms\":"
            << compaction.flush_tombstone_write_ms
            << ",\"tombstone_write_cpu_ms\":"
            << compaction.flush_tombstone_write_cpu_ms
            << ",\"manifest_write_ms\":"
            << compaction.flush_manifest_write_ms
            << ",\"manifest_write_cpu_ms\":"
            << compaction.flush_manifest_write_cpu_ms
            << ",\"wal_reset_ms\":"
            << compaction.flush_wal_reset_ms
            << ",\"wal_reset_cpu_ms\":"
            << compaction.flush_wal_reset_cpu_ms
            << ",\"gc_ms\":" << compaction.flush_gc_ms
            << ",\"gc_cpu_ms\":" << compaction.flush_gc_cpu_ms
            << ",\"delta_reset_ms\":"
            << compaction.flush_delta_reset_ms
            << ",\"delta_reset_cpu_ms\":"
            << compaction.flush_delta_reset_cpu_ms
            << "}},\"writer_samples\":";
  PrintValues(arm.writer_ms);
  std::cout << ",\"writer_p50_ms\":" << Percentile(arm.writer_ms, 0.50)
            << ",\"writer_p95_ms\":" << Percentile(arm.writer_ms, 0.95)
            << ",\"writer_max_ms\":" << Percentile(arm.writer_ms, 1.0)
            << ",\"normalize_samples\":";
  PrintValues(arm.normalize_ms);
  std::cout << ",\"normalize_p95_ms\":"
            << Percentile(arm.normalize_ms, 0.95)
            << ",\"normalize_cpu_samples\":";
  PrintValues(arm.normalize_cpu_ms);
  std::cout << ",\"state_wait_samples\":";
  PrintValues(arm.state_wait_ms);
  std::cout << ",\"state_wait_p95_ms\":" << Percentile(arm.state_wait_ms, 0.95)
            << ",\"state_wait_cpu_samples\":";
  PrintValues(arm.state_wait_cpu_ms);
  std::cout << ",\"state_hold_samples\":";
  PrintValues(arm.state_hold_ms);
  std::cout << ",\"state_hold_p95_ms\":"
            << Percentile(arm.state_hold_ms, 0.95)
            << ",\"state_hold_cpu_samples\":";
  PrintValues(arm.state_hold_cpu_ms);
  std::cout << ",\"wal_encode_samples\":";
  PrintValues(arm.wal_encode_ms);
  std::cout << ",\"wal_encode_p95_ms\":" << Percentile(arm.wal_encode_ms, 0.95)
            << ",\"wal_encode_cpu_samples\":";
  PrintValues(arm.wal_encode_cpu_ms);
  std::cout << ",\"wal_append_samples\":";
  PrintValues(arm.wal_append_ms);
  std::cout << ",\"wal_append_p95_ms\":" << Percentile(arm.wal_append_ms, 0.95)
            << ",\"wal_append_cpu_samples\":";
  PrintValues(arm.wal_append_cpu_ms);
  std::cout << ",\"query_wait_samples\":";
  PrintValues(arm.query_wait_ms);
  std::cout << ",\"query_wait_p95_ms\":" << Percentile(arm.query_wait_ms, 0.95)
            << ",\"apply_samples\":";
  PrintValues(arm.apply_ms);
  std::cout << ",\"apply_p95_ms\":" << Percentile(arm.apply_ms, 0.95)
            << ",\"auto_flush_samples\":";
  PrintValues(arm.auto_flush_ms);
  std::cout << ",\"auto_flush_p95_ms\":" << Percentile(arm.auto_flush_ms, 0.95)
            << ",\"reader_samples\":";
  PrintValues(arm.reader_ms);
  std::cout << ",\"reader_mutex_wait_samples\":";
  PrintValues(arm.reader_mutex_wait_ms);
  std::cout << ",\"reader_mutex_wait_p95_ms\":"
            << Percentile(arm.reader_mutex_wait_ms, 0.95)
            << ",\"reader_p50_ms\":" << Percentile(arm.reader_ms, 0.50)
            << ",\"reader_p95_ms\":" << Percentile(arm.reader_ms, 0.95)
            << ",\"writer_failures\":" << arm.writer_failures
            << ",\"reader_failures\":" << arm.reader_failures
            << ",\"durability_failures\":" << arm.durability_failures
            << ",\"runs_after_flush\":" << arm.runs_after_flush << '}'
            << (last ? "\n" : ",");
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 6) {
    std::cerr << "usage: m4_compaction_writer_contention <root> <jieba-dict> "
                 "--m4-compaction-contention <documents> <writer-operations>\n";
    return 2;
  }
  const fs::path root(argv[1]);
  const std::string dict_dir(argv[2]);
  if (!fs::is_directory(dict_dir) ||
      std::string(argv[3]) != "--m4-compaction-contention") {
    std::cerr << "missing Jieba dictionary or invalid workload mode\n";
    return 2;
  }
  const size_t documents = std::stoull(argv[4]);
  const size_t writer_operation_count = std::stoull(argv[5]);
  constexpr size_t writers = 4;
  if (documents < 1024 || documents % 1024 != 0 || writer_operation_count == 0 ||
      writer_operation_count % writers != 0 || writer_operation_count / writers > 64) {
    std::cerr << "workload dimensions outside bounded experimental limits\n";
    return 2;
  }
  const size_t ops_per_writer = writer_operation_count / writers;

  std::vector<std::string> order{"default", "snapshot", "snapshot_rebase",
                                 "apply_batch"};
  if (std::hash<std::string>{}(root.string()) % 2 != 0) {
    std::reverse(order.begin(), order.end());
  }
  std::vector<ArmResult> results;
  for (const auto& name : order) {
    results.push_back(RunArm(root / name, name, dict_dir, documents, writers,
                             ops_per_writer));
  }
  size_t failures = 0;
  for (const auto& result : results) {
    failures += result.writer_failures + result.reader_failures +
                result.durability_failures;
  }
  std::cout << "{\"mode\":\"m4_compaction_contention\",\"timing_schema_version\":4,\"documents\":"
            << documents << ",\"writers\":" << writers
            << ",\"ops_per_writer\":" << ops_per_writer
            << ",\"rounds\":" << writer_operation_count
            << ",\"writer_operations\":" << writer_operation_count
            << ",\"reader_queries\":" << 2 * 32
            << ",\"coherence_errors\":0,\"failures\":" << failures
            << ",\"arm_order\":[";
  for (size_t i = 0; i < order.size(); ++i) {
    if (i != 0) std::cout << ',';
    std::cout << '\"' << order[i] << '\"';
  }
  std::cout << "],\"arms\":[\n";
  for (size_t i = 0; i < results.size(); ++i) {
    PrintArm(results[i], i + 1 == results.size());
  }
  std::cout << "]}\n";
  return failures == 0 ? 0 : 1;
}
