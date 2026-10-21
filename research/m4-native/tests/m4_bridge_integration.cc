// Research-only Rust-side generation coordinator proof: M4 owns FTS/INVERT;
// the checked-out native Zvec C API remains the vector/HNSW backend.
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <fcntl.h>
#include <iostream>
#include <iomanip>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <shared_mutex>
#include <system_error>
#include <sstream>
#include <string>
#include <utility>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <thread>
#include <time.h>
#include <vector>

#include "m4/index.h"
#include "../src/tokenizer.h"
#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
#ifndef M4_BRIDGE_DIMENSION
#define M4_BRIDGE_DIMENSION 4
#endif
constexpr uint32_t kDimension = M4_BRIDGE_DIMENSION;
static_assert(kDimension >= 4, "bridge fixture requires at least four vector dimensions");
std::atomic<int> checks{0};
std::atomic<int> failures{0};
void Check(bool ok, const std::string& label) {
  ++checks;
  if (!ok) { ++failures; std::cerr << "FAIL: " << label << '\n'; }
}
uint64_t CurrentThreadTid() {
  return static_cast<uint64_t>(::syscall(SYS_gettid));
}
double CurrentThreadCpuMs() {
  timespec cpu{};
  if (::clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cpu) != 0)
    throw std::runtime_error("thread CPU clock unavailable");
  return cpu.tv_sec * 1000.0 + cpu.tv_nsec / 1e6;
}
uint64_t CurrentThreadRunqueueWaitNs() {
  const auto tid = static_cast<pid_t>(::syscall(SYS_gettid));
  std::ifstream in("/proc/self/task/" + std::to_string(tid) + "/schedstat");
  uint64_t runtime_ns = 0, wait_ns = 0, timeslices = 0;
  if (!(in >> runtime_ns >> wait_ns >> timeslices))
    throw std::runtime_error("thread schedstat unavailable");
  return wait_ns;
}
std::string ReadProcText(const char* path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error(std::string("cannot read ") + path);
  return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}
std::string JsonEscape(const std::string& value) {
  std::ostringstream out;
  for (unsigned char c : value) {
    switch (c) {
      case '\\': out << "\\\\"; break;
      case '"': out << "\\\""; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (c < 0x20) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<unsigned>(c) << std::dec;
        else out << static_cast<char>(c);
    }
  }
  return out.str();
}
struct PsiReceipt {
  double offset_ms{0};
  std::string label, cpu, io, memory;
};
void Need(zvec_error_code_t code, const std::string& label) {
  if (code == ZVEC_OK) {
    Check(true, label);
    return;
  }
  zvec_error_details_t details{};
  (void)zvec_get_last_error_details(&details);
  std::ostringstream message;
  message << label << ": " << zvec_error_code_to_string(code);
  if (details.message != nullptr && details.message[0] != '\0')
    message << ": " << details.message;
  if (details.file != nullptr && details.file[0] != '\0')
    message << " (" << details.file << ':' << details.line << ' ' << (details.function ? details.function : "") << ')';
  Check(false, message.str());
  throw std::runtime_error(message.str());
}
struct Row {
  std::string pk;
  std::string text;
  std::string file_name;
  std::string symbol_type;
  uint32_t file_id{0};
  std::vector<uint32_t> directories;
  std::array<float, kDimension> vector{};
};
struct Native {
  explicit Native(zvec_collection_t* handle = nullptr) : value(handle) {}
  Native(const Native&) = delete;
  Native& operator=(const Native&) = delete;
  Native(Native&& other) noexcept : value(std::exchange(other.value, nullptr)) {}
  Native& operator=(Native&& other) noexcept {
    if (this != &other) {
      if (value) (void)zvec_collection_close(value);
      value = std::exchange(other.value, nullptr);
    }
    return *this;
  }
  ~Native() { (void)Close(); }
  bool Close() {
    if (!value) return true;
    auto* handle = std::exchange(value, nullptr);
    return zvec_collection_close(handle) == ZVEC_OK;
  }
  zvec_collection_t* value{nullptr};
};
struct Hit { std::string pk; double score; };
struct HybridStageTimes { double vector_ms{0}; double fts_ms{0}; double merge_ms{0}; };

zvec_field_schema_t* AddField(std::vector<zvec_field_schema_t*>* owned_fields,
                             const char* name, zvec_data_type_t type, bool nullable,
                             uint32_t dim = 0) {
  auto* field = zvec_field_schema_create(name, type, nullable, dim);
  if (!field) throw std::runtime_error(std::string("create field ") + name);
  owned_fields->push_back(field);
  return field;
}
void AddInvert(zvec_field_schema_t* field, bool range, bool wildcard) {
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
  Need(zvec_index_params_set_invert_params(params, range, wildcard), "set invert params");
  Need(zvec_field_schema_set_index_params(field, params), "attach invert params");
  zvec_index_params_destroy(params);
}
Native OpenVector(const fs::path& path, bool create,
                  zvec_index_type_t vector_index_type = ZVEC_INDEX_TYPE_HNSW) {
  auto* schema = zvec_collection_schema_create("m4_bridge_vector");
  std::vector<zvec_field_schema_t*> owned_fields;
  auto* logical = AddField(&owned_fields, "logical_pk", ZVEC_DATA_TYPE_STRING, false);
  auto* generation = AddField(&owned_fields, "generation", ZVEC_DATA_TYPE_UINT32, false);
  auto* file_id = AddField(&owned_fields, "file_id", ZVEC_DATA_TYPE_UINT32, false);
  auto* dirs = AddField(&owned_fields, "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, false);
  auto* name = AddField(&owned_fields, "file_name", ZVEC_DATA_TYPE_STRING, false);
  auto* symbol = AddField(&owned_fields, "symbol_type", ZVEC_DATA_TYPE_STRING, true);
  AddInvert(logical, false, false); AddInvert(generation, true, false);
  AddInvert(file_id, true, false); AddInvert(dirs, true, false);
  AddInvert(name, false, true); AddInvert(symbol, false, false);
  auto* embedding = AddField(&owned_fields, "embedding", ZVEC_DATA_TYPE_VECTOR_FP32, false, kDimension);
  auto* vector_params = zvec_index_params_create(vector_index_type);
  Need(zvec_index_params_set_metric_type(vector_params, ZVEC_METRIC_TYPE_COSINE),
       "set vector cosine metric");
  if (vector_index_type == ZVEC_INDEX_TYPE_HNSW)
    Need(zvec_index_params_set_hnsw_params(vector_params, 16, 100),
         "set HNSW params");
  Need(zvec_field_schema_set_index_params(embedding, vector_params),
       "attach vector index params");
  zvec_index_params_destroy(vector_params);
  for (auto* field : owned_fields)
    Need(zvec_collection_schema_add_field(schema, field), "add configured vector field");
  for (auto* field : owned_fields) zvec_field_schema_destroy(field);
  zvec_collection_t* collection = nullptr;
  const auto code = create
      ? zvec_collection_create_and_open(path.c_str(), schema, nullptr, &collection)
      : zvec_collection_open(path.c_str(), nullptr, &collection);
  zvec_collection_schema_destroy(schema);
  Need(code, create ? "create vector collection" : "open vector collection");
  return Native(collection);
}
uint64_t NativeDocCount(zvec_collection_t* collection) {
  zvec_collection_stats_t* stats = nullptr;
  Need(zvec_collection_get_stats(collection, &stats), "get native collection stats");
  const auto count = zvec_collection_stats_get_doc_count(stats);
  zvec_collection_stats_destroy(stats);
  return count;
}
void AddString(zvec_doc_t* doc, const char* field, const std::string& value) {
  Need(zvec_doc_add_field_by_value(doc, field, ZVEC_DATA_TYPE_STRING, value.data(), value.size()), std::string("add ") + field);
}
zvec_doc_t* VectorDoc(const Row& row, uint32_t generation) {
  auto* doc = zvec_doc_create();
  const std::string versioned = std::to_string(generation) + ":" + row.pk;
  zvec_doc_set_pk(doc, versioned.c_str());
  AddString(doc, "logical_pk", row.pk);
  Need(zvec_doc_add_field_by_value(doc, "generation", ZVEC_DATA_TYPE_UINT32, &generation, sizeof(generation)), "add generation");
  Need(zvec_doc_add_field_by_value(doc, "file_id", ZVEC_DATA_TYPE_UINT32, &row.file_id, sizeof(row.file_id)), "add file_id");
  Need(zvec_doc_add_field_by_value(doc, "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, row.directories.data(), row.directories.size() * sizeof(uint32_t)), "add directory ids");
  AddString(doc, "file_name", row.file_name);
  if (!row.symbol_type.empty()) AddString(doc, "symbol_type", row.symbol_type);
  Need(zvec_doc_add_field_by_value(doc, "embedding", ZVEC_DATA_TYPE_VECTOR_FP32, row.vector.data(), sizeof(row.vector)), "add vector");
  return doc;
}
std::string ManifestPath(const fs::path& root) { return (root / "generation.manifest").string(); }
void SyncDir(const fs::path& path) {
  const int fd = ::open(path.c_str(), O_RDONLY | O_DIRECTORY);
  if (fd < 0) throw std::runtime_error("open directory for sync");
  const int result = ::fsync(fd); const int saved_errno = errno; ::close(fd);
  if (result != 0) { errno = saved_errno; throw std::runtime_error("sync directory"); }
}
void AtomicManifest(const fs::path& root, uint32_t generation) {
  const fs::path tmp = root / "generation.manifest.tmp";
  const fs::path dst = root / "generation.manifest";
  { std::ofstream out(tmp, std::ios::trunc); out << generation << '\n'; out.flush(); if (!out) throw std::runtime_error("write manifest"); }
  int fd = ::open(tmp.c_str(), O_RDONLY); if (fd < 0 || ::fsync(fd) != 0) throw std::runtime_error("fsync manifest"); ::close(fd);
  fs::rename(tmp, dst); SyncDir(root);
}
uint32_t ReadManifest(const fs::path& root) {
  std::ifstream in(ManifestPath(root)); uint32_t generation = 0;
  if (!(in >> generation)) throw std::runtime_error("read manifest");
  return generation;
}
uint32_t Checksum(const std::string& text) {
  uint32_t hash = 2166136261u;
  for (unsigned char byte : text) hash = (hash ^ byte) * 16777619u;
  return hash;
}
void AppendSync(const fs::path& root, const std::string& record) {
  const fs::path wal = root / "coordinator.wal";
  int fd = ::open(wal.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
  if (fd < 0) throw std::runtime_error("open coordinator WAL");
  char checksum[16]; std::snprintf(checksum, sizeof(checksum), "%08x", Checksum(record));
  const std::string line = record + " " + checksum + "\n";
  size_t offset = 0;
  while (offset < line.size()) {
    const ssize_t n = ::write(fd, line.data() + offset, line.size() - offset);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) { ::close(fd); throw std::runtime_error("append coordinator WAL"); }
    offset += static_cast<size_t>(n);
  }
  const bool ok = ::fsync(fd) == 0; ::close(fd);
  if (!ok) throw std::runtime_error("sync coordinator WAL");
}
bool ValidWalRecord(const std::string& line, std::string* kind_out = nullptr,
                    uint32_t* generation_out = nullptr) {
  std::istringstream parsed(line); std::string kind, checksum; uint32_t generation;
  if (!(parsed >> kind >> generation >> checksum)) return false;
  const std::string body = kind + " " + std::to_string(generation);
  char expected[16]; std::snprintf(expected, sizeof(expected), "%08x", Checksum(body));
  if (checksum != expected || (kind != "R" && kind != "P")) return false;
  if (kind_out) *kind_out = kind;
  if (generation_out) *generation_out = generation;
  return true;
}
void RepairWal(const fs::path& root) {
  const fs::path path = root / "coordinator.wal";
  if (!fs::exists(path)) return;
  std::ifstream in(path, std::ios::binary); std::string line;
  uintmax_t valid_end = 0; bool truncate = false;
  while (std::getline(in, line)) {
    const auto position = in.tellg();
    if (in.eof() || !ValidWalRecord(line)) { truncate = true; break; }
    valid_end = static_cast<uintmax_t>(position);
  }
  if (truncate) {
    fs::resize_file(path, valid_end);
    int fd = ::open(path.c_str(), O_WRONLY);
    if (fd < 0 || ::fsync(fd) != 0) throw std::runtime_error("sync repaired coordinator WAL");
    ::close(fd);
  }
}
uint32_t Recover(const fs::path& root) {
  RepairWal(root);
  uint32_t published = ReadManifest(root), highest_reserved = published;
  std::ifstream in(root / "coordinator.wal"); std::string line;
  while (std::getline(in, line)) {
    if (in.eof()) break;  // An unterminated suffix is a torn WAL record.
    std::string kind; uint32_t gen;
    if (!ValidWalRecord(line, &kind, &gen)) break;
    if (kind == "R") highest_reserved = std::max(highest_reserved, gen);
    else if (kind == "P") published = std::max(published, gen);
    else break;
  }
  if (published != ReadManifest(root)) AtomicManifest(root, published);
  return published;
}
m4::Document M4Doc(const Row& row, uint32_t generation) {
  m4::Document doc; doc.text = row.text;
  doc.values.push_back(m4::ScalarValue::Unsigned32("generation", generation));
  doc.values.push_back(m4::ScalarValue::Unsigned32("file_id", row.file_id));
  doc.values.push_back({"file_name", row.file_name});
  doc.values.push_back({"logical_pk", row.pk});
  if (!row.symbol_type.empty()) doc.values.push_back({"symbol_type", row.symbol_type});
  doc.array_uint32_values.push_back({"ancestor_directory_ids", row.directories});
  return doc;
}

void SyncTree(const fs::path& root,
              bool hardlinked_m4_segments_already_durable = false) {
  std::vector<fs::path> directories;
  for (const auto& entry : fs::recursive_directory_iterator(root)) {
    if (entry.is_directory()) {
      directories.push_back(entry.path());
    } else if (entry.is_regular_file()) {
      const std::string name = entry.path().filename().string();
      const bool is_hardlinked_m4_segment =
          name.rfind("run-", 0) == 0 && entry.path().extension() == ".seg";
      // Only immutable hardlinked segments may reuse an already-durable
      // inode. Mutable FTS state, especially wal.log after Flush truncation,
      // must be file-synced before generation publication. Directory fsync
      // alone does not persist file contents or the WAL's new size.
      if (hardlinked_m4_segments_already_durable &&
          is_hardlinked_m4_segment) continue;
      const int fd = ::open(entry.path().c_str(), O_RDONLY | O_CLOEXEC);
      if (fd < 0) throw std::runtime_error("open generation file for sync");
      const int result = ::fsync(fd);
      const int saved_errno = errno;
      ::close(fd);
      if (result != 0) { errno = saved_errno; throw std::runtime_error("sync generation file"); }
    }
  }
  std::sort(directories.rbegin(), directories.rend());
  for (const auto& directory : directories) SyncDir(directory);
  SyncDir(root);
}

uint64_t DirectoryBytes(const fs::path& root) {
  uint64_t bytes = 0;
  std::error_code ec;
  for (fs::recursive_directory_iterator it(root, ec), end; !ec && it != end; it.increment(ec)) {
    if (it->is_regular_file(ec) && !ec) bytes += it->file_size(ec);
    if (ec) throw std::runtime_error("measure generation bytes: " + ec.message());
  }
  if (ec) throw std::runtime_error("walk generation bytes: " + ec.message());
  return bytes;
}

void PutU32(std::string* out, uint32_t value) {
  for (unsigned shift = 0; shift != 32; shift += 8)
    out->push_back(static_cast<char>((value >> shift) & 0xff));
}
void PutString(std::string* out, const std::string& value) {
  if (value.size() > UINT32_MAX) throw std::runtime_error("snapshot string too large");
  PutU32(out, static_cast<uint32_t>(value.size()));
  out->append(value);
}
struct SnapshotReader {
  const std::string& bytes;
  size_t offset{0};
  uint32_t U32() {
    if (offset + 4 > bytes.size()) throw std::runtime_error("truncated generation snapshot");
    uint32_t value = 0;
    for (unsigned shift = 0; shift != 32; shift += 8)
      value |= static_cast<uint32_t>(static_cast<unsigned char>(bytes[offset++])) << shift;
    return value;
  }
  std::string String() {
    const uint32_t size = U32();
    if (size > (64u << 20) || offset + size > bytes.size())
      throw std::runtime_error("invalid generation snapshot string length");
    std::string value = bytes.substr(offset, size);
    offset += size;
    return value;
  }
};
std::string EncodeSnapshot(uint32_t generation, const std::vector<Row>& rows) {
  std::string bytes("M4GS", 4);
  PutU32(&bytes, 1);
  PutU32(&bytes, generation);
  if (rows.size() > UINT32_MAX) throw std::runtime_error("too many snapshot rows");
  PutU32(&bytes, static_cast<uint32_t>(rows.size()));
  for (const auto& row : rows) {
    PutString(&bytes, row.pk); PutString(&bytes, row.text);
    PutString(&bytes, row.file_name); PutString(&bytes, row.symbol_type);
    PutU32(&bytes, row.file_id);
    if (row.directories.size() > UINT32_MAX) throw std::runtime_error("too many directory ids");
    PutU32(&bytes, static_cast<uint32_t>(row.directories.size()));
    for (uint32_t id : row.directories) PutU32(&bytes, id);
    for (float value : row.vector) {
      uint32_t bits;
      std::memcpy(&bits, &value, sizeof(bits));
      PutU32(&bytes, bits);
    }
  }
  PutU32(&bytes, Checksum(bytes));
  return bytes;
}
std::vector<Row> DecodeSnapshot(const fs::path& path, uint32_t expected_generation) {
  std::error_code size_error;
  const uint64_t file_bytes = fs::file_size(path, size_error);
  if (size_error || file_bytes > (1ull << 32)) throw std::runtime_error("generation snapshot exceeds size limit");
  std::ifstream in(path, std::ios::binary);
  const std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (in.bad() || bytes.size() < 20 || bytes.compare(0, 4, "M4GS") != 0)
    throw std::runtime_error("invalid generation snapshot header");
  SnapshotReader reader{bytes};
  reader.offset = 4;
  if (reader.U32() != 1 || reader.U32() != expected_generation)
    throw std::runtime_error("generation snapshot version/id mismatch");
  const uint32_t count = reader.U32();
  if (count > 1'000'000) throw std::runtime_error("generation snapshot row count exceeds limit");
  std::vector<Row> rows;
  rows.reserve(count);
  std::set<std::string> pks;
  for (uint32_t i = 0; i < count; ++i) {
    Row row;
    row.pk = reader.String(); row.text = reader.String();
    row.file_name = reader.String(); row.symbol_type = reader.String();
    row.file_id = reader.U32();
    const uint32_t directories = reader.U32();
    if (directories > 1'000'000) throw std::runtime_error("snapshot directory list exceeds limit");
    row.directories.reserve(directories);
    for (uint32_t j = 0; j < directories; ++j) row.directories.push_back(reader.U32());
    for (float& value : row.vector) {
      const uint32_t bits = reader.U32();
      std::memcpy(&value, &bits, sizeof(value));
    }
    if (!pks.insert(row.pk).second) throw std::runtime_error("duplicate PK in generation snapshot");
    rows.push_back(std::move(row));
  }
  if (reader.offset + 4 != bytes.size()) throw std::runtime_error("generation snapshot trailing/short bytes");
  const uint32_t stored = reader.U32();
  if (stored != Checksum(bytes.substr(0, bytes.size() - 4)))
    throw std::runtime_error("generation snapshot checksum mismatch");
  return rows;
}
void DurableWrite(const fs::path& path, const std::string& bytes) {
  const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) throw std::runtime_error("open durable generation file");
  size_t offset = 0;
  while (offset < bytes.size()) {
    const ssize_t written = ::write(fd, bytes.data() + offset, bytes.size() - offset);
    if (written < 0 && errno == EINTR) continue;
    if (written <= 0) { ::close(fd); throw std::runtime_error("write durable generation file"); }
    offset += static_cast<size_t>(written);
  }
  const bool ok = ::fsync(fd) == 0;
  const int saved_errno = errno;
  ::close(fd);
  if (!ok) { errno = saved_errno; throw std::runtime_error("sync durable generation file"); }
}
struct NativeCallMetric {
  std::chrono::steady_clock::time_point start{}, end{};
  double cpu_ms{0};
  uint64_t caller_tid{0};
};
struct BackendLockEvent {
  std::chrono::steady_clock::time_point wait_start{}, acquired{};
};
struct SharedGcProgress {
  uint32_t reclaimed_below{1};
  uint32_t orphan_reserved_through{0};
};
struct SharedCommitTimings {
  double m4_apply_ms{0};
  double m4_flush_ms{0};
  double native_upsert_ms{0};
  double native_flush_ms{0};
  double publish_ms{0};
};
struct SharedGcTimings {
  double m4_search_delete_ms{0};
  double m4_flush_ms{0};
  double native_delete_ms{0};
  double native_flush_ms{0};
  double compaction_ms{0};
  double checkpoint_ms{0};
  size_t generations_examined{0};
};
bool ReadSharedGcProgress(const fs::path& root, SharedGcProgress* progress,
                          std::string* error);
void WriteSharedGcProgress(const fs::path& root,
                           const SharedGcProgress& progress);
class Coordinator {
 public:
  Coordinator(fs::path root, zvec_collection_t* vector, m4::Index* fts)
      : root_(std::move(root)), vector_(vector), fts_(fts), generation_(Recover(root_)) {}
  uint32_t generation() const { return generation_.load(std::memory_order_acquire); }
  size_t last_gc_generations_examined() const { return last_gc_generations_examined_; }
  // A new immutable generation is staged in both stores; the coordinator WAL
  // and atomic manifest are the only visibility point. Old generations remain
  // queryable for readers that pinned their manifest generation.
  bool Commit(const std::vector<Row>& complete_live_snapshot, uint32_t expected_generation,
              const std::string& stop_after = {},
              const std::function<void()>& after_vector_flush = {},
              const std::function<void()>& after_reserve = {},
              SharedCommitTimings* timings = nullptr,
              const std::function<void()>& after_m4_apply = {},
              const std::function<void()>& after_vector_batch = {}) {
    std::lock_guard<std::mutex> writer(commit_mutex_);
    if (generation_.load(std::memory_order_acquire) != expected_generation) return false;
    if (stop_after == "crash_before_reserve") ::_exit(79);
    const uint32_t next = NextGeneration(root_);
    AppendSync(root_, "R " + std::to_string(next));
    if (after_reserve) after_reserve();
    if (stop_after == "crash_after_reserve") ::_exit(80);
    constexpr size_t kMaxBatchDocs = 256;
    constexpr size_t kMaxBatchBytes = 7u << 20;
    const auto m4_apply_start = std::chrono::steady_clock::now();
    for (size_t at = 0; at < complete_live_snapshot.size();) {
      size_t end = at, bytes = 0;
      std::vector<m4::Mutation> mutations;
      mutations.reserve(std::min(kMaxBatchDocs, complete_live_snapshot.size() - at));
      while (end < complete_live_snapshot.size() && end - at < kMaxBatchDocs) {
        const auto& row = complete_live_snapshot[end];
        const size_t row_bytes = row.pk.size() + row.text.size() + row.file_name.size() + row.symbol_type.size() + 256;
        if (end != at && bytes + row_bytes > kMaxBatchBytes) break;
        if (row_bytes > kMaxBatchBytes) return false;
        bytes += row_bytes;
        mutations.emplace_back(m4::MutationKind::UPSERT,
            std::to_string(next) + ":" + row.pk, M4Doc(row, next));
        ++end;
      }
      if (!fts_->ApplyBatch(mutations).ok) return false;
      at = end;
    }
    if (timings) timings->m4_apply_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - m4_apply_start).count();
    if (after_m4_apply) after_m4_apply();
    const auto m4_flush_start = std::chrono::steady_clock::now();
    if (!fts_->Flush().ok) return false;
    if (timings) timings->m4_flush_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - m4_flush_start).count();
    if (stop_after == "after_m4") return false;
    if (stop_after == "crash_after_m4") ::_exit(81);
    // M4 generation rows are append-only and filtered by snapshot generation,
    // so they can be staged while readers query the prior published generation.
    // Native vector writes mutate row-count-dependent filter bitmaps: acquire an
    // exclusive lease around each mutation call and final flush/publication.
    const auto native_upsert_start = std::chrono::steady_clock::now();
    for (size_t at = 0; at < complete_live_snapshot.size(); at += kMaxBatchDocs) {
      const size_t end = std::min(at + kMaxBatchDocs, complete_live_snapshot.size());
      std::vector<zvec_doc_t*> docs; docs.reserve(end - at);
      for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(complete_live_snapshot[i], next));
      std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
      zvec_write_result_t* results = nullptr; size_t count = 0;
      zvec_error_code_t code;
      {
        // Each native mutation call is an indivisible row-count transition for
        // query safety; readers of the old generation may run between batches.
        std::unique_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
        code = zvec_collection_upsert_with_results(
            vector_, refs.data(), refs.size(), &results, &count);
      }
      bool ok = code == ZVEC_OK && count == refs.size();
      for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
      zvec_write_results_free(results, count);
      for (auto* doc : docs) zvec_doc_destroy(doc);
      if (!ok) return false;
      if (after_vector_batch) after_vector_batch();
    }
    if (timings) timings->native_upsert_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - native_upsert_start).count();
    // No mutation is in flight between upsert calls. Reacquire an exclusive
    // lease for final flush and keep it through durable generation publication.
    std::unique_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    const auto native_flush_start = std::chrono::steady_clock::now();
    if (zvec_collection_flush(vector_) != ZVEC_OK) return false;
    if (timings) timings->native_flush_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - native_flush_start).count();
    if (std::getenv("M4_BRIDGE_TRACE"))
      std::cerr << "BRIDGE vector_staged generation=" << next << " rows="
                << complete_live_snapshot.size() << " native_doc_count="
                << NativeDocCount(vector_) << " owner=writer\n";
    if (after_vector_flush) after_vector_flush();
    if (stop_after == "after_vector") return false;
    if (stop_after == "crash_after_vector") ::_exit(82);
    const auto publish_start = std::chrono::steady_clock::now();
    AppendSync(root_, "P " + std::to_string(next));
    if (stop_after == "after_publish_wal") return false;
    if (stop_after == "crash_after_publish_wal") ::_exit(83);
    AtomicManifest(root_, next);
    if (timings) timings->publish_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - publish_start).count();
    generation_.store(next, std::memory_order_release);
    if (std::getenv("M4_BRIDGE_TRACE"))
      std::cerr << "BRIDGE publish generation=" << next << " owner=manifest+atomic\n";
    return true;
  }
  uint32_t SearchSnapshot() {
    std::shared_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    return generation_.load(std::memory_order_acquire);
  }
  uint32_t PinCurrent(const std::function<void(uint32_t)>& register_pin) const {
    std::shared_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    const uint32_t current = generation_.load(std::memory_order_acquire);
    register_pin(current);
    return current;
  }
  std::vector<Hit> SearchFts(uint32_t snapshot, const std::string& query,
                             const m4::FilterExpr& filter, size_t topk,
                             BackendLockEvent* lock_event = nullptr,
                             m4::QueryDiagnostics* diagnostics = nullptr) const {
    const auto wait_start = std::chrono::steady_clock::now();
    std::shared_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    if (lock_event) {
      lock_event->wait_start = wait_start;
      lock_event->acquired = std::chrono::steady_clock::now();
    }
    return SearchFtsUnlocked(snapshot, query, filter, topk, diagnostics);
  }
  std::vector<Hit> SearchVector(uint32_t snapshot, const std::array<float, kDimension>& q,
                                const std::string& extra_filter, size_t topk,
                                double* native_search_ms = nullptr,
                                BackendLockEvent* lock_event = nullptr,
                                double* native_search_cpu_ms = nullptr,
                                std::vector<NativeCallMetric>* native_calls = nullptr) const {
    const auto wait_start = std::chrono::steady_clock::now();
    std::shared_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    if (lock_event) {
      lock_event->wait_start = wait_start;
      lock_event->acquired = std::chrono::steady_clock::now();
    }
    return SearchVectorUnlocked(snapshot, q, extra_filter, topk, native_search_ms,
                                native_search_cpu_ms, native_calls);
  }
  std::vector<Hit> Hybrid(uint32_t snapshot, const std::array<float, kDimension>& q,
                          const std::string& text, size_t topk,
                          const std::function<void()>& after_vector_query = {},
                          HybridStageTimes* stage_times = nullptr,
                          m4::QueryDiagnostics* m4_diagnostics = nullptr) const {
    std::shared_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    if (std::getenv("M4_BRIDGE_TRACE"))
      std::cerr << "BRIDGE hybrid_pin generation=" << snapshot << " owner=read_lease\n";
    const auto vector_start = stage_times ? std::chrono::steady_clock::now()
                                          : std::chrono::steady_clock::time_point{};
    auto v = SearchVectorUnlocked(snapshot, q, "", topk);
    if (stage_times)
      stage_times->vector_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - vector_start).count();
    if (after_vector_query) after_vector_query();
    const auto fts_start = stage_times ? std::chrono::steady_clock::now()
                                       : std::chrono::steady_clock::time_point{};
    auto f = SearchFtsUnlocked(snapshot, text, m4::FilterExpr::All(), topk, m4_diagnostics);
    if (stage_times)
      stage_times->fts_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - fts_start).count();
    const auto merge_start = stage_times ? std::chrono::steady_clock::now()
                                         : std::chrono::steady_clock::time_point{};
    std::map<std::string, double> scores;
    for (size_t i = 0; i < v.size(); ++i) scores[v[i].pk] += 1.0 / (60.0 + i + 1);
    for (size_t i = 0; i < f.size(); ++i) scores[f[i].pk] += 1.0 / (60.0 + i + 1);
    std::vector<Hit> out;
    for (const auto& [pk, score] : scores) out.push_back({pk, score});
    std::sort(out.begin(), out.end(), [](const Hit& a, const Hit& b) {
      return a.score != b.score ? a.score > b.score : a.pk < b.pk;
    });
    if (out.size() > topk) out.resize(topk);
    if (stage_times)
      stage_times->merge_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - merge_start).count();
    return out;
  }
  bool CompactComponents() {
    std::lock_guard<std::mutex> writer(commit_mutex_);
    std::unique_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    const auto fts_status = fts_->CompactNow();
    if (!fts_status.ok) return false;
    return zvec_collection_optimize(vector_) == ZVEC_OK;
  }
  // Research-only in-place generation reclamation. Publication happens first;
  // this method is idempotent and may be retried after a crash. Readers hold
  // generation leases. The oldest pinned generation bounds M4 reclamation;
  // stale M4 rows can be deleted while current-generation readers continue.
  // Acquire the exclusive snapshot lock before native deletion/compaction, whose
  // row-count and cross-backend transitions must not overlap readers. Writer
  // lock is acquired first, as in Commit, so staging and GC cannot invert order.
  bool CollectBeforeGeneration(uint32_t floor, bool force_compact = false,
                               const std::string& stop_after = {},
                               SharedGcTimings* timings = nullptr,
                               const std::function<void()>& after_m4_delete = {}) {
    std::lock_guard<std::mutex> writer(commit_mutex_);
    last_gc_generations_examined_ = 0;
    const uint32_t current = generation_.load(std::memory_order_acquire);
    if (floor == 0 || floor > current) return false;

    SharedGcProgress progress;
    std::string progress_error;
    if (!ReadSharedGcProgress(root_, &progress, &progress_error)) {
      std::cerr << "reuse_gc progress read error=" << progress_error << '\n';
      return false;
    }
    if (progress.reclaimed_below > floor) {
      std::cerr << "reuse_gc floor moved behind durable GC checkpoint\n";
      return false;
    }

    // Reclaim only work newer than the durable cursors. Published generations
    // below the oldest reader lease are a contiguous prefix; orphan reservations
    // are above the published generation and never reused. Persist cursors only
    // after both stores and any required compaction are durable.
    std::vector<uint32_t> reclaim_generations;
    for (uint64_t generation = progress.reclaimed_below; generation < floor; ++generation)
      reclaim_generations.push_back(static_cast<uint32_t>(generation));
    const uint32_t highest_reserved = NextGeneration(root_) - 1;
    const uint64_t orphan_start = std::max(
        static_cast<uint64_t>(current) + 1,
        static_cast<uint64_t>(progress.orphan_reserved_through) + 1);
    for (uint64_t generation = orphan_start; generation <= highest_reserved; ++generation)
      reclaim_generations.push_back(static_cast<uint32_t>(generation));
    last_gc_generations_examined_ = reclaim_generations.size();
    if (timings) timings->generations_examined = reclaim_generations.size();
    const auto m4_delete_start = std::chrono::steady_clock::now();
    if (!reclaim_generations.empty()) {
      m4::SearchResult old_rows;
      const auto search = fts_->SearchFilter(
          m4::FilterExpr::Uint32In("generation", reclaim_generations),
          &old_rows);
      if (!search.ok) {
        std::cerr << "reuse_gc search generations="
                  << reclaim_generations.size()
                  << " error=" << search.message << '\n';
        return false;
      }
      std::vector<m4::Mutation> deletions;
      deletions.reserve(old_rows.hits.size());
      for (const auto& hit : old_rows.hits) {
        deletions.emplace_back(m4::MutationKind::DELETE, hit.pk);
      }
      constexpr size_t kDeleteBatchSize = 256;
      for (size_t begin = 0; begin < deletions.size();
           begin += kDeleteBatchSize) {
        const size_t end = std::min(deletions.size(), begin + kDeleteBatchSize);
        std::vector<m4::Mutation> batch(
            deletions.begin() + static_cast<std::ptrdiff_t>(begin),
            deletions.begin() + static_cast<std::ptrdiff_t>(end));
        const auto status = fts_->ApplyBatch(batch);
        if (!status.ok) {
          std::cerr << "reuse_gc batch delete generations="
                    << reclaim_generations.size()
                    << " documents=" << deletions.size()
                    << " batch_begin=" << begin
                    << " error=" << status.message << '\n';
          return false;
        }
      }
    }
    if (timings) timings->m4_search_delete_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - m4_delete_start).count();
    if (after_m4_delete) after_m4_delete();
    const auto m4_flush_start = std::chrono::steady_clock::now();
    const auto m4_flush = fts_->Flush();
    if (timings) timings->m4_flush_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - m4_flush_start).count();
    if (!m4_flush.ok) {
      std::cerr << "reuse_gc M4 flush error=" << m4_flush.message << '\n';
      return false;
    }
    if (stop_after == "crash_after_m4_gc") ::_exit(84);

    // M4 reclamation is restricted to generations older than every registered
    // lease. Drain current queries only before mutating the native collection.
    std::unique_lock<std::shared_mutex> snapshot_guard(snapshot_mutex_);
    const uint64_t native_before = NativeDocCount(vector_);
    // The pinned native delete_by_filter implementation rejects range scans
    // here with Arrow ExecBatch::ToRecordBatch schema mismatch; equality is
    // covered by the native API tests and is idempotent across recovery.
    const auto native_delete_start = std::chrono::steady_clock::now();
    for (const uint32_t generation : reclaim_generations) {
      const std::string filter = "generation = " + std::to_string(generation);
      const auto vector_delete = zvec_collection_delete_by_filter(vector_, filter.c_str());
      if (vector_delete != ZVEC_OK) {
        zvec_error_details_t details{};
        (void)zvec_get_last_error_details(&details);
        std::cerr << "reuse_gc vector delete generation=" << generation
                  << " error=" << zvec_error_code_to_string(vector_delete)
                  << " detail=" << (details.message ? details.message : "") << '\n';
        return false;
      }
    }
    if (timings) timings->native_delete_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - native_delete_start).count();
    const auto native_flush_start = std::chrono::steady_clock::now();
    const auto vector_flush = zvec_collection_flush(vector_);
    if (timings) timings->native_flush_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - native_flush_start).count();
    if (vector_flush != ZVEC_OK) {
      zvec_error_details_t details{};
      (void)zvec_get_last_error_details(&details);
      std::cerr << "reuse_gc vector flush error=" << zvec_error_code_to_string(vector_flush)
                << " detail=" << (details.message ? details.message : "") << '\n';
      return false;
    }
    if (stop_after == "crash_after_vector_gc") ::_exit(85);

    const auto fts_stats = fts_->GetStats();
    const uint64_t native_after = NativeDocCount(vector_);
    const bool m4_compaction_due = fts_stats.dead_docs >=
        std::max<uint64_t>(8, fts_stats.live_docs * 2);
    const bool native_compaction_due = native_before > native_after &&
        (native_after == 0 || native_before / native_after >= 3);
    if (force_compact || m4_compaction_due || native_compaction_due) {
      const auto compaction_start = std::chrono::steady_clock::now();
      const auto compact = fts_->CompactNow();
      if (!compact.ok) {
        std::cerr << "reuse_gc M4 compact error=" << compact.message << '\n';
        return false;
      }
      const auto optimize = zvec_collection_optimize(vector_);
      if (optimize != ZVEC_OK) {
        zvec_error_details_t details{};
        (void)zvec_get_last_error_details(&details);
        std::cerr << "reuse_gc native optimize error=" << zvec_error_code_to_string(optimize)
                  << " detail=" << (details.message ? details.message : "") << '\n';
        return false;
      }
      if (timings) timings->compaction_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - compaction_start).count();
    }
    progress.reclaimed_below = floor;
    progress.orphan_reserved_through =
        std::max(progress.orphan_reserved_through, highest_reserved);
    const auto checkpoint_start = std::chrono::steady_clock::now();
    try {
      WriteSharedGcProgress(root_, progress);
      if (timings) timings->checkpoint_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - checkpoint_start).count();
    } catch (const std::exception& error) {
      std::cerr << "reuse_gc progress write error=" << error.what() << '\n';
      return false;
    }
    return true;
  }
 private:
  std::vector<Hit> SearchFtsUnlocked(uint32_t snapshot, const std::string& query,
                                     const m4::FilterExpr& filter, size_t topk,
                                     m4::QueryDiagnostics* diagnostics = nullptr) const {
    m4::SearchResult result;
    auto combined = m4::FilterExpr::And({m4::FilterExpr::Uint32Eq("generation", snapshot), filter});
    auto status = fts_->SearchFtsFiltered(query, static_cast<uint32_t>(topk), combined,
                                          &result, diagnostics);
    if (!status.ok) throw std::runtime_error(status.message);
    std::vector<Hit> out;
    for (const auto& hit : result.hits) {
      const auto colon = hit.pk.find(':');
      out.push_back({hit.pk.substr(colon + 1), hit.score});
    }
    return out;
  }
  std::vector<Hit> SearchVectorUnlocked(uint32_t snapshot, const std::array<float, kDimension>& q,
                                        const std::string& extra_filter, size_t topk,
                                        double* native_search_ms = nullptr,
                                        double* native_search_cpu_ms = nullptr,
                                        std::vector<NativeCallMetric>* native_calls = nullptr) const {
    auto* query = zvec_vector_query_create();
    Need(zvec_vector_query_set_field_name(query, "embedding"), "set vector query field");
    Need(zvec_vector_query_set_query_vector(query, q.data(), sizeof(q)), "set vector query data");
    Need(zvec_vector_query_set_topk(query, static_cast<int>(topk)), "set vector topk");
    Need(zvec_vector_query_set_include_doc_id(query, true), "include native vector doc id");
    const std::string filter = "generation = " + std::to_string(snapshot) + (extra_filter.empty() ? "" : " AND (" + extra_filter + ")");
    Need(zvec_vector_query_set_filter(query, filter.c_str()), "set generation vector filter");
    const char* fields[] = {"logical_pk"}; Need(zvec_vector_query_set_output_fields(query, fields, 1), "set vector output fields");
    zvec_doc_t** docs = nullptr; size_t count = 0;
    const double native_cpu_start = (native_search_cpu_ms || native_calls) ? CurrentThreadCpuMs() : 0.0;
    const auto native_start = std::chrono::steady_clock::now();
    const auto code = zvec_collection_query(vector_, query, &docs, &count);
    const auto native_end = std::chrono::steady_clock::now();
    if (native_search_ms)
      *native_search_ms += std::chrono::duration<double, std::milli>(native_end - native_start).count();
    if (native_search_cpu_ms || native_calls) {
      const double native_cpu_ms = CurrentThreadCpuMs() - native_cpu_start;
      if (native_search_cpu_ms) *native_search_cpu_ms += native_cpu_ms;
      if (native_calls)
        native_calls->push_back({native_start, native_end, native_cpu_ms,
                                 CurrentThreadTid()});
    }
    zvec_vector_query_destroy(query); Need(code, "run vector query");
    std::vector<Hit> out; out.reserve(count);
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]); const std::string full = pk ? pk : "";
      const auto colon = full.find(':');
      out.push_back({full.substr(colon + 1), zvec_doc_get_score(docs[i])});
      if (std::getenv("M4_BRIDGE_TRACE"))
        std::cerr << "BRIDGE vector_hit snapshot=" << snapshot << " native_doc_id="
                  << zvec_doc_get_doc_id(docs[i]) << " logical_pk=" << out.back().pk
                  << " native_doc_count=" << NativeDocCount(vector_) << " owner=read_lease\n";
    }
    zvec_docs_free(docs, count); return out;
  }
  static uint32_t NextGeneration(const fs::path& root) {
    uint32_t highest = ReadManifest(root);
    std::ifstream in(root / "coordinator.wal"); std::string line;
    while (std::getline(in, line)) {
      if (in.eof()) break;
      std::string kind; uint32_t gen;
      if (!ValidWalRecord(line, &kind, &gen)) break;
      highest = std::max(highest, gen);
    }
    return highest + 1;
  }
  fs::path root_; zvec_collection_t* vector_; m4::Index* fts_;
  std::atomic<uint32_t> generation_{0};
  // Native Zvec queries bind forward-filter row bitmaps to mutable segment row
  // counts. Keep queries out of each vector mutation call; Hybrid holds one
  // read lease across both backends. M4 rows can be staged
  // concurrently because unpublished generations are filtered out; published
  // generation rows remain append-only and queryable after unlock.
  mutable std::shared_mutex snapshot_mutex_;
  std::mutex commit_mutex_;
  size_t last_gc_generations_examined_{0};
};

// Process-local epoch leases constrain collection reuse. The owner must refuse
// to publish beyond its retention cap while an old lease remains pinned.
class EpochPins {
 public:
  void Acquire(uint32_t generation) {
    std::lock_guard<std::mutex> lock(mutex_);
    ++counts_[generation];
  }
  void Release(uint32_t generation) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = counts_.find(generation);
    if (it == counts_.end() || it->second == 0)
      throw std::logic_error("release of unpinned generation");
    if (--it->second == 0) counts_.erase(it);
  }
  uint32_t Oldest(uint32_t current) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return counts_.empty() ? current : std::min(current, counts_.begin()->first);
  }
  size_t Count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return counts_.size();
  }
 private:
  mutable std::mutex mutex_;
  std::map<uint32_t, size_t> counts_;
};
class GenerationLease {
 public:
  GenerationLease(Coordinator& coordinator, EpochPins& pins)
      : pins_(&pins), generation_(coordinator.PinCurrent(
            [&](uint32_t generation) { pins.Acquire(generation); })) {}
  GenerationLease(const GenerationLease&) = delete;
  GenerationLease& operator=(const GenerationLease&) = delete;
  GenerationLease(GenerationLease&& other) noexcept
      : pins_(std::exchange(other.pins_, nullptr)), generation_(other.generation_) {}
  GenerationLease& operator=(GenerationLease&& other) noexcept {
    if (this != &other) {
      Reset();
      pins_ = std::exchange(other.pins_, nullptr);
      generation_ = other.generation_;
    }
    return *this;
  }
  ~GenerationLease() { Reset(); }
  uint32_t generation() const { return generation_; }
  void Reset() {
    if (pins_) {
      pins_->Release(generation_);
      pins_ = nullptr;
    }
  }
 private:
  EpochPins* pins_;
  uint32_t generation_;
};

// Branch A candidate: one physical M4+native vector collection per immutable
// outer generation. The outer manifest/WAL publishes a shared_ptr snapshot;
// generation handles are only retired after all pinned readers release them.
struct BatchMutation {
  enum class Kind { UPSERT, DELETE } kind{Kind::UPSERT};
  std::string pk;
  std::optional<Row> row;
};
struct ReaderStageTimeline {
  using Clock = std::chrono::steady_clock;
  Clock::time_point vector_start{}, vector_end{};
  Clock::time_point m4_start{}, m4_end{};
  Clock::time_point merge_start{}, merge_end{};
  std::vector<BackendLockEvent> vector_lock_acquires;
  BackendLockEvent m4_lock_acquire;
  double native_vector_search_ms{0};
  double native_vector_search_cpu_ms{0};
  double vector_stage_cpu_ms{0};
  double vector_stage_runqueue_wait_ms{0};
  double m4_search_cpu_ms{0};
  double m4_stage_runqueue_wait_ms{0};
  double merge_cpu_ms{0};
  double merge_runqueue_wait_ms{0};
  bool m4_query_diagnostics_captured{false};
  m4::QueryDiagnostics m4_query_diagnostics;
  std::vector<NativeCallMetric> native_vector_calls;
};
struct ReaderStageEvent {
  using Clock = std::chrono::steady_clock;
  size_t round{0}, reader{0};
  uint64_t reader_tid{0};
  Clock::time_point acquire_release{}, acquire_end{}, pin_start{}, pin_end{};
  ReaderStageTimeline stages;
  Clock::time_point pin_release_start{}, pin_release_end{};
};
struct WriterStageEvent {
  using Clock = std::chrono::steady_clock;
  size_t round{0};
  uint64_t writer_tid{0};
  Clock::time_point round_start{}, query_release{}, build_start{}, build_end{};
  Clock::time_point generation_wal_start{}, generation_wal_end{};
  double generation_wal_cpu_ms{0};
  Clock::time_point vector_start{}, vector_end{}, vector_open_start{}, vector_open_end{};
  Clock::time_point vector_flush_start{}, vector_flush_end{};
  Clock::time_point generation_metadata_start{}, generation_metadata_end{};
  Clock::time_point m4_clone_start{}, m4_clone_end{}, m4_open_start{}, m4_open_end{};
  Clock::time_point m4_apply_start{}, m4_apply_end{}, m4_flush_start{}, m4_flush_end{};
  Clock::time_point post_m4_sync_start{}, post_m4_sync_end{};
  Clock::time_point snapshot_finalize_start{}, snapshot_finalize_end{};
  Clock::time_point compaction_start{}, compaction_end{}, m4_start{}, m4_end{};
  Clock::time_point manifest_publish_start{}, manifest_publish_end{};
  Clock::time_point snapshot_publish_start{}, snapshot_publish_end{};
  Clock::time_point retirement_start{}, retirement_end{}, round_end{};
  bool compacted{false};
};
struct GenerationRead {
  uint32_t generation{0};
  std::vector<std::string> source_pks;
  std::vector<Hit> hits;
  ReaderStageTimeline stages;
};

std::string GenerationDirectoryName(uint32_t generation) {
  return "generation-" + std::to_string(generation);
}
fs::path GenerationDirectory(const fs::path& root, uint32_t generation) {
  return root / GenerationDirectoryName(generation);
}
std::string EncodeCheckedState(const char* magic, const std::vector<uint32_t>& values) {
  std::string bytes(magic, 4);
  PutU32(&bytes, 1);
  for (uint32_t value : values) PutU32(&bytes, value);
  PutU32(&bytes, Checksum(bytes));
  return bytes;
}
bool DecodeCheckedState(const fs::path& path, const char* magic,
                        size_t value_count, std::vector<uint32_t>* values) {
  std::error_code ec;
  if (!fs::is_regular_file(path, ec) || ec) return false;
  std::ifstream in(path, std::ios::binary);
  const std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (in.bad() || bytes.size() != 12 + value_count * 4 || bytes.compare(0, 4, magic) != 0)
    return false;
  SnapshotReader reader{bytes}; reader.offset = 4;
  if (reader.U32() != 1) return false;
  values->clear();
  for (size_t i = 0; i < value_count; ++i) values->push_back(reader.U32());
  const uint32_t stored = reader.U32();
  return stored == Checksum(bytes.substr(0, bytes.size() - 4));
}
void AtomicCheckedState(const fs::path& root, const char* filename,
                        const char* magic, const std::vector<uint32_t>& values) {
  const fs::path temporary = root / (std::string(filename) + ".tmp");
  const fs::path target = root / filename;
  DurableWrite(temporary, EncodeCheckedState(magic, values));
  fs::rename(temporary, target);
  SyncDir(root);
}
bool ReadSharedGcProgress(const fs::path& root, SharedGcProgress* progress,
                          std::string* error) {
  const fs::path path = root / "shared-gc.state";
  std::error_code ec;
  const bool exists = fs::exists(path, ec);
  if (ec) {
    if (error) *error = "stat shared-gc.state: " + ec.message();
    return false;
  }
  if (!exists) {
    *progress = {};
    return true;
  }
  std::vector<uint32_t> values;
  if (!DecodeCheckedState(path, "M4GC", 2, &values) || values.size() != 2 || values[0] == 0) {
    if (error) *error = "invalid or corrupt shared-gc.state";
    return false;
  }
  progress->reclaimed_below = values[0];
  progress->orphan_reserved_through = values[1];
  return true;
}
void WriteSharedGcProgress(const fs::path& root,
                           const SharedGcProgress& progress) {
  if (progress.reclaimed_below == 0)
    throw std::invalid_argument("shared GC checkpoint floor must be positive");
  AtomicCheckedState(root, "shared-gc.state", "M4GC",
                     {progress.reclaimed_below, progress.orphan_reserved_through});
}
void AppendGenerationWal(const fs::path& root, char kind, uint32_t generation) {
  const std::string body = std::string(1, kind) + " " + std::to_string(generation);
  char checksum[16];
  std::snprintf(checksum, sizeof(checksum), "%08x", Checksum(body));
  const std::string line = body + " " + checksum + "\n";
  const int fd = ::open((root / "bridge.wal").c_str(),
                        O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
  if (fd < 0) throw std::runtime_error("open generation WAL");
  size_t offset = 0;
  while (offset < line.size()) {
    const ssize_t n = ::write(fd, line.data() + offset, line.size() - offset);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) { ::close(fd); throw std::runtime_error("write generation WAL"); }
    offset += static_cast<size_t>(n);
  }
  const bool synced = ::fsync(fd) == 0;
  const int saved_errno = errno;
  ::close(fd);
  if (!synced) { errno = saved_errno; throw std::runtime_error("sync generation WAL"); }
}
struct GenerationWalState { uint32_t highwater{0}; uint32_t committed{0}; };
GenerationWalState RepairAndReadGenerationWal(const fs::path& root) {
  const fs::path path = root / "bridge.wal";
  if (!fs::exists(path)) return {};
  std::ifstream in(path, std::ios::binary);
  const std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (in.bad()) throw std::runtime_error("read generation WAL failed");
  GenerationWalState state;
  size_t start = 0, valid_end = 0;
  while (start < bytes.size()) {
    const size_t newline = bytes.find('\n', start);
    if (newline == std::string::npos) break;  // Only an unterminated tail is repairable.
    const std::string line = bytes.substr(start, newline - start);
    std::string kind; uint32_t generation = 0;
    if (!ValidWalRecord(line, &kind, &generation))
      throw std::runtime_error("corrupt complete generation WAL record");
    state.highwater = std::max(state.highwater, generation);
    if (kind == "P") state.committed = std::max(state.committed, generation);
    start = newline + 1;
    valid_end = start;
  }
  if (valid_end != bytes.size()) {
    fs::resize_file(path, valid_end);
    const int fd = ::open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (fd < 0) throw std::runtime_error("open repaired generation WAL");
    const bool synced = ::fsync(fd) == 0;
    const int saved_errno = errno; ::close(fd);
    if (!synced) { errno = saved_errno; throw std::runtime_error("sync repaired generation WAL"); }
  }
  return state;
}
void TruncateGenerationWal(const fs::path& root) {
  const int fd = ::open((root / "bridge.wal").c_str(),
                        O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) throw std::runtime_error("truncate generation WAL");
  const bool synced = ::fsync(fd) == 0;
  const int saved_errno = errno; ::close(fd);
  if (!synced) { errno = saved_errno; throw std::runtime_error("sync truncated generation WAL"); }
  SyncDir(root);
}
uint64_t GenerationBytes(const fs::path& root) {
  uint64_t bytes = 0;
  for (const auto& entry : fs::directory_iterator(root)) {
    if (!entry.is_directory()) continue;
    const std::string name = entry.path().filename().string();
    if (name.rfind("generation-", 0) == 0)
      bytes += DirectoryBytes(entry.path());
  }
  return bytes;
}
std::vector<fs::path> GenerationDirectories(const fs::path& root) {
  std::vector<fs::path> result;
  for (const auto& entry : fs::directory_iterator(root)) {
    if (!entry.is_directory()) continue;
    if (entry.path().filename().string().rfind("generation-", 0) == 0)
      result.push_back(entry.path());
  }
  return result;
}

class ImmutableGeneration {
 public:
  uint32_t id{0};
  fs::path directory;
  std::vector<Row> rows;
  Native vector;
  std::unique_ptr<m4::Index> fts;
  std::unique_ptr<Coordinator> bridge;
  bool closed{false};

  ~ImmutableGeneration() { (void)Close(); }
  bool Close() {
    if (closed) return true;
    bridge.reset();
    fts.reset();
    closed = vector.Close();
    return closed;
  }
  std::vector<Hit> Hybrid(const std::array<float, kDimension>& query,
                          const std::string& text, size_t topk,
                          const std::function<void()>& after_vector = {}) const {
    if (closed || !bridge) throw std::runtime_error("query on closed generation");
    return bridge->Hybrid(1, query, text, topk, after_vector);
  }
};

struct DeltaVectorShard {
  uint32_t id{0};
  fs::path directory;
  std::vector<Row> rows;
  Native vector;
};

class DeltaVectorSnapshot {
 public:
  uint32_t id{0};
  std::vector<Row> rows;
  std::map<std::string, uint32_t> live_versions;
  std::vector<std::shared_ptr<DeltaVectorShard>> shards;
  bool vector_compacted{false};
  std::unique_ptr<m4::Index> fts;
  std::vector<std::unique_ptr<Coordinator>> vector_bridges;
  std::unique_ptr<Coordinator> bridge;
  fs::path directory;

  std::vector<Hit> SearchVector(const std::array<float, kDimension>& query,
                                size_t topk, const std::string& filter = {},
                                double* native_search_ms = nullptr,
                                std::vector<BackendLockEvent>* lock_acquires = nullptr,
                                double* native_search_cpu_ms = nullptr,
                                std::vector<NativeCallMetric>* native_calls = nullptr) const {
    std::vector<Hit> candidates;
    for (size_t i = 0; i < shards.size(); ++i) {
      const auto& shard = shards[i];
      size_t stale = 0;
      for (const auto& row : shard->rows) {
        const auto live = live_versions.find(row.pk);
        if (live == live_versions.end() || live->second != shard->id) ++stale;
      }
      const size_t wanted = std::min(shard->rows.size(), topk + stale);
      if (wanted == 0) continue;
      BackendLockEvent lock_event;
      auto hits = vector_bridges[i]->SearchVector(shard->id, query, filter, wanted,
                                                    native_search_ms, &lock_event,
                                                    native_search_cpu_ms, native_calls);
      if (lock_acquires) lock_acquires->push_back(lock_event);
      for (auto& hit : hits) {
        const auto live = live_versions.find(hit.pk);
        if (live != live_versions.end() && live->second == shard->id)
          candidates.push_back(std::move(hit));
      }
    }
    std::sort(candidates.begin(), candidates.end(), [](const Hit& a, const Hit& b) {
      return a.score != b.score ? a.score < b.score : a.pk < b.pk;
    });
    candidates.erase(std::unique(candidates.begin(), candidates.end(),
        [](const Hit& a, const Hit& b) { return a.pk == b.pk; }), candidates.end());
    if (candidates.size() > topk) candidates.resize(topk);
    return candidates;
  }

  GenerationRead Hybrid(const std::array<float, kDimension>& query,
                        const std::string& text, size_t topk,
                        const std::function<void()>& after_vector = {},
                        bool capture_cpu_scheduler = false,
                        bool capture_m4_diagnostics = false,
                        bool collect_source_pks = true,
                        const std::string& vector_filter = {},
                        bool collect_m4_counters = true,
                        bool capture_m4_timing = true) const {
    GenerationRead result;
    result.generation = id;
    if (collect_source_pks)
      for (const auto& row : rows) result.source_pks.push_back(row.pk);
    const uint64_t vector_wait_start = capture_cpu_scheduler ? CurrentThreadRunqueueWaitNs() : 0;
    const double vector_cpu_start = capture_cpu_scheduler ? CurrentThreadCpuMs() : 0.0;
    result.stages.vector_start = std::chrono::steady_clock::now();
    const auto vector_hits = SearchVector(query, topk, vector_filter,
        &result.stages.native_vector_search_ms,
        &result.stages.vector_lock_acquires,
        capture_cpu_scheduler ? &result.stages.native_vector_search_cpu_ms : nullptr,
        capture_cpu_scheduler ? &result.stages.native_vector_calls : nullptr);
    result.stages.vector_end = std::chrono::steady_clock::now();
    if (capture_cpu_scheduler) {
      result.stages.vector_stage_cpu_ms = CurrentThreadCpuMs() - vector_cpu_start;
      result.stages.vector_stage_runqueue_wait_ms =
          (CurrentThreadRunqueueWaitNs() - vector_wait_start) / 1e6;
    }
    if (after_vector) after_vector();
    const uint64_t m4_wait_start = capture_cpu_scheduler ? CurrentThreadRunqueueWaitNs() : 0;
    const double m4_cpu_start = capture_cpu_scheduler ? CurrentThreadCpuMs() : 0.0;
    result.stages.m4_start = std::chrono::steady_clock::now();
    result.stages.m4_query_diagnostics_captured = capture_m4_diagnostics;
    result.stages.m4_query_diagnostics.collect_counters = collect_m4_counters;
    result.stages.m4_query_diagnostics.capture_timing = capture_m4_timing;
    const auto fts_hits = bridge->SearchFts(1, text, m4::FilterExpr::All(), topk,
        &result.stages.m4_lock_acquire,
        capture_m4_diagnostics ? &result.stages.m4_query_diagnostics : nullptr);
    result.stages.m4_end = std::chrono::steady_clock::now();
    if (capture_cpu_scheduler) {
      result.stages.m4_search_cpu_ms = CurrentThreadCpuMs() - m4_cpu_start;
      result.stages.m4_stage_runqueue_wait_ms =
          (CurrentThreadRunqueueWaitNs() - m4_wait_start) / 1e6;
    }
    const uint64_t merge_wait_start = capture_cpu_scheduler ? CurrentThreadRunqueueWaitNs() : 0;
    const double merge_cpu_start = capture_cpu_scheduler ? CurrentThreadCpuMs() : 0.0;
    result.stages.merge_start = std::chrono::steady_clock::now();
    std::map<std::string, double> scores;
    for (size_t i = 0; i < vector_hits.size(); ++i)
      scores[vector_hits[i].pk] += 1.0 / (60.0 + i + 1);
    for (size_t i = 0; i < fts_hits.size(); ++i)
      scores[fts_hits[i].pk] += 1.0 / (60.0 + i + 1);
    for (const auto& [pk, score] : scores) result.hits.push_back({pk, score});
    std::sort(result.hits.begin(), result.hits.end(), [](const Hit& a, const Hit& b) {
      return a.score != b.score ? a.score > b.score : a.pk < b.pk;
    });
    if (result.hits.size() > topk) result.hits.resize(topk);
    result.stages.merge_end = std::chrono::steady_clock::now();
    if (capture_cpu_scheduler) {
      result.stages.merge_cpu_ms = CurrentThreadCpuMs() - merge_cpu_start;
      result.stages.merge_runqueue_wait_ms =
          (CurrentThreadRunqueueWaitNs() - merge_wait_start) / 1e6;
    }
    return result;
  }
};

class ImmutableGenerationCoordinator {
 public:
  explicit ImmutableGenerationCoordinator(fs::path root, std::string dictionary,
      size_t max_generations = 3, uint64_t max_generation_bytes = 1ull << 30)
      : root_(std::move(root)), dictionary_(std::move(dictionary)),
        max_generations_(max_generations), max_generation_bytes_(max_generation_bytes) {
    fs::create_directories(root_);
    RecoverAndOpen();
  }
  ~ImmutableGenerationCoordinator() {
    std::lock_guard<std::mutex> lock(writer_mutex_);
    (void)CollectRetiredUnlocked(false);
    std::atomic_store_explicit(&current_, std::shared_ptr<ImmutableGeneration>{},
                               std::memory_order_release);
    retired_.clear();
  }
  ImmutableGenerationCoordinator(const ImmutableGenerationCoordinator&) = delete;
  ImmutableGenerationCoordinator& operator=(const ImmutableGenerationCoordinator&) = delete;

  std::shared_ptr<const ImmutableGeneration> Pin() const {
    if (poisoned_.load(std::memory_order_acquire))
      throw std::runtime_error("generation coordinator requires reopen after uncertain WAL sync");
    return std::atomic_load_explicit(&current_, std::memory_order_acquire);
  }
  uint32_t generation() const {
    auto current = Pin();
    return current ? current->id : 0;
  }
  GenerationRead Hybrid(const std::array<float, kDimension>& query,
                        const std::string& text, size_t topk,
                        const std::function<void()>& after_vector = {}) const {
    auto generation = Pin();
    if (!generation) throw std::runtime_error("no committed generation");
    GenerationRead result;
    result.generation = generation->id;
    result.hits = generation->Hybrid(query, text, topk, after_vector);
    for (const auto& row : generation->rows) result.source_pks.push_back(row.pk);
    return result;
  }
  GenerationRead HybridPinned(const std::shared_ptr<const ImmutableGeneration>& generation,
                              const std::array<float, kDimension>& query,
                              const std::string& text, size_t topk,
                              const std::function<void()>& after_vector = {}) const {
    if (!generation) throw std::runtime_error("null generation pin");
    GenerationRead result;
    result.generation = generation->id;
    for (const auto& row : generation->rows) result.source_pks.push_back(row.pk);
    result.hits = generation->Hybrid(query, text, topk, after_vector);
    return result;
  }

  bool Apply(const std::vector<BatchMutation>& mutations, uint32_t expected_generation,
             const std::string& fault = {}) {
    std::lock_guard<std::mutex> lock(writer_mutex_);
    last_error_.clear();
    if (poisoned_.load(std::memory_order_acquire)) return Fail("coordinator is poisoned");
    auto old = std::atomic_load_explicit(&current_, std::memory_order_acquire);
    const uint32_t old_id = old ? old->id : 0;
    if (old_id != expected_generation) return Fail("stale expected generation");
    if (fault == "crash_before_reserve") ::_exit(79);
    if (metadata_dirty_ && !CheckpointMetadata(old_id, highwater_))
      return Fail("metadata checkpoint pending; refusing another write");
    (void)CollectRetiredUnlocked(false);
    const auto existing_dirs = GenerationDirectories(root_);
    if (existing_dirs.size() >= max_generations_)
      return Fail("BACKPRESSURE: retained generation count limit");
    if (GenerationBytes(root_) >= max_generation_bytes_)
      return Fail("BACKPRESSURE: retained generation byte limit");

    std::map<std::string, Row> live;
    if (old) for (const auto& row : old->rows) live.emplace(row.pk, row);
    for (const auto& mutation : mutations) {
      if (mutation.pk.empty()) return Fail("empty logical PK");
      if (mutation.kind == BatchMutation::Kind::DELETE) {
        if (mutation.row) return Fail("delete mutation unexpectedly carries row");
        live.erase(mutation.pk);
      } else {
        if (!mutation.row || mutation.row->pk != mutation.pk)
          return Fail("upsert mutation PK mismatch");
        live[mutation.pk] = *mutation.row;
      }
    }
    std::vector<Row> snapshot;
    snapshot.reserve(live.size());
    for (auto& [pk, row] : live) { (void)pk; snapshot.push_back(std::move(row)); }
    const uint32_t next = NextGenerationId();
    highwater_ = next;
    try {
      AppendGenerationWal(root_, 'R', next);
    } catch (const std::exception& error) {
      return Fail(std::string("reserve generation failed: ") + error.what());
    }
    if (fault == "crash_after_reserve") ::_exit(80);

    const fs::path staging = root_ / (GenerationDirectoryName(next) + ".staging");
    const fs::path final = GenerationDirectory(root_, next);
    bool durable_publish = false;
    std::error_code remove_error;
    fs::remove_all(staging, remove_error);
    try {
      BuildStage(staging, next, snapshot, fault);
      if (fault == "crash_after_stage") ::_exit(81);
      if (GenerationBytes(root_) > max_generation_bytes_)
        throw std::system_error(ENOSPC, std::generic_category(), "generation disk budget");
      fs::rename(staging, final);
      SyncDir(root_);
      if (fault == "crash_after_rename") ::_exit(82);
      auto prepared = OpenGeneration(final, next, dictionary_);
      retired_.reserve(retired_.size() + (old ? 1u : 0u));
      try {
        AppendGenerationWal(root_, 'P', next);
        durable_publish = true;
      } catch (const std::exception& error) {
        poisoned_.store(true, std::memory_order_release);
        last_error_ = std::string("uncertain publish WAL outcome; reopen required: ") + error.what();
        return false;  // Keep final files: recovery decides from the durable WAL.
      }
      if (fault == "crash_after_publish_wal") ::_exit(83);
      auto previous = std::atomic_exchange_explicit(&current_, prepared,
                                                    std::memory_order_acq_rel);
      if (previous) retired_.push_back(previous);
      if (fault == "crash_after_pointer") ::_exit(84);
      bool metadata_ok = true;
      try {
        AtomicCheckedState(root_, "bridge.manifest", "M4GM", {next});
        AtomicCheckedState(root_, "bridge.sequence", "M4GS", {next, highwater_});
      } catch (const std::exception& error) {
        metadata_ok = false;
        last_error_ = std::string("committed; metadata repair pending: ") + error.what();
      }
      if (metadata_ok) {
        metadata_dirty_ = false;
        try { TruncateGenerationWal(root_); }
        catch (const std::exception& error) {
          metadata_dirty_ = true;
          last_error_ = std::string("committed; WAL checkpoint pending: ") + error.what();
        }
      } else {
        metadata_dirty_ = true;
      }
      if (fault == "crash_after_manifest") ::_exit(85);
      old.reset();
      (void)CollectRetiredUnlocked(false);
      return true;
    } catch (const std::exception& error) {
      if (durable_publish) {
        metadata_dirty_ = true;
        last_error_ = std::string("commit durable; metadata repair pending: ") + error.what();
        return true;
      }
      fs::remove_all(staging, remove_error);
      if (fs::exists(final)) fs::remove_all(final, remove_error);
      return Fail(std::string("generation preparation aborted: ") + error.what());
    }
  }

  bool CollectRetired(bool inject_failure_once = false) {
    std::lock_guard<std::mutex> lock(writer_mutex_);
    if (inject_failure_once) fail_gc_once_ = true;
    return CollectRetiredUnlocked(true);
  }
  size_t retained_generation_count() const {
    std::lock_guard<std::mutex> lock(writer_mutex_);
    return GenerationDirectories(root_).size();
  }
  uint64_t retained_generation_bytes() const { return GenerationBytes(root_); }
  fs::path root() const { return root_; }
  std::string last_error() const { return last_error_; }
  void SetByteLimit(uint64_t value) {
    std::lock_guard<std::mutex> lock(writer_mutex_);
    max_generation_bytes_ = value;
  }

 private:
  bool Fail(std::string message) { last_error_ = std::move(message); return false; }
  uint32_t NextGenerationId() const {
    uint32_t highest = highwater_;
    auto current = std::atomic_load_explicit(&current_, std::memory_order_acquire);
    if (current) highest = std::max(highest, current->id);
    return highest + 1;
  }
  bool CheckpointMetadata(uint32_t committed, uint32_t highwater) {
    try {
      AtomicCheckedState(root_, "bridge.manifest", "M4GM", {committed});
      AtomicCheckedState(root_, "bridge.sequence", "M4GS", {committed, highwater});
      TruncateGenerationWal(root_);
      metadata_dirty_ = false;
      return true;
    } catch (...) { return false; }
  }
  bool CollectRetiredUnlocked(bool report_failure) {
    bool ok = true;
    for (auto it = retired_.begin(); it != retired_.end();) {
      if (it->use_count() != 1) { ++it; continue; }
      if (fail_gc_once_) { fail_gc_once_ = false; ok = false; ++it; continue; }
      if (!(*it)->Close()) { ok = false; ++it; continue; }
      std::error_code ec;
      fs::remove_all((*it)->directory, ec);
      if (ec) { ok = false; ++it; continue; }
      it = retired_.erase(it);
    }
    if (!ok && report_failure) last_error_ = "retired generation GC failed; path retained for retry";
    return ok;
  }
  void BuildStage(const fs::path& staging, uint32_t generation,
                  const std::vector<Row>& rows, const std::string& fault) {
    fs::create_directories(staging);
    const fs::path bridge_root = staging / "bridge";
    fs::create_directories(bridge_root);
    const fs::path fts_path = bridge_root / "fts";
    const bool trace_stages = std::getenv("M4_BRIDGE_STAGE_TIMINGS") != nullptr;
    auto phase_wall = std::chrono::steady_clock::now();
    double fts_open_ms = 0, fts_open_cpu_ms = 0;
    double fts_apply_ms = 0, fts_apply_cpu_ms = 0;
    double fts_flush_ms = 0, fts_flush_cpu_ms = 0;
    double vector_open_ms = 0, vector_open_cpu_ms = 0;
    double vector_upsert_ms = 0, vector_upsert_cpu_ms = 0;
    double vector_flush_ms = 0, vector_flush_cpu_ms = 0;
    double vector_close_ms = 0, vector_close_cpu_ms = 0;
    double finalize_ms = 0, finalize_cpu_ms = 0;
    auto phase_cpu = CurrentThreadCpuMs();
    m4::Options options;
    options.fts_field = "text";
    options.fts_tokenizer_name = "jieba";
    options.jieba_dict_dir = dictionary_;
    options.fsync_wal = true;
    options.cache_filter_candidates =
        std::getenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE") != nullptr;
    std::unique_ptr<m4::Index> fts;
    const auto opened = m4::Index::Open(fts_path.string(), options, &fts);
    if (!opened.ok) throw std::runtime_error("open staged M4 generation: " + opened.message);
    fts_open_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    fts_open_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    constexpr size_t kBatchDocs = 256;
    for (size_t at = 0; at < rows.size(); at += kBatchDocs) {
      const size_t end = std::min(at + kBatchDocs, rows.size());
      std::vector<m4::Mutation> batch;
      batch.reserve(end - at);
      for (size_t i = at; i < end; ++i)
        batch.emplace_back(m4::MutationKind::UPSERT, "1:" + rows[i].pk, M4Doc(rows[i], 1));
      const auto result = fts->ApplyBatch(batch);
      if (!result.ok) throw std::runtime_error("stage FTS batch: " + result.message);
    }
    fts_apply_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    fts_apply_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    if (GenerationBytes(root_) > max_generation_bytes_)
      throw std::system_error(ENOSPC, std::generic_category(), "generation disk budget during FTS build");
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    if (!fts->Flush().ok) throw std::runtime_error("flush staged M4 generation");
    fts_flush_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    fts_flush_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    if (fault == "fail_after_fts") throw std::runtime_error("injected FTS stage failure");
    if (fault == "crash_after_fts") ::_exit(86);

    const fs::path vector_path = bridge_root / "vector";
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    Native vector = OpenVector(vector_path, true);
    vector_open_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    vector_open_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    for (size_t at = 0; at < rows.size(); at += kBatchDocs) {
      const size_t end = std::min(at + kBatchDocs, rows.size());
      std::vector<zvec_doc_t*> docs;
      docs.reserve(end - at);
      for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(rows[i], 1));
      std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
      zvec_write_result_t* results = nullptr;
      size_t result_count = 0;
      const auto code = zvec_collection_upsert_with_results(
          vector.value, refs.data(), refs.size(), &results, &result_count);
      bool success = code == ZVEC_OK && result_count == refs.size();
      for (size_t i = 0; success && i < result_count; ++i)
        success = results[i].code == ZVEC_OK;
      zvec_write_results_free(results, result_count);
      for (auto* doc : docs) zvec_doc_destroy(doc);
      if (!success) throw std::runtime_error("stage native vector batch failed");
    }
    vector_upsert_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    vector_upsert_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    if (GenerationBytes(root_) > max_generation_bytes_)
      throw std::system_error(ENOSPC, std::generic_category(), "generation disk budget during vector build");
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    Need(zvec_collection_flush(vector.value), "flush staged native generation");
    vector_flush_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    vector_flush_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    if (fault == "fail_after_vector")
      throw std::runtime_error("injected native vector stage failure");
    if (fault == "crash_after_vector") ::_exit(87);
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    if (!vector.Close()) throw std::runtime_error("close staged native generation");
    vector_close_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    vector_close_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    phase_wall = std::chrono::steady_clock::now(); phase_cpu = CurrentThreadCpuMs();
    DurableWrite(staging / "snapshot.bin", EncodeSnapshot(generation, rows));
    AtomicManifest(bridge_root, 1);
    SyncTree(staging);
    finalize_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - phase_wall).count();
    finalize_cpu_ms = CurrentThreadCpuMs() - phase_cpu;
    if (trace_stages) {
      std::cerr << std::setprecision(8) << "GEN_STAGE generation=" << generation
                << " rows=" << rows.size()
                << " fts_open_ms=" << fts_open_ms << " fts_open_cpu_ms=" << fts_open_cpu_ms
                << " fts_apply_ms=" << fts_apply_ms << " fts_apply_cpu_ms=" << fts_apply_cpu_ms
                << " fts_flush_ms=" << fts_flush_ms << " fts_flush_cpu_ms=" << fts_flush_cpu_ms
                << " vector_open_ms=" << vector_open_ms << " vector_open_cpu_ms=" << vector_open_cpu_ms
                << " vector_upsert_ms=" << vector_upsert_ms << " vector_upsert_cpu_ms=" << vector_upsert_cpu_ms
                << " vector_flush_ms=" << vector_flush_ms << " vector_flush_cpu_ms=" << vector_flush_cpu_ms
                << " vector_close_ms=" << vector_close_ms << " vector_close_cpu_ms=" << vector_close_cpu_ms
                << " finalize_ms=" << finalize_ms << " finalize_cpu_ms=" << finalize_cpu_ms << '\n';
    }
    if (fault == "disk_full")
      throw std::system_error(ENOSPC, std::generic_category(), "injected ENOSPC before generation publish");
  }
  std::shared_ptr<ImmutableGeneration> OpenGeneration(const fs::path& directory,
      uint32_t generation, const std::string& dictionary) {
    auto rows = DecodeSnapshot(directory / "snapshot.bin", generation);
    const fs::path bridge_root = directory / "bridge";
    Native vector = OpenVector(bridge_root / "vector", false);
    m4::Options options;
    options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
    options.jieba_dict_dir = dictionary; options.fsync_wal = true;
    options.cache_filter_candidates =
        std::getenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE") != nullptr;
    std::unique_ptr<m4::Index> fts;
    const auto status = m4::Index::Open((bridge_root / "fts").string(), options, &fts);
    if (!status.ok) throw std::runtime_error("reopen generation FTS: " + status.message);
    if (NativeDocCount(vector.value) != rows.size() || fts->GetStats().live_docs != rows.size())
      throw std::runtime_error("generation component row-count mismatch");
    auto bridge = std::make_unique<Coordinator>(bridge_root, vector.value, fts.get());
    if (bridge->generation() != 1) throw std::runtime_error("inner generation manifest mismatch");
    auto result = std::make_shared<ImmutableGeneration>();
    result->id = generation; result->directory = directory;
    result->rows = std::move(rows); result->vector = std::move(vector);
    result->fts = std::move(fts); result->bridge = std::move(bridge);
    return result;
  }
  void RecoverAndOpen() {
    const bool has_manifest_file = fs::exists(root_ / "bridge.manifest");
    std::vector<uint32_t> manifest_values, sequence_values;
    const bool manifest_ok = DecodeCheckedState(root_ / "bridge.manifest", "M4GM", 1, &manifest_values);
    const bool sequence_ok = DecodeCheckedState(root_ / "bridge.sequence", "M4GS", 2, &sequence_values);
    const auto wal = RepairAndReadGenerationWal(root_);
    if (has_manifest_file && !manifest_ok && !sequence_ok && wal.committed == 0)
      throw std::runtime_error("corrupt bridge manifest without recoverable commit record");
    const uint32_t manifest_id = manifest_ok ? manifest_values[0] : 0;
    const uint32_t checkpoint_id = sequence_ok ? sequence_values[0] : 0;
    const uint32_t committed = std::max({manifest_id, checkpoint_id, wal.committed});
    highwater_ = std::max({committed, sequence_ok ? sequence_values[1] : 0, wal.highwater});
    if (committed != 0 && !fs::is_directory(GenerationDirectory(root_, committed)))
      throw std::runtime_error("durable committed generation directory missing");
    for (const auto& path : GenerationDirectories(root_)) {
      if (path == GenerationDirectory(root_, committed)) continue;
      std::error_code ec; fs::remove_all(path, ec);
      if (ec) throw std::runtime_error("failed to remove uncommitted/orphan generation: " + ec.message());
    }
    if (!manifest_ok || manifest_id != committed)
      AtomicCheckedState(root_, "bridge.manifest", "M4GM", {committed});
    if (!sequence_ok || checkpoint_id != committed || sequence_values.size() != 2 || sequence_values[1] != highwater_)
      AtomicCheckedState(root_, "bridge.sequence", "M4GS", {committed, highwater_});
    if (fs::exists(root_ / "bridge.wal")) TruncateGenerationWal(root_);
    if (committed != 0) {
      auto generation = OpenGeneration(GenerationDirectory(root_, committed), committed, dictionary_);
      std::atomic_store_explicit(&current_, std::move(generation), std::memory_order_release);
    }
  }

  fs::path root_;
  std::string dictionary_;
  size_t max_generations_;
  uint64_t max_generation_bytes_;
  mutable std::shared_ptr<ImmutableGeneration> current_;
  std::vector<std::shared_ptr<ImmutableGeneration>> retired_;
  mutable std::mutex writer_mutex_;
  uint32_t highwater_{0};
  bool metadata_dirty_{false};
  bool fail_gc_once_{false};
  std::atomic<bool> poisoned_{false};
  std::string last_error_;
};

std::vector<Row> Fixtures();
std::set<std::string> RowPks(const std::vector<Row>& rows);
std::set<std::string> HitPks(const std::vector<Hit>& hits);
std::string EncodeVectorShardCatalog(uint32_t generation, const std::vector<uint32_t>& ids) {
  if (ids.size() > 4096) throw std::runtime_error("vector shard fan-out exceeds catalog limit");
  std::string bytes("M4VC", 4); PutU32(&bytes, 1); PutU32(&bytes, generation);
  PutU32(&bytes, static_cast<uint32_t>(ids.size()));
  for (uint32_t id : ids) PutU32(&bytes, id);
  PutU32(&bytes, Checksum(bytes));
  return bytes;
}
std::vector<uint32_t> DecodeVectorShardCatalog(const fs::path& path, uint32_t generation) {
  std::ifstream in(path, std::ios::binary);
  const std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (in.bad() || bytes.size() < 20 || bytes.compare(0, 4, "M4VC") != 0)
    throw std::runtime_error("invalid vector shard catalog header");
  SnapshotReader reader{bytes}; reader.offset = 4;
  if (reader.U32() != 1 || reader.U32() != generation)
    throw std::runtime_error("vector shard catalog generation mismatch");
  const uint32_t count = reader.U32();
  if (count == 0 || count > 4096 || bytes.size() != 20 + static_cast<size_t>(count) * 4)
    throw std::runtime_error("invalid vector shard catalog count/length");
  std::vector<uint32_t> ids; ids.reserve(count);
  for (uint32_t i = 0; i < count; ++i) ids.push_back(reader.U32());
  if (reader.U32() != Checksum(bytes.substr(0, bytes.size() - 4)))
    throw std::runtime_error("vector shard catalog checksum mismatch");
  if (!std::is_sorted(ids.begin(), ids.end()) ||
      std::adjacent_find(ids.begin(), ids.end()) != ids.end())
    throw std::runtime_error("vector shard catalog is not strictly ordered");
  return ids;
}

struct DeltaBuildMetrics {
  double vector_wall_ms{0};
  double vector_cpu_ms{0};
  double vector_open_wall_ms{0};
  double vector_open_cpu_ms{0};
  double vector_convert_wall_ms{0};
  double vector_convert_cpu_ms{0};
  double vector_upsert_wall_ms{0};
  double vector_upsert_cpu_ms{0};
  double vector_flush_wall_ms{0};
  double vector_flush_cpu_ms{0};
  double generation_metadata_wall_ms{0};
  double generation_metadata_cpu_ms{0};
  double post_m4_sync_wall_ms{0};
  double post_m4_sync_cpu_ms{0};
  double snapshot_finalize_wall_ms{0};
  double snapshot_finalize_cpu_ms{0};
  double m4_wall_ms{0};
  double m4_cpu_ms{0};
  double m4_clone_wall_ms{0};
  double m4_clone_cpu_ms{0};
  double m4_open_wall_ms{0};
  double m4_open_cpu_ms{0};
  m4::OpenDiagnostics m4_open_diagnostics;
  double m4_apply_wall_ms{0};
  double m4_apply_cpu_ms{0};
  double m4_flush_wall_ms{0};
  double m4_flush_cpu_ms{0};
  double compaction_wall_ms{0};
  double compaction_cpu_ms{0};
  uint64_t vector_rows_written{0};
  uint64_t vector_upsert_calls{0};
  uint64_t m4_rows_indexed{0};
  uint64_t m4_apply_batch_calls{0};
  uint64_t m4_mutations_applied{0};
  uint64_t m4_shared_segment_files{0};
  uint64_t m4_copied_state_bytes{0};
  bool compacted{false};
  std::chrono::steady_clock::time_point vector_start_time{}, vector_end_time{};
  std::chrono::steady_clock::time_point vector_open_start_time{}, vector_open_end_time{};
  std::chrono::steady_clock::time_point vector_flush_start_time{}, vector_flush_end_time{};
  std::chrono::steady_clock::time_point generation_metadata_start_time{}, generation_metadata_end_time{};
  std::chrono::steady_clock::time_point m4_clone_start_time{}, m4_clone_end_time{};
  std::chrono::steady_clock::time_point m4_open_start_time{}, m4_open_end_time{};
  std::chrono::steady_clock::time_point m4_apply_start_time{}, m4_apply_end_time{};
  std::chrono::steady_clock::time_point m4_flush_start_time{}, m4_flush_end_time{};
  std::chrono::steady_clock::time_point post_m4_sync_start_time{}, post_m4_sync_end_time{};
  std::chrono::steady_clock::time_point snapshot_finalize_start_time{}, snapshot_finalize_end_time{};
  std::chrono::steady_clock::time_point compaction_start_time{}, compaction_end_time{};
  std::chrono::steady_clock::time_point m4_start_time{}, m4_end_time{};
};
void CloneM4IndexSnapshot(const fs::path& source, const fs::path& destination,
                          DeltaBuildMetrics* metrics) {
  if (!fs::is_directory(source))
    throw std::runtime_error("incremental M4 source generation is missing");
  if (fs::exists(destination) && !fs::is_empty(destination))
    throw std::runtime_error("refusing to overwrite M4 snapshot branch");
  fs::create_directories(destination);
  for (const auto& entry : fs::directory_iterator(source)) {
    if (!entry.is_regular_file())
      throw std::runtime_error("unexpected non-file in M4 snapshot root: " +
                               entry.path().string());
    const fs::path target = destination / entry.path().filename();
    const std::string name = entry.path().filename().string();
    if (name.rfind("run-", 0) == 0 && entry.path().extension() == ".seg") {
      std::error_code ec;
      fs::create_hard_link(entry.path(), target, ec);
      if (ec) throw std::runtime_error("hard-link immutable M4 run " + name + ": " + ec.message());
      if (metrics) ++metrics->m4_shared_segment_files;
    } else {
      fs::copy_file(entry.path(), target, fs::copy_options::none);
      if (metrics) metrics->m4_copied_state_bytes += fs::file_size(target);
    }
  }
  SyncTree(destination, true);
}
std::shared_ptr<DeltaVectorSnapshot> BuildDeltaVectorSnapshot(
    const fs::path& root, uint32_t generation, const std::vector<Row>& rows,
    const std::vector<Row>& vector_delta,
    std::vector<std::shared_ptr<DeltaVectorShard>> shards,
    std::map<std::string, uint32_t> live_versions, const char* dictionary,
    DeltaBuildMetrics* metrics = nullptr,
    const fs::path* m4_base_root = nullptr,
    const std::vector<m4::Mutation>* m4_delta = nullptr,
    bool reuse_analyzer_cache = false,
    bool skip_cached_analyzer_fingerprint_verification = false,
    const std::function<void()>& on_compaction_first_batch = {},
    const std::function<void()>& on_compaction_end = {}) {
  fs::create_directories(root);
  const fs::path generation_root = GenerationDirectory(root, generation);
  if (fs::exists(generation_root) && !fs::is_empty(generation_root))
    throw std::runtime_error("refusing to overwrite committed delta generation directory");
  fs::create_directories(generation_root);
  constexpr size_t kMaxVectorShards = 4;
  bool vector_compacted = false;
  std::vector<Row> rows_to_index = vector_delta;
  if (!vector_delta.empty() && shards.size() >= kMaxVectorShards) {
    vector_compacted = true;
    rows_to_index = rows;
    shards.clear();
    live_versions.clear();
    for (const auto& row : rows) live_versions[row.pk] = generation;
  }
  std::vector<uint32_t> shard_ids;
  for (const auto& shard : shards) shard_ids.push_back(shard->id);
  if (metrics) {
    metrics->vector_rows_written = rows_to_index.size();
    metrics->vector_upsert_calls = (rows_to_index.size() + 255) / 256;
    metrics->m4_rows_indexed = m4_delta ? std::count_if(m4_delta->begin(), m4_delta->end(),
        [](const m4::Mutation& mutation) { return mutation.kind == m4::MutationKind::UPSERT; }) : rows.size();
    metrics->m4_mutations_applied = m4_delta ? m4_delta->size() : rows.size();
    metrics->m4_apply_batch_calls = (metrics->m4_mutations_applied + 255) / 256;
  }
  const auto vector_start = std::chrono::steady_clock::now();
  const double vector_cpu_start = CurrentThreadCpuMs();
  if (metrics) {
    metrics->vector_start_time = vector_start;
    if (vector_compacted) metrics->compaction_start_time = vector_start;
  }
  if (!rows_to_index.empty()) {
    auto shard = std::make_shared<DeltaVectorShard>();
    shard->id = generation;
    shard->directory = root / "vector-shards" / GenerationDirectoryName(generation);
    if (fs::exists(shard->directory) && !fs::is_empty(shard->directory))
      throw std::runtime_error("refusing to overwrite vector shard directory");
    fs::create_directories(shard->directory);
    const fs::path bridge_root = shard->directory / "bridge";
    fs::create_directories(bridge_root);
    AtomicManifest(bridge_root, 1);
    const auto vector_open_start = std::chrono::steady_clock::now();
    const double vector_open_cpu_start = CurrentThreadCpuMs();
    if (metrics) metrics->vector_open_start_time = vector_open_start;
    shard->vector = OpenVector(shard->directory / "vector", true);
    if (metrics) {
      metrics->vector_open_end_time = std::chrono::steady_clock::now();
      metrics->vector_open_wall_ms = std::chrono::duration<double, std::milli>(
          metrics->vector_open_end_time - vector_open_start).count();
      metrics->vector_open_cpu_ms = CurrentThreadCpuMs() - vector_open_cpu_start;
    }
    for (size_t at = 0; at < rows_to_index.size(); at += 256) {
      const size_t end = std::min(at + 256, rows_to_index.size());
      std::vector<zvec_doc_t*> docs;
      docs.reserve(end - at);
      const auto vector_convert_start = std::chrono::steady_clock::now();
      const double vector_convert_cpu_start = CurrentThreadCpuMs();
      for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(rows_to_index[i], generation));
      if (metrics) {
        metrics->vector_convert_wall_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - vector_convert_start).count();
        metrics->vector_convert_cpu_ms += CurrentThreadCpuMs() - vector_convert_cpu_start;
      }
      std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
      zvec_write_result_t* results = nullptr;
      size_t count = 0;
      const auto vector_upsert_start = std::chrono::steady_clock::now();
      const double vector_upsert_cpu_start = CurrentThreadCpuMs();
      const auto code = zvec_collection_upsert_with_results(
          shard->vector.value, refs.data(), refs.size(), &results, &count);
      bool success = code == ZVEC_OK && count == refs.size();
      for (size_t i = 0; success && i < count; ++i) success = results[i].code == ZVEC_OK;
      zvec_write_results_free(results, count);
      if (metrics) {
        metrics->vector_upsert_wall_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - vector_upsert_start).count();
        metrics->vector_upsert_cpu_ms += CurrentThreadCpuMs() - vector_upsert_cpu_start;
      }
      for (auto* doc : docs) zvec_doc_destroy(doc);
      if (!success) throw std::runtime_error("write incremental vector shard");
      if (vector_compacted && at == 0 && on_compaction_first_batch)
        on_compaction_first_batch();
    }
    const auto vector_flush_start = std::chrono::steady_clock::now();
    const double vector_flush_cpu_start = CurrentThreadCpuMs();
    if (metrics) metrics->vector_flush_start_time = vector_flush_start;
    Need(zvec_collection_flush(shard->vector.value), "flush incremental vector shard");
    if (metrics) {
      metrics->vector_flush_end_time = std::chrono::steady_clock::now();
      metrics->vector_flush_wall_ms = std::chrono::duration<double, std::milli>(
          metrics->vector_flush_end_time - vector_flush_start).count();
      metrics->vector_flush_cpu_ms = CurrentThreadCpuMs() - vector_flush_cpu_start;
    }
    shard->rows = std::move(rows_to_index);
    DurableWrite(shard->directory / "shard-rows.bin", EncodeSnapshot(generation, shard->rows));
    shard_ids.push_back(generation);
    shards.push_back(std::move(shard));
  }
  if (metrics) {
    metrics->vector_end_time = std::chrono::steady_clock::now();
    if (vector_compacted) metrics->compaction_end_time = metrics->vector_end_time;
    metrics->vector_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->vector_end_time - vector_start).count();
    metrics->vector_cpu_ms = CurrentThreadCpuMs() - vector_cpu_start;
    metrics->compacted = vector_compacted;
    if (vector_compacted) {
      metrics->compaction_wall_ms = metrics->vector_wall_ms;
      metrics->compaction_cpu_ms = metrics->vector_cpu_ms;
    }
  }
  if (vector_compacted && on_compaction_end) on_compaction_end();

  const auto generation_metadata_start = std::chrono::steady_clock::now();
  const double generation_metadata_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->generation_metadata_start_time = generation_metadata_start;
  std::sort(shard_ids.begin(), shard_ids.end());
  DurableWrite(generation_root / "snapshot.bin", EncodeSnapshot(generation, rows));
  DurableWrite(generation_root / "vector-shards.bin",
               EncodeVectorShardCatalog(generation, shard_ids));
  const fs::path bridge_root = generation_root / "bridge";
  fs::create_directories(bridge_root);
  AtomicManifest(bridge_root, 1);
  if (metrics) {
    metrics->generation_metadata_end_time = std::chrono::steady_clock::now();
    metrics->generation_metadata_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->generation_metadata_end_time - generation_metadata_start).count();
    metrics->generation_metadata_cpu_ms = CurrentThreadCpuMs() - generation_metadata_cpu_start;
  }
  const auto m4_start = std::chrono::steady_clock::now();
  const double m4_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->m4_start_time = m4_start;
  m4::Options options;
  options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  options.cache_filter_candidates =
      std::getenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE") != nullptr;
  options.reuse_analyzer_cache = reuse_analyzer_cache;
  options.skip_cached_analyzer_fingerprint_verification =
      skip_cached_analyzer_fingerprint_verification;
  if (m4_base_root) {
    if (!m4_delta) throw std::runtime_error("incremental M4 branch has no mutation delta");
    const auto clone_start = std::chrono::steady_clock::now();
    const double clone_cpu_start = CurrentThreadCpuMs();
    if (metrics) metrics->m4_clone_start_time = clone_start;
    CloneM4IndexSnapshot(*m4_base_root, generation_root / "fts", metrics);
    if (metrics) {
      metrics->m4_clone_end_time = std::chrono::steady_clock::now();
      metrics->m4_clone_wall_ms = std::chrono::duration<double, std::milli>(
          metrics->m4_clone_end_time - clone_start).count();
      metrics->m4_clone_cpu_ms = CurrentThreadCpuMs() - clone_cpu_start;
    }
  }
  std::unique_ptr<m4::Index> fts;
  const auto open_start = std::chrono::steady_clock::now();
  const double open_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->m4_open_start_time = open_start;
  const auto opened = m4::Index::Open((generation_root / "fts").string(), options, &fts,
                                       metrics ? &metrics->m4_open_diagnostics : nullptr);
  if (metrics) {
    metrics->m4_open_end_time = std::chrono::steady_clock::now();
    metrics->m4_open_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->m4_open_end_time - open_start).count();
    metrics->m4_open_cpu_ms = CurrentThreadCpuMs() - open_cpu_start;
  }
  if (!opened.ok) throw std::runtime_error("open delta candidate FTS: " + opened.message);
  if (reuse_analyzer_cache && m4_base_root && metrics &&
      !metrics->m4_open_diagnostics.analyzer_cache_hit)
    throw std::runtime_error("incremental M4 open missed the expected live analyzer cache");
  const auto apply_start = std::chrono::steady_clock::now();
  const double apply_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->m4_apply_start_time = apply_start;
  if (m4_delta) {
    for (size_t at = 0; at < m4_delta->size(); at += 256) {
      const size_t end = std::min(at + 256, m4_delta->size());
      std::vector<m4::Mutation> batch(m4_delta->begin() + at, m4_delta->begin() + end);
      const auto applied = fts->ApplyBatch(batch);
      if (!applied.ok) throw std::runtime_error("apply incremental M4 branch delta: " + applied.message);
    }
  } else {
    for (size_t at = 0; at < rows.size(); at += 256) {
      const size_t end = std::min(at + 256, rows.size());
      std::vector<m4::Mutation> batch;
      batch.reserve(end - at);
      for (size_t i = at; i < end; ++i)
        batch.emplace_back(m4::MutationKind::UPSERT, "1:" + rows[i].pk, M4Doc(rows[i], 1));
      const auto applied = fts->ApplyBatch(batch);
      if (!applied.ok) throw std::runtime_error("apply delta candidate FTS snapshot: " + applied.message);
    }
  }
  if (metrics) {
    metrics->m4_apply_end_time = std::chrono::steady_clock::now();
    metrics->m4_apply_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->m4_apply_end_time - apply_start).count();
    metrics->m4_apply_cpu_ms = CurrentThreadCpuMs() - apply_cpu_start;
  }
  const auto flush_start = std::chrono::steady_clock::now();
  const double flush_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->m4_flush_start_time = flush_start;
  if (!fts->Flush().ok) throw std::runtime_error("flush delta candidate FTS snapshot");
  if (metrics) {
    metrics->m4_flush_end_time = std::chrono::steady_clock::now();
    metrics->m4_flush_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->m4_flush_end_time - flush_start).count();
    metrics->m4_flush_cpu_ms = CurrentThreadCpuMs() - flush_cpu_start;
  }
  if (fts->GetStats().live_docs != rows.size())
    throw std::runtime_error("M4 generation live-doc count differs from snapshot rows");
  if (metrics) {
    metrics->m4_end_time = std::chrono::steady_clock::now();
    metrics->m4_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->m4_end_time - m4_start).count();
    metrics->m4_cpu_ms = CurrentThreadCpuMs() - m4_cpu_start;
  }
  const auto post_m4_sync_start = std::chrono::steady_clock::now();
  const double post_m4_sync_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->post_m4_sync_start_time = post_m4_sync_start;
  SyncTree(generation_root);
  const fs::path new_shard_dir = root / "vector-shards" / GenerationDirectoryName(generation);
  if (fs::is_directory(new_shard_dir)) SyncTree(new_shard_dir);
  SyncDir(root);
  if (metrics) {
    metrics->post_m4_sync_end_time = std::chrono::steady_clock::now();
    metrics->post_m4_sync_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->post_m4_sync_end_time - post_m4_sync_start).count();
    metrics->post_m4_sync_cpu_ms = CurrentThreadCpuMs() - post_m4_sync_cpu_start;
  }
  if (shards.empty()) throw std::runtime_error("delta snapshot must own at least one vector shard");
  const auto snapshot_finalize_start = std::chrono::steady_clock::now();
  const double snapshot_finalize_cpu_start = CurrentThreadCpuMs();
  if (metrics) metrics->snapshot_finalize_start_time = snapshot_finalize_start;
  auto snapshot = std::make_shared<DeltaVectorSnapshot>();
  snapshot->id = generation;
  snapshot->directory = generation_root;
  snapshot->rows = rows;
  snapshot->live_versions = std::move(live_versions);
  snapshot->shards = std::move(shards);
  snapshot->vector_compacted = vector_compacted;
  snapshot->fts = std::move(fts);
  snapshot->bridge = std::make_unique<Coordinator>(bridge_root,
      snapshot->shards.front()->vector.value, snapshot->fts.get());
  for (const auto& shard : snapshot->shards) {
    snapshot->vector_bridges.push_back(std::make_unique<Coordinator>(
        shard->directory / "bridge", shard->vector.value, snapshot->fts.get()));
  }
  if (metrics) {
    metrics->snapshot_finalize_end_time = std::chrono::steady_clock::now();
    metrics->snapshot_finalize_wall_ms = std::chrono::duration<double, std::milli>(
        metrics->snapshot_finalize_end_time - snapshot_finalize_start).count();
    metrics->snapshot_finalize_cpu_ms = CurrentThreadCpuMs() - snapshot_finalize_cpu_start;
  }
  return snapshot;
}
std::shared_ptr<DeltaVectorSnapshot> OpenDeltaVectorSnapshot(
    const fs::path& root, uint32_t generation, const char* dictionary) {
  const fs::path generation_root = GenerationDirectory(root, generation);
  auto rows = DecodeSnapshot(generation_root / "snapshot.bin", generation);
  const auto shard_ids = DecodeVectorShardCatalog(
      generation_root / "vector-shards.bin", generation);
  std::vector<std::shared_ptr<DeltaVectorShard>> shards;
  std::map<std::string, uint32_t> live_versions;
  for (uint32_t shard_id : shard_ids) {
    auto shard = std::make_shared<DeltaVectorShard>();
    shard->id = shard_id;
    shard->directory = root / "vector-shards" / GenerationDirectoryName(shard_id);
    shard->rows = DecodeSnapshot(shard->directory / "shard-rows.bin", shard_id);
    shard->vector = OpenVector(shard->directory / "vector", false);
    if (NativeDocCount(shard->vector.value) != shard->rows.size())
      throw std::runtime_error("vector shard metadata/index row count mismatch");
    for (const auto& row : shard->rows) live_versions[row.pk] = shard_id;
    shards.push_back(std::move(shard));
  }
  std::set<std::string> current_pks;
  for (const auto& row : rows) current_pks.insert(row.pk);
  for (auto it = live_versions.begin(); it != live_versions.end();) {
    if (current_pks.count(it->first) == 0) it = live_versions.erase(it);
    else ++it;
  }
  for (const auto& row : rows) {
    if (live_versions.count(row.pk) == 0)
      throw std::runtime_error("current row has no vector shard version");
  }

  const fs::path bridge_root = generation_root / "bridge";
  m4::Options options;
  options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto opened = m4::Index::Open((generation_root / "fts").string(), options, &fts);
  if (!opened.ok) throw std::runtime_error("reopen delta candidate FTS: " + opened.message);
  if (fts->GetStats().live_docs != rows.size())
    throw std::runtime_error("current FTS snapshot row count mismatch");
  auto snapshot = std::make_shared<DeltaVectorSnapshot>();
  snapshot->id = generation; snapshot->directory = generation_root;
  snapshot->rows = std::move(rows); snapshot->live_versions = std::move(live_versions);
  snapshot->shards = std::move(shards); snapshot->fts = std::move(fts);
  snapshot->bridge = std::make_unique<Coordinator>(bridge_root,
      snapshot->shards.front()->vector.value, snapshot->fts.get());
  for (const auto& shard : snapshot->shards)
    snapshot->vector_bridges.push_back(std::make_unique<Coordinator>(
        shard->directory / "bridge", shard->vector.value, snapshot->fts.get()));
  return snapshot;
}
size_t CollectDeltaRetired(
    const fs::path& root, const std::shared_ptr<const DeltaVectorSnapshot>& current,
    std::vector<std::weak_ptr<const DeltaVectorSnapshot>>* retired) {
  if (!current || !retired) throw std::runtime_error("delta retirement requires current snapshot and registry");
  std::set<uint32_t> keep_generations{current->id};
  std::set<uint32_t> keep_shards;
  for (const auto& shard : current->shards) keep_shards.insert(shard->id);
  size_t pinned = 0;
  std::vector<std::weak_ptr<const DeltaVectorSnapshot>> live_retired;
  for (const auto& weak : *retired) {
    auto snapshot = weak.lock();
    if (!snapshot) continue;
    ++pinned;
    keep_generations.insert(snapshot->id);
    for (const auto& shard : snapshot->shards) keep_shards.insert(shard->id);
    live_retired.push_back(snapshot);
  }
  for (const auto& path : GenerationDirectories(root)) {
    uint32_t id = 0;
    try { id = static_cast<uint32_t>(std::stoul(path.filename().string().substr(11))); }
    catch (...) { throw std::runtime_error("invalid delta generation directory name during GC"); }
    if (keep_generations.count(id) == 0) {
      std::error_code ec; fs::remove_all(path, ec);
      if (ec) throw std::runtime_error("remove retired delta snapshot: " + ec.message());
    }
  }
  const fs::path shard_root = root / "vector-shards";
  if (fs::exists(shard_root)) for (const auto& path : fs::directory_iterator(shard_root)) {
    if (!path.is_directory()) continue;
    const auto name = path.path().filename().string();
    if (name.rfind("generation-", 0) != 0) continue;
    uint32_t id = 0;
    try { id = static_cast<uint32_t>(std::stoul(name.substr(11))); }
    catch (...) { throw std::runtime_error("invalid delta shard directory name during GC"); }
    if (keep_shards.count(id) == 0) {
      std::error_code ec; fs::remove_all(path.path(), ec);
      if (ec) throw std::runtime_error("remove retired vector shard: " + ec.message());
    }
  }
  *retired = std::move(live_retired);
  return pinned;
}
bool DeltaCanPublishWithinLimit(
    const std::shared_ptr<const DeltaVectorSnapshot>& current,
    const std::vector<std::weak_ptr<const DeltaVectorSnapshot>>& retired,
    bool current_has_reader, size_t max_pinned_snapshots) {
  if (!current || max_pinned_snapshots < 2) return false;
  size_t pinned = 0;
  for (const auto& weak : retired) if (!weak.expired()) ++pinned;
  return pinned + (current_has_reader ? 1u : 0u) + 1 <= max_pinned_snapshots;
}
void PublishDeltaGeneration(const fs::path& root, uint32_t generation,
                           uint32_t highwater) {
  AppendGenerationWal(root, 'P', generation);
  AtomicCheckedState(root, "bridge.manifest", "M4GM", {generation});
  AtomicCheckedState(root, "bridge.sequence", "M4GS", {generation, highwater});
  TruncateGenerationWal(root);
}
std::shared_ptr<DeltaVectorSnapshot> CommitDeltaOne(
    const fs::path& root, const std::shared_ptr<const DeltaVectorSnapshot>& current,
    const char* dictionary) {
  if (!current || current->rows.empty()) throw std::runtime_error("delta commit requires a live snapshot");
  const uint32_t generation = current->id + 1;
  auto rows = current->rows;
  Row updated = rows.front();
  updated.text += " delta update " + std::to_string(generation);
  updated.vector[0] += 0.03125f * static_cast<float>(generation);
  rows.front() = updated;
  auto versions = current->live_versions;
  versions[updated.pk] = generation;
  auto shards = current->shards;
  AppendGenerationWal(root, 'R', generation);
  auto next = BuildDeltaVectorSnapshot(root, generation, rows, {updated},
      std::move(shards), std::move(versions), dictionary);
  PublishDeltaGeneration(root, generation, generation);
  return next;
}
std::shared_ptr<DeltaVectorSnapshot> RecoverDeltaVectorSnapshot(
    const fs::path& root, const char* dictionary, uint32_t* highwater_out = nullptr) {
  std::vector<uint32_t> manifest_values, sequence_values;
  const bool manifest_ok = DecodeCheckedState(root / "bridge.manifest", "M4GM", 1, &manifest_values);
  const bool sequence_ok = DecodeCheckedState(root / "bridge.sequence", "M4GS", 2, &sequence_values);
  const auto wal = RepairAndReadGenerationWal(root);
  const uint32_t committed = std::max({manifest_ok ? manifest_values[0] : 0u,
      sequence_ok ? sequence_values[0] : 0u, wal.committed});
  const uint32_t highwater = std::max({committed, sequence_ok ? sequence_values[1] : 0u,
      wal.highwater});
  if (committed == 0 || !fs::is_directory(GenerationDirectory(root, committed)))
    throw std::runtime_error("no durable delta generation to recover");
  const auto shard_ids = DecodeVectorShardCatalog(
      GenerationDirectory(root, committed) / "vector-shards.bin", committed);
  const std::set<uint32_t> keep(shard_ids.begin(), shard_ids.end());
  for (const auto& path : GenerationDirectories(root)) if (path != GenerationDirectory(root, committed)) {
    std::error_code ec; fs::remove_all(path, ec);
    if (ec) throw std::runtime_error("remove orphan delta generation: " + ec.message());
  }
  const fs::path shard_root = root / "vector-shards";
  if (fs::exists(shard_root)) for (const auto& path : fs::directory_iterator(shard_root)) {
    if (!path.is_directory()) continue;
    const std::string name = path.path().filename().string();
    if (name.rfind("generation-", 0) != 0) continue;
    uint32_t id = 0;
    try { id = static_cast<uint32_t>(std::stoul(name.substr(11))); }
    catch (...) { throw std::runtime_error("invalid vector shard directory name"); }
    if (keep.count(id) == 0) {
      std::error_code ec; fs::remove_all(path.path(), ec);
      if (ec) throw std::runtime_error("remove orphan vector shard: " + ec.message());
    }
  }
  if (!manifest_ok || manifest_values[0] != committed)
    AtomicCheckedState(root, "bridge.manifest", "M4GM", {committed});
  if (!sequence_ok || sequence_values[0] != committed || sequence_values[1] != highwater)
    AtomicCheckedState(root, "bridge.sequence", "M4GS", {committed, highwater});
  if (fs::exists(root / "bridge.wal")) TruncateGenerationWal(root);
  if (highwater_out) *highwater_out = highwater;
  return OpenDeltaVectorSnapshot(root, committed, dictionary);
}
int RunDeltaVectorCrashChild(const fs::path& root, const char* dictionary,
                             const std::string& stage) {
  Need(zvec_initialize(nullptr), "initialize delta-vector crash child");
  uint32_t highwater = 0;
  auto current = RecoverDeltaVectorSnapshot(root, dictionary, &highwater);
  const uint32_t next_generation = highwater + 1;
  if (next_generation <= current->id) throw std::runtime_error("delta crash child generation highwater regressed");
  auto rows = current->rows;
  Row updated = rows.front();
  updated.text = "delta marker crash generation " + std::to_string(next_generation);
  updated.vector[0] += static_cast<float>(next_generation);
  rows.front() = updated;
  auto versions = current->live_versions;
  versions[updated.pk] = next_generation;
  auto shards = current->shards;
  AppendGenerationWal(root, 'R', next_generation);
  auto staged = BuildDeltaVectorSnapshot(root, next_generation, rows, {updated},
      std::move(shards), std::move(versions), dictionary);
  (void)staged;
  if (stage == "after_stage") ::_exit(86);
  if (stage == "after_publish_wal") {
    staged.reset();  // Close/reopen boundary is part of the durable-stage check.
    AppendGenerationWal(root, 'P', next_generation);
    ::_exit(87);
  }
  return 92;
}
int SpawnDeltaVectorCrash(const char* executable, const fs::path& root,
                          const char* dictionary, const std::string& stage) {
  const pid_t child = ::fork();
  if (child == 0) {
    ::execl(executable, executable, "--delta-crash-child", root.c_str(), dictionary,
            stage.c_str(), static_cast<char*>(nullptr));
    ::_exit(127);
  }
  if (child < 0) throw std::runtime_error("fork delta-vector crash child");
  int status = 0;
  if (::waitpid(child, &status, 0) != child) throw std::runtime_error("wait delta-vector crash child");
  return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}
int RunVectorDeltaCandidate(const fs::path& root, const char* dictionary,
                            const char* executable) {
  if (fs::exists(root) && !fs::is_empty(root))
    throw std::runtime_error("vector delta candidate root must be fresh");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize vector delta candidate");
  const auto base_rows = Fixtures();
  std::map<std::string, uint32_t> base_versions;
  for (const auto& row : base_rows) base_versions[row.pk] = 1;
  std::shared_ptr<DeltaVectorSnapshot> base;
  std::shared_ptr<const DeltaVectorSnapshot> current;
  {
    AppendGenerationWal(root, 'R', 1);
    base = BuildDeltaVectorSnapshot(root, 1, base_rows, base_rows, {},
                                    std::move(base_versions), dictionary);
    PublishDeltaGeneration(root, 1, 1);
    std::atomic_store_explicit(&current,
        std::static_pointer_cast<const DeltaVectorSnapshot>(base), std::memory_order_release);
  }
  auto pinned = std::atomic_load_explicit(&current, std::memory_order_acquire);
  std::mutex gate_mutex;
  std::condition_variable gate;
  bool reader_pinned_at_vector_fts_boundary = false, release_reader = false;
  GenerationRead old_read;
  std::thread reader([&] {
    old_read = pinned->Hybrid(base_rows.front().vector, "database parse", 100, [&] {
      std::unique_lock<std::mutex> lock(gate_mutex);
      reader_pinned_at_vector_fts_boundary = true;
      gate.notify_all();
      gate.wait(lock, [&] { return release_reader; });
    });
  });
  {
    std::unique_lock<std::mutex> lock(gate_mutex);
    if (!gate.wait_for(lock, std::chrono::seconds(10), [&] {
          return reader_pinned_at_vector_fts_boundary;
        })) throw std::runtime_error("delta reader did not reach vector/FTS boundary");
  }

  auto next_rows = base_rows;
  Row updated = next_rows.front();
  updated.text = "delta marker updated request timeout";
  updated.vector = {-1.0f, 0.0f, 1.0f, 0.0f};
  Row added = next_rows.back();
  added.pk = "fragment-delta-added"; added.file_id = 1001;
  added.text = "delta marker inserted json handler";
  added.vector = {0.5f, 0.25f, 0.0f, 0.0f};
  next_rows.erase(next_rows.begin() + 1);
  next_rows.front() = updated;
  next_rows.push_back(added);
  std::map<std::string, uint32_t> next_versions = pinned->live_versions;
  next_versions[updated.pk] = 2;
  next_versions.erase(base_rows[1].pk);
  next_versions[added.pk] = 2;
  auto next_shards = pinned->shards;
  AppendGenerationWal(root, 'R', 2);
  auto next = BuildDeltaVectorSnapshot(root, 2, next_rows,
      {updated, added}, std::move(next_shards), std::move(next_versions), dictionary);
  PublishDeltaGeneration(root, 2, 2);
  std::atomic_store_explicit(&current,
      std::static_pointer_cast<const DeltaVectorSnapshot>(next), std::memory_order_release);
  {
    std::lock_guard<std::mutex> lock(gate_mutex);
    release_reader = true;
  }
  gate.notify_all();
  reader.join();

  Check(old_read.generation == 1 && HitPks(old_read.hits) == RowPks(base_rows),
        "delta reader combines FTS/vector from the pinned old snapshot across concurrent publish");
  const auto new_read = next->Hybrid(base_rows.front().vector, "delta marker", 100);
  Check(new_read.generation == 2 && HitPks(new_read.hits) == RowPks(next_rows),
        "delta snapshot combines full M4 snapshot with vector base+delta and tombstone filtering");
  Check(next->SearchVector(updated.vector, next_rows.size()).size() == next_rows.size(),
        "top-K refill removes stale and deleted base-shard hits without underfilling");
  const fs::path oracle_root = root / "full-vector-oracle";
  fs::create_directories(oracle_root / "bridge");
  AtomicManifest(oracle_root / "bridge", 1);
  Native oracle_vector = OpenVector(oracle_root / "vector", true);
  std::vector<zvec_doc_t*> oracle_docs;
  for (const auto& row : next_rows) oracle_docs.push_back(VectorDoc(row, 1));
  std::vector<const zvec_doc_t*> oracle_refs(oracle_docs.begin(), oracle_docs.end());
  zvec_write_result_t* oracle_results = nullptr; size_t oracle_count = 0;
  auto oracle_code = zvec_collection_upsert_with_results(oracle_vector.value,
      oracle_refs.data(), oracle_refs.size(), &oracle_results, &oracle_count);
  bool oracle_ok = oracle_code == ZVEC_OK && oracle_count == oracle_refs.size();
  for (size_t i = 0; oracle_ok && i < oracle_count; ++i)
    oracle_ok = oracle_results[i].code == ZVEC_OK;
  zvec_write_results_free(oracle_results, oracle_count);
  for (auto* doc : oracle_docs) zvec_doc_destroy(doc);
  Check(oracle_ok && zvec_collection_flush(oracle_vector.value) == ZVEC_OK,
        "build full-current-vector oracle for global Top-K comparisons");
  Coordinator oracle_bridge(oracle_root / "bridge", oracle_vector.value, next->fts.get());
  const std::vector<std::array<float, kDimension>> queries = {
      updated.vector, base_rows[5].vector, {0.1f, 0.2f, 0.3f, 0.0f}};
  for (const auto& query : queries) for (size_t k : {size_t{1}, size_t{3}, size_t{7}}) {
    const auto candidate = next->SearchVector(query, k);
    const auto oracle = oracle_bridge.SearchVector(1, query, "", k);
    std::map<std::string, double> candidate_scores, oracle_scores;
    for (const auto& hit : candidate) candidate_scores[hit.pk] = hit.score;
    for (const auto& hit : oracle) oracle_scores[hit.pk] = hit.score;
    bool equivalent = candidate_scores.size() == oracle_scores.size();
    for (const auto& [pk, score] : oracle_scores) {
      const auto found = candidate_scores.find(pk);
      equivalent = equivalent && found != candidate_scores.end() &&
          std::abs(found->second - score) < 1e-5;
    }
    Check(equivalent, "base+delta global Top-K equals full native index for k=" + std::to_string(k));
  }
  const std::string scalar_filter = "file_name LIKE 'generated-%'";
  const auto candidate_filtered = next->SearchVector(base_rows[1].vector, 7, scalar_filter);
  const auto oracle_filtered = oracle_bridge.SearchVector(1, base_rows[1].vector, scalar_filter, 7);
  Check(HitPks(candidate_filtered) == HitPks(oracle_filtered),
        "base+delta applies scalar filter before Top-K and refills stale candidates");
  const auto fts_hits = next->bridge->SearchFts(1, "delta marker", m4::FilterExpr::All(), 10);
  Check(fts_hits.size() == 2 && HitPks(fts_hits) ==
        std::set<std::string>{updated.pk, added.pk},
        "current M4 FTS snapshot contains updated/new rows and excludes tombstone");

  oracle_vector.Close();
  std::atomic_store_explicit(&current, std::shared_ptr<const DeltaVectorSnapshot>{},
                             std::memory_order_release);
  pinned.reset(); next.reset(); base.reset();
  Need(zvec_shutdown(), "shutdown vector delta candidate before recovery");
  Need(zvec_initialize(nullptr), "reinitialize vector delta candidate for recovery");
  uint32_t recovered_highwater = 0;
  auto recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 2 && recovered_highwater == 2 &&
        RowPks(recovered->rows) == RowPks(next_rows),
        "durable manifest/catalog reopen restores the committed cross-component snapshot");
  Check(HitPks(recovered->Hybrid(updated.vector, "delta marker", 100).hits) ==
        RowPks(next_rows), "reopened delta shards and M4 snapshot remain query-consistent");
  recovered.reset();
  Need(zvec_shutdown(), "shutdown recovered vector delta candidate");
  Check(SpawnDeltaVectorCrash(executable, root, dictionary, "after_stage") == 86,
        "child crashes after durable stage but before WAL publish");
  Need(zvec_initialize(nullptr), "reinitialize after pre-publish crash");
  recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 2 && recovered_highwater == 3 &&
        !fs::exists(GenerationDirectory(root, 3)) &&
        !fs::exists(root / "vector-shards" / GenerationDirectoryName(3)),
        "recovery ignores and collects an uncommitted staged FTS/vector generation");
  recovered.reset();
  Need(zvec_shutdown(), "shutdown after pre-publish crash recovery");
  Check(SpawnDeltaVectorCrash(executable, root, dictionary, "after_publish_wal") == 87,
        "child crashes after durable publish WAL record");
  Need(zvec_initialize(nullptr), "reinitialize after publish-WAL crash");
  recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 4 && recovered_highwater == 4,
        "recovery promotes WAL-committed generation after crash before manifest checkpoint");
  const auto crash_marker_hits = recovered->bridge->SearchFts(
      1, "delta marker", m4::FilterExpr::All(), 10);
  Check(std::any_of(crash_marker_hits.begin(), crash_marker_hits.end(),
        [&](const Hit& hit) { return hit.pk == updated.pk; }),
        "WAL-promoted recovery exposes the staged FTS row paired with its vector delta");
  auto generation5 = CommitDeltaOne(root, recovered, dictionary);
  Check(generation5->shards.size() == 4 && !generation5->vector_compacted,
        "delta fan-out grows only to the configured four-shard debt bound");
  generation5.reset(); recovered.reset();
  Need(zvec_shutdown(), "shutdown before compaction crash-boundary tests");
  Check(SpawnDeltaVectorCrash(executable, root, dictionary, "after_stage") == 86,
        "compaction child crashes after staging the compacted vector base but before WAL publish");
  Need(zvec_initialize(nullptr), "reinitialize after staged compaction crash");
  recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 5 && recovered_highwater == 6 && recovered->shards.size() == 4 &&
        !fs::exists(GenerationDirectory(root, 6)) &&
        !fs::exists(root / "vector-shards" / GenerationDirectoryName(6)),
        "recovery keeps the four-shard committed snapshot and collects staged compaction output");
  recovered.reset();
  Need(zvec_shutdown(), "shutdown before WAL-committed compaction crash");
  Check(SpawnDeltaVectorCrash(executable, root, dictionary, "after_publish_wal") == 87,
        "compaction child crashes after durable publish WAL but before manifest checkpoint");
  Need(zvec_initialize(nullptr), "reinitialize after WAL-committed compaction crash");
  recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 7 && recovered_highwater == 7 &&
        recovered->shards.size() == 1 && recovered->shards.front()->rows.size() == recovered->rows.size(),
        "recovery promotes compacted base and matching M4 snapshot from durable publish WAL");
  Check(HitPks(recovered->Hybrid(updated.vector, "delta marker", 100).hits) ==
        RowPks(recovered->rows), "recovered compacted base preserves cross-component snapshot results");
  std::vector<std::weak_ptr<const DeltaVectorSnapshot>> retired_snapshots;
  for (int i = 0; i < 3; ++i) {
    auto old = recovered;
    recovered = CommitDeltaOne(root, recovered, dictionary);
    retired_snapshots.emplace_back(old);
    old.reset();
    (void)CollectDeltaRetired(root, recovered, &retired_snapshots);
  }
  Check(recovered->shards.size() == 4,
        "recovered compacted base can accrue bounded delta shards again");
  auto pinned_generation = recovered;
  std::atomic<bool> old_reader_at_vector{false}, resume_old_reader{false};
  std::vector<Hit> pinned_hybrid;
  std::thread pinned_reader([&] {
    pinned_hybrid = pinned_generation->Hybrid(updated.vector, "delta marker", 100, [&] {
      old_reader_at_vector.store(true, std::memory_order_release);
      while (!resume_old_reader.load(std::memory_order_acquire)) std::this_thread::yield();
    }).hits;
  });
  while (!old_reader_at_vector.load(std::memory_order_acquire)) std::this_thread::yield();
  retired_snapshots.emplace_back(pinned_generation);
  recovered = CommitDeltaOne(root, recovered, dictionary);  // fifth shard request compacts
  resume_old_reader.store(true, std::memory_order_release);
  pinned_reader.join();
  Check(recovered->vector_compacted && recovered->shards.size() == 1 &&
        recovered->shards.front()->rows.size() == recovered->rows.size(),
        "fifth-shard maintenance publishes one compacted immutable base");
  Check(HitPks(pinned_hybrid) == RowPks(pinned_generation->rows),
        "reader pinned before compaction completes on its coherent old vector+M4 snapshot");
  Check(HitPks(recovered->Hybrid(updated.vector, "delta marker", 100).hits) ==
        RowPks(recovered->rows), "new reader sees only the compacted vector+M4 snapshot");
  Check(CollectDeltaRetired(root, recovered, &retired_snapshots) == 1 &&
        fs::exists(GenerationDirectory(root, pinned_generation->id)),
        "retirement retains old FTS and vector shards while a reader pins the generation");
  auto current_generation_pin = recovered;
  Check(!DeltaCanPublishWithinLimit(recovered, retired_snapshots, true, 2),
        "retention cap backpressures another publish while current and retired snapshots are pinned");
  current_generation_pin.reset();
  Check(DeltaCanPublishWithinLimit(recovered, retired_snapshots, false, 2),
        "releasing current-generation reader restores bounded publish capacity");
  const uint32_t pinned_generation_id = pinned_generation->id;
  pinned_generation.reset();
  Check(CollectDeltaRetired(root, recovered, &retired_snapshots) == 0 &&
        !fs::exists(GenerationDirectory(root, pinned_generation_id)),
        "retirement collects old FTS and unreferenced vector shards after final reader release");
  recovered.reset();
  Need(zvec_shutdown(), "shutdown after pinned-reader compaction");
  Need(zvec_initialize(nullptr), "reinitialize after pinned-reader compaction");
  recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 11 && recovered_highwater == 11 && recovered->shards.size() == 1,
        "recovery retains the latest compacted snapshot after pinned reader release");
  recovered.reset();
  Need(zvec_shutdown(), "shutdown recovered compacted vector base");
  std::cout << "VECTOR_DELTA_CANDIDATE checks=" << checks << " failures=" << failures << '\n';
  return failures == 0 ? 0 : 1;
}

std::vector<Row> Fixtures() {
  std::vector<Row> rows;
  for (uint32_t i = 0; i < 24; ++i) {
    Row r; r.pk = "fragment-" + std::to_string(i); r.file_id = 100 + i;
    r.text = i % 2 ? "json request timeout handler" : "database parse cache response";
    r.file_name = i % 2 ? "worker-log.log" : "generated-cache.json";
    r.symbol_type = i % 3 ? "function" : "class"; r.directories = {i % 2 ? 17u : 21u};
    r.vector = {static_cast<float>(i), 1.0f, static_cast<float>(i % 3), 0.0f}; rows.push_back(std::move(r));
  }
  return rows;
}
std::set<std::string> RowPks(const std::vector<Row>& rows) {
  std::set<std::string> result;
  for (const auto& row : rows) result.insert(row.pk);
  return result;
}
std::set<std::string> HitPks(const std::vector<Hit>& hits) {
  std::set<std::string> result;
  for (const auto& hit : hits) result.insert(hit.pk);
  return result;
}
BatchMutation Upsert(Row row) {
  BatchMutation mutation; mutation.kind = BatchMutation::Kind::UPSERT;
  mutation.pk = row.pk; mutation.row = std::move(row); return mutation;
}
BatchMutation Delete(const std::string& pk) {
  BatchMutation mutation; mutation.kind = BatchMutation::Kind::DELETE; mutation.pk = pk; return mutation;
}
void CheckGenerationRead(const GenerationRead& read, const std::string& label) {
  const std::set<std::string> source(read.source_pks.begin(), read.source_pks.end());
  Check(source.size() == read.source_pks.size() && HitPks(read.hits) == source,
        label + " returns a single pinned FTS/vector row set");
}
int GenerationCrashChild(const fs::path& root, const char* dictionary,
                         const std::string& stage) {
  Need(zvec_initialize(nullptr), "generation crash child initialize");
  ImmutableGenerationCoordinator coordinator(root, dictionary);
  auto current = coordinator.Pin();
  if (!current || current->rows.empty()) throw std::runtime_error("crash child has no committed generation");
  Row update = current->rows.front();
  update.text = "crash update marker " + stage;
  update.vector[0] += 1000.0f;
  (void)coordinator.Apply({Upsert(std::move(update))}, current->id, stage);
  return 90;
}
int SpawnGenerationCrash(const char* executable, const fs::path& root,
                         const char* dictionary, const std::string& stage) {
  const pid_t child = ::fork();
  if (child == 0) {
    ::execl(executable, executable, "--generation-crash-child", root.c_str(), dictionary,
            stage.c_str(), static_cast<char*>(nullptr));
    ::_exit(127);
  }
  if (child < 0) throw std::runtime_error("fork generation crash child");
  int status = 0;
  if (::waitpid(child, &status, 0) != child) throw std::runtime_error("wait generation crash child");
  return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}
int RunGenerationLifecycle(const fs::path& root, const char* dictionary,
                           const char* executable) {
  if (fs::exists(root) && !fs::is_empty(root))
    throw std::runtime_error("generation lifecycle root must be empty; refusing to remove existing data");
  fs::create_directories(root);
  auto base = Fixtures();
  std::vector<Row> expected_rows;
  uint32_t last_generation = 0;
  Need(zvec_initialize(nullptr), "initialize immutable generation tests");
  {
    ImmutableGenerationCoordinator coordinator(root, dictionary, 3, 1ull << 30);
    std::vector<BatchMutation> initial;
    for (const auto& row : base) initial.push_back(Upsert(row));
    if (!coordinator.Apply(initial, 0))
      throw std::runtime_error("publish initial immutable base generation: " + coordinator.last_error());
    Check(true, "publish initial immutable base generation");
    auto old_pin = coordinator.Pin();
    Check(old_pin && old_pin->id == 1 && old_pin->rows.size() == base.size(),
          "initial generation snapshot is complete");
    const auto old_dir = old_pin->directory;

    Row updated = base.front(); updated.text = "database parse updated snapshot marker";
    updated.vector = {-1.0f, 0.0f, 1.0f, 0.0f};
    Row added = base.back(); added.pk = "fragment-added"; added.file_id = 999;
    added.text = "json generated new snapshot marker";
    std::vector<BatchMutation> batch{Upsert(updated), Delete(base[1].pk), Upsert(added)};
    std::mutex gate_mutex;
    std::condition_variable gate;
    bool reader_at_vector_boundary = false, release_reader = false, writer_done = false;
    bool writer_ok = false, reader_done = false;
    GenerationRead reader_result;
    std::thread reader([&] {
      reader_result = coordinator.Hybrid(base.front().vector, "database parse", 100, [&] {
        std::unique_lock<std::mutex> lock(gate_mutex);
        reader_at_vector_boundary = true;
        gate.notify_all();
        gate.wait(lock, [&] { return release_reader; });
      });
      std::lock_guard<std::mutex> lock(gate_mutex);
      reader_done = true; gate.notify_all();
    });
    {
      std::unique_lock<std::mutex> lock(gate_mutex);
      const bool reached = gate.wait_for(lock, std::chrono::seconds(10), [&] {
        return reader_at_vector_boundary;
      });
      Check(reached, "reader pins old generation before publication");
    }
    std::thread writer([&] {
      const bool ok = coordinator.Apply(batch, old_pin->id);
      if (!ok) std::cerr << "generation lifecycle publish failed: " << coordinator.last_error() << '\n';
      std::lock_guard<std::mutex> lock(gate_mutex);
      writer_ok = ok; writer_done = true; gate.notify_all();
    });
    bool published_without_reader_release = false;
    {
      std::unique_lock<std::mutex> lock(gate_mutex);
      published_without_reader_release = gate.wait_for(lock, std::chrono::seconds(30), [&] {
        return writer_done;
      });
      if (!published_without_reader_release) {
        release_reader = true; gate.notify_all();
      }
    }
    writer.join();
    if (!published_without_reader_release) {
      { std::lock_guard<std::mutex> lock(gate_mutex); release_reader = true; gate.notify_all(); }
      reader.join();
      throw std::runtime_error("immutable-generation writer stalled behind a pinned reader");
    }
    Check(writer_ok && !reader_done, "publish completes while old reader is held between vector and FTS");
    auto new_read = coordinator.Hybrid(base.front().vector, "database parse", 100);
    Check(new_read.generation == 2, "new readers atomically acquire the published generation");
    Check(fs::is_directory(old_dir), "pinned old generation remains on disk after publish");
    Check(coordinator.retained_generation_count() == 2,
          "current and pinned retired generation both count against retention");
    coordinator.CollectRetired(true);  // Pin prevents collection; injected failure remains pending.
    {
      std::lock_guard<std::mutex> lock(gate_mutex); release_reader = true; gate.notify_all();
    }
    reader.join();
    const auto old_expected = RowPks(base);
    Check(reader_result.generation == 1 && HitPks(reader_result.hits) == old_expected,
          "held reader completes with exact old-generation FTS/vector results");
    old_pin.reset();
    Check(!coordinator.CollectRetired(true), "injected retire failure is surfaced rather than silently discarded");
    Check(fs::is_directory(old_dir), "failed retirement leaves the directory searchable/persistent");
    Check(coordinator.CollectRetired(), "retirement retry succeeds after injected failure");
    Check(!fs::exists(old_dir), "old generation files are removed after all pins drain");
    const std::set<std::string> generation2_pks = RowPks([&] {
      auto p = coordinator.Pin(); return p->rows;
    }());
    Check(HitPks(new_read.hits) == generation2_pks && generation2_pks.count(base[1].pk) == 0 &&
          generation2_pks.count("fragment-added") == 1,
          "published generation contains update/delete/insert atomically");

    // Ordered same-PK operations use last-operation-wins semantics.
    Row delete_after_upsert = updated; delete_after_upsert.text = "must not survive ordered delete";
    Row upsert_after_delete = base[2]; upsert_after_delete.text = "survives ordered upsert";
    Check(coordinator.Apply({Upsert(delete_after_upsert), Delete(delete_after_upsert.pk),
                             Delete(upsert_after_delete.pk), Upsert(upsert_after_delete)}, 2),
          "ordered duplicate-PK batch commits");
    auto ordered = coordinator.Pin();
    Check(RowPks(ordered->rows).count(delete_after_upsert.pk) == 0 &&
          RowPks(ordered->rows).count(upsert_after_delete.pk) == 1,
          "same-PK upsert/delete order is applied deterministically");

    Row winner_a = upsert_after_delete; winner_a.text = "overlap writer A";
    Row winner_b = upsert_after_delete; winner_b.text = "overlap writer B";
    std::atomic<int> writers_won{0};
    std::thread writer_a([&] { if (coordinator.Apply({Upsert(winner_a)}, ordered->id)) ++writers_won; });
    std::thread writer_b([&] { if (coordinator.Apply({Upsert(winner_b)}, ordered->id)) ++writers_won; });
    writer_a.join(); writer_b.join();
    auto overlap = coordinator.Pin();
    const auto row2 = std::find_if(overlap->rows.begin(), overlap->rows.end(), [&](const Row& row) {
      return row.pk == upsert_after_delete.pk;
    });
    Check(writers_won == 1 && overlap->id == ordered->id + 1 && row2 != overlap->rows.end() &&
          (row2->text == winner_a.text || row2->text == winner_b.text),
          "overlapping stale-base batches publish exactly one complete winner");
    ordered.reset(); overlap.reset();

    std::atomic<int> readers_started{0}, incoherent_reads{0};
    std::vector<std::thread> readers;
    for (int i = 0; i < 8; ++i) readers.emplace_back([&] {
      readers_started.fetch_add(1, std::memory_order_release);
      for (int n = 0; n < 12; ++n) {
        const auto result = coordinator.Hybrid(base.front().vector, "database parse", 100);
        const std::set<std::string> expected(result.source_pks.begin(), result.source_pks.end());
        if (expected.size() != result.source_pks.size() || HitPks(result.hits) != expected)
          incoherent_reads.fetch_add(1, std::memory_order_relaxed);
      }
    });
    while (readers_started.load(std::memory_order_acquire) != 8) std::this_thread::yield();
    for (int i = 0; i < 3; ++i) {
      auto pin = coordinator.Pin(); Row row = pin->rows[i]; row.text += " churn";
      Check(coordinator.Apply({Upsert(std::move(row))}, pin->id), "concurrent-reader generation rebuild");
    }
    for (auto& thread : readers) thread.join();
    Check(incoherent_reads.load() == 0, "eight readers observe coherent snapshots during three publishes");

    // Three retained generations (two pinned retired + current) trigger
    // deterministic backpressure; releasing one pin permits the next publish.
    auto pin_a = coordinator.Pin();
    Row mutation_a = pin_a->rows.front(); mutation_a.text += " retained-a";
    Check(coordinator.Apply({Upsert(mutation_a)}, pin_a->id), "publish with first long-lived pin");
    auto pin_b = coordinator.Pin();
    Row mutation_b = pin_b->rows.back(); mutation_b.text += " retained-b";
    Check(coordinator.Apply({Upsert(mutation_b)}, pin_b->id), "publish with second long-lived pin");
    auto pin_c = coordinator.Pin();
    Row blocked = pin_c->rows.front(); blocked.text += " must-backpressure";
    Check(!coordinator.Apply({Upsert(blocked)}, pin_c->id) &&
          coordinator.last_error().find("BACKPRESSURE") != std::string::npos &&
          coordinator.generation() == pin_c->id && coordinator.retained_generation_count() == 3,
          "generation-count limit backpressures without changing current snapshot");
    pin_a.reset();
    Check(coordinator.CollectRetired(), "released long-lived reader allows generation retirement");
    Check(coordinator.Apply({Upsert(blocked)}, pin_c->id),
          "writer resumes after retained-generation pressure is relieved");
    pin_b.reset(); pin_c.reset();
    Check(coordinator.CollectRetired(), "remaining old generations retire after readers release");

    // Build failures and configured disk limits must not publish partial state.
    const uint32_t stable = coordinator.generation();
    auto stable_pin = coordinator.Pin();
    Row failing = stable_pin->rows.front(); failing.text += " failure-probe";
    for (const std::string fault : {"fail_after_fts", "fail_after_vector", "disk_full"}) {
      Check(!coordinator.Apply({Upsert(failing)}, stable, fault),
            "staged component/ENOSPC failure aborts before publish: " + fault);
      Check(coordinator.generation() == stable && coordinator.retained_generation_count() == 1,
            "failed staging leaves committed generation and directory set unchanged: " + fault);
    }
    const uint64_t stable_bytes = coordinator.retained_generation_bytes();
    coordinator.SetByteLimit(stable_bytes + 1);
    Check(!coordinator.Apply({Upsert(failing)}, stable) && coordinator.generation() == stable &&
          coordinator.last_error().find("generation disk budget") != std::string::npos,
          "configured disk budget rejects staged generation and preserves current");
    coordinator.SetByteLimit(1ull << 30);
    CheckGenerationRead(coordinator.Hybrid(base.front().vector, "database parse", 100),
                        "current after failure matrix");
    stable_pin.reset();
    auto final_pin = coordinator.Pin(); expected_rows = final_pin->rows;
    last_generation = final_pin->id;
  }
  Need(zvec_shutdown(), "shutdown after generation concurrency tests");

  // Process reopen validates the durable snapshot and rebuilds only the
  // committed outer generation; all unpinned historical files are reclaimed.
  Need(zvec_initialize(nullptr), "initialize generation recovery check");
  {
    ImmutableGenerationCoordinator recovered(root, dictionary);
    auto pin = recovered.Pin();
    Check(pin && pin->id == last_generation && pin->rows.size() == expected_rows.size(),
          "restart recovers the durable outer generation and exact row snapshot");
    CheckGenerationRead(recovered.Hybrid(base.front().vector, "database parse", 100),
                        "recovered generation");
    Check(recovered.retained_generation_count() == 1,
          "restart removes all unpinned historical generation directories");
    expected_rows = pin->rows;
  }
  Need(zvec_shutdown(), "shutdown before generation crash matrix");

  struct CrashCase { const char* stage; int exit_code; bool publishes; };
  const CrashCase crash_cases[] = {
      {"crash_before_reserve", 79, false}, {"crash_after_reserve", 80, false},
      {"crash_after_fts", 86, false}, {"crash_after_vector", 87, false},
      {"crash_after_stage", 81, false}, {"crash_after_rename", 82, false},
      {"crash_after_publish_wal", 83, true}, {"crash_after_pointer", 84, true},
      {"crash_after_manifest", 85, true}};
  const std::string crash_pk = expected_rows.front().pk;
  for (const auto& test : crash_cases) {
    const uint32_t before_id = last_generation;
    const std::string marker = std::string("crash update marker ") + test.stage;
    Check(SpawnGenerationCrash(executable, root, dictionary, test.stage) == test.exit_code,
          std::string("fresh process terminates at lifecycle fault ") + test.stage);
    if (std::string(test.stage) == "crash_after_reserve") {
      const int fd = ::open((root / "bridge.wal").c_str(), O_WRONLY | O_APPEND | O_CLOEXEC);
      const char torn[] = "P 999";
      if (fd >= 0) { (void)::write(fd, torn, sizeof(torn) - 1); (void)::fsync(fd); ::close(fd); }
    }
    if (std::string(test.stage) == "crash_after_publish_wal")
      DurableWrite(root / "bridge.manifest", "torn-manifest");
    Need(zvec_initialize(nullptr), "reopen after generation process crash");
    {
      ImmutableGenerationCoordinator recovered(root, dictionary);
      auto pin = recovered.Pin();
      Check(pin && (test.publishes ? pin->id > before_id : pin->id == before_id),
            std::string("recovery selects commit side of ") + test.stage);
      const auto row = std::find_if(pin->rows.begin(), pin->rows.end(), [&](const Row& item) {
        return item.pk == crash_pk;
      });
      const auto prior_row = std::find_if(expected_rows.begin(), expected_rows.end(), [&](const Row& item) {
        return item.pk == crash_pk;
      });
      Check(row != pin->rows.end() && prior_row != expected_rows.end() &&
            row->text == (test.publishes ? marker : prior_row->text),
            std::string("recovery row contents match durable boundary at ") + test.stage);
      CheckGenerationRead(recovered.Hybrid(base.front().vector, "database parse", 100),
                          std::string("recovered crash case ") + test.stage);
      Check(recovered.retained_generation_count() == 1,
            std::string("orphan/staging generations reclaimed after ") + test.stage);
      if (test.publishes) {
        expected_rows = pin->rows;
        last_generation = pin->id;
      }
    }
    Need(zvec_shutdown(), "shutdown after generation crash recovery");
  }
  std::cout << "m4_bridge_generation checks=" << checks.load()
            << " failures=" << failures.load() << " generation=" << last_generation
            << " live_rows=" << expected_rows.size()
            << " max_retained_generations=3 architecture=immutable-generation-shared-handles\n";
  return failures.load() ? 1 : 0;
}
} // namespace

int ChildCrash(const fs::path& root, const char* dictionary, const std::string& stage) {
  Need(zvec_initialize(nullptr), "child initialize native");
  Native native = OpenVector(root / "vector", false);
  m4::Options options; options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto opened = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!opened.ok) throw std::runtime_error(opened.message);
  Coordinator coordinator(root, native.value, fts.get());
  (void)coordinator.Commit(Fixtures(), coordinator.generation(), "crash_" + stage);
  return 90;  // The requested failpoint should have terminated this process.
}

int SpawnCrash(const char* executable, const fs::path& root, const char* dictionary,
               const std::string& stage) {
  const pid_t child = ::fork();
  if (child == 0) {
    ::execl(executable, executable, "--crash-child", root.c_str(), dictionary,
            stage.c_str(), static_cast<char*>(nullptr));
    ::_exit(127);
  }
  if (child < 0) throw std::runtime_error("fork crash child");
  int status = 0;
  if (::waitpid(child, &status, 0) != child) throw std::runtime_error("wait crash child");
  return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

double PercentileMs(std::vector<double> values, double percentile) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  const size_t index = static_cast<size_t>(std::ceil(percentile * values.size())) - 1;
  return values[std::min(index, values.size() - 1)];
}
std::vector<Row> MakeChurnWorkload(size_t count) {
  std::vector<Row> rows; rows.reserve(count);
  for (size_t i = 0; i < count; ++i) {
    Row row;
    char pk[32]; std::snprintf(pk, sizeof(pk), "doc-%08zu", i);
    row.pk = pk; row.file_id = static_cast<uint32_t>(i);
    row.text = "fn parse_request_json handler log timeout cache response source_line " +
        std::to_string(i) + " { generated payload id=" + std::to_string(i) + " }";
    row.file_name = i % 3 == 0 ? "generated-handler.cc" :
                    i % 3 == 1 ? "request-worker.log" : "response-cache.json";
    row.symbol_type = i % 2 == 0 ? "function" : "class";
    row.directories = {17, static_cast<uint32_t>(i % 31)};
    row.vector = {static_cast<float>(i % 1000) * 0.01f,
                  static_cast<float>(i % 37) * 0.03f,
                  static_cast<float>(i % 11) * 0.07f, 1.0f};
    if constexpr (kDimension > 4) {
      // The dedicated profiling build uses zg's default 256-D normalized
      // cosine-vector shape; deterministic pseudo-random vectors avoid an
      // artificial four-axis neighborhood while keeping each run repeatable.
      double norm_squared = 0.0;
      for (uint32_t d = 0; d < kDimension; ++d) {
        uint32_t h = static_cast<uint32_t>(i * 2654435761ull) ^
                     (d * 2246822519u + 3266489917u);
        h ^= h >> 16; h *= 0x7feb352du;
        h ^= h >> 15; h *= 0x846ca68bu;
        h ^= h >> 16;
        row.vector[d] = static_cast<float>(h & 0xffffu) / 32767.5f - 1.0f;
        norm_squared += static_cast<double>(row.vector[d]) * row.vector[d];
      }
      const float inverse_norm = static_cast<float>(1.0 / std::sqrt(norm_squared));
      for (float& value : row.vector) value *= inverse_norm;
    }
    rows.push_back(std::move(row));
  }
  return rows;
}
std::vector<BatchMutation> ChurnBatch(const std::vector<Row>& current, size_t round,
                                      std::vector<Row>* next) {
  if (current.size() < 2) throw std::runtime_error("churn workload needs at least two rows");
  *next = current;
  std::vector<BatchMutation> mutations;
  const size_t updates = std::max<size_t>(1, current.size() / 100);
  for (size_t i = 0; i < updates; ++i) {
    const size_t index = (round * updates + i) % (current.size() - 1);
    Row row = (*next)[index];
    row.text += " updated round " + std::to_string(round);
    row.vector[1] += static_cast<float>(round + 1) * 0.001f;
    (*next)[index] = row;
    mutations.push_back(Upsert(std::move(row)));
  }
  const Row removed = next->back();
  Row inserted = removed;
  inserted.pk = "churn-" + std::to_string(round);
  inserted.file_id = static_cast<uint32_t>(current.size() + round);
  inserted.text = "fn generated churn json log request timeout round " + std::to_string(round);
  inserted.file_name = "churn-generated.json";
  inserted.vector[0] += 0.25f;
  next->back() = inserted;
  mutations.push_back(Delete(removed.pk));
  mutations.push_back(Upsert(std::move(inserted)));
  return mutations;
}
struct ProcessMeasurement {
  uint64_t write_bytes{0};
  double cpu_ms{0};
  long peak_rss_kb{0};
};
uint64_t ProcessWriteBytes() {
  std::ifstream in("/proc/self/io");
  std::string key; uint64_t value = 0;
  while (in >> key >> value) if (key == "write_bytes:") return value;
  return 0;
}
ProcessMeasurement MeasureProcess() {
  rusage usage{};
  if (::getrusage(RUSAGE_SELF, &usage) != 0) throw std::runtime_error("getrusage failed");
  ProcessMeasurement result;
  result.write_bytes = ProcessWriteBytes();
  result.cpu_ms = (usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000.0 +
      (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000.0;
  result.peak_rss_kb = usage.ru_maxrss;
  return result;
}
void PrintArray(const char* name, const std::vector<double>& values) {
  std::cout << ",\"" << name << "\":[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}
int RunGenerationStageProfile(const fs::path& root, const char* dictionary,
                              size_t documents, size_t rounds) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("stage profile requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize generation stage profile");
  auto rows = MakeChurnWorkload(documents);
  const ProcessMeasurement before = MeasureProcess();
  std::vector<double> commit_ms;
  uint64_t retained_bytes = 0;
  double base_ms = 0;
  {
    ImmutableGenerationCoordinator coordinator(root, dictionary, 3, 2ull << 30);
    std::vector<BatchMutation> initial;
    for (const auto& row : rows) initial.push_back(Upsert(row));
    auto start = std::chrono::steady_clock::now();
    if (!coordinator.Apply(initial, 0))
      throw std::runtime_error("stage profile base commit failed: " + coordinator.last_error());
    base_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    for (size_t round = 0; round < rounds; ++round) {
      std::vector<Row> next;
      auto mutations = ChurnBatch(rows, round, &next);
      start = std::chrono::steady_clock::now();
      if (!coordinator.Apply(mutations, coordinator.generation()))
        throw std::runtime_error("stage profile churn failed: " + coordinator.last_error());
      commit_ms.push_back(std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - start).count());
      rows = std::move(next);
    }
    retained_bytes = coordinator.retained_generation_bytes();
  }
  const ProcessMeasurement after = MeasureProcess();
  std::cout << std::setprecision(8) << "{\"arm\":\"B1-stage-profile\",\"docs\":"
            << documents << ",\"rounds\":" << rounds << ",\"base_commit_ms\":" << base_ms
            << ",\"commit_p50_ms\":" << PercentileMs(commit_ms, .50)
            << ",\"commit_p95_ms\":" << PercentileMs(commit_ms, .95)
            << ",\"process_cpu_ms\":" << after.cpu_ms - before.cpu_ms
            << ",\"physical_write_bytes\":" << after.write_bytes - before.write_bytes
            << ",\"peak_rss_kb\":" << after.peak_rss_kb
            << ",\"retained_generation_bytes\":" << retained_bytes;
  PrintArray("commit_samples_ms", commit_ms);
  std::cout << "}\n";
  Need(zvec_shutdown(), "shutdown generation stage profile");
  return 0;
}
int RunLockSustained(const fs::path& root, const char* dictionary,
                     size_t documents, size_t rounds) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("lock benchmark requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root); AtomicManifest(root, 0);
  Need(zvec_initialize(nullptr), "initialize lock benchmark");
  Native native = OpenVector(root / "vector", true);
  m4::Options options; options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto opened = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!opened.ok) throw std::runtime_error(opened.message);
  Coordinator coordinator(root, native.value, fts.get());
  auto rows = MakeChurnWorkload(documents);
  const ProcessMeasurement before = MeasureProcess();
  const auto base_start = std::chrono::steady_clock::now();
  if (!coordinator.Commit(rows, 0)) throw std::runtime_error("lock benchmark base commit failed");
  const double base_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - base_start).count();
  const size_t topk = std::min<size_t>(50, documents);
  const auto query = rows.front().vector;
  const uint32_t generation1 = coordinator.SearchSnapshot();
  (void)coordinator.Hybrid(generation1, query, "request timeout", topk);
  std::vector<double> commit_ms, reader_ms;
  std::mutex reader_samples_mutex;
  std::atomic<int> bad_snapshots{0};
  for (size_t round = 0; round < rounds; ++round) {
    std::vector<Row> next;
    (void)ChurnBatch(rows, round, &next);
    const uint32_t old_generation = coordinator.SearchSnapshot();
    std::atomic<bool> writer_locked{false};
    std::atomic<int> attempted{0};
    std::vector<std::thread> readers;
    for (int i = 0; i < 4; ++i) readers.emplace_back([&] {
      while (!writer_locked.load(std::memory_order_acquire)) std::this_thread::yield();
      const auto start = std::chrono::steady_clock::now();
      attempted.fetch_add(1, std::memory_order_release);
      const auto hits = coordinator.Hybrid(old_generation, query, "request timeout", topk);
      const double elapsed = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - start).count();
      { std::lock_guard<std::mutex> lock(reader_samples_mutex); reader_ms.push_back(elapsed); }
      if (hits.size() != topk) bad_snapshots.fetch_add(1, std::memory_order_relaxed);
    });
    const auto start = std::chrono::steady_clock::now();
    const bool ok = coordinator.Commit(next, old_generation, {}, {}, [&] {
      writer_locked.store(true, std::memory_order_release);
      while (attempted.load(std::memory_order_acquire) != 4) std::this_thread::yield();
    });
    commit_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count());
    for (auto& reader : readers) reader.join();
    if (!ok || bad_snapshots.load() != 0) throw std::runtime_error("lock stress correctness failure");
    rows = std::move(next);
  }
  const ProcessMeasurement after = MeasureProcess();
  std::cout << std::setprecision(8) << "{\"arm\":\"coordinator-lock\",\"docs\":" << documents
            << ",\"rounds\":" << rounds << ",\"reader_threads\":4,\"queries\":" << reader_ms.size()
            << ",\"base_commit_ms\":" << base_ms
            << ",\"commit_p50_ms\":" << PercentileMs(commit_ms, .50)
            << ",\"commit_p95_ms\":" << PercentileMs(commit_ms, .95)
            << ",\"commit_p99_ms\":" << PercentileMs(commit_ms, .99)
            << ",\"reader_p50_ms\":" << PercentileMs(reader_ms, .50)
            << ",\"reader_p95_ms\":" << PercentileMs(reader_ms, .95)
            << ",\"reader_p99_ms\":" << PercentileMs(reader_ms, .99)
            << ",\"physical_write_bytes\":" << after.write_bytes - before.write_bytes
            << ",\"process_cpu_ms\":" << after.cpu_ms - before.cpu_ms
            << ",\"peak_rss_kb\":" << after.peak_rss_kb
            << ",\"root_bytes\":" << DirectoryBytes(root)
            << ",\"bad_snapshots\":" << bad_snapshots.load();
  PrintArray("commit_samples_ms", commit_ms); PrintArray("reader_samples_ms", reader_ms);
  std::cout << "}\n";
  fts.reset(); (void)native.Close();
  Need(zvec_shutdown(), "shutdown lock benchmark");
  return 0;
}
int RunImmutableGenerationPerformance(const fs::path& root, const char* dictionary,
                                     size_t documents, size_t rounds) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("generation benchmark requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize immutable generation benchmark");
  auto rows = MakeChurnWorkload(documents);
  const ProcessMeasurement before = MeasureProcess();
  std::vector<double> commit_ms, reader_ms, reader_query_cpu_ms,
      reader_thread_cpu_ms, writer_commit_cpu_ms, mutation_count_per_round,
      mutation_upserts_per_round, mutation_deletes_per_round;
  size_t queries = 0;
  double base_writer_thread_cpu_ms = 0;
  double reader_query_cpu_total_ms = 0, reader_thread_cpu_total_ms = 0;
  double writer_commit_thread_cpu_total_ms = 0;
  uint64_t final_disk_bytes = 0;
  {
    ImmutableGenerationCoordinator coordinator(root, dictionary, 3, 2ull << 30);
    std::vector<BatchMutation> initial;
    for (const auto& row : rows) initial.push_back(Upsert(row));
    const double base_cpu_start = CurrentThreadCpuMs();
    const auto base_start = std::chrono::steady_clock::now();
    if (!coordinator.Apply(initial, 0)) throw std::runtime_error("immutable base build failed: " + coordinator.last_error());
    base_writer_thread_cpu_ms = CurrentThreadCpuMs() - base_cpu_start;
    const double base_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - base_start).count();
    const size_t topk = std::min<size_t>(50, documents);
    const auto query = rows.front().vector;
    (void)coordinator.Hybrid(query, "request timeout", topk);
    size_t round_requested = 0, reader_ready = 0, writer_go = 0, reader_done = 0;
    std::mutex sync_mutex, sample_mutex;
    std::condition_variable sync_cv;
    std::atomic<int> coherence_errors{0};
    std::vector<std::thread> readers;
    for (int i = 0; i < 4; ++i) readers.emplace_back([&, i] {
      (void)i;
      const double thread_cpu_start = CurrentThreadCpuMs();
      for (size_t round = 0; round < rounds; ++round) {
        {
          std::unique_lock<std::mutex> lock(sync_mutex);
          sync_cv.wait(lock, [&] { return round_requested >= round + 1; });
          ++reader_ready;
          sync_cv.notify_all();
          sync_cv.wait(lock, [&] { return writer_go >= round + 1; });
        }
        const auto start = std::chrono::steady_clock::now();
        const double query_cpu_start = CurrentThreadCpuMs();
        const auto result = coordinator.Hybrid(query, "request timeout", topk);
        const double query_cpu = CurrentThreadCpuMs() - query_cpu_start;
        const double elapsed = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - start).count();
        std::set<std::string> expected(result.source_pks.begin(), result.source_pks.end());
        bool coherent = expected.size() == result.source_pks.size() && result.hits.size() == topk;
        for (const auto& hit : result.hits) coherent = coherent && expected.count(hit.pk) != 0;
        if (!coherent) coherence_errors.fetch_add(1, std::memory_order_relaxed);
        { std::lock_guard<std::mutex> lock(sample_mutex); reader_ms.push_back(elapsed); reader_query_cpu_ms.push_back(query_cpu); }
        {
          std::lock_guard<std::mutex> lock(sync_mutex);
          ++reader_done;
        }
        sync_cv.notify_all();
      }
      { std::lock_guard<std::mutex> lock(sample_mutex); reader_thread_cpu_ms.push_back(CurrentThreadCpuMs() - thread_cpu_start); }
    });
    for (size_t round = 0; round < rounds; ++round) {
      std::vector<Row> next;
      const auto mutations = ChurnBatch(rows, round, &next);
      size_t mutation_upserts = 0, mutation_deletes = 0;
      for (const auto& mutation : mutations) {
        if (mutation.kind == BatchMutation::Kind::DELETE) ++mutation_deletes;
        else ++mutation_upserts;
      }
      mutation_count_per_round.push_back(static_cast<double>(mutations.size()));
      mutation_upserts_per_round.push_back(static_cast<double>(mutation_upserts));
      mutation_deletes_per_round.push_back(static_cast<double>(mutation_deletes));
      auto long_pin = coordinator.Pin();
      const uint32_t old_generation = long_pin->id;
      {
        std::unique_lock<std::mutex> lock(sync_mutex);
        round_requested = round + 1;
        sync_cv.notify_all();
        sync_cv.wait(lock, [&] { return reader_ready == (round + 1) * 4; });
      }
      const auto start = std::chrono::steady_clock::now();
      const double writer_cpu_start = CurrentThreadCpuMs();
      {
        std::lock_guard<std::mutex> lock(sync_mutex);
        writer_go = round + 1;
      }
      sync_cv.notify_all();
      const bool ok = coordinator.Apply(mutations, old_generation);
      writer_commit_cpu_ms.push_back(CurrentThreadCpuMs() - writer_cpu_start);
      commit_ms.push_back(std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - start).count());
      {
        std::unique_lock<std::mutex> lock(sync_mutex);
        sync_cv.wait(lock, [&] { return reader_done == (round + 1) * 4; });
      }
      if (!ok) throw std::runtime_error("immutable generation churn failed: " + coordinator.last_error());
      rows = std::move(next);
      long_pin.reset();
      if (!coordinator.CollectRetired()) throw std::runtime_error("generation retirement failed in benchmark");
    }
    for (auto& reader : readers) reader.join();
    queries = reader_ms.size();
    if (coherence_errors.load() != 0 || queries == 0)
      throw std::runtime_error("immutable generation reader workload did not produce coherent samples");
    final_disk_bytes = coordinator.retained_generation_bytes();
    if (coordinator.retained_generation_count() != 1)
      throw std::runtime_error("immutable generation benchmark left extra retained generations");
    const auto sum_cpu = [](const std::vector<double>& values) {
      double total = 0; for (double value : values) total += value; return total;
    };
    reader_query_cpu_total_ms = sum_cpu(reader_query_cpu_ms);
    reader_thread_cpu_total_ms = sum_cpu(reader_thread_cpu_ms);
    writer_commit_thread_cpu_total_ms = sum_cpu(writer_commit_cpu_ms);
    std::cout << std::setprecision(8) << "{\"arm\":\"immutable-generation\",\"docs\":" << documents
              << ",\"rounds\":" << rounds << ",\"reader_threads\":4,\"queries\":" << queries
              << ",\"base_build_ms\":" << base_ms
              << ",\"base_writer_thread_cpu_ms\":" << base_writer_thread_cpu_ms
              << ",\"m4_docs_rebuilt_per_generation\":" << documents
              << ",\"m4_apply_batch_calls_per_generation\":" << ((documents + 255) / 256)
              << ",\"native_vector_rows_upserted_per_generation\":" << documents
              << ",\"native_vector_upsert_calls_per_generation\":" << ((documents + 255) / 256)
              << ",\"reader_query_cpu_total_ms\":" << reader_query_cpu_total_ms
              << ",\"reader_thread_cpu_total_ms\":" << reader_thread_cpu_total_ms
              << ",\"reader_coordination_cpu_ms\":" << (reader_thread_cpu_total_ms - reader_query_cpu_total_ms)
              << ",\"reader_query_cpu_p50_ms\":" << PercentileMs(reader_query_cpu_ms,.50)
              << ",\"reader_query_cpu_p95_ms\":" << PercentileMs(reader_query_cpu_ms,.95)
              << ",\"commit_p50_ms\":" << PercentileMs(commit_ms, .50)
              << ",\"commit_p95_ms\":" << PercentileMs(commit_ms, .95)
              << ",\"commit_p99_ms\":" << PercentileMs(commit_ms, .99)
              << ",\"reader_p50_ms\":" << PercentileMs(reader_ms, .50)
              << ",\"reader_p95_ms\":" << PercentileMs(reader_ms, .95)
              << ",\"reader_p99_ms\":" << PercentileMs(reader_ms, .99)
              << ",\"root_bytes\":" << DirectoryBytes(root)
              << ",\"retained_generation_bytes\":" << final_disk_bytes
              << ",\"retained_generations\":" << coordinator.retained_generation_count()
              << ",\"coherence_errors\":" << coherence_errors.load();
    PrintArray("commit_samples_ms", commit_ms); PrintArray("reader_samples_ms", reader_ms);
    PrintArray("reader_query_cpu_samples_ms", reader_query_cpu_ms);
    PrintArray("reader_thread_cpu_samples_ms", reader_thread_cpu_ms);
    PrintArray("writer_commit_thread_cpu_samples_ms", writer_commit_cpu_ms);
    PrintArray("logical_mutation_count_per_round", mutation_count_per_round);
    PrintArray("logical_upserts_per_round", mutation_upserts_per_round);
    PrintArray("logical_deletes_per_round", mutation_deletes_per_round);
    std::cout << "}\n";
  }
  const ProcessMeasurement after = MeasureProcess();
  // Output a separate resource record so the coordinator object can close all
  // handles before RSS/IO are sampled at process completion.
  const double whole_process_cpu_ms = after.cpu_ms - before.cpu_ms;
  std::cout << "{\"arm\":\"immutable-generation-resources\",\"physical_write_bytes\":"
            << after.write_bytes - before.write_bytes << ",\"process_cpu_ms\":"
            << whole_process_cpu_ms << ",\"reader_query_cpu_total_ms\":" << reader_query_cpu_total_ms
            << ",\"reader_thread_cpu_total_ms\":" << reader_thread_cpu_total_ms
            << ",\"reader_coordination_cpu_ms\":" << (reader_thread_cpu_total_ms - reader_query_cpu_total_ms)
            << ",\"writer_commit_thread_cpu_total_ms\":" << writer_commit_thread_cpu_total_ms
            << ",\"base_writer_thread_cpu_ms\":" << base_writer_thread_cpu_ms
            << ",\"process_cpu_excluding_reader_threads_ms\":" << (whole_process_cpu_ms - reader_thread_cpu_total_ms)
            << ",\"unattributed_process_cpu_ms\":" << (whole_process_cpu_ms - reader_thread_cpu_total_ms - writer_commit_thread_cpu_total_ms - base_writer_thread_cpu_ms)
            << ",\"peak_rss_kb\":" << after.peak_rss_kb
            << ",\"root_bytes_after_close\":" << DirectoryBytes(root) << "}\n";
  Need(zvec_shutdown(), "shutdown immutable generation benchmark");
  return 0;
}
int RunDeltaVectorPerformance(const fs::path& root, const char* dictionary,
                              size_t documents, size_t rounds,
                              bool incremental_m4 = false,
                              bool reuse_analyzer_cache = false,
                              bool skip_cached_analyzer_fingerprint_verification = false) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("delta benchmark requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize delta-vector performance arm");
  auto rows = MakeChurnWorkload(documents);
  const ProcessMeasurement before = MeasureProcess();
  const auto cache_stats_before = m4::Analyzer::CacheStats();
  const auto whole_start = std::chrono::steady_clock::now();
  std::vector<double> commits, reader_latencies, reader_query_cpu_ms,
      reader_thread_cpu_ms, writer_commit_cpu_ms, mutation_count_per_round,
      mutation_upserts_per_round, mutation_deletes_per_round,
      vector_rows_written_per_round, vector_upsert_calls_per_round,
      m4_rows_indexed_per_round, m4_apply_batch_calls_per_round,
      m4_mutations_per_round, m4_shared_segments_per_round,
      m4_copied_state_bytes_per_round, m4_clone_wall, m4_clone_cpu,
      m4_open_wall, m4_open_cpu, m4_open_total_cpu, m4_open_segments,
      m4_open_segments_cpu, m4_open_tombstones, m4_open_stats, m4_open_wal,
      m4_open_key_map, m4_open_gc, m4_open_analyzer, m4_open_analyzer_cpu,
      m4_open_analyzer_init, m4_open_analyzer_init_cpu,
      m4_open_fingerprint_verify, m4_open_fingerprint_verify_cpu,
      m4_open_manifest, m4_open_cache_hits, m4_open_fingerprint_calls,
      m4_open_fingerprint_sha256_calls, m4_open_fingerprint_dictionary_bytes,
      m4_open_fingerprint_sha256_bytes, m4_open_cache_misses,
      m4_open_cache_races, m4_open_cache_waits, m4_open_cache_wait_reuses,
      m4_open_dictionary_snapshots, m4_open_cache_expired, m4_open_init_attempts,
      m4_open_init_successes, m4_open_verification_skipped, m4_open_segment_count,
      m4_open_segment_bytes, m4_apply_wall, m4_apply_cpu,
      m4_flush_wall, m4_flush_cpu, vector_open_wall, vector_open_cpu,
      vector_convert_wall, vector_convert_cpu, vector_upsert_wall, vector_upsert_cpu,
      vector_flush_wall, vector_flush_cpu, generation_metadata_wall,
      generation_metadata_cpu, post_m4_sync_wall, post_m4_sync_cpu,
      snapshot_finalize_wall, snapshot_finalize_cpu, vector_wall, vector_cpu, m4_wall, m4_cpu, compaction_wall,
      compaction_cpu, publish_wall, retirement_wall, commit_write_bytes;
  std::vector<uint8_t> compaction_flags;
  double base_wall = 0, base_vector_wall = 0, base_vector_cpu = 0,
      base_m4_wall = 0, base_m4_cpu = 0, base_writer_thread_cpu_ms = 0;
  double reader_query_cpu_total_ms = 0, reader_thread_cpu_total_ms = 0;
  double writer_commit_thread_cpu_total_ms = 0;
  uint64_t final_root_bytes = 0;
  size_t retained_generation_count = 0, pinned_retired_generation_count = 0;
  uint32_t final_generation_id = 0;
  size_t query_count = 0, coherence_errors = 0;
  {
    std::map<std::string, uint32_t> versions;
    for (const auto& row : rows) versions[row.pk] = 1;
    const double base_writer_cpu_start = CurrentThreadCpuMs();
    AppendGenerationWal(root, 'R', 1);
    DeltaBuildMetrics base_metrics;
    auto start = std::chrono::steady_clock::now();
    auto base = BuildDeltaVectorSnapshot(root, 1, rows, rows, {},
        std::move(versions), dictionary, &base_metrics, nullptr, nullptr,
        reuse_analyzer_cache, skip_cached_analyzer_fingerprint_verification);
    base_wall = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    start = std::chrono::steady_clock::now();
    PublishDeltaGeneration(root, 1, 1);
    base_writer_thread_cpu_ms = CurrentThreadCpuMs() - base_writer_cpu_start;
    base_wall += std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    base_vector_wall = base_metrics.vector_wall_ms;
    base_vector_cpu = base_metrics.vector_cpu_ms;
    base_m4_wall = base_metrics.m4_wall_ms;
    base_m4_cpu = base_metrics.m4_cpu_ms;
    std::shared_ptr<const DeltaVectorSnapshot> current = std::move(base);
    const auto query = rows.front().vector;
    const size_t topk = std::min<size_t>(50, documents);
    (void)current->Hybrid(query, "request timeout", topk);
    size_t requested = 0, ready = 0, released = 0, completed = 0;
    std::mutex sync_mutex, samples_mutex;
    std::condition_variable sync_cv;
    std::atomic<size_t> bad{0};
    std::vector<std::thread> readers;
    std::vector<std::chrono::steady_clock::time_point> query_release_times(rounds);
    std::vector<ReaderStageEvent> reader_stage_events;
    reader_stage_events.reserve(rounds * 4);
    for (size_t reader = 0; reader < 4; ++reader) readers.emplace_back([&, reader] {
      const double thread_cpu_start = CurrentThreadCpuMs();
      for (size_t round = 0; round < rounds; ++round) {
        std::chrono::steady_clock::time_point acquire_release;
        {
          std::unique_lock<std::mutex> lock(sync_mutex);
          sync_cv.wait(lock, [&] { return requested >= round + 1; });
          ++ready;
          sync_cv.notify_all();
          sync_cv.wait(lock, [&] { return released >= round + 1; });
          acquire_release = query_release_times[round];
        }
        ReaderStageEvent stage_event;
        stage_event.round = round;
        stage_event.reader = reader;
        stage_event.reader_tid = CurrentThreadTid();
        stage_event.acquire_release = acquire_release;
        stage_event.acquire_end = std::chrono::steady_clock::now();
        stage_event.pin_start = std::chrono::steady_clock::now();
        auto pin = std::atomic_load_explicit(&current, std::memory_order_acquire);
        stage_event.pin_end = std::chrono::steady_clock::now();
        const auto query_start = stage_event.pin_start;
        const double query_cpu_start = CurrentThreadCpuMs();
        const auto result = pin->Hybrid(query, "request timeout", topk);
        stage_event.stages = result.stages;
        const auto query_end = std::chrono::steady_clock::now();
        const double query_cpu = CurrentThreadCpuMs() - query_cpu_start;
        const double elapsed = std::chrono::duration<double, std::milli>(
            query_end - query_start).count();
        std::set<std::string> pks(result.source_pks.begin(), result.source_pks.end());
        bool coherent = result.generation == pin->id && pks.size() == result.source_pks.size() &&
                        result.hits.size() == topk;
        for (const auto& hit : result.hits) coherent = coherent && pks.count(hit.pk) != 0;
        if (!coherent) bad.fetch_add(1, std::memory_order_relaxed);
        stage_event.pin_release_start = std::chrono::steady_clock::now();
        pin.reset();
        stage_event.pin_release_end = std::chrono::steady_clock::now();
        {
          std::lock_guard<std::mutex> lock(samples_mutex);
          reader_latencies.push_back(elapsed);
          reader_query_cpu_ms.push_back(query_cpu);
          reader_stage_events.push_back(stage_event);
        }
        {
          std::lock_guard<std::mutex> lock(sync_mutex);
          ++completed;
        }
        sync_cv.notify_all();
      }
      { std::lock_guard<std::mutex> lock(samples_mutex); reader_thread_cpu_ms.push_back(CurrentThreadCpuMs() - thread_cpu_start); }
    });
    std::vector<std::weak_ptr<const DeltaVectorSnapshot>> retired;
    std::vector<WriterStageEvent> writer_stage_events;
    writer_stage_events.reserve(rounds);
    for (size_t round = 0; round < rounds; ++round) {
      WriterStageEvent writer_event;
      writer_event.round = round;
      writer_event.writer_tid = CurrentThreadTid();
      writer_event.round_start = std::chrono::steady_clock::now();
      std::vector<Row> next_rows;
      const auto mutations = ChurnBatch(rows, round, &next_rows);
      size_t mutation_upserts = 0, mutation_deletes = 0;
      for (const auto& mutation : mutations) {
        if (mutation.kind == BatchMutation::Kind::DELETE) ++mutation_deletes;
        else ++mutation_upserts;
      }
      mutation_count_per_round.push_back(static_cast<double>(mutations.size()));
      mutation_upserts_per_round.push_back(static_cast<double>(mutation_upserts));
      mutation_deletes_per_round.push_back(static_cast<double>(mutation_deletes));
      auto old = std::atomic_load_explicit(&current, std::memory_order_acquire);
      {
        std::unique_lock<std::mutex> lock(sync_mutex);
        requested = round + 1;
        sync_cv.notify_all();
        sync_cv.wait(lock, [&] { return ready == (round + 1) * 4; });
      }
      const double writer_cpu_start = CurrentThreadCpuMs();
      const auto commit_start = std::chrono::steady_clock::now();
      const uint64_t write_start = ProcessWriteBytes();
      {
        std::lock_guard<std::mutex> lock(sync_mutex);
        query_release_times[round] = std::chrono::steady_clock::now();
        writer_event.query_release = query_release_times[round];
        released = round + 1;
      }
      sync_cv.notify_all();
      const uint32_t generation = old->id + 1;
      std::vector<Row> vector_delta;
      auto next_versions = old->live_versions;
      for (const auto& mutation : mutations) {
        if (mutation.kind == BatchMutation::Kind::DELETE) next_versions.erase(mutation.pk);
        else {
          if (!mutation.row) throw std::runtime_error("delta benchmark upsert is missing row");
          vector_delta.push_back(*mutation.row);
          next_versions[mutation.pk] = generation;
        }
      }
      std::vector<m4::Mutation> m4_delta;
      if (incremental_m4) {
        m4_delta.reserve(mutations.size());
        for (const auto& mutation : mutations) {
          if (mutation.kind == BatchMutation::Kind::DELETE) {
            m4_delta.emplace_back(m4::MutationKind::DELETE, "1:" + mutation.pk);
          } else {
            if (!mutation.row) throw std::runtime_error("M4 upsert mutation is missing row");
            m4_delta.emplace_back(m4::MutationKind::UPSERT, "1:" + mutation.pk,
                                  M4Doc(*mutation.row, 1));
          }
        }
      }
      writer_event.build_start = std::chrono::steady_clock::now();
      const double generation_wal_cpu_start = CurrentThreadCpuMs();
      writer_event.generation_wal_start = std::chrono::steady_clock::now();
      AppendGenerationWal(root, 'R', generation);
      writer_event.generation_wal_end = std::chrono::steady_clock::now();
      writer_event.generation_wal_cpu_ms = CurrentThreadCpuMs() - generation_wal_cpu_start;
      DeltaBuildMetrics metrics;
      const fs::path prior_m4_root = old->directory / "fts";
      auto next = BuildDeltaVectorSnapshot(root, generation, next_rows, vector_delta,
          old->shards, std::move(next_versions), dictionary, &metrics,
          incremental_m4 ? &prior_m4_root : nullptr,
          incremental_m4 ? &m4_delta : nullptr, reuse_analyzer_cache,
          skip_cached_analyzer_fingerprint_verification);
      writer_event.build_end = std::chrono::steady_clock::now();
      writer_event.compacted = metrics.compacted;
      writer_event.compaction_start = metrics.compaction_start_time;
      writer_event.compaction_end = metrics.compaction_end_time;
      writer_event.vector_start = metrics.vector_start_time;
      writer_event.vector_end = metrics.vector_end_time;
      writer_event.vector_open_start = metrics.vector_open_start_time;
      writer_event.vector_open_end = metrics.vector_open_end_time;
      writer_event.vector_flush_start = metrics.vector_flush_start_time;
      writer_event.vector_flush_end = metrics.vector_flush_end_time;
      writer_event.generation_metadata_start = metrics.generation_metadata_start_time;
      writer_event.generation_metadata_end = metrics.generation_metadata_end_time;
      writer_event.m4_clone_start = metrics.m4_clone_start_time;
      writer_event.m4_clone_end = metrics.m4_clone_end_time;
      writer_event.m4_open_start = metrics.m4_open_start_time;
      writer_event.m4_open_end = metrics.m4_open_end_time;
      writer_event.m4_apply_start = metrics.m4_apply_start_time;
      writer_event.m4_apply_end = metrics.m4_apply_end_time;
      writer_event.m4_flush_start = metrics.m4_flush_start_time;
      writer_event.m4_flush_end = metrics.m4_flush_end_time;
      writer_event.post_m4_sync_start = metrics.post_m4_sync_start_time;
      writer_event.post_m4_sync_end = metrics.post_m4_sync_end_time;
      writer_event.snapshot_finalize_start = metrics.snapshot_finalize_start_time;
      writer_event.snapshot_finalize_end = metrics.snapshot_finalize_end_time;
      writer_event.m4_start = metrics.m4_start_time;
      writer_event.m4_end = metrics.m4_end_time;
      const auto publish_start = std::chrono::steady_clock::now();
      writer_event.manifest_publish_start = publish_start;
      PublishDeltaGeneration(root, generation, generation);
      const auto publish_end = std::chrono::steady_clock::now();
      writer_event.manifest_publish_end = publish_end;
      publish_wall.push_back(std::chrono::duration<double, std::milli>(
          publish_end - publish_start).count());
      retired.emplace_back(old);
      writer_event.snapshot_publish_start = std::chrono::steady_clock::now();
      std::atomic_store_explicit(&current,
          std::shared_ptr<const DeltaVectorSnapshot>(next), std::memory_order_release);
      writer_event.snapshot_publish_end = std::chrono::steady_clock::now();
      old.reset();
      auto retirement_start = std::chrono::steady_clock::now();
      writer_event.retirement_start = retirement_start;
      (void)CollectDeltaRetired(root, std::atomic_load_explicit(&current, std::memory_order_acquire), &retired);
      const auto retirement_end = std::chrono::steady_clock::now();
      writer_event.retirement_end = retirement_end;
      writer_event.round_end = retirement_end;
      retirement_wall.push_back(std::chrono::duration<double, std::milli>(
          retirement_end - retirement_start).count());
      writer_stage_events.push_back(writer_event);
      writer_commit_cpu_ms.push_back(CurrentThreadCpuMs() - writer_cpu_start);
      commits.push_back(std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - commit_start).count());
      commit_write_bytes.push_back(static_cast<double>(ProcessWriteBytes() - write_start));
      vector_wall.push_back(metrics.vector_wall_ms); vector_cpu.push_back(metrics.vector_cpu_ms);
      vector_open_wall.push_back(metrics.vector_open_wall_ms); vector_open_cpu.push_back(metrics.vector_open_cpu_ms);
      vector_convert_wall.push_back(metrics.vector_convert_wall_ms); vector_convert_cpu.push_back(metrics.vector_convert_cpu_ms);
      vector_upsert_wall.push_back(metrics.vector_upsert_wall_ms); vector_upsert_cpu.push_back(metrics.vector_upsert_cpu_ms);
      vector_flush_wall.push_back(metrics.vector_flush_wall_ms); vector_flush_cpu.push_back(metrics.vector_flush_cpu_ms);
      generation_metadata_wall.push_back(metrics.generation_metadata_wall_ms);
      generation_metadata_cpu.push_back(metrics.generation_metadata_cpu_ms);
      post_m4_sync_wall.push_back(metrics.post_m4_sync_wall_ms); post_m4_sync_cpu.push_back(metrics.post_m4_sync_cpu_ms);
      snapshot_finalize_wall.push_back(metrics.snapshot_finalize_wall_ms);
      snapshot_finalize_cpu.push_back(metrics.snapshot_finalize_cpu_ms);
      m4_wall.push_back(metrics.m4_wall_ms); m4_cpu.push_back(metrics.m4_cpu_ms);
      vector_rows_written_per_round.push_back(static_cast<double>(metrics.vector_rows_written));
      vector_upsert_calls_per_round.push_back(static_cast<double>(metrics.vector_upsert_calls));
      m4_rows_indexed_per_round.push_back(static_cast<double>(metrics.m4_rows_indexed));
      m4_apply_batch_calls_per_round.push_back(static_cast<double>(metrics.m4_apply_batch_calls));
      m4_mutations_per_round.push_back(static_cast<double>(metrics.m4_mutations_applied));
      m4_shared_segments_per_round.push_back(static_cast<double>(metrics.m4_shared_segment_files));
      m4_copied_state_bytes_per_round.push_back(static_cast<double>(metrics.m4_copied_state_bytes));
      m4_clone_wall.push_back(metrics.m4_clone_wall_ms); m4_clone_cpu.push_back(metrics.m4_clone_cpu_ms);
      m4_open_wall.push_back(metrics.m4_open_wall_ms); m4_open_cpu.push_back(metrics.m4_open_cpu_ms);
      m4_open_total_cpu.push_back(metrics.m4_open_diagnostics.total_cpu_ms);
      m4_open_segments.push_back(metrics.m4_open_diagnostics.segment_open_ms);
      m4_open_segments_cpu.push_back(metrics.m4_open_diagnostics.segment_open_cpu_ms);
      m4_open_tombstones.push_back(metrics.m4_open_diagnostics.tombstones_ms);
      m4_open_stats.push_back(metrics.m4_open_diagnostics.stats_load_ms);
      m4_open_wal.push_back(metrics.m4_open_diagnostics.wal_replay_ms);
      m4_open_key_map.push_back(metrics.m4_open_diagnostics.live_key_map_ms);
      m4_open_gc.push_back(metrics.m4_open_diagnostics.garbage_collect_ms);
      m4_open_analyzer.push_back(metrics.m4_open_diagnostics.analyzer_fingerprint_ms);
      m4_open_analyzer_cpu.push_back(metrics.m4_open_diagnostics.analyzer_fingerprint_cpu_ms);
      m4_open_analyzer_init.push_back(metrics.m4_open_diagnostics.analyzer_init_ms);
      m4_open_analyzer_init_cpu.push_back(metrics.m4_open_diagnostics.analyzer_init_cpu_ms);
      m4_open_fingerprint_verify.push_back(metrics.m4_open_diagnostics.fingerprint_verification_ms);
      m4_open_fingerprint_verify_cpu.push_back(metrics.m4_open_diagnostics.fingerprint_verification_cpu_ms);
      m4_open_manifest.push_back(metrics.m4_open_diagnostics.manifest_and_analyzer_ms);
      m4_open_cache_hits.push_back(metrics.m4_open_diagnostics.analyzer_cache_hit ? 1.0 : 0.0);
      m4_open_fingerprint_calls.push_back(static_cast<double>(metrics.m4_open_diagnostics.analyzer_fingerprint_calls));
      m4_open_fingerprint_sha256_calls.push_back(static_cast<double>(metrics.m4_open_diagnostics.analyzer_fingerprint_sha256_calls));
      m4_open_fingerprint_dictionary_bytes.push_back(static_cast<double>(metrics.m4_open_diagnostics.analyzer_fingerprint_dictionary_bytes));
      m4_open_fingerprint_sha256_bytes.push_back(static_cast<double>(metrics.m4_open_diagnostics.analyzer_fingerprint_sha256_bytes));
      m4_open_cache_misses.push_back(metrics.m4_open_diagnostics.analyzer_cache_miss ? 1.0 : 0.0);
      m4_open_cache_races.push_back(metrics.m4_open_diagnostics.analyzer_cache_race_reuse ? 1.0 : 0.0);
      m4_open_cache_waits.push_back(metrics.m4_open_diagnostics.analyzer_cache_waited ? 1.0 : 0.0);
      m4_open_cache_wait_reuses.push_back(metrics.m4_open_diagnostics.analyzer_cache_wait_reuse ? 1.0 : 0.0);
      m4_open_dictionary_snapshots.push_back(metrics.m4_open_diagnostics.analyzer_dictionary_snapshot_used ? 1.0 : 0.0);
      m4_open_cache_expired.push_back(metrics.m4_open_diagnostics.analyzer_cache_expired_entry ? 1.0 : 0.0);
      m4_open_init_attempts.push_back(metrics.m4_open_diagnostics.analyzer_initialization_attempted ? 1.0 : 0.0);
      m4_open_init_successes.push_back(metrics.m4_open_diagnostics.analyzer_initialization_succeeded ? 1.0 : 0.0);
      m4_open_verification_skipped.push_back(metrics.m4_open_diagnostics.fingerprint_verification_skipped ? 1.0 : 0.0);
      m4_open_segment_count.push_back(static_cast<double>(metrics.m4_open_diagnostics.segment_count));
      m4_open_segment_bytes.push_back(static_cast<double>(metrics.m4_open_diagnostics.segment_bytes));
      m4_apply_wall.push_back(metrics.m4_apply_wall_ms); m4_apply_cpu.push_back(metrics.m4_apply_cpu_ms);
      m4_flush_wall.push_back(metrics.m4_flush_wall_ms); m4_flush_cpu.push_back(metrics.m4_flush_cpu_ms);
      compaction_wall.push_back(metrics.compaction_wall_ms);
      compaction_cpu.push_back(metrics.compaction_cpu_ms);
      compaction_flags.push_back(metrics.compacted ? 1 : 0);
      {
        std::unique_lock<std::mutex> lock(sync_mutex);
        sync_cv.wait(lock, [&] { return completed == (round + 1) * 4; });
      }
      rows = std::move(next_rows);
      (void)CollectDeltaRetired(root, std::atomic_load_explicit(&current, std::memory_order_acquire), &retired);
    }
    for (auto& reader : readers) reader.join();
    query_count = reader_latencies.size(); coherence_errors = bad.load();
    const auto sum_cpu = [](const std::vector<double>& values) {
      double total = 0; for (double value : values) total += value; return total;
    };
    reader_query_cpu_total_ms = sum_cpu(reader_query_cpu_ms);
    reader_thread_cpu_total_ms = sum_cpu(reader_thread_cpu_ms);
    writer_commit_thread_cpu_total_ms = sum_cpu(writer_commit_cpu_ms);
    if (coherence_errors != 0 || query_count != rounds * 4)
      throw std::runtime_error("delta benchmark reader coherence failed");
    auto final = std::atomic_load_explicit(&current, std::memory_order_acquire);
    size_t stale_rows = 0;
    for (const auto& shard : final->shards) for (const auto& row : shard->rows) {
      const auto found = final->live_versions.find(row.pk);
      if (found == final->live_versions.end() || found->second != shard->id) ++stale_rows;
    }
    final_root_bytes = DirectoryBytes(root);
    final_generation_id = final->id;
    retained_generation_count = GenerationDirectories(root).size();
    pinned_retired_generation_count = static_cast<size_t>(std::count_if(
        retired.begin(), retired.end(), [](const auto& generation) {
          return !generation.expired();
        }));
    const auto cache_stats_after = m4::Analyzer::CacheStats();
    std::cout << std::setprecision(8)
      << "{\"arm\":\""
      << (!incremental_m4 ? "base-delta-vector" :
          (!reuse_analyzer_cache ? "base-delta-vector-incremental-fts-no-analyzer-cache" :
           (skip_cached_analyzer_fingerprint_verification
                ? "base-delta-vector-incremental-fts-single-fingerprint-cache"
                : "base-delta-vector-incremental-fts-verified-cache")))
      << "\",\"whole_start_monotonic_ns\":"
      << std::chrono::duration_cast<std::chrono::nanoseconds>(
             whole_start.time_since_epoch()).count()
      << ",\"m4_update_strategy\":\""
      << (incremental_m4 ? "hardlink-immutable-runs-and-apply-mutation-delta" : "full-rebuild-per-generation")
      << "\",\"reuse_analyzer_cache\":" << (reuse_analyzer_cache ? "true" : "false")
      << ",\"skip_cached_analyzer_fingerprint_verification\":"
      << (skip_cached_analyzer_fingerprint_verification ? "true" : "false")
      << ",\"base_fingerprint_calls\":" << base_metrics.m4_open_diagnostics.analyzer_fingerprint_calls
      << ",\"base_fingerprint_sha256_calls\":" << base_metrics.m4_open_diagnostics.analyzer_fingerprint_sha256_calls
      << ",\"base_fingerprint_dictionary_bytes\":" << base_metrics.m4_open_diagnostics.analyzer_fingerprint_dictionary_bytes
      << ",\"base_fingerprint_sha256_bytes\":" << base_metrics.m4_open_diagnostics.analyzer_fingerprint_sha256_bytes
      << ",\"base_analyzer_cache_hit\":" << (base_metrics.m4_open_diagnostics.analyzer_cache_hit ? "true" : "false")
      << ",\"base_analyzer_cache_waited\":" << (base_metrics.m4_open_diagnostics.analyzer_cache_waited ? "true" : "false")
      << ",\"base_analyzer_cache_wait_reuse\":" << (base_metrics.m4_open_diagnostics.analyzer_cache_wait_reuse ? "true" : "false")
      << ",\"base_analyzer_dictionary_snapshot_used\":" << (base_metrics.m4_open_diagnostics.analyzer_dictionary_snapshot_used ? "true" : "false")
      << ",\"base_analyzer_initialization_attempted\":" << (base_metrics.m4_open_diagnostics.analyzer_initialization_attempted ? "true" : "false")
      << ",\"base_fingerprint_verification_skipped\":" << (base_metrics.m4_open_diagnostics.fingerprint_verification_skipped ? "true" : "false")
      << ",\"docs\":" << documents
      << ",\"rounds\":" << rounds << ",\"reader_threads\":4,\"queries\":" << query_count
      << ",\"base_build_ms\":" << base_wall
      << ",\"base_vector_build_wall_ms\":" << base_vector_wall
      << ",\"base_vector_build_writer_thread_cpu_ms\":" << base_vector_cpu
      << ",\"base_m4_build_wall_ms\":" << base_m4_wall
      << ",\"base_m4_build_writer_thread_cpu_ms\":" << base_m4_cpu
      << ",\"commit_p50_ms\":" << PercentileMs(commits,.50)
      << ",\"commit_p95_ms\":" << PercentileMs(commits,.95)
      << ",\"commit_p99_ms\":" << PercentileMs(commits,.99)
      << ",\"commit_max_ms\":" << PercentileMs(commits,1.0)
      << ",\"reader_p50_ms\":" << PercentileMs(reader_latencies,.50)
      << ",\"reader_p95_ms\":" << PercentileMs(reader_latencies,.95)
      << ",\"reader_p99_ms\":" << PercentileMs(reader_latencies,.99)
      << ",\"reader_max_ms\":" << PercentileMs(reader_latencies,1.0)
      << ",\"reader_query_cpu_total_ms\":" << reader_query_cpu_total_ms
      << ",\"reader_thread_cpu_total_ms\":" << reader_thread_cpu_total_ms
      << ",\"reader_coordination_cpu_ms\":" << (reader_thread_cpu_total_ms - reader_query_cpu_total_ms)
      << ",\"writer_commit_thread_cpu_total_ms\":" << writer_commit_thread_cpu_total_ms
      << ",\"reader_blocked_wait_ms\":\"NOT_MEASURED\""
      << ",\"peak_cpu_percent\":\"NOT_MEASURED\",\"peak_pss_kb\":\"NOT_MEASURED\""
      << ",\"native_vector_build_wall_p50_ms\":" << PercentileMs(vector_wall,.50)
      << ",\"native_vector_build_writer_thread_cpu_p50_ms\":" << PercentileMs(vector_cpu,.50)
      << ",\"m4_update_wall_p50_ms\":" << PercentileMs(m4_wall,.50)
      << ",\"m4_update_writer_thread_cpu_p50_ms\":" << PercentileMs(m4_cpu,.50)
      << ",\"m4_clone_writer_thread_cpu_p50_ms\":" << PercentileMs(m4_clone_cpu,.50)
      << ",\"m4_open_wall_p50_ms\":" << PercentileMs(m4_open_wall,.50)
      << ",\"m4_open_wall_p95_ms\":" << PercentileMs(m4_open_wall,.95)
      << ",\"m4_open_writer_thread_cpu_p50_ms\":" << PercentileMs(m4_open_cpu,.50)
      << ",\"m4_open_writer_thread_cpu_p95_ms\":" << PercentileMs(m4_open_cpu,.95)
      << ",\"m4_apply_writer_thread_cpu_p50_ms\":" << PercentileMs(m4_apply_cpu,.50)
      << ",\"m4_flush_writer_thread_cpu_p50_ms\":" << PercentileMs(m4_flush_cpu,.50)
      << ",\"vector_compaction_wall_p50_ms\":" << PercentileMs(compaction_wall,.50)
      << ",\"vector_compaction_writer_thread_cpu_p50_ms\":" << PercentileMs(compaction_cpu,.50)
      << ",\"compaction_physical_bytes\":\"NOT_MEASURED\""
      << ",\"generation_publish_p50_ms\":" << PercentileMs(publish_wall,.50)
      << ",\"retirement_p50_ms\":" << PercentileMs(retirement_wall,.50)
      << ",\"retained_root_logical_bytes\":" << final_root_bytes
      << ",\"retained_generation_directories\":" << retained_generation_count
      << ",\"pinned_retired_generations\":" << pinned_retired_generation_count
      << ",\"published_generation_id\":" << final_generation_id
      << ",\"current_shards\":" << final->shards.size()
      << ",\"current_stale_vector_rows\":" << stale_rows
      << ",\"compactions\":" << std::count(compaction_flags.begin(),compaction_flags.end(),uint8_t{1})
      << ",\"coherence_errors\":" << coherence_errors
      << ",\"analyzer_cache_lookups\":" << (cache_stats_after.lookups - cache_stats_before.lookups)
      << ",\"analyzer_cache_lookup_hits\":" << (cache_stats_after.lookup_hits - cache_stats_before.lookup_hits)
      << ",\"analyzer_cache_lookup_misses\":" << (cache_stats_after.lookup_misses - cache_stats_before.lookup_misses)
      << ",\"analyzer_cache_expired_weak_entries\":" << (cache_stats_after.expired_weak_entries - cache_stats_before.expired_weak_entries)
      << ",\"analyzer_initialization_attempts\":" << (cache_stats_after.initialization_attempts - cache_stats_before.initialization_attempts)
      << ",\"analyzer_successful_initializations\":" << (cache_stats_after.successful_initializations - cache_stats_before.successful_initializations)
      << ",\"analyzer_late_race_reuses\":" << (cache_stats_after.late_race_reuses - cache_stats_before.late_race_reuses)
      << ",\"analyzer_cache_waits\":" << (cache_stats_after.cache_waits - cache_stats_before.cache_waits)
      << ",\"analyzer_cache_wait_reuses\":" << (cache_stats_after.cache_wait_reuses - cache_stats_before.cache_wait_reuses)
      << ",\"analyzer_cache_bypasses\":" << (cache_stats_after.cache_bypasses - cache_stats_before.cache_bypasses);
    PrintArray("commit_samples_ms", commits); PrintArray("commit_write_bytes", commit_write_bytes);
    PrintArray("reader_samples_ms", reader_latencies);
    PrintArray("reader_query_cpu_samples_ms", reader_query_cpu_ms);
    PrintArray("reader_thread_cpu_samples_ms", reader_thread_cpu_ms);
    PrintArray("writer_commit_thread_cpu_samples_ms", writer_commit_cpu_ms);
    PrintArray("logical_mutation_count_per_round", mutation_count_per_round);
    PrintArray("logical_upserts_per_round", mutation_upserts_per_round);
    PrintArray("logical_deletes_per_round", mutation_deletes_per_round);
    PrintArray("vector_rows_written_per_round", vector_rows_written_per_round);
    PrintArray("vector_upsert_calls_per_round", vector_upsert_calls_per_round);
    PrintArray("m4_rows_indexed_per_round", m4_rows_indexed_per_round);
    PrintArray("m4_apply_batch_calls_per_round", m4_apply_batch_calls_per_round);
    PrintArray("m4_mutations_applied_per_round", m4_mutations_per_round);
    PrintArray("m4_shared_segment_files_per_round", m4_shared_segments_per_round);
    PrintArray("m4_copied_state_bytes_per_round", m4_copied_state_bytes_per_round);
    PrintArray("m4_clone_wall_samples_ms", m4_clone_wall); PrintArray("m4_clone_writer_thread_cpu_samples_ms", m4_clone_cpu);
    PrintArray("m4_open_wall_samples_ms", m4_open_wall); PrintArray("m4_open_writer_thread_cpu_samples_ms", m4_open_cpu);
    PrintArray("m4_open_total_cpu_ms", m4_open_total_cpu);
    PrintArray("m4_open_analyzer_fingerprint_wall_ms", m4_open_analyzer);
    PrintArray("m4_open_analyzer_fingerprint_cpu_ms", m4_open_analyzer_cpu);
    PrintArray("m4_open_analyzer_init_wall_ms", m4_open_analyzer_init);
    PrintArray("m4_open_analyzer_init_cpu_ms", m4_open_analyzer_init_cpu);
    PrintArray("m4_open_fingerprint_verification_wall_ms", m4_open_fingerprint_verify);
    PrintArray("m4_open_fingerprint_verification_cpu_ms", m4_open_fingerprint_verify_cpu);
    PrintArray("m4_open_manifest_analyzer_wall_ms", m4_open_manifest);
    PrintArray("m4_open_analyzer_cache_hits", m4_open_cache_hits);
    PrintArray("m4_open_fingerprint_calls", m4_open_fingerprint_calls);
    PrintArray("m4_open_fingerprint_sha256_calls", m4_open_fingerprint_sha256_calls);
    PrintArray("m4_open_fingerprint_dictionary_bytes", m4_open_fingerprint_dictionary_bytes);
    PrintArray("m4_open_fingerprint_sha256_bytes", m4_open_fingerprint_sha256_bytes);
    PrintArray("m4_open_cache_misses", m4_open_cache_misses);
    PrintArray("m4_open_cache_race_reuses", m4_open_cache_races);
    PrintArray("m4_open_cache_waits", m4_open_cache_waits);
    PrintArray("m4_open_cache_wait_reuses", m4_open_cache_wait_reuses);
    PrintArray("m4_open_dictionary_snapshots", m4_open_dictionary_snapshots);
    PrintArray("m4_open_cache_expired_entries", m4_open_cache_expired);
    PrintArray("m4_open_analyzer_initialization_attempted", m4_open_init_attempts);
    PrintArray("m4_open_analyzer_initialization_succeeded", m4_open_init_successes);
    PrintArray("m4_open_fingerprint_verification_skipped", m4_open_verification_skipped);
    PrintArray("m4_open_segments_wall_ms", m4_open_segments);
    PrintArray("m4_open_segments_cpu_ms", m4_open_segments_cpu);
    PrintArray("m4_open_tombstones_wall_ms", m4_open_tombstones);
    PrintArray("m4_open_stats_wall_ms", m4_open_stats);
    PrintArray("m4_open_wal_replay_wall_ms", m4_open_wal);
    PrintArray("m4_open_live_key_map_wall_ms", m4_open_key_map);
    PrintArray("m4_open_garbage_collect_wall_ms", m4_open_gc);
    PrintArray("m4_open_segment_count", m4_open_segment_count);
    PrintArray("m4_open_segment_bytes", m4_open_segment_bytes);
    PrintArray("m4_apply_wall_samples_ms", m4_apply_wall); PrintArray("m4_apply_writer_thread_cpu_samples_ms", m4_apply_cpu);
    PrintArray("m4_flush_wall_samples_ms", m4_flush_wall); PrintArray("m4_flush_writer_thread_cpu_samples_ms", m4_flush_cpu);
    PrintArray("native_vector_open_wall_samples_ms", vector_open_wall);
    PrintArray("native_vector_open_writer_thread_cpu_samples_ms", vector_open_cpu);
    PrintArray("native_vector_document_conversion_wall_samples_ms", vector_convert_wall);
    PrintArray("native_vector_document_conversion_writer_thread_cpu_samples_ms", vector_convert_cpu);
    PrintArray("native_vector_upsert_wall_samples_ms", vector_upsert_wall);
    PrintArray("native_vector_upsert_writer_thread_cpu_samples_ms", vector_upsert_cpu);
    PrintArray("native_vector_flush_wall_samples_ms", vector_flush_wall);
    PrintArray("native_vector_flush_writer_thread_cpu_samples_ms", vector_flush_cpu);
    PrintArray("generation_metadata_wall_samples_ms", generation_metadata_wall);
    PrintArray("generation_metadata_writer_thread_cpu_samples_ms", generation_metadata_cpu);
    PrintArray("post_m4_sync_tree_wall_samples_ms", post_m4_sync_wall);
    PrintArray("post_m4_sync_tree_writer_thread_cpu_samples_ms", post_m4_sync_cpu);
    PrintArray("snapshot_finalize_wall_samples_ms", snapshot_finalize_wall);
    PrintArray("snapshot_finalize_writer_thread_cpu_samples_ms", snapshot_finalize_cpu);
    PrintArray("native_vector_build_wall_samples_ms", vector_wall);
    PrintArray("native_vector_build_writer_thread_cpu_samples_ms", vector_cpu); PrintArray("m4_update_wall_samples_ms", m4_wall);
    PrintArray("m4_update_writer_thread_cpu_samples_ms", m4_cpu); PrintArray("compaction_wall_samples_ms", compaction_wall);
    PrintArray("compaction_writer_thread_cpu_samples_ms", compaction_cpu); PrintArray("generation_publish_samples_ms", publish_wall);
    PrintArray("retirement_samples_ms", retirement_wall);
    const auto offset_ms = [&whole_start](std::chrono::steady_clock::time_point point) {
      if (point == std::chrono::steady_clock::time_point{}) return -1.0;
      return std::chrono::duration<double, std::milli>(point - whole_start).count();
    };
    const auto duration_ms = [](auto start, auto end) {
      return std::chrono::duration<double, std::milli>(end - start).count();
    };
    std::cout << ",\"reader_stage_timeline_ms\":[";
    for (size_t i = 0; i < reader_stage_events.size(); ++i) {
      const auto& e = reader_stage_events[i];
      if (i) std::cout << ',';
      std::cout << "{\"round\":" << e.round << ",\"reader\":" << e.reader
        << ",\"reader_tid\":" << e.reader_tid
        << ",\"acquire_release_ms\":" << offset_ms(e.acquire_release)
        << ",\"acquire_end_ms\":" << offset_ms(e.acquire_end)
        << ",\"reader_dispatch_wait_ms\":" << duration_ms(e.acquire_release, e.acquire_end)
        << ",\"snapshot_pin_start_ms\":" << offset_ms(e.pin_start)
        << ",\"snapshot_pin_end_ms\":" << offset_ms(e.pin_end)
        << ",\"snapshot_pin_ms\":" << duration_ms(e.pin_start, e.pin_end)
        << ",\"native_vector_stage_start_ms\":" << offset_ms(e.stages.vector_start)
        << ",\"native_vector_stage_end_ms\":" << offset_ms(e.stages.vector_end)
        << ",\"native_vector_stage_ms\":" << duration_ms(e.stages.vector_start, e.stages.vector_end)
        << ",\"native_vector_search_ms\":" << e.stages.native_vector_search_ms
        << ",\"vector_backend_query_lock_acquire_events\":[";
      for (size_t j = 0; j < e.stages.vector_lock_acquires.size(); ++j) {
        const auto& lock = e.stages.vector_lock_acquires[j];
        if (j) std::cout << ',';
        std::cout << "{\"wait_start_ms\":" << offset_ms(lock.wait_start)
          << ",\"acquired_ms\":" << offset_ms(lock.acquired)
          << ",\"wait_ms\":" << duration_ms(lock.wait_start, lock.acquired) << '}';
      }
      std::cout << "],\"m4_backend_query_lock_wait_start_ms\":"
        << offset_ms(e.stages.m4_lock_acquire.wait_start)
        << ",\"m4_backend_query_lock_acquired_ms\":"
        << offset_ms(e.stages.m4_lock_acquire.acquired)
        << ",\"m4_backend_query_lock_acquire_ms\":"
        << duration_ms(e.stages.m4_lock_acquire.wait_start, e.stages.m4_lock_acquire.acquired)
        << ",\"m4_search_start_ms\":" << offset_ms(e.stages.m4_start)
        << ",\"m4_search_end_ms\":" << offset_ms(e.stages.m4_end)
        << ",\"m4_search_ms\":" << duration_ms(e.stages.m4_start, e.stages.m4_end)
        << ",\"merge_start_ms\":" << offset_ms(e.stages.merge_start)
        << ",\"merge_end_ms\":" << offset_ms(e.stages.merge_end)
        << ",\"merge_ms\":" << duration_ms(e.stages.merge_start, e.stages.merge_end)
        << ",\"snapshot_release_start_ms\":" << offset_ms(e.pin_release_start)
        << ",\"snapshot_release_end_ms\":" << offset_ms(e.pin_release_end)
        << ",\"snapshot_release_ms\":" << duration_ms(e.pin_release_start, e.pin_release_end)
        << ",\"end_to_end_ms\":" << duration_ms(e.acquire_release, e.pin_release_end) << '}';
    }
    std::cout << "],\"writer_stage_timeline_ms\":[";
    for (size_t i = 0; i < writer_stage_events.size(); ++i) {
      const auto& e = writer_stage_events[i];
      if (i) std::cout << ',';
      std::cout << "{\"round\":" << e.round << ",\"writer_tid\":" << e.writer_tid
        << ",\"compacted\":" << (e.compacted ? "true" : "false")
        << ",\"round_start_ms\":" << offset_ms(e.round_start)
        << ",\"query_release_ms\":" << offset_ms(e.query_release)
        << ",\"build_start_ms\":" << offset_ms(e.build_start)
        << ",\"build_end_ms\":" << offset_ms(e.build_end)
        << ",\"generation_wal_start_ms\":" << offset_ms(e.generation_wal_start)
        << ",\"generation_wal_end_ms\":" << offset_ms(e.generation_wal_end)
        << ",\"generation_wal_cpu_ms\":" << e.generation_wal_cpu_ms
        << ",\"vector_start_ms\":" << offset_ms(e.vector_start)
        << ",\"vector_end_ms\":" << offset_ms(e.vector_end)
        << ",\"vector_open_start_ms\":" << offset_ms(e.vector_open_start)
        << ",\"vector_open_end_ms\":" << offset_ms(e.vector_open_end)
        << ",\"vector_flush_start_ms\":" << offset_ms(e.vector_flush_start)
        << ",\"vector_flush_end_ms\":" << offset_ms(e.vector_flush_end)
        << ",\"generation_metadata_start_ms\":" << offset_ms(e.generation_metadata_start)
        << ",\"generation_metadata_end_ms\":" << offset_ms(e.generation_metadata_end)
        << ",\"m4_clone_start_ms\":" << offset_ms(e.m4_clone_start)
        << ",\"m4_clone_end_ms\":" << offset_ms(e.m4_clone_end)
        << ",\"m4_open_start_ms\":" << offset_ms(e.m4_open_start)
        << ",\"m4_open_end_ms\":" << offset_ms(e.m4_open_end)
        << ",\"m4_apply_start_ms\":" << offset_ms(e.m4_apply_start)
        << ",\"m4_apply_end_ms\":" << offset_ms(e.m4_apply_end)
        << ",\"m4_flush_start_ms\":" << offset_ms(e.m4_flush_start)
        << ",\"m4_flush_end_ms\":" << offset_ms(e.m4_flush_end)
        << ",\"post_m4_sync_start_ms\":" << offset_ms(e.post_m4_sync_start)
        << ",\"post_m4_sync_end_ms\":" << offset_ms(e.post_m4_sync_end)
        << ",\"snapshot_finalize_start_ms\":" << offset_ms(e.snapshot_finalize_start)
        << ",\"snapshot_finalize_end_ms\":" << offset_ms(e.snapshot_finalize_end)
        << ",\"compaction_start_ms\":" << offset_ms(e.compaction_start)
        << ",\"compaction_end_ms\":" << offset_ms(e.compaction_end)
        << ",\"m4_build_start_ms\":" << offset_ms(e.m4_start)
        << ",\"m4_build_end_ms\":" << offset_ms(e.m4_end)
        << ",\"manifest_publish_start_ms\":" << offset_ms(e.manifest_publish_start)
        << ",\"manifest_publish_end_ms\":" << offset_ms(e.manifest_publish_end)
        << ",\"snapshot_publish_start_ms\":" << offset_ms(e.snapshot_publish_start)
        << ",\"snapshot_publish_end_ms\":" << offset_ms(e.snapshot_publish_end)
        << ",\"retirement_start_ms\":" << offset_ms(e.retirement_start)
        << ",\"retirement_end_ms\":" << offset_ms(e.retirement_end)
        << ",\"round_end_ms\":" << offset_ms(e.round_end) << '}';
    }
    std::cout << "]}\n";
    current.reset();
  }
  const ProcessMeasurement after = MeasureProcess();
  const double whole_process_cpu_ms = after.cpu_ms - before.cpu_ms;
  std::cout << "{\"arm\":\""
      << (!incremental_m4 ? "base-delta-vector-resources" :
          (!reuse_analyzer_cache ? "incremental-fts-no-analyzer-cache-resources" :
           (skip_cached_analyzer_fingerprint_verification
                ? "incremental-fts-single-fingerprint-cache-resources"
                : "incremental-fts-verified-cache-resources")))
      << "\",\"whole_process_cpu_ms\":"
      << whole_process_cpu_ms << ",\"reader_query_cpu_total_ms\":" << reader_query_cpu_total_ms
      << ",\"reader_thread_cpu_total_ms\":" << reader_thread_cpu_total_ms
      << ",\"reader_coordination_cpu_ms\":" << (reader_thread_cpu_total_ms - reader_query_cpu_total_ms)
      << ",\"writer_commit_thread_cpu_total_ms\":" << writer_commit_thread_cpu_total_ms
      << ",\"base_writer_thread_cpu_ms\":" << base_writer_thread_cpu_ms
      << ",\"process_cpu_excluding_reader_threads_ms\":" << (whole_process_cpu_ms - reader_thread_cpu_total_ms)
      << ",\"unattributed_process_cpu_ms\":" << (whole_process_cpu_ms - reader_thread_cpu_total_ms - writer_commit_thread_cpu_total_ms - base_writer_thread_cpu_ms)
      << ",\"physical_write_bytes\":"
      << after.write_bytes - before.write_bytes << ",\"peak_rss_kb\":" << after.peak_rss_kb
      << ",\"peak_pss_kb\":\"NOT_MEASURED\",\"peak_cpu_percent\":\"NOT_MEASURED\""
      << ",\"root_bytes_after_close\":" << DirectoryBytes(root)
      << ",\"whole_wall_ms\":" << std::chrono::duration<double,std::milli>(
          std::chrono::steady_clock::now()-whole_start).count() << "}\n";
  const ProcessMeasurement recovery_before = MeasureProcess();
  const auto recovery_start = std::chrono::steady_clock::now();
  auto recovered = OpenDeltaVectorSnapshot(root, final_generation_id, dictionary);
  const double recovery_open_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - recovery_start).count();
  const auto recovery_query = recovered->Hybrid(rows.front().vector,
                                                "request timeout",
                                                std::min<size_t>(50, documents));
  if (recovered->id != final_generation_id || recovered->rows.size() != documents ||
      recovered->fts->GetStats().live_docs != documents ||
      recovery_query.generation != final_generation_id ||
      recovery_query.hits.size() != std::min<size_t>(50, documents))
    throw std::runtime_error("post-close generation reopen/recovery mismatch");
  recovered.reset();
  const ProcessMeasurement recovery_after = MeasureProcess();
  std::cout << "{\"arm\":\""
      << (!incremental_m4 ? "base-delta-vector-post-close-reopen" :
          (!reuse_analyzer_cache ? "incremental-fts-no-cache-post-close-reopen" :
           (skip_cached_analyzer_fingerprint_verification
                ? "incremental-fts-single-fingerprint-cache-post-close-reopen"
                : "incremental-fts-verified-cache-post-close-reopen")))
      << "\",\"generation\":" << final_generation_id
      << ",\"snapshot_reopen_wall_ms\":" << recovery_open_ms
      << ",\"reopen_process_cpu_ms\":" << recovery_after.cpu_ms - recovery_before.cpu_ms
      << ",\"reopen_physical_write_bytes\":" << recovery_after.write_bytes - recovery_before.write_bytes
      << ",\"peak_rss_kb\":" << recovery_after.peak_rss_kb
      << ",\"fts_live_docs\":" << documents
      << ",\"vector_fts_coherence_errors\":0}\n";
  Need(zvec_shutdown(), "shutdown delta-vector performance arm");
  return 0;
}
int RunReaderCompactionCrossover(const fs::path& root, const char* dictionary,
                                 bool search_profile = false,
                                 bool capture_search_diagnostics = true,
                                 bool capture_search_counters = true,
                                 bool capture_search_timing = true) {
  constexpr size_t kDocuments = 1024;
  constexpr size_t kReaders = 4;
  constexpr size_t kQueryIds = 8;
  constexpr size_t kRepeats = 4;
  constexpr size_t kWaves = kQueryIds * kRepeats / kReaders;
  constexpr size_t kTopK = 32;
  const std::string search_diagnostics_mode = !capture_search_diagnostics
      ? "off"
      : (capture_search_counters && capture_search_timing
             ? "counters-and-timing"
             : (capture_search_counters ? "counters-only" : "timing-only"));
  using Clock = std::chrono::steady_clock;
  if (fs::exists(root) && !fs::is_empty(root))
    throw std::runtime_error("reader-compaction experiment requires a fresh root");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize reader-compaction crossover");
  const auto experiment_start = Clock::now();
  const auto offset_ms = [&](Clock::time_point point) {
    return std::chrono::duration<double, std::milli>(point - experiment_start).count();
  };
  struct QuerySpec {
    size_t id{0};
    std::array<float, kDimension> vector{};
    std::string text;
    size_t topk{kTopK};
    std::string vector_filter;
    std::string filter_label{"all"};
    size_t expected_filter_matches{0};
  };
  struct QuerySample {
    size_t wave{0}, reader{0}, query_id{0}, repetition{0}, topk{kTopK};
    uint64_t reader_tid{0};
    std::string vector_filter;
    std::string filter_label{"all"};
    size_t expected_filter_matches{0};
    Clock::time_point start{}, end{};
    double cpu_ms{0};
    GenerationRead read;
    std::string hit_signature;
  };
  struct ArmResult {
    size_t pair{0}, order{0};
    bool compaction{false};
    uint32_t pinned_generation{0};
    size_t pinned_shards{0};
    Clock::time_point condition_start{}, compaction_start{}, compaction_end{};
    double compaction_cpu_ms{0}, compaction_wall_ms{0};
    std::vector<QuerySample> queries;
    std::vector<PsiReceipt> psi;
  };
  std::mutex psi_mutex;
  const auto sample_psi = [&](ArmResult* arm, const std::string& label) {
    PsiReceipt receipt;
    receipt.label = label;
    receipt.cpu = ReadProcText("/proc/pressure/cpu");
    receipt.io = ReadProcText("/proc/pressure/io");
    receipt.memory = ReadProcText("/proc/pressure/memory");
    receipt.offset_ms = offset_ms(Clock::now());
    std::lock_guard<std::mutex> lock(psi_mutex);
    arm->psi.push_back(std::move(receipt));
  };
  const std::array<std::string, kQueryIds> query_texts = {
      "request timeout", "parse request", "json handler", "log cache",
      "response source", "cache timeout", "handler response", "request source"};
  const auto build_m4_delta = [](const std::vector<BatchMutation>& mutations) {
    std::vector<m4::Mutation> delta;
    delta.reserve(mutations.size());
    for (const auto& mutation : mutations) {
      if (mutation.kind == BatchMutation::Kind::DELETE) {
        delta.emplace_back(m4::MutationKind::DELETE, "1:" + mutation.pk);
      } else {
        if (!mutation.row) throw std::runtime_error("crossover upsert has no row");
        delta.emplace_back(m4::MutationKind::UPSERT, "1:" + mutation.pk,
                           M4Doc(*mutation.row, 1));
      }
    }
    return delta;
  };
  const auto prepare_snapshot = [&](const fs::path& arm_root) {
    fs::create_directories(arm_root);
    auto rows = MakeChurnWorkload(kDocuments);
    std::map<std::string, uint32_t> versions;
    for (const auto& row : rows) versions[row.pk] = 1;
    AppendGenerationWal(arm_root, 'R', 1);
    auto current = BuildDeltaVectorSnapshot(arm_root, 1, rows, rows, {},
        std::move(versions), dictionary, nullptr, nullptr, nullptr, true, false);
    PublishDeltaGeneration(arm_root, 1, 1);
    for (size_t round = 0; round < 3; ++round) {
      std::vector<Row> next_rows;
      const auto mutations = ChurnBatch(rows, round, &next_rows);
      std::vector<Row> vector_delta;
      auto next_versions = current->live_versions;
      const uint32_t generation = current->id + 1;
      for (const auto& mutation : mutations) {
        if (mutation.kind == BatchMutation::Kind::DELETE) {
          next_versions.erase(mutation.pk);
        } else {
          if (!mutation.row) throw std::runtime_error("crossover upsert has no row");
          vector_delta.push_back(*mutation.row);
          next_versions[mutation.pk] = generation;
        }
      }
      const auto m4_delta = build_m4_delta(mutations);
      const fs::path old_fts = current->directory / "fts";
      AppendGenerationWal(arm_root, 'R', generation);
      auto next = BuildDeltaVectorSnapshot(arm_root, generation, next_rows,
          vector_delta, current->shards, std::move(next_versions), dictionary,
          nullptr, &old_fts, &m4_delta, true, false);
      PublishDeltaGeneration(arm_root, generation, generation);
      rows = std::move(next_rows);
      current = std::move(next);
    }
    if (current->id != 4 || current->shards.size() != 4 || current->rows.size() != kDocuments)
      throw std::runtime_error("crossover preparation did not produce the expected four-shard snapshot");
    return current;
  };
  const auto run_arm = [&](size_t pair, size_t order, bool with_compaction) {
    ArmResult arm;
    arm.pair = pair;
    arm.order = order;
    arm.compaction = with_compaction;
    const std::string condition = with_compaction ? "compaction" : "control";
    const fs::path arm_root = root / ("pair-" + std::to_string(pair) + "-" + condition);
    auto current = prepare_snapshot(arm_root);
    arm.pinned_generation = current->id;
    arm.pinned_shards = current->shards.size();
    std::vector<QuerySpec> specs;
    specs.reserve(kQueryIds);
    for (size_t id = 0; id < kQueryIds; ++id) {
      const size_t row_index = ((id + 1) * kDocuments) / (kQueryIds + 1);
      QuerySpec spec;
      spec.id = id;
      spec.vector = current->rows[row_index].vector;
      spec.text = query_texts[id];
      spec.expected_filter_matches = current->rows.size();
      if (search_profile) {
        spec.topk = id < kQueryIds / 2 ? 8 : 64;
        switch (id % 4) {
          case 1:
            spec.vector_filter = "file_name LIKE 'generated-%'";
            spec.filter_label = "file_name_prefix_about_1_of_3";
            spec.expected_filter_matches = static_cast<size_t>(std::count_if(
                current->rows.begin(), current->rows.end(), [](const Row& row) {
                  return row.file_name.rfind("generated-", 0) == 0;
                }));
            break;
          case 2:
            spec.vector_filter = "symbol_type = 'function'";
            spec.filter_label = "symbol_type_about_1_of_2";
            spec.expected_filter_matches = static_cast<size_t>(std::count_if(
                current->rows.begin(), current->rows.end(), [](const Row& row) {
                  return row.symbol_type == "function";
                }));
            break;
          case 3:
            spec.vector_filter = "file_id = " + std::to_string(current->rows[row_index].file_id);
            spec.filter_label = "file_id_exact_sparse";
            spec.expected_filter_matches = static_cast<size_t>(std::count_if(
                current->rows.begin(), current->rows.end(), [&](const Row& row) {
                  return row.file_id == current->rows[row_index].file_id;
                }));
            break;
          default:
            break;
        }
      }
      specs.push_back(std::move(spec));
    }
    arm.queries.resize(kQueryIds * kRepeats);
    std::mutex reader_mutex;
    std::condition_variable reader_cv;
    size_t ready_readers = 0, released_waves = 0, completed_queries = 0;
    bool abort_readers = false;
    std::exception_ptr reader_error;
    std::vector<std::thread> readers;
    readers.reserve(kReaders);
    for (size_t reader = 0; reader < kReaders; ++reader) {
      readers.emplace_back([&, reader] {
        std::exception_ptr warmup_error;
        try {
          for (size_t id = reader; id < kQueryIds; id += kReaders) {
            const auto warmed = current->Hybrid(specs[id].vector, specs[id].text,
                                                specs[id].topk, {}, false, false, true,
                                                specs[id].vector_filter);
            if (warmed.hits.empty()) throw std::runtime_error("reader-thread warmup produced no hits");
          }
        } catch (...) {
          warmup_error = std::current_exception();
        }
        {
          std::lock_guard<std::mutex> lock(reader_mutex);
          if (warmup_error && !reader_error) reader_error = warmup_error;
          ++ready_readers;
        }
        reader_cv.notify_all();
        if (warmup_error) return;
        for (size_t wave = 0; wave < kWaves; ++wave) {
          {
            std::unique_lock<std::mutex> lock(reader_mutex);
            reader_cv.wait(lock, [&] { return abort_readers || released_waves > wave; });
            if (abort_readers) return;
          }
          const size_t slot = wave * kReaders + reader;
          try {
            const auto& spec = specs[slot % kQueryIds];
            QuerySample sample;
            sample.wave = wave;
            sample.reader = reader;
            sample.query_id = spec.id;
            sample.repetition = slot / kQueryIds;
            sample.topk = spec.topk;
            sample.vector_filter = spec.vector_filter;
            sample.filter_label = spec.filter_label;
            sample.expected_filter_matches = spec.expected_filter_matches;
            sample.reader_tid = CurrentThreadTid();
            sample.start = Clock::now();
            const double cpu_start = CurrentThreadCpuMs();
            sample.read = current->Hybrid(
                spec.vector, spec.text, spec.topk, {}, true,
                search_profile && capture_search_diagnostics, false,
                spec.vector_filter, capture_search_counters,
                capture_search_timing);
            if (std::any_of(sample.read.stages.native_vector_calls.begin(),
                            sample.read.stages.native_vector_calls.end(),
                            [&](const NativeCallMetric& call) {
                              return call.caller_tid != sample.reader_tid;
                            }))
              throw std::runtime_error("native API wrapper timing changed reader thread owner");
            sample.cpu_ms = CurrentThreadCpuMs() - cpu_start;
            sample.end = Clock::now();
            std::ostringstream signature;
            for (const auto& hit : sample.read.hits) {
              if (current->live_versions.find(hit.pk) == current->live_versions.end())
                throw std::runtime_error("crossover query returned a non-live PK");
              signature << hit.pk << '\n';
            }
            sample.hit_signature = signature.str();
            arm.queries[slot] = std::move(sample);
          } catch (...) {
            std::lock_guard<std::mutex> lock(reader_mutex);
            if (!reader_error) reader_error = std::current_exception();
          }
          {
            std::lock_guard<std::mutex> lock(reader_mutex);
            ++completed_queries;
          }
          reader_cv.notify_all();
        }
      });
    }
    {
      std::unique_lock<std::mutex> lock(reader_mutex);
      reader_cv.wait(lock, [&] { return ready_readers == kReaders; });
      if (reader_error) {
        const auto warmup_error = reader_error;
        abort_readers = true;
        lock.unlock();
        reader_cv.notify_all();
        for (auto& reader : readers) reader.join();
        std::rethrow_exception(warmup_error);
      }
    }
    std::vector<Row> next_rows;
    std::vector<Row> vector_delta;
    std::map<std::string, uint32_t> next_versions;
    std::vector<m4::Mutation> m4_delta;
    const uint32_t next_generation = current->id + 1;
    fs::path old_fts;
    if (with_compaction) {
      const auto mutations = ChurnBatch(current->rows, 3, &next_rows);
      next_versions = current->live_versions;
      for (const auto& mutation : mutations) {
        if (mutation.kind == BatchMutation::Kind::DELETE) {
          next_versions.erase(mutation.pk);
        } else {
          if (!mutation.row) throw std::runtime_error("crossover compaction upsert has no row");
          vector_delta.push_back(*mutation.row);
          next_versions[mutation.pk] = next_generation;
        }
      }
      m4_delta = build_m4_delta(mutations);
      old_fts = current->directory / "fts";
    }
    arm.condition_start = Clock::now();
    sample_psi(&arm, "condition-start");

    std::thread writer;
    std::mutex start_mutex, compaction_mutex;
    std::condition_variable start_cv, compaction_cv;
    bool first_batch_done = false, writer_done = false;
    bool query_phase_done = false;
    std::exception_ptr writer_error;
    DeltaBuildMetrics compaction_metrics;
    std::shared_ptr<DeltaVectorSnapshot> next_snapshot;
    if (with_compaction) {
      writer = std::thread([&] {
        try {
          AppendGenerationWal(arm_root, 'R', next_generation);
          next_snapshot = BuildDeltaVectorSnapshot(arm_root, next_generation,
              next_rows, vector_delta, current->shards, std::move(next_versions),
              dictionary, &compaction_metrics, &old_fts, &m4_delta, true, false,
              [&] {
                sample_psi(&arm, "after-first-compaction-batch");
                {
                  std::lock_guard<std::mutex> lock(start_mutex);
                  first_batch_done = true;
                }
                start_cv.notify_all();
              },
              [&] {
                sample_psi(&arm, "vector-compaction-end");
                {
                  std::unique_lock<std::mutex> lock(compaction_mutex);
                  compaction_cv.notify_all();
                  compaction_cv.wait(lock, [&] { return query_phase_done; });
                }
              });
        } catch (...) {
          std::lock_guard<std::mutex> lock(start_mutex);
          writer_error = std::current_exception();
        }
        {
          std::lock_guard<std::mutex> lock(start_mutex);
          writer_done = true;
        }
        start_cv.notify_all();
      });
      std::unique_lock<std::mutex> lock(start_mutex);
      start_cv.wait(lock, [&] { return first_batch_done || writer_done; });
      const auto early_error = writer_error;
      lock.unlock();
      if (early_error || !first_batch_done) {
        {
          std::lock_guard<std::mutex> reader_lock(reader_mutex);
          abort_readers = true;
        }
        reader_cv.notify_all();
        for (auto& reader : readers) reader.join();
        if (writer.joinable()) writer.join();
        if (early_error) std::rethrow_exception(early_error);
        throw std::runtime_error("compaction writer ended before first vector batch");
      }
    }
    for (size_t wave = 0; wave < kWaves; ++wave) {
      {
        std::lock_guard<std::mutex> lock(reader_mutex);
        released_waves = wave + 1;
      }
      reader_cv.notify_all();
      std::unique_lock<std::mutex> lock(reader_mutex);
      reader_cv.wait(lock, [&] { return completed_queries >= (wave + 1) * kReaders; });
    }
    if (with_compaction) {
      {
        std::lock_guard<std::mutex> lock(compaction_mutex);
        query_phase_done = true;
      }
      compaction_cv.notify_all();
    }
    for (auto& reader : readers) reader.join();
    if (writer.joinable()) writer.join();
    if (reader_error) std::rethrow_exception(reader_error);
    if (writer_error) std::rethrow_exception(writer_error);
    if (with_compaction) {
      if (!compaction_metrics.compacted || !next_snapshot)
        throw std::runtime_error("natural four-shard trigger did not compact the vector base");
      arm.compaction_start = compaction_metrics.compaction_start_time;
      arm.compaction_end = compaction_metrics.compaction_end_time;
      arm.compaction_wall_ms = compaction_metrics.compaction_wall_ms;
      arm.compaction_cpu_ms = compaction_metrics.compaction_cpu_ms;
      PublishDeltaGeneration(arm_root, next_generation, next_generation);
    }
    sample_psi(&arm, "condition-end");
    for (const auto& sample : arm.queries) {
      if (sample.read.generation != arm.pinned_generation || sample.hit_signature.empty())
        throw std::runtime_error("crossover query did not return the pinned generation");
    }
    next_snapshot.reset();
    current.reset();
    return arm;
  };

  const std::string process_cpu_psi_before = ReadProcText("/proc/pressure/cpu");
  const std::string process_io_psi_before = ReadProcText("/proc/pressure/io");
  const std::string process_memory_psi_before = ReadProcText("/proc/pressure/memory");
  std::vector<ArmResult> arms;
  arms.reserve(search_profile ? 1 : 4);
  if (search_profile) {
    arms.push_back(run_arm(1, 1, false));
  } else {
    arms.push_back(run_arm(1, 1, true));
    arms.push_back(run_arm(1, 2, false));
    arms.push_back(run_arm(2, 1, false));
    arms.push_back(run_arm(2, 2, true));
  }
  const std::string process_cpu_psi_after = ReadProcText("/proc/pressure/cpu");
  const std::string process_io_psi_after = ReadProcText("/proc/pressure/io");
  const std::string process_memory_psi_after = ReadProcText("/proc/pressure/memory");

  std::map<std::string, std::string> paired_signatures;
  for (const auto& arm : arms) {
    for (const auto& sample : arm.queries) {
      const std::string key = std::to_string(arm.pair) + ":" +
          std::to_string(sample.query_id) + ":" + std::to_string(sample.repetition);
      auto found = paired_signatures.find(key);
      if (found == paired_signatures.end()) paired_signatures.emplace(key, sample.hit_signature);
      else Check(found->second == sample.hit_signature,
                 "paired crossover query returns identical hit order for " + key);
    }
  }
  std::cout << std::setprecision(10);
  std::cout << "{\"kind\":\"meta\",\"experiment\":\""
            << (search_profile ? "reader-search-" + search_diagnostics_mode
                               : "reader-compaction-crossover") << "\","
            << "\"m4_diagnostics_mode\":\"" << search_diagnostics_mode << "\","
            << "\"docs\":" << kDocuments << ",\"vector_dimension\":" << kDimension
            << ",\"vector_metric\":\"COSINE\""
            << ",\"vector_normalized\":" << (kDimension > 4 ? "true" : "false")
            << ",\"readers\":" << kReaders
            << ",\"query_ids\":" << kQueryIds << ",\"repeats_per_id\":" << kRepeats
            << ",\"topk_values\":" << (search_profile ? "[8,64]" : "[32]")
            << ",\"filter_applied_to\":\"native_vector_only; FTS remains unfiltered except generation pin\""
            << ",\"filter_candidate_cache\":"
            << (std::getenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE") ? "true" : "false")
            << ",\"pairing\":\"pair,query_id,repetition\","
            << "\"conditions\":\""
            << (search_profile ? ("one quiescent fixed-snapshot control arm; M4 diagnostics " +
                                       search_diagnostics_mode)
                               : "same pinned four-shard snapshot; active natural vector compaction vs quiescent control")
            << "\",\"order\":"
            << (search_profile ? "[\"control\"]" : "[\"compaction\",\"control\",\"control\",\"compaction\"]") << ','
            << "\"compaction_policy_changed\":false,\"native_search_source_changed\":false}\n";
  std::cout << "{\"kind\":\"process_psi\",\"phase\":\"before\",\"cpu\":\""
            << JsonEscape(process_cpu_psi_before) << "\",\"io\":\""
            << JsonEscape(process_io_psi_before) << "\",\"memory\":\""
            << JsonEscape(process_memory_psi_before) << "\"}\n";
  for (const auto& arm : arms) {
    std::cout << "{\"kind\":\"arm\",\"pair\":" << arm.pair
              << ",\"order\":" << arm.order << ",\"condition\":\""
              << (arm.compaction ? "compaction" : "control")
              << "\",\"pinned_generation\":" << arm.pinned_generation
              << ",\"pinned_shards\":" << arm.pinned_shards
              << ",\"compaction_start_ms\":"
              << (arm.compaction ? offset_ms(arm.compaction_start) : -1.0)
              << ",\"compaction_end_ms\":"
              << (arm.compaction ? offset_ms(arm.compaction_end) : -1.0)
              << ",\"compaction_wall_ms\":" << arm.compaction_wall_ms
              << ",\"compaction_cpu_ms\":" << arm.compaction_cpu_ms << "}\n";
    for (const auto& sample : arm.queries) {
      const auto& stage = sample.read.stages;
      std::cout << "{\"kind\":\"query\",\"pair\":" << arm.pair
                << ",\"order\":" << arm.order << ",\"condition\":\""
                << (arm.compaction ? "compaction" : "control")
                << "\",\"wave\":" << sample.wave << ",\"reader\":" << sample.reader
                << ",\"query_id\":" << sample.query_id << ",\"repetition\":" << sample.repetition
                << ",\"topk\":" << sample.topk
                << ",\"filter_label\":\"" << JsonEscape(sample.filter_label) << "\""
                << ",\"vector_filter\":\"" << JsonEscape(sample.vector_filter) << "\""
                << ",\"expected_filter_matches\":" << sample.expected_filter_matches
                << ",\"reader_tid\":" << sample.reader_tid
                << ",\"generation\":" << sample.read.generation
                << ",\"query_start_ms\":" << offset_ms(sample.start)
                << ",\"query_end_ms\":" << offset_ms(sample.end)
                << ",\"query_start_monotonic_ns\":"
                << std::chrono::duration_cast<std::chrono::nanoseconds>(
                       sample.start.time_since_epoch()).count()
                << ",\"query_end_monotonic_ns\":"
                << std::chrono::duration_cast<std::chrono::nanoseconds>(
                       sample.end.time_since_epoch()).count()
                << ",\"query_wall_ms\":"
                << std::chrono::duration<double, std::milli>(sample.end - sample.start).count()
                << ",\"query_cpu_ms\":" << sample.cpu_ms
                << ",\"vector_stage_start_ms\":" << offset_ms(stage.vector_start)
                << ",\"vector_stage_end_ms\":" << offset_ms(stage.vector_end)
                << ",\"vector_stage_wall_ms\":"
                << std::chrono::duration<double, std::milli>(stage.vector_end - stage.vector_start).count()
                << ",\"vector_stage_cpu_ms\":" << stage.vector_stage_cpu_ms
                << ",\"vector_stage_scheduler_delay_ms\":" << stage.vector_stage_runqueue_wait_ms
                << ",\"native_api_wall_ms\":" << stage.native_vector_search_ms
                << ",\"native_api_cpu_ms\":" << stage.native_vector_search_cpu_ms
                << ",\"m4_start_ms\":" << offset_ms(stage.m4_start)
                << ",\"m4_end_ms\":" << offset_ms(stage.m4_end)
                << ",\"m4_wall_ms\":"
                << std::chrono::duration<double, std::milli>(stage.m4_end - stage.m4_start).count()
                << ",\"m4_cpu_ms\":" << stage.m4_search_cpu_ms
                << ",\"m4_scheduler_delay_ms\":" << stage.m4_stage_runqueue_wait_ms
                << ",\"merge_start_ms\":" << offset_ms(stage.merge_start)
                << ",\"merge_end_ms\":" << offset_ms(stage.merge_end)
                << ",\"merge_wall_ms\":"
                << std::chrono::duration<double, std::milli>(stage.merge_end - stage.merge_start).count()
                << ",\"merge_cpu_ms\":" << stage.merge_cpu_ms
                << ",\"merge_scheduler_delay_ms\":" << stage.merge_runqueue_wait_ms;
      if (stage.m4_query_diagnostics_captured) {
        const auto& d = stage.m4_query_diagnostics;
        std::cout << ",\"m4_query_diagnostics\":{\"collect_counters\":"
                  << (d.collect_counters ? "true" : "false")
                  << ",\"capture_timing\":" << (d.capture_timing ? "true" : "false")
                  << ",\"mutex_wait_ms\":" << d.mutex_wait_ms
                  << ",\"mutex_wait_cpu_ms\":" << d.mutex_wait_cpu_ms
                  << ",\"filter_ms\":" << d.filter_ms
                  << ",\"filter_cpu_ms\":" << d.filter_cpu_ms
                  << ",\"query_parse_ms\":" << d.query_parse_ms
                  << ",\"query_parse_cpu_ms\":" << d.query_parse_cpu_ms
                  << ",\"setup_ms\":" << d.setup_ms
                  << ",\"setup_cpu_ms\":" << d.setup_cpu_ms
                  << ",\"execution_ms\":" << d.execution_ms
                  << ",\"execution_cpu_ms\":" << d.execution_cpu_ms
                  << ",\"finalize_ms\":" << d.finalize_ms
                  << ",\"finalize_cpu_ms\":" << d.finalize_cpu_ms
                  << ",\"runs_read\":" << d.runs_read
                  << ",\"run_term_parts\":" << d.run_term_parts
                  << ",\"postings_scanned\":" << d.postings_scanned
                  << ",\"cursor_next_calls\":" << d.cursor_next_calls
                  << ",\"cursor_advance_calls\":" << d.cursor_advance_calls
                  << ",\"score_evaluations\":" << d.score_evaluations
                  << ",\"upper_bound_evaluations\":" << d.upper_bound_evaluations
                  << ",\"wand_pivots\":" << d.wand_pivots
                  << ",\"topk_insertions\":" << d.topk_insertions
                  << ",\"topk_replacements\":" << d.topk_replacements
                  << ",\"topk_rejections\":" << d.topk_rejections
                  << ",\"topk_pushes\":" << d.topk_pushes
                  << ",\"topk_pops\":" << d.topk_pops
                  << ",\"topk_finalize_pops\":" << d.topk_finalize_pops
                  << ",\"sorted_hits\":" << d.sorted_hits
                  << ",\"dead_postings_filtered\":" << d.dead_postings_filtered
                  << ",\"liveness_lookups\":" << d.liveness_lookups
                  << ",\"candidates\":" << d.candidates
                  << ",\"filter_checks\":" << d.filter_checks
                  << ",\"filter_rejections\":" << d.filter_rejections
                  << ",\"filter_candidates\":" << d.filter_candidates
                  << ",\"filter_cache_hit\":"
                  << (d.filter_cache_hit ? "true" : "false")
                  << ",\"examined\":" << d.examined << '}';
      }
      std::cout << ",\"native_calls\":[";
      for (size_t i = 0; i < stage.native_vector_calls.size(); ++i) {
        const auto& call = stage.native_vector_calls[i];
        if (i) std::cout << ',';
        std::cout << "{\"start_ms\":" << offset_ms(call.start)
                  << ",\"end_ms\":" << offset_ms(call.end)
                  << ",\"caller_tid\":" << call.caller_tid
                  << ",\"wall_ms\":"
                  << std::chrono::duration<double, std::milli>(call.end - call.start).count()
                  << ",\"cpu_ms\":" << call.cpu_ms << '}';
      }
      std::cout << "],\"hit_signature\":\"" << JsonEscape(sample.hit_signature) << "\"}\n";
    }
    for (const auto& psi : arm.psi) {
      std::cout << "{\"kind\":\"psi\",\"pair\":" << arm.pair
                << ",\"condition\":\"" << (arm.compaction ? "compaction" : "control")
                << "\",\"label\":\"" << JsonEscape(psi.label)
                << "\",\"offset_ms\":" << psi.offset_ms
                << ",\"cpu\":\"" << JsonEscape(psi.cpu)
                << "\",\"io\":\"" << JsonEscape(psi.io)
                << "\",\"memory\":\"" << JsonEscape(psi.memory) << "\"}\n";
    }
  }
  std::cout << "{\"kind\":\"process_psi\",\"phase\":\"after\",\"cpu\":\""
            << JsonEscape(process_cpu_psi_after) << "\",\"io\":\""
            << JsonEscape(process_io_psi_after) << "\",\"memory\":\""
            << JsonEscape(process_memory_psi_after) << "\"}\n";
  std::cerr << "M4_READER_COMPACTION_CROSSOVER checks=" << checks.load()
            << " failures=" << failures.load() << '\n';
  Need(zvec_shutdown(), "shutdown reader-compaction crossover");
  return failures.load() ? 1 : 0;
}

int RunIncrementalM4DeltaCandidate(const fs::path& root, const char* dictionary) {
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize incremental M4 candidate");
  const auto base_rows = MakeChurnWorkload(64);
  std::map<std::string, uint32_t> versions;
  for (const auto& row : base_rows) versions[row.pk] = 1;
  AppendGenerationWal(root, 'R', 1);
  DeltaBuildMetrics base_metrics;
  auto base = BuildDeltaVectorSnapshot(root, 1, base_rows, base_rows, {},
      std::move(versions), dictionary, &base_metrics, nullptr, nullptr, true);
  PublishDeltaGeneration(root, 1, 1);

  m4::Options options;
  options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  options.reuse_analyzer_cache = true;
  std::unique_ptr<m4::Index> oracle;
  auto oracle_open = m4::Index::Open((root / "oracle-fts").string(), options, &oracle);
  if (!oracle_open.ok) throw std::runtime_error("open persistent M4 oracle: " + oracle_open.message);
  std::vector<m4::Mutation> initial;
  for (const auto& row : base_rows)
    initial.emplace_back(m4::MutationKind::UPSERT, "1:" + row.pk, M4Doc(row, 1));
  for (size_t at = 0; at < initial.size(); at += 256) {
    const size_t end = std::min(at + 256, initial.size());
    std::vector<m4::Mutation> batch(initial.begin() + at, initial.begin() + end);
    const auto applied = oracle->ApplyBatch(batch);
    if (!applied.ok) throw std::runtime_error("seed persistent M4 oracle: " + applied.message);
  }
  if (!oracle->Flush().ok) throw std::runtime_error("flush persistent M4 oracle base");

  const std::vector<std::string> queries = {
      "request timeout", "updated round 0", "churn generated", "\"request timeout\""};
  auto search = [](m4::Index* index, const std::string& query,
                   const m4::FilterExpr* filter = nullptr) {
    m4::SearchResult result;
    const auto status = filter
        ? index->SearchFtsFiltered(query, 64, *filter, &result)
        : index->SearchFts(query, 64, &result);
    if (!status.ok) throw std::runtime_error("incremental M4 test search: " + status.message);
    return result.hits;
  };
  auto same_hits = [](const std::vector<m4::Hit>& a, const std::vector<m4::Hit>& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i) {
      if (a[i].doc_id != b[i].doc_id || a[i].pk != b[i].pk ||
          std::fabs(a[i].score - b[i].score) > 1e-6f) return false;
    }
    return true;
  };
  std::vector<std::vector<m4::Hit>> pinned_old;
  for (const auto& query : queries) pinned_old.push_back(search(base->fts.get(), query));

  std::vector<Row> next_rows;
  const auto mutations = ChurnBatch(base_rows, 0, &next_rows);
  std::vector<m4::Mutation> m4_delta;
  std::vector<Row> vector_delta;
  auto next_versions = base->live_versions;
  for (const auto& mutation : mutations) {
    if (mutation.kind == BatchMutation::Kind::DELETE) {
      next_versions.erase(mutation.pk);
      m4_delta.emplace_back(m4::MutationKind::DELETE, "1:" + mutation.pk);
    } else {
      if (!mutation.row) throw std::runtime_error("incremental candidate upsert has no row");
      next_versions[mutation.pk] = 2;
      vector_delta.push_back(*mutation.row);
      m4_delta.emplace_back(m4::MutationKind::UPSERT, "1:" + mutation.pk,
                            M4Doc(*mutation.row, 1));
    }
  }
  const fs::path base_fts = base->directory / "fts";
  DeltaBuildMetrics update_metrics;
  AppendGenerationWal(root, 'R', 2);
  auto next = BuildDeltaVectorSnapshot(root, 2, next_rows, vector_delta,
      base->shards, std::move(next_versions), dictionary, &update_metrics,
      &base_fts, &m4_delta, true);
  PublishDeltaGeneration(root, 2, 2);
  const auto oracle_delta = oracle->ApplyBatch(m4_delta);
  if (!oracle_delta.ok) throw std::runtime_error("update persistent M4 oracle: " + oracle_delta.message);
  if (!oracle->Flush().ok) throw std::runtime_error("flush persistent M4 oracle delta");
  Check(next->fts->GetStats().live_docs == next_rows.size(),
        "incremental M4 branch has exact post-update live doc count");
  Check(update_metrics.m4_rows_indexed == vector_delta.size() &&
        update_metrics.m4_mutations_applied == m4_delta.size(),
        "incremental M4 writes only changed upserts and delete/upsert mutations");
  Check(update_metrics.m4_open_diagnostics.analyzer_cache_hit,
        "incremental M4 branch reuses verified immutable analyzer state");
  Check(update_metrics.m4_shared_segment_files > 0 &&
        fs::equivalent(base_fts / "run-1.seg", next->directory / "fts" / "run-1.seg"),
        "incremental generation hard-links immutable base M4 run");
  for (const auto& query : queries) {
    const auto branch_hits = search(next->fts.get(), query);
    const auto oracle_hits = search(oracle.get(), query);
    Check(same_hits(branch_hits, oracle_hits),
          "incremental M4 branch preserves persistent-index scores/order for: " + query);
  }
  const auto filter = m4::FilterExpr::Uint32Eq("file_id", base_rows.front().file_id);
  Check(same_hits(search(next->fts.get(), "request timeout", &filter),
                  search(oracle.get(), "request timeout", &filter)),
        "incremental M4 branch preserves typed-filtered FTS results");
  for (size_t i = 0; i < queries.size(); ++i)
    Check(same_hits(search(base->fts.get(), queries[i]), pinned_old[i]),
          "old pinned M4 snapshot stays unchanged after branch update");
  next->bridge.reset(); next->vector_bridges.clear(); next->fts.reset();
  std::unique_ptr<m4::Index> reopened;
  const auto reopen_status = m4::Index::Open((next->directory / "fts").string(), options, &reopened);
  Check(reopen_status.ok && reopened->GetStats().live_docs == next_rows.size(),
        "incremental M4 branch reopens with current live state");
  if (reopen_status.ok) {
    for (const auto& query : queries)
      Check(same_hits(search(reopened.get(), query), search(oracle.get(), query)),
            "reopened incremental M4 branch matches persistent oracle: " + query);
  }
  reopened.reset(); next.reset(); base.reset();
  uint32_t recovered_highwater = 0;
  auto recovered = RecoverDeltaVectorSnapshot(root, dictionary, &recovered_highwater);
  Check(recovered->id == 2 && recovered_highwater == 2,
        "recovery opens published incremental M4/vector generation");
  for (const auto& query : queries)
    Check(same_hits(search(recovered->fts.get(), query), search(oracle.get(), query)),
          "recovered incremental M4 branch matches persistent oracle: " + query);
  recovered.reset(); oracle.reset();
  std::cout << "M4_INCREMENTAL_FTS_CANDIDATE checks=" << checks.load()
            << " failures=" << failures.load()
            << " changed_upserts=" << vector_delta.size()
            << " mutations=" << m4_delta.size()
            << " shared_runs=" << update_metrics.m4_shared_segment_files << '\n';
  Need(zvec_shutdown(), "shutdown incremental M4 candidate");
  return failures.load() ? 1 : 0;
}

int RunNativeHnswCounterFixture(const fs::path& root, size_t documents,
                                size_t query_count) {
  constexpr size_t kReaders = 4;
  constexpr size_t kTopK = 16;
  if (documents < 256 || query_count < kReaders || query_count % kReaders != 0 ||
      (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error(
        "native HNSW counter fixture requires a fresh root, >=256 docs and a query count divisible by four");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize native HNSW counter fixture");
  Native native = OpenVector(root / "vector", true);
  zvec_collection_schema_t* stored_schema = nullptr;
  Need(zvec_collection_get_schema(native.value, &stored_schema),
       "read native HNSW fixture schema");
  auto* stored_embedding = zvec_collection_schema_get_field(stored_schema,
                                                             "embedding");
  if (!stored_embedding ||
      zvec_field_schema_get_index_type(stored_embedding) != ZVEC_INDEX_TYPE_HNSW) {
    zvec_collection_schema_destroy(stored_schema);
    throw std::runtime_error("native HNSW fixture schema did not persist HNSW index type");
  }
  zvec_collection_schema_destroy(stored_schema);
  const auto rows = MakeChurnWorkload(documents);
  for (size_t at = 0; at < rows.size(); at += 256) {
    const size_t end = std::min(at + 256, rows.size());
    std::vector<zvec_doc_t*> docs;
    docs.reserve(end - at);
    for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(rows[i], 1));
    std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
    zvec_write_result_t* results = nullptr;
    size_t count = 0;
    const auto code = zvec_collection_upsert_with_results(
        native.value, refs.data(), refs.size(), &results, &count);
    bool ok = code == ZVEC_OK && count == refs.size();
    for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
    zvec_write_results_free(results, count);
    for (auto* doc : docs) zvec_doc_destroy(doc);
    if (!ok) throw std::runtime_error("native HNSW fixture upsert failed");
  }
  Need(zvec_collection_flush(native.value), "flush native HNSW counter fixture");
  Need(zvec_collection_optimize(native.value),
       "build optimized HNSW segment for counter fixture");
  if (NativeDocCount(native.value) != documents)
    throw std::runtime_error("native HNSW fixture cardinality mismatch");

  std::vector<zvec_vector_query_t*> queries(query_count + 1, nullptr);
  for (size_t logical_query_id = 0; logical_query_id <= query_count;
       ++logical_query_id) {
    const size_t row_index = (logical_query_id * 17 + 1) % rows.size();
    auto* query = zvec_vector_query_create();
    if (!query) throw std::runtime_error("create native HNSW query");
    queries[logical_query_id] = query;
    Need(zvec_vector_query_set_field_name(query, "embedding"),
         "set native HNSW field");
    Need(zvec_vector_query_set_query_vector(query, rows[row_index].vector.data(),
                                             sizeof(rows[row_index].vector)),
         "set native HNSW query vector");
    Need(zvec_vector_query_set_topk(query, static_cast<int>(kTopK)),
         "set native HNSW topk");
    Need(zvec_vector_query_set_include_doc_id(query, true),
         "include native HNSW doc id");
    const char* output_fields[] = {"logical_pk"};
    Need(zvec_vector_query_set_output_fields(query, output_fields, 1),
         "set native HNSW output fields");
  }
  const auto run_query = [&](size_t logical_query_id,
                             uint64_t* query_handle_out, uint64_t* tid_out,
                             double* wall_ms_out, double* cpu_ms_out) {
    auto* query = queries.at(logical_query_id);
    *query_handle_out = reinterpret_cast<uintptr_t>(query);
    zvec_doc_t** docs = nullptr;
    size_t count = 0;
    const uint64_t tid = CurrentThreadTid();
    const double cpu_start = CurrentThreadCpuMs();
    const auto wall_start = std::chrono::steady_clock::now();
    const auto code = zvec_collection_query(native.value, query, &docs, &count);
    const double wall_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - wall_start).count();
    const double cpu_ms = CurrentThreadCpuMs() - cpu_start;
    Need(code, "run native HNSW query");
    if (count != kTopK) {
      zvec_docs_free(docs, count);
      throw std::runtime_error("native HNSW query returned an unexpected hit count");
    }
    std::ostringstream signature;
    for (size_t i = 0; i < count; ++i) {
      if (i) signature << '|';
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      signature << (pk ? pk : "");
    }
    zvec_docs_free(docs, count);
    *tid_out = tid;
    *wall_ms_out = wall_ms;
    *cpu_ms_out = cpu_ms;
    return signature.str();
  };

  uint64_t warmup_query_handle = 0, warmup_tid = 0;
  double warmup_wall_ms = 0.0, warmup_cpu_ms = 0.0;
  const auto warmup_signature = run_query(query_count, &warmup_query_handle,
                                           &warmup_tid, &warmup_wall_ms,
                                           &warmup_cpu_ms);
  std::cout << std::setprecision(9)
            << "{\"kind\":\"native_hnsw_query\",\"phase\":\"warmup\","
            << "\"logical_query_id\":" << query_count
            << ",\"query_handle\":" << warmup_query_handle
            << ",\"reader\":-1,\"reader_tid\":" << warmup_tid
            << ",\"query_wall_ms\":" << warmup_wall_ms
            << ",\"query_cpu_ms\":" << warmup_cpu_ms
            << ",\"hit_signature\":\"" << JsonEscape(warmup_signature)
            << "\"}\n";

  std::mutex start_mutex, output_mutex, error_mutex;
  std::condition_variable start_cv;
  size_t ready = 0;
  bool start = false;
  std::exception_ptr reader_error;
  std::vector<std::thread> readers;
  readers.reserve(kReaders);
  const size_t queries_per_reader = query_count / kReaders;
  for (size_t reader = 0; reader < kReaders; ++reader) {
    readers.emplace_back([&, reader] {
      {
        std::unique_lock<std::mutex> lock(start_mutex);
        ++ready;
        start_cv.notify_all();
        start_cv.wait(lock, [&] { return start; });
      }
      try {
        for (size_t sequence = 0; sequence < queries_per_reader; ++sequence) {
          const size_t logical_query_id = reader * queries_per_reader + sequence;
          uint64_t query_handle = 0, tid = 0;
          double wall_ms = 0.0, cpu_ms = 0.0;
          const auto signature = run_query(logical_query_id, &query_handle,
                                            &tid, &wall_ms, &cpu_ms);
          std::lock_guard<std::mutex> lock(output_mutex);
          std::cout << std::setprecision(9)
                    << "{\"kind\":\"native_hnsw_query\",\"phase\":\"measure\","
                    << "\"logical_query_id\":" << logical_query_id
                    << ",\"query_handle\":" << query_handle
                    << ",\"reader\":" << reader
                    << ",\"reader_tid\":" << tid
                    << ",\"query_wall_ms\":" << wall_ms
                    << ",\"query_cpu_ms\":" << cpu_ms
                    << ",\"hit_signature\":\"" << JsonEscape(signature)
                    << "\"}\n";
        }
      } catch (...) {
        std::lock_guard<std::mutex> lock(error_mutex);
        if (!reader_error) reader_error = std::current_exception();
      }
    });
  }
  {
    std::unique_lock<std::mutex> lock(start_mutex);
    start_cv.wait(lock, [&] { return ready == kReaders; });
    start = true;
  }
  start_cv.notify_all();
  for (auto& reader : readers) reader.join();
  for (auto* query : queries) zvec_vector_query_destroy(query);
  if (reader_error) std::rethrow_exception(reader_error);
  std::cout << "{\"kind\":\"native_hnsw_summary\",\"docs\":" << documents
            << ",\"readers\":" << kReaders << ",\"queries\":" << query_count
            << ",\"topk\":" << kTopK << "}\n";
  if (!native.Close()) throw std::runtime_error("close native HNSW fixture");
  Need(zvec_shutdown(), "shutdown native HNSW counter fixture");
  return 0;
}

int RunNativeCounterCoverageFixture(const fs::path& root) {
  constexpr size_t kFlatDocs = 24;
  constexpr size_t kHnswDocs = 48;
  constexpr size_t kTopK = 4;
  constexpr size_t kReaders = 4;
  if (fs::exists(root) && !fs::is_empty(root))
    throw std::runtime_error("native counter coverage fixture requires a fresh root");
  fs::create_directories(root);
  if (::setenv("M4_HNSW_BF_THRESHOLD", "32", 1) != 0)
    throw std::runtime_error("set tiny-fixture HNSW route threshold");
  Need(zvec_initialize(nullptr), "initialize native counter coverage fixture");
  Native flat = OpenVector(root / "flat", true, ZVEC_INDEX_TYPE_FLAT);
  Native hnsw_small = OpenVector(root / "hnsw-small", true, ZVEC_INDEX_TYPE_HNSW);
  Native hnsw = OpenVector(root / "hnsw", true, ZVEC_INDEX_TYPE_HNSW);

  const auto verify_index_type = [](zvec_collection_t* collection,
                                    zvec_index_type_t expected,
                                    const char* label) {
    zvec_collection_schema_t* schema = nullptr;
    Need(zvec_collection_get_schema(collection, &schema),
         std::string("read ") + label + " fixture schema");
    auto* field = zvec_collection_schema_get_field(schema, "embedding");
    const bool matches = field && zvec_field_schema_get_index_type(field) == expected;
    zvec_collection_schema_destroy(schema);
    Check(matches, std::string(label) + " fixture persists expected index type");
  };
  verify_index_type(flat.value, ZVEC_INDEX_TYPE_FLAT, "Flat");
  verify_index_type(hnsw_small.value, ZVEC_INDEX_TYPE_HNSW, "small HNSW");
  verify_index_type(hnsw.value, ZVEC_INDEX_TYPE_HNSW, "HNSW");

  auto flat_rows = MakeChurnWorkload(kFlatDocs);
  auto hnsw_small_rows = MakeChurnWorkload(kFlatDocs);
  auto hnsw_rows = MakeChurnWorkload(kHnswDocs);
  for (size_t i = 0; i < flat_rows.size(); ++i) {
    flat_rows[i].pk = "flat-" + flat_rows[i].pk;
    flat_rows[i].file_id = static_cast<uint32_t>(i + 1);
  }
  for (size_t i = 0; i < hnsw_small_rows.size(); ++i) {
    hnsw_small_rows[i].pk = "hnsw-small-" + hnsw_small_rows[i].pk;
    hnsw_small_rows[i].file_id = static_cast<uint32_t>(200 + i);
  }
  for (size_t i = 0; i < hnsw_rows.size(); ++i) {
    hnsw_rows[i].pk = "hnsw-" + hnsw_rows[i].pk;
    hnsw_rows[i].file_id = static_cast<uint32_t>(100 + i);
  }
  const auto seed = [](zvec_collection_t* collection,
                       const std::vector<Row>& rows, const char* label) {
    std::vector<zvec_doc_t*> docs;
    docs.reserve(rows.size());
    for (const auto& row : rows) docs.push_back(VectorDoc(row, 1));
    std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
    zvec_write_result_t* results = nullptr;
    size_t count = 0;
    const auto code = zvec_collection_upsert_with_results(
        collection, refs.data(), refs.size(), &results, &count);
    bool ok = code == ZVEC_OK && count == refs.size();
    for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
    zvec_write_results_free(results, count);
    for (auto* doc : docs) zvec_doc_destroy(doc);
    Check(ok, std::string("seed tiny ") + label + " fixture");
    if (!ok) throw std::runtime_error(std::string("seed ") + label + " failed");
    Need(zvec_collection_flush(collection), std::string("flush tiny ") + label + " fixture");
    if (std::string(label) == "HNSW")
      Need(zvec_collection_optimize(collection), "optimize tiny HNSW fixture");
  };
  seed(flat.value, flat_rows, "Flat");
  seed(hnsw_small.value, hnsw_small_rows, "HNSW");
  seed(hnsw.value, hnsw_rows, "HNSW");
  Check(NativeDocCount(flat.value) == kFlatDocs, "tiny Flat fixture doc count");
  Check(NativeDocCount(hnsw_small.value) == kFlatDocs, "tiny small HNSW fixture doc count");
  Check(NativeDocCount(hnsw.value) == kHnswDocs, "tiny HNSW fixture doc count");

  std::vector<zvec_vector_query_t*> owned_queries;
  const auto make_query = [&](const Row& row, const char* filter) {
    auto* query = zvec_vector_query_create();
    if (!query) throw std::runtime_error("create counter coverage query");
    owned_queries.push_back(query);
    Need(zvec_vector_query_set_field_name(query, "embedding"),
         "set coverage query field");
    Need(zvec_vector_query_set_query_vector(query, row.vector.data(),
                                             sizeof(row.vector)),
         "set coverage query vector");
    Need(zvec_vector_query_set_topk(query, static_cast<int>(kTopK)),
         "set coverage topk");
    if (filter && *filter)
      Need(zvec_vector_query_set_filter(query, filter), "set coverage filter");
    return query;
  };
  std::mutex output_mutex;
  const auto run_query = [&](zvec_collection_t* collection,
                             zvec_vector_query_t* query,
                             const std::string& case_name,
                             const std::string& shard,
                             const std::string& logical_id, int reader,
                             size_t expected_count) {
    zvec_doc_t** docs = nullptr;
    size_t count = 0;
    const uint64_t tid = CurrentThreadTid();
    Need(zvec_collection_query(collection, query, &docs, &count),
         "run counter coverage query");
    std::vector<std::string> hits;
    hits.reserve(count);
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      hits.emplace_back(pk ? pk : "");
    }
    zvec_docs_free(docs, count);
    Check(count == expected_count, case_name + "/" + shard + " result count");
    std::ostringstream line;
    line << "{\"kind\":\"native_counter_coverage_query\",\"case\":\""
         << JsonEscape(case_name) << "\",\"shard\":\"" << JsonEscape(shard)
         << "\",\"logical_id\":\"" << JsonEscape(logical_id)
         << "\",\"query_id\":\"" << reinterpret_cast<uintptr_t>(query)
         << "\",\"reader\":" << reader << ",\"reader_tid\":" << tid
         << ",\"result_count\":" << count << ",\"expected_count\":"
         << expected_count << ",\"hits\":[";
    for (size_t i = 0; i < hits.size(); ++i) {
      if (i) line << ',';
      line << "\"" << JsonEscape(hits[i]) << "\"";
    }
    line << "]}";
    {
      std::lock_guard<std::mutex> lock(output_mutex);
      std::cout << line.str() << '\n';
    }
    return hits;
  };

  auto* bf_query = make_query(flat_rows.front(), "");
  run_query(flat.value, bf_query, "bf_only", "flat", "bf-only", -1, kTopK);
  auto* hnsw_query = make_query(hnsw_rows.front(), "");
  run_query(hnsw.value, hnsw_query, "hnsw_only", "hnsw", "hnsw-only", -1,
            kTopK);

  auto* flat_partial_filter = make_query(flat_rows.front(), "file_id < 4");
  run_query(flat.value, flat_partial_filter, "filter_partial_rejection", "flat",
            "filter-flat-partial", -1, 3);
  auto* hnsw_partial_filter = make_query(hnsw_rows.front(), "file_id < 104");
  run_query(hnsw.value, hnsw_partial_filter, "filter_partial_rejection", "hnsw",
            "filter-hnsw-partial", -1, 4);
  constexpr const char* kRejectAllFilter = "file_id = 4294967295";
  auto* flat_filter_query = make_query(flat_rows.front(), kRejectAllFilter);
  run_query(flat.value, flat_filter_query, "filter_rejection", "flat",
            "filter-flat", -1, 0);
  auto* hnsw_filter_query = make_query(hnsw_rows.front(), kRejectAllFilter);
  run_query(hnsw.value, hnsw_filter_query, "filter_rejection", "hnsw",
            "filter-hnsw", -1, 0);

  auto* mixed_query = make_query(flat_rows.front(), "");
  const auto mixed_flat = run_query(flat.value, mixed_query, "mixed_shards",
                                    "flat", "mixed-base", -1, kTopK);
  const auto mixed_small = run_query(hnsw_small.value, mixed_query, "mixed_shards",
                                     "hnsw-small-bf", "mixed-base", -1, kTopK);
  const auto mixed_hnsw = run_query(hnsw.value, mixed_query, "mixed_shards",
                                    "hnsw", "mixed-base", -1, kTopK);
  Check(!mixed_flat.empty() && !mixed_small.empty() && !mixed_hnsw.empty(),
        "mixed-shard query returns hits from Flat, small-HNSW BF, and HNSW routes");

  struct ReaderQueries {
    zvec_vector_query_t* normal{nullptr};
    zvec_vector_query_t* reject{nullptr};
  };
  std::array<ReaderQueries, kReaders> reader_queries{};
  for (size_t reader = 0; reader < kReaders; ++reader) {
    reader_queries[reader].normal = make_query(flat_rows[reader], "");
    reader_queries[reader].reject =
        make_query(flat_rows[reader], kRejectAllFilter);
  }
  std::mutex start_mutex;
  std::condition_variable start_cv;
  size_t ready = 0;
  bool start = false;
  std::mutex error_mutex;
  std::exception_ptr reader_error;
  std::vector<std::thread> readers;
  for (size_t reader = 0; reader < kReaders; ++reader) {
    readers.emplace_back([&, reader] {
      {
        std::unique_lock<std::mutex> lock(start_mutex);
        ++ready;
        start_cv.notify_all();
        start_cv.wait(lock, [&] { return start; });
      }
      try {
        const auto normal_id = "reader-" + std::to_string(reader) + "-normal";
        const auto reject_id = "reader-" + std::to_string(reader) + "-reject";
        run_query(flat.value, reader_queries[reader].normal,
                  "multi_reader_normal", "flat", normal_id,
                  static_cast<int>(reader), kTopK);
        run_query(hnsw.value, reader_queries[reader].normal,
                  "multi_reader_normal", "hnsw", normal_id,
                  static_cast<int>(reader), kTopK);
        run_query(flat.value, reader_queries[reader].reject,
                  "multi_reader_filter_rejection", "flat", reject_id,
                  static_cast<int>(reader), 0);
        run_query(hnsw.value, reader_queries[reader].reject,
                  "multi_reader_filter_rejection", "hnsw", reject_id,
                  static_cast<int>(reader), 0);
      } catch (...) {
        std::lock_guard<std::mutex> lock(error_mutex);
        if (!reader_error) reader_error = std::current_exception();
      }
    });
  }
  {
    std::unique_lock<std::mutex> lock(start_mutex);
    start_cv.wait(lock, [&] { return ready == kReaders; });
    start = true;
  }
  start_cv.notify_all();
  for (auto& reader : readers) reader.join();
  for (auto* query : owned_queries) zvec_vector_query_destroy(query);
  if (reader_error) std::rethrow_exception(reader_error);
  if (!flat.Close() || !hnsw_small.Close() || !hnsw.Close())
    throw std::runtime_error("close native counter coverage fixture");
  Need(zvec_shutdown(), "shutdown native counter coverage fixture");
  (void)::unsetenv("M4_HNSW_BF_THRESHOLD");
  std::cout << "{\"kind\":\"native_counter_coverage_summary\",\"flat_docs\":"
            << kFlatDocs << ",\"hnsw_small_docs\":" << kFlatDocs
            << ",\"hnsw_docs\":" << kHnswDocs
            << ",\"readers\":" << kReaders << ",\"topk\":" << kTopK
            << "}\n";
  return failures.load() ? 1 : 0;
}

int RunNativeRouteCostModelFixture(const fs::path& root, size_t documents,
                                   size_t query_count,
                                   bool compare_counter_modes = false) {
  constexpr size_t kTopK = 10;
  if (documents < 256 || query_count == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("native route cost-model fixture requires a fresh root, >=256 docs and >=1 query");
  fs::create_directories(root);
  if (::setenv("M4_HNSW_BF_THRESHOLD", "0", 1) != 0)
    throw std::runtime_error("force HNSW route for cost-model fixture");
  Need(zvec_initialize(nullptr), "initialize native route cost-model fixture");
  if (compare_counter_modes) {
    if (::setenv("M4_NATIVE_SEARCH_COUNTERS_MODE", "off", 1) != 0 ||
        ::setenv("M4_NATIVE_SEARCH_COUNTERS_SILENT", "1", 1) != 0 ||
        ::unsetenv("M4_NATIVE_SEARCH_COUNTERS") != 0 ||
        ::unsetenv("M4_ROUTE_TRACE_FILE") != 0)
      throw std::runtime_error("configure same-index counter-mode parity fixture");
  }
  Native flat = OpenVector(root / "flat", true, ZVEC_INDEX_TYPE_FLAT);
  Native hnsw = OpenVector(root / "hnsw", true, ZVEC_INDEX_TYPE_HNSW);
  const auto rows = MakeChurnWorkload(documents);
  const auto seed = [&](zvec_collection_t* collection, const char* label) {
    for (size_t at = 0; at < rows.size(); at += 256) {
      const size_t end = std::min(at + 256, rows.size());
      std::vector<zvec_doc_t*> docs;
      docs.reserve(end - at);
      for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(rows[i], 1));
      std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
      zvec_write_result_t* results = nullptr;
      size_t count = 0;
      const auto code = zvec_collection_upsert_with_results(
          collection, refs.data(), refs.size(), &results, &count);
      bool ok = code == ZVEC_OK && count == refs.size();
      for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
      zvec_write_results_free(results, count);
      for (auto* doc : docs) zvec_doc_destroy(doc);
      Check(ok, std::string("seed cost-model ") + label);
      if (!ok) throw std::runtime_error(std::string("seed cost-model ") + label + " failed");
    }
    Need(zvec_collection_flush(collection), std::string("flush cost-model ") + label);
  };
  seed(flat.value, "Flat");
  seed(hnsw.value, "HNSW");
  Need(zvec_collection_optimize(hnsw.value), "optimize cost-model HNSW");
  Check(NativeDocCount(flat.value) == documents, "cost-model Flat cardinality");
  Check(NativeDocCount(hnsw.value) == documents, "cost-model HNSW cardinality");

  const auto make_query = [&](size_t query_id) {
    const size_t row_index = (query_id * 131 + 7) % rows.size();
    auto* query = zvec_vector_query_create();
    if (!query) throw std::runtime_error("create native cost-model query");
    Need(zvec_vector_query_set_field_name(query, "embedding"), "set cost-model field");
    Need(zvec_vector_query_set_query_vector(query, rows[row_index].vector.data(),
                                             sizeof(rows[row_index].vector)),
         "set cost-model query vector");
    Need(zvec_vector_query_set_topk(query, static_cast<int>(kTopK)),
         "set cost-model topk");
    return query;
  };
  struct OwnedQuery {
    zvec_vector_query_t* flat{nullptr};
    zvec_vector_query_t* hnsw{nullptr};
  };
  std::vector<OwnedQuery> queries;
  queries.reserve(query_count);
  for (size_t i = 0; i < query_count; ++i)
    queries.push_back({make_query(i), make_query(i)});
  const auto execute = [&](zvec_collection_t* collection,
                           zvec_vector_query_t* query, const char* backend,
                           const char* phase, size_t query_id) {
    zvec_doc_t** docs = nullptr;
    size_t count = 0;
    const auto start = std::chrono::steady_clock::now();
    Need(zvec_collection_query(collection, query, &docs, &count),
         "execute native cost-model query");
    const double wall_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    std::vector<std::string> hits;
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      hits.emplace_back(pk ? pk : "");
    }
    zvec_docs_free(docs, count);
    Check(count == std::min(kTopK, documents),
          std::string("cost-model result count ") + backend);
    std::cout << std::setprecision(9) << "{\"kind\":\"native_route_cost_query\",\"backend\":\""
              << backend << "\",\"phase\":\"" << phase << "\",\"query_id\":"
              << query_id << ",\"query_handle\":\"" << reinterpret_cast<uintptr_t>(query)
              << "\",\"wall_ms\":" << wall_ms << ",\"result_count\":" << count
              << ",\"hits\":[";
    for (size_t i = 0; i < hits.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << "\"" << JsonEscape(hits[i]) << "\"";
    }
    std::cout << "]}\n";
    return hits;
  };
  execute(flat.value, queries.front().flat, "flat", "warmup", 0);
  execute(hnsw.value, queries.front().hnsw, "hnsw", "warmup", 0);
  size_t recall_hits = 0;
  size_t counter_mode_matches = 0;
  for (size_t i = 0; i < query_count; ++i) {
    const auto exact = execute(flat.value, queries[i].flat, "flat", "measure", i);
    std::vector<std::string> approximate;
    if (!compare_counter_modes) {
      approximate = execute(hnsw.value, queries[i].hnsw, "hnsw", "measure", i);
    } else {
      if (::setenv("M4_NATIVE_SEARCH_COUNTERS_MODE", "off", 1) != 0)
        throw std::runtime_error("disable counters for same-index baseline");
      auto* off_query = make_query(i);
      const auto off_hits = execute(hnsw.value, off_query, "hnsw", "counter_off", i);
      zvec_vector_query_destroy(off_query);

      if (::setenv("M4_NATIVE_SEARCH_COUNTERS_MODE", "ops", 1) != 0)
        throw std::runtime_error("enable counters for same-index comparison");
      auto* on_query = make_query(i);
      const auto on_hits = execute(hnsw.value, on_query, "hnsw", "counter_on", i);
      zvec_vector_query_destroy(on_query);

      if (::setenv("M4_NATIVE_SEARCH_COUNTERS_MODE", "off", 1) != 0)
        throw std::runtime_error("disable counters for same-index repeat");
      auto* repeat_query = make_query(i);
      const auto repeat_hits = execute(hnsw.value, repeat_query, "hnsw", "counter_off_repeat", i);
      zvec_vector_query_destroy(repeat_query);
      const bool same = off_hits == on_hits && off_hits == repeat_hits;
      if (same) ++counter_mode_matches;
      Check(same, "same-index HNSW result parity across counter mode");
      approximate = off_hits;
    }
    std::set<std::string> exact_hits(exact.begin(), exact.end());
    for (const auto& hit : approximate) recall_hits += exact_hits.count(hit);
  }
  for (const auto& query : queries) {
    zvec_vector_query_destroy(query.flat);
    zvec_vector_query_destroy(query.hnsw);
  }
  if (!flat.Close() || !hnsw.Close())
    throw std::runtime_error("close native route cost-model fixture");
  Need(zvec_shutdown(), "shutdown native route cost-model fixture");
  (void)::unsetenv("M4_HNSW_BF_THRESHOLD");
  const size_t possible = query_count * std::min(kTopK, documents);
  std::cout << "{\"kind\":\"native_route_cost_summary\",\"documents\":"
            << documents << ",\"queries\":" << query_count << ",\"topk\":"
            << std::min(kTopK, documents) << ",\"flat_hnsw_recall_hits\":"
            << recall_hits << ",\"possible_hits\":" << possible
            << ",\"counter_mode_matches\":" << counter_mode_matches
            << ",\"counter_mode_comparisons\":"
            << (compare_counter_modes ? query_count : 0) << "}\n";
  return failures.load() ? 1 : 0;
}

int RunNativeHybridRouteCostModelFixture(const fs::path& root,
                                         const char* dictionary,
                                         size_t documents, size_t query_count,
                                         bool collect_native_counters = false,
                                         size_t hnsw_bf_threshold = 0,
                                         bool collect_m4_diagnostics = false,
                                         bool cache_filter_candidates = false) {
  if (documents < 64 || query_count == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("hybrid route cost fixture requires a fresh root, >=64 docs and >=1 query");
  fs::create_directories(root);
  const std::string threshold_text = std::to_string(hnsw_bf_threshold);
  if (::setenv("M4_NATIVE_SEARCH_COUNTERS_MODE", collect_native_counters ? "ops" : "off", 1) != 0 ||
      ::setenv("M4_NATIVE_SEARCH_COUNTERS_SILENT", "1", 1) != 0 ||
      ::unsetenv("M4_NATIVE_SEARCH_COUNTERS") != 0 ||
      (!collect_native_counters && ::unsetenv("M4_ROUTE_TRACE_FILE") != 0) ||
      (cache_filter_candidates
           ? ::setenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE", "1", 1)
           : ::unsetenv("M4_BRIDGE_FILTER_CANDIDATE_CACHE")) != 0 ||
      ::setenv("M4_HNSW_BF_THRESHOLD", threshold_text.c_str(), 1) != 0)
    throw std::runtime_error("configure diagnostics-off hybrid route fixture");
  Need(zvec_initialize(nullptr), "initialize hybrid route cost fixture");
  const auto rows = MakeChurnWorkload(documents);
  constexpr size_t kTopK = 10;
  struct Arm {
    std::string name;
    Native vector;
    std::unique_ptr<m4::Index> fts;
    std::unique_ptr<Coordinator> coordinator;
  };
  std::array<std::unique_ptr<Arm>, 2> arms;
  {
    const std::array<std::pair<const char*, zvec_index_type_t>, 2> configs{{
        {"flat", ZVEC_INDEX_TYPE_FLAT}, {"hnsw", ZVEC_INDEX_TYPE_HNSW}}};
    for (size_t i = 0; i < configs.size(); ++i) {
      std::string arm_name = configs[i].first;
      if (configs[i].second == ZVEC_INDEX_TYPE_HNSW && hnsw_bf_threshold != 0)
        arm_name = "hnsw_bf_" + threshold_text;
      const fs::path arm_root = root / arm_name;
      fs::create_directories(arm_root);
      AtomicManifest(arm_root, 0);
      auto arm = std::make_unique<Arm>();
      arm->name = arm_name;
      arm->vector = OpenVector(arm_root / "vector", true, configs[i].second);
      m4::Options options;
      options.fts_field = "text";
      options.fts_tokenizer_name = "jieba";
      options.jieba_dict_dir = dictionary;
      options.fsync_wal = true;
      options.cache_filter_candidates = cache_filter_candidates;
      const auto opened = m4::Index::Open((arm_root / "fts").string(), options, &arm->fts);
      if (!opened.ok) throw std::runtime_error("open hybrid route FTS: " + opened.message);
      arm->coordinator = std::make_unique<Coordinator>(arm_root, arm->vector.value, arm->fts.get());
      if (!arm->coordinator->Commit(rows, 0))
        throw std::runtime_error(std::string("commit hybrid route fixture ") + arm->name);
      if (configs[i].second == ZVEC_INDEX_TYPE_HNSW)
        Need(zvec_collection_optimize(arm->vector.value), "optimize hybrid HNSW route fixture");
      if (NativeDocCount(arm->vector.value) != documents || arm->fts->GetStats().live_docs != documents)
        throw std::runtime_error(std::string("hybrid route fixture cardinality mismatch: ") + arm->name);
      arms[i] = std::move(arm);
    }

    const size_t topk = std::min(kTopK, documents);
    const std::string text_query = "request timeout";
    const auto set_arm_build_id = [](const Arm& arm) {
      const std::string build_id = "whole-hybrid-" + arm.name;
      if (::setenv("M4_ROUTE_BUILD_ID", build_id.c_str(), 1) != 0)
        throw std::runtime_error("set route build id for hybrid arm");
    };
    for (const auto& arm : arms) {
      set_arm_build_id(*arm);
      const auto warm = arm->coordinator->Hybrid(arm->coordinator->SearchSnapshot(),
                                                  rows.front().vector, text_query, topk);
      Check(warm.size() == topk, std::string("hybrid route warmup ") + arm->name);
    }
    size_t exact_hybrid_matches = 0;
    size_t overlap_hits = 0;
    for (size_t qid = 0; qid < query_count; ++qid) {
      const size_t row_index = (qid * 131 + 7) % rows.size();
      struct Sample {
        double wall_ms{0};
        HybridStageTimes stages;
        m4::QueryDiagnostics m4_diagnostics;
        std::vector<Hit> hits;
      };
      std::array<Sample, 2> samples;
      const std::array<size_t, 2> order = qid % 2 == 0
          ? std::array<size_t, 2>{0, 1} : std::array<size_t, 2>{1, 0};
      for (size_t arm_index : order) {
        set_arm_build_id(*arms[arm_index]);
        const auto start = std::chrono::steady_clock::now();
        samples[arm_index].hits = arms[arm_index]->coordinator->Hybrid(
            arms[arm_index]->coordinator->SearchSnapshot(), rows[row_index].vector,
            text_query, topk, {}, &samples[arm_index].stages,
            collect_m4_diagnostics ? &samples[arm_index].m4_diagnostics : nullptr);
        samples[arm_index].wall_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - start).count();
        Check(samples[arm_index].hits.size() == topk,
              std::string("hybrid route result count ") + arms[arm_index]->name);
      }
      std::set<std::string> flat_pks, hnsw_pks;
      for (const auto& hit : samples[0].hits) flat_pks.insert(hit.pk);
      for (const auto& hit : samples[1].hits) hnsw_pks.insert(hit.pk);
      size_t overlap = 0;
      for (const auto& pk : hnsw_pks) overlap += flat_pks.count(pk);
      overlap_hits += overlap;
      const bool exact_match = samples[0].hits.size() == samples[1].hits.size() &&
          std::equal(samples[0].hits.begin(), samples[0].hits.end(), samples[1].hits.begin(),
                     [](const Hit& a, const Hit& b) { return a.pk == b.pk; });
      exact_hybrid_matches += exact_match;
      for (size_t arm_index = 0; arm_index < arms.size(); ++arm_index) {
        const auto& sample = samples[arm_index];
        std::cout << std::setprecision(9)
                  << "{\"kind\":\"native_hybrid_route_query\",\"native_counters_on\":"
                  << (collect_native_counters ? "true" : "false")
                  << ",\"backend\":\"" << arms[arm_index]->name << "\",\"query_id\":" << qid
                  << ",\"row_index\":" << row_index << ",\"topk\":" << topk
                  << ",\"wall_ms\":" << sample.wall_ms
                  << ",\"vector_ms\":" << sample.stages.vector_ms
                  << ",\"fts_ms\":" << sample.stages.fts_ms
                  << ",\"merge_ms\":" << sample.stages.merge_ms
                  << ",\"m4_diagnostics_on\":" << (collect_m4_diagnostics ? "true" : "false")
                  << ",\"m4_diagnostics\":";
        if (collect_m4_diagnostics) {
          const auto& d = sample.m4_diagnostics;
          std::cout << "{\"runs_read\":" << d.runs_read
                    << ",\"run_term_parts\":" << d.run_term_parts
                    << ",\"postings_scanned\":" << d.postings_scanned
                    << ",\"cursor_next_calls\":" << d.cursor_next_calls
                    << ",\"cursor_advance_calls\":" << d.cursor_advance_calls
                    << ",\"dead_postings_filtered\":" << d.dead_postings_filtered
                    << ",\"liveness_lookups\":" << d.liveness_lookups
                    << ",\"filter_checks\":" << d.filter_checks
                    << ",\"filter_rejections\":" << d.filter_rejections
                    << ",\"candidates\":" << d.candidates
                    << ",\"score_evaluations\":" << d.score_evaluations
                    << ",\"upper_bound_evaluations\":" << d.upper_bound_evaluations
                    << ",\"wand_pivots\":" << d.wand_pivots
                    << ",\"topk_insertions\":" << d.topk_insertions
                    << ",\"topk_replacements\":" << d.topk_replacements
                    << ",\"topk_rejections\":" << d.topk_rejections
                    << ",\"sorted_hits\":" << d.sorted_hits
                    << ",\"filter_candidates\":" << d.filter_candidates
                    << ",\"filter_candidate_bytes\":" << d.filter_candidate_bytes
                    << ",\"filter_docid_temp_payload_upper_bound_bytes\":"
                    << d.filter_docid_temp_payload_upper_bound_bytes
                    << ",\"filter_cache_hit\":" << (d.filter_cache_hit ? "true" : "false")
                    << ",\"filter_like_strings_scanned\":" << d.filter_like_strings_scanned
                    << ",\"examined\":" << d.examined
                    << ",\"mutex_wait_ms\":" << d.mutex_wait_ms
                    << ",\"filter_ms\":" << d.filter_ms
                    << ",\"query_parse_ms\":" << d.query_parse_ms
                    << ",\"setup_ms\":" << d.setup_ms
                    << ",\"execution_ms\":" << d.execution_ms
                    << ",\"finalize_ms\":" << d.finalize_ms
                    << ",\"mutex_wait_cpu_ms\":" << d.mutex_wait_cpu_ms
                    << ",\"filter_cpu_ms\":" << d.filter_cpu_ms
                    << ",\"query_parse_cpu_ms\":" << d.query_parse_cpu_ms
                    << ",\"setup_cpu_ms\":" << d.setup_cpu_ms
                    << ",\"execution_cpu_ms\":" << d.execution_cpu_ms
                    << ",\"finalize_cpu_ms\":" << d.finalize_cpu_ms << "}";
        } else {
          std::cout << "null";
        }
        std::cout << ",\"hits\":[";
        for (size_t i = 0; i < sample.hits.size(); ++i) {
          if (i) std::cout << ',';
          std::cout << "\"" << JsonEscape(sample.hits[i].pk) << "\"";
        }
        std::cout << "]}\n";
      }
    }
    std::cout << "{\"kind\":\"native_hybrid_route_summary\",\"documents\":"
              << documents << ",\"queries\":" << query_count << ",\"topk\":"
              << std::min(kTopK, documents) << ",\"native_counters_on\":"
              << (collect_native_counters ? "true" : "false")
              << ",\"hnsw_bf_threshold\":" << hnsw_bf_threshold
              << ",\"m4_diagnostics_on\":" << (collect_m4_diagnostics ? "true" : "false")
              << ",\"filter_candidate_cache_on\":" << (cache_filter_candidates ? "true" : "false")
              << ",\"exact_hybrid_hit_order_matches\":"
              << exact_hybrid_matches << ",\"possible_matches\":" << query_count
              << ",\"overlap_hits\":" << overlap_hits << ",\"possible_overlap_hits\":"
              << query_count * std::min(kTopK, documents) << "}\n";
  }
  arms = {};
  Need(zvec_shutdown(), "shutdown hybrid route cost fixture");
  (void)::unsetenv("M4_HNSW_BF_THRESHOLD");
  return failures.load() ? 1 : 0;
}

int RunNativeVectorReference(const fs::path& root, const char* dictionary,
                             size_t documents, size_t rounds) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("native reference requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root); AtomicManifest(root, 0);
  Need(zvec_initialize(nullptr), "initialize native vector reference");
  Native native = OpenVector(root / "vector", true);
  m4::Options options; options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto opened = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!opened.ok) throw std::runtime_error(opened.message);
  Coordinator query_adapter(root, native.value, fts.get());  // vector-only queries; no FTS writes.
  auto rows = MakeChurnWorkload(documents);
  const ProcessMeasurement before = MeasureProcess();
  const auto base_start = std::chrono::steady_clock::now();
  for (size_t at = 0; at < rows.size(); at += 256) {
    const size_t end = std::min(at + 256, rows.size());
    std::vector<zvec_doc_t*> docs; docs.reserve(end - at);
    for (size_t i = at; i < end; ++i) docs.push_back(VectorDoc(rows[i], 1));
    std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
    zvec_write_result_t* results = nullptr; size_t count = 0;
    const auto code = zvec_collection_upsert_with_results(native.value, refs.data(), refs.size(), &results, &count);
    bool ok = code == ZVEC_OK && count == refs.size();
    for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
    zvec_write_results_free(results, count);
    for (auto* doc : docs) zvec_doc_destroy(doc);
    if (!ok) throw std::runtime_error("native baseline initial upsert failed");
  }
  Need(zvec_collection_flush(native.value), "flush native reference base");
  const double base_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - base_start).count();
  const size_t topk = std::min<size_t>(50, documents);
  const auto query = rows.front().vector;
  (void)query_adapter.SearchVector(1, query, "", topk);
  std::vector<double> commit_ms, query_ms;
  for (size_t round = 0; round < rounds; ++round) {
    std::vector<Row> next;
    const auto mutations = ChurnBatch(rows, round, &next);
    const auto start = std::chrono::steady_clock::now();
    std::vector<zvec_doc_t*> docs;
    std::vector<std::string> deleted;
    for (const auto& mutation : mutations) {
      if (mutation.kind == BatchMutation::Kind::DELETE) deleted.push_back(mutation.pk);
      else docs.push_back(VectorDoc(*mutation.row, 1));
    }
    std::vector<const zvec_doc_t*> refs(docs.begin(), docs.end());
    zvec_write_result_t* results = nullptr; size_t count = 0;
    const auto upsert_code = zvec_collection_upsert_with_results(native.value, refs.data(), refs.size(), &results, &count);
    bool ok = upsert_code == ZVEC_OK && count == refs.size();
    for (size_t i = 0; ok && i < count; ++i) ok = results[i].code == ZVEC_OK;
    zvec_write_results_free(results, count);
    for (auto* doc : docs) zvec_doc_destroy(doc);
    std::vector<const char*> delete_refs;
    std::vector<std::string> full_deleted;
    for (const auto& pk : deleted) full_deleted.push_back("1:" + pk);
    for (const auto& pk : full_deleted) delete_refs.push_back(pk.c_str());
    size_t success_count = 0, error_count = 0;
    if (!delete_refs.empty()) {
      const auto delete_code = zvec_collection_delete(native.value, delete_refs.data(), delete_refs.size(),
                                                      &success_count, &error_count);
      ok = ok && delete_code == ZVEC_OK && error_count == 0 && success_count == delete_refs.size();
    }
    if (!ok) throw std::runtime_error("native reference churn failed");
    Need(zvec_collection_flush(native.value), "flush native reference churn");
    commit_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count());
    const auto query_start = std::chrono::steady_clock::now();
    const auto hits = query_adapter.SearchVector(1, query, "", topk);
    query_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - query_start).count());
    if (hits.size() != topk || NativeDocCount(native.value) != documents)
      throw std::runtime_error("native reference query/cardinality mismatch");
    rows = std::move(next);
  }
  const ProcessMeasurement after = MeasureProcess();
  std::cout << std::setprecision(8) << "{\"arm\":\"native-vector-only\",\"docs\":" << documents
            << ",\"rounds\":" << rounds << ",\"reader_threads\":0,\"queries\":" << query_ms.size()
            << ",\"base_build_ms\":" << base_ms
            << ",\"commit_p50_ms\":" << PercentileMs(commit_ms, .50)
            << ",\"commit_p95_ms\":" << PercentileMs(commit_ms, .95)
            << ",\"commit_p99_ms\":" << PercentileMs(commit_ms, .99)
            << ",\"idle_vector_query_p50_ms\":" << PercentileMs(query_ms, .50)
            << ",\"idle_vector_query_p95_ms\":" << PercentileMs(query_ms, .95)
            << ",\"idle_vector_query_p99_ms\":" << PercentileMs(query_ms, .99)
            << ",\"physical_write_bytes\":" << after.write_bytes - before.write_bytes
            << ",\"process_cpu_ms\":" << after.cpu_ms - before.cpu_ms
            << ",\"peak_rss_kb\":" << after.peak_rss_kb
            << ",\"root_bytes\":" << DirectoryBytes(root);
  PrintArray("commit_samples_ms", commit_ms); PrintArray("query_samples_ms", query_ms);
  std::cout << "}\n";
  fts.reset(); (void)native.Close();
  Need(zvec_shutdown(), "shutdown native vector reference");
  return 0;
}
int RunLockPerformance(const fs::path& root, const char* dictionary) {
  fs::create_directories(root);
  AtomicManifest(root, 0);
  Need(zvec_initialize(nullptr), "initialize native for lock performance");
  Native native = OpenVector(root / "vector", true);
  m4::Options options; options.fts_field = "text"; options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary; options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto opened = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!opened.ok) throw std::runtime_error(opened.message);
  Coordinator coordinator(root, native.value, fts.get());
  std::vector<Row> base; base.reserve(500);
  for (uint32_t i = 0; i < 500; ++i) {
    Row row; row.pk = "perf-" + std::to_string(i); row.file_id = i;
    row.text = i == 0 ? "bridge baseline marker" : "bridge common record";
    row.file_name = "perf-" + std::to_string(i) + ".json";
    row.symbol_type = "function"; row.directories = {17};
    row.vector = {static_cast<float>(i), static_cast<float>(i % 7), 1.0f, 0.0f};
    base.push_back(std::move(row));
  }
  if (!coordinator.Commit(base, 0)) throw std::runtime_error("publish performance base");
  const uint32_t old_generation = coordinator.SearchSnapshot();
  const auto query_vector = base.front().vector;
  (void)coordinator.Hybrid(old_generation, query_vector, "bridge common", 50);
  std::vector<double> baseline_ms;
  for (int i = 0; i < 20; ++i) {
    const auto begin = std::chrono::steady_clock::now();
    (void)coordinator.Hybrid(old_generation, query_vector, "bridge common", 50);
    baseline_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - begin).count());
  }
  auto next = base; next.front().text = "bridge updated marker";
  next.front().vector = {0.0f, 0.0f, 1.0f, 1.0f};
  std::atomic<bool> writer_locked{false}, reader_attempted{false};
  std::atomic<int> bad_snapshots{0};
  std::vector<double> concurrent_ms;
  concurrent_ms.reserve(20);
  double blocked_query_ms = 0.0;
  std::thread reader([&] {
    while (!writer_locked.load(std::memory_order_acquire)) std::this_thread::yield();
    const auto blocked_begin = std::chrono::steady_clock::now();
    reader_attempted.store(true, std::memory_order_release);
    const auto pinned_hits = coordinator.Hybrid(old_generation, query_vector, "bridge common", 50);
    blocked_query_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - blocked_begin).count();
    concurrent_ms.push_back(blocked_query_ms);
    if (pinned_hits.size() != 50) ++bad_snapshots;
    for (int i = 0; i < 19; ++i) {
      const auto begin = std::chrono::steady_clock::now();
      const auto hits = coordinator.Hybrid(old_generation, query_vector, "bridge common", 50);
      concurrent_ms.push_back(std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - begin).count());
      if (hits.size() != 50) ++bad_snapshots;
    }
  });
  double writer_ms = 0.0;
  bool committed = false;
  std::thread writer([&] {
    const auto writer_begin = std::chrono::steady_clock::now();
    committed = coordinator.Commit(next, old_generation, {}, {}, [&] {
      writer_locked.store(true, std::memory_order_release);
      while (!reader_attempted.load(std::memory_order_acquire)) std::this_thread::yield();
    });
    writer_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - writer_begin).count();
  });
  writer.join();
  reader.join();
  if (!committed) throw std::runtime_error("performance update commit failed");
  const uint32_t new_generation = coordinator.SearchSnapshot();
  if (coordinator.SearchFts(old_generation, "baseline marker", m4::FilterExpr::All(), 10).size() != 1 ||
      coordinator.SearchFts(new_generation, "updated marker", m4::FilterExpr::All(), 10).size() != 1 ||
      bad_snapshots.load() != 0)
    throw std::runtime_error("performance snapshot correctness check failed");
  std::cout << "{\"benchmark\":\"m4_bridge_snapshot_lock\",\"documents\":500"
            << ",\"writer_ms\":" << writer_ms
            << ",\"blocked_query_ms\":" << blocked_query_ms
            << ",\"reader_count\":" << concurrent_ms.size()
            << ",\"baseline_p50_ms\":" << PercentileMs(baseline_ms, 0.50)
            << ",\"baseline_p95_ms\":" << PercentileMs(baseline_ms, 0.95)
            << ",\"concurrent_p50_ms\":" << PercentileMs(concurrent_ms, 0.50)
            << ",\"concurrent_p95_ms\":" << PercentileMs(concurrent_ms, 0.95)
            << ",\"concurrent_max_ms\":" << PercentileMs(concurrent_ms, 1.0)
            << ",\"bad_snapshots\":" << bad_snapshots.load() << "}\n";
  fts.reset();
  (void)zvec_collection_close(native.value); native.value = nullptr;
  Need(zvec_shutdown(), "shutdown lock performance test");
  return 0;
}
std::vector<Row> ReusePrototypeRows(uint32_t generation) {
  std::vector<Row> rows;
  for (uint32_t i = 0; i < 8; ++i) {
    Row row;
    row.pk = "reuse-" + std::to_string(i);
    row.text = "incremental collection reuse generation " + std::to_string(generation) +
               " document " + std::to_string(i);
    row.file_name = "reuse-" + std::to_string(i) + ".cc";
    row.symbol_type = "function";
    row.file_id = 100 + i;
    row.directories = {7, 17 + i};
    row.vector = {1.0f + static_cast<float>(i),
                  1.0f + static_cast<float>(generation % 5),
                  0.25f * static_cast<float>(i + 1), 0.5f};
    rows.push_back(std::move(row));
  }
  return rows;
}
int RunSharedCollectionReusePrototype(const fs::path& root,
                                      const std::string& executable,
                                      const char* dictionary) {
  if (fs::exists(root)) throw std::runtime_error("reuse prototype root already exists");
  fs::create_directories(root);
  Need(zvec_initialize(nullptr), "initialize shared-collection prototype");
  AtomicManifest(root, 0);
  Native native = OpenVector(root / "vector", true);
  m4::Options options;
  options.fts_field = "text";
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary;
  options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  const auto open_status = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!open_status.ok) throw std::runtime_error("open shared M4 index: " + open_status.message);
  Coordinator coordinator(root, native.value, fts.get());
  EpochPins pins;
  auto try_commit = [&](const std::vector<Row>& rows, uint32_t expected) {
    const uint32_t next = expected + 1;
    const uint32_t oldest = pins.Oldest(next);
    const uint64_t retained_after = static_cast<uint64_t>(next) - oldest + 1;
    if (retained_after > 3) return false;
    return coordinator.Commit(rows, expected);
  };
  auto collect = [&](bool force = false,
                     const std::function<void()>& after_m4_delete = {}) {
    return coordinator.CollectBeforeGeneration(
        pins.Oldest(coordinator.generation()), force, {}, nullptr,
        after_m4_delete);
  };
  std::vector<double> commit_ms, collect_ms;
  auto commit_timed = [&](uint32_t generation) {
    const auto start = std::chrono::steady_clock::now();
    const bool ok = try_commit(ReusePrototypeRows(generation), generation - 1);
    commit_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count());
    return ok;
  };
  auto collect_timed = [&](bool force = false) {
    const auto start = std::chrono::steady_clock::now();
    const bool ok = collect(force);
    collect_ms.push_back(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count());
    return ok;
  };
  Check(commit_timed(1), "reuse prototype publishes initial generation");
  GenerationLease pinned_first(coordinator, pins);
  Check(pinned_first.generation() == 1, "first generation lease pins publication");
  Check(commit_timed(2) && collect_timed(), "reuse commit retains leased generation 1");
  Check(commit_timed(3) && collect_timed(), "reuse commit reaches three-generation cap");
  Check(coordinator.Hybrid(pinned_first.generation(), ReusePrototypeRows(1).front().vector,
                           "incremental collection", 16).size() == 8,
        "old pinned generation remains queryable across commits");
  Check(!commit_timed(4) && coordinator.generation() == 3,
        "writer backpressure enforces the three-generation lease cap");
  pinned_first.Reset();
  GenerationLease current_during_gc(coordinator, pins);
  std::atomic<bool> m4_gc_staged{false}, release_m4_gc{false};
  std::atomic<bool> current_reader_done{false};
  std::vector<Hit> current_during_gc_hits;
  bool gc_ok = false;
  std::thread current_reader([&] {
    while (!m4_gc_staged.load(std::memory_order_acquire))
      std::this_thread::yield();
    current_during_gc_hits = coordinator.Hybrid(
        current_during_gc.generation(), ReusePrototypeRows(3).front().vector,
        "incremental collection", 16);
    current_reader_done.store(true, std::memory_order_release);
  });
  std::thread gc_writer([&] {
    gc_ok = collect(false, [&] {
      m4_gc_staged.store(true, std::memory_order_release);
      while (!release_m4_gc.load(std::memory_order_acquire))
        std::this_thread::yield();
    });
  });
  while (!m4_gc_staged.load(std::memory_order_acquire))
    std::this_thread::yield();
  const auto gc_reader_deadline = std::chrono::steady_clock::now() +
                                  std::chrono::seconds(2);
  while (!current_reader_done.load(std::memory_order_acquire) &&
         std::chrono::steady_clock::now() < gc_reader_deadline)
    std::this_thread::yield();
  const bool reader_completed_during_m4_gc =
      current_reader_done.load(std::memory_order_acquire);
  release_m4_gc.store(true, std::memory_order_release);
  gc_writer.join();
  current_reader.join();
  Check(gc_ok, "released epoch permits old generation reclamation");
  Check(reader_completed_during_m4_gc && current_during_gc_hits.size() == 8,
        "current-generation Hybrid reader completes coherently during stale M4 GC");
  current_during_gc.Reset();
  Check(NativeDocCount(native.value) == 8 && fts->GetStats().live_docs == 8,
        "reclamation removes old rows from both reused stores");

  for (uint32_t generation = 4; generation <= 11; ++generation) {
    Check(commit_timed(generation), "bounded reuse commit generation " + std::to_string(generation));
    Check(collect_timed(), "bounded reuse GC generation " + std::to_string(generation));
    Check(NativeDocCount(native.value) <= 24 && fts->GetStats().live_docs <= 24,
          "retained live rows stay within the three-generation cap");
    Check(NativeDocCount(native.value) == 8 && fts->GetStats().live_docs == 8,
          "unpinned generation is reclaimed after each bounded cycle");
  }

  // Simulate process death after durable M4 tombstones but before native vector
  // deletion. Restart must keep the published generation and make GC replayable.
  Check(commit_timed(12), "publish pre-crash generation 12");
  Check(NativeDocCount(native.value) == 16 && fts->GetStats().live_docs == 16,
        "pre-crash fixture contains current and one stale generation");
  fts.reset();
  Check(native.Close(), "close native store before GC crash injection");
  Need(zvec_shutdown(), "shutdown before GC crash injection");
  const pid_t child = ::fork();
  if (child < 0) throw std::runtime_error("fork GC crash child");
  if (child == 0) {
    ::execl(executable.c_str(), executable.c_str(), "--reuse-gc-crash-child",
            root.c_str(), dictionary, "crash_after_m4_gc", static_cast<char*>(nullptr));
    ::_exit(127);
  }
  int child_status = 0;
  if (::waitpid(child, &child_status, 0) != child)
    throw std::runtime_error("wait for GC crash child");
  Check(WIFEXITED(child_status) && WEXITSTATUS(child_status) == 84,
        "fault injection crashes after durable M4 delete before vector delete");

  Need(zvec_initialize(nullptr), "reinitialize after GC crash");
  native = OpenVector(root / "vector", false);
  const auto reopen_status = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!reopen_status.ok) throw std::runtime_error("reopen M4 after GC crash: " + reopen_status.message);
  Coordinator recovered(root, native.value, fts.get());
  Check(recovered.generation() == 12, "recovery retains the published shared-collection generation");
  Check(recovered.CollectBeforeGeneration(12, true), "recovery replays GC idempotently");
  Check(NativeDocCount(native.value) == 8 && fts->GetStats().live_docs == 8 &&
            fts->GetStats().dead_docs == 0,
        "post-crash GC converges both stores to the current generation");
  Check(recovered.Hybrid(12, ReusePrototypeRows(12).front().vector,
                         "incremental collection", 16).size() == 8,
        "published generation remains searchable after GC recovery");
  Check(recovered.CollectBeforeGeneration(12) &&
            recovered.last_gc_generations_examined() == 0,
        "durable GC cursor makes repeated no-op collection constant work");

  // A crash after native deletion but before the atomic cursor checkpoint must
  // replay idempotently rather than skipping data or rescanning old history.
  Check(recovered.Commit(ReusePrototypeRows(13), 12),
        "publish generation before post-vector-GC crash");
  fts.reset();
  Check(native.Close(), "close native store before post-vector-GC crash");
  native.value = nullptr;
  Need(zvec_shutdown(), "shutdown before post-vector-GC crash");
  const pid_t vector_gc_child = ::fork();
  if (vector_gc_child < 0) throw std::runtime_error("fork vector-GC crash child");
  if (vector_gc_child == 0) {
    ::execl(executable.c_str(), executable.c_str(), "--reuse-gc-crash-child",
            root.c_str(), dictionary, "crash_after_vector_gc", static_cast<char*>(nullptr));
    ::_exit(127);
  }
  int vector_gc_status = 0;
  if (::waitpid(vector_gc_child, &vector_gc_status, 0) != vector_gc_child)
    throw std::runtime_error("wait for vector-GC crash child");
  Check(WIFEXITED(vector_gc_status) && WEXITSTATUS(vector_gc_status) == 85,
        "fault injection crashes after vector delete before durable GC cursor");
  Need(zvec_initialize(nullptr), "reinitialize after vector-GC crash");
  native = OpenVector(root / "vector", false);
  const auto post_vector_status = m4::Index::Open((root / "fts").string(), options, &fts);
  if (!post_vector_status.ok)
    throw std::runtime_error("reopen M4 after vector-GC crash: " + post_vector_status.message);
  Coordinator post_vector_recovery(root, native.value, fts.get());
  Check(post_vector_recovery.generation() == 13,
        "post-vector-GC recovery preserves the published generation");
  Check(post_vector_recovery.CollectBeforeGeneration(13) &&
            post_vector_recovery.last_gc_generations_examined() == 1,
        "post-vector-GC recovery replays exactly the uncheckpointed generation");
  Check(post_vector_recovery.CollectBeforeGeneration(13) &&
            post_vector_recovery.last_gc_generations_examined() == 0,
        "post-vector-GC replay advances a durable constant-work cursor");
  Check(NativeDocCount(native.value) == 8 && fts->GetStats().live_docs == 8 &&
            post_vector_recovery.Hybrid(13, ReusePrototypeRows(13).front().vector,
                                        "incremental collection", 16).size() == 8,
        "both stores and search remain coherent after cursor replay");
  const uint32_t stable_generation = post_vector_recovery.generation();

  // Reserved-but-unpublished generations are invisible but still occupy both
  // stores. Recovery GC must reclaim them after each possible pre-publish crash.
  const std::array<std::tuple<const char*, int, uint64_t, uint64_t>, 3> commit_crashes = {{
      {"after_reserve", 80, 8, 8},
      {"after_m4", 81, 32, 8},
      {"after_vector", 82, 32, 32},
  }};
  for (const auto& [stage, expected_exit, expected_m4_docs, expected_native_docs] : commit_crashes) {
    fts.reset();
    Check(native.Close(), "close native before unpublished-generation crash probe");
    native.value = nullptr;
    Need(zvec_shutdown(), "shutdown before unpublished-generation crash probe");
    Check(SpawnCrash(executable.c_str(), root, dictionary, stage) == expected_exit,
          std::string("process crash at ") + stage);
    Need(zvec_initialize(nullptr), "reopen after unpublished-generation crash");
    native = OpenVector(root / "vector", false);
    const auto status = m4::Index::Open((root / "fts").string(), options, &fts);
    if (!status.ok) throw std::runtime_error("reopen M4 after unpublished crash: " + status.message);
    Coordinator orphan_recovery(root, native.value, fts.get());
    Check(orphan_recovery.generation() == stable_generation,
          std::string("unpublished generation stays hidden after ") + stage);
    Check(fts->GetStats().live_docs == expected_m4_docs &&
              NativeDocCount(native.value) == expected_native_docs,
          std::string("crash leaves expected recoverable staging rows after ") + stage);
    Check(orphan_recovery.CollectBeforeGeneration(stable_generation),
          std::string("reclaim reserved unpublished generation after ") + stage);
    Check(orphan_recovery.last_gc_generations_examined() == 1,
          std::string("GC cursor examines only the new orphan after ") + stage);
    Check(orphan_recovery.CollectBeforeGeneration(stable_generation) &&
              orphan_recovery.last_gc_generations_examined() == 0,
          std::string("durable GC cursor avoids rechecking prior orphan after ") + stage);
    Check(fts->GetStats().live_docs == 8 && NativeDocCount(native.value) == 8,
          std::string("reclamation bounds both stores after ") + stage);
  }
  std::cout << std::fixed << std::setprecision(3)
            << "reuse_prototype checks=" << checks.load()
            << " failures=" << failures.load()
            << " commit_samples=" << commit_ms.size()
            << " gc_samples=" << collect_ms.size()
            << " current_generation=" << stable_generation
            << " native_docs=" << NativeDocCount(native.value)
            << " m4_live_docs=" << fts->GetStats().live_docs
            << " architecture=shared_collection+epoch_gc+bounded_backpressure\n";
  fts.reset();
  Check(native.Close(), "close native store after shared-collection prototype");
  Need(zvec_shutdown(), "shutdown shared-collection prototype");
  return failures.load() == 0 ? 0 : 1;
}

int RunSharedCollectionPerformance(const fs::path& root, const char* dictionary,
                                   size_t documents, size_t rounds) {
  if (documents < 64 || rounds == 0 || (fs::exists(root) && !fs::is_empty(root)))
    throw std::runtime_error("shared-collection benchmark requires a fresh root, >=64 docs and >=1 round");
  fs::create_directories(root);
  // Coordinator::Recover reads the initial generation before the first commit.
  AtomicManifest(root, 0);
  Need(zvec_initialize(nullptr), "initialize shared-collection performance arm");
  auto rows = MakeChurnWorkload(documents);
  const auto query_vector = rows.front().vector;
  const size_t topk = std::min<size_t>(50, documents);
  const uint64_t process_write_start = ProcessWriteBytes();
  auto start = std::chrono::steady_clock::now();
  const double native_open_cpu_start = CurrentThreadCpuMs();
  Native native = OpenVector(root / "vector", true);
  const double native_open_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - start).count();
  const double native_open_cpu_ms = CurrentThreadCpuMs() - native_open_cpu_start;
  m4::Options options;
  options.fts_field = "text";
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dictionary;
  options.fsync_wal = true;
  std::unique_ptr<m4::Index> fts;
  start = std::chrono::steady_clock::now();
  const double m4_open_cpu_start = CurrentThreadCpuMs();
  const auto open_status = m4::Index::Open((root / "fts").string(), options, &fts);
  const double m4_open_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - start).count();
  const double m4_open_cpu_ms = CurrentThreadCpuMs() - m4_open_cpu_start;
  if (!open_status.ok) throw std::runtime_error("open shared M4 benchmark index: " + open_status.message);
  Coordinator coordinator(root, native.value, fts.get());
  EpochPins pins;
  start = std::chrono::steady_clock::now();
  const double initial_commit_cpu_start = CurrentThreadCpuMs();
  if (!coordinator.Commit(rows, 0)) throw std::runtime_error("publish shared-collection base generation");
  const double initial_commit_ms = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - start).count();
  const double initial_commit_cpu_ms = CurrentThreadCpuMs() - initial_commit_cpu_start;
  if (NativeDocCount(native.value) != documents || fts->GetStats().live_docs != documents)
    throw std::runtime_error("shared-collection base row counts diverged");
  {
    GenerationLease warmup(coordinator, pins);
    if (coordinator.Hybrid(warmup.generation(), query_vector, "request timeout", topk).size() != topk)
      throw std::runtime_error("shared-collection warmup query was incomplete");
  }

  std::vector<double> writer_commit_ms, writer_gc_ms, writer_cycle_ms, reader_total_ms,
      reader_cpu_ms, m4_apply_ms, m4_flush_ms, native_upsert_ms, native_flush_ms,
      publish_ms, gc_m4_delete_ms, gc_m4_flush_ms, gc_native_delete_ms,
      gc_native_flush_ms, gc_compaction_ms, gc_checkpoint_ms;
  std::vector<size_t> mutation_counts, gc_examined;
  std::vector<double> writer_write_bytes_per_round;
  std::map<uint32_t, std::set<std::string>> expected_pks;
  expected_pks.emplace(coordinator.generation(), RowPks(rows));
  std::mutex sync_mutex;
  std::condition_variable sync_cv;
  size_t requested = 0, ready = 0, released = 0, completed = 0;
  bool stop = false;
  std::atomic<size_t> coherence_errors{0};
  std::mutex sample_mutex;
  std::vector<std::thread> readers;
  for (size_t reader = 0; reader < 4; ++reader) readers.emplace_back([&] {
    for (size_t round = 0; round < rounds; ++round) {
      {
        std::unique_lock<std::mutex> lock(sync_mutex);
        sync_cv.wait(lock, [&] { return stop || requested > round; });
        if (stop) return;
        ++ready;
        sync_cv.notify_all();
        sync_cv.wait(lock, [&] { return stop || released > round; });
        if (stop) return;
      }
      const auto query_start = std::chrono::steady_clock::now();
      const double query_cpu_start = CurrentThreadCpuMs();
      GenerationLease lease(coordinator, pins);
      const uint32_t generation = lease.generation();
      const auto hits = coordinator.Hybrid(generation, query_vector, "request timeout", topk);
      const double elapsed_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - query_start).count();
      const double cpu_ms = CurrentThreadCpuMs() - query_cpu_start;
      std::set<std::string> seen;
      bool coherent = hits.size() == topk;
      {
        std::lock_guard<std::mutex> lock(sync_mutex);
        const auto expected = expected_pks.find(generation);
        coherent = coherent && expected != expected_pks.end();
        for (const auto& hit : hits) {
          coherent = coherent && expected != expected_pks.end() &&
                     expected->second.count(hit.pk) != 0 && seen.insert(hit.pk).second;
        }
      }
      if (!coherent) coherence_errors.fetch_add(1, std::memory_order_relaxed);
      {
        std::lock_guard<std::mutex> lock(sample_mutex);
        reader_total_ms.push_back(elapsed_ms);
        reader_cpu_ms.push_back(cpu_ms);
      }
      {
        std::lock_guard<std::mutex> lock(sync_mutex);
        ++completed;
      }
      sync_cv.notify_all();
    }
  });

  double writer_thread_cpu_ms = 0;
  for (size_t round = 0; round < rounds; ++round) {
    std::vector<Row> next_rows;
    const auto mutations = ChurnBatch(rows, round, &next_rows);
    const uint32_t expected_generation = coordinator.generation();
    const uint32_t next_generation = expected_generation + 1;
    {
      std::unique_lock<std::mutex> lock(sync_mutex);
      expected_pks.emplace(next_generation, RowPks(next_rows));
      requested = round + 1;
      sync_cv.notify_all();
      sync_cv.wait(lock, [&] { return ready == (round + 1) * 4 || stop; });
      if (stop) break;
    }
    const double cpu_before = CurrentThreadCpuMs();
    const uint64_t write_before = ProcessWriteBytes();
    const auto cycle_start = std::chrono::steady_clock::now();
    {
      std::lock_guard<std::mutex> lock(sync_mutex);
      released = round + 1;
    }
    sync_cv.notify_all();
    SharedCommitTimings commit_timing;
    const auto commit_start = std::chrono::steady_clock::now();
    const bool committed = coordinator.Commit(next_rows, expected_generation, {}, {}, {},
                                               &commit_timing);
    const double commit_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - commit_start).count();
    SharedGcTimings gc_timing;
    bool collected = false;
    double gc_ms = 0;
    if (committed) {
      const auto gc_start = std::chrono::steady_clock::now();
      collected = coordinator.CollectBeforeGeneration(pins.Oldest(coordinator.generation()),
                                                        false, {}, &gc_timing);
      gc_ms = std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - gc_start).count();
    }
    const double cycle_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - cycle_start).count();
    writer_thread_cpu_ms += CurrentThreadCpuMs() - cpu_before;
    if (committed && collected) {
      writer_commit_ms.push_back(commit_ms);
      writer_gc_ms.push_back(gc_ms);
      writer_cycle_ms.push_back(cycle_ms);
      m4_apply_ms.push_back(commit_timing.m4_apply_ms);
      m4_flush_ms.push_back(commit_timing.m4_flush_ms);
      native_upsert_ms.push_back(commit_timing.native_upsert_ms);
      native_flush_ms.push_back(commit_timing.native_flush_ms);
      publish_ms.push_back(commit_timing.publish_ms);
      gc_m4_delete_ms.push_back(gc_timing.m4_search_delete_ms);
      gc_m4_flush_ms.push_back(gc_timing.m4_flush_ms);
      gc_native_delete_ms.push_back(gc_timing.native_delete_ms);
      gc_native_flush_ms.push_back(gc_timing.native_flush_ms);
      gc_compaction_ms.push_back(gc_timing.compaction_ms);
      gc_checkpoint_ms.push_back(gc_timing.checkpoint_ms);
      mutation_counts.push_back(mutations.size());
      gc_examined.push_back(gc_timing.generations_examined);
      writer_write_bytes_per_round.push_back(static_cast<double>(ProcessWriteBytes() - write_before));
    }
    {
      std::unique_lock<std::mutex> lock(sync_mutex);
      sync_cv.wait(lock, [&] { return completed == (round + 1) * 4; });
    }
    if (!committed || !collected) {
      std::lock_guard<std::mutex> lock(sync_mutex);
      stop = true;
      sync_cv.notify_all();
      break;
    }
    rows = std::move(next_rows);
    if (NativeDocCount(native.value) != documents || fts->GetStats().live_docs != documents) {
      coherence_errors.fetch_add(1, std::memory_order_relaxed);
      std::lock_guard<std::mutex> lock(sync_mutex);
      stop = true;
      sync_cv.notify_all();
      break;
    }
  }
  {
    std::lock_guard<std::mutex> lock(sync_mutex);
    stop = true;
  }
  sync_cv.notify_all();
  for (auto& reader : readers) reader.join();

  const auto final_generation = coordinator.generation();
  GenerationLease final_lease(coordinator, pins);
  const auto final_hits = coordinator.Hybrid(final_lease.generation(), query_vector,
                                              "request timeout", topk);
  if (final_generation != final_lease.generation() || final_hits.size() != topk ||
      NativeDocCount(native.value) != documents || fts->GetStats().live_docs != documents)
    coherence_errors.fetch_add(1, std::memory_order_relaxed);
  final_lease.Reset();
  const size_t failures = coherence_errors.load(std::memory_order_relaxed) +
      (writer_cycle_ms.size() == rounds && reader_total_ms.size() == rounds * 4 ? 0 : 1);
  const uint64_t process_write_bytes = ProcessWriteBytes() - process_write_start;
  const uint64_t root_bytes = DirectoryBytes(root);
  const auto fts_stats = fts->GetStats();
  std::cout << std::fixed << std::setprecision(3)
            << "{\"mode\":\"shared_collection\",\"documents\":" << documents
            << ",\"rounds\":" << rounds << ",\"completed_rounds\":" << writer_cycle_ms.size()
            << ",\"generation\":" << final_generation
            << ",\"native_open_ms\":" << native_open_ms
            << ",\"native_open_cpu_ms\":" << native_open_cpu_ms
            << ",\"m4_open_ms\":" << m4_open_ms
            << ",\"m4_open_cpu_ms\":" << m4_open_cpu_ms
            << ",\"initial_commit_ms\":" << initial_commit_ms
            << ",\"initial_commit_cpu_ms\":" << initial_commit_cpu_ms
            << ",\"writer_cycle_p50_ms\":" << PercentileMs(writer_cycle_ms, .50)
            << ",\"writer_cycle_p95_ms\":" << PercentileMs(writer_cycle_ms, .95)
            << ",\"writer_cycle_p99_ms\":" << PercentileMs(writer_cycle_ms, .99)
            << ",\"writer_cycle_max_ms\":" << PercentileMs(writer_cycle_ms, 1.0)
            << ",\"reader_total_p50_ms\":" << PercentileMs(reader_total_ms, .50)
            << ",\"reader_total_p95_ms\":" << PercentileMs(reader_total_ms, .95)
            << ",\"reader_total_p99_ms\":" << PercentileMs(reader_total_ms, .99)
            << ",\"reader_total_max_ms\":" << PercentileMs(reader_total_ms, 1.0)
            << ",\"reader_thread_cpu_p50_ms\":" << PercentileMs(reader_cpu_ms, .50)
            << ",\"reader_thread_cpu_p95_ms\":" << PercentileMs(reader_cpu_ms, .95)
            << ",\"writer_thread_cpu_ms\":" << writer_thread_cpu_ms
            << ",\"process_write_bytes\":" << process_write_bytes
            << ",\"root_bytes\":" << root_bytes
            << ",\"native_live_docs\":" << NativeDocCount(native.value)
            << ",\"m4_live_docs\":" << fts_stats.live_docs
            << ",\"m4_dead_docs\":" << fts_stats.dead_docs
            << ",\"coherence_errors\":" << coherence_errors.load()
            << ",\"failures\":" << failures;
  auto write_samples = [](const char* name, const std::vector<double>& values) {
    std::cout << ",\"" << name << "\":[";
    for (size_t i = 0; i < values.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << values[i];
    }
    std::cout << ']';
  };
  auto write_counts = [](const char* name, const std::vector<size_t>& values) {
    std::cout << ",\"" << name << "\":[";
    for (size_t i = 0; i < values.size(); ++i) {
      if (i) std::cout << ',';
      std::cout << values[i];
    }
    std::cout << ']';
  };
  write_samples("writer_commit_ms", writer_commit_ms);
  write_samples("writer_gc_ms", writer_gc_ms);
  write_samples("writer_cycle_ms", writer_cycle_ms);
  write_samples("writer_write_bytes_per_round", writer_write_bytes_per_round);
  write_samples("reader_total_ms", reader_total_ms);
  write_samples("reader_thread_cpu_ms", reader_cpu_ms);
  write_samples("m4_apply_ms", m4_apply_ms);
  write_samples("m4_flush_ms", m4_flush_ms);
  write_samples("native_upsert_ms", native_upsert_ms);
  write_samples("native_flush_ms", native_flush_ms);
  write_samples("publish_ms", publish_ms);
  write_samples("gc_m4_search_delete_ms", gc_m4_delete_ms);
  write_samples("gc_m4_flush_ms", gc_m4_flush_ms);
  write_samples("gc_native_delete_ms", gc_native_delete_ms);
  write_samples("gc_native_flush_ms", gc_native_flush_ms);
  write_samples("gc_compaction_ms", gc_compaction_ms);
  write_samples("gc_checkpoint_ms", gc_checkpoint_ms);
  write_counts("logical_mutations_per_round", mutation_counts);
  write_counts("gc_generations_examined", gc_examined);
  std::cout << "}\n";
  fts.reset();
  if (!native.Close()) throw std::runtime_error("close shared-collection performance native store");
  Need(zvec_shutdown(), "shutdown shared-collection performance arm");
  return failures == 0 ? 0 : 1;
}

int main(int argc, char** argv) {
  if (argc == 6 && std::string(argv[3]) == "--shared-collection-performance") {
    try {
      return RunSharedCollectionPerformance(argv[1], argv[2], std::stoull(argv[4]),
                                            std::stoull(argv[5]));
    } catch (const std::exception& error) {
      std::cerr << "SHARED COLLECTION PERFORMANCE: " << error.what() << '\n';
      (void)zvec_shutdown();
      return 14;
    }
  }
  if (argc == 4 && std::string(argv[1]) == "--shared-collection-reuse-prototype") {
    try {
      return RunSharedCollectionReusePrototype(argv[2], fs::absolute(argv[0]).string(), argv[3]);
    } catch (const std::exception& error) {
      std::cerr << "FATAL reuse prototype: " << error.what() << '\n';
      (void)zvec_shutdown();
      return 3;
    }
  }
  if (argc == 5 && std::string(argv[1]) == "--reuse-gc-crash-child") {
    try {
      Need(zvec_initialize(nullptr), "initialize GC crash child");
      const fs::path root(argv[2]);
      Native native = OpenVector(root / "vector", false);
      m4::Options options;
      options.fts_field = "text";
      options.fts_tokenizer_name = "jieba";
      options.jieba_dict_dir = argv[3];
      options.fsync_wal = true;
      std::unique_ptr<m4::Index> fts;
      const auto status = m4::Index::Open((root / "fts").string(), options, &fts);
      if (!status.ok) throw std::runtime_error("open M4 in GC crash child: " + status.message);
      Coordinator coordinator(root, native.value, fts.get());
      coordinator.CollectBeforeGeneration(coordinator.generation(), false, argv[4]);
      return 86;
    } catch (...) {
      (void)zvec_shutdown();
      return 87;
    }
  }
  // Focused process-crash recovery oracle, not a performance workload.
  if (argc == 6 && std::string(argv[1]) == "--delta-recover-check") {
    try {
      Need(zvec_initialize(nullptr), "initialize recovery check");
      uint32_t highwater = 0;
      auto snapshot = RecoverDeltaVectorSnapshot(argv[2], argv[3], &highwater);
      Check(snapshot->id == std::stoul(argv[4]) && highwater == std::stoul(argv[5]),
            "fault recovery selects expected committed generation/highwater");
      Check(snapshot->fts->GetStats().live_docs == snapshot->rows.size(),
            "fault recovery FTS live count matches snapshot");
      Check(HitPks(snapshot->Hybrid(snapshot->rows.front().vector, "delta marker", 100).hits) ==
                RowPks(snapshot->rows), "fault recovery vector/FTS snapshot remains coherent");
      snapshot.reset();
      Need(zvec_shutdown(), "shutdown recovery check");
      return failures == 0 ? 0 : 1;
    } catch (const std::exception& e) {
      std::cerr << "DELTA RECOVERY CHECK: " << e.what() << '\n'; return 91;
    }
  }
  if (argc == 5 && std::string(argv[1]) == "--delta-crash-child") {
    try { return RunDeltaVectorCrashChild(argv[2], argv[3], argv[4]); }
    catch (const std::exception& e) { std::cerr << "DELTA CRASH CHILD: " << e.what() << '\n'; return 91; }
  }
  if (argc == 4 && std::string(argv[3]) == "--vector-delta-candidate") {
    try { return RunVectorDeltaCandidate(argv[1], argv[2], argv[0]); }
    catch (const std::exception& e) { std::cerr << "VECTOR DELTA CANDIDATE: " << e.what() << '\n'; return 10; }
  }
  if (argc == 4 && std::string(argv[3]) == "--reader-compaction-crossover") {
    try { return RunReaderCompactionCrossover(argv[1], argv[2]); }
    catch (const std::exception& e) { std::cerr << "READER COMPACTION CROSSOVER: " << e.what() << '\n'; return 13; }
  }
  if (argc == 4 && std::string(argv[3]) == "--reader-search-diagnostics") {
    try { return RunReaderCompactionCrossover(argv[1], argv[2], true, true); }
    catch (const std::exception& e) { std::cerr << "READER SEARCH DIAGNOSTICS: " << e.what() << '\n'; return 13; }
  }
  if (argc == 4 && std::string(argv[3]) == "--reader-search-control") {
    try { return RunReaderCompactionCrossover(argv[1], argv[2], true, false); }
    catch (const std::exception& e) { std::cerr << "READER SEARCH CONTROL: " << e.what() << '\n'; return 13; }
  }
  if (argc == 4 && std::string(argv[3]) == "--reader-search-counters") {
    try { return RunReaderCompactionCrossover(argv[1], argv[2], true, true, true, false); }
    catch (const std::exception& e) { std::cerr << "READER SEARCH COUNTERS: " << e.what() << '\n'; return 13; }
  }
  if (argc == 4 && std::string(argv[3]) == "--reader-search-timing") {
    try { return RunReaderCompactionCrossover(argv[1], argv[2], true, true, false, true); }
    catch (const std::exception& e) { std::cerr << "READER SEARCH TIMING: " << e.what() << '\n'; return 13; }
  }
  if (argc == 4 && std::string(argv[3]) == "--incremental-fts-candidate") {
    try { return RunIncrementalM4DeltaCandidate(argv[1], argv[2]); }
    catch (const std::exception& e) { std::cerr << "INCREMENTAL M4 CANDIDATE: " << e.what() << '\n'; return 11; }
  }
  if (argc == 6 && std::string(argv[3]) == "--lock-sustained") {
    try { return RunLockSustained(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "LOCK BENCH: " << e.what() << '\n'; return 5; }
  }
  if (argc == 6 && std::string(argv[3]) == "--generation-stage-profile") {
    try { return RunGenerationStageProfile(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "GENERATION STAGE PROFILE: " << e.what() << '\n'; return 9; }
  }
  if (argc == 6 && std::string(argv[3]) == "--generation-sustained") {
    try { return RunImmutableGenerationPerformance(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "GENERATION BENCH: " << e.what() << '\n'; return 6; }
  }
  if (argc == 6 && std::string(argv[3]) == "--delta-vector-sustained") {
    try { return RunDeltaVectorPerformance(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "DELTA VECTOR BENCH: " << e.what() << '\n'; return 8; }
  }
  if (argc == 6 && std::string(argv[3]) == "--incremental-fts-sustained") {
    try { return RunDeltaVectorPerformance(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), true, true); }
    catch (const std::exception& e) { std::cerr << "INCREMENTAL FTS BENCH: " << e.what() << '\n'; return 12; }
  }
  if (argc == 6 && std::string(argv[3]) == "--incremental-fts-sustained-no-analyzer-cache") {
    try { return RunDeltaVectorPerformance(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), true, false, false); }
    catch (const std::exception& e) { std::cerr << "INCREMENTAL FTS BENCH: " << e.what() << '\n'; return 12; }
  }
  if (argc == 6 && std::string(argv[3]) == "--incremental-fts-sustained-cached-fingerprint") {
    try { return RunDeltaVectorPerformance(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), true, true, true); }
    catch (const std::exception& e) { std::cerr << "INCREMENTAL FTS BENCH: " << e.what() << '\n'; return 12; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-vector-reference") {
    try { return RunNativeVectorReference(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "NATIVE REFERENCE: " << e.what() << '\n'; return 7; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-hnsw-counter-fixture") {
    try { return RunNativeHnswCounterFixture(argv[1], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "NATIVE HNSW COUNTER FIXTURE: " << e.what() << '\n'; return 14; }
  }
  if (argc == 4 && std::string(argv[3]) == "--native-counter-coverage-fixture") {
    try { return RunNativeCounterCoverageFixture(argv[1]); }
    catch (const std::exception& e) { std::cerr << "NATIVE COUNTER COVERAGE FIXTURE: " << e.what() << '\n'; return 15; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-route-cost-model-fixture") {
    try { return RunNativeRouteCostModelFixture(argv[1], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "NATIVE ROUTE COST MODEL FIXTURE: " << e.what() << '\n'; return 16; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-route-counter-parity-fixture") {
    try { return RunNativeRouteCostModelFixture(argv[1], std::stoull(argv[4]), std::stoull(argv[5]), true); }
    catch (const std::exception& e) { std::cerr << "NATIVE ROUTE COUNTER PARITY FIXTURE: " << e.what() << '\n'; return 17; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-hybrid-route-cost-model-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5])); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID ROUTE COST MODEL FIXTURE: " << e.what() << '\n'; return 18; }
  }
  if (argc == 7 && std::string(argv[3]) == "--native-hybrid-route-threshold-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), false, std::stoull(argv[6])); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID ROUTE THRESHOLD FIXTURE: " << e.what() << '\n'; return 20; }
  }
  if (argc == 7 && std::string(argv[3]) == "--native-hybrid-route-threshold-counters-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), true, std::stoull(argv[6])); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID ROUTE THRESHOLD COUNTERS FIXTURE: " << e.what() << '\n'; return 21; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-hybrid-m4-diagnostics-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), false, 0, true); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID M4 DIAGNOSTICS FIXTURE: " << e.what() << '\n'; return 22; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-hybrid-filter-cache-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), false, 0, true, true); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID FILTER CACHE FIXTURE: " << e.what() << '\n'; return 23; }
  }
  if (argc == 6 && std::string(argv[3]) == "--native-hybrid-route-counters-fixture") {
    try { return RunNativeHybridRouteCostModelFixture(argv[1], argv[2], std::stoull(argv[4]), std::stoull(argv[5]), true); }
    catch (const std::exception& e) { std::cerr << "NATIVE HYBRID ROUTE COUNTERS FIXTURE: " << e.what() << '\n'; return 19; }
  }
  if (argc == 5 && std::string(argv[1]) == "--generation-crash-child") {
    try { return GenerationCrashChild(argv[2], argv[3], argv[4]); }
    catch (const std::exception& e) { std::cerr << "GENERATION CHILD: " << e.what() << '\n'; return 91; }
  }
  if (argc == 4 && std::string(argv[3]) == "--generation-lifecycle") {
    try { return RunGenerationLifecycle(argv[1], argv[2], argv[0]); }
    catch (const std::exception& e) { std::cerr << "GENERATION: " << e.what() << '\n'; return 4; }
  }
  if (argc == 4 && std::string(argv[3]) == "--perf") {
    try { return RunLockPerformance(argv[1], argv[2]); }
    catch (const std::exception& e) { std::cerr << "PERF: " << e.what() << '\n'; return 3; }
  }
  if (argc == 5 && std::string(argv[1]) == "--crash-child") {
    try { return ChildCrash(argv[2], argv[3], argv[4]); }
    catch (const std::exception& e) { std::cerr << "CHILD: " << e.what() << '\n'; return 91; }
  }
  if (argc != 3) {
    std::cerr << "usage: m4_bridge_integration <fresh-root> <jieba-dict-dir> [--perf|--generation-lifecycle|--lock-sustained docs rounds|--generation-sustained docs rounds|--delta-vector-sustained docs rounds|--shared-collection-performance docs rounds|--reader-compaction-crossover|--reader-search-diagnostics|--reader-search-control|--incremental-fts-candidate|--incremental-fts-sustained docs rounds|--incremental-fts-sustained-no-analyzer-cache docs rounds|--incremental-fts-sustained-cached-fingerprint docs rounds|--native-vector-reference docs rounds|--native-hnsw-counter-fixture docs queries|--native-counter-coverage-fixture|--native-route-cost-model-fixture docs queries]\n";
    return 2;
  }
  const fs::path root(argv[1]); fs::create_directories(root);
  if (fs::exists(root / "generation.manifest")) { std::cerr << "root must be fresh\n"; return 2; }
  try {
    Need(zvec_initialize(nullptr), "initialize native zvec");
    AtomicManifest(root, 0);
    Native native = OpenVector(root / "vector", true);
    m4::Options options; options.fts_field = "text"; options.fts_tokenizer_name = "jieba"; options.jieba_dict_dir = argv[2]; options.fsync_wal = true;
    std::unique_ptr<m4::Index> fts; auto status = m4::Index::Open((root / "fts").string(), options, &fts);
    if (!status.ok) throw std::runtime_error(status.message);
    auto coordinator = std::make_unique<Coordinator>(root, native.value, fts.get());
    const auto base = Fixtures(); Check(coordinator->Commit(base, 0), "publish base generation");
    const uint32_t gen1 = coordinator->SearchSnapshot();
    const auto lexical = coordinator->SearchFts(gen1, "json request",
        m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids", {17}), 100);
    Check(lexical.size() == 12, "generation-pinned typed array FTS filter");
    Check(coordinator->SearchVector(gen1, base.front().vector,
        "file_name LIKE 'generated-%'", 100).size() == 12,
        "generation-pinned native vector + LIKE filter");
    const auto filtered_top1 = coordinator->SearchVector(gen1, base[1].vector,
        "file_name LIKE 'generated-%'", 1);
    Check(filtered_top1.size() == 1 && filtered_top1.front().pk != base[1].pk,
        "native scalar filter refills before vector top-k");
    Check(coordinator->SearchFts(gen1, "json request", m4::FilterExpr::And({
        m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids", {17}),
        m4::FilterExpr::StringPrefix("file_name", "worker-"),
        m4::FilterExpr::StringEq("symbol_type", "function")}), 100).size() == 8,
        "Boolean + typed scalar filter before lexical top-k");
    Check(!coordinator->Hybrid(gen1, base.front().vector, "database parse", 10).empty(),
        "same-generation hybrid search");
    Check(coordinator->Hybrid(gen1, base.front().vector, "database parse", 24).size() <= 24,
        "hybrid results deduplicate logical primary keys");

    auto next = base; next.erase(next.begin() + 3);
    next[2].text = "updated json recovery signal";
    next[2].vector = {0.0f, 1.0f, 0.0f, 1.0f};
    Row added = base.back(); added.pk = "fragment-added"; added.file_id = 999;
    added.text = "new json recovery signal"; next.push_back(added);
    Check(coordinator->Commit(next, gen1), "publish replacement generation");
    const uint32_t gen2 = coordinator->SearchSnapshot();
    Check(gen2 > gen1, "generation advanced monotonically");
    const auto old_snapshot = coordinator->SearchFts(gen1, "json request", m4::FilterExpr::All(), 100);
    const auto new_snapshot = coordinator->SearchFts(gen2, "recovery signal", m4::FilterExpr::All(), 100);
    Check(old_snapshot.size() == base.size() / 2, "old pinned generation remains readable");
    Check(std::any_of(new_snapshot.begin(), new_snapshot.end(), [](const Hit& h) { return h.pk == "fragment-added"; }), "new snapshot sees inserted row");
    Check(std::none_of(new_snapshot.begin(), new_snapshot.end(), [](const Hit& h) { return h.pk == "fragment-3"; }), "deleted row not resurrected");
    Check(coordinator->SearchVector(gen1, base.front().vector, "", 100).size() == base.size(),
        "vector old-generation snapshot remains readable after publish");
    Check(coordinator->SearchVector(gen2, base.front().vector, "", 100).size() == next.size(),
        "vector current generation matches FTS live cardinality");
    Check(!coordinator->Commit(base, gen1), "stale writer generation is rejected before prepare");

    std::atomic<bool> query_pinned{false}, finish_pinned_query{false}, compact_started{false};
    std::vector<Hit> compact_pinned_hybrid;
    std::thread pinned_reader([&] {
      compact_pinned_hybrid = coordinator->Hybrid(gen2, base.front().vector, "recovery signal", 100, [&] {
        query_pinned.store(true, std::memory_order_release);
        while (!finish_pinned_query.load(std::memory_order_acquire)) std::this_thread::yield();
      });
    });
    while (!query_pinned.load(std::memory_order_acquire)) std::this_thread::yield();
    std::thread compactor([&] {
      compact_started.store(true, std::memory_order_release);
      Check(coordinator->CompactComponents(), "component compaction waits for pinned hybrid reader");
    });
    while (!compact_started.load(std::memory_order_acquire)) std::this_thread::yield();
    finish_pinned_query.store(true, std::memory_order_release);
    pinned_reader.join(); compactor.join();
    Check(compact_pinned_hybrid.size() == next.size(),
        "pinned hybrid completes before component GC/compaction");
    Check(coordinator->SearchFts(gen1, "json request", m4::FilterExpr::All(), 100).size() == base.size() / 2 &&
          coordinator->SearchVector(gen1, base.front().vector, "", 100).size() == base.size(),
        "old logical generation remains readable after component compaction and file GC");

    const auto old_hybrid = coordinator->Hybrid(gen2, base.front().vector, "recovery signal", 100);
    std::vector<std::string> old_hybrid_pks;
    for (const auto& hit : old_hybrid) old_hybrid_pks.push_back(hit.pk);
    std::sort(old_hybrid_pks.begin(), old_hybrid_pks.end());
    std::array<std::atomic<bool>, 2> stage_reached{}, release_stage{};
    std::array<std::atomic<int>, 2> readers_attempted{}, readers_done{};
    std::array<std::array<std::vector<std::string>, 3>, 2> pinned_hybrid_pks;
    for (size_t phase = 0; phase < 2; ++phase) {
      stage_reached[phase].store(false, std::memory_order_relaxed);
      release_stage[phase].store(false, std::memory_order_relaxed);
      readers_attempted[phase].store(0, std::memory_order_relaxed);
      readers_done[phase].store(0, std::memory_order_relaxed);
    }
    std::array<std::thread, 3> readers;
    for (size_t i = 0; i < readers.size(); ++i) {
      readers[i] = std::thread([&, i] {
        for (size_t phase = 0; phase < 2; ++phase) {
          while (!stage_reached[phase].load(std::memory_order_acquire))
            std::this_thread::yield();
          readers_attempted[phase].fetch_add(1, std::memory_order_release);
          auto hits = coordinator->Hybrid(gen2, base.front().vector,
                                          "recovery signal", 100);
          for (const auto& hit : hits)
            pinned_hybrid_pks[phase][i].push_back(hit.pk);
          std::sort(pinned_hybrid_pks[phase][i].begin(),
                    pinned_hybrid_pks[phase][i].end());
          readers_done[phase].fetch_add(1, std::memory_order_release);
        }
      });
    }
    auto pause_for_readers = [&](size_t phase) {
      stage_reached[phase].store(true, std::memory_order_release);
      while (readers_attempted[phase].load(std::memory_order_acquire) != 3)
        std::this_thread::yield();
      while (!release_stage[phase].load(std::memory_order_acquire))
        std::this_thread::yield();
    };
    std::thread writer([&] {
      Check(coordinator->Commit(next, gen2, {}, {}, {}, nullptr,
          [&] { pause_for_readers(0); },
          [&] { pause_for_readers(1); }),
          "deterministic staged generation publishes");
    });
    std::array<bool, 2> readers_completed_during_stage{};
    for (size_t phase = 0; phase < 2; ++phase) {
      while (readers_attempted[phase].load(std::memory_order_acquire) != 3)
        std::this_thread::yield();
      const auto reader_deadline = std::chrono::steady_clock::now() +
                                   std::chrono::seconds(2);
      while (readers_done[phase].load(std::memory_order_acquire) != 3 &&
             std::chrono::steady_clock::now() < reader_deadline)
        std::this_thread::yield();
      readers_completed_during_stage[phase] =
          readers_done[phase].load(std::memory_order_acquire) == 3;
      release_stage[phase].store(true, std::memory_order_release);
    }
    writer.join();
    for (auto& reader : readers) reader.join();
    const uint32_t gen3 = coordinator->generation();
    Check(gen3 == gen2 + 1, "generation advances exactly once after staged publish");
    Check(readers_completed_during_stage[0],
        "hybrid readers complete while unpublished M4 generation is staged");
    Check(readers_completed_during_stage[1],
        "hybrid readers complete between native vector upsert and flush");
    for (size_t phase = 0; phase < 2; ++phase) {
      for (const auto& pks : pinned_hybrid_pks[phase])
        Check(pks == old_hybrid_pks,
            "reader during backend staging observes the exact prior FTS/vector snapshot");
    }
    const auto new_hybrid = coordinator->Hybrid(gen3, base.front().vector, "recovery signal", 100);
    Check(std::any_of(new_hybrid.begin(), new_hybrid.end(), [](const Hit& h) { return h.pk == "fragment-added"; }) &&
          std::none_of(new_hybrid.begin(), new_hybrid.end(), [](const Hit& h) { return h.pk == "fragment-3"; }),
        "new hybrid snapshot sees inserted row and excludes deleted old version");
    auto competing = next;
    competing[0].vector = {0.0f, 0.0f, 1.0f, 1.0f};
    std::atomic<int> writers_won{0};
    std::thread writer_a([&] { if (coordinator->Commit(competing, gen3)) ++writers_won; });
    std::thread writer_b([&] { if (coordinator->Commit(next, gen2)) ++writers_won; });
    writer_a.join(); writer_b.join();
    const uint32_t published_before_crashes = coordinator->generation();
    Check(writers_won.load() == 1 && published_before_crashes == gen3 + 1,
        "overlapping stale-base batches serialize to one published generation");

    // Close both components before the exec'd crash probe; no inherited DB
    // handles or production process state are shared with the child.
    coordinator.reset(); fts.reset();
    (void)zvec_collection_close(native.value); native.value = nullptr;
    Need(zvec_shutdown(), "shutdown before crash probes");
    const std::array<std::pair<const char*, int>, 5> faults = {{{"before_reserve", 79},
        {"after_reserve", 80}, {"after_m4", 81}, {"after_vector", 82},
        {"after_publish_wal", 83}}};
    for (const auto& [stage, expected_exit] : faults) {
      Check(SpawnCrash(argv[0], root, argv[2], stage) == expected_exit,
          std::string("real process crash at ") + stage);
      Need(zvec_initialize(nullptr), "reopen native after crash");
      native = OpenVector(root / "vector", false);
      status = m4::Index::Open((root / "fts").string(), options, &fts);
      Check(status.ok, std::string("M4 reopen after crash at ") + stage);
      coordinator = std::make_unique<Coordinator>(root, native.value, fts.get());
      const uint32_t expected_generation = std::string(stage) == "after_publish_wal" ? 8u : published_before_crashes;
      Check(coordinator->generation() == expected_generation,
          std::string("recovered published generation after ") + stage);
      if (std::string(stage) == "after_publish_wal")
        Check(expected_generation > gen2, "durable publish WAL advances generation on recovery");
      const auto visible = coordinator->SearchFts(coordinator->SearchSnapshot(),
          "recovery signal", m4::FilterExpr::All(), 100);
      const bool recovered_publish = std::string(stage) == "after_publish_wal";
      Check(visible.size() == (recovered_publish ? 0u : 2u),
          std::string("FTS visibility matches commit point after ") + stage);
      const auto visible_vector = coordinator->SearchVector(expected_generation,
          base.front().vector, "", 100);
      Check(visible_vector.size() == base.size(),
          std::string("vector snapshot cardinality after ") + stage);
      Check(std::any_of(visible_vector.begin(), visible_vector.end(), [&](const Hit& h) {
              return h.pk == (recovered_publish ? "fragment-3" : "fragment-added");
            }) &&
            std::none_of(visible_vector.begin(), visible_vector.end(), [&](const Hit& h) {
              return h.pk == (recovered_publish ? "fragment-added" : "fragment-3");
            }), std::string("vector generation filtering after ") + stage);
      const auto recovered_hybrid = coordinator->Hybrid(expected_generation,
          base.front().vector, "recovery signal", 100);
      Check(std::any_of(recovered_hybrid.begin(), recovered_hybrid.end(), [&](const Hit& h) {
              return h.pk == (recovered_publish ? "fragment-3" : "fragment-added");
            }) &&
            std::none_of(recovered_hybrid.begin(), recovered_hybrid.end(), [&](const Hit& h) {
              return h.pk == (recovered_publish ? "fragment-added" : "fragment-3");
            }), std::string("no mixed-generation hybrid result after ") + stage);
      if (std::string(stage) == "before_reserve" || std::string(stage) == "after_reserve" || std::string(stage) == "after_m4" || std::string(stage) == "after_vector") {
        Check(coordinator->generation() == published_before_crashes, "unpublished generation remains hidden");
      }
      coordinator.reset(); fts.reset();
      (void)zvec_collection_close(native.value); native.value = nullptr;
      Need(zvec_shutdown(), "close after crash verification");
    }

    // A torn final coordinator-WAL frame is truncated before a later append;
    // repeated recovery must be idempotent.
    { int fd = ::open((root / "coordinator.wal").c_str(), O_WRONLY | O_APPEND);
      const char torn[] = "P 999 "; (void)::write(fd, torn, sizeof(torn) - 1); (void)::fsync(fd); ::close(fd); }
    Check(Recover(root) == ReadManifest(root), "torn coordinator WAL suffix ignored/repaired");
    Check(Recover(root) == ReadManifest(root), "coordinator recovery idempotent");
    Need(zvec_initialize(nullptr), "initialize final verification");
    native = OpenVector(root / "vector", false);
    status = m4::Index::Open((root / "fts").string(), options, &fts);
    Check(status.ok, "reopen both components after crash matrix");
    coordinator = std::make_unique<Coordinator>(root, native.value, fts.get());
    Check(coordinator->Commit(base, coordinator->generation()), "append after torn WAL repair");
    Check(coordinator->generation() == 9, "generation high-water never reused after abandoned commits");
    const uint32_t final_generation = coordinator->generation();
    coordinator.reset(); fts.reset();
    (void)zvec_collection_close(native.value); native.value = nullptr;
    Need(zvec_shutdown(), "shutdown before analyzer identity check");

    const fs::path changed_dict = root / "changed-dictionary";
    fs::create_directories(changed_dict);
    for (const char* file : {"jieba.dict.utf8", "hmm_model.utf8"})
      fs::copy_file(fs::path(argv[2]) / file, changed_dict / file);
    { std::ofstream out(changed_dict / "jieba.dict.utf8", std::ios::app); out << "zzbridgeword 1 n\\n"; }
    options.jieba_dict_dir = changed_dict.string();
    status = m4::Index::Open((root / "fts").string(), options, &fts);
    Check(!status.ok, "changed dictionary identity rejected on reopen");
    std::cout << "m4_bridge checks=" << checks.load() << " failures=" << failures.load()
              << " generation=" << final_generation << " live_rows=" << base.size()
              << " architecture=full-snapshot-generation-publish\n";
    return failures.load() ? 1 : 0;
  } catch (const std::exception& e) {
    std::cerr << "FATAL: " << e.what() << '\n'; (void)zvec_shutdown(); return 3;
  }
}
