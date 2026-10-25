// M4 research prototype implementation.
//
// Design summary:
//   * One shared document/version catalog drives FTS and scalar INVERT.
//   * Writes append to a bounded in-memory delta and a WAL.
//   * Flush seals an immutable run; deletes only add tombstones.
//   * Tiered leveling bounds run count and therefore query fan-out.
//   * Compaction rewrites only live rows and drops tombstones.
//   * FTS block metadata stores raw max_tf/min_dl; BM25 bounds are computed at
//     query time from current global live stats (no stale IDF in the format).

#include "m4/index.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <lz4.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <future>
#include <iterator>
#include <limits>
#include <map>
#include <mutex>
#include <memory>
#include <optional>
#include <queue>
#include <set>
#include <shared_mutex>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <unordered_set>

#include "../include/m4/index.h"
#include "writer_preferred_shared_mutex.h"
#include "format.h"
#include "tokenizer.h"
#include "util.h"

#ifdef M4_USE_ZVEC_NATIVE_TOKENIZER
#include "db/index/column/fts_column/tokenizer/jieba_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/token_filter.h"
#endif

namespace m4 {
namespace {

struct DictionarySnapshotEntry {
  std::filesystem::path directory;
  size_t leases{0};
};

struct DictionarySnapshotRegistry {
  std::mutex mutex;
  std::filesystem::path root;
  std::unordered_map<std::string, DictionarySnapshotEntry> entries;

  ~DictionarySnapshotRegistry() {
    if (!root.empty()) {
      std::error_code error;
      std::filesystem::remove_all(root, error);
    }
  }
};

DictionarySnapshotRegistry& GetDictionarySnapshotRegistry() {
  static DictionarySnapshotRegistry registry;
  return registry;
}

struct DictionarySnapshotLease {
  std::string identity;

  ~DictionarySnapshotLease() {
    auto& registry = GetDictionarySnapshotRegistry();
    std::lock_guard<std::mutex> lock(registry.mutex);
    const auto found = registry.entries.find(identity);
    if (found == registry.entries.end() || found->second.leases == 0) return;
    if (--found->second.leases == 0) {
      std::error_code error;
      std::filesystem::remove_all(found->second.directory, error);
      // Keep the entry only when cleanup failed so a later acquire can verify
      // and safely reuse the residual path. Successful release needs no
      // per-version registry metadata; the native cppjieba cache is separate.
      if (!error) registry.entries.erase(found);
    }
  }
};

Status AcquireDictionarySnapshot(
    const std::string& identity,
    const std::vector<AnalyzerDictionaryFileSnapshot>& files,
    std::string* directory, std::shared_ptr<void>* lease) {
  if (!directory || !lease || identity.size() != 64 || files.empty() ||
      !std::all_of(identity.begin(), identity.end(), [](unsigned char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
      })) {
    return Status::Error("invalid content-addressed dictionary snapshot identity");
  }

  auto& registry = GetDictionarySnapshotRegistry();
  std::lock_guard<std::mutex> lock(registry.mutex);
  if (registry.root.empty()) {
    std::string pattern =
        (std::filesystem::temp_directory_path() /
         "m4-analyzer-snapshot-cache-XXXXXX").string();
    std::vector<char> buffer(pattern.begin(), pattern.end());
    buffer.push_back('\0');
    char* created = ::mkdtemp(buffer.data());
    if (!created) {
      return Status::Error("JiebaTokenizer could not create snapshot cache root");
    }
    registry.root = created;
  }

  auto [entry_it, inserted] = registry.entries.try_emplace(
      identity, DictionarySnapshotEntry{registry.root / identity, 0});
  auto& entry = entry_it->second;
  std::error_code error;
  bool created_directory = false;
  if (!std::filesystem::is_directory(entry.directory, error) || error) {
    error.clear();
    created_directory = std::filesystem::create_directory(entry.directory, error);
    if (error || !created_directory) {
      if (inserted) registry.entries.erase(entry_it);
      return Status::Error("JiebaTokenizer could not create immutable dictionary snapshot");
    }
  }

  for (const auto& file : files) {
    if (file.filename != "jieba.dict.utf8" && file.filename != "hmm_model.utf8") {
      if (created_directory) std::filesystem::remove_all(entry.directory, error);
      if (inserted) registry.entries.erase(entry_it);
      return Status::Error("JiebaTokenizer snapshot contains an unexpected file name");
    }
    const auto path = entry.directory / file.filename;
    if (created_directory) {
      std::ofstream output(path, std::ios::binary | std::ios::trunc);
      if (!output ||
          !output.write(file.contents.data(),
                        static_cast<std::streamsize>(file.contents.size()))) {
        const int write_error = errno;
        output.close();
        std::filesystem::remove_all(entry.directory, error);
        if (inserted) registry.entries.erase(entry_it);
        std::string message = "JiebaTokenizer could not write immutable dictionary snapshot at " +
                             path.string();
        message += write_error == 0
                       ? ": stream write failed without an OS error"
                       : ": " + std::string(std::strerror(write_error));
        return Status::Error(std::move(message));
      }
      output.close();
      if (!output) {
        std::filesystem::remove_all(entry.directory, error);
        if (inserted) registry.entries.erase(entry_it);
        return Status::Error("JiebaTokenizer could not close immutable dictionary snapshot");
      }
    } else if (entry.leases == 0) {
      // A prior cleanup may have failed. Reuse the stable path only if its
      // existing bytes still match the content fingerprint being requested.
      std::ifstream input(path, std::ios::binary);
      const std::string contents((std::istreambuf_iterator<char>(input)),
                                 std::istreambuf_iterator<char>());
      if (!input || contents != file.contents) {
        return Status::Error("JiebaTokenizer cached snapshot content mismatch");
      }
    }
  }

  auto token = std::make_shared<DictionarySnapshotLease>();
  token->identity = identity;
  ++entry.leases;
  *directory = entry.directory.string();
  *lease = std::move(token);
  return Status::OK();
}

}  // namespace

struct Analyzer::Impl {
  bool initialized{false};
  bool use_jieba{false};
  std::string dictionary_snapshot_dir;
  std::shared_ptr<void> dictionary_snapshot_lease;
#ifdef M4_USE_ZVEC_NATIVE_TOKENIZER
  zvec::fts::JiebaTokenizer jieba;
  zvec::fts::LowercaseTokenFilter lowercase;
#endif
};

struct Analyzer::CacheRegistry {
  struct FlightResult {
    std::shared_ptr<Impl> impl;
    Status status;
  };
  std::mutex mutex;
  std::unordered_map<std::string, std::weak_ptr<Impl>> entries;
  std::unordered_map<std::string, std::shared_future<FlightResult>> in_flight;
  std::atomic<uint64_t> lookups{0};
  std::atomic<uint64_t> lookup_hits{0};
  std::atomic<uint64_t> lookup_misses{0};
  std::atomic<uint64_t> expired_weak_entries{0};
  std::atomic<uint64_t> initialization_attempts{0};
  std::atomic<uint64_t> successful_initializations{0};
  std::atomic<uint64_t> late_race_reuses{0};
  std::atomic<uint64_t> cache_waits{0};
  std::atomic<uint64_t> cache_wait_reuses{0};
  std::atomic<uint64_t> cache_bypasses{0};
  std::function<void()> after_cache_miss_hook;
  std::function<void(const std::string&)> before_native_initialization_hook;
  std::function<void()> after_initialization_hook;
};

Analyzer::CacheRegistry& Analyzer::Registry() {
  static CacheRegistry registry;
  return registry;
}

Analyzer::Analyzer() : impl_(std::make_shared<Impl>()) {}
Analyzer::~Analyzer() = default;

AnalyzerCacheStats Analyzer::CacheStats() {
  auto& registry = Registry();
  return AnalyzerCacheStats{
      registry.lookups.load(),
      registry.lookup_hits.load(),
      registry.lookup_misses.load(),
      registry.expired_weak_entries.load(),
      registry.initialization_attempts.load(),
      registry.successful_initializations.load(),
      registry.late_race_reuses.load(),
      registry.cache_waits.load(),
      registry.cache_wait_reuses.load(),
      registry.cache_bypasses.load()};
}

namespace analyzer_test {
void SetAfterCacheMissHook(std::function<void()> hook) {
  auto& registry = Analyzer::Registry();
  std::lock_guard<std::mutex> lock(registry.mutex);
  registry.after_cache_miss_hook = std::move(hook);
}

void SetBeforeNativeInitializationHook(
    std::function<void(const std::string&)> hook) {
  auto& registry = Analyzer::Registry();
  std::lock_guard<std::mutex> lock(registry.mutex);
  registry.before_native_initialization_hook = std::move(hook);
}

void SetAfterInitializationHook(std::function<void()> hook) {
  auto& registry = Analyzer::Registry();
  std::lock_guard<std::mutex> lock(registry.mutex);
  registry.after_initialization_hook = std::move(hook);
}
}  // namespace analyzer_test

Status Analyzer::Init(
    const std::string& tokenizer_name, const std::string& jieba_dict_dir,
    const std::string& cache_identity, bool* cache_hit,
    AnalyzerInitDiagnostics* diagnostics,
    const std::vector<AnalyzerDictionaryFileSnapshot>* dictionary_snapshot,
    const std::string& dictionary_snapshot_identity) {
  if (cache_hit) *cache_hit = false;
  if (diagnostics) *diagnostics = AnalyzerInitDiagnostics{};
  auto& registry = Registry();
  std::function<void()> after_miss_hook;
  std::shared_ptr<std::promise<CacheRegistry::FlightResult>> leader_promise;
  std::shared_future<CacheRegistry::FlightResult> wait_future;
  std::string cache_key;
  bool cache_enabled = !cache_identity.empty();
  bool flight_owner = false;
  if (cache_enabled) {
    // The fingerprint is content-addressed by Index::Open; include the
    // tokenizer selector as a second guard against accidental cross-config
    // reuse by internal callers.
    cache_key = tokenizer_name;
    cache_key.push_back('\0');
    cache_key += cache_identity;
    std::lock_guard<std::mutex> lock(registry.mutex);
    registry.lookups.fetch_add(1);
    if (diagnostics) diagnostics->cache_lookup = true;
    const auto found = registry.entries.find(cache_key);
    if (found != registry.entries.end()) {
      if (auto shared = found->second.lock()) {
        impl_ = std::move(shared);
        registry.lookup_hits.fetch_add(1);
        if (cache_hit) *cache_hit = true;
        if (diagnostics) diagnostics->initial_cache_hit = true;
        return Status::OK();
      }
      registry.expired_weak_entries.fetch_add(1);
      if (diagnostics) diagnostics->expired_weak_entry = true;
      registry.entries.erase(found);
    }
    registry.lookup_misses.fetch_add(1);
    if (diagnostics) diagnostics->cache_miss = true;
    const auto flight = registry.in_flight.find(cache_key);
    if (flight != registry.in_flight.end()) {
      wait_future = flight->second;
      registry.cache_waits.fetch_add(1);
      if (diagnostics) diagnostics->cache_waited = true;
    } else {
      after_miss_hook = registry.after_cache_miss_hook;
      leader_promise = std::make_shared<std::promise<CacheRegistry::FlightResult>>();
      wait_future = leader_promise->get_future().share();
      registry.in_flight.emplace(cache_key, wait_future);
      flight_owner = true;
    }
  } else {
    registry.cache_bypasses.fetch_add(1);
  }
  if (cache_enabled && !flight_owner) {
    const CacheRegistry::FlightResult result = wait_future.get();
    if (!result.status.ok) return result.status;
    impl_ = result.impl;
    registry.cache_wait_reuses.fetch_add(1);
    if (cache_hit) *cache_hit = true;
    if (diagnostics) diagnostics->cache_wait_reuse = true;
    return Status::OK();
  }
  auto complete_flight = [&](Status status) {
    if (!flight_owner) return;
    {
      std::lock_guard<std::mutex> lock(registry.mutex);
      registry.in_flight.erase(cache_key);
    }
    CacheRegistry::FlightResult result;
    result.impl = status.ok ? impl_ : nullptr;
    result.status = std::move(status);
    leader_promise->set_value(std::move(result));
    flight_owner = false;
  };
  auto fail_initialization = [&](Status status) {
    complete_flight(status);
    return status;
  };
  auto note_initialization_attempt = [&]() {
    registry.initialization_attempts.fetch_add(1);
    if (diagnostics) diagnostics->initialization_attempted = true;
  };
  auto note_initialization_success = [&]() {
    registry.successful_initializations.fetch_add(1);
    if (diagnostics) diagnostics->initialization_succeeded = true;
  };
  auto run_after_init_hook = [&]() {
    std::function<void()> hook;
    {
      std::lock_guard<std::mutex> lock(registry.mutex);
      hook = registry.after_initialization_hook;
    }
    if (hook) hook();
  };
  auto publish_initialized = [&]() {
    note_initialization_success();
    run_after_init_hook();
    if (!cache_enabled) return;
    {
      std::lock_guard<std::mutex> lock(registry.mutex);
      auto& entry = registry.entries[cache_key];
      if (auto shared = entry.lock()) {
        // Retain a defensive late-race path for internal callers that may
        // publish without joining the current single-flight entry.
        impl_ = std::move(shared);
        registry.late_race_reuses.fetch_add(1);
        if (cache_hit) *cache_hit = true;
        if (diagnostics) diagnostics->late_race_reuse = true;
      } else {
        entry = impl_;
      }
      if (registry.entries.size() > 64) {
        for (auto it = registry.entries.begin(); it != registry.entries.end();) {
          if (it->second.expired()) it = registry.entries.erase(it);
          else ++it;
        }
      }
    }
    complete_flight(Status::OK());
  };

  try {
  if (after_miss_hook) after_miss_hook();
  impl_ = std::make_shared<Impl>();
  impl_->initialized = false;
  impl_->use_jieba = false;
  if (tokenizer_name == "standard") {
    note_initialization_attempt();
    impl_->initialized = true;
    publish_initialized();
    return Status::OK();
  }
  if (tokenizer_name != "jieba") {
    return fail_initialization(
        Status::Error("unsupported M4 FTS tokenizer: " + tokenizer_name));
  }
  if (jieba_dict_dir.empty()) {
    return fail_initialization(Status::Error(
        "JiebaTokenizer: explicit jieba_dict_dir is required by M4"));
  }
#ifndef M4_USE_ZVEC_NATIVE_TOKENIZER
  return fail_initialization(Status::Error(
      "JiebaTokenizer requires an M4_ZVEC_ROOT native-source build"));
#else
  std::string init_dict_dir = jieba_dict_dir;
  if (dictionary_snapshot && !dictionary_snapshot->empty()) {
    std::string snapshot_dir;
    std::shared_ptr<void> snapshot_lease;
    const Status snapshot_status = AcquireDictionarySnapshot(
        dictionary_snapshot_identity, *dictionary_snapshot, &snapshot_dir,
        &snapshot_lease);
    if (!snapshot_status.ok) return fail_initialization(snapshot_status);
    impl_->dictionary_snapshot_dir = snapshot_dir;
    impl_->dictionary_snapshot_lease = std::move(snapshot_lease);
    init_dict_dir = std::move(snapshot_dir);
    if (diagnostics) diagnostics->dictionary_snapshot_used = true;
    std::function<void(const std::string&)> hook;
    {
      std::lock_guard<std::mutex> lock(registry.mutex);
      hook = registry.before_native_initialization_hook;
    }
    if (hook) hook(init_dict_dir);
  }
  const std::filesystem::path dict_dir(init_dict_dir);
  for (const char* filename : {"jieba.dict.utf8", "hmm_model.utf8"}) {
    const auto path = dict_dir / filename;
    std::error_code error;
    const bool regular_file = std::filesystem::is_regular_file(path, error);
    const auto size = regular_file ? std::filesystem::file_size(path, error) : 0;
    std::ifstream readable(path, std::ios::binary);
    if (error || !regular_file || size == 0 || !readable) {
      return fail_initialization(Status::Error(
          "JiebaTokenizer init failed: required dictionary file is missing, "
          "empty, or unreadable: " + path.string()));
    }
  }
  zvec::ailego::JsonObject config;
  config.set(zvec::ailego::JsonString("jieba_dict_dir"),
             zvec::ailego::JsonValue(init_dict_dir));
  note_initialization_attempt();
  const auto status = impl_->jieba.init(config);
  if (!status.ok()) return fail_initialization(Status::Error(status.message()));
  impl_->use_jieba = true;
  impl_->initialized = true;
  publish_initialized();
  return Status::OK();
#endif
  } catch (const std::exception& error) {
    return fail_initialization(Status::Error(
        "M4 analyzer initialization threw: " + std::string(error.what())));
  } catch (...) {
    return fail_initialization(Status::Error(
        "M4 analyzer initialization threw an unknown exception"));
  }
}

std::vector<Token> Analyzer::Tokenize(const std::string& text) const {
  if (!impl_->initialized) return {};
  if (!impl_->use_jieba) return m4::Tokenize(text);
#ifdef M4_USE_ZVEC_NATIVE_TOKENIZER
  const auto native_tokens =
      impl_->lowercase.filter(impl_->jieba.tokenize(text));
  std::vector<Token> tokens;
  tokens.reserve(native_tokens.size());
  for (const auto& token : native_tokens) {
    tokens.push_back(Token{token.text, token.position, token.offset});
  }
  return tokens;
#else
  return {};
#endif
}

namespace {

struct FingerprintWork {
  uint64_t dictionary_bytes{0};
  uint64_t sha256_calls{0};
  uint64_t sha256_bytes{0};
};

Status BuildAnalyzerFingerprint(
    const Options& options, std::string* out, FingerprintWork* work = nullptr,
    std::vector<AnalyzerDictionaryFileSnapshot>* dictionary_snapshot = nullptr) {
  (void)dictionary_snapshot;
  if (!out) return Status::Error("null analyzer fingerprint output");
  BufWriter identity;
  identity.String("m4-analyzer-fingerprint-v1");
  identity.String(options.fts_tokenizer_name);
  if (options.fts_tokenizer_name == "standard") {
    identity.String("builtin-standard-v1;filters=lowercase");
  } else if (options.fts_tokenizer_name == "jieba") {
    if (options.jieba_dict_dir.empty()) {
      return Status::Error("JiebaTokenizer: explicit jieba_dict_dir is required by M4");
    }
#ifndef M4_USE_ZVEC_NATIVE_TOKENIZER
    return Status::Error("JiebaTokenizer requires an M4_ZVEC_ROOT native-source build");
#else
    // These are exactly the files loaded by native Jieba's default search
    // mode. user_dict_path is unset, cut_mode is search, and lowercase is
    // applied after tokenization; all are part of this canonical config.
    identity.String("native-jieba-v1;cut_mode=search;user_dict_path=empty;filters=lowercase");
    const std::filesystem::path dict_dir(options.jieba_dict_dir);
    for (const char* filename : {"jieba.dict.utf8", "hmm_model.utf8"}) {
      const auto path = dict_dir / filename;
      std::error_code ec;
      if (!std::filesystem::is_regular_file(path, ec) || ec ||
          std::filesystem::file_size(path, ec) == 0 || ec) {
        return Status::Error("JiebaTokenizer identity requires a readable non-empty file: " + path.string());
      }
      std::ifstream in(path, std::ios::binary);
      if (!in) return Status::Error("JiebaTokenizer identity cannot read: " + path.string());
      const std::string contents((std::istreambuf_iterator<char>(in)),
                                 std::istreambuf_iterator<char>());
      if (in.bad()) return Status::Error("JiebaTokenizer identity read failed: " + path.string());
      identity.String(filename);
      if (work) {
        work->dictionary_bytes += contents.size();
        ++work->sha256_calls;
        work->sha256_bytes += contents.size();
      }
      const std::string file_fingerprint = Sha256(contents);
      if (dictionary_snapshot)
        dictionary_snapshot->push_back({filename, std::move(contents)});
      identity.String(file_fingerprint);
    }
#endif
  } else {
    return Status::Error("unsupported M4 FTS tokenizer: " + options.fts_tokenizer_name);
  }
  if (work) {
    ++work->sha256_calls;
    work->sha256_bytes += identity.data().size();
  }
  *out = Sha256(identity.data());
  return Status::OK();
}


namespace fs = std::filesystem;

double NowMs() {
  struct timespec ts {};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

double ThreadCpuMs() {
  struct timespec ts {};
  clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

class ScopedStageTimer {
 public:
  ScopedStageTimer(double* wall_ms, double* cpu_ms, bool start_immediately = true)
      : wall_ms_(wall_ms), cpu_ms_(cpu_ms) {
    if (start_immediately) Start();
  }
  ~ScopedStageTimer() { Stop(); }
  ScopedStageTimer(const ScopedStageTimer&) = delete;
  ScopedStageTimer& operator=(const ScopedStageTimer&) = delete;

  void Start() {
    if (active_ || (!wall_ms_ && !cpu_ms_)) return;
    wall_start_ = NowMs();
    cpu_start_ = ThreadCpuMs();
    active_ = true;
  }
  void Stop() {
    if (!active_) return;
    if (wall_ms_) *wall_ms_ += NowMs() - wall_start_;
    if (cpu_ms_) *cpu_ms_ += ThreadCpuMs() - cpu_start_;
    active_ = false;
  }

 private:
  double* wall_ms_;
  double* cpu_ms_;
  double wall_start_{0.0};
  double cpu_start_{0.0};
  bool active_{false};
};

constexpr uint64_t kNoDoc = std::numeric_limits<uint64_t>::max();

// ------------------------------- Manifest --------------------------------

struct RunInfo {
  uint64_t id{0};
  uint64_t level{0};
  uint64_t doc_count{0};
  uint64_t dead_count{0};
  uint64_t file_size{0};
  std::string file;
};

struct Manifest {
  std::string analyzer_fingerprint;
  uint64_t next_doc_id{1};
  uint64_t wal_seq{0};
  uint64_t run_id_seq{0};
  uint64_t stats_epoch{1};
  uint64_t stats_log_end{0};
  std::string tombstone_file;
  std::vector<RunInfo> runs;
};

std::string ManifestPath(const std::string& dir) {
  return (fs::path(dir) / "manifest").string();
}

std::string RunFileName(uint64_t id) {
  return "run-" + std::to_string(id) + ".seg";
}

std::string StatsCkptName(uint64_t epoch) {
  return "stats-" + std::to_string(epoch) + ".ckpt";
}
std::string StatsLogName(uint64_t epoch) {
  return "stats-" + std::to_string(epoch) + ".log";
}

std::string EncodeManifest(const Manifest& m) {
  BufWriter body;
  body.U64(m.next_doc_id);
  body.U64(m.wal_seq);
  body.U64(m.run_id_seq);
  body.U64(m.stats_epoch);
  body.U64(m.stats_log_end);
  body.String(m.tombstone_file);
  body.Varint(m.runs.size());
  for (const auto& r : m.runs) {
    body.U64(r.id);
    body.U64(r.level);
    body.U64(r.doc_count);
    body.U64(r.dead_count);
    body.U64(r.file_size);
    body.String(r.file);
  }
  body.String(m.analyzer_fingerprint);
  BufWriter out;
  out.U32(0x4D344D46);  // M4MF
  out.U32(2);
  out.Bytes(body.data().data(), body.size());
  out.U64(Fnv1a(body.data().data(), body.size()));
  return out.data();
}

bool DecodeManifest(const std::string& bytes, Manifest* out) {
  if (bytes.size() < 16) return false;
  BufReader head(bytes.data(), bytes.size());
  if (head.U32() != 0x4D344D46) return false;
  const uint32_t version = head.U32();
  if (version != 1 && version != 2) return false;
  const size_t body_size = bytes.size() - 8 - head.offset();
  if (body_size > bytes.size()) return false;
  const char* body = bytes.data() + head.offset();
  uint64_t want = 0;
  std::memcpy(&want, bytes.data() + bytes.size() - 8, 8);
  if (Fnv1a(body, body_size) != want) return false;
  BufReader r(body, body_size);
  Manifest m;
  m.next_doc_id = r.U64();
  m.wal_seq = r.U64();
  m.run_id_seq = r.U64();
  m.stats_epoch = r.U64();
  m.stats_log_end = r.U64();
  m.tombstone_file = r.String();
  const uint64_t run_count = r.Varint();
  if (!r.ok() || run_count > 1000000) return false;
  for (uint64_t i = 0; i < run_count; ++i) {
    RunInfo info;
    info.id = r.U64();
    info.level = r.U64();
    info.doc_count = r.U64();
    info.dead_count = r.U64();
    info.file_size = r.U64();
    info.file = r.String();
    if (!r.ok()) return false;
    m.runs.push_back(std::move(info));
  }
  if (version >= 2) m.analyzer_fingerprint = r.String();
  if (!r.ok() || !r.eof()) return false;
  *out = std::move(m);
  return true;
}

// ------------------------------- Stats -----------------------------------

struct StatsEvent {
  enum class Kind : uint8_t { kDf = 0, kDocs = 1, kTokens = 2 };
  Kind kind{Kind::kDf};
  std::string term;
  int64_t delta{0};
};

struct StatsState {
  uint64_t live_docs{0};
  uint64_t live_tokens{0};
  std::unordered_map<std::string, uint64_t> df;
};

void EncodeStatsEvent(BufWriter* w, const StatsEvent& e) {
  w->U8(static_cast<uint8_t>(e.kind));
  if (e.kind == StatsEvent::Kind::kDf) {
    w->String(e.term);
    w->VarintSigned(e.delta);
  } else {
    w->VarintSigned(e.delta);
  }
}

bool DecodeStatsEvent(BufReader* r, StatsEvent* e) {
  e->kind = static_cast<StatsEvent::Kind>(r->U8());
  if (e->kind == StatsEvent::Kind::kDf) {
    e->term = r->String();
    e->delta = r->VarintSigned();
  } else {
    e->delta = r->VarintSigned();
  }
  return r->ok();
}

void ApplyStatsEvent(StatsState* s, const StatsEvent& e) {
  switch (e.kind) {
    case StatsEvent::Kind::kDocs:
      s->live_docs = static_cast<uint64_t>(
          static_cast<int64_t>(s->live_docs) + e.delta);
      break;
    case StatsEvent::Kind::kTokens:
      s->live_tokens = static_cast<uint64_t>(
          static_cast<int64_t>(s->live_tokens) + e.delta);
      break;
    case StatsEvent::Kind::kDf: {
      auto it = s->df.find(e.term);
      int64_t v = (it == s->df.end() ? 0 : static_cast<int64_t>(it->second)) +
                  e.delta;
      if (v <= 0) {
        if (it != s->df.end()) s->df.erase(it);
      } else {
        s->df[e.term] = static_cast<uint64_t>(v);
      }
      break;
    }
  }
}

std::string EncodeStatsCkpt(const StatsState& s) {
  BufWriter w;
  w.U32(0x4D345354);  // M4ST
  w.U32(1);
  w.U64(s.live_docs);
  w.U64(s.live_tokens);
  w.Varint(s.df.size());
  for (const auto& [term, df] : s.df) {
    w.String(term);
    w.Varint(df);
  }
  w.U64(Fnv1a(w.data().data() + 8, w.size() - 8));
  return w.data();
}

bool DecodeStatsCkpt(const std::string& bytes, StatsState* s) {
  if (bytes.size() < 16) return false;
  BufReader h(bytes.data(), bytes.size());
  if (h.U32() != 0x4D345354 || h.U32() != 1) return false;
  const size_t body_size = bytes.size() - 8 - h.offset();
  const char* body = bytes.data() + h.offset();
  uint64_t want = 0;
  std::memcpy(&want, bytes.data() + bytes.size() - 8, 8);
  if (Fnv1a(body, body_size) != want) return false;
  BufReader r(body, body_size);
  StatsState out;
  out.live_docs = r.U64();
  out.live_tokens = r.U64();
  const uint64_t n = r.Varint();
  if (!r.ok() || n > 100000000ull) return false;
  for (uint64_t i = 0; i < n; ++i) {
    std::string term = r.String();
    uint64_t df = r.Varint();
    if (!r.ok()) return false;
    out.df[term] = df;
  }
  if (!r.ok()) return false;
  *s = std::move(out);
  return true;
}

// -------------------------------- WAL ------------------------------------

struct WalRecord {
  uint64_t seq{0};
  std::string payload;
};

constexpr uint32_t kMaxWalCompressedPayloadBytes = 8u << 20;

class Wal {
public:
  ~Wal() {
    if (fd_ >= 0)
      ::close(fd_);
  }

  Status Open(const std::string &path, bool compression_lz4,
              uint32_t lz4_acceleration) {
    if (compression_lz4 &&
        (lz4_acceleration == 0 ||
         lz4_acceleration >
             static_cast<uint32_t>(std::numeric_limits<int>::max()))) {
      return Status::Error("invalid WAL LZ4 acceleration");
    }
    path_ = path;
    compression_lz4_ = compression_lz4;
    lz4_acceleration_ = lz4_acceleration;
    fd_ = ::open(path.c_str(), O_RDWR | O_CREAT | O_APPEND, 0644);
    if (fd_ < 0)
      return Status::Error("open wal: " + path);
    return Status::OK();
  }

  Status Append(uint64_t seq, const std::string &payload, bool sync,
                double *write_ms = nullptr, double *fsync_ms = nullptr) {
    if (fd_ < 0)
      return Status::OK();
    if (poisoned_)
      return Status::Error("wal requires reopen after prior I/O failure");
    if (payload.size() > std::numeric_limits<uint32_t>::max()) {
      return Status::Error("wal record exceeds format limit");
    }
    struct stat before{};
    if (::fstat(fd_, &before) != 0) {
      poisoned_ = true;
      return Status::Error("wal stat before append failed");
    }
    const uint32_t checksum = static_cast<uint32_t>(
        Fnv1a(payload.data(), payload.size()) & 0xffffffffu);
    std::string compressed;
    bool use_compressed = false;
    if (compression_lz4_ && payload.size() <= kMaxWalCompressedPayloadBytes) {
      lz4_attempts_.fetch_add(1, std::memory_order_relaxed);
      lz4_attempted_input_bytes_.fetch_add(payload.size(),
                                           std::memory_order_relaxed);
      const double compress_cpu_start = ThreadCpuMs();
      const int source_size = static_cast<int>(payload.size());
      const int bound = LZ4_compressBound(source_size);
      if (bound > 0) {
        compressed.resize(static_cast<size_t>(bound));
        const int compressed_size = LZ4_compress_fast(
            payload.data(), compressed.data(), source_size, bound,
            static_cast<int>(lz4_acceleration_));
        if (compressed_size > 0 &&
            static_cast<size_t>(compressed_size) < payload.size()) {
          compressed.resize(static_cast<size_t>(compressed_size));
          use_compressed = true;
          lz4_compressed_frames_.fetch_add(1, std::memory_order_relaxed);
          lz4_input_bytes_.fetch_add(payload.size(), std::memory_order_relaxed);
          lz4_output_bytes_.fetch_add(compressed.size(),
                                      std::memory_order_relaxed);
        } else {
          compressed.clear();
        }
      }
      const double compress_cpu_ms =
          std::max(0.0, ThreadCpuMs() - compress_cpu_start);
      lz4_compress_cpu_ns_.fetch_add(
          static_cast<uint64_t>(compress_cpu_ms * 1'000'000.0),
          std::memory_order_relaxed);
    }
    BufWriter w;
    if (use_compressed) {
      w.U32(0x4D34575A); // M4WZ: LZ4 payload, length and checksum below.
      w.U32(static_cast<uint32_t>(compressed.size()));
      w.U64(seq);
      w.U32(static_cast<uint32_t>(payload.size()));
      w.U32(checksum);
      w.Bytes(compressed.data(), compressed.size());
    } else {
      w.U32(0x4D34574C); // M4WL: legacy uncompressed record.
      w.U32(static_cast<uint32_t>(payload.size()));
      w.U64(seq);
      w.U32(checksum);
      w.Bytes(payload.data(), payload.size());
    }
    const std::string &rec = w.data();
    const double write_started = write_ms ? NowMs() : 0.0;
    size_t done = 0;
    while (done < rec.size()) {
      const ssize_t n = ::write(fd_, rec.data() + done, rec.size() - done);
      if (n < 0) {
        if (errno == EINTR)
          continue;
        const int saved_errno = errno;
        if (::ftruncate(fd_, before.st_size) != 0 ||
            (sync && ::fsync(fd_) != 0)) {
          poisoned_ = true;
        }
        return Status::Error("wal write failed: " +
                             std::string(std::strerror(saved_errno)));
      }
      if (n == 0) {
        if (::ftruncate(fd_, before.st_size) != 0 ||
            (sync && ::fsync(fd_) != 0)) {
          poisoned_ = true;
        }
        return Status::Error("wal write made no progress");
      }
      done += static_cast<size_t>(n);
    }
    wal_bytes_appended_ += rec.size();
    if (write_ms)
      *write_ms = NowMs() - write_started;
    if (sync) {
      const double fsync_started = fsync_ms ? NowMs() : 0.0;
      const int fsync_result = ::fsync(fd_);
      if (fsync_ms)
        *fsync_ms = NowMs() - fsync_started;
      if (fsync_result != 0) {
        poisoned_ = true;
        return Status::Error("wal fsync failed: " +
                             std::string(std::strerror(errno)));
      }
      ++fsync_count_;
    }
    return Status::OK();
  }

  Status Sync(double* fsync_ms = nullptr) {
    if (fd_ < 0) return Status::OK();
    if (poisoned_)
      return Status::Error("wal requires reopen after prior I/O failure");
    const double fsync_started = fsync_ms ? NowMs() : 0.0;
    const int result = ::fsync(fd_);
    if (fsync_ms) *fsync_ms = NowMs() - fsync_started;
    if (result != 0) {
      const int saved_errno = errno;
      poisoned_ = true;
      return Status::Error("wal fsync failed: " +
                           std::string(std::strerror(saved_errno)));
    }
    ++fsync_count_;
    return Status::OK();
  }

  Status Replay(std::vector<WalRecord> *out,
                OpenDiagnostics *diagnostics = nullptr) {
    if (fd_ < 0)
      return Status::OK();
    struct stat st{};
    if (::fstat(fd_, &st) != 0)
      return Status::Error("wal stat");
    if (st.st_size < 0)
      return Status::Error("negative wal size");
    std::string bytes(static_cast<size_t>(st.st_size), '\0');
    size_t done = 0;
    uint64_t decoded_payload_bytes = 0;
    uint64_t decoded_payload_capacity_bytes = 0;
    uint64_t vector_growths = 0;
    uint64_t peak_tracked_capacity_bytes = bytes.capacity();
    const char *fault = std::getenv("M4_FAULT");
    bool inject_eintr = fault && std::string(fault) == "wal_pread_eintr_once";
    bool inject_error = fault && std::string(fault) == "wal_pread_error";
    while (done < bytes.size()) {
      ssize_t n;
      if (inject_eintr) {
        inject_eintr = false;
        errno = EINTR;
        n = -1;
      } else if (inject_error) {
        inject_error = false;
        errno = EIO;
        n = -1;
      } else {
        n = ::pread(fd_, bytes.data() + done, bytes.size() - done,
                    static_cast<off_t>(done));
      }
      if (n < 0 && errno == EINTR)
        continue;
      if (n < 0) {
        return Status::Error("wal read failed: " +
                             std::string(std::strerror(errno)));
      }
      if (n == 0) {
        return Status::Error("unexpected end of wal while reading");
      }
      done += static_cast<size_t>(n);
    }
    size_t off = 0;
    while (off + 20 <= bytes.size()) {
      BufReader r(bytes.data() + off, bytes.size() - off);
      const uint32_t magic = r.U32();
      const uint32_t len = r.U32();
      const uint64_t seq = r.U64();
      uint32_t original_len = len;
      uint32_t crc = 0;
      size_t header_size = 20;
      if (magic == 0x4D34574C) { // Legacy M4WL.
        crc = r.U32();
      } else if (magic == 0x4D34575A) { // M4WZ: LZ4 payload.
        if (off + 24 > bytes.size())
          break;
        original_len = r.U32();
        crc = r.U32();
        header_size = 24;
      } else {
        break;
      }
      if (len > bytes.size() - off - header_size)
        break;
      const char *encoded = bytes.data() + off + header_size;
      WalRecord rec;
      rec.seq = seq;
      if (magic == 0x4D34575A) {
        if (len > static_cast<uint32_t>(std::numeric_limits<int>::max()) ||
            original_len > kMaxWalCompressedPayloadBytes) {
          break;
        }
        rec.payload.resize(original_len);
        const int decoded = LZ4_decompress_safe(encoded, rec.payload.data(),
                                                static_cast<int>(len),
                                                static_cast<int>(original_len));
        if (decoded < 0 || static_cast<uint32_t>(decoded) != original_len)
          break;
      } else {
        rec.payload.assign(encoded, len);
      }
      if ((Fnv1a(rec.payload.data(), rec.payload.size()) & 0xffffffffu) != crc)
        break;
      const size_t old_vector_capacity = diagnostics ? out->capacity() : 0;
      uint64_t fixed_live_bytes = 0;
      if (diagnostics) {
        decoded_payload_bytes += rec.payload.size();
        decoded_payload_capacity_bytes += rec.payload.capacity();
        fixed_live_bytes = static_cast<uint64_t>(bytes.capacity()) +
                           decoded_payload_capacity_bytes;
        peak_tracked_capacity_bytes = std::max(
            peak_tracked_capacity_bytes,
            fixed_live_bytes + old_vector_capacity * sizeof(WalRecord));
      }
      out->push_back(std::move(rec));
      if (diagnostics) {
        if (out->capacity() != old_vector_capacity) {
          ++vector_growths;
          // Conservative upper bound while a vector growth allocates the new
          // backing store before releasing the old one.
          peak_tracked_capacity_bytes = std::max(
              peak_tracked_capacity_bytes,
              fixed_live_bytes +
                  (old_vector_capacity + out->capacity()) * sizeof(WalRecord));
        }
        peak_tracked_capacity_bytes =
            std::max(peak_tracked_capacity_bytes,
                     fixed_live_bytes + out->capacity() * sizeof(WalRecord));
      }
      off += header_size + len;
    }
    if (diagnostics) {
      diagnostics->wal_replay_encoded_file_bytes = bytes.size();
      diagnostics->wal_replay_encoded_buffer_capacity_bytes = bytes.capacity();
      diagnostics->wal_replay_record_count = out->size();
      diagnostics->wal_replay_decoded_payload_bytes = decoded_payload_bytes;
      diagnostics->wal_replay_decoded_payload_capacity_bytes =
          decoded_payload_capacity_bytes;
      diagnostics->wal_replay_record_vector_capacity_bytes =
          out->capacity() * sizeof(WalRecord);
      diagnostics->wal_replay_peak_tracked_live_bytes =
          peak_tracked_capacity_bytes;
      diagnostics->wal_replay_record_vector_growths = vector_growths;
    }
    if (off != bytes.size()) {
      // Drop only the invalid/torn suffix, preserving every complete frame.
      if (::ftruncate(fd_, static_cast<off_t>(off)) != 0 || ::fsync(fd_) != 0) {
        poisoned_ = true;
        return Status::Error("cannot repair torn wal suffix");
      }
      ++fsync_count_;
    }
    return Status::OK();
  }

  Status Reset() {
    if (fd_ < 0)
      return Status::OK();
    if (::ftruncate(fd_, 0) != 0)
      return Status::Error("wal truncate");
    ::lseek(fd_, 0, SEEK_SET);
    return Status::OK();
  }

  uint64_t fsync_count() const { return fsync_count_; }
  uint64_t bytes_appended() const { return wal_bytes_appended_; }
  uint64_t lz4_attempts() const { return lz4_attempts_.load(std::memory_order_relaxed); }
  uint64_t lz4_attempted_input_bytes() const {
    return lz4_attempted_input_bytes_.load(std::memory_order_relaxed);
  }
  uint64_t lz4_compressed_frames() const {
    return lz4_compressed_frames_.load(std::memory_order_relaxed);
  }
  uint64_t lz4_input_bytes() const {
    return lz4_input_bytes_.load(std::memory_order_relaxed);
  }
  uint64_t lz4_output_bytes() const {
    return lz4_output_bytes_.load(std::memory_order_relaxed);
  }
  double lz4_compress_cpu_ms() const {
    return static_cast<double>(lz4_compress_cpu_ns_.load(
               std::memory_order_relaxed)) /
           1'000'000.0;
  }

  uint64_t size() const {
    if (fd_ < 0)
      return 0;
    struct stat st{};
    if (::fstat(fd_, &st) != 0) return 0;
    return static_cast<uint64_t>(st.st_size);
  }

 private:
  std::string path_;
  int fd_{-1};
  bool poisoned_{false};
  uint64_t fsync_count_{0};
  uint64_t wal_bytes_appended_{0};
  bool compression_lz4_{false};
  uint32_t lz4_acceleration_{1};
  std::atomic<uint64_t> lz4_attempts_{0};
  std::atomic<uint64_t> lz4_attempted_input_bytes_{0};
  std::atomic<uint64_t> lz4_compressed_frames_{0};
  std::atomic<uint64_t> lz4_input_bytes_{0};
  std::atomic<uint64_t> lz4_output_bytes_{0};
  std::atomic<uint64_t> lz4_compress_cpu_ns_{0};
};

// ----------------------------- Manifest I/O ------------------------------

Status SyncDirectory(const std::string& path) {
  const size_t slash = path.find_last_of('/');
  const std::string dir = slash == std::string::npos ? "." : path.substr(0, slash);
  const int fd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY);
  if (fd < 0) return Status::Error("open directory for fsync " + dir + ": " + std::strerror(errno));
  const char* fault = std::getenv("M4_FAULT");
  int result = 0;
  if (fault && std::strcmp(fault, "manifest_directory_fsync") == 0) {
    errno = EIO;
    result = -1;
  } else {
    do {
      result = ::fsync(fd);
    } while (result != 0 && errno == EINTR);
  }
  const int saved_errno = errno;
  ::close(fd);
  if (result != 0) return Status::Error("fsync directory " + dir + ": " + std::strerror(saved_errno));
  return Status::OK();
}

Status AtomicWrite(const std::string& path, const std::string& data) {
  const std::string tmp = path + ".tmp." + std::to_string(::getpid());
  const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return Status::Error("open " + tmp + ": " + std::strerror(errno));
  size_t done = 0;
  Status s;
  while (done < data.size()) {
    const ssize_t n = ::write(fd, data.data() + done, data.size() - done);
    if (n < 0) {
      if (errno == EINTR) continue;
      s = Status::Error("write " + tmp);
      break;
    }
    done += static_cast<size_t>(n);
  }
  if (s.ok) {
    const char* fault = std::getenv("M4_FAULT");
    int sync_result = 0;
    if (fault && std::strcmp(fault, "metadata_file_fsync") == 0) {
      errno = EIO;
      sync_result = -1;
    } else {
      do {
        sync_result = ::fsync(fd);
      } while (sync_result != 0 && errno == EINTR);
    }
    if (sync_result != 0) {
      s = Status::Error("fsync " + tmp + ": " + std::strerror(errno));
    }
  }
  if (::close(fd) != 0 && s.ok) {
    s = Status::Error("close " + tmp + ": " + std::strerror(errno));
  }
  if (!s.ok) {
    ::unlink(tmp.c_str());
    return s;
  }
  if (::rename(tmp.c_str(), path.c_str()) != 0) {
    const std::string err = std::strerror(errno);
    ::unlink(tmp.c_str());
    return Status::Error("rename manifest: " + err);
  }
  return Status::OK();
}

Status WriteManifest(const std::string& dir, const Manifest& m) {
  const std::string path = ManifestPath(dir);
  if (fs::exists(path)) {
    std::error_code ec;
    fs::copy_file(path, path + ".bak", fs::copy_options::overwrite_existing, ec);
  }
  Status s = AtomicWrite(path, EncodeManifest(m));
  if (s.ok) s = SyncDirectory(path);
  if (s.ok) {
    // Make .bak a duplicate of the committed manifest. A later compaction may
    // delete the old run files, so .bak must never point at a superseded set.
    std::error_code ec;
    fs::copy_file(path, path + ".bak", fs::copy_options::overwrite_existing, ec);
  }
  return s;
}

bool ReadManifestFile(const std::string& path, Manifest* out) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  std::string bytes((std::istreambuf_iterator<char>(in)),
                    std::istreambuf_iterator<char>());
  return DecodeManifest(bytes, out);
}

// ----------------------------- Tombstones --------------------------------

std::string EncodeSortedTombstones(std::vector<DocId> ids) {
  std::sort(ids.begin(), ids.end());
  BufWriter w;
  w.U32(0x4D345442);  // M4TB
  w.U32(1);
  w.Varint(ids.size());
  uint64_t prev = 0;
  for (DocId id : ids) {
    w.Varint(id - prev);
    prev = id;
  }
  w.U64(Fnv1a(w.data().data() + 8, w.size() - 8));
  return w.data();
}

std::string EncodeTombstones(const std::unordered_set<DocId>& set) {
  return EncodeSortedTombstones(std::vector<DocId>(set.begin(), set.end()));
}

std::string EncodeTombstonesOutsideRange(
    const std::unordered_set<DocId>& set, DocId first, DocId last) {
  size_t retained_count = 0;
  for (DocId id : set) {
    if (id < first || id > last) ++retained_count;
  }
  std::vector<DocId> ids;
  ids.reserve(retained_count);
  for (DocId id : set) {
    if (id < first || id > last) ids.push_back(id);
  }
  return EncodeSortedTombstones(std::move(ids));
}

std::string EncodeTombstonesExcluding(
    const std::unordered_set<DocId>& set,
    const std::unordered_set<DocId>& excluded) {
  size_t retained_count = 0;
  for (DocId id : set) {
    if (excluded.count(id) == 0) ++retained_count;
  }
  std::vector<DocId> ids;
  ids.reserve(retained_count);
  for (DocId id : set) {
    if (excluded.count(id) == 0) ids.push_back(id);
  }
  return EncodeSortedTombstones(std::move(ids));
}

bool DecodeTombstones(const std::string& bytes,
                      std::unordered_set<DocId>* out) {
  if (bytes.size() < 16) return false;
  BufReader h(bytes.data(), bytes.size());
  if (h.U32() != 0x4D345442 || h.U32() != 1) return false;
  const size_t body_size = bytes.size() - 8 - h.offset();
  const char* body = bytes.data() + h.offset();
  uint64_t want = 0;
  std::memcpy(&want, bytes.data() + bytes.size() - 8, 8);
  if (Fnv1a(body, body_size) != want) return false;
  BufReader r(body, body_size);
  const uint64_t n = r.Varint();
  if (!r.ok() || n > 100000000ull) return false;
  uint64_t prev = 0;
  for (uint64_t i = 0; i < n; ++i) {
    prev += r.Varint();
    out->insert(prev);
  }
  return r.ok();
}

Status CollectDeletedIdsFromWalPayload(
    const std::string& payload, std::unordered_set<DocId>* deleted) {
  BufReader r(payload.data(), payload.size());
  const uint8_t type = r.U8();
  if (type == 3) {
    const uint64_t count = r.Varint();
    if (!r.ok() || count == 0 || count > 256) {
      return Status::Error("bad batch wal record count while reading pending deletes");
    }
    std::vector<std::string> children;
    children.reserve(static_cast<size_t>(count));
    for (uint64_t i = 0; i < count; ++i) children.push_back(r.String());
    if (!r.ok() || r.remaining() != 0) {
      return Status::Error("bad batch wal framing while reading pending deletes");
    }
    for (const auto& child : children) {
      Status s = CollectDeletedIdsFromWalPayload(child, deleted);
      if (!s.ok) return s;
    }
    return Status::OK();
  }
  if (type == 1) {
    const DocId old_id = r.U64();
    const DocId new_id = r.U64();
    (void)r.String();
    (void)r.String();
    const uint64_t value_count = r.Varint();
    if (!r.ok() || new_id == 0 || value_count > 1000000ull) {
      return Status::Error("bad upsert wal record while reading pending deletes");
    }
    for (uint64_t i = 0; i < value_count; ++i) {
      (void)r.String();
      (void)r.String();
    }
    if (!r.ok() || r.remaining() != 0) {
      return Status::Error("bad upsert wal framing while reading pending deletes");
    }
    if (old_id != 0) deleted->insert(old_id);
    return Status::OK();
  }
  if (type == 2) {
    const DocId id = r.U64();
    (void)r.String();
    if (!r.ok() || id == 0 || r.remaining() != 0) {
      return Status::Error("bad delete wal record while reading pending deletes");
    }
    deleted->insert(id);
    return Status::OK();
  }
  return Status::Error("unknown wal record while reading pending deletes");
}

// --------------------------- Posting cursors -----------------------------

class PostingCursor {
 public:
  virtual ~PostingCursor() = default;
  virtual bool valid() const = 0;
  virtual DocId doc() const = 0;
  virtual uint32_t tf() const = 0;
  virtual uint32_t dl() const = 0;
  virtual const std::vector<uint32_t>& positions() const = 0;
  virtual float max_score() const = 0;
  virtual bool Next() = 0;
  virtual bool Advance(DocId target) = 0;
};

class SegmentCursor : public PostingCursor {
 public:
  SegmentCursor(const SegmentPtr& seg, const TermRef* term, bool decode_positions,
                double idf, double k1, double b, double avg_dl,
                QueryDiagnostics* diagnostics = nullptr)
      : it_(seg, term, decode_positions),
        idf_(idf),
        k1_(k1),
        b_(b),
        avg_dl_(avg_dl),
        diagnostics_(diagnostics) {
    if (diagnostics_ && it_.valid()) ++diagnostics_->postings_scanned;
  }

  bool valid() const override { return it_.valid(); }
  DocId doc() const override { return it_.doc(); }
  uint32_t tf() const override { return it_.tf(); }
  uint32_t dl() const override { return it_.dl(); }
  const std::vector<uint32_t>& positions() const override {
    return it_.positions();
  }
  float max_score() const override {
    return it_.BlockMaxScore(idf_, k1_, b_, avg_dl_);
  }
  bool Next() override {
    if (diagnostics_) ++diagnostics_->cursor_next_calls;
    const bool ok = it_.Next();
    if (ok && diagnostics_) ++diagnostics_->postings_scanned;
    return ok;
  }
  bool Advance(DocId target) override {
    if (diagnostics_) ++diagnostics_->cursor_advance_calls;
    const DocId before = it_.doc();
    const bool ok = it_.Advance(target);
    if (ok && diagnostics_ && it_.doc() != before) {
      ++diagnostics_->postings_scanned;
    }
    return ok;
  }

 private:
  SegmentTermIterator it_;
  double idf_;
  double k1_;
  double b_;
  double avg_dl_;
  QueryDiagnostics* diagnostics_{nullptr};
};

class DeltaCursor : public PostingCursor {
 public:
  DeltaCursor(const std::vector<BuildPosting>* postings, double idf, double k1,
              double b, double avg_dl,
              QueryDiagnostics* diagnostics = nullptr)
      : postings_(postings),
        idf_(idf),
        k1_(k1),
        b_(b),
        avg_dl_(avg_dl),
        diagnostics_(diagnostics) {
    // A bounded delta is small; one exact bound over its postings is a valid
    // (if conservative) WAND bound for the whole cursor.
    max_score_ = 0.0f;
    if (postings_) {
      for (const auto& p : *postings_) {
        const double denom = p.tf + k1_ * (1.0 - b_ + b_ * p.dl / avg_dl_);
        const float s = denom <= 0.0
                            ? 0.0f
                            : static_cast<float>(idf_ * p.tf * (k1_ + 1.0) / denom);
        max_score_ = std::max(max_score_, s);
      }
    }
    idx_ = 0;
    if (diagnostics_ && valid()) ++diagnostics_->postings_scanned;
  }

  bool valid() const override {
    return postings_ && idx_ < postings_->size();
  }
  DocId doc() const override { return (*postings_)[idx_].doc; }
  uint32_t tf() const override { return (*postings_)[idx_].tf; }
  uint32_t dl() const override { return (*postings_)[idx_].dl; }
  const std::vector<uint32_t>& positions() const override {
    return (*postings_)[idx_].positions;
  }
  float max_score() const override { return max_score_; }
  bool Next() override {
    if (diagnostics_) ++diagnostics_->cursor_next_calls;
    if (!valid()) return false;
    ++idx_;
    if (valid() && diagnostics_) ++diagnostics_->postings_scanned;
    return valid();
  }
  bool Advance(DocId target) override {
    if (diagnostics_) ++diagnostics_->cursor_advance_calls;
    if (!valid()) return false;
    if (target <= doc()) return true;
    auto it = std::lower_bound(
        postings_->begin() + static_cast<std::ptrdiff_t>(idx_),
        postings_->end(), target,
        [](const BuildPosting& p, DocId t) { return p.doc < t; });
    const size_t before = idx_;
    idx_ = static_cast<size_t>(it - postings_->begin());
    if (valid() && diagnostics_ && idx_ != before) {
      ++diagnostics_->postings_scanned;
    }
    return valid();
  }

 private:
  const std::vector<BuildPosting>* postings_;
  size_t idx_{0};
  double idf_;
  double k1_;
  double b_;
  double avg_dl_;
  float max_score_{0.0f};
  QueryDiagnostics* diagnostics_{nullptr};
};

// Union of one term's postings across all runs and the active delta.
class TermUnionCursor {
 public:
  TermUnionCursor(std::string term,
                  std::vector<std::unique_ptr<PostingCursor>> cursors,
                  double idf, double k1, double b, double avg_dl,
                  QueryDiagnostics* diagnostics)
      : term_(std::move(term)),
        cursors_(std::move(cursors)),
        idf_(idf),
        k1_(k1),
        b_(b),
        avg_dl_(avg_dl),
        diagnostics_(diagnostics) {
    Refresh();
  }

  bool valid() const { return valid_; }
  DocId doc() const { return doc_; }
  float score() const { return score_; }
  float max_score() const { return max_score_; }
  const std::vector<uint32_t>& positions() const { return positions_; }
  const std::string& term() const { return term_; }

  bool Next() {
    if (!valid_) return false;
    const DocId cur = doc_;
    for (auto& c : cursors_) {
      if (c->valid() && c->doc() == cur) c->Next();
    }
    Refresh();
    return valid_;
  }

  bool Advance(DocId target) {
    if (!valid_) return false;
    if (target <= doc_) return true;
    for (auto& c : cursors_) {
      if (c->valid() && c->doc() < target) c->Advance(target);
    }
    Refresh();
    return valid_;
  }

 private:
  void Refresh() {
    valid_ = false;
    doc_ = kNoDoc;
    score_ = 0.0f;
    max_score_ = 0.0f;
    positions_.clear();
    for (auto& c : cursors_) {
      if (!c->valid()) continue;
      max_score_ = std::max(max_score_, c->max_score());
      if (c->doc() < doc_) {
        doc_ = c->doc();
        score_ = Bm25(c->tf(), c->dl());
        positions_ = c->positions();
      } else if (c->doc() == doc_) {
        score_ += Bm25(c->tf(), c->dl());
        positions_.insert(positions_.end(), c->positions().begin(),
                          c->positions().end());
      }
    }
    if (doc_ != kNoDoc) valid_ = true;
  }

  float Bm25(uint32_t tf, uint32_t dl) const {
    if (diagnostics_) ++diagnostics_->score_evaluations;
    if (avg_dl_ <= 0.0 || tf == 0) return 0.0f;
    const double denom = tf + k1_ * (1.0 - b_ + b_ * dl / avg_dl_);
    if (denom <= 0.0) return 0.0f;
    return static_cast<float>(idf_ * tf * (k1_ + 1.0) / denom);
  }

  std::string term_;
  std::vector<std::unique_ptr<PostingCursor>> cursors_;
  double idf_;
  double k1_;
  double b_;
  double avg_dl_;
  bool valid_{false};
  DocId doc_{kNoDoc};
  float score_{0.0f};
  float max_score_{0.0f};
  QueryDiagnostics* diagnostics_{nullptr};
  std::vector<uint32_t> positions_;
};

// -------------------------------- Query ----------------------------------

struct ParsedQuery {
  std::vector<std::string> terms;  // unique, query order
  std::vector<std::vector<std::string>> phrases;
  bool and_mode{false};
};

ParsedQuery ParseQuery(const std::string& query, const Analyzer& analyzer) {
  ParsedQuery q;
  std::vector<std::string> words;
  std::string cur;
  bool in_quote = false;
  for (char ch : query) {
    if (ch == '"') {
      if (!cur.empty()) {
        words.push_back(cur);
        cur.clear();
      }
      words.push_back("\"");
      in_quote = !in_quote;
      continue;
    }
    if (std::isspace(static_cast<unsigned char>(ch))) {
      if (!cur.empty()) {
        words.push_back(cur);
        cur.clear();
      }
      continue;
    }
    cur.push_back(ch);
  }
  (void)in_quote;
  if (!cur.empty()) words.push_back(cur);

  bool quote_open = false;
  std::string phrase_text;
  for (const auto& w : words) {
    if (w == "\"") {
      if (quote_open) {
        std::vector<std::string> phrase;
        for (const auto& tok : analyzer.Tokenize(phrase_text)) {
          phrase.push_back(tok.term);
        }
        if (!phrase.empty()) {
          q.phrases.push_back(phrase);
          for (const auto& term : phrase) q.terms.push_back(term);
          q.and_mode = true;
        }
        phrase_text.clear();
      }
      quote_open = !quote_open;
      continue;
    }
    if (quote_open) {
      if (!phrase_text.empty()) phrase_text.push_back(' ');
      phrase_text += w;
      continue;
    }
    if (w == "AND" || w == "and") {
      q.and_mode = true;
      continue;
    }
    if (w == "OR" || w == "or") {
      q.and_mode = false;
      continue;
    }
    for (const auto& tok : analyzer.Tokenize(w)) q.terms.push_back(tok.term);
  }
  // Preserve first occurrence, drop duplicates.
  std::vector<std::string> uniq;
  std::unordered_set<std::string> seen;
  for (auto& t : q.terms) {
    if (seen.insert(t).second) uniq.push_back(std::move(t));
  }
  q.terms = std::move(uniq);
  return q;
}

bool PhraseMatches(
    const std::vector<const std::vector<uint32_t>*>& positions,
    std::vector<size_t>* scan_indices) {
  if (positions.empty() || !positions[0] || !scan_indices ||
      scan_indices->size() < positions.size()) {
    return false;
  }
  for (size_t i = 1; i < positions.size(); ++i) {
    if (!positions[i] || positions[i]->empty()) return false;
  }
  std::fill(scan_indices->begin(),
            scan_indices->begin() + static_cast<std::ptrdiff_t>(positions.size()),
            0);
  // Position lists are sorted by the analyzer and delta-coded on disk. Scan
  // each list monotonically instead of copying it into per-candidate sets.
  for (uint32_t start : *positions[0]) {
    bool ok = true;
    for (size_t i = 1; i < positions.size(); ++i) {
      const auto& term_positions = *positions[i];
      const uint32_t target = start + static_cast<uint32_t>(i);
      size_t& cursor = (*scan_indices)[i];
      while (cursor < term_positions.size() &&
             term_positions[cursor] < target) {
        ++cursor;
      }
      if (cursor == term_positions.size() ||
          term_positions[cursor] != target) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

constexpr size_t kMaxArrayUint32Operands = 32;
constexpr char kArrayUint32Presence[] = "\0a32p";

std::string EncodeArrayUint32Element(uint32_t value) {
  std::string encoded("\0a32", 4);
  encoded.push_back(static_cast<char>(value / 16777216u));
  encoded.push_back(static_cast<char>((value / 65536u) % 256u));
  encoded.push_back(static_cast<char>((value / 256u) % 256u));
  encoded.push_back(static_cast<char>(value % 256u));
  return encoded;
}

Document NormalizeDocument(const Document& document) {
  Document normalized = document;
  std::map<std::string, std::set<uint32_t>> arrays;
  for (const auto& array : document.array_uint32_values) {
    arrays[array.field].insert(array.values.begin(), array.values.end());
  }
  normalized.array_uint32_values.clear();
  for (const auto& [field, values] : arrays) {
    normalized.values.push_back(
        ScalarValue{field, std::string(kArrayUint32Presence, 5)});
    for (uint32_t value : values) {
      normalized.values.push_back(
          ScalarValue{field, EncodeArrayUint32Element(value)});
    }
  }
  return normalized;
}

}  // namespace

FilterExpr FilterExpr::All() { return {}; }
FilterExpr FilterExpr::None() {
  FilterExpr expr;
  expr.op = FilterKind::NONE;
  return expr;
}
FilterExpr FilterExpr::StringEq(std::string field, std::string value) {
  FilterExpr expr;
  expr.op = FilterKind::STRING_EQ;
  expr.field = std::move(field);
  expr.string_value = std::move(value);
  return expr;
}
FilterExpr FilterExpr::StringPrefix(std::string field, std::string value) {
  FilterExpr expr;
  expr.op = FilterKind::STRING_PREFIX;
  expr.field = std::move(field);
  expr.string_value = std::move(value);
  return expr;
}
FilterExpr FilterExpr::StringSuffix(std::string field, std::string value) {
  FilterExpr expr;
  expr.op = FilterKind::STRING_SUFFIX;
  expr.field = std::move(field);
  expr.string_value = std::move(value);
  return expr;
}
FilterExpr FilterExpr::Uint32Eq(std::string field, uint32_t value) {
  FilterExpr expr;
  expr.op = FilterKind::UINT32_EQ;
  expr.field = std::move(field);
  expr.uint32_value = value;
  return expr;
}
FilterExpr FilterExpr::Uint32In(std::string field,
                                std::vector<uint32_t> values) {
  FilterExpr expr;
  expr.op = FilterKind::UINT32_IN;
  expr.field = std::move(field);
  expr.uint32_values = std::move(values);
  return expr;
}
FilterExpr FilterExpr::ArrayContainsAny(std::string field,
                                        std::vector<uint32_t> values) {
  FilterExpr expr;
  expr.op = FilterKind::ARRAY_UINT32_CONTAINS_ANY;
  expr.field = std::move(field);
  expr.uint32_values = std::move(values);
  return expr;
}
FilterExpr FilterExpr::ArrayNotContainsAny(
    std::string field, std::vector<uint32_t> values) {
  FilterExpr expr;
  expr.op = FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY;
  expr.field = std::move(field);
  expr.uint32_values = std::move(values);
  return expr;
}
FilterExpr FilterExpr::And(std::vector<FilterExpr> children) {
  FilterExpr expr;
  expr.op = FilterKind::AND;
  expr.children = std::move(children);
  return expr;
}
FilterExpr FilterExpr::Or(std::vector<FilterExpr> children) {
  FilterExpr expr;
  expr.op = FilterKind::OR;
  expr.children = std::move(children);
  return expr;
}
FilterExpr FilterExpr::Not(FilterExpr child) {
  FilterExpr expr;
  expr.op = FilterKind::NOT;
  expr.children.push_back(std::move(child));
  return expr;
}

ScalarValue ScalarValue::Unsigned32(std::string field, uint32_t value) {
  std::string encoded("\0u32", 4);
  encoded.push_back(static_cast<char>(value / 16777216u));
  encoded.push_back(static_cast<char>((value / 65536u) % 256u));
  encoded.push_back(static_cast<char>((value / 256u) % 256u));
  encoded.push_back(static_cast<char>(value % 256u));
  return ScalarValue{std::move(field), std::move(encoded)};
}

// ================================ Impl ====================================

class Index::Impl {
 public:
  Impl(std::string dir, Options options)
      : dir_(std::move(dir)), options_(std::move(options)) {}

  ~Impl() {
    StopWriterQueue();
    // Deliberately do not flush: crash/restart semantics are tested by simply
    // destroying the object with uncommitted WAL records.
  }

  void StartWriterQueue() {
    if (options_.writer_group_commit_max_batch_size < 2) return;
    writer_queue_thread_ = std::thread(&Impl::WriterQueueLoop, this);
  }

  struct QueuedUpsert {
    // QueueUpsert waits synchronously for completion, so these caller-owned
    // inputs remain alive without an unbounded extra copy before admission.
    const std::string* pk{nullptr};
    const Document* document{nullptr};
    size_t estimated_bytes{0};
    std::chrono::steady_clock::time_point enqueued_at;
    std::chrono::steady_clock::time_point dequeued_at;
    size_t group_size{0};
    std::promise<Status> completion;
  };

  static constexpr size_t kWriterGroupCommitMaxBytes = 6u << 20;
  static constexpr size_t kWriterGroupCommitMaxQueuedDocBytes = 256u << 10;

  static size_t EstimateQueuedMutationBytes(const std::string& pk,
                                             const Document& doc) {
    size_t total = 128;
    auto add = [&total](size_t bytes) {
      if (bytes > kWriterGroupCommitMaxQueuedDocBytes ||
          total > kWriterGroupCommitMaxQueuedDocBytes - bytes) {
        total = kWriterGroupCommitMaxQueuedDocBytes + 1;
      } else {
        total += bytes;
      }
    };
    add(pk.size());
    add(doc.text.size());
    for (const auto& value : doc.values) {
      add(value.field.size());
      add(value.value.size());
      add(128);  // conservative framing/varint allowance per value
      if (total > kWriterGroupCommitMaxQueuedDocBytes) break;
    }
    return total;
  }

  Status WriterGroupHealth() const {
    if (!writer_group_reopen_required_) return Status::OK();
    return Status::Error(
        "writer group has an uncertain durable/apply outcome; reopen required");
  }

  Status QueueUpsert(const std::string& pk, const Document& doc,
                     WriteDiagnostics* diagnostics) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    if (diagnostics) {
      *diagnostics = WriteDiagnostics{};
      diagnostics->capture_timing = capture;
    }
    const size_t estimate = EstimateQueuedMutationBytes(pk, doc);
    if (estimate > kWriterGroupCommitMaxQueuedDocBytes) {
      // Large records do not enter the bounded queue or the smaller atomic
      // ApplyBatch encoding limit; preserve the ordinary Upsert path for them.
      return UpsertDirect(pk, doc, diagnostics);
    }

    auto request = std::make_shared<QueuedUpsert>();
    request->pk = &pk;
    request->document = &doc;
    request->estimated_bytes = estimate;
    auto result = request->completion.get_future();
    const double wait_cpu_start = capture ? ThreadCpuMs() : 0.0;
    {
      std::unique_lock<std::mutex> lock(writer_queue_mutex_);
      const size_t capacity = std::min<size_t>(
          32, std::max<size_t>(4,
              options_.writer_group_commit_max_batch_size * 4ull));
      writer_queue_space_.wait(lock, [&] {
        return writer_queue_stopping_ || writer_queue_.size() < capacity;
      });
      if (writer_queue_stopping_) {
        return Status::Error("writer group-commit queue is stopping");
      }
      request->enqueued_at = std::chrono::steady_clock::now();
      writer_queue_.push_back(request);
    }
    writer_queue_cv_.notify_one();
    const Status status = result.get();
    if (capture) {
      diagnostics->writer_queue_wait_ms =
          std::chrono::duration<double, std::milli>(
              request->dequeued_at - request->enqueued_at).count();
      diagnostics->writer_queue_wait_cpu_ms =
          ThreadCpuMs() - wait_cpu_start;
      diagnostics->writer_group_commit_size = request->group_size;
    }
    return status;
  }

  std::vector<Status> ApplyUpsertRecordsGroup(
      const std::vector<Mutation>& mutations) {
    auto fail_all = [&](const Status& status) {
      return std::vector<Status>(mutations.size(), status);
    };
    if (mutations.empty()) return {};
    if (!options_.wal_enabled || !options_.fsync_wal) {
      std::unique_lock<std::recursive_mutex> state_lock(mutex_);
      const Status healthy = WriterGroupHealth();
      if (!healthy.ok) return fail_all(healthy);
      std::vector<Status> results(mutations.size(), Status::OK());
      for (size_t i = 0; i < mutations.size(); ++i) {
        results[i] = UpsertDirect(mutations[i].pk, mutations[i].document, nullptr);
          if (!results[i].ok) {
          writer_group_reopen_required_ = true;
          const Status stopped = Status::Error(
              "writer group stopped after an Upsert failure; reopen required: " +
              results[i].message);
          results[i] = stopped;
          for (size_t j = i + 1; j < mutations.size(); ++j) results[j] = stopped;
          break;
        }
      }
      return results;
    }
    if (mutations.size() > 256) {
      return fail_all(Status::Error("writer group exceeds 256 Upserts"));
    }

    std::vector<Document> normalized;
    normalized.reserve(mutations.size());
    for (const Mutation& mutation : mutations) {
      if (mutation.kind != MutationKind::UPSERT) {
        return fail_all(Status::Error(
            "per-Upsert writer group accepts only Upsert mutations"));
      }
      normalized.push_back(NormalizeDocument(mutation.document));
    }

    std::unique_lock<std::recursive_mutex> state_lock(mutex_);
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return fail_all(healthy);
    std::unordered_map<std::string, DocId> staged_ids;
    std::vector<std::string> records;
    records.reserve(mutations.size());
    uint64_t staged_next_id = next_doc_id_;
    size_t encoded_bytes = 0;
    auto current_id = [&](const std::string& pk) {
      const auto it = staged_ids.find(pk);
      return it == staged_ids.end() ? LookupPk(pk) : it->second;
    };
    for (size_t i = 0; i < mutations.size(); ++i) {
      if (staged_next_id == std::numeric_limits<uint64_t>::max()) {
        return fail_all(Status::Error("DocId space exhausted"));
      }
      const uint64_t old_id = current_id(mutations[i].pk);
      const uint64_t new_id = staged_next_id++;
      const Document& document = normalized[i];
      BufWriter record;
      record.U8(1);  // one legacy, independently replayable Upsert frame
      record.U64(old_id);
      record.U64(new_id);
      record.String(mutations[i].pk);
      record.String(document.text);
      record.Varint(document.values.size());
      for (const auto& value : document.values) {
        record.String(value.field);
        record.String(value.value);
      }
      encoded_bytes += record.data().size();
      if (encoded_bytes > kWriterGroupCommitMaxBytes) {
        return fail_all(Status::Error("writer group exceeds 6 MiB encoded limit"));
      }
      records.push_back(record.data());
      staged_ids[mutations[i].pk] = new_id;
    }

    const char* fault = std::getenv("M4_FAULT");
    if (fault && std::string(fault) == "writer_group_before_wal") {
      return fail_all(Status::Error("injected fault before writer-group WAL append"));
    }

    size_t appended = 0;
    std::vector<uint64_t> sequences;
    sequences.reserve(records.size());
    Status append_failure = Status::OK();
    for (const std::string& record : records) {
      if (fault && std::string(fault) == "writer_group_append_error_after_first" &&
          appended == 1) {
        append_failure = Status::Error("injected writer-group append failure");
        break;
      }
      if (next_wal_seq_ == std::numeric_limits<uint64_t>::max()) {
        append_failure = Status::Error("WAL sequence space exhausted");
        break;
      }
      const uint64_t seq = next_wal_seq_;
      Status status = options_.wal_enabled
                          ? wal_.Append(seq, record, false)
                          : Status::OK();
      if (!status.ok) {
        append_failure = status;
        break;
      }
      ++next_wal_seq_;
      sequences.push_back(seq);
      ++appended;
    }

    if (appended > 0 && options_.wal_enabled && options_.fsync_wal) {
      if (fault && std::string(fault) == "writer_group_before_wal_sync") {
        ::_exit(92);
      }
      if (fault && std::string(fault) == "writer_group_fsync_error") {
        writer_group_reopen_required_ = true;
        return fail_all(Status::Error(
            "writer-group fsync failed; durable outcome uncertain, reopen required: injected"));
      }
      const Status sync_status = wal_.Sync();
      if (!sync_status.ok) {
        writer_group_reopen_required_ = true;
        return fail_all(Status::Error(
            "writer-group fsync failed; durable outcome uncertain, reopen required: " +
            sync_status.message));
      }
      if (fault && std::string(fault) == "writer_group_after_wal_sync") {
        ::_exit(90);
      }
    }
    if (appended == 0) {
      writer_group_reopen_required_ = !append_failure.ok;
      if (!append_failure.ok) {
        return fail_all(Status::Error(
            "writer-group WAL append failed; reopen required: " +
            append_failure.message));
      }
      return fail_all(append_failure);
    }

    Status barrier_status = WaitForTestWalApplyBarrier();
    if (!barrier_status.ok) {
      writer_group_reopen_required_ = true;
      return fail_all(Status::Error(
          "writer group is durable but not applied; reopen required: " +
          barrier_status.message));
    }

    std::vector<Status> results(mutations.size(), Status::OK());
    if (!append_failure.ok) writer_group_reopen_required_ = true;
    for (size_t i = 0; i < appended; ++i) {
      // A flush during sequential application may checkpoint this prefix.
      // Never advertise later, durable-but-unapplied frames as included in
      // the manifest's durable WAL sequence.
      last_wal_seq_ = sequences[i];
      Status status;
      {
        std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
        status = ReplayWalPayload(records[i]);
      }
      if (!status.ok) {
        writer_group_reopen_required_ = true;
        const Status stopped = Status::Error(
            "writer-group record is durable but apply/flush failed; reopen required: " +
            status.message);
        for (size_t j = i; j < mutations.size(); ++j) results[j] = stopped;
        return results;
      }
      if (i == 0) {
        if (fault && std::string(fault) ==
                         "writer_group_apply_error_after_first") {
          writer_group_reopen_required_ = true;
          for (size_t j = 1; j < mutations.size(); ++j) {
            results[j] = Status::Error(
                "writer-group suffix is durable but not yet applied; reopen required: injected");
          }
          return results;
        }
        Status step_status = WaitForTestWriterGroupStepBarrier();
        if (!step_status.ok) {
          writer_group_reopen_required_ = true;
          const Status stopped = Status::Error(
              "writer group is durable but partially applied; reopen required: " +
              step_status.message);
          for (size_t j = 1; j < mutations.size(); ++j) results[j] = stopped;
          return results;
        }
        if (fault && std::string(fault) == "writer_group_after_first_apply") {
          ::_exit(91);
        }
      }
    }
    // Defer auto-flush until every appended record has been applied. A flush
    // resets the entire WAL, so doing it mid-group could discard later frames
    // that are already durable but not yet represented in the live snapshot.
    Status flush_status;
    {
      std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
      flush_status = MaybeAutoFlush(&query_guard);
    }
    if (!flush_status.ok) {
      writer_group_reopen_required_ = true;
      const Status stopped = Status::Error(
          "writer group is durable but auto-flush failed; reopen required: " +
          flush_status.message);
      return std::vector<Status>(mutations.size(), stopped);
    }
    if (!append_failure.ok) {
      const Status suffix_failure = Status::Error(
          "writer-group WAL append stopped after a durable prefix; reopen required: " +
          append_failure.message);
      for (size_t i = appended; i < mutations.size(); ++i) {
        results[i] = suffix_failure;
      }
    }
    return results;
  }

  void WriterQueueLoop() {
    for (;;) {
      std::vector<std::shared_ptr<QueuedUpsert>> group;
      {
        std::unique_lock<std::mutex> lock(writer_queue_mutex_);
        writer_queue_cv_.wait(lock, [&] {
          return writer_queue_stopping_ || !writer_queue_.empty();
        });
        if (writer_queue_.empty() && writer_queue_stopping_) return;

        const size_t max_batch = options_.writer_group_commit_max_batch_size;
        const auto deadline = writer_queue_.front()->enqueued_at +
            std::chrono::microseconds(
                options_.writer_group_commit_max_delay_us);
        if (!writer_queue_stopping_ && writer_queue_.size() < max_batch) {
          writer_queue_cv_.wait_until(lock, deadline, [&] {
            return writer_queue_stopping_ || writer_queue_.size() >= max_batch;
          });
        }

        size_t bytes = 0;
        while (!writer_queue_.empty() && group.size() < max_batch) {
          const size_t next = writer_queue_.front()->estimated_bytes;
          if (!group.empty() && next > kWriterGroupCommitMaxBytes - bytes) break;
          auto request = std::move(writer_queue_.front());
          writer_queue_.pop_front();
          request->dequeued_at = std::chrono::steady_clock::now();
          bytes += next;
          group.push_back(std::move(request));
        }
        for (const auto& request : group) request->group_size = group.size();
      }
      writer_queue_space_.notify_all();

      std::vector<Mutation> mutations;
      mutations.reserve(group.size());
      for (const auto& request : group) {
        mutations.push_back(Mutation{MutationKind::UPSERT, *request->pk,
                                     *request->document});
      }
      if (options_.writer_group_commit_per_upsert_records) {
        const std::vector<Status> statuses = ApplyUpsertRecordsGroup(mutations);
        for (size_t i = 0; i < group.size(); ++i) {
          group[i]->completion.set_value(i < statuses.size()
                                             ? statuses[i]
                                             : Status::Error("missing writer-group status"));
        }
      } else {
        const Status status = ApplyBatch(mutations);
        for (const auto& request : group) request->completion.set_value(status);
      }
    }
  }

  void StopWriterQueue() {
    if (!writer_queue_thread_.joinable()) return;
    {
      std::lock_guard<std::mutex> lock(writer_queue_mutex_);
      writer_queue_stopping_ = true;
    }
    writer_queue_cv_.notify_all();
    writer_queue_space_.notify_all();
    writer_queue_thread_.join();
  }

  Status Open(OpenDiagnostics* diagnostics) {
    using Clock = std::chrono::steady_clock;
    const auto open_started = Clock::now();
    const double open_cpu_started = ThreadCpuMs();
    auto record_stage = [](double* wall_target, double* cpu_target,
                           Clock::time_point wall_start, double cpu_start) {
      if (wall_target) *wall_target += std::chrono::duration<double, std::milli>(
          Clock::now() - wall_start).count();
      if (cpu_target) *cpu_target += ThreadCpuMs() - cpu_start;
    };
    auto build_fingerprint = [&](
      std::string* out,
      std::vector<AnalyzerDictionaryFileSnapshot>* dictionary_snapshot = nullptr) {
      FingerprintWork work;
      const Status status = BuildAnalyzerFingerprint(options_, out, &work,
                                                     dictionary_snapshot);
      if (diagnostics) {
        ++diagnostics->analyzer_fingerprint_calls;
        diagnostics->analyzer_fingerprint_sha256_calls += work.sha256_calls;
        diagnostics->analyzer_fingerprint_dictionary_bytes += work.dictionary_bytes;
        diagnostics->analyzer_fingerprint_sha256_bytes += work.sha256_bytes;
      }
      return status;
    };
    auto stage_started = Clock::now();
    double stage_cpu_started = ThreadCpuMs();
    std::string analyzer_fingerprint;
    std::vector<AnalyzerDictionaryFileSnapshot> dictionary_snapshot;
    const Status fingerprint_status =
        build_fingerprint(&analyzer_fingerprint, &dictionary_snapshot);
    record_stage(diagnostics ? &diagnostics->analyzer_fingerprint_ms : nullptr,
                 diagnostics ? &diagnostics->analyzer_fingerprint_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);
    if (!fingerprint_status.ok) return fingerprint_status;
    bool analyzer_cache_hit = false;
    AnalyzerInitDiagnostics analyzer_init_diagnostics;
    auto initialize_analyzer = [&]() {
      const Status status = analyzer_.Init(
          options_.fts_tokenizer_name, options_.jieba_dict_dir,
          options_.reuse_analyzer_cache ? analyzer_fingerprint : std::string(),
          &analyzer_cache_hit, &analyzer_init_diagnostics,
          &dictionary_snapshot, analyzer_fingerprint);
      if (diagnostics) {
        diagnostics->analyzer_cache_hit = analyzer_cache_hit;
        diagnostics->analyzer_cache_miss = analyzer_init_diagnostics.cache_miss;
        diagnostics->analyzer_cache_race_reuse = analyzer_init_diagnostics.late_race_reuse;
        diagnostics->analyzer_cache_waited = analyzer_init_diagnostics.cache_waited;
        diagnostics->analyzer_cache_wait_reuse = analyzer_init_diagnostics.cache_wait_reuse;
        diagnostics->analyzer_dictionary_snapshot_used =
            analyzer_init_diagnostics.dictionary_snapshot_used;
        diagnostics->analyzer_cache_expired_entry = analyzer_init_diagnostics.expired_weak_entry;
        diagnostics->analyzer_initialization_attempted = analyzer_init_diagnostics.initialization_attempted;
        diagnostics->analyzer_initialization_succeeded = analyzer_init_diagnostics.initialization_succeeded;
      }
      return status;
    };

    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    std::error_code ec;
    fs::create_directories(dir_, ec);
    if (ec) return Status::Error("create directory: " + ec.message());

    Manifest m;
    bool have_manifest = ReadManifestFile(ManifestPath(dir_), &m);
    if (!have_manifest) {
      have_manifest = ReadManifestFile(ManifestPath(dir_) + ".bak", &m);
    }
    if (have_manifest) {
      if (m.analyzer_fingerprint.empty()) {
        return Status::Error(
            "index manifest has no analyzer identity (legacy/corrupt index); "
            "rebuild into a new directory is required");
      }
      if (m.analyzer_fingerprint != analyzer_fingerprint) {
        return Status::Error(
            "analyzer identity mismatch; refusing to open or replay WAL "
            "with different tokenizer/filter/dictionary contents");
      }
      const auto analyzer_init_wall_start = Clock::now();
      const double analyzer_init_cpu_start = ThreadCpuMs();
      const Status analyzer_status = initialize_analyzer();
      record_stage(diagnostics ? &diagnostics->analyzer_init_ms : nullptr,
                   diagnostics ? &diagnostics->analyzer_init_cpu_ms : nullptr,
                   analyzer_init_wall_start, analyzer_init_cpu_start);
      if (!analyzer_status.ok) return analyzer_status;
      if (options_.skip_cached_analyzer_fingerprint_verification &&
          analyzer_init_diagnostics.initial_cache_hit) {
        if (diagnostics) diagnostics->fingerprint_verification_skipped = true;
      } else {
        std::string initialized_fingerprint;
        const auto verify_wall_start = Clock::now();
        const double verify_cpu_start = ThreadCpuMs();
        const Status verify_status = build_fingerprint(&initialized_fingerprint);
        record_stage(diagnostics ? &diagnostics->fingerprint_verification_ms : nullptr,
                     diagnostics ? &diagnostics->fingerprint_verification_cpu_ms : nullptr,
                     verify_wall_start, verify_cpu_start);
        if (!verify_status.ok) return verify_status;
        if (initialized_fingerprint != analyzer_fingerprint) {
          return Status::Error(
              "analyzer dictionary changed during initialization; refusing to "
              "open or replay WAL");
        }
      }
      manifest_ = m;
    } else {
      // A directory with data but no valid manifest is not silently
      // reinitialized: that would orphan potentially recoverable runs.
      bool has_data = false;
      std::error_code ec2;
      for (const auto& e : fs::directory_iterator(dir_, ec2)) {
        const std::string name = e.path().filename().string();
        if (name != "manifest.bak") {
          has_data = true;
          break;
        }
      }
      if (has_data) {
        return Status::Error("index has data but no valid manifest");
      }
      const auto analyzer_init_wall_start = Clock::now();
      const double analyzer_init_cpu_start = ThreadCpuMs();
      const Status analyzer_status = initialize_analyzer();
      record_stage(diagnostics ? &diagnostics->analyzer_init_ms : nullptr,
                   diagnostics ? &diagnostics->analyzer_init_cpu_ms : nullptr,
                   analyzer_init_wall_start, analyzer_init_cpu_start);
      if (!analyzer_status.ok) return analyzer_status;
      if (options_.skip_cached_analyzer_fingerprint_verification &&
          analyzer_init_diagnostics.initial_cache_hit) {
        if (diagnostics) diagnostics->fingerprint_verification_skipped = true;
      } else {
        std::string initialized_fingerprint;
        const auto verify_wall_start = Clock::now();
        const double verify_cpu_start = ThreadCpuMs();
        const Status verify_status = build_fingerprint(&initialized_fingerprint);
        record_stage(diagnostics ? &diagnostics->fingerprint_verification_ms : nullptr,
                     diagnostics ? &diagnostics->fingerprint_verification_cpu_ms : nullptr,
                     verify_wall_start, verify_cpu_start);
        if (!verify_status.ok) return verify_status;
        if (initialized_fingerprint != analyzer_fingerprint) {
          return Status::Error(
              "analyzer dictionary changed during initialization; refusing to "
              "create index");
        }
      }
      manifest_ = Manifest{};
      manifest_.analyzer_fingerprint = analyzer_fingerprint;
      // Ensure an initial checkpoint/log exists.
      StatsState empty;
      const auto ckpt = fs::path(dir_) / StatsCkptName(1);
      const auto log = fs::path(dir_) / StatsLogName(1);
      Status initial_status = AtomicWrite(ckpt.string(), EncodeStatsCkpt(empty));
      if (!initial_status.ok) return initial_status;
      initial_status = AtomicWrite(log.string(), "");
      if (!initial_status.ok) return initial_status;
      const Status manifest_status = WriteManifest(dir_, manifest_);
      if (!manifest_status.ok) return manifest_status;
    }

    // Remember the durable checkpoint separately from manifest_.wal_seq, which
    // WAL replay advances in memory without committing a new manifest.
    durable_wal_seq_ = manifest_.wal_seq;

    // Analyzer identity is validated and the matching tokenizer initialized
    // before loading runs or replaying WAL.
    record_stage(diagnostics ? &diagnostics->manifest_and_analyzer_ms : nullptr,
                 diagnostics ? &diagnostics->manifest_and_analyzer_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);
    if (diagnostics) diagnostics->analyzer_cache_hit = analyzer_cache_hit;

    // Load runs.
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    for (const auto& info : manifest_.runs) {
      Run run;
      run.info = info;
      std::string err;
      Status s =
          Segment::Open((fs::path(dir_) / info.file).string(), &run.segment, &err);
      if (!s.ok) {
        return Status::Error("open run " + info.file + ": " + err);
      }
      if (run.segment->run_id() != info.id ||
          run.segment->level() != info.level) {
        return Status::Error("run metadata mismatch: " + info.file);
      }
      if (diagnostics) {
        ++diagnostics->segment_count;
        diagnostics->segment_bytes += run.segment->file_size();
      }
      runs_.push_back(std::move(run));
    }
    std::sort(runs_.begin(), runs_.end(), [](const Run& a, const Run& b) {
      return a.segment->first_doc_id() < b.segment->first_doc_id();
    });
    record_stage(diagnostics ? &diagnostics->segment_open_ms : nullptr,
                 diagnostics ? &diagnostics->segment_open_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);

    // Tombstones.
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    if (!manifest_.tombstone_file.empty()) {
      std::ifstream in((fs::path(dir_) / manifest_.tombstone_file).string(),
                       std::ios::binary);
      if (in) {
        std::string bytes((std::istreambuf_iterator<char>(in)),
                          std::istreambuf_iterator<char>());
        if (!DecodeTombstones(bytes, &tombstones_)) {
          return Status::Error("invalid tombstone snapshot");
        }
      }
    }

    record_stage(diagnostics ? &diagnostics->tombstones_ms : nullptr,
                 diagnostics ? &diagnostics->tombstones_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);

    // Stats checkpoint + delta log.
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    StatsState stats;
    const auto ckpt_path =
        (fs::path(dir_) / StatsCkptName(manifest_.stats_epoch)).string();
    const auto log_path =
        (fs::path(dir_) / StatsLogName(manifest_.stats_epoch)).string();
    bool stats_ok = false;
    {
      std::ifstream in(ckpt_path, std::ios::binary);
      if (in) {
        std::string bytes((std::istreambuf_iterator<char>(in)),
                          std::istreambuf_iterator<char>());
        stats_ok = DecodeStatsCkpt(bytes, &stats);
      }
    }
    if (!stats_ok) {
      return Status::Error("missing stats checkpoint");
    }
    if (!ReplayStatsLog(log_path, manifest_.stats_log_end, &stats)) {
      return Status::Error("invalid stats log");
    }
    stats_ = std::move(stats);
    stats_log_end_ = manifest_.stats_log_end;
    stats_epoch_ = manifest_.stats_epoch;
    // Discard an uncommitted tail written before a crash (the manifest end is
    // authoritative). Appending after a stale tail would corrupt replay.
    {
      const int fd = ::open(log_path.c_str(), O_WRONLY);
      if (fd >= 0) {
        ::ftruncate(fd, static_cast<off_t>(stats_log_end_));
        ::close(fd);
      }
    }

    record_stage(diagnostics ? &diagnostics->stats_load_ms : nullptr,
                 diagnostics ? &diagnostics->stats_load_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);

    // WAL replay (records not covered by the manifest).
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    {
      Status s = wal_.Open((fs::path(dir_) / "wal.log").string(),
                           options_.wal_compression_lz4,
                           options_.wal_lz4_acceleration);
      if (!s.ok) return s;
      std::vector<WalRecord> records;
      s = wal_.Replay(&records, diagnostics);
      if (!s.ok) return s;
      for (const auto& rec : records) {
        if (rec.seq <= manifest_.wal_seq) continue;
        s = ReplayWalPayload(rec.payload);
        if (!s.ok) return s;
        manifest_.wal_seq = std::max(manifest_.wal_seq, rec.seq);
        next_wal_seq_ = std::max(next_wal_seq_, rec.seq + 1);
      }
    }
    next_wal_seq_ = std::max(next_wal_seq_, manifest_.wal_seq + 1);
    last_wal_seq_ = manifest_.wal_seq;
    // WAL replay may have appended DocIds beyond the last manifest commit.
    // Preserve that high-water mark so the next write never reuses a replayed
    // version's DocId.
    next_doc_id_ = std::max(next_doc_id_, manifest_.next_doc_id);

    record_stage(diagnostics ? &diagnostics->wal_replay_ms : nullptr,
                 diagnostics ? &diagnostics->wal_replay_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);

    // Build the live key map from persisted docs, then apply delta docs.
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    for (const auto& run : runs_) {
      for (const auto& doc : run.segment->docs()) {
        if (tombstones_.count(doc.id) == 0) pk_to_doc_[doc.pk] = doc.id;
      }
    }
    for (const auto& doc : delta_docs_) {
      if (tombstones_.count(doc.id) == 0) pk_to_doc_[doc.pk] = doc.id;
    }

    record_stage(diagnostics ? &diagnostics->live_key_map_ms : nullptr,
                 diagnostics ? &diagnostics->live_key_map_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);
    run_id_seq_ = manifest_.run_id_seq;
    stage_started = Clock::now();
    stage_cpu_started = ThreadCpuMs();
    GarbageCollect();
    record_stage(diagnostics ? &diagnostics->garbage_collect_ms : nullptr,
                 diagnostics ? &diagnostics->garbage_collect_cpu_ms : nullptr,
                 stage_started, stage_cpu_started);
    if (diagnostics) {
      diagnostics->total_ms = std::chrono::duration<double, std::milli>(
          Clock::now() - open_started).count();
      diagnostics->total_cpu_ms = ThreadCpuMs() - open_cpu_started;
    }
    return Status::OK();
  }

  Status Upsert(const std::string& pk, const Document& doc,
                WriteDiagnostics* diagnostics) {
    if (options_.writer_group_commit_max_batch_size >= 2) {
      return QueueUpsert(pk, doc, diagnostics);
    }
    return UpsertDirect(pk, doc, diagnostics);
  }

  Status UpsertDirect(const std::string& pk, const Document& doc,
                     WriteDiagnostics* diagnostics) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    double wall_start = capture ? NowMs() : 0.0;
    double cpu_start = capture ? ThreadCpuMs() : 0.0;
    // Array normalization is pure input preparation. Do it before acquiring
    // the writer-state mutex so competing writers do not queue behind it.
    const Document normalized = NormalizeDocument(doc);
    if (capture) {
      diagnostics->normalize_ms = NowMs() - wall_start;
      diagnostics->normalize_cpu_ms = ThreadCpuMs() - cpu_start;
      wall_start = NowMs();
      cpu_start = ThreadCpuMs();
    }
    std::unique_lock<std::recursive_mutex> lock(mutex_);
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return healthy;
    if (capture) {
      diagnostics->state_mutex_wait_ms = NowMs() - wall_start;
      diagnostics->state_mutex_wait_cpu_ms = ThreadCpuMs() - cpu_start;
    }
    ScopedStageTimer state_hold_timer(
        capture ? &diagnostics->state_mutex_hold_ms : nullptr,
        capture ? &diagnostics->state_mutex_hold_cpu_ms : nullptr);
    const uint64_t old_id = LookupPk(pk);
    // Keep the high-water mark stable for shared readers until InsertInternal
    // advances it under exclusive query admission; writers are state-serialized.
    const DocId new_id = next_doc_id_;

    // Persist the complete replacement before changing query-visible state.
    if (capture) {
      wall_start = NowMs();
      cpu_start = ThreadCpuMs();
    }
    BufWriter w;
    w.U8(1);  // upsert
    w.U64(old_id);
    w.U64(new_id);
    w.String(pk);
    w.String(normalized.text);
    w.Varint(normalized.values.size());
    for (const auto& v : normalized.values) {
      w.String(v.field);
      w.String(v.value);
    }
    if (capture) {
      diagnostics->wal_encode_ms = NowMs() - wall_start;
      diagnostics->wal_encode_cpu_ms = ThreadCpuMs() - cpu_start;
    }
    const uint64_t seq = next_wal_seq_++;
    last_wal_seq_ = seq;
    Status s;
    if (options_.wal_enabled) {
      if (capture) {
        wall_start = NowMs();
        cpu_start = ThreadCpuMs();
      }
      s = wal_.Append(seq, w.data(), options_.fsync_wal,
                      capture ? &diagnostics->wal_write_ms : nullptr,
                      capture ? &diagnostics->wal_fsync_ms : nullptr);
      if (capture) {
        diagnostics->wal_append_ms = NowMs() - wall_start;
        diagnostics->wal_append_cpu_ms = ThreadCpuMs() - cpu_start;
      }
      if (!s.ok) return s;
      const char* fault = std::getenv("M4_FAULT");
      if (fault && std::string(fault) == "upsert_after_wal_sync") ::_exit(88);
    }
    s = WaitForTestWalApplyBarrier();
    if (!s.ok) return s;

    if (capture) {
      wall_start = NowMs();
      cpu_start = ThreadCpuMs();
    }
    std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
    if (capture) {
      diagnostics->query_mutex_wait_ms = NowMs() - wall_start;
      diagnostics->query_mutex_wait_cpu_ms = ThreadCpuMs() - cpu_start;
      wall_start = NowMs();
      cpu_start = ThreadCpuMs();
    }
    if (old_id != 0) {
      s = DeleteInternal(old_id, true);
      if (!s.ok) {
        if (capture) {
          diagnostics->apply_ms = NowMs() - wall_start;
          diagnostics->apply_cpu_ms = ThreadCpuMs() - cpu_start;
        }
        return s;
      }
    }
    s = InsertInternal(new_id, pk, normalized);
    if (capture) {
      diagnostics->apply_ms = NowMs() - wall_start;
      diagnostics->apply_cpu_ms = ThreadCpuMs() - cpu_start;
    }
    if (!s.ok) return s;
    pending_flush_ = true;
    if (capture) {
      wall_start = NowMs();
      cpu_start = ThreadCpuMs();
    }
    s = MaybeAutoFlush(&query_guard);
    if (capture) {
      diagnostics->auto_flush_ms = NowMs() - wall_start;
      diagnostics->auto_flush_cpu_ms = ThreadCpuMs() - cpu_start;
    }
    return s;
  }

  Status Delete(const std::string& pk) {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return healthy;
    const uint64_t id = LookupPk(pk);
    if (id == 0) return Status::OK();
    BufWriter w;
    w.U8(2);  // delete
    w.U64(id);
    w.String(pk);
    const uint64_t seq = next_wal_seq_++;
    last_wal_seq_ = seq;
    Status s;
    if (options_.wal_enabled) {
      s = wal_.Append(seq, w.data(), options_.fsync_wal);
      if (!s.ok) return s;
      const char* fault = std::getenv("M4_FAULT");
      if (fault && std::string(fault) == "delete_after_wal_sync") ::_exit(89);
    }
    s = WaitForTestWalApplyBarrier();
    if (!s.ok) return s;

    std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
    s = DeleteInternal(id, true);
    if (!s.ok) return s;
    pending_flush_ = true;
    return MaybeAutoFlush(&query_guard);
  }

  Status ApplyBatch(const std::vector<Mutation>& mutations) {
    return ApplyBatch(mutations, nullptr);
  }

  Status ApplyBatch(const std::vector<Mutation>& mutations,
                    WriteDiagnostics* diagnostics) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    if (diagnostics) {
      const bool requested = diagnostics->capture_timing;
      *diagnostics = WriteDiagnostics{};
      diagnostics->capture_timing = requested;
    }
    constexpr size_t kMaxBatchMutations = 256;
    constexpr size_t kMaxBatchBytes = 8u << 20;
    if (mutations.empty()) return Status::OK();
    if (mutations.size() > kMaxBatchMutations) {
      return Status::Error("batch exceeds 256 mutations");
    }

    // Normalize pure inputs before taking the writer-state mutex so concurrent
    // batches do not queue behind per-document array canonicalization.
    const double normalize_wall = capture ? NowMs() : 0.0;
    const double normalize_cpu = capture ? ThreadCpuMs() : 0.0;
    std::vector<Document> normalized_documents;
    normalized_documents.reserve(mutations.size());
    for (const Mutation& mutation : mutations) {
      if (mutation.kind == MutationKind::UPSERT) {
        normalized_documents.push_back(NormalizeDocument(mutation.document));
      } else {
        normalized_documents.emplace_back();
      }
    }
    if (capture) {
      diagnostics->normalize_ms = NowMs() - normalize_wall;
      diagnostics->normalize_cpu_ms = ThreadCpuMs() - normalize_cpu;
    }

    const double state_wait_wall = capture ? NowMs() : 0.0;
    const double state_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<std::recursive_mutex> lock(mutex_);
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return healthy;
    if (capture) {
      diagnostics->state_mutex_wait_ms = NowMs() - state_wait_wall;
      diagnostics->state_mutex_wait_cpu_ms =
          ThreadCpuMs() - state_wait_cpu;
    }
    ScopedStageTimer state_hold_timer(
        capture ? &diagnostics->state_mutex_hold_ms : nullptr,
        capture ? &diagnostics->state_mutex_hold_cpu_ms : nullptr);
    // Prepare the complete transaction without changing visible index state.
    const double encode_wall = capture ? NowMs() : 0.0;
    const double encode_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unordered_map<std::string, DocId> staged_ids;
    std::vector<std::string> records;
    records.reserve(mutations.size());
    uint64_t staged_next_id = next_doc_id_;
    size_t encoded_bytes = 0;
    auto current_id = [&](const std::string& pk) {
      const auto it = staged_ids.find(pk);
      return it == staged_ids.end() ? LookupPk(pk) : it->second;
    };
    for (size_t mutation_index = 0; mutation_index < mutations.size();
         ++mutation_index) {
      const Mutation& mutation = mutations[mutation_index];
      const uint64_t old_id = current_id(mutation.pk);
      BufWriter record;
      if (mutation.kind == MutationKind::DELETE) {
        if (old_id == 0) continue;
        record.U8(2);
        record.U64(old_id);
        record.String(mutation.pk);
        staged_ids[mutation.pk] = 0;
      } else if (mutation.kind == MutationKind::UPSERT) {
        if (staged_next_id == std::numeric_limits<uint64_t>::max()) {
          return Status::Error("DocId space exhausted");
        }
        const Document& normalized = normalized_documents[mutation_index];
        const uint64_t new_id = staged_next_id++;
        record.U8(1);
        record.U64(old_id);
        record.U64(new_id);
        record.String(mutation.pk);
        record.String(normalized.text);
        record.Varint(normalized.values.size());
        for (const auto& value : normalized.values) {
          record.String(value.field);
          record.String(value.value);
        }
        staged_ids[mutation.pk] = new_id;
      } else {
        return Status::Error("unknown batch mutation kind");
      }
      encoded_bytes += record.data().size() + sizeof(uint64_t);
      if (encoded_bytes > kMaxBatchBytes) {
        return Status::Error("batch exceeds 8 MiB encoded limit");
      }
      records.push_back(record.data());
    }
    if (records.empty()) return Status::OK();

    BufWriter batch;
    batch.U8(3);  // atomic mutation batch
    batch.Varint(records.size());
    for (const auto& record : records) batch.String(record);
    if (batch.data().size() > kMaxBatchBytes) {
      return Status::Error("batch exceeds 8 MiB encoded limit");
    }
    if (capture) {
      diagnostics->wal_encode_ms = NowMs() - encode_wall;
      diagnostics->wal_encode_cpu_ms = ThreadCpuMs() - encode_cpu;
    }

    const char* fault = std::getenv("M4_FAULT");
    if (fault && std::string(fault) == "batch_before_wal") {
      return Status::Error("injected fault before batch wal append");
    }
    const uint64_t seq = next_wal_seq_;
    if (options_.wal_enabled) {
      const double wal_wall = capture ? NowMs() : 0.0;
      const double wal_cpu = capture ? ThreadCpuMs() : 0.0;
      Status status = wal_.Append(
          seq, batch.data(), options_.fsync_wal,
          capture ? &diagnostics->wal_write_ms : nullptr,
          capture ? &diagnostics->wal_fsync_ms : nullptr);
      if (capture) {
        diagnostics->wal_append_ms = NowMs() - wal_wall;
        diagnostics->wal_append_cpu_ms = ThreadCpuMs() - wal_cpu;
      }
      if (!status.ok) return status;
    }
    if (fault && std::string(fault) == "batch_after_wal_sync") ::_exit(86);
    ++next_wal_seq_;
    last_wal_seq_ = seq;

    // WAL construction and sync do not mutate query-visible state. Keep readers
    // on the old snapshot until the durable frame is ready, then exclude them
    // while applying the state transition. FlushInternal may later release this
    // lock while building a segment, but only after the batch state is complete.
    const double query_wait_wall = capture ? NowMs() : 0.0;
    const double query_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
    if (capture) {
      diagnostics->query_mutex_wait_ms = NowMs() - query_wait_wall;
      diagnostics->query_mutex_wait_cpu_ms = ThreadCpuMs() - query_wait_cpu;
    }
    const double apply_wall = capture ? NowMs() : 0.0;
    const double apply_cpu = capture ? ThreadCpuMs() : 0.0;
    size_t applied = 0;
    for (const auto& record : records) {
      Status status = ReplayWalPayload(record);
      if (!status.ok) {
        if (capture) {
          diagnostics->apply_ms = NowMs() - apply_wall;
          diagnostics->apply_cpu_ms = ThreadCpuMs() - apply_cpu;
        }
        return Status::Error("durable batch apply failed: " + status.message);
      }
      if (++applied == 1 && fault &&
          std::string(fault) == "batch_after_first_apply") {
        ::_exit(87);
      }
    }
    if (capture) {
      diagnostics->apply_ms = NowMs() - apply_wall;
      diagnostics->apply_cpu_ms = ThreadCpuMs() - apply_cpu;
    }
    const double flush_wall = capture ? NowMs() : 0.0;
    const double flush_cpu = capture ? ThreadCpuMs() : 0.0;
    Status flush_status = MaybeAutoFlush(&query_guard);
    if (capture) {
      diagnostics->auto_flush_ms = NowMs() - flush_wall;
      diagnostics->auto_flush_cpu_ms = ThreadCpuMs() - flush_cpu;
    }
    return flush_status;
  }

  Status Flush() { return Flush(nullptr); }

  Status Flush(CompactionDiagnostics* diagnostics) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    if (diagnostics) {
      const bool requested = diagnostics->capture_timing;
      *diagnostics = CompactionDiagnostics{};
      diagnostics->capture_timing = requested;
    }
    ScopedStageTimer total_timer(capture ? &diagnostics->total_ms : nullptr,
                                 capture ? &diagnostics->total_cpu_ms : nullptr);
    const double state_wait_wall = capture ? NowMs() : 0.0;
    const double state_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<std::recursive_mutex> state_guard(mutex_);
    if (capture) {
      diagnostics->state_mutex_wait_ms = NowMs() - state_wait_wall;
      diagnostics->state_mutex_wait_cpu_ms =
          ThreadCpuMs() - state_wait_cpu;
    }
    ScopedStageTimer state_hold_timer(
        capture ? &diagnostics->state_mutex_hold_ms : nullptr,
        capture ? &diagnostics->state_mutex_hold_cpu_ms : nullptr);
    const double query_wait_wall = capture ? NowMs() : 0.0;
    const double query_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
    if (capture) {
      diagnostics->query_mutex_wait_ms = NowMs() - query_wait_wall;
      diagnostics->query_mutex_wait_cpu_ms = ThreadCpuMs() - query_wait_cpu;
    }
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return healthy;
    if (!pending_flush_) return Status::OK();
    const double flush_wall = capture ? NowMs() : 0.0;
    const double flush_cpu = capture ? ThreadCpuMs() : 0.0;
    Status status = FlushInternal(query_guard, diagnostics);
    if (capture) {
      diagnostics->pending_flush_ms = NowMs() - flush_wall;
      diagnostics->pending_flush_cpu_ms = ThreadCpuMs() - flush_cpu;
    }
    return status;
  }

  Status CompactNow() {
    return CompactNowWithDiagnostics(nullptr, false, false);
  }
  Status CompactNow(CompactionDiagnostics* diagnostics) {
    return CompactNowWithDiagnostics(diagnostics, false, false);
  }

  Status CompactNowSnapshotPrototype() {
    return CompactNowWithDiagnostics(nullptr, true, false);
  }
  Status CompactNowSnapshotPrototype(CompactionDiagnostics* diagnostics) {
    return CompactNowWithDiagnostics(diagnostics, true, false);
  }

  Status CompactNowSnapshotRebasePrototype() {
    return CompactNowWithDiagnostics(nullptr, true, true);
  }
  Status CompactNowSnapshotRebasePrototype(
      CompactionDiagnostics* diagnostics) {
    return CompactNowWithDiagnostics(diagnostics, true, true);
  }

  Status SearchFts(const std::string& query, uint32_t top_k, SearchResult* out,
                   QueryDiagnostics* diagnostics = nullptr) const {
    if (!diagnostics) {
      std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
      return SearchFtsInternal(query, top_k, nullptr, out, nullptr);
    }
    const bool capture_timing = diagnostics->capture_timing;
    const double wait_wall_start = capture_timing ? NowMs() : 0.0;
    const double wait_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    const double wait_ms = capture_timing ? NowMs() - wait_wall_start : 0.0;
    const double wait_cpu_ms = capture_timing
                                   ? ThreadCpuMs() - wait_cpu_start
                                   : 0.0;
    const Status status = SearchFtsInternal(query, top_k, nullptr, out,
                                            diagnostics);
    if (capture_timing) {
      diagnostics->mutex_wait_ms = wait_ms;
      diagnostics->mutex_wait_cpu_ms = wait_cpu_ms;
    }
    return status;
  }

  Status SearchFtsFiltered(const std::string& query, uint32_t top_k,
                           const FilterExpr& filter, SearchResult* out,
                           QueryDiagnostics* diagnostics) const {
    // ALL is exactly the unfiltered query: avoid materializing a live-DocId
    // candidate vector, which costs O(live_docs) on every repeated query.
    if (filter.op == FilterKind::ALL) {
      return SearchFts(query, top_k, out, diagnostics);
    }
    if (!diagnostics) {
      std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
      std::vector<DocId> candidates;
      bool cache_hit = options_.cache_filter_candidates &&
                       ReadFilterCache(filter, &candidates);
      Status status;
      if (!cache_hit) {
        status = FilterCandidates(filter, &candidates);
        if (status.ok && options_.cache_filter_candidates) {
          WriteFilterCache(filter, candidates);
        }
      }
      if (!status.ok) return status;
      return SearchFtsInternal(query, top_k, &candidates, out, nullptr);
    }
    const bool collect_counters = diagnostics->collect_counters;
    const bool capture_timing = diagnostics->capture_timing;
    const double wait_wall_start = capture_timing ? NowMs() : 0.0;
    const double wait_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    // Filtered queries are read-only against the query-locked index snapshot.
    // Candidate-cache state has its own short mutex, so cache hits/misses do
    // not serialize readers or require the writer state mutex.
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_, std::defer_lock);
    query_read_lock.lock();
    const double wait_ms = capture_timing ? NowMs() - wait_wall_start : 0.0;
    const double wait_cpu_ms = capture_timing
                                   ? ThreadCpuMs() - wait_cpu_start
                                   : 0.0;
    std::vector<DocId> candidates;
    const std::vector<DocId>* allowed = &candidates;
    std::optional<FilterLikeDiagnostics> filter_diag;
    if (collect_counters) filter_diag.emplace();
    bool filter_cache_hit = false;
    const double filter_wall_start = capture_timing ? NowMs() : 0.0;
    const double filter_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    Status status;
    if (options_.cache_filter_candidates) {
      filter_cache_hit = ReadFilterCache(filter, &candidates);
    }
    if (!filter_cache_hit) {
      status = FilterCandidates(filter, &candidates,
                                filter_diag ? &*filter_diag : nullptr);
      if (status.ok && options_.cache_filter_candidates) {
        WriteFilterCache(filter, candidates);
      }
    }
    const double filter_ms = capture_timing ? NowMs() - filter_wall_start : 0.0;
    const double filter_cpu_ms = capture_timing
                                     ? ThreadCpuMs() - filter_cpu_start
                                     : 0.0;
    if (!status.ok) {
      const bool keep_counters = diagnostics->collect_counters;
      const bool keep_timing = diagnostics->capture_timing;
      *diagnostics = QueryDiagnostics{};
      diagnostics->collect_counters = keep_counters;
      diagnostics->capture_timing = keep_timing;
      if (capture_timing) {
        diagnostics->mutex_wait_ms = wait_ms;
        diagnostics->mutex_wait_cpu_ms = wait_cpu_ms;
        diagnostics->filter_ms = filter_ms;
        diagnostics->filter_cpu_ms = filter_cpu_ms;
      }
      return status;
    }
    status = SearchFtsInternal(query, top_k, allowed, out, diagnostics);
    {
      if (capture_timing) {
        diagnostics->mutex_wait_ms = wait_ms;
        diagnostics->mutex_wait_cpu_ms = wait_cpu_ms;
        diagnostics->filter_ms = filter_ms;
        diagnostics->filter_cpu_ms = filter_cpu_ms;
      }
      if (collect_counters) {
        diagnostics->filter_candidates = allowed->size();
        diagnostics->filter_cache_hit = filter_cache_hit;
        diagnostics->filter_candidate_bytes = allowed->capacity() * sizeof(DocId);
        diagnostics->filter_like_strings_scanned = filter_diag->strings_scanned;
        diagnostics->filter_like_candidate_capacity_bytes =
            filter_diag->candidate_capacity_bytes;
      }
      const uint64_t nodes = collect_counters ? FilterNodeCount(filter) : 0;
      // Logical-payload bound only: at most three candidate buffers per
      // expression node, each bounded by twice the DocId high-water mark.
      // This intentionally excludes vector capacity slack and Hit/PK storage.
      const uint64_t high_water = next_doc_id_;
      const uint64_t factor = 6 * sizeof(DocId);
      const uint64_t maximum = std::numeric_limits<uint64_t>::max();
      if (collect_counters) {
        diagnostics->filter_docid_temp_payload_upper_bound_bytes =
            high_water != 0 && nodes > maximum / high_water / factor
                ? maximum
                : nodes * high_water * factor;
      }
    }
    return status;
  }

  Status SearchFilter(const FilterExpr& filter, SearchResult* out) const {
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    out->hits.clear();
    out->examined = 0;
    std::vector<DocId> candidates;
    Status status = FilterCandidates(filter, &candidates);
    if (!status.ok) return status;
    out->examined = candidates.size();
    for (DocId id : candidates) {
      const std::string* pk = PkForDoc(id);
      if (pk) out->hits.push_back(Hit{id, *pk, 0.0f});
    }
    return Status::OK();
  }

 private:
  Status CollectUncommittedDeletedIds(
      std::unordered_set<DocId>* deleted) {
    deleted->clear();
    if (!options_.wal_enabled) return Status::OK();
    std::vector<WalRecord> records;
    Status status = wal_.Replay(&records);
    if (!status.ok) return status;
    for (const WalRecord& record : records) {
      if (record.seq <= durable_wal_seq_) continue;
      status = CollectDeletedIdsFromWalPayload(record.payload, deleted);
      if (!status.ok) return status;
    }
    return Status::OK();
  }

  Status CompactNowWithDiagnostics(CompactionDiagnostics* diagnostics,
                                   bool snapshot_prototype,
                                   bool allow_concurrent_rebase) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    if (diagnostics) {
      const bool requested = diagnostics->capture_timing;
      *diagnostics = CompactionDiagnostics{};
      diagnostics->capture_timing = requested;
    }
    ScopedStageTimer total_timer(capture ? &diagnostics->total_ms : nullptr,
                                capture ? &diagnostics->total_cpu_ms : nullptr);
    const double state_wait_wall = capture ? NowMs() : 0.0;
    const double state_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<std::recursive_mutex> state_guard(mutex_);
    if (capture) {
      diagnostics->state_mutex_wait_ms = NowMs() - state_wait_wall;
      diagnostics->state_mutex_wait_cpu_ms = ThreadCpuMs() - state_wait_cpu;
    }
    ScopedStageTimer state_hold_timer(
        capture ? &diagnostics->state_mutex_hold_ms : nullptr,
        capture ? &diagnostics->state_mutex_hold_cpu_ms : nullptr);
    const double query_wait_wall = capture ? NowMs() : 0.0;
    const double query_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    std::unique_lock<detail::WriterPreferredSharedMutex> query_guard(query_mutex_);
    if (capture) {
      diagnostics->query_mutex_wait_ms = NowMs() - query_wait_wall;
      diagnostics->query_mutex_wait_cpu_ms = ThreadCpuMs() - query_wait_cpu;
    }
    const Status healthy = WriterGroupHealth();
    if (!healthy.ok) return healthy;
    const bool defer_rebase_flush =
        allow_concurrent_rebase && options_.wal_enabled;
    const double flush_wall = capture ? NowMs() : 0.0;
    const double flush_cpu = capture ? ThreadCpuMs() : 0.0;
    Status status = pending_flush_ && !defer_rebase_flush
                        ? FlushInternal(query_guard, diagnostics)
                        : Status::OK();
    if (capture) {
      diagnostics->pending_flush_ms = NowMs() - flush_wall;
      diagnostics->pending_flush_cpu_ms = ThreadCpuMs() - flush_cpu;
    }
    if (!status.ok) return status;
    std::vector<size_t> all;
    for (size_t i = 0; i < runs_.size(); ++i) all.push_back(i);
    if (all.empty()) return Status::OK();
    return MergeRuns(all, /*target_level=*/0, /*force=*/true, query_guard,
                     snapshot_prototype ? &state_guard : nullptr, diagnostics,
                     allow_concurrent_rebase,
                     capture ? &state_hold_timer : nullptr);
  }

  Status SearchFtsInternal(const std::string& query, uint32_t top_k,
                           const std::vector<DocId>* allowed, SearchResult* out,
                           QueryDiagnostics* diagnostics) const {
    QueryDiagnostics* diag = diagnostics;
    const bool collect_counters = diag && diag->collect_counters;
    const bool capture_timing = diag && diag->capture_timing;
    QueryDiagnostics* counter_diag = collect_counters ? diag : nullptr;
    if (diag) {
      *diag = QueryDiagnostics{};
      diag->collect_counters = collect_counters;
      diag->capture_timing = capture_timing;
    }
    out->hits.clear();
    out->examined = 0;
    if (top_k == 0) return Status::OK();
    const double setup_start = capture_timing ? NowMs() : 0.0;
    const double setup_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    const double parse_start = capture_timing ? NowMs() : 0.0;
    const double parse_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    const ParsedQuery q = ParseQuery(query, analyzer_);
    if (capture_timing) {
      diag->query_parse_ms = NowMs() - parse_start;
      diag->query_parse_cpu_ms = ThreadCpuMs() - parse_cpu_start;
    }
    if (q.terms.empty()) {
      if (capture_timing) {
        diag->setup_ms = NowMs() - setup_start;
        diag->setup_cpu_ms = ThreadCpuMs() - setup_cpu_start;
      }
      return Status::OK();
    }

    const double n = static_cast<double>(stats_.live_docs);
    const double avg_dl =
        stats_.live_docs == 0
            ? 1.0
            : static_cast<double>(stats_.live_tokens) / stats_.live_docs;

    std::vector<std::unique_ptr<TermUnionCursor>> cursors;
    cursors.reserve(q.terms.size());
    std::vector<const std::vector<uint32_t>*> phrase_positions;
    std::vector<std::vector<size_t>> phrase_term_indices;
    std::vector<size_t> phrase_scan_indices;
    if (!q.phrases.empty()) {
      size_t max_phrase_terms = 0;
      phrase_term_indices.reserve(q.phrases.size());
      for (const auto& phrase : q.phrases) {
        max_phrase_terms = std::max(max_phrase_terms, phrase.size());
        std::vector<size_t> indices;
        indices.reserve(phrase.size());
        for (const auto& term : phrase) {
          const auto it = std::find(q.terms.begin(), q.terms.end(), term);
          indices.push_back(static_cast<size_t>(it - q.terms.begin()));
        }
        phrase_term_indices.push_back(std::move(indices));
      }
      phrase_positions.reserve(max_phrase_terms);
      phrase_scan_indices.resize(max_phrase_terms);
    }
    std::unordered_set<size_t> touched_runs;
    if (counter_diag) touched_runs.reserve(runs_.size());
    bool missing = false;
    for (const auto& term : q.terms) {
      const uint64_t df = LiveDf(term);
      const double idf =
          n <= 0.0 ? 0.0
                   : std::log((n - static_cast<double>(df) + 0.5) /
                                  (static_cast<double>(df) + 0.5) +
                              1.0);
      std::vector<std::unique_ptr<PostingCursor>> parts;
      for (size_t run_idx = 0; run_idx < runs_.size(); ++run_idx) {
        const auto& run = runs_[run_idx];
        const TermRef* t = run.segment->FindTerm(term);
        if (!t) continue;
        if (counter_diag) {
          touched_runs.insert(run_idx);
          ++counter_diag->run_term_parts;
        }
        parts.emplace_back(std::make_unique<SegmentCursor>(
            run.segment, t,
            options_.decode_positions || !q.phrases.empty(), idf,
            options_.bm25_k1, options_.bm25_b, avg_dl, counter_diag));
      }
      const auto it = delta_postings_.find(term);
      if (it != delta_postings_.end()) {
        parts.emplace_back(std::make_unique<DeltaCursor>(
            &it->second, idf, options_.bm25_k1, options_.bm25_b, avg_dl,
            counter_diag));
      }
      if (parts.empty()) {
        missing = true;
        if (q.and_mode) break;
        continue;
      }
      cursors.emplace_back(std::make_unique<TermUnionCursor>(
          term, std::move(parts), idf, options_.bm25_k1, options_.bm25_b,
          avg_dl, counter_diag));
    }
    if (counter_diag) counter_diag->runs_read = touched_runs.size();
    if (capture_timing) {
      diag->setup_ms = NowMs() - setup_start;
      diag->setup_cpu_ms = ThreadCpuMs() - setup_cpu_start;
    }
    if (missing && q.and_mode) return Status::OK();
    if (cursors.empty()) return Status::OK();
    const double execution_start = capture_timing ? NowMs() : 0.0;
    const double execution_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;

    auto live = [&](DocId id) { return tombstones_.count(id) == 0; };

    struct HeapCmp {
      bool operator()(const Hit& a, const Hit& b) const {
        return a.score > b.score;  // min-heap by score
      }
    };
    std::priority_queue<Hit, std::vector<Hit>, HeapCmp> heap;

    auto push_hit = [&](DocId id, float score) {
      if (allowed) {
        if (counter_diag) ++counter_diag->filter_checks;
        if (!std::binary_search(allowed->begin(), allowed->end(), id)) {
          if (counter_diag) ++counter_diag->filter_rejections;
          return;
        }
      }
      if (counter_diag) ++counter_diag->liveness_lookups;
      if (!live(id)) {
        if (counter_diag) ++counter_diag->dead_postings_filtered;
        return;
      }
      if (counter_diag) ++counter_diag->candidates;
      if (heap.size() < top_k) {
        if (counter_diag) ++counter_diag->topk_insertions;
        const std::string* pk = PkForDoc(id);
        heap.push(Hit{id, pk ? *pk : std::string(), score});
        if (counter_diag) ++counter_diag->topk_pushes;
      } else if (score > heap.top().score) {
        if (counter_diag) {
          ++counter_diag->topk_replacements;
          ++counter_diag->topk_pops;
        }
        heap.pop();
        const std::string* pk = PkForDoc(id);
        heap.push(Hit{id, pk ? *pk : std::string(), score});
        if (counter_diag) ++counter_diag->topk_pushes;
      } else if (counter_diag) {
        ++counter_diag->topk_rejections;
      }
    };

    if (q.and_mode) {
      // Document-at-a-time intersection. Phrase queries force AND mode.
      while (true) {
        DocId max_doc = 0;
        bool any_invalid = false;
        for (auto& c : cursors) {
          if (!c->valid()) {
            any_invalid = true;
            break;
          }
          max_doc = std::max(max_doc, c->doc());
        }
        if (any_invalid) break;
        bool all_equal = true;
        for (auto& c : cursors) {
          if (c->doc() != max_doc) {
            c->Advance(max_doc);
            all_equal = false;
          }
        }
        if (!all_equal) continue;
        float score = 0.0f;
        for (auto& c : cursors) score += c->score();
        bool phrase_ok = true;
        for (const auto& indices : phrase_term_indices) {
          phrase_positions.clear();
          for (size_t idx : indices) {
            if (idx >= cursors.size()) {
              phrase_ok = false;
              break;
            }
            phrase_positions.push_back(&cursors[idx]->positions());
          }
          if (!phrase_ok ||
              !PhraseMatches(phrase_positions, &phrase_scan_indices)) {
            phrase_ok = false;
            break;
          }
        }
        if (phrase_ok) push_hit(max_doc, score);
        ++out->examined;
        for (auto& c : cursors) c->Next();
      }
    } else {
      // WAND over the union of terms.
      std::vector<TermUnionCursor*> active;
      for (auto& c : cursors) {
        if (c->valid()) active.push_back(c.get());
      }
      while (!active.empty()) {
        active.erase(std::remove_if(active.begin(), active.end(),
                                    [](TermUnionCursor* c) {
                                      return !c->valid();
                                    }),
                     active.end());
        if (active.empty()) break;
        std::sort(active.begin(), active.end(),
                  [](TermUnionCursor* a, TermUnionCursor* b) {
                    return a->doc() < b->doc();
                  });
        if (counter_diag) ++counter_diag->wand_pivots;
        const float threshold =
            heap.size() < top_k ? -std::numeric_limits<float>::infinity()
                                : heap.top().score;
        double acc = 0.0;
        int pivot = -1;
        for (size_t i = 0; i < active.size(); ++i) {
          if (counter_diag) ++counter_diag->upper_bound_evaluations;
          acc += active[i]->max_score();
          if (acc > threshold) {
            pivot = static_cast<int>(i);
            break;
          }
        }
        if (pivot < 0) break;
        const DocId pivot_doc = active[pivot]->doc();
        if (active[0]->doc() == pivot_doc) {
          float score = 0.0f;
          for (auto* c : active) {
            if (c->doc() == pivot_doc) score += c->score();
          }
          push_hit(pivot_doc, score);
          ++out->examined;
          for (auto* c : active) {
            if (c->doc() == pivot_doc) c->Next();
          }
        } else {
          for (int i = 0; i < pivot; ++i) active[i]->Advance(pivot_doc);
        }
      }
    }

    if (capture_timing) {
      diag->execution_ms = NowMs() - execution_start;
      diag->execution_cpu_ms = ThreadCpuMs() - execution_cpu_start;
    }
    const double finalize_start = capture_timing ? NowMs() : 0.0;
    const double finalize_cpu_start = capture_timing ? ThreadCpuMs() : 0.0;
    std::vector<Hit> hits;
    hits.reserve(heap.size());
    while (!heap.empty()) {
      hits.push_back(heap.top());
      heap.pop();
      if (counter_diag) ++counter_diag->topk_finalize_pops;
    }
    std::sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
      if (a.score != b.score) return a.score > b.score;
      return a.doc_id < b.doc_id;
    });
    if (counter_diag) diag->sorted_hits = hits.size();
    out->hits = std::move(hits);
    if (capture_timing) {
      diag->finalize_ms = NowMs() - finalize_start;
      diag->finalize_cpu_ms = ThreadCpuMs() - finalize_cpu_start;
    }
    if (diag) diag->examined = out->examined;
    return Status::OK();
  }

 public:
  Status SearchInvertUint32(const std::string& field, uint32_t value,
                             SearchResult* out) const {
    const auto encoded = ScalarValue::Unsigned32(field, value);
    return SearchInvert(field, encoded.value, CompareOp::EQ, out);
  }

  Status SearchInvertUint32In(const std::string& field,
                              const std::vector<uint32_t>& values,
                              SearchResult* out) const {
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    out->hits.clear();
    out->examined = 0;
    std::set<std::string> wanted;
    for (const uint32_t value : values) {
      wanted.insert(ScalarValue::Unsigned32(field, value).value);
    }
    std::vector<DocId> docs;
    for (const auto& encoded : wanted) {
      std::vector<DocId> part;
      CollectValueDocs(field, encoded, &part);
      docs.insert(docs.end(), part.begin(), part.end());
    }
    std::sort(docs.begin(), docs.end());
    docs.erase(std::unique(docs.begin(), docs.end()), docs.end());
    out->examined = docs.size();
    for (DocId id : docs) {
      if (tombstones_.count(id) != 0) continue;
      const std::string* pk = PkForDoc(id);
      out->hits.push_back(Hit{id, pk ? *pk : std::string(), 0.0f});
    }
    return Status::OK();
  }

  Status SearchArrayUint32(const std::string& field,
                           const std::vector<uint32_t>& values,
                           bool negate, SearchResult* out) const {
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    out->hits.clear();
    out->examined = 0;
    if (values.size() > kMaxArrayUint32Operands) {
      return Status::Error("ArrayUint32 filter accepts at most 32 operands");
    }

    std::vector<DocId> matched;
    std::set<uint32_t> wanted(values.begin(), values.end());
    for (uint32_t value : wanted) {
      std::vector<DocId> part;
      CollectValueDocs(field, EncodeArrayUint32Element(value), &part);
      out->examined += part.size();
      std::sort(part.begin(), part.end());
      part.erase(std::unique(part.begin(), part.end()), part.end());
      part.erase(std::remove_if(part.begin(), part.end(), [&](DocId id) {
                   return tombstones_.count(id) != 0;
                 }),
                 part.end());
      std::vector<DocId> merged;
      merged.reserve(matched.size() + part.size());
      std::set_union(matched.begin(), matched.end(), part.begin(), part.end(),
                     std::back_inserter(merged));
      matched.swap(merged);
    }

    if (!negate) {
      for (DocId id : matched) {
        const std::string* pk = PkForDoc(id);
        out->hits.push_back(Hit{id, pk ? *pk : std::string(), 0.0f});
      }
      return Status::OK();
    }

    // Native NOT CONTAIN ANY is complement within the non-null field bitmap,
    // not within every live document. The marker preserves empty arrays and
    // excludes missing fields. Only this query materializes that universe.
    std::vector<DocId> universe;
    CollectValueDocs(field, std::string(kArrayUint32Presence, 5), &universe);
    out->examined += universe.size();
    std::sort(universe.begin(), universe.end());
    universe.erase(std::unique(universe.begin(), universe.end()),
                   universe.end());
    universe.erase(std::remove_if(universe.begin(), universe.end(),
                                  [&](DocId id) {
                                    return tombstones_.count(id) != 0;
                                  }),
                   universe.end());
    size_t match_index = 0;
    for (DocId id : universe) {
      while (match_index < matched.size() && matched[match_index] < id) {
        ++match_index;
      }
      if (match_index < matched.size() && matched[match_index] == id) continue;
      const std::string* pk = PkForDoc(id);
      out->hits.push_back(Hit{id, pk ? *pk : std::string(), 0.0f});
    }
    return Status::OK();
  }

  Status SearchInvert(const std::string& field, const std::string& value,
                      CompareOp op, SearchResult* out) const {
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    out->hits.clear();
    out->examined = 0;
    std::set<std::string> wanted;
    auto in_range = [&](const std::string& v) {
      switch (op) {
        case CompareOp::EQ: return v == value;
        case CompareOp::LT: return v < value;
        case CompareOp::LE: return v <= value;
        case CompareOp::GT: return v > value;
        case CompareOp::GE: return v >= value;
      }
      return false;
    };
    if (op == CompareOp::EQ) {
      // Point lookup must not scan the whole dictionary.
      wanted.insert(value);
    } else {
      for (const auto& run : runs_) {
        const InvertFieldRef* f = run.segment->FindField(field);
        if (!f) continue;
        const auto& values = f->values;
        auto begin = values.begin();
        auto end = values.end();
        auto key = [](const InvertValueRef& v) -> const std::string& {
          return v.value;
        };
        switch (op) {
          case CompareOp::LT:
            end = std::lower_bound(values.begin(), values.end(), value,
                                   [&](const InvertValueRef& v,
                                       const std::string& k) {
                                     return v.value < k;
                                   });
            break;
          case CompareOp::LE:
            end = std::upper_bound(values.begin(), values.end(), value,
                                   [](const std::string& k,
                                      const InvertValueRef& v) {
                                     return k < v.value;
                                   });
            break;
          case CompareOp::GT:
            begin = std::upper_bound(values.begin(), values.end(), value,
                                     [](const std::string& k,
                                        const InvertValueRef& v) {
                                       return k < v.value;
                                     });
            break;
          case CompareOp::GE:
            begin = std::lower_bound(values.begin(), values.end(), value,
                                     [&](const InvertValueRef& v,
                                         const std::string& k) {
                                       return v.value < k;
                                     });
            break;
          case CompareOp::EQ:
            break;
        }
        for (auto it = begin; it != end; ++it) wanted.insert(key(*it));
      }
      const auto dit = delta_invert_.find(field);
      if (dit != delta_invert_.end()) {
        for (const auto& [v, docs] : dit->second) {
          (void)docs;
          if (in_range(v)) wanted.insert(v);
        }
      }
    }
    std::vector<DocId> docs;
    for (const auto& v : wanted) {
      std::vector<DocId> part;
      CollectValueDocs(field, v, &part);
      docs.insert(docs.end(), part.begin(), part.end());
    }
    std::sort(docs.begin(), docs.end());
    docs.erase(std::unique(docs.begin(), docs.end()), docs.end());
    out->examined = docs.size();
    for (DocId id : docs) {
      if (tombstones_.count(id) != 0) continue;
      const std::string* pk = PkForDoc(id);
      out->hits.push_back(Hit{id, pk ? *pk : std::string(), 0.0f});
    }
    return Status::OK();
  }

  Status SearchHybrid(
      const std::string& query, uint32_t top_k, double alpha,
      const std::unordered_map<std::string, float>& vector_scores,
      SearchResult* out) const {
    out->hits.clear();
    out->examined = 0;
    if (top_k == 0) return Status::OK();
    std::shared_lock<detail::WriterPreferredSharedMutex> query_read_lock(query_mutex_);
    SearchResult fts;
    const uint64_t fts_top_k = std::min<uint64_t>(
        static_cast<uint64_t>(top_k) * 4,
        std::numeric_limits<uint32_t>::max());
    Status s = SearchFtsInternal(query, static_cast<uint32_t>(fts_top_k),
                                 nullptr, &fts, nullptr);
    if (!s.ok) return s;
    out->examined = fts.examined;

    float fmin = std::numeric_limits<float>::max();
    float fmax = std::numeric_limits<float>::lowest();
    for (const auto& h : fts.hits) {
      fmin = std::min(fmin, h.score);
      fmax = std::max(fmax, h.score);
    }
    float vmin = std::numeric_limits<float>::max();
    float vmax = std::numeric_limits<float>::lowest();
    for (const auto& [pk, score] : vector_scores) {
      (void)pk;
      vmin = std::min(vmin, score);
      vmax = std::max(vmax, score);
    }
    // Borrow map keys from stable inputs; Hit owns the pk moved into output.
    std::unordered_map<std::string_view, Hit> merged;
    for (const auto& h : fts.hits) {
      const float norm =
          fmax > fmin ? (h.score - fmin) / (fmax - fmin) : 0.0f;
      const auto vit = vector_scores.find(h.pk);
      float vn = 0.0f;
      if (vit != vector_scores.end() && vmax > vmin) {
        vn = (vit->second - vmin) / (vmax - vmin);
      }
      const uint64_t id = LookupPk(h.pk);
      if (id == 0 || tombstones_.count(id) != 0) continue;
      merged[std::string_view(h.pk)] =
          Hit{id, h.pk, static_cast<float>(alpha * norm + (1.0 - alpha) * vn)};
    }
    for (const auto& [pk, score] : vector_scores) {
      if (merged.count(std::string_view(pk)) != 0) continue;
      const uint64_t id = LookupPk(pk);
      if (id == 0 || tombstones_.count(id) != 0) continue;
      const float vn = vmax > vmin ? (score - vmin) / (vmax - vmin) : 0.0f;
      merged[std::string_view(pk)] =
          Hit{id, pk, static_cast<float>((1.0 - alpha) * vn)};
    }
    for (auto& entry : merged) out->hits.push_back(std::move(entry.second));
    std::sort(out->hits.begin(), out->hits.end(),
              [](const Hit& a, const Hit& b) {
                if (a.score != b.score) return a.score > b.score;
                return a.pk < b.pk;
              });
    if (out->hits.size() > top_k) out->hits.resize(top_k);
    return Status::OK();
  }

  IndexStats GetStats() const {
    std::lock_guard<std::recursive_mutex> lock(mutex_);
    IndexStats s;
    s.live_docs = stats_.live_docs;
    s.live_tokens = stats_.live_tokens;
    s.dead_docs = tombstones_.size();
    s.runs = runs_.size();
    s.wal_bytes_appended = wal_.bytes_appended();
    s.wal_fsyncs = wal_.fsync_count();
    s.wal_lz4_attempts = wal_.lz4_attempts();
    s.wal_lz4_attempted_input_bytes = wal_.lz4_attempted_input_bytes();
    s.wal_lz4_compressed_frames = wal_.lz4_compressed_frames();
    s.wal_lz4_input_bytes = wal_.lz4_input_bytes();
    s.wal_lz4_output_bytes = wal_.lz4_output_bytes();
    s.wal_lz4_compress_cpu_ms = wal_.lz4_compress_cpu_ms();
    s.fsyncs = fsyncs_;
    s.compactions = compactions_;
    s.compacted_docs = compacted_docs_;
    s.bytes_written = bytes_written_;
    s.segment_bytes_written = segment_bytes_written_;
    s.run_ids_created = run_id_seq_;
    s.compaction_wall_ms = compaction_wall_ms_;
    s.compaction_merges = compaction_merges_;
    s.compaction_input_docs = compaction_input_docs_;
    s.compaction_output_docs = compaction_output_docs_;
    s.compaction_peak_bytes = compaction_peak_bytes_;
    uint64_t max_level = 0;
    for (const auto& r : runs_) max_level = std::max(max_level, r.info.level);
    s.runs_per_level.assign(max_level + 1, 0);
    for (const auto& r : runs_) ++s.runs_per_level[r.info.level];
    auto add_size = [&](const std::string& name, uint64_t* field) {
      if (name.empty()) return;
      std::error_code ec;
      const auto sz = fs::file_size(fs::path(dir_) / name, ec);
      if (!ec) *field += sz;
    };
    for (const auto& r : runs_) add_size(r.info.file, &s.segments_bytes);
    add_size(manifest_.tombstone_file, &s.tombstone_bytes);
    add_size(StatsLogName(stats_epoch_), &s.stats_log_bytes);
    add_size("wal.log", &s.wal_bytes);
    return s;
  }


 private:
  struct Run {
    RunInfo info;
    SegmentPtr segment;
  };

  struct DeltaDoc {
    DocId id{0};
    uint32_t dl{0};
    std::string pk;
    std::string text;
    std::vector<ScalarValue> values;
    std::vector<std::string> unique_terms;
  };

  // ---------------------------- mutation --------------------------------

  Status MaybeAutoFlush(std::unique_lock<detail::WriterPreferredSharedMutex>* query_guard) {
    if (delta_docs_.size() >= options_.max_delta_docs ||
        delta_bytes_ >= options_.max_delta_bytes) {
      return FlushInternal(*query_guard);
    }
    return Status::OK();
  }

  Status WaitForTestWriterGroupStepBarrier() const {
    const char* barrier_dir =
        std::getenv("M4_TEST_WRITER_GROUP_STEP_BARRIER");
    if (!barrier_dir || !*barrier_dir) return Status::OK();
    const fs::path barrier(barrier_dir);
    std::error_code ec;
    fs::create_directories(barrier, ec);
    if (ec) return Status::Error("create writer-group test barrier: " + ec.message());
    {
      std::ofstream applied(barrier / "first-upsert-applied", std::ios::binary);
      if (!applied) return Status::Error("write writer-group test barrier marker");
      applied << "applied";
    }
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(5);
    while (!fs::exists(barrier / "continue", ec)) {
      if (ec) return Status::Error("check writer-group test barrier: " + ec.message());
      if (std::chrono::steady_clock::now() >= deadline) {
        return Status::Error("writer-group test barrier timed out");
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return Status::OK();
  }

  // Test-only synchronization point; unset in normal operation.
  Status WaitForTestWalApplyBarrier() const {
    const char* barrier_dir = std::getenv("M4_TEST_WAL_APPLY_BARRIER");
    if (!barrier_dir || !*barrier_dir) return Status::OK();
    const fs::path barrier(barrier_dir);
    std::error_code ec;
    fs::create_directories(barrier, ec);
    if (ec) return Status::Error("create test WAL apply barrier: " + ec.message());
    {
      std::ofstream ready(barrier / "wal-ready", std::ios::binary);
      if (!ready) return Status::Error("write test WAL apply barrier marker");
      ready << "ready\n";
    }
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(5);
    while (!fs::exists(barrier / "reader-finished", ec)) {
      if (ec) return Status::Error("check test WAL apply barrier: " + ec.message());
      if (std::chrono::steady_clock::now() >= deadline) {
        return Status::Error("test WAL apply barrier timed out");
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return Status::OK();
  }

  uint64_t LookupPk(const std::string& pk) const {
    const auto it = pk_to_doc_.find(pk);
    return it == pk_to_doc_.end() ? 0 : it->second;
  }

  const std::string* PkForDoc(DocId id) const {
    for (const auto& d : delta_docs_) {
      if (d.id == id) return &d.pk;
    }
    const Run* run = FindRunForDoc(id);
    if (!run) return nullptr;
    const DocMeta* doc = run->segment->FindDoc(id);
    return doc ? &doc->pk : nullptr;
  }

  const Run* FindRunForDoc(DocId id) const {
    // Runs are sorted by first_doc_id and ranges are non-overlapping.
    auto it = std::upper_bound(
        runs_.begin(), runs_.end(), id,
        [](DocId target, const Run& r) {
          return target < r.segment->first_doc_id();
        });
    if (it == runs_.begin()) return nullptr;
    --it;
    if (id >= it->segment->first_doc_id() &&
        id <= it->segment->last_doc_id()) {
      return &*it;
    }
    return nullptr;
  }

  Run* FindRunForDoc(DocId id) {
    return const_cast<Run*>(static_cast<const Impl*>(this)->FindRunForDoc(id));
  }

  std::vector<std::string> DocTerms(DocId id) const {
    for (const auto& d : delta_docs_) {
      if (d.id == id) return d.unique_terms;
    }
    const Run* run = FindRunForDoc(id);
    if (!run) return {};
    const DocMeta* doc = run->segment->FindDoc(id);
    if (!doc) return {};
    std::vector<std::string> out;
    out.reserve(doc->term_ids.size());
    for (uint32_t tid : doc->term_ids) out.push_back(run->segment->terms()[tid].term);
    return out;
  }

  uint32_t DocLen(DocId id) const {
    for (const auto& d : delta_docs_) {
      if (d.id == id) return d.dl;
    }
    const Run* run = FindRunForDoc(id);
    if (!run) return 0;
    const DocMeta* doc = run->segment->FindDoc(id);
    return doc ? doc->dl : 0;
  }

  void AddStatsEvent(StatsEvent e) {
    ApplyStatsEvent(&stats_, e);
    pending_events_.push_back(std::move(e));
  }

  Status DeleteInternal(DocId id, bool from_wal) {
    (void)from_wal;
    if (id == 0 || tombstones_.count(id) != 0) return Status::OK();
    ++state_generation_;
    InvalidateFilterCache();
    const uint32_t dl = DocLen(id);
    const std::vector<std::string> terms = DocTerms(id);
    std::string pk;
    if (const std::string* p = PkForDoc(id)) pk = *p;

    if (Run* run = FindRunForDoc(id)) {
      ++run->info.dead_count;
    }
    tombstones_.insert(id);
    if (!pk.empty()) pk_to_doc_.erase(pk);

    AddStatsEvent({StatsEvent::Kind::kDocs, "", -1});
    AddStatsEvent({StatsEvent::Kind::kTokens, "", -static_cast<int64_t>(dl)});
    for (const auto& term : terms) {
      AddStatsEvent({StatsEvent::Kind::kDf, term, -1});
    }
    return Status::OK();
  }

  Status InsertInternal(DocId id, const std::string& pk, const Document& doc) {
    ++state_generation_;
    InvalidateFilterCache();
    const auto tokens = analyzer_.Tokenize(doc.text);
    DeltaDoc d;
    d.id = id;
    d.dl = static_cast<uint32_t>(tokens.size());
    d.pk = pk;
    d.text = doc.text;
    d.values = doc.values;

    std::unordered_map<std::string, std::vector<uint32_t>> by_term;
    for (const auto& t : tokens) by_term[t.term].push_back(t.pos);
    d.unique_terms.reserve(by_term.size());
    for (auto& [term, positions] : by_term) {
      BuildPosting p;
      p.doc = id;
      p.dl = d.dl;
      p.tf = static_cast<uint32_t>(positions.size());
      p.positions = std::move(positions);
      delta_postings_[term].push_back(std::move(p));
      d.unique_terms.push_back(term);
    }
    for (const auto& v : doc.values) {
      delta_invert_[v.field][v.value].push_back(id);
    }

    AddStatsEvent({StatsEvent::Kind::kDocs, "", 1});
    AddStatsEvent({StatsEvent::Kind::kTokens, "", static_cast<int64_t>(d.dl)});
    for (const auto& term : d.unique_terms) {
      AddStatsEvent({StatsEvent::Kind::kDf, term, 1});
    }

    delta_bytes_ += doc.text.size() + 64 +
                    static_cast<uint64_t>(d.unique_terms.size()) * 32;
    for (const auto& value : doc.values) {
      delta_bytes_ += value.field.size() + value.value.size() + 32;
    }
    delta_docs_.push_back(std::move(d));
    pk_to_doc_[pk] = id;
    next_doc_id_ = std::max(next_doc_id_, id + 1);
    return Status::OK();
  }

  // ------------------------------ flush ----------------------------------

  Status FlushInternal(
      std::unique_lock<detail::WriterPreferredSharedMutex>& query_guard,
      CompactionDiagnostics* diagnostics = nullptr) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    // Segment construction and durable metadata staging read the current delta
    // but do not alter query-visible state. Let readers continue against that
    // complete state; re-acquire exclusively only for the atomic in-memory
    // publication. The state mutex remains held to serialize writers and keep
    // the delta/WAL prefix stable throughout the durable commit.
    if (query_guard.owns_lock()) query_guard.unlock();
    ScopedStageTimer flush_query_hold_timer(
        capture ? &diagnostics->flush_query_mutex_hold_ms : nullptr,
        capture ? &diagnostics->flush_query_mutex_hold_cpu_ms : nullptr,
        false);

    // Docs that were created and deleted inside this delta were never
    // persisted. Their tombstones are dropped only after the segment build has
    // filtered them out; dropping them before the build would resurrect them.
    ScopedStageTimer segment_build_timer(
        capture ? &diagnostics->flush_segment_build_ms : nullptr,
        capture ? &diagnostics->flush_segment_build_cpu_ms : nullptr);
    std::vector<DocId> never_persisted;
    size_t live_doc_count = 0;
    for (const auto& d : delta_docs_) {
      if (tombstones_.count(d.id) == 0) {
        ++live_doc_count;
      } else if (FindRunForDoc(d.id) == nullptr) {
        never_persisted.push_back(d.id);
      }
    }

    bool wrote_run = false;
    Run new_run;
    if (!delta_docs_.empty()) {
      SegmentBuild build;
      build.run_id = run_id_seq_ + 1;
      build.level = 0;
      build.block_size = options_.block_size;

      // Terms that still have at least one live posting.
      std::vector<std::string> terms;
      terms.reserve(delta_postings_.size());
      for (const auto& [term, postings] : delta_postings_) {
        bool any = false;
        for (const auto& p : postings) {
          if (tombstones_.count(p.doc) == 0) {
            any = true;
            break;
          }
        }
        if (any) terms.push_back(term);
      }
      std::sort(terms.begin(), terms.end());
      build.terms = std::move(terms);
      build.postings.resize(build.terms.size());
      {
        // Borrow keys from the stable, sorted build.terms vector instead of
        // allocating a second copy of every vocabulary string in the map.
        std::unordered_map<std::string_view, uint32_t> term_id;
        term_id.reserve(build.terms.size() * 2);
        for (size_t i = 0; i < build.terms.size(); ++i) {
          term_id.emplace(build.terms[i], static_cast<uint32_t>(i));
        }

        for (const auto& term : build.terms) {
          const auto term_it = term_id.find(std::string_view(term));
          auto& dst = build.postings[term_it->second];
          for (const auto& p : delta_postings_.at(term)) {
            if (tombstones_.count(p.doc) == 0) dst.push_back(p);
          }
          std::sort(dst.begin(), dst.end(),
                    [](const BuildPosting& a, const BuildPosting& b) {
                      return a.doc < b.doc;
                    });
        }

        build.docs.reserve(live_doc_count);
        for (const auto& d : delta_docs_) {
          if (tombstones_.count(d.id) != 0) continue;
          BuildDoc bd;
          bd.id = d.id;
          bd.dl = d.dl;
          bd.pk = d.pk;
          for (const auto& t : d.unique_terms) {
            const auto it = term_id.find(std::string_view(t));
            if (it != term_id.end()) bd.term_ids.push_back(it->second);
          }
          std::sort(bd.term_ids.begin(), bd.term_ids.end());
          bd.term_ids.erase(std::unique(bd.term_ids.begin(), bd.term_ids.end()),
                            bd.term_ids.end());
          build.total_tokens += bd.dl;
          build.docs.push_back(std::move(bd));
        }
      }

      for (const auto& [field, values] : delta_invert_) {
        BuildField bf;
        bf.name = field;
        for (const auto& [value, docs] : values) {
          BuildValue bv;
          bv.value = value;
          for (DocId id : docs) {
            if (tombstones_.count(id) == 0) bv.docs.push_back(id);
          }
          if (!bv.docs.empty()) {
            // WriteSegment sorts value doc IDs before encoding.
            bf.values.push_back(std::move(bv));
          }
        }
        if (!bf.values.empty()) build.fields.push_back(std::move(bf));
      }

      if (!build.docs.empty()) {
        const std::string file = RunFileName(build.run_id);
        std::string err;
        segment_build_timer.Stop();
        Status s;
        {
          ScopedStageTimer segment_write_timer(
              capture ? &diagnostics->flush_segment_write_ms : nullptr,
              capture ? &diagnostics->flush_segment_write_cpu_ms : nullptr);
          s = WriteSegment((fs::path(dir_) / file).string(),
                           std::move(build), &err);
        }
        if (!s.ok) return Status::Error("write segment: " + err);
        new_run.info.id = run_id_seq_ + 1;
        new_run.info.level = 0;
        new_run.info.doc_count = live_doc_count;
        new_run.info.dead_count = 0;
        new_run.info.file = file;
        {
          ScopedStageTimer segment_open_timer(
              capture ? &diagnostics->flush_segment_open_ms : nullptr,
              capture ? &diagnostics->flush_segment_open_cpu_ms : nullptr);
          std::error_code ec;
          new_run.info.file_size =
              fs::file_size(fs::path(dir_) / file, ec);
          s = Segment::Open((fs::path(dir_) / file).string(), &new_run.segment,
                            &err);
        }
        if (!s.ok) return Status::Error("reopen new run: " + err);
        wrote_run = true;
      }
    }
    segment_build_timer.Stop();

    // The baseline control reacquires the reader lock before metadata writes,
    // matching the pre-staging boundary. Candidate mode leaves readers on the
    // old view until the durable manifest is ready. Both paths retain the
    // writer-state lock and the same WAL/publication ordering.
    if (!options_.flush_metadata_outside_query_lock && !query_guard.owns_lock()) {
      const double wait_wall = capture ? NowMs() : 0.0;
      const double wait_cpu = capture ? ThreadCpuMs() : 0.0;
      query_guard.lock();
      if (capture) {
        diagnostics->flush_query_mutex_wait_ms += NowMs() - wait_wall;
        diagnostics->flush_query_mutex_wait_cpu_ms += ThreadCpuMs() - wait_cpu;
      }
      flush_query_hold_timer.Start();
    }
    Status s;
    {
      ScopedStageTimer timer(
          capture ? &diagnostics->flush_stats_append_ms : nullptr,
          capture ? &diagnostics->flush_stats_append_cpu_ms : nullptr);
      s = AppendStatsEvents();
    }
    if (!s.ok) return s;
    const char* fault = std::getenv("M4_FAULT");
    if (fault && std::strcmp(fault, "flush_after_stats_append") == 0) ::_exit(93);
    if (fault && std::strcmp(fault, "flush_fail_after_stats_append") == 0)
      return Status::Error("injected flush failure after stats append");

    std::unordered_set<DocId> next_tombstones = tombstones_;
    for (DocId id : never_persisted) next_tombstones.erase(id);
    const uint64_t next_run_id = run_id_seq_ + (wrote_run ? 1 : 0);
    const std::string tb_file =
        "tombstones-" + std::to_string(last_wal_seq_) + "-" +
        std::to_string(next_run_id) + ".bin";
    {
      ScopedStageTimer timer(
          capture ? &diagnostics->flush_tombstone_write_ms : nullptr,
          capture ? &diagnostics->flush_tombstone_write_cpu_ms : nullptr);
      s = AtomicWrite((fs::path(dir_) / tb_file).string(),
                      EncodeTombstones(next_tombstones));
      if (!s.ok) return s;
      bytes_written_ += fs::file_size((fs::path(dir_) / tb_file).string());
    }

    const uint64_t added_segment_bytes = wrote_run ? new_run.info.file_size : 0;
    std::vector<Run> next_runs = runs_;
    if (wrote_run) next_runs.push_back(std::move(new_run));
    std::sort(next_runs.begin(), next_runs.end(), [](const Run& a, const Run& b) {
      return a.segment->first_doc_id() < b.segment->first_doc_id();
    });
    Manifest next_manifest = manifest_;
    next_manifest.next_doc_id = next_doc_id_;
    next_manifest.wal_seq = last_wal_seq_;
    next_manifest.run_id_seq = next_run_id;
    next_manifest.stats_epoch = stats_epoch_;
    next_manifest.stats_log_end = stats_log_end_;
    next_manifest.tombstone_file = tb_file;
    next_manifest.runs.clear();
    for (const auto& r : next_runs) next_manifest.runs.push_back(r.info);
    if (wrote_run) {
      // Readers remain on the old in-memory state while metadata is staged.
      // This deterministic barrier exercises that unlocked interval.
      const char* barrier_dir = std::getenv("M4_TEST_FLUSH_PUBLICATION_BARRIER");
      if (barrier_dir && *barrier_dir) {
        const fs::path barrier(barrier_dir);
        std::error_code ec;
        fs::create_directories(barrier, ec);
        if (ec) return Status::Error("create test flush publication barrier: " + ec.message());
        {
          std::ofstream ready(barrier / "segment-ready", std::ios::binary);
          if (!ready) return Status::Error("write test flush publication barrier");
          ready << "ready\\n";
        }
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::seconds(5);
        while (!fs::exists(barrier / "reader-finished", ec) &&
               std::chrono::steady_clock::now() < deadline) {
          if (ec) return Status::Error("inspect test flush publication barrier: " + ec.message());
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!fs::exists(barrier / "reader-finished", ec))
          return Status::Error("test flush publication barrier timed out");
      }
    }
    if (fault && std::strcmp(fault, "flush_before_manifest") == 0) ::_exit(92);
    {
      ScopedStageTimer timer(
          capture ? &diagnostics->flush_manifest_write_ms : nullptr,
          capture ? &diagnostics->flush_manifest_write_cpu_ms : nullptr);
      s = WriteManifest(dir_, next_manifest);
      if (!s.ok) return s;
      bytes_written_ += EncodeManifest(next_manifest).size();
    }
    if (fault && std::strcmp(fault, "flush_after_manifest") == 0) ::_exit(94);

    if (!query_guard.owns_lock()) {
      const double wait_wall = capture ? NowMs() : 0.0;
      const double wait_cpu = capture ? ThreadCpuMs() : 0.0;
      query_guard.lock();
      if (capture) {
        diagnostics->flush_query_mutex_wait_ms += NowMs() - wait_wall;
        diagnostics->flush_query_mutex_wait_cpu_ms +=
            ThreadCpuMs() - wait_cpu;
      }
    }
    flush_query_hold_timer.Start();
    {
      if (wrote_run) {
        runs_.swap(next_runs);
        run_id_seq_ = next_run_id;
        segment_bytes_written_ += added_segment_bytes;
        bytes_written_ += added_segment_bytes;
      }
      tombstones_.swap(next_tombstones);
      using std::swap;
      swap(manifest_, next_manifest);
      durable_wal_seq_ = manifest_.wal_seq;
      ++state_generation_;
    }

    // Retire large delta allocations outside the reader lock. State remains
    // writer-serialized until after WAL reset, so no post-snapshot write can
    // be lost from the committed prefix.
    {
      std::vector<Run> retired_runs;
      std::unordered_set<DocId> retired_tombstones;
      Manifest retired_manifest;
      std::vector<DeltaDoc> retired_docs;
      std::unordered_map<std::string, std::vector<BuildPosting>> retired_postings;
      std::unordered_map<std::string,
                         std::unordered_map<std::string, std::vector<DocId>>>
          retired_invert;
      std::vector<StatsEvent> retired_events;
      {
        ScopedStageTimer timer(
            capture ? &diagnostics->flush_delta_reset_ms : nullptr,
            capture ? &diagnostics->flush_delta_reset_cpu_ms : nullptr);
        retired_docs.swap(delta_docs_);
        retired_postings.swap(delta_postings_);
        retired_invert.swap(delta_invert_);
        retired_events.swap(pending_events_);
        delta_bytes_ = 0;
        pending_events_appended_count_ = 0;
        pending_flush_ = false;
      }
      flush_query_hold_timer.Stop();
      query_guard.unlock();
      retired_runs.swap(next_runs);
      retired_tombstones.swap(next_tombstones);
      using std::swap;
      swap(retired_manifest, next_manifest);
      if (options_.wal_enabled) {
        ScopedStageTimer timer(
            capture ? &diagnostics->flush_wal_reset_ms : nullptr,
            capture ? &diagnostics->flush_wal_reset_cpu_ms : nullptr);
        s = wal_.Reset();
        if (!s.ok) {
          query_guard.lock();
          return s;
        }
      }
      if (fault && std::strcmp(fault, "flush_after_wal_reset") == 0) ::_exit(95);
      // No active query can retain a run omitted by the just-published state;
      // new readers see the new run set before obsolete files are collected.
      {
        ScopedStageTimer timer(capture ? &diagnostics->flush_gc_ms : nullptr,
                               capture ? &diagnostics->flush_gc_cpu_ms : nullptr);
        GarbageCollect();
      }
    }
    query_guard.lock();

    if (options_.rebuild_on_flush) {
      std::vector<size_t> all;
      for (size_t i = 0; i < runs_.size(); ++i) all.push_back(i);
      if (all.size() > 1) {
        std::unique_lock<std::mutex> compaction_probe(
            compaction_build_mutex_, std::try_to_lock);
        if (compaction_probe.owns_lock()) {
          compaction_probe.unlock();
          return MergeRuns(all, 0, true, query_guard);
        }
        // Defer this optional full rebuild while the snapshot prototype owns
        // the build slot; do not turn an already-committed flush into an error.
      }
    }
    return MaybeCompact(query_guard);
  }

  Status AppendStatsEvents() {
    if (pending_events_appended_count_ > pending_events_.size()) {
      return Status::Error("stats event append cursor exceeds pending events");
    }
    if (pending_events_appended_count_ == pending_events_.size()) {
      if (pending_events_appended_count_ != 0 &&
          stats_log_end_ >= options_.stats_log_threshold) {
        return CheckpointStats();
      }
      return Status::OK();
    }
    const std::string log_path =
        (fs::path(dir_) / StatsLogName(stats_epoch_)).string();
    const int fd = ::open(log_path.c_str(), O_RDWR | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return Status::Error("open stats log");
    struct stat before {};
    if (::fstat(fd, &before) != 0) {
      ::close(fd);
      return Status::Error("stat stats log before append");
    }
    if (before.st_size < 0 ||
        static_cast<uint64_t>(before.st_size) < stats_log_end_) {
      ::close(fd);
      return Status::Error("stats log shorter than committed offset");
    }
    if (static_cast<uint64_t>(before.st_size) != stats_log_end_) {
      if (::ftruncate(fd, static_cast<off_t>(stats_log_end_)) != 0 ||
          ::fsync(fd) != 0) {
        ::close(fd);
        return Status::Error("repair uncommitted stats log suffix");
      }
      before.st_size = static_cast<off_t>(stats_log_end_);
    }
    std::vector<StatsEvent> coalesced_events;
    bool use_coalesced_events = false;
    size_t append_begin = pending_events_appended_count_;
    if (options_.coalesce_stats_events) {
      std::map<std::pair<StatsEvent::Kind, std::string>, int64_t> deltas;
      bool overflow = false;
      for (size_t i = append_begin; i < pending_events_.size(); ++i) {
        const auto& event = pending_events_[i];
        auto& delta = deltas[{event.kind, event.term}];
        if ((event.delta > 0 &&
             delta > std::numeric_limits<int64_t>::max() - event.delta) ||
            (event.delta < 0 &&
             delta < std::numeric_limits<int64_t>::min() - event.delta)) {
          overflow = true;
          break;
        }
        delta += event.delta;
      }
      if (!overflow) {
        coalesced_events.reserve(deltas.size());
        for (const auto& [key, delta] : deltas) {
          if (delta == 0) continue;
          coalesced_events.push_back({key.first, key.second, delta});
        }
        use_coalesced_events = true;
      }
    }

    const std::vector<StatsEvent>* events = &pending_events_;
    if (use_coalesced_events) {
      if (coalesced_events.empty()) {
        pending_events_appended_count_ = pending_events_.size();
        if (stats_log_end_ >= options_.stats_log_threshold) {
          return CheckpointStats();
        }
        return Status::OK();
      }
      events = &coalesced_events;
      append_begin = 0;
    }
    BufWriter w;
    for (size_t i = append_begin; i < events->size(); ++i) {
      const auto& e = (*events)[i];
      BufWriter payload;
      EncodeStatsEvent(&payload, e);
      w.U32(0x4D345354);
      w.U32(static_cast<uint32_t>(payload.size()));
      w.Bytes(payload.data().data(), payload.size());
      w.U32(static_cast<uint32_t>(
          Fnv1a(payload.data().data(), payload.size()) & 0xffffffffu));
    }
    size_t done = 0;
    while (done < w.size()) {
      const ssize_t n =
          ::write(fd, w.data().data() + done, w.size() - done);
      if (n < 0) {
        if (errno == EINTR) continue;
        const int saved_errno = errno;
        const bool rollback_ok =
            ::ftruncate(fd, before.st_size) == 0 && ::fsync(fd) == 0;
        ::close(fd);
        return Status::Error("stats log write failed: " +
                             std::string(std::strerror(saved_errno)) +
                             (rollback_ok ? "" : "; rollback failed"));
      }
      if (n == 0) {
        const bool rollback_ok =
            ::ftruncate(fd, before.st_size) == 0 && ::fsync(fd) == 0;
        ::close(fd);
        return Status::Error(std::string("stats log write made no progress") +
                             (rollback_ok ? "" : "; rollback failed"));
      }
      done += static_cast<size_t>(n);
    }
    if (::fsync(fd) != 0) {
      const int saved_errno = errno;
      const bool rollback_ok =
          ::ftruncate(fd, before.st_size) == 0 && ::fsync(fd) == 0;
      ::close(fd);
      return Status::Error("stats log fsync failed: " +
                           std::string(std::strerror(saved_errno)) +
                           (rollback_ok ? "" : "; rollback failed"));
    }
    ::close(fd);
    ++fsyncs_;
    bytes_written_ += w.size();
    stats_log_end_ += w.size();
    pending_events_appended_count_ = pending_events_.size();

    if (stats_log_end_ >= options_.stats_log_threshold) {
      return CheckpointStats();
    }
    return Status::OK();
  }

  Status CheckpointStats() {
    const uint64_t new_epoch = stats_epoch_ + 1;
    const std::string ckpt =
        (fs::path(dir_) / StatsCkptName(new_epoch)).string();
    const std::string log = (fs::path(dir_) / StatsLogName(new_epoch)).string();
    Status s = AtomicWrite(ckpt, EncodeStatsCkpt(stats_));
    if (!s.ok) return s;
    s = AtomicWrite(log, "");
    if (!s.ok) return s;
    stats_epoch_ = new_epoch;
    stats_log_end_ = 0;
    bytes_written_ += fs::file_size(ckpt);
    return Status::OK();
  }

  bool ReplayStatsLog(const std::string& path, uint64_t end,
                      StatsState* stats) const {
    std::ifstream in(path, std::ios::binary);
    if (!in) return end == 0;
    std::string bytes((std::istreambuf_iterator<char>(in)),
                      std::istreambuf_iterator<char>());
    if (end > bytes.size()) return false;
    size_t off = 0;
    while (off + 12 <= end) {
      BufReader r(bytes.data() + off, end - off);
      if (r.U32() != 0x4D345354) return false;
      const uint32_t len = r.U32();
      if (len > end - off - 12) return false;
      const char* payload = bytes.data() + off + 8;
      BufReader cr(bytes.data() + off + 8 + len, 4);
      const uint32_t want = cr.U32();
      if ((Fnv1a(payload, len) & 0xffffffffu) != want) return false;
      BufReader pr(payload, len);
      StatsEvent e;
      if (!DecodeStatsEvent(&pr, &e)) return false;
      ApplyStatsEvent(stats, e);
      off += 12 + len;
    }
    return off == end;
  }

  void RebuildStats(StatsState* stats) const {
    for (const auto& run : runs_) {
      for (const auto& term : run.segment->terms()) {
        SegmentTermIterator it(run.segment, &term, false);
        while (it.valid()) {
          if (tombstones_.count(it.doc()) == 0) ++stats->df[term.term];
          it.Next();
        }
      }
      for (const auto& doc : run.segment->docs()) {
        if (tombstones_.count(doc.id) == 0) {
          ++stats->live_docs;
          stats->live_tokens += doc.dl;
        }
      }
    }
  }

  // ---------------------------- compaction -------------------------------

  bool AnyLevelOverflow() const {
    std::map<uint64_t, uint32_t> counts;
    for (const auto& r : runs_) ++counts[r.info.level];
    for (const auto& [level, count] : counts) {
      (void)level;
      if (count > options_.max_runs_per_level) return true;
    }
    return false;
  }

  bool TombstonePressure() const {
    if (stats_.live_docs == 0) return false;
    const double ratio = static_cast<double>(tombstones_.size()) /
                         static_cast<double>(stats_.live_docs);
    if (ratio > options_.tombstone_compact_ratio) return true;
    for (const auto& r : runs_) {
      if (r.info.dead_count > 0 && r.info.doc_count > 0 &&
          static_cast<double>(r.info.dead_count) / r.info.doc_count >
              options_.tombstone_compact_ratio) {
        return true;
      }
    }
    return false;
  }

  Status MaybeDebtCompact(bool* merged,
                          std::unique_lock<detail::WriterPreferredSharedMutex>& query_guard) {
    *merged = false;
    if (!options_.debt_policy_enabled || options_.debt_max_merges_per_flush == 0) {
      return Status::OK();
    }
    if (runs_.size() < options_.debt_min_runs || stats_.live_docs == 0) {
      return Status::OK();
    }
    const double now = NowMs();
    if (now - last_debt_compact_ms_ < options_.debt_cooldown_ms) {
      return Status::OK();
    }
    const double global_ratio =
        static_cast<double>(tombstones_.size()) /
        static_cast<double>(stats_.live_docs);
    bool pressure = global_ratio >= options_.debt_compact_ratio;
    if (!pressure) {
      for (const auto& r : runs_) {
        if (r.info.doc_count == 0 || r.info.dead_count == 0) continue;
        if (static_cast<double>(r.info.dead_count) / r.info.doc_count >=
            options_.debt_compact_ratio) {
          pressure = true;
          break;
        }
      }
    }
    if (!pressure) return Status::OK();

    const uint64_t cap = options_.debt_max_merge_docs != 0
                             ? options_.debt_max_merge_docs
                             : std::max<uint64_t>(1, stats_.live_docs / 4);
    size_t best = 0;
    bool found = false;
    uint64_t best_cost = std::numeric_limits<uint64_t>::max();
    uint64_t best_dead = 0;
    for (size_t i = 0; i + 1 < runs_.size(); ++i) {
      const uint64_t cost = runs_[i].info.doc_count +
                            runs_[i + 1].info.doc_count;
      const uint64_t dead = runs_[i].info.dead_count +
                            runs_[i + 1].info.dead_count;
      if (dead == 0 || cost == 0 || cost > cap) continue;
      // Prefer the pair that removes the most dead postings per rewritten
      // doc; break ties by smaller rewrite cost.
      const double score = static_cast<double>(dead) / static_cast<double>(cost);
      const double best_score = found && best_cost != 0
                                    ? static_cast<double>(best_dead) /
                                          static_cast<double>(best_cost)
                                    : -1.0;
      if (!found || score > best_score + 1e-12 ||
          (std::abs(score - best_score) <= 1e-12 && cost < best_cost)) {
        found = true;
        best = i;
        best_cost = cost;
        best_dead = dead;
      }
    }
    if (!found) return Status::OK();
    Status s = MergeRuns({best, best + 1}, /*target_level=*/0,
                         /*force=*/false, query_guard);
    if (!s.ok) return s;
    last_debt_compact_ms_ = NowMs();
    *merged = true;
    return Status::OK();
  }

  Status MaybeCompact(std::unique_lock<detail::WriterPreferredSharedMutex>& query_guard) {
    std::unique_lock<std::mutex> compaction_probe(compaction_build_mutex_,
                                                  std::try_to_lock);
    if (!compaction_probe.owns_lock()) return Status::OK();
    compaction_probe.unlock();
    bool debt_merged = false;
    Status debt_status = MaybeDebtCompact(&debt_merged, query_guard);
    if (!debt_status.ok) return debt_status;
    for (int iter = 0; iter < 256; ++iter) {
      const bool over_cap = runs_.size() > options_.max_total_runs;
      const bool level_overflow = AnyLevelOverflow();
      const bool tombstone = TombstonePressure();
      if (!over_cap && !level_overflow && !tombstone) break;

      std::vector<size_t> group;
      if (over_cap) {
        // Merge the two smallest adjacent runs. Picking the oldest pair would
        // pair the huge base run with a one-doc delta and reintroduce the
        // O(collection) cost the architecture removes.
        uint64_t best_cost = std::numeric_limits<uint64_t>::max();
        size_t best = 0;
        for (size_t i = 0; i + 1 < runs_.size(); ++i) {
          const uint64_t cost = runs_[i].info.doc_count +
                                runs_[i + 1].info.doc_count;
          if (cost < best_cost) {
            best_cost = cost;
            best = i;
          }
        }
        group = {best, best + 1};
      } else if (level_overflow) {
        // Pick the level that is over its run budget and merge the smallest
        // adjacent pair that includes a run at that level. Merging a fixed
        // R+1 window can accidentally include the huge base when the whole
        // collection has few runs; a pair is enough to restore the budget and
        // the loop repeats while the level stays overfull.
        std::map<uint64_t, uint32_t> counts;
        for (const auto& r : runs_) ++counts[r.info.level];
        uint64_t over_level = 0;
        uint32_t over_count = 0;
        for (const auto& [level, count] : counts) {
          if (count > options_.max_runs_per_level && count > over_count) {
            over_count = count;
            over_level = level;
          }
        }
        uint64_t best_cost = std::numeric_limits<uint64_t>::max();
        size_t best = 0;
        bool found = false;
        for (size_t i = 0; i + 1 < runs_.size(); ++i) {
          if (runs_[i].info.level != over_level &&
              runs_[i + 1].info.level != over_level) {
            continue;
          }
          const uint64_t cost = runs_[i].info.doc_count +
                                runs_[i + 1].info.doc_count;
          if (!found || cost < best_cost) {
            found = true;
            best_cost = cost;
            best = i;
          }
        }
        if (!found) {
          // Degenerate layout: merge the smallest adjacent pair.
          for (size_t i = 0; i + 1 < runs_.size(); ++i) {
            const uint64_t cost = runs_[i].info.doc_count +
                                  runs_[i + 1].info.doc_count;
            if (!found || cost < best_cost) {
              found = true;
              best_cost = cost;
              best = i;
            }
          }
        }
        group = {best, best + 1};
      } else {
        // Tombstone pressure: rewrite the run with the highest dead ratio,
        // together with its smaller neighbour when one exists.
        size_t best = 0;
        double best_ratio = -1.0;
        for (size_t i = 0; i < runs_.size(); ++i) {
          const auto& r = runs_[i];
          const double ratio =
              r.info.doc_count == 0
                  ? 0.0
                  : static_cast<double>(r.info.dead_count) / r.info.doc_count;
          if (ratio > best_ratio) {
            best_ratio = ratio;
            best = i;
          }
        }
        group.push_back(best);
        if (runs_.size() > 1) {
          if (best == 0) {
            group.push_back(1);
          } else if (best + 1 == runs_.size()) {
            group.insert(group.begin(), best - 1);
          } else {
            const uint64_t left = runs_[best - 1].info.doc_count;
            const uint64_t right = runs_[best + 1].info.doc_count;
            group.push_back(left <= right ? best - 1 : best + 1);
          }
        }
      }
      if (group.size() == 1) {
        const auto& r = runs_[group[0]];
        if (r.info.dead_count == 0) break;
      }
      Status s = MergeRuns(group, /*target_level=*/0, /*force=*/false,
                           query_guard);
      if (!s.ok) return s;
      if (runs_.empty()) break;
    }
    return Status::OK();
  }

  Status MergeRuns(
      const std::vector<size_t>& indices, uint64_t target_level, bool force,
      std::unique_lock<detail::WriterPreferredSharedMutex>& query_guard,
      std::unique_lock<std::recursive_mutex>* state_guard = nullptr,
      CompactionDiagnostics* diagnostics = nullptr,
      bool allow_concurrent_rebase = false,
      ScopedStageTimer* state_hold_timer = nullptr) {
    const bool capture = diagnostics && diagnostics->capture_timing;
    const bool durable_rebase = allow_concurrent_rebase && options_.wal_enabled;
    const double merge_start_ms = NowMs();
    ScopedStageTimer prepare_timer(
        capture ? &diagnostics->merge_prepare_ms : nullptr,
        capture ? &diagnostics->merge_prepare_cpu_ms : nullptr);
    if (indices.empty()) return Status::OK();
    std::unique_lock<std::mutex> compaction_build_guard(
        compaction_build_mutex_, std::try_to_lock);
    if (!compaction_build_guard.owns_lock()) {
      return Status::Error("another compaction build is active");
    }
    std::vector<size_t> idx = indices;
    std::sort(idx.begin(), idx.end());
    idx.erase(std::unique(idx.begin(), idx.end()), idx.end());
    if (idx.empty()) return Status::OK();

    if (state_guard && !state_guard->owns_lock()) {
      return Status::Error("snapshot compaction requires the writer state lock");
    }
    if (state_guard && !query_guard.owns_lock()) {
      return Status::Error("snapshot compaction requires exclusive query admission");
    }
    if (allow_concurrent_rebase && !state_guard) {
      return Status::Error("snapshot rebase requires the writer state lock");
    }
    const uint64_t snapshot_state_generation = state_generation_;
    const uint64_t snapshot_wal_seq = last_wal_seq_;
    std::vector<Run> snapshot_runs;

    std::vector<Run*> group;
    uint64_t max_level = 0;
    for (size_t i : idx) {
      if (i >= runs_.size()) return Status::Error("bad merge index");
      group.push_back(&runs_[i]);
      max_level = std::max(max_level, runs_[i].info.level);
    }
    const DocId group_first = group.front()->segment->first_doc_id();
    const DocId group_last = group.back()->segment->last_doc_id();
    const uint64_t new_level = force ? target_level : max_level + 1;
    uint64_t input_docs = 0;
    std::vector<SegmentPtr> input_segments;
    std::vector<uint64_t> input_run_ids;
    std::vector<std::string> input_run_files;
    input_segments.reserve(group.size());
    input_run_ids.reserve(group.size());
    input_run_files.reserve(group.size());
    for (Run* run : group) {
      input_docs += run->info.doc_count;
      input_segments.push_back(run->segment);
      input_run_ids.push_back(run->info.id);
      input_run_files.push_back(run->info.file);
    }

    std::unordered_set<DocId> snapshot_tombstones;
    const std::unordered_set<DocId>* merge_tombstones = &tombstones_;
    uint64_t external_memory_charge_bytes = 0;
    if (state_guard) {
      const uint64_t limit = options_.compaction_memory_limit_bytes;
      const uint64_t fixed_charge = kStreamMergeFixedIoBufferChargeBytes;
      if (limit < fixed_charge ||
          runs_.size() > (limit - fixed_charge) / sizeof(Run)) {
        return Status::Error(
            "compaction memory budget exceeded by snapshot state");
      }
      uint64_t run_snapshot_charge = sizeof(Run) * runs_.size();
      for (const Run& run : runs_) {
        const uint64_t file_charge = run.info.file.capacity() + 32;
        if (file_charge > limit - fixed_charge - run_snapshot_charge) {
          return Status::Error(
              "compaction memory budget exceeded by snapshot state");
        }
        run_snapshot_charge += file_charge;
      }
      // Snapshot only tombstones that may refer to the selected runs. Walking
      // every document twice here made snapshot admission O(input documents)
      // while both writer-state and query-admission locks were held. Run ID
      // ranges are ordered and non-overlapping; a tombstone in a range gap is
      // harmless because the merge can only match IDs present in its inputs.
      std::vector<std::pair<DocId, DocId>> input_ranges;
      input_ranges.reserve(input_segments.size());
      for (const SegmentPtr& segment : input_segments) {
        if (!segment->docs().empty()) {
          input_ranges.emplace_back(segment->first_doc_id(),
                                    segment->last_doc_id());
        }
      }
      std::sort(input_ranges.begin(), input_ranges.end());
      auto belongs_to_input = [&input_ranges](DocId id) {
        const auto it = std::upper_bound(
            input_ranges.begin(), input_ranges.end(), id,
            [](DocId value, const std::pair<DocId, DocId>& range) {
              return value < range.first;
            });
        return it != input_ranges.begin() && id <= std::prev(it)->second;
      };
      std::unordered_set<DocId> pending_deleted_at_snapshot;
      if (durable_rebase) {
        Status pending_status =
            CollectUncommittedDeletedIds(&pending_deleted_at_snapshot);
        if (!pending_status.ok) return pending_status;
      }
      uint64_t tombstone_count = 0;
      for (DocId id : tombstones_) {
        if (pending_deleted_at_snapshot.count(id) == 0 &&
            belongs_to_input(id)) {
          ++tombstone_count;
        }
      }
      const uint64_t set_charge = sizeof(snapshot_tombstones);
      if (limit - fixed_charge - run_snapshot_charge < set_charge) {
        return Status::Error(
            "compaction memory budget exceeded by snapshot state");
      }
      const uint64_t remaining_charge =
          limit - fixed_charge - run_snapshot_charge - set_charge;
      if (tombstone_count > remaining_charge / 128 ||
          pending_deleted_at_snapshot.size() >
              (remaining_charge - tombstone_count * 128) / 128) {
        return Status::Error(
            "compaction memory budget exceeded by snapshot state");
      }
      external_memory_charge_bytes =
          run_snapshot_charge + set_charge + tombstone_count * 128;
      snapshot_runs = runs_;
      snapshot_tombstones.reserve(static_cast<size_t>(tombstone_count));
      for (DocId id : tombstones_) {
        if (pending_deleted_at_snapshot.count(id) == 0 &&
            belongs_to_input(id)) {
          snapshot_tombstones.insert(id);
        }
      }
      std::unordered_set<DocId>().swap(pending_deleted_at_snapshot);
      merge_tombstones = &snapshot_tombstones;
    }

    const uint64_t previous_run_id_seq = run_id_seq_;
    const uint64_t new_run_id = run_id_seq_ + 1;
    if (state_guard) run_id_seq_ = new_run_id;  // reserve while the state lock is held
    const uint64_t snapshot_run_id_seq = run_id_seq_;
    const std::string file = RunFileName(new_run_id);
    const std::string temp_file =
        file + ".tmp." + std::to_string(::getpid());
    struct ActiveCompactionFilesGuard {
      std::string& output;
      std::string& temporary;
      std::unique_lock<std::recursive_mutex>* state_guard;
      ScopedStageTimer* state_hold_timer;
      ~ActiveCompactionFilesGuard() {
        if (state_guard && !state_guard->owns_lock()) {
          state_guard->lock();
          if (state_hold_timer) state_hold_timer->Start();
        }
        output.clear();
        temporary.clear();
      }
    } active_files_guard{active_compaction_file_, active_compaction_temp_file_,
                         state_guard, state_hold_timer};
    active_compaction_file_ = file;
    active_compaction_temp_file_ = temp_file;
    StreamMergeOptions merge_options;
    merge_options.run_id = new_run_id;
    merge_options.level = new_level;
    merge_options.block_size = options_.block_size;
    merge_options.fts_field = options_.fts_field;
    merge_options.memory_limit_bytes = options_.compaction_memory_limit_bytes;
    merge_options.external_memory_charge_bytes = external_memory_charge_bytes;
    StreamMergeStats merge_stats;
    std::string err;
    std::string build_error;
    Run new_run;
    uint64_t new_doc_count = 0;

    if (!query_guard.owns_lock()) {
      return Status::Error("compaction requires exclusive query admission");
    }
    prepare_timer.Stop();
    query_guard.unlock();
    if (state_guard) {
      if (state_hold_timer) state_hold_timer->Stop();
      state_guard->unlock();
    }
    ScopedStageTimer build_timer(
        capture ? &diagnostics->merge_build_ms : nullptr,
        capture ? &diagnostics->merge_build_cpu_ms : nullptr);
    Status s = MergeSegmentsStreaming((fs::path(dir_) / file).string(),
                                      input_segments, *merge_tombstones,
                                      merge_options, &merge_stats, &err);
    if (!s.ok) {
      build_error = "compaction stream: " +
                    (err.empty() ? s.message : err);
    }
    if (s.ok) {
      new_doc_count = merge_stats.output_docs;
      if (new_doc_count > 0) {
        new_run.info.id = new_run_id;
        new_run.info.level = new_level;
        new_run.info.doc_count = new_doc_count;
        new_run.info.dead_count = 0;
        new_run.info.file = file;
        std::error_code ec;
        new_run.info.file_size = fs::file_size(fs::path(dir_) / file, ec);
        s = Segment::Open((fs::path(dir_) / file).string(), &new_run.segment,
                          &err);
        if (!s.ok) build_error = "compaction reopen: " + err;
      }
    }
    if (s.ok) {
      // Deterministic test barrier proves reads can finish while the durable
      // replacement run exists locally but is not yet query-visible.
      const char* barrier_dir =
          std::getenv("M4_TEST_COMPACTION_BUILD_BARRIER");
      if (barrier_dir && *barrier_dir) {
        const fs::path barrier(barrier_dir);
        std::error_code ec;
        fs::create_directories(barrier, ec);
        if (ec) {
          build_error = "create test compaction barrier: " + ec.message();
          s = Status::Error(build_error);
        }
        if (s.ok) {
          std::ofstream ready(barrier / "run-ready", std::ios::binary);
          if (!ready) {
            build_error = "write test compaction barrier marker";
            s = Status::Error(build_error);
          } else {
            ready << "ready\n";
          }
        }
        if (s.ok) {
          const auto deadline = std::chrono::steady_clock::now() +
                                std::chrono::seconds(5);
          while (!fs::exists(barrier / "reader-finished", ec) &&
                 std::chrono::steady_clock::now() < deadline) {
            if (ec) {
              build_error = "inspect test compaction barrier: " + ec.message();
              s = Status::Error(build_error);
              break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
          }
          if (s.ok && !fs::exists(barrier / "reader-finished", ec)) {
            build_error = "test compaction barrier timed out";
            s = Status::Error(build_error);
          }
        }
      }
    }
    build_timer.Stop();
    if (state_guard) {
      const double wait_wall = capture ? NowMs() : 0.0;
      const double wait_cpu = capture ? ThreadCpuMs() : 0.0;
      state_guard->lock();
      if (capture) {
        diagnostics->publish_state_mutex_wait_ms = NowMs() - wait_wall;
        diagnostics->publish_state_mutex_wait_cpu_ms =
            ThreadCpuMs() - wait_cpu;
      }
      if (state_hold_timer) state_hold_timer->Start();
    }
    const double query_wait_wall = capture ? NowMs() : 0.0;
    const double query_wait_cpu = capture ? ThreadCpuMs() : 0.0;
    query_guard.lock();
    if (capture) {
      diagnostics->publish_query_mutex_wait_ms = NowMs() - query_wait_wall;
      diagnostics->publish_query_mutex_wait_cpu_ms =
          ThreadCpuMs() - query_wait_cpu;
    }
    ScopedStageTimer publication_timer(
        capture ? &diagnostics->publication_ms : nullptr,
        capture ? &diagnostics->publication_cpu_ms : nullptr);
    std::unordered_set<DocId> pending_deleted_at_publication;
    if (s.ok && durable_rebase) {
      Status pending_status =
          CollectUncommittedDeletedIds(&pending_deleted_at_publication);
      if (!pending_status.ok) {
        build_error = "snapshot rebase WAL scan: " + pending_status.message;
        s = pending_status;
      }
    } else if (s.ok && allow_concurrent_rebase && pending_flush_) {
      ScopedStageTimer rebase_flush_timer(
          capture ? &diagnostics->pending_flush_ms : nullptr,
          capture ? &diagnostics->pending_flush_cpu_ms : nullptr);
      Status flush_status = FlushInternal(query_guard, diagnostics);
      if (!flush_status.ok) {
        build_error = "snapshot rebase flush: " + flush_status.message;
        s = flush_status;
      }
    }
    if (!s.ok) {
      // No manifest was published; the old runs and tombstones stay active.
      new_run.segment.reset();
      std::error_code cleanup_ec;
      fs::remove(fs::path(dir_) / file, cleanup_ec);
      if (state_guard && run_id_seq_ == new_run_id)
        run_id_seq_ = previous_run_id_seq;
      return Status::Error(build_error.empty() ? s.message : build_error);
    }
    compaction_peak_bytes_ =
        std::max(compaction_peak_bytes_, merge_stats.tracked_peak_bytes);
    if (state_guard) {
      bool snapshot_matches = true;
      if (allow_concurrent_rebase) {
        for (size_t i = 0; snapshot_matches && i < input_run_ids.size(); ++i) {
          const auto current = std::find_if(
              runs_.begin(), runs_.end(), [&](const Run& run) {
                return run.info.id == input_run_ids[i] &&
                       run.info.file == input_run_files[i] &&
                       run.segment.get() == input_segments[i].get();
              });
          snapshot_matches = current != runs_.end();
        }
      } else {
        snapshot_matches =
            state_generation_ == snapshot_state_generation &&
            last_wal_seq_ == snapshot_wal_seq &&
            run_id_seq_ == snapshot_run_id_seq &&
            runs_.size() == snapshot_runs.size();
        for (size_t i = 0; snapshot_matches && i < runs_.size(); ++i) {
          snapshot_matches = runs_[i].info.id == snapshot_runs[i].info.id &&
                             runs_[i].info.file == snapshot_runs[i].info.file &&
                             runs_[i].segment.get() ==
                                 snapshot_runs[i].segment.get();
        }
      }
      if (!snapshot_matches) {
        if (new_doc_count > 0) {
          segment_bytes_written_ += new_run.info.file_size;
          bytes_written_ += new_run.info.file_size +
                            merge_stats.temporary_bytes_written;
        }
        new_run.segment.reset();
        std::error_code cleanup_ec;
        fs::remove(fs::path(dir_) / file, cleanup_ec);
        if (run_id_seq_ == new_run_id) run_id_seq_ = previous_run_id_seq;
        return Status::Error(
            "compaction snapshot superseded by concurrent state change");
      }
    }
    if (new_doc_count > 0) {
      if (!state_guard) ++run_id_seq_;
      segment_bytes_written_ += new_run.info.file_size;
      bytes_written_ += new_run.info.file_size +
                        merge_stats.temporary_bytes_written;
    } else if (state_guard && !allow_concurrent_rebase) {
      run_id_seq_ = previous_run_id_seq;
    }

    if (allow_concurrent_rebase && new_doc_count > 0) {
      uint64_t current_dead_count = 0;
      for (const DocMeta& doc : new_run.segment->docs()) {
        if (tombstones_.count(doc.id) != 0) ++current_dead_count;
      }
      new_run.info.dead_count = current_dead_count;
    }

    const char* fault = std::getenv("M4_FAULT");
    if (fault && std::string(fault) == "before_manifest") {
      return Status::Error("injected fault before manifest");
    }

    const std::unordered_set<uint64_t> selected_run_ids(input_run_ids.begin(),
                                                        input_run_ids.end());
    std::unordered_map<uint64_t, uint64_t> pending_deleted_by_run;
    if (durable_rebase) {
      for (DocId id : pending_deleted_at_publication) {
        if (new_doc_count > 0 && new_run.segment->FindDoc(id)) {
          ++pending_deleted_by_run[new_run_id];
          continue;
        }
        for (const Run& run : runs_) {
          if (selected_run_ids.count(run.info.id) != 0) continue;
          if (run.segment->FindDoc(id)) {
            ++pending_deleted_by_run[run.info.id];
            break;
          }
        }
      }
      for (const auto& pending_entry : pending_deleted_by_run) {
        const uint64_t run_id = pending_entry.first;
        const uint64_t pending_count = pending_entry.second;
        uint64_t dead_count = 0;
        if (run_id == new_run_id) {
          dead_count = new_run.info.dead_count;
        } else {
          const auto run = std::find_if(runs_.begin(), runs_.end(),
                                        [run_id](const Run& candidate) {
            return candidate.info.id == run_id;
          });
          if (run != runs_.end()) dead_count = run->info.dead_count;
        }
        if (dead_count < pending_count) {
          new_run.segment.reset();
          std::error_code cleanup_ec;
          fs::remove(fs::path(dir_) / file, cleanup_ec);
          if (run_id_seq_ == new_run_id) run_id_seq_ = previous_run_id_seq;
          return Status::Error(
              "snapshot rebase pending-delete count exceeds run dead count");
        }
      }
    }
    std::vector<std::string> removed_files;
    for (const auto& run : runs_) {
      if (selected_run_ids.count(run.info.id) != 0) {
        removed_files.push_back(run.info.file);
      }
    }

    // Replace exactly the captured inputs. Rebase mode may have concurrent
    // writer-created runs after those inputs while the merge was unlocked.
    std::vector<Run> next;
    next.reserve(runs_.size() + 1);
    for (auto& run : runs_) {
      if (selected_run_ids.count(run.info.id) != 0) continue;
      next.push_back(std::move(run));
    }
    if (!new_run.info.file.empty()) next.push_back(std::move(new_run));
    std::sort(next.begin(), next.end(), [](const Run& a, const Run& b) {
      return a.segment->first_doc_id() < b.segment->first_doc_id();
    });
    runs_ = std::move(next);
    ++state_generation_;

    // Keep post-snapshot tombstones for docs in the compacted range: they
    // describe writes that occurred after the merge input was captured.
    const uint64_t tombstone_seq =
        durable_rebase ? durable_wal_seq_ : last_wal_seq_;
    std::string tb_file = "tombstones-" + std::to_string(tombstone_seq) + "-" +
                          std::to_string(run_id_seq_);
    if (allow_concurrent_rebase) {
      // Keep the prior manifest's tombstone file intact until this replacement
      // manifest is atomically published.
      tb_file += "-rebase-" + std::to_string(new_run_id);
    }
    tb_file += ".bin";
    std::string encoded_tombstones;
    if (durable_rebase) {
      std::vector<DocId> committed_tombstones;
      committed_tombstones.reserve(tombstones_.size());
      for (DocId id : tombstones_) {
        if (snapshot_tombstones.count(id) == 0 &&
            pending_deleted_at_publication.count(id) == 0) {
          committed_tombstones.push_back(id);
        }
      }
      encoded_tombstones =
          EncodeSortedTombstones(std::move(committed_tombstones));
    } else if (allow_concurrent_rebase) {
      encoded_tombstones =
          EncodeTombstonesExcluding(tombstones_, snapshot_tombstones);
    } else {
      encoded_tombstones =
          EncodeTombstonesOutsideRange(tombstones_, group_first, group_last);
    }
    s = AtomicWrite((fs::path(dir_) / tb_file).string(), encoded_tombstones);
    if (!s.ok) return s;
    bytes_written_ += fs::file_size((fs::path(dir_) / tb_file).string());
    if (allow_concurrent_rebase && fault &&
        std::string(fault) == "after_compaction_tombstone") {
      return Status::Error("injected fault after compaction tombstone snapshot");
    }

    if (!durable_rebase) {
      manifest_.next_doc_id = next_doc_id_;
      manifest_.wal_seq = last_wal_seq_;
      manifest_.stats_epoch = stats_epoch_;
      manifest_.stats_log_end = stats_log_end_;
    } else {
      manifest_.wal_seq = durable_wal_seq_;
    }
    manifest_.run_id_seq = run_id_seq_;
    manifest_.tombstone_file = tb_file;
    manifest_.runs.clear();
    for (const auto& r : runs_) {
      RunInfo info = r.info;
      if (durable_rebase) {
        const auto pending = pending_deleted_by_run.find(info.id);
        if (pending != pending_deleted_by_run.end()) {
          info.dead_count -= pending->second;
        }
      }
      manifest_.runs.push_back(std::move(info));
    }
    s = WriteManifest(dir_, manifest_);
    if (!s.ok) return s;
    durable_wal_seq_ = manifest_.wal_seq;
    fault = std::getenv("M4_FAULT");
    if (fault && std::string(fault) == "after_manifest") {
      return Status::Error("injected fault after manifest");
    }

    if (allow_concurrent_rebase) {
      for (DocId id : snapshot_tombstones) tombstones_.erase(id);
    } else {
      for (auto it = tombstones_.begin(); it != tombstones_.end();) {
        if (*it >= group_first && *it <= group_last) {
          it = tombstones_.erase(it);
        } else {
          ++it;
        }
      }
    }
    ++compactions_;
    ++compaction_merges_;
    compaction_input_docs_ += input_docs;
    compaction_output_docs_ += new_doc_count;
    compaction_wall_ms_ += NowMs() - merge_start_ms;
    compacted_docs_ += new_doc_count;
    for (const auto& f : removed_files) {
      std::error_code ec;
      fs::remove(fs::path(dir_) / f, ec);
    }
    GarbageCollect();
    return Status::OK();
  }

  void GarbageCollect() {
    std::unordered_set<std::string> keep;
    auto add_manifest = [&](const Manifest& m) {
      for (const auto& r : m.runs) keep.insert(r.file);
      keep.insert(m.tombstone_file);
      keep.insert(StatsCkptName(m.stats_epoch));
      keep.insert(StatsLogName(m.stats_epoch));
    };
    add_manifest(manifest_);
    // Snapshot compaction releases mutex_ while a flush may run GC. Preserve
    // its in-flight temp and final output until MergeRuns commits or cleans it.
    if (!active_compaction_file_.empty()) {
      keep.insert(active_compaction_file_);
    }
    if (!active_compaction_temp_file_.empty()) {
      keep.insert(active_compaction_temp_file_);
    }
    // Keep one previous generation so manifest.bak remains a usable fallback.
    Manifest bak;
    if (ReadManifestFile(ManifestPath(dir_) + ".bak", &bak)) {
      add_manifest(bak);
    }
    keep.insert("manifest");
    keep.insert("manifest.bak");
    keep.insert("wal.log");
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator(dir_, ec)) {
      if (ec) break;
      const std::string name = entry.path().filename().string();
      if (keep.count(name) != 0) continue;
      if (name.rfind("run-", 0) == 0 || name.rfind("tombstones-", 0) == 0 ||
          name.rfind("stats-", 0) == 0 || name.rfind("manifest.tmp", 0) == 0 ||
          name.rfind("wal.log.tmp", 0) == 0) {
        fs::remove(entry.path(), ec);
      }
    }
  }

  // ------------------------------ readers --------------------------------

  uint64_t LiveDf(const std::string& term) const {
    const auto it = stats_.df.find(term);
    return it == stats_.df.end() ? 0 : it->second;
  }

  std::vector<DocId> LiveUniverse() const {
    std::vector<DocId> ids;
    ids.reserve(pk_to_doc_.size());
    for (const auto& [pk, id] : pk_to_doc_) {
      (void)pk;
      ids.push_back(id);
    }
    std::sort(ids.begin(), ids.end());
    return ids;
  }

  struct FilterLikeDiagnostics {
    uint64_t strings_scanned{0};
    uint64_t candidate_capacity_bytes{0};
  };

  std::vector<DocId> FieldPresent(const std::string& field) const {
    std::vector<DocId> ids;
    for (const auto& run : runs_) {
      const InvertFieldRef* ref = run.segment->FindField(field);
      if (!ref) continue;
      for (const auto& value : ref->values) {
        CollectValueDocsFromRun(run, *ref, value, &ids);
      }
    }
    const auto delta = delta_invert_.find(field);
    if (delta != delta_invert_.end()) {
      for (const auto& [value, docs] : delta->second) {
        (void)value;
        ids.insert(ids.end(), docs.begin(), docs.end());
      }
    }
    std::sort(ids.begin(), ids.end());
    ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    ids.erase(std::remove_if(ids.begin(), ids.end(), [&](DocId id) {
                return tombstones_.count(id) != 0;
              }),
              ids.end());
    return ids;
  }

  Status SearchStringPattern(const std::string& field,
                             const std::string& literal, bool prefix,
                             std::vector<DocId>* out,
                             FilterLikeDiagnostics* diagnostics) const {
    auto matches = [&](const std::string& value) {
      if (prefix) {
        return value.size() >= literal.size() &&
               value.compare(0, literal.size(), literal) == 0;
      }
      return value.size() >= literal.size() &&
             value.compare(value.size() - literal.size(), literal.size(),
                           literal) == 0;
    };
    for (const auto& run : runs_) {
      const InvertFieldRef* ref = run.segment->FindField(field);
      if (!ref) continue;
      if (prefix) {
        // Segment values are serialized in lexical order by WriteSegment.
        // Seek to the prefix range rather than testing every distinct value.
        auto value = std::lower_bound(
            ref->values.begin(), ref->values.end(), literal,
            [&](const InvertValueRef& candidate, const std::string& key) {
              if (diagnostics) ++diagnostics->strings_scanned;
              return candidate.value < key;
            });
        for (; value != ref->values.end(); ++value) {
          if (diagnostics) ++diagnostics->strings_scanned;
          if (!matches(value->value)) break;
          CollectValueDocsFromRun(run, *ref, *value, out);
        }
      } else {
        for (const auto& value : ref->values) {
          if (diagnostics) ++diagnostics->strings_scanned;
          if (matches(value.value)) {
            CollectValueDocsFromRun(run, *ref, value, out);
          }
        }
      }
    }
    const auto delta = delta_invert_.find(field);
    if (delta != delta_invert_.end()) {
      for (const auto& [value, docs] : delta->second) {
        if (diagnostics) ++diagnostics->strings_scanned;
        if (matches(value)) out->insert(out->end(), docs.begin(), docs.end());
      }
    }
    std::sort(out->begin(), out->end());
    out->erase(std::unique(out->begin(), out->end()), out->end());
    out->erase(std::remove_if(out->begin(), out->end(), [&](DocId id) {
                 return tombstones_.count(id) != 0;
               }),
               out->end());
    if (diagnostics) {
      diagnostics->candidate_capacity_bytes += out->capacity() * sizeof(DocId);
    }
    return Status::OK();
  }

  uint64_t FilterNodeCount(const FilterExpr& expr) const {
    uint64_t count = 1;
    for (const auto& child : expr.children) {
      const uint64_t child_count = FilterNodeCount(child);
      if (child_count > std::numeric_limits<uint64_t>::max() - count) {
        return std::numeric_limits<uint64_t>::max();
      }
      count += child_count;
    }
    return count;
  }

  static bool FilterExprEqual(const FilterExpr& a, const FilterExpr& b) {
    if (a.op != b.op || a.field != b.field ||
        a.string_value != b.string_value || a.uint32_value != b.uint32_value ||
        a.uint32_values != b.uint32_values || a.children.size() != b.children.size())
      return false;
    for (size_t i = 0; i < a.children.size(); ++i) {
      if (!FilterExprEqual(a.children[i], b.children[i])) return false;
    }
    return true;
  }

  static bool FilterExprCacheableImpl(const FilterExpr& expr, size_t depth,
                                      size_t* nodes, size_t* bytes) {
    if (depth > 8 || ++*nodes > 64) return false;
    *bytes += expr.field.size() + expr.string_value.size() +
              expr.uint32_values.size() * sizeof(uint32_t);
    if (*bytes > 4096) return false;
    for (const auto& child : expr.children) {
      if (!FilterExprCacheableImpl(child, depth + 1, nodes, bytes)) return false;
    }
    return true;
  }

  static bool IsFilterCacheable(const FilterExpr& expr) {
    size_t nodes = 0, bytes = 0;
    return FilterExprCacheableImpl(expr, 0, &nodes, &bytes);
  }

  bool ReadFilterCache(const FilterExpr& expr,
                       std::vector<DocId>* candidates) const {
    std::lock_guard<std::mutex> lock(filter_cache_mutex_);
    if (!filter_cache_valid_ || filter_cache_epoch_ != filter_state_epoch_ ||
        !FilterExprEqual(filter_cache_expr_, expr)) {
      return false;
    }
    *candidates = filter_cache_candidates_;
    return true;
  }

  void WriteFilterCache(const FilterExpr& expr,
                        const std::vector<DocId>& candidates) const {
    if (!IsFilterCacheable(expr) ||
        candidates.capacity() * sizeof(DocId) > kFilterCandidateCacheLimitBytes) {
      return;
    }
    std::lock_guard<std::mutex> lock(filter_cache_mutex_);
    filter_cache_expr_ = expr;
    filter_cache_candidates_ = candidates;
    filter_cache_epoch_ = filter_state_epoch_;
    filter_cache_valid_ = true;
  }

  void InvalidateFilterCache() {
    std::lock_guard<std::mutex> lock(filter_cache_mutex_);
    filter_cache_valid_ = false;
    if (++filter_state_epoch_ == 0) filter_state_epoch_ = 1;
  }

  Status FilterCandidates(const FilterExpr& expr, std::vector<DocId>* out,
                          FilterLikeDiagnostics* diagnostics = nullptr) const {
    return EvalFilter(expr, false, out, diagnostics);
  }

  Status EvalFilter(const FilterExpr& expr, bool negated,
                    std::vector<DocId>* out,
                    FilterLikeDiagnostics* diagnostics) const {
    out->clear();
    if (expr.op == FilterKind::NOT) {
      if (expr.children.size() != 1) {
        return Status::Error("NOT filter requires exactly one child");
      }
      return EvalFilter(expr.children.front(), !negated, out, diagnostics);
    }
    if (expr.op == FilterKind::ALL || expr.op == FilterKind::NONE) {
      const bool matches = (expr.op == FilterKind::ALL) != negated;
      if (matches) *out = LiveUniverse();
      return Status::OK();
    }
    if (expr.op == FilterKind::AND || expr.op == FilterKind::OR) {
      const bool intersect = (expr.op == FilterKind::AND) != negated;
      size_t first_nonconstant = expr.children.size();
      for (size_t i = 0; i < expr.children.size(); ++i) {
        const auto child_op = expr.children[i].op;
        if (child_op == FilterKind::ALL || child_op == FilterKind::NONE) {
          const bool child_matches = (child_op == FilterKind::ALL) != negated;
          if ((intersect && !child_matches) || (!intersect && child_matches)) {
            if (!intersect) *out = LiveUniverse();
            return Status::OK();
          }
        } else if (first_nonconstant == expr.children.size()) {
          first_nonconstant = i;
        }
      }
      if (first_nonconstant == expr.children.size()) {
        if (intersect) *out = LiveUniverse();
        return Status::OK();
      }
      Status status = EvalFilter(expr.children[first_nonconstant], negated, out,
                                 diagnostics);
      if (!status.ok) return status;
      for (size_t i = first_nonconstant + 1; i < expr.children.size(); ++i) {
        const auto child_op = expr.children[i].op;
        if (child_op == FilterKind::ALL || child_op == FilterKind::NONE)
          continue;
        std::vector<DocId> rhs;
        status = EvalFilter(expr.children[i], negated, &rhs, diagnostics);
        if (!status.ok) return status;
        std::vector<DocId> combined;
        combined.reserve(intersect ? std::min(out->size(), rhs.size())
                                   : out->size() + rhs.size());
        if (intersect) {
          std::set_intersection(out->begin(), out->end(), rhs.begin(), rhs.end(),
                                std::back_inserter(combined));
        } else {
          std::set_union(out->begin(), out->end(), rhs.begin(), rhs.end(),
                         std::back_inserter(combined));
        }
        out->swap(combined);
      }
      return Status::OK();
    }

    SearchResult result;
    Status status;
    switch (expr.op) {
      case FilterKind::STRING_EQ:
        status = SearchInvert(expr.field, expr.string_value, CompareOp::EQ,
                              &result);
        break;
      case FilterKind::STRING_PREFIX:
      case FilterKind::STRING_SUFFIX:
        if (negated) {
          return Status::Error("native-compatible NOT LIKE is unsupported");
        }
        return SearchStringPattern(expr.field, expr.string_value,
                                   expr.op == FilterKind::STRING_PREFIX, out,
                                   diagnostics);
      case FilterKind::UINT32_EQ:
        status = SearchInvertUint32(expr.field, expr.uint32_value, &result);
        break;
      case FilterKind::UINT32_IN:
        status = SearchInvertUint32In(expr.field, expr.uint32_values, &result);
        break;
      case FilterKind::ARRAY_UINT32_CONTAINS_ANY:
        // Native normalizes CONTAIN_ANY () to constant false before
        // complementing Boolean expressions. Thus generic NOT(predicate) is
        // all live rows for the empty positive predicate, while the explicit
        // NOT CONTAIN ANY () primitive remains IS NOT NULL.
        if (negated && expr.uint32_values.empty()) {
          *out = LiveUniverse();
          return Status::OK();
        }
        status = SearchArrayUint32(expr.field, expr.uint32_values, negated,
                                   &result);
        break;
      case FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY:
        if (negated && expr.uint32_values.empty()) {
          const std::vector<DocId> universe = LiveUniverse();
          const std::vector<DocId> present = FieldPresent(expr.field);
          std::set_difference(universe.begin(), universe.end(), present.begin(),
                              present.end(), std::back_inserter(*out));
          return Status::OK();
        }
        status = SearchArrayUint32(expr.field, expr.uint32_values, !negated,
                                   &result);
        break;
      default:
        return Status::Error("invalid Boolean filter node");
    }
    if (!status.ok) return status;
    for (const auto& hit : result.hits) out->push_back(hit.doc_id);
    if (negated && expr.op != FilterKind::ARRAY_UINT32_CONTAINS_ANY &&
        expr.op != FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY) {
      std::vector<DocId> present = FieldPresent(expr.field);
      std::vector<DocId> complement;
      complement.reserve(present.size());
      std::set_difference(present.begin(), present.end(), out->begin(),
                          out->end(), std::back_inserter(complement));
      out->swap(complement);
    }
    return Status::OK();
  }

  void CollectValueDocsFromRun(const Run& run, const InvertFieldRef& field,
                               const InvertValueRef& value,
                               std::vector<DocId>* out) const {
    (void)field;
    if (value.block_count == 0) return;
    for (uint32_t b = 0; b < value.block_count; ++b) {
      const InvertBlockMeta meta = run.segment->invert_meta_at(
          value.meta_base + static_cast<uint64_t>(b) * 24);
      const char* payload = run.segment->data() + value.payload_base +
                            meta.payload_offset;
      if (value.payload_base + meta.payload_offset + meta.payload_size >
          run.segment->size()) {
        continue;
      }
      BufReader r(payload, meta.payload_size);
      const uint64_t count = r.Varint();
      uint64_t prev = 0;
      for (uint64_t i = 0; i < count; ++i) {
        prev += r.Varint();
        out->push_back(prev);
      }
      if (!r.ok()) break;
    }
  }

  void CollectValueDocs(const std::string& field, const std::string& value,
                        std::vector<DocId>* out) const {
    for (const auto& run : runs_) {
      const InvertFieldRef* f = run.segment->FindField(field);
      if (!f) continue;
      const InvertValueRef* v = run.segment->FindValue(*f, value);
      if (!v) continue;
      CollectValueDocsFromRun(run, *f, *v, out);
    }
    const auto fit = delta_invert_.find(field);
    if (fit != delta_invert_.end()) {
      const auto vit = fit->second.find(value);
      if (vit != fit->second.end()) {
        out->insert(out->end(), vit->second.begin(), vit->second.end());
      }
    }
  }

  Status ReplayWalPayload(const std::string& payload) {
    BufReader r(payload.data(), payload.size());
    const uint8_t type = r.U8();
    if (type == 3) {
      const uint64_t count = r.Varint();
      if (!r.ok() || count == 0 || count > 256) {
        return Status::Error("bad batch wal record count");
      }
      std::vector<std::string> records;
      records.reserve(static_cast<size_t>(count));
      for (uint64_t i = 0; i < count; ++i) records.push_back(r.String());
      if (!r.ok() || r.remaining() != 0) {
        return Status::Error("bad batch wal record framing");
      }

      // Validate every child before applying any child: a malformed transaction
      // must never replay as a visible prefix.
      for (const std::string& child : records) {
        BufReader c(child.data(), child.size());
        const uint8_t child_type = c.U8();
        if (child_type == 1) {
          const uint64_t old_id = c.U64();
          const uint64_t new_id = c.U64();
          const std::string pk = c.String();
          (void)c.String();  // text
          const uint64_t n = c.Varint();
          if (!c.ok() || new_id == 0 || n > 1000000ull) {
            return Status::Error("bad upsert in batch wal record");
          }
          for (uint64_t i = 0; i < n; ++i) {
            (void)c.String();
            (void)c.String();
          }
          (void)old_id;
        } else if (child_type == 2) {
          const uint64_t id = c.U64();
          const std::string pk = c.String();
          if (!c.ok() || id == 0) {
            return Status::Error("bad delete in batch wal record");
          }
        } else {
          return Status::Error("unknown child in batch wal record");
        }
        if (!c.ok() || c.remaining() != 0) {
          return Status::Error("bad child framing in batch wal record");
        }
      }
      for (const std::string& child : records) {
        Status status = ReplayWalPayload(child);
        if (!status.ok) return status;
      }
      pending_flush_ = true;
      return Status::OK();
    }
    if (type == 1) {
      const uint64_t old_id = r.U64();
      const uint64_t new_id = r.U64();
      const std::string pk = r.String();
      Document doc;
      doc.text = r.String();
      const uint64_t n = r.Varint();
      for (uint64_t i = 0; i < n; ++i) {
        ScalarValue v;
        v.field = r.String();
        v.value = r.String();
        doc.values.push_back(std::move(v));
      }
      if (!r.ok() || r.remaining() != 0 || new_id == 0) {
        return Status::Error("bad upsert wal record");
      }
      if (old_id != 0) {
        Status s = DeleteInternal(old_id, true);
        if (!s.ok) return s;
      }
      Status s = InsertInternal(new_id, pk, doc);
      if (!s.ok) return s;
      pending_flush_ = true;
      return Status::OK();
    }
    if (type == 2) {
      const uint64_t id = r.U64();
      const std::string pk = r.String();
      (void)pk;
      if (!r.ok() || r.remaining() != 0 || id == 0) {
        return Status::Error("bad delete wal record");
      }
      Status s = DeleteInternal(id, true);
      if (!s.ok) return s;
      pending_flush_ = true;
      return Status::OK();
    }
    return Status::Error("unknown wal record");
  }

 private:
  static constexpr size_t kFilterCandidateCacheLimitBytes = 64u << 10;
  std::mutex writer_queue_mutex_;
  std::condition_variable writer_queue_cv_;
  std::condition_variable writer_queue_space_;
  std::deque<std::shared_ptr<QueuedUpsert>> writer_queue_;
  std::thread writer_queue_thread_;
  bool writer_queue_stopping_{false};
  bool writer_group_reopen_required_{false};
  mutable detail::WriterPreferredSharedMutex query_mutex_;
  mutable std::recursive_mutex mutex_;
  mutable std::mutex filter_cache_mutex_;
  uint64_t state_generation_{1};  // protected by mutex_; mutation/snapshot conflict detector
  std::mutex compaction_build_mutex_;
  std::string active_compaction_file_;      // protected by mutex_
  std::string active_compaction_temp_file_; // protected by mutex_
  uint64_t filter_state_epoch_{1};
  mutable uint64_t filter_cache_epoch_{0};
  mutable bool filter_cache_valid_{false};
  mutable FilterExpr filter_cache_expr_;
  mutable std::vector<DocId> filter_cache_candidates_;
  std::string dir_;
  Options options_;
  Analyzer analyzer_;
  Manifest manifest_;
  uint64_t durable_wal_seq_{0};
  std::vector<Run> runs_;
  std::unordered_map<std::string, DocId> pk_to_doc_;
  std::unordered_set<DocId> tombstones_;
  StatsState stats_;

  std::vector<DeltaDoc> delta_docs_;
  std::unordered_map<std::string, std::vector<BuildPosting>> delta_postings_;
  std::unordered_map<std::string,
                     std::unordered_map<std::string, std::vector<DocId>>>
      delta_invert_;
  uint64_t delta_bytes_{0};
  std::vector<StatsEvent> pending_events_;
  size_t pending_events_appended_count_{0};
  bool pending_flush_{false};

  uint64_t next_doc_id_{1};
  uint64_t next_wal_seq_{1};
  uint64_t last_wal_seq_{0};
  uint64_t run_id_seq_{0};
  uint64_t stats_epoch_{1};
  uint64_t stats_log_end_{0};

  Wal wal_;
  uint64_t bytes_written_{0};
  uint64_t segment_bytes_written_{0};
  mutable uint64_t fsyncs_{0};
  mutable uint64_t compactions_{0};
  mutable uint64_t compacted_docs_{0};
  mutable double compaction_wall_ms_{0.0};
  mutable uint64_t compaction_merges_{0};
  double last_debt_compact_ms_{-1e9};
  mutable uint64_t compaction_input_docs_{0};
  mutable uint64_t compaction_output_docs_{0};
  mutable uint64_t compaction_peak_bytes_{0};
};

// ============================== Public API ================================

Index::Index(Impl* impl) : impl_(impl) {}
Index::~Index() = default;

Status Index::Open(const std::string& directory, const Options& options,
                   std::unique_ptr<Index>* out,
                   OpenDiagnostics* diagnostics) {
  if (diagnostics) *diagnostics = OpenDiagnostics{};
  if (options.writer_group_commit_max_batch_size == 1 ||
      options.writer_group_commit_max_batch_size > 256 ||
      options.writer_group_commit_max_delay_us > 50000 ||
      (options.writer_group_commit_per_upsert_records &&
       options.writer_group_commit_max_batch_size < 2)) {
    return Status::Error("invalid writer group-commit bounds");
  }
  auto impl = std::make_unique<Impl>(directory, options);
  Status s = impl->Open(diagnostics);
  if (!s.ok) return s;
  impl->StartWriterQueue();
  *out = std::unique_ptr<Index>(new Index(impl.release()));
  return Status::OK();
}

Status Index::Upsert(const std::string& pk, const Document& document) {
  return impl_->Upsert(pk, document, nullptr);
}
Status Index::Upsert(const std::string& pk, const Document& document,
                     WriteDiagnostics* diagnostics) {
  if (diagnostics) {
    const bool requested = diagnostics->capture_timing;
    *diagnostics = WriteDiagnostics{};
    diagnostics->capture_timing = requested;
  }
  return impl_->Upsert(pk, document, diagnostics);
}
Status Index::Delete(const std::string& pk) { return impl_->Delete(pk); }
Status Index::ApplyBatch(const std::vector<Mutation>& mutations) {
  return impl_->ApplyBatch(mutations);
}
Status Index::ApplyBatch(const std::vector<Mutation>& mutations,
                         WriteDiagnostics* diagnostics) {
  return impl_->ApplyBatch(mutations, diagnostics);
}
Status Index::Flush() { return impl_->Flush(); }
Status Index::Flush(CompactionDiagnostics* diagnostics) {
  return impl_->Flush(diagnostics);
}
Status Index::CompactNow() { return impl_->CompactNow(); }
Status Index::CompactNow(CompactionDiagnostics* diagnostics) {
  return impl_->CompactNow(diagnostics);
}
Status Index::CompactNowSnapshotPrototype() {
  return impl_->CompactNowSnapshotPrototype();
}
Status Index::CompactNowSnapshotPrototype(
    CompactionDiagnostics* diagnostics) {
  return impl_->CompactNowSnapshotPrototype(diagnostics);
}
Status Index::CompactNowSnapshotRebasePrototype() {
  return impl_->CompactNowSnapshotRebasePrototype();
}
Status Index::CompactNowSnapshotRebasePrototype(
    CompactionDiagnostics* diagnostics) {
  return impl_->CompactNowSnapshotRebasePrototype(diagnostics);
}
Status Index::SearchFts(const std::string& query, uint32_t top_k,
                        SearchResult* out,
                        QueryDiagnostics* diagnostics) const {
  return impl_->SearchFts(query, top_k, out, diagnostics);
}
Status Index::SearchFtsFiltered(const std::string& query, uint32_t top_k,
                                const FilterExpr& filter, SearchResult* out,
                                QueryDiagnostics* diagnostics) const {
  return impl_->SearchFtsFiltered(query, top_k, filter, out, diagnostics);
}
Status Index::SearchFilter(const FilterExpr& filter, SearchResult* out) const {
  return impl_->SearchFilter(filter, out);
}
Status Index::SearchInvert(const std::string& field, const std::string& value,
                           CompareOp op, SearchResult* out) const {
  return impl_->SearchInvert(field, value, op, out);
}
Status Index::SearchInvertUint32(const std::string& field, uint32_t value,
                                 SearchResult* out) const {
  return impl_->SearchInvertUint32(field, value, out);
}
Status Index::SearchInvertUint32In(
    const std::string& field, const std::vector<uint32_t>& values,
    SearchResult* out) const {
  return impl_->SearchInvertUint32In(field, values, out);
}
Status Index::SearchArrayUint32ContainsAny(
    const std::string& field, const std::vector<uint32_t>& values,
    SearchResult* out) const {
  return impl_->SearchArrayUint32(field, values, false, out);
}
Status Index::SearchArrayUint32NotContainsAny(
    const std::string& field, const std::vector<uint32_t>& values,
    SearchResult* out) const {
  return impl_->SearchArrayUint32(field, values, true, out);
}
Status Index::SearchHybrid(
    const std::string& query, uint32_t top_k, double alpha,
    const std::unordered_map<std::string, float>& vector_scores,
    SearchResult* out) const {
  return impl_->SearchHybrid(query, top_k, alpha, vector_scores, out);
}
IndexStats Index::GetStats() const { return impl_->GetStats(); }

}  // namespace m4
