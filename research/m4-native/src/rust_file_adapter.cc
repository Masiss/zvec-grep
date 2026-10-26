#include "m4/rust_file_adapter.h"

#include "m4/index.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <charconv>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <system_error>
#include <unistd.h>

namespace fs = std::filesystem;
namespace {
constexpr size_t kMaxDocBytes = 2u << 20;
constexpr size_t kMaxBatchDocs = 256;
constexpr size_t kMaxBatchBytes = 7u << 20;
constexpr size_t kMaxStateBytes = 256;
constexpr size_t kMaxSearchHits = 65536;
std::atomic<uint64_t> gTempSequence{0};

enum class Visibility { kNotIndexed, kStaging, kIndexed };
struct FileState {
  uint64_t revision{0};
  Visibility visibility{Visibility::kNotIndexed};
  uint32_t expected{0};
  uint32_t acknowledged{0};
  uint32_t retained_docs{0};
};

uint64_t Checksum(const std::string &s) {
  uint64_t value = 14695981039346656037ull;
  for (unsigned char c : s)
    value = (value ^ c) * 1099511628211ull;
  return value;
}
void SetError(char *out, size_t cap, const std::string &message) {
  if (!out || cap == 0)
    return;
  const size_t n = std::min(cap - 1, message.size());
  std::memcpy(out, message.data(), n);
  out[n] = '\0';
}
void WriteAll(int fd, const char *data, size_t size) {
  size_t offset = 0;
  while (offset < size) {
    const ssize_t n = ::write(fd, data + offset, size - offset);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      throw std::system_error(errno, std::generic_category(),
                              "write state manifest");
    offset += static_cast<size_t>(n);
  }
}
std::string StatePrefix(uint64_t file_id, const FileState &state) {
  const char *visibility = state.visibility == Visibility::kIndexed   ? "I"
                           : state.visibility == Visibility::kStaging ? "S"
                                                                      : "N";
  std::ostringstream out;
  out << "M4PF1 " << file_id << ' ' << state.revision << ' ' << visibility
      << ' ' << state.expected << ' ' << state.acknowledged << ' '
      << state.retained_docs;
  return out.str();
}
fs::path StatePath(const fs::path &root, uint64_t file_id) {
  return root / "files" / ("file-" + std::to_string(file_id) + ".state");
}
void PersistState(const fs::path &root, uint64_t file_id,
                  const FileState &state) {
  const fs::path dir = root / "files";
  fs::create_directories(dir);
  const fs::path target = StatePath(root, file_id);
  const fs::path temp = target.string() + ".tmp." + std::to_string(::getpid()) +
                        "." + std::to_string(gTempSequence.fetch_add(1));
  const std::string prefix = StatePrefix(file_id, state);
  std::ostringstream encoded;
  encoded << prefix << ' ' << std::hex << std::setw(16) << std::setfill('0')
          << Checksum(prefix) << '\n';
  const std::string bytes = encoded.str();
  const int fd =
      ::open(temp.c_str(),
             O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (fd < 0)
    throw std::system_error(errno, std::generic_category(), "open state temp");
  try {
    WriteAll(fd, bytes.data(), bytes.size());
    if (::fsync(fd) != 0)
      throw std::system_error(errno, std::generic_category(),
                              "fsync state temp");
  } catch (...) {
    const int saved = errno;
    ::close(fd);
    ::unlink(temp.c_str());
    errno = saved;
    throw;
  }
  if (::close(fd) != 0) {
    const int saved = errno;
    ::unlink(temp.c_str());
    throw std::system_error(saved, std::generic_category(), "close state temp");
  }
  if (::rename(temp.c_str(), target.c_str()) != 0) {
    const int saved = errno;
    ::unlink(temp.c_str());
    throw std::system_error(saved, std::generic_category(),
                            "rename state manifest");
  }
  const int dirfd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dirfd < 0)
    throw std::system_error(errno, std::generic_category(),
                            "open state directory");
  const bool synced = ::fsync(dirfd) == 0;
  const int saved = errno;
  ::close(dirfd);
  if (!synced)
    throw std::system_error(saved, std::generic_category(),
                            "fsync state directory");
}
FileState ReadState(const fs::path &path, uint64_t expected_id) {
  std::error_code size_error;
  const uintmax_t size = fs::file_size(path, size_error);
  if (size_error || size > kMaxStateBytes)
    throw std::runtime_error("M4PF1 state manifest exceeds 256-byte limit");
  std::ifstream in(path, std::ios::binary);
  std::string line;
  if (!in || !std::getline(in, line) ||
      in.peek() != std::char_traits<char>::eof())
    throw std::runtime_error("malformed M4PF1 state manifest");
  std::istringstream fields(line);
  std::string magic, visibility, checksum_text;
  uint64_t file_id = 0;
  FileState state;
  if (!(fields >> magic >> file_id >> state.revision >> visibility >>
        state.expected >> state.acknowledged >> state.retained_docs >>
        checksum_text) ||
      magic != "M4PF1" || file_id != expected_id ||
      (fields >> std::ws && !fields.eof()))
    throw std::runtime_error("malformed M4PF1 state fields");
  if (visibility == "N")
    state.visibility = Visibility::kNotIndexed;
  else if (visibility == "S")
    state.visibility = Visibility::kStaging;
  else if (visibility == "I")
    state.visibility = Visibility::kIndexed;
  else
    throw std::runtime_error("unknown M4PF1 visibility state");
  const std::string prefix = StatePrefix(file_id, state);
  std::ostringstream expected;
  expected << std::hex << std::setw(16) << std::setfill('0')
           << Checksum(prefix);
  if (checksum_text != expected.str() || state.acknowledged > state.expected ||
      state.retained_docs < state.acknowledged ||
      (state.visibility == Visibility::kIndexed &&
       state.acknowledged != state.expected) ||
      (state.visibility == Visibility::kIndexed &&
       state.retained_docs != state.expected))
    throw std::runtime_error("M4PF1 checksum or invariant failure");
  return state;
}
bool ParsePk(const std::string &pk, uint64_t *file_id, uint32_t *ordinal) {
  if (pk.empty() || pk[0] != 'f')
    return false;
  const size_t c = pk.find(":c", 1);
  if (c == std::string::npos)
    return false;
  auto parse = [](const char *first, const char *last, auto *output) {
    const auto result = std::from_chars(first, last, *output);
    return result.ec == std::errc{} && result.ptr == last;
  };
  return parse(pk.data() + 1, pk.data() + c, file_id) &&
         parse(pk.data() + c + 2, pk.data() + pk.size(), ordinal);
}
std::string Pk(uint64_t file_id, uint32_t ordinal) {
  return "f" + std::to_string(file_id) + ":c" + std::to_string(ordinal);
}
void DeleteOrdinals(m4::Index *index, uint64_t file_id, uint32_t begin,
                    uint32_t end) {
  uint64_t ordinal = begin;
  while (ordinal < end) {
    const size_t count =
        static_cast<size_t>(std::min<uint64_t>(kMaxBatchDocs, end - ordinal));
    std::vector<m4::Mutation> deletes;
    deletes.reserve(count);
    for (size_t i = 0; i < count; ++i)
      deletes.emplace_back(m4::MutationKind::DELETE,
                           Pk(file_id, static_cast<uint32_t>(ordinal + i)));
    const m4::Status status = index->ApplyBatch(deletes);
    if (!status.ok)
      throw std::runtime_error(status.message);
    ordinal += count;
  }
}
} // namespace

struct m4_rust_file_index {
  fs::path root;
  std::unique_ptr<m4::Index> index;
  std::map<uint64_t, FileState> files;
  std::mutex mutex;
  bool poisoned{false};
  int lock_fd{-1};
  ~m4_rust_file_index() {
    index.reset();
    if (lock_fd >= 0)
      ::close(lock_fd);
  }
};

namespace {
void EnsureHealthy(const m4_rust_file_index *handle) {
  if (handle->poisoned)
    throw std::runtime_error("adapter state is uncertain; close and reopen");
}
void PersistAndPublish(m4_rust_file_index *handle, uint64_t file_id,
                       const FileState &state) {
  try {
    PersistState(handle->root, file_id, state);
    handle->files[file_id] = state;
  } catch (...) {
    handle->poisoned = true;
    throw;
  }
}
} // namespace

extern "C" m4_rust_file_index *
m4_rust_file_open(const char *root, char *error_out, size_t error_capacity) {
  try {
    if (!root || !*root)
      throw std::invalid_argument("root path is empty");
    auto handle = std::make_unique<m4_rust_file_index>();
    handle->root = fs::absolute(root);
    fs::create_directories(handle->root / "files");
    handle->lock_fd = ::open((handle->root / "writer.lock").c_str(),
                             O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (handle->lock_fd < 0 || ::flock(handle->lock_fd, LOCK_EX | LOCK_NB) != 0)
      throw std::runtime_error(
          "M4 Rust adapter root is already open for writing");
    for (const auto &entry : fs::directory_iterator(handle->root / "files")) {
      if (!entry.is_regular_file() || entry.path().extension() != ".state")
        continue;
      const std::string name = entry.path().filename().string();
      constexpr char prefix[] = "file-";
      if (name.rfind(prefix, 0) != 0)
        throw std::runtime_error("unexpected state manifest name");
      const std::string number = name.substr(
          sizeof(prefix) - 1, name.size() - (sizeof(prefix) - 1) - 6);
      uint64_t file_id = 0;
      const auto parsed = std::from_chars(
          number.data(), number.data() + number.size(), file_id);
      if (parsed.ec != std::errc{} ||
          parsed.ptr != number.data() + number.size())
        throw std::runtime_error("invalid state manifest file id");
      handle->files.emplace(file_id, ReadState(entry.path(), file_id));
    }
    m4::Options options;
    options.fsync_wal = true;
    options.wal_enabled = true;
    m4::Status status = m4::Index::Open((handle->root / "fts").string(),
                                        options, &handle->index);
    if (!status.ok)
      throw std::runtime_error(status.message);
    return handle.release();
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return nullptr;
  } catch (...) {
    SetError(error_out, error_capacity, "unknown adapter open failure");
    return nullptr;
  }
}
extern "C" void m4_rust_file_close(m4_rust_file_index *index) { delete index; }

extern "C" int m4_rust_file_begin(m4_rust_file_index *h, uint64_t file_id,
                                  uint64_t revision, uint32_t expected_docs,
                                  char *error_out, size_t error_capacity) {
  try {
    if (!h)
      throw std::invalid_argument("null adapter handle");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    auto it = h->files.find(file_id);
    if (it != h->files.end() &&
        (revision < it->second.revision ||
         (revision == it->second.revision &&
          it->second.visibility == Visibility::kIndexed)))
      throw std::runtime_error(
          "file revision is not newer than published state");
    const uint32_t retained =
        it == h->files.end()
            ? expected_docs
            : std::max({it->second.retained_docs, it->second.expected,
                        it->second.acknowledged, expected_docs});
    FileState next{revision, Visibility::kStaging, expected_docs, 0, retained};
    PersistAndPublish(h, file_id, next);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_append(m4_rust_file_index *h, uint64_t file_id,
                                   uint64_t revision, uint32_t first_ordinal,
                                   const m4_rust_file_doc *docs,
                                   size_t doc_count, char *error_out,
                                   size_t error_capacity) {
  try {
    if (!h || (!docs && doc_count))
      throw std::invalid_argument("invalid append arguments");
    if (doc_count == 0 || doc_count > 256)
      throw std::invalid_argument("append batch must contain 1..256 documents");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    auto it = h->files.find(file_id);
    if (it == h->files.end() || it->second.visibility != Visibility::kStaging ||
        it->second.revision != revision ||
        it->second.acknowledged != first_ordinal ||
        first_ordinal > it->second.expected ||
        doc_count > it->second.expected - first_ordinal)
      throw std::runtime_error(
          "append does not match durable file staging state");
    std::vector<m4::Mutation> mutations;
    mutations.reserve(doc_count);
    size_t encoded_estimate = 0;
    for (size_t i = 0; i < doc_count; ++i) {
      if (!docs[i].text && docs[i].text_len)
        throw std::invalid_argument("null document text");
      if (docs[i].text_len > kMaxDocBytes)
        throw std::invalid_argument("document exceeds 2 MiB adapter limit");
      if (docs[i].text_len > kMaxBatchBytes - encoded_estimate)
        throw std::invalid_argument("append exceeds bounded batch bytes");
      encoded_estimate += docs[i].text_len;
      const uint32_t ordinal = first_ordinal + static_cast<uint32_t>(i);
      m4::Document document;
      if (docs[i].text_len != 0)
        document.text.assign(reinterpret_cast<const char *>(docs[i].text),
                             docs[i].text_len);
      mutations.emplace_back(m4::MutationKind::UPSERT, Pk(file_id, ordinal),
                             std::move(document));
    }
    if (encoded_estimate > kMaxBatchBytes)
      throw std::invalid_argument("append exceeds bounded batch bytes");
    const m4::Status status = h->index->ApplyBatch(mutations);
    if (!status.ok)
      throw std::runtime_error(status.message);
    FileState next = it->second;
    next.acknowledged += static_cast<uint32_t>(doc_count);
    next.retained_docs = std::max(next.retained_docs, next.acknowledged);
    PersistAndPublish(h, file_id, next);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_publish(m4_rust_file_index *h, uint64_t file_id,
                                    uint64_t revision, char *error_out,
                                    size_t error_capacity) {
  try {
    if (!h)
      throw std::invalid_argument("null adapter handle");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    auto it = h->files.find(file_id);
    if (it != h->files.end() && it->second.revision == revision &&
        it->second.visibility == Visibility::kIndexed &&
        it->second.acknowledged == it->second.expected)
      return 0;
    if (it == h->files.end() || it->second.revision != revision ||
        it->second.visibility != Visibility::kStaging ||
        it->second.acknowledged != it->second.expected)
      throw std::runtime_error(
          "publish requires all expected documents durably acknowledged");
    FileState next = it->second;
    DeleteOrdinals(h->index.get(), file_id, next.expected, next.retained_docs);
    next.visibility = Visibility::kIndexed;
    next.retained_docs = next.expected;
    PersistAndPublish(h, file_id, next);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_rollback(m4_rust_file_index *h, uint64_t file_id,
                                     uint64_t revision, char *error_out,
                                     size_t error_capacity) {
  try {
    if (!h)
      throw std::invalid_argument("null adapter handle");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    const auto it = h->files.find(file_id);
    if (it == h->files.end() || it->second.revision != revision ||
        (it->second.visibility != Visibility::kStaging &&
         it->second.visibility != Visibility::kNotIndexed))
      throw std::runtime_error("rollback does not match hidden file revision");
    const uint32_t retained =
        std::max({it->second.retained_docs, it->second.expected,
                  it->second.acknowledged});
    FileState next{revision, Visibility::kNotIndexed, 0, 0, retained};
    PersistAndPublish(h, file_id, next);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_delete(m4_rust_file_index *h, uint64_t file_id,
                                   uint64_t revision, char *error_out,
                                   size_t error_capacity) {
  try {
    if (!h)
      throw std::invalid_argument("null adapter handle");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    const auto it = h->files.find(file_id);
    if (it != h->files.end() && revision < it->second.revision)
      throw std::runtime_error("delete revision is older than file state");
    if (it != h->files.end() && revision == it->second.revision &&
        it->second.visibility == Visibility::kNotIndexed &&
        it->second.retained_docs == 0)
      return 0;
    const uint32_t retained =
        it == h->files.end()
            ? 0
            : std::max({it->second.retained_docs, it->second.expected,
                        it->second.acknowledged});
    FileState hidden{revision, Visibility::kNotIndexed, 0, 0, retained};
    PersistAndPublish(h, file_id, hidden);
    DeleteOrdinals(h->index.get(), file_id, 0, retained);
    hidden.retained_docs = 0;
    PersistAndPublish(h, file_id, hidden);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_get_state(m4_rust_file_index *h, uint64_t file_id,
                                      m4_rust_file_state *output,
                                      char *error_out, size_t error_capacity) {
  try {
    if (!h || !output)
      throw std::invalid_argument("invalid state arguments");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    *output = {};
    const auto it = h->files.find(file_id);
    if (it == h->files.end())
      return 0;
    output->revision = it->second.revision;
    output->visibility = it->second.visibility == Visibility::kIndexed   ? 2u
                         : it->second.visibility == Visibility::kStaging ? 1u
                                                                         : 0u;
    output->expected = it->second.expected;
    output->acknowledged = it->second.acknowledged;
    output->retained_docs = it->second.retained_docs;
    output->found = 1;
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
extern "C" int m4_rust_file_search(m4_rust_file_index *h, const uint8_t *query,
                                   size_t query_len, size_t limit,
                                   uint32_t scan_cap, m4_rust_file_hit *output,
                                   size_t capacity, size_t *hit_count,
                                   char *error_out, size_t error_capacity) {
  try {
    if (!h || (!query && query_len) || !hit_count || (!output && capacity))
      throw std::invalid_argument("invalid search arguments");
    if (limit > capacity)
      throw std::invalid_argument("hit capacity is below requested limit");
    if (limit > kMaxSearchHits || scan_cap > kMaxSearchHits)
      throw std::invalid_argument("search exceeds 65,536-hit memory bound");
    *hit_count = 0;
    if (limit == 0)
      return 0;
    if (scan_cap < limit)
      throw std::invalid_argument("scan cap is below requested limit");
    std::lock_guard<std::mutex> lock(h->mutex);
    EnsureHealthy(h);
    const std::string query_text(reinterpret_cast<const char *>(query),
                                 query_len);
    uint32_t depth = std::min<uint32_t>(
        scan_cap, static_cast<uint32_t>(std::max<size_t>(limit, 32)));
    std::vector<m4_rust_file_hit> accepted;
    accepted.reserve(limit);
    for (;;) {
      m4::SearchResult result;
      const m4::Status status = h->index->SearchFts(query_text, depth, &result);
      if (!status.ok)
        throw std::runtime_error(status.message);
      accepted.clear();
      for (const auto &hit : result.hits) {
        uint64_t file_id = 0;
        uint32_t ordinal = 0;
        if (!ParsePk(hit.pk, &file_id, &ordinal))
          continue;
        const auto state = h->files.find(file_id);
        if (state == h->files.end() ||
            state->second.visibility != Visibility::kIndexed ||
            ordinal >= state->second.expected)
          continue;
        accepted.push_back(
            {file_id, state->second.revision, ordinal, hit.score});
        if (accepted.size() == limit)
          break;
      }
      if (accepted.size() >= limit || result.hits.size() < depth ||
          depth >= scan_cap)
        break;
      const uint64_t grown = std::max<uint64_t>(
          static_cast<uint64_t>(depth) + 1, static_cast<uint64_t>(depth) * 2);
      depth = static_cast<uint32_t>(std::min<uint64_t>(scan_cap, grown));
    }
    *hit_count = accepted.size();
    std::copy(accepted.begin(), accepted.end(), output);
    return 0;
  } catch (const std::exception &e) {
    SetError(error_out, error_capacity, e.what());
    return -1;
  }
}
