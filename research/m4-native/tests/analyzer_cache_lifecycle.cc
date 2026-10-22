// Research-only M4 analyzer-cache lifecycle, identity, race and TOCTOU tests.
#include <condition_variable>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <mutex>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

#include "m4/index.h"
#include "../src/tokenizer.h"

#ifndef M4_ZVEC_JIEBA_DICT_DIR
#error "CMake must provide the native cppjieba dictionary path"
#endif

namespace fs = std::filesystem;
namespace {
int checks = 0;
int failures = 0;

void Check(bool ok, const std::string& label) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL: " << label << '\n';
  }
}

std::string ReadFile(const fs::path& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("read " + path.string());
  return std::string(std::istreambuf_iterator<char>(in),
                     std::istreambuf_iterator<char>());
}

void WriteFile(const fs::path& path, const std::string& bytes) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  if (!out || !out.write(bytes.data(), static_cast<std::streamsize>(bytes.size())))
    throw std::runtime_error("write " + path.string());
}

void CopyDictionary(const fs::path& from, const fs::path& to) {
  fs::create_directories(to);
  for (const char* name : {"jieba.dict.utf8", "hmm_model.utf8"})
    fs::copy_file(from / name, to / name, fs::copy_options::overwrite_existing);
}

std::string MutatedSameLength(const std::string& bytes) {
  const std::string from = "AT&T 3 nz\n";
  const std::string to = "AT&T 4 nz\n";
  const size_t at = bytes.find(from);
  if (at == std::string::npos) throw std::runtime_error("pinned dictionary fixture row missing");
  std::string changed = bytes;
  changed.replace(at, from.size(), to);
  if (changed.size() != bytes.size()) throw std::runtime_error("mutation changed dictionary size");
  return changed;
}

void WriteWithOriginalMtime(const fs::path& path, const std::string& bytes,
                            const struct stat& before) {
  WriteFile(path, bytes);
  const timespec times[2] = {before.st_atim, before.st_mtim};
  if (::utimensat(AT_FDCWD, path.c_str(), times, 0) != 0)
    throw std::runtime_error("restore dictionary timestamps");
  struct stat after {};
  if (::stat(path.c_str(), &after) != 0 ||
      after.st_size != before.st_size ||
      after.st_mtim.tv_sec != before.st_mtim.tv_sec ||
      after.st_mtim.tv_nsec != before.st_mtim.tv_nsec)
    throw std::runtime_error("same-size/same-mtime mutation fixture failed");
}

m4::Options JiebaOptions(const fs::path& dictionary, bool optimize = false) {
  m4::Options options;
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary.string();
  options.reuse_analyzer_cache = true;
  options.skip_cached_analyzer_fingerprint_verification = optimize;
  return options;
}

std::unique_ptr<m4::Index> OpenIndex(const fs::path& root,
                                     const m4::Options& options,
                                     m4::OpenDiagnostics* diagnostics = nullptr) {
  std::unique_ptr<m4::Index> index;
  const m4::Status status = m4::Index::Open(root.string(), options, &index, diagnostics);
  if (!status.ok) throw std::runtime_error("open " + root.string() + ": " + status.message);
  return index;
}

int ColdProcessReopen(const fs::path& root, const fs::path& dictionary) {
  try {
    m4::OpenDiagnostics diagnostics{};
    auto index = OpenIndex(root, JiebaOptions(dictionary, true), &diagnostics);
    const auto stats = m4::Analyzer::CacheStats();
    const bool verified = !diagnostics.analyzer_cache_hit &&
        !diagnostics.fingerprint_verification_skipped &&
        diagnostics.analyzer_fingerprint_calls == 2 &&
        stats.lookup_misses == 1 && stats.initialization_attempts == 1;
    if (!verified) {
      std::cerr << "cold-process reopen diagnostics mismatch: cache_hit="
                << diagnostics.analyzer_cache_hit << " skipped="
                << diagnostics.fingerprint_verification_skipped << " fingerprints="
                << diagnostics.analyzer_fingerprint_calls << " misses="
                << stats.lookup_misses << " initializations="
                << stats.initialization_attempts << '\n';
      return 1;
    }
    index.reset();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "cold-process reopen failed: " << error.what() << '\n';
    return 2;
  }
}

bool ExecColdProcessReopen(const fs::path& executable, const fs::path& root,
                           const fs::path& dictionary) {
  const pid_t child = ::fork();
  if (child < 0) return false;
  if (child == 0) {
    ::execl(executable.c_str(), executable.c_str(), "--cold-process-reopen",
            root.c_str(), dictionary.c_str(), static_cast<char*>(nullptr));
    ::_exit(127);
  }
  int status = 0;
  pid_t waited;
  do {
    waited = ::waitpid(child, &status, 0);
  } while (waited < 0 && errno == EINTR);
  return waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 4 && std::string(argv[1]) == "--cold-process-reopen")
    return ColdProcessReopen(argv[2], argv[3]);
  if (argc != 1) return 2;
  std::string temporary = (fs::temp_directory_path() / "m4-analyzer-cache-lifecycle-XXXXXX").string();
  std::vector<char> temp_buffer(temporary.begin(), temporary.end());
  temp_buffer.push_back('\0');
  char* root_name = ::mkdtemp(temp_buffer.data());
  if (!root_name) {
    std::cerr << "FAIL mkdtemp\n";
    return 1;
  }
  const fs::path base(root_name);
  try {
    const fs::path source(M4_ZVEC_JIEBA_DICT_DIR);
    const fs::path dictionary = base / "dictionary";
    const fs::path relocated_dictionary = base / "relocated-dictionary";
    CopyDictionary(source, dictionary);
    CopyDictionary(source, relocated_dictionary);
    const fs::path dictionary_file = dictionary / "jieba.dict.utf8";
    const std::string original_dictionary = ReadFile(dictionary_file);
    const struct stat original_stat = [&] {
      struct stat value {};
      if (::stat(dictionary_file.c_str(), &value) != 0)
        throw std::runtime_error("stat dictionary");
      return value;
    }();

    // Strong ownership follows each live generation. Closing one generation
    // must not evict the analyzer while another generation still references it.
    const auto lifecycle_before = m4::Analyzer::CacheStats();
    auto g1 = OpenIndex(base / "generation-1", JiebaOptions(dictionary));
    auto g2_diag = m4::OpenDiagnostics{};
    auto g2 = OpenIndex(base / "generation-2",
                        JiebaOptions(relocated_dictionary), &g2_diag);
    Check(g2_diag.analyzer_cache_hit && !g2_diag.analyzer_initialization_attempted,
          "second live generation reuses the initialized analyzer");
    Check(g2_diag.analyzer_fingerprint_calls == 2,
          "B3.1 cache arm retains both pre/post fingerprint passes");
    g1.reset();
    auto g3_diag = m4::OpenDiagnostics{};
    auto g3 = OpenIndex(base / "generation-3", JiebaOptions(dictionary), &g3_diag);
    Check(g3_diag.analyzer_cache_hit && !g3_diag.analyzer_cache_expired_entry,
          "third generation hits while generation 2 owns a strong reference");
    g2.reset();
    Check(g3->GetStats().live_docs == 0,
          "closing older generations leaves the newest generation usable");

    m4::Options standard_options;
    auto standard_index = OpenIndex(base / "standard-configuration", standard_options);
    const auto config_stats_before = m4::Analyzer::CacheStats();
    std::unique_ptr<m4::Index> wrong_configuration;
    const m4::Status config_mismatch = m4::Index::Open(
        (base / "standard-configuration").string(), JiebaOptions(dictionary, true),
        &wrong_configuration);
    const auto config_stats_after = m4::Analyzer::CacheStats();
    Check(!config_mismatch.ok &&
              config_mismatch.message.find("analyzer identity mismatch") != std::string::npos,
          "tokenizer configuration is included in persistent analyzer identity");
    Check(config_stats_after.lookups == config_stats_before.lookups,
          "wrong tokenizer configuration is rejected before cache lookup");

    // Candidate C uses one full dictionary fingerprint on a cache hit only.
    auto optimized_diag = m4::OpenDiagnostics{};
    auto g4 = OpenIndex(base / "generation-4",
                        JiebaOptions(dictionary, true), &optimized_diag);
    Check(optimized_diag.analyzer_cache_hit &&
              optimized_diag.fingerprint_verification_skipped,
          "verified cache hit skips only the redundant post-init reread");
    Check(optimized_diag.analyzer_fingerprint_calls == 1 &&
              optimized_diag.analyzer_fingerprint_sha256_calls == 3,
          "cache hit performs one fingerprint pass with two file digests and one composite digest");
    const uint64_t expected_dictionary_bytes =
        fs::file_size(dictionary / "jieba.dict.utf8") +
        fs::file_size(dictionary / "hmm_model.utf8");
    Check(optimized_diag.analyzer_fingerprint_dictionary_bytes == expected_dictionary_bytes,
          "fingerprint instrumentation counts exact dictionary/model bytes");
    Check(optimized_diag.analyzer_fingerprint_sha256_bytes >= expected_dictionary_bytes,
          "SHA-256 byte counter includes the full dictionary/model payload");

    // Content identity detects same-length in-place mutation even when mtime is
    // restored exactly. Existing open generations keep their immutable analyzer.
    std::string changed_dictionary = MutatedSameLength(original_dictionary);
    WriteWithOriginalMtime(dictionary_file, changed_dictionary, original_stat);
    auto mismatch_diag = m4::OpenDiagnostics{};
    std::unique_ptr<m4::Index> mismatched;
    const m4::Status mismatch = m4::Index::Open(
        (base / "generation-1").string(), JiebaOptions(dictionary, true),
        &mismatched, &mismatch_diag);
    Check(!mismatch.ok && mismatch.message.find("analyzer identity mismatch") != std::string::npos,
          "same-size/same-mtime content mutation rejects reopen before recovery");
    Check(!mismatch_diag.analyzer_cache_hit && !mismatch_diag.analyzer_cache_miss,
          "manifest identity mismatch is rejected before cache lookup");
    Check(g3->GetStats().live_docs == 0,
          "already-open generation remains valid after source dictionary mutation");
    g3.reset();

    // The native cppjieba parsed-dictionary cache is keyed by dictionary path
    // and process-lifetime. Sequential uncached opens must therefore reuse one
    // content-addressed snapshot path, while releasing its files after each
    // last live analyzer lease.
    m4::Options uncached_options = JiebaOptions(dictionary);
    uncached_options.reuse_analyzer_cache = false;
    std::string uncached_snapshot_path;
    bool same_snapshot_path = true;
    m4::analyzer_test::SetBeforeNativeInitializationHook(
        [&](const std::string& path) {
          if (uncached_snapshot_path.empty()) uncached_snapshot_path = path;
          else same_snapshot_path = same_snapshot_path && path == uncached_snapshot_path;
        });
    for (size_t generation = 0; generation < 4; ++generation) {
      m4::OpenDiagnostics uncached_diag{};
      auto uncached = OpenIndex(base / ("uncached-generation-" +
          std::to_string(generation)), uncached_options, &uncached_diag);
      Check(uncached_diag.analyzer_initialization_attempted &&
                uncached_diag.analyzer_dictionary_snapshot_used &&
                !uncached_diag.analyzer_cache_hit,
            "uncached generation performs native initialization from its snapshot");
      Check(fs::is_directory(uncached_snapshot_path),
            "content-addressed snapshot exists while an uncached analyzer is live");
      uncached.reset();
      Check(!fs::exists(uncached_snapshot_path),
            "last uncached analyzer lease removes snapshot files");
    }
    m4::analyzer_test::SetBeforeNativeInitializationHook({});
    Check(same_snapshot_path && !uncached_snapshot_path.empty(),
          "sequential uncached opens reuse one stable native dictionary cache key");

    // Simultaneous uncached initializations must share the same immutable
    // snapshot path without releasing it until the final analyzer lease ends.
    constexpr size_t concurrent_open_count = 4;
    std::mutex snapshot_paths_mutex;
    std::condition_variable snapshot_paths_cv;
    size_t snapshot_initializers_arrived = 0;
    std::vector<std::string> concurrent_snapshot_paths;
    m4::analyzer_test::SetBeforeNativeInitializationHook(
        [&](const std::string& path) {
          std::unique_lock<std::mutex> lock(snapshot_paths_mutex);
          concurrent_snapshot_paths.push_back(path);
          ++snapshot_initializers_arrived;
          snapshot_paths_cv.notify_all();
          snapshot_paths_cv.wait_for(lock, std::chrono::seconds(10), [&] {
            return snapshot_initializers_arrived == concurrent_open_count;
          });
        });
    std::vector<std::unique_ptr<m4::Index>> concurrent_uncached(concurrent_open_count);
    std::vector<std::string> concurrent_open_errors(concurrent_open_count);
    std::vector<std::thread> concurrent_openers;
    for (size_t i = 0; i < concurrent_open_count; ++i) {
      concurrent_openers.emplace_back([&, i] {
        try {
          concurrent_uncached[i] = OpenIndex(
              base / ("concurrent-uncached-generation-" + std::to_string(i)),
              uncached_options);
        } catch (const std::exception& error) {
          concurrent_open_errors[i] = error.what();
        }
      });
    }
    for (auto& opener : concurrent_openers) opener.join();
    m4::analyzer_test::SetBeforeNativeInitializationHook({});
    bool concurrent_opens_succeeded = snapshot_initializers_arrived == concurrent_open_count;
    for (size_t i = 0; i < concurrent_open_count; ++i)
      concurrent_opens_succeeded = concurrent_opens_succeeded &&
          concurrent_open_errors[i].empty() && concurrent_uncached[i] != nullptr;
    Check(concurrent_opens_succeeded,
          "concurrent uncached opens reach native initialization and succeed");
    bool concurrent_paths_match = concurrent_snapshot_paths.size() == concurrent_open_count;
    for (const auto& path : concurrent_snapshot_paths)
      concurrent_paths_match = concurrent_paths_match &&
          path == concurrent_snapshot_paths.front();
    const std::string concurrent_snapshot_path = concurrent_paths_match
        ? concurrent_snapshot_paths.front() : std::string{};
    Check(concurrent_paths_match && fs::is_directory(concurrent_snapshot_path),
          "concurrent analyzer leases use one live content-addressed snapshot");

    std::vector<int> concurrent_query_ok(concurrent_open_count, 0);
    std::vector<std::thread> concurrent_readers;
    for (size_t i = 0; i < concurrent_open_count; ++i) {
      concurrent_readers.emplace_back([&, i] {
        m4::SearchResult result;
        const auto status = concurrent_uncached[i]->SearchFts("量子猫星球", 10, &result);
        concurrent_query_ok[i] = status.ok ? 1 : 0;
      });
    }
    for (auto& reader : concurrent_readers) reader.join();
    bool concurrent_queries_succeeded = true;
    for (const int ok : concurrent_query_ok)
      concurrent_queries_succeeded = concurrent_queries_succeeded && ok != 0;
    Check(concurrent_queries_succeeded,
          "concurrent readers tokenize through live snapshot-backed analyzers");

    for (size_t i = 0; i + 1 < concurrent_open_count; ++i)
      concurrent_uncached[i].reset();
    Check(fs::is_directory(concurrent_snapshot_path),
          "closing all but one concurrent analyzer preserves its snapshot lease");
    concurrent_uncached.back().reset();
    Check(!fs::exists(concurrent_snapshot_path),
          "last concurrent analyzer release removes the shared snapshot files");

    auto changed_dict_index = OpenIndex(base / "changed-dictionary-generation",
                                      JiebaOptions(dictionary, true));
    Check(changed_dict_index->GetStats().live_docs == 0,
          "new generation initializes from changed dictionary content");
    WriteWithOriginalMtime(dictionary_file, original_dictionary, original_stat);
    auto restored_diag = m4::OpenDiagnostics{};
    auto restored = OpenIndex(base / "generation-1",
                              JiebaOptions(dictionary, true), &restored_diag);
    Check(restored_diag.analyzer_cache_hit && restored_diag.fingerprint_verification_skipped,
          "restored content returns to the original verified cache identity");

    // Reproduce a transient TOCTOU: the dictionary changes after its identity
    // fingerprint, native Jieba reads the changed file, and the source is
    // restored before the post-init fingerprint. The opened analyzer must
    // still match the exact bytes represented by the persistent identity.
    const fs::path toctou_dictionary = base / "toctou-dictionary";
    CopyDictionary(source, toctou_dictionary);
    const std::string toctou_original = ReadFile(toctou_dictionary / "jieba.dict.utf8");
    const std::string unique_row = "量子猫星球 1000000 n\n";
    WriteFile(toctou_dictionary / "jieba.dict.utf8", toctou_original + unique_row);
    const fs::path toctou_file = toctou_dictionary / "jieba.dict.utf8";
    const std::string toctou_bytes = ReadFile(toctou_file);
    const struct stat toctou_stat = [&] {
      struct stat value {};
      if (::stat(toctou_file.c_str(), &value) != 0)
        throw std::runtime_error("stat TOCTOU dictionary");
      return value;
    }();
    const std::string toctou_changed = MutatedSameLength(toctou_bytes);
    const std::string hmm_bytes = ReadFile(toctou_dictionary / "hmm_model.utf8");
    bool mutation_applied = false;
    bool restored_after_init = false;
    bool immutable_snapshot_verified = false;
    std::string snapshot_directory;
    m4::analyzer_test::SetAfterCacheMissHook([&] {
      WriteWithOriginalMtime(toctou_file, toctou_changed, toctou_stat);
      mutation_applied = true;
    });
    m4::analyzer_test::SetBeforeNativeInitializationHook([&](const std::string& snapshot_dir) {
      snapshot_directory = snapshot_dir;
      immutable_snapshot_verified = snapshot_dir != toctou_dictionary.string() &&
          ReadFile(fs::path(snapshot_dir) / "jieba.dict.utf8") == toctou_bytes &&
          ReadFile(fs::path(snapshot_dir) / "hmm_model.utf8") == hmm_bytes &&
          ReadFile(toctou_file) != toctou_bytes;
    });
    m4::analyzer_test::SetAfterInitializationHook([&] {
      if (mutation_applied && !restored_after_init) {
        WriteWithOriginalMtime(toctou_file, toctou_bytes, toctou_stat);
        restored_after_init = true;
      }
    });
    m4::OpenDiagnostics toctou_diag;
    std::unique_ptr<m4::Index> toctou_index;
    const m4::Status toctou_status = m4::Index::Open(
        (base / "toctou-index").string(), JiebaOptions(toctou_dictionary, true),
        &toctou_index, &toctou_diag);
    m4::analyzer_test::SetAfterCacheMissHook({});
    m4::analyzer_test::SetBeforeNativeInitializationHook({});
    m4::analyzer_test::SetAfterInitializationHook({});
    Check(mutation_applied && restored_after_init && toctou_status.ok,
          "transient dictionary change is linearized to the fingerprint snapshot");
    Check(immutable_snapshot_verified && toctou_diag.analyzer_dictionary_snapshot_used,
          "native Jieba initializes from the exact immutable bytes fingerprinted before the source changed");

    // A persistent source change is still rejected against the manifest before
    // the weak registry can return an analyzer with the old identity.
    WriteWithOriginalMtime(toctou_file, toctou_changed, toctou_stat);
    auto persistent_mismatch_diag = m4::OpenDiagnostics{};
    std::unique_ptr<m4::Index> persistent_mismatch;
    const m4::Status persistent_mismatch_status = m4::Index::Open(
        (base / "toctou-index").string(), JiebaOptions(toctou_dictionary, true),
        &persistent_mismatch, &persistent_mismatch_diag);
    Check(!persistent_mismatch_status.ok &&
              persistent_mismatch_status.message.find("analyzer identity mismatch") != std::string::npos,
          "persistent same-size/same-mtime mutation remains fail-closed");
    Check(!persistent_mismatch_diag.analyzer_cache_hit &&
              !persistent_mismatch_diag.analyzer_cache_miss,
          "persistent mutation is rejected before cache lookup");
    WriteWithOriginalMtime(toctou_file, toctou_bytes, toctou_stat);
    auto toctou_second_diag = m4::OpenDiagnostics{};
    auto toctou_second = OpenIndex(base / "toctou-generation-2",
        JiebaOptions(toctou_dictionary, true), &toctou_second_diag);
    Check(toctou_second_diag.analyzer_cache_hit &&
              !toctou_second_diag.analyzer_dictionary_snapshot_used &&
              fs::exists(snapshot_directory),
          "shared analyzer snapshot remains live across generations without recopying files");
    toctou_index.reset();
    Check(fs::exists(snapshot_directory),
          "closing one generation retains the snapshot owned by another generation");
    toctou_second.reset();
    Check(!fs::exists(snapshot_directory),
          "last generation close releases the analyzer snapshot directory");

    // A non-transient change after native initialization is detected before
    // manifest publication, preserving the existing second-fingerprint guard.
    const fs::path after_init_dictionary = base / "after-init-dictionary";
    CopyDictionary(source, after_init_dictionary);
    const std::string after_init_bytes =
        ReadFile(after_init_dictionary / "jieba.dict.utf8") +
        "m4afterinitfreshword 1000000 n\n";
    const fs::path after_init_file = after_init_dictionary / "jieba.dict.utf8";
    WriteFile(after_init_file, after_init_bytes);
    struct stat after_init_stat {};
    if (::stat(after_init_file.c_str(), &after_init_stat) != 0)
      throw std::runtime_error("stat after-init dictionary");
    const std::string after_init_changed = MutatedSameLength(after_init_bytes);
    bool after_init_mutation_applied = false;
    m4::analyzer_test::SetAfterInitializationHook([&] {
      WriteWithOriginalMtime(after_init_file, after_init_changed, after_init_stat);
      after_init_mutation_applied = true;
    });
    std::unique_ptr<m4::Index> after_init_index;
    const m4::Status after_init_status = m4::Index::Open(
        (base / "after-init-index").string(),
        JiebaOptions(after_init_dictionary, true), &after_init_index);
    m4::analyzer_test::SetAfterInitializationHook({});
    Check(after_init_mutation_applied && !after_init_status.ok &&
              after_init_status.message.find("changed during initialization") != std::string::npos,
          "persistent mutation after initialization still fails closed");
    Check(!fs::exists(base / "after-init-index" / "manifest"),
          "post-init mutation rejection precedes manifest publication");

    // Hold the first cache miss after it claims the flight, then start a
    // second Open. It must join the in-flight initialization, not build a
    // duplicate native Jieba analyzer.
    const fs::path race_dictionary = base / "race-dictionary";
    CopyDictionary(source, race_dictionary);
    const fs::path race_dict_file = race_dictionary / "jieba.dict.utf8";
    const std::string race_bytes = ReadFile(race_dict_file) +
        "m4concurrentfreshword 1000000 n\n";
    WriteFile(race_dict_file, race_bytes);
    const auto race_before = m4::Analyzer::CacheStats();
    std::mutex gate_mutex;
    std::condition_variable gate_cv;
    bool owner_entered = false;
    bool release_owner = false;
    m4::analyzer_test::SetAfterCacheMissHook([&] {
      std::unique_lock<std::mutex> lock(gate_mutex);
      owner_entered = true;
      gate_cv.notify_all();
      gate_cv.wait(lock, [&] { return release_owner; });
    });
    std::unique_ptr<m4::Index> concurrent[2];
    m4::OpenDiagnostics concurrent_diagnostics[2];
    std::exception_ptr errors[2];
    std::thread workers[2];
    const auto concurrent_begin = std::chrono::steady_clock::now();
    workers[0] = std::thread([&] {
      try {
        concurrent[0] = OpenIndex(base / "race-generation-0",
            JiebaOptions(race_dictionary, true), &concurrent_diagnostics[0]);
      } catch (...) { errors[0] = std::current_exception(); }
    });
    bool owner_ready = false;
    {
      std::unique_lock<std::mutex> lock(gate_mutex);
      owner_ready = gate_cv.wait_for(lock, std::chrono::seconds(5),
                                     [&] { return owner_entered; });
    }
    workers[1] = std::thread([&] {
      try {
        concurrent[1] = OpenIndex(base / "race-generation-1",
            JiebaOptions(race_dictionary, true), &concurrent_diagnostics[1]);
      } catch (...) { errors[1] = std::current_exception(); }
    });
    const auto waiter_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (m4::Analyzer::CacheStats().cache_waits == race_before.cache_waits &&
           std::chrono::steady_clock::now() < waiter_deadline)
      std::this_thread::yield();
    const bool waiter_joined =
        m4::Analyzer::CacheStats().cache_waits > race_before.cache_waits;
    {
      std::lock_guard<std::mutex> lock(gate_mutex);
      release_owner = true;
    }
    gate_cv.notify_all();
    for (auto& worker : workers) worker.join();
    const double concurrent_cold_open_wall_ms =
        std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - concurrent_begin).count();
    m4::analyzer_test::SetAfterCacheMissHook({});
    for (const auto& error : errors) Check(!error, "concurrent Open completes without exception");
    Check(owner_ready && waiter_joined,
          "second Open joins the deterministically held cold-cache flight");
    Check(concurrent[0] && concurrent[1], "both racing generation opens succeed");
    const auto race_after = m4::Analyzer::CacheStats();
    Check(race_after.lookups - race_before.lookups == 2 &&
              race_after.lookup_misses - race_before.lookup_misses == 2,
          "both racing Opens record weak-cache misses");
    Check(race_after.cache_waits - race_before.cache_waits == 1 &&
              race_after.cache_wait_reuses - race_before.cache_wait_reuses == 1,
          "one racing Open waits for and reuses the single-flight result");
    Check(race_after.initialization_attempts - race_before.initialization_attempts == 1 &&
              race_after.successful_initializations - race_before.successful_initializations == 1,
          "concurrent cache miss constructs exactly one analyzer");
    Check(race_after.late_race_reuses == race_before.late_race_reuses,
          "single-flight eliminates late duplicate-construction reuse");
    Check(concurrent_diagnostics[0].analyzer_initialization_attempted &&
              concurrent_diagnostics[1].analyzer_cache_wait_reuse &&
              !concurrent_diagnostics[1].analyzer_initialization_attempted,
          "per-open diagnostics distinguish flight owner from waiter");
    std::cout << "concurrent_cold_open_wall_ms=" << concurrent_cold_open_wall_ms << '\n';

    concurrent[0].reset();
    auto held_generation_diag = m4::OpenDiagnostics{};
    auto held_generation = OpenIndex(base / "race-generation-3",
        JiebaOptions(race_dictionary, true), &held_generation_diag);
    Check(held_generation_diag.analyzer_cache_hit,
          "shared analyzer remains alive while one racing generation is pinned");
    concurrent[1].reset();
    held_generation.reset();
    const auto expired_before = m4::Analyzer::CacheStats();
    auto expired_diag = m4::OpenDiagnostics{};
    auto after_close = OpenIndex(base / "race-generation-after-close",
        JiebaOptions(race_dictionary, true), &expired_diag);
    const auto expired_after = m4::Analyzer::CacheStats();
    Check(expired_diag.analyzer_cache_expired_entry &&
              expired_after.expired_weak_entries > expired_before.expired_weak_entries,
          "closing every generation expires the weak entry and forces reconstruction");
    Check(!expired_diag.fingerprint_verification_skipped &&
              expired_diag.analyzer_fingerprint_calls == 2,
          "candidate cold-cache reopen retains the post-init fingerprint verification");
    Check(after_close->GetStats().live_docs == 0,
          "reopen after cache expiry succeeds with clean generation state");
    after_close.reset();
    std::error_code executable_error;
    const fs::path executable = fs::canonical(argv[0], executable_error);
    Check(!executable_error && ExecColdProcessReopen(
              executable, base / "race-generation-after-close", race_dictionary),
          "new process reopens the persisted generation with a cold cache and full fingerprint verification");

    const auto final_stats = m4::Analyzer::CacheStats();
    std::cout << "analyzer_cache_lifecycle checks=" << checks
              << " failures=" << failures
              << " cache_lookups=" << final_stats.lookups - lifecycle_before.lookups
              << " cache_hits=" << final_stats.lookup_hits - lifecycle_before.lookup_hits
              << " cache_misses=" << final_stats.lookup_misses - lifecycle_before.lookup_misses
              << " analyzer_init_attempts=" << final_stats.initialization_attempts - lifecycle_before.initialization_attempts
              << " init_successes=" << final_stats.successful_initializations - lifecycle_before.successful_initializations
              << " late_race_reuses=" << final_stats.late_race_reuses - lifecycle_before.late_race_reuses
              << " cache_waits=" << final_stats.cache_waits - lifecycle_before.cache_waits
              << " cache_wait_reuses=" << final_stats.cache_wait_reuses - lifecycle_before.cache_wait_reuses
              << " expired_weak_entries=" << final_stats.expired_weak_entries - lifecycle_before.expired_weak_entries
              << '\n';
  } catch (const std::exception& error) {
    m4::analyzer_test::SetAfterCacheMissHook({});
    m4::analyzer_test::SetBeforeNativeInitializationHook({});
    m4::analyzer_test::SetAfterInitializationHook({});
    ++failures;
    std::cerr << "FATAL: " << error.what() << '\n';
  }
  fs::remove_all(base);
  return failures ? 1 : 0;
}

// The test target is research-only. Keep every fixture in its temporary root;
// no files in the pinned native dictionary tree are changed.
