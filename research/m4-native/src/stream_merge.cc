// M4-S3: direct one-pass streaming compaction (format v2).
//
// Payload and block metadata are written directly into the final file. The
// term/value dictionaries are written at the end of their sections, and the
// 120-byte header (which carries section offsets and the checksum) is patched
// after the data is durable. No section is written twice and no merged posting
// list is resident.
//
// v2 layout (all sections contiguous, order: FTS, docs, INVERT):
//   header (120 bytes, patched last)
//   FTS: field name, per-term [payload blocks][block metas], dict, footer
//   docs: count, records
//   INVERT: per-field [value payload+metas][dict], directory, footer
//
// The reader dispatches on the header version and supports v1 segments
// produced by flush.

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <limits>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "format.h"
#include "util.h"

namespace m4 {
namespace {

namespace fs = std::filesystem;

class TempPathGuard {
 public:
  explicit TempPathGuard(std::string path) : path_(std::move(path)) {}
  ~TempPathGuard() {
    if (!released_) {
      std::error_code ec;
      fs::remove(path_, ec);
    }
  }
  void Release() { released_ = true; }

 private:
  std::string path_;
  bool released_{false};
};

constexpr uint32_t kMagic = 0x4D345347;  // "M4SG"
constexpr uint32_t kVersionV2 = 2;
constexpr size_t kHeaderSize = 120;
constexpr uint64_t kNoDoc = std::numeric_limits<uint64_t>::max();
constexpr uint32_t kFtsFooterMagic = 0x46325452;    // "F2TR"
constexpr uint32_t kInvertFooterMagic = 0x49325452; // "I2TR"

struct FtsFooterV2 {
  uint32_t magic;
  uint32_t version;
  uint64_t dict_offset;
  uint64_t dict_size;
  uint32_t term_count;
  uint32_t block_count;
};
static_assert(sizeof(FtsFooterV2) == 32, "v2 fts footer");

struct InvertFooterV2 {
  uint32_t magic;
  uint32_t version;
  uint64_t dir_offset;
  uint64_t dir_size;
  uint32_t field_count;
  uint32_t reserved;
};
static_assert(sizeof(InvertFooterV2) == 32, "v2 invert footer");

class MemoryTracker {
 public:
  explicit MemoryTracker(uint64_t limit) : limit_(limit) {}
  bool Add(uint64_t n) {
    current_ += n;
    peak_ = std::max(peak_, current_);
    return current_ <= limit_;
  }
  void Sub(uint64_t n) { current_ = current_ > n ? current_ - n : 0; }
  uint64_t peak() const { return peak_; }
  uint64_t current() const { return current_; }
  uint64_t limit() const { return limit_; }

 private:
  uint64_t limit_;
  uint64_t current_{0};
  uint64_t peak_{0};
};

class FileWriter {
 public:
  ~FileWriter() {
    if (fd_ >= 0) ::close(fd_);
  }
  Status Open(const std::string& path) {
    path_ = path;
    fd_ = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd_ < 0) {
      return Status::Error("open " + path + ": " + std::strerror(errno));
    }
    return Status::OK();
  }
  Status Write(const void* data, size_t size) {
    if (!ok_) return Status::Error("write after failure");
    const char* p = static_cast<const char*>(data);
    if (buf_.size() + size > kBufferSize) {
      Status s = Flush();
      if (!s.ok) return s;
    }
    if (size >= kBufferSize) return RawWrite(p, size);
    buf_.append(p, size);
    return Status::OK();
  }
  Status Write(const std::string& s) { return Write(s.data(), s.size()); }
  Status Varint(uint64_t v) {
    char tmp[10];
    size_t n = 0;
    while (v >= 0x80) {
      tmp[n++] = static_cast<char>((v & 0x7f) | 0x80);
      v >>= 7;
    }
    tmp[n++] = static_cast<char>(v);
    return Write(tmp, n);
  }
  Status Flush() {
    if (!buf_.empty()) {
      Status s = RawWrite(buf_.data(), buf_.size());
      if (!s.ok) return s;
      buf_.clear();
    }
    return Status::OK();
  }
  Status FsyncClose() {
    Status s = Flush();
    if (s.ok && fd_ >= 0 && ::fsync(fd_) != 0) {
      s = Status::Error("fsync " + path_);
    }
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
    return s;
  }
  uint64_t size() const { return written_ + buf_.size(); }
  int fd() const { return fd_; }

 private:
  Status RawWrite(const char* p, size_t n) {
    size_t done = 0;
    while (done < n) {
      const ssize_t w = ::write(fd_, p + done, n - done);
      if (w < 0) {
        if (errno == EINTR) continue;
        ok_ = false;
        return Status::Error("write " + path_);
      }
      done += static_cast<size_t>(w);
    }
    written_ += n;
    return Status::OK();
  }
  static constexpr size_t kBufferSize = 1 << 20;
  std::string path_;
  int fd_{-1};
  std::string buf_;
  uint64_t written_{0};
  bool ok_{true};
};

Status WriteAllAt(int fd, const char* data, size_t size, off_t offset) {
  size_t done = 0;
  while (done < size) {
    const ssize_t n = ::pwrite(fd, data + done, size - done, offset + done);
    if (n < 0) {
      if (errno == EINTR) continue;
      return Status::Error("pwrite header");
    }
    done += static_cast<size_t>(n);
  }
  return Status::OK();
}

void PutU32(std::string* s, size_t off, uint32_t v) {
  std::memcpy(s->data() + off, &v, 4);
}
void PutU64(std::string* s, size_t off, uint64_t v) {
  std::memcpy(s->data() + off, &v, 8);
}

uint64_t EstimateActiveTermBytes(size_t inputs, size_t term_len) {
  return static_cast<uint64_t>(inputs) * 4096ull + 4096ull + term_len;
}
uint64_t EstimateActiveValueBytes(size_t inputs) {
  return static_cast<uint64_t>(inputs) * 1024ull + 2048ull;
}
uint64_t EstimateDictEntryBytes(std::string_view key) {
  return key.size() + 64;
}

struct StreamPosting {
  DocId doc{0};
  uint32_t tf{0};
  uint32_t dl{0};
  size_t positions_offset{0};
  size_t positions_count{0};
};

Status EncodeFtsBlock(const std::vector<StreamPosting>& block,
                      const std::vector<uint32_t>& positions,
                      std::string* payload, FtsBlockMeta* meta,
                      uint64_t payload_offset) {
  if (block.empty()) return Status::Error("empty fts block");
  BufWriter w;
  w.Varint(block.size());
  uint64_t prev_doc = 0;
  uint32_t max_tf = 0;
  uint32_t min_dl = std::numeric_limits<uint32_t>::max();
  for (const auto& p : block) {
    if (p.positions_offset > positions.size() ||
        p.positions_count > positions.size() - p.positions_offset) {
      return Status::Error("invalid streamed posting positions");
    }
    w.Varint(p.doc - prev_doc);
    prev_doc = p.doc;
    w.Varint(p.tf);
    w.Varint(p.dl);
    w.Varint(p.positions_count);
    uint32_t prev_pos = 0;
    const size_t positions_end = p.positions_offset + p.positions_count;
    for (size_t i = p.positions_offset; i < positions_end; ++i) {
      const uint32_t pos = positions[i];
      w.Varint(pos - prev_pos);
      prev_pos = pos;
    }
    max_tf = std::max(max_tf, p.tf);
    min_dl = std::min(min_dl, p.dl);
  }
  if (min_dl == std::numeric_limits<uint32_t>::max()) min_dl = 0;
  *payload = std::move(w.data());
  meta->last_doc = block.back().doc;
  meta->max_tf = max_tf;
  meta->min_dl = min_dl;
  meta->payload_offset = payload_offset;
  meta->payload_size = static_cast<uint32_t>(payload->size());
  meta->reserved = 0;
  return Status::OK();
}

Status EncodeInvertBlock(const std::vector<DocId>& docs, std::string* payload,
                         InvertBlockMeta* meta, uint64_t payload_offset) {
  if (docs.empty()) return Status::Error("empty invert block");
  BufWriter w;
  w.Varint(docs.size());
  uint64_t prev = 0;
  for (DocId id : docs) {
    w.Varint(id - prev);
    prev = id;
  }
  *payload = std::move(w.data());
  meta->last_doc = docs.back();
  meta->payload_offset = payload_offset;
  meta->payload_size = static_cast<uint32_t>(payload->size());
  meta->reserved = 0;
  return Status::OK();
}

class ValueCursor {
 public:
  ValueCursor(const SegmentPtr& segment, const InvertValueRef* value)
      : segment_(segment), value_(value) {
    if (value_->block_count > 0) LoadBlock(0);
  }
  bool valid() const { return valid_; }
  DocId doc() const { return doc_; }
  void Next() {
    if (!valid_) return;
    ++pos_;
    if (pos_ < docs_.size()) {
      doc_ = docs_[pos_];
      return;
    }
    if (!LoadBlock(block_ + 1)) valid_ = false;
  }
  void Advance(DocId target) {
    if (!valid_ || target <= doc_) return;
    while (valid_) {
      if (target <= docs_.back()) {
        auto it = std::lower_bound(
            docs_.begin() + static_cast<std::ptrdiff_t>(pos_), docs_.end(),
            target);
        pos_ = static_cast<size_t>(it - docs_.begin());
        if (pos_ < docs_.size()) {
          doc_ = docs_[pos_];
          return;
        }
      }
      if (!LoadBlock(block_ + 1)) {
        valid_ = false;
        return;
      }
    }
  }

 private:
  bool LoadBlock(uint32_t block) {
    if (block >= value_->block_count) return false;
    const InvertBlockMeta meta = segment_->invert_meta_at(
        value_->meta_base + static_cast<uint64_t>(block) * 24);
    const uint64_t begin =
        value_->payload_base + static_cast<uint64_t>(meta.payload_offset);
    if (begin + meta.payload_size > segment_->size()) return false;
    BufReader r(segment_->data() + begin, meta.payload_size);
    const uint64_t count = r.Varint();
    if (!r.ok() || count == 0 || count > 4096) return false;
    docs_.clear();
    docs_.reserve(count);
    uint64_t prev = 0;
    for (uint64_t i = 0; i < count; ++i) {
      prev += r.Varint();
      docs_.push_back(prev);
    }
    if (!r.ok()) return false;
    block_ = block;
    pos_ = 0;
    doc_ = docs_[0];
    valid_ = true;
    return true;
  }

  SegmentPtr segment_;
  const InvertValueRef* value_;
  bool valid_{false};
  uint32_t block_{0};
  size_t pos_{0};
  DocId doc_{0};
  std::vector<DocId> docs_;
};

struct FtsDictEntry {
  // Borrows from an input Segment, which remains pinned for this merge.
  std::string_view term;
  uint64_t df{0};
  uint32_t blocks{0};
  uint64_t payload_offset{0};  // relative to fts section
  uint64_t meta_offset{0};     // relative to fts section
};

struct InvertValueDict {
  // Borrows from an input Segment, which remains pinned for this merge.
  std::string_view value;
  uint64_t df{0};
  uint32_t blocks{0};
  uint64_t payload_rel{0};  // relative to field start
  uint64_t meta_rel{0};     // relative to field start
};

struct InvertFieldDir {
  // Borrows from the stable field_names set in MergeSegmentsStreaming.
  std::string_view name;
  uint64_t field_offset{0};  // relative to invert section
  uint64_t field_size{0};
  uint64_t dict_offset{0};   // relative to invert section
  uint64_t dict_size{0};
  uint64_t value_count{0};
  uint64_t block_count{0};
};

}  // namespace

Status MergeSegmentsStreaming(const std::string& path,
                              const std::vector<SegmentPtr>& inputs,
                              const std::unordered_set<DocId>& tombstones,
                              const StreamMergeOptions& options,
                              StreamMergeStats* stats, std::string* error) {
  if (inputs.empty()) {
    if (error) *error = "no inputs";
    return Status::Error("no inputs");
  }
  if (!stats) return Status::Error("null stats");
  const uint32_t bs = options.block_size == 0 ? 128 : options.block_size;
  MemoryTracker mem(options.memory_limit_bytes);
  *stats = StreamMergeStats{};
  // One output buffer plus per-term active buffers. The dictionary is charged
  // as it grows.
  constexpr uint64_t kIoBufferCharge = 4ull << 20;
  if (!mem.Add(kIoBufferCharge)) {
    if (error) *error = "compaction IO buffer budget exceeded";
    return Status::Error("compaction memory budget exceeded");
  }

  // Cheap live-doc pre-count: no postings are read, so this is not a second
  // postings pass. It lets us skip the output entirely for an all-deleted run.
  uint64_t live_docs = 0;
  for (const auto& segment : inputs) {
    for (const auto& doc : segment->docs()) {
      if (tombstones.count(doc.id) == 0) ++live_docs;
    }
  }
  if (live_docs == 0) {
    stats->output_docs = 0;
    return Status::OK();
  }

  const std::string final_tmp = path + ".tmp." + std::to_string(::getpid());
  TempPathGuard tmp_guard(final_tmp);
  FileWriter out;
  Status s = out.Open(final_tmp);
  if (!s.ok) return s;
  std::string zeros(kHeaderSize, '\0');
  s = out.Write(zeros);
  if (!s.ok) return s;
  uint64_t hash = Fnv1aInit();

  auto write_hashed = [&](const void* data, size_t size) -> Status {
    Status st = out.Write(data, size);
    if (st.ok) Fnv1aUpdate(&hash, data, size);
    return st;
  };
  auto write_hashed_str = [&](const std::string& data) -> Status {
    return write_hashed(data.data(), data.size());
  };

  // ---------------- FTS section (first, so docs can use final term ids) ---
  const uint64_t fts_off = out.size();
  {
    BufWriter fh;
    fh.String(options.fts_field);
    s = write_hashed_str(fh.data());
    if (!s.ok) return s;
  }

  // Keys borrow from immutable input Segment term strings; inputs outlive this map.
  std::unordered_map<std::string_view, uint32_t> term_to_id;
  std::vector<FtsDictEntry> fts_dict;
  std::vector<size_t> term_pos(inputs.size(), 0);
  uint64_t fts_block_cursor = 0;

  while (true) {
    const std::string* min_term = nullptr;
    for (size_t i = 0; i < inputs.size(); ++i) {
      const auto& terms = inputs[i]->terms();
      if (term_pos[i] >= terms.size()) continue;
      const std::string& t = terms[term_pos[i]].term;
      if (!min_term || t < *min_term) min_term = &t;
    }
    if (!min_term) break;

    std::vector<size_t> group;
    for (size_t i = 0; i < inputs.size(); ++i) {
      const auto& terms = inputs[i]->terms();
      if (term_pos[i] < terms.size() && terms[term_pos[i]].term == *min_term) {
        group.push_back(i);
      }
    }

    const uint64_t active =
        EstimateActiveTermBytes(group.size(), min_term->size());
    if (!mem.Add(active)) {
      if (error) *error = "compaction memory budget exceeded (fts)";
      return Status::Error("compaction memory budget exceeded");
    }

    std::vector<std::unique_ptr<SegmentTermIterator>> its;
    its.reserve(group.size());
    for (size_t input_idx : group) {
      const auto& terms = inputs[input_idx]->terms();
      its.emplace_back(std::make_unique<SegmentTermIterator>(
          inputs[input_idx], &terms[term_pos[input_idx]], true));
    }

    const uint64_t payload_start = out.size() - fts_off;
    std::vector<StreamPosting> block;
    block.reserve(bs);
    std::vector<uint32_t> block_positions;
    std::vector<FtsBlockMeta> metas;
    uint64_t df = 0;
    uint64_t local_payload = 0;

    auto flush_block = [&]() -> Status {
      if (block.empty()) return Status::OK();
      std::string payload;
      FtsBlockMeta meta;
      Status st = EncodeFtsBlock(block, block_positions, &payload, &meta,
                                 local_payload);
      if (!st.ok) return st;
      st = write_hashed_str(payload);
      if (!st.ok) return st;
      local_payload += payload.size();
      metas.push_back(meta);
      if (!mem.Add(sizeof(FtsBlockMeta))) {
        if (error) *error = "compaction memory budget exceeded (fts metas)";
        return Status::Error("compaction memory budget exceeded");
      }
      block.clear();
      block_positions.clear();
      ++fts_block_cursor;
      return Status::OK();
    };

    while (true) {
      DocId min_doc = kNoDoc;
      for (auto& it : its) {
        if (it->valid()) min_doc = std::min(min_doc, it->doc());
      }
      if (min_doc == kNoDoc) break;
      bool emitted = false;
      for (auto& it : its) {
        if (!it->valid() || it->doc() != min_doc) continue;
        if (tombstones.count(min_doc) == 0 && !emitted) {
          StreamPosting p;
          p.doc = min_doc;
          p.tf = it->tf();
          p.dl = it->dl();
          p.positions_offset = block_positions.size();
          const auto& source_positions = it->positions();
          p.positions_count = source_positions.size();
          block_positions.insert(block_positions.end(), source_positions.begin(),
                                 source_positions.end());
          block.push_back(p);
          ++df;
          emitted = true;
          if (block.size() >= bs) {
            Status st = flush_block();
            if (!st.ok) return st;
          }
        }
        it->Next();
      }
    }
    Status st = flush_block();
    if (!st.ok) return st;
    if (df > 0) {
      const uint64_t meta_start = out.size() - fts_off;
      for (const auto& meta : metas) {
        st = write_hashed(&meta, sizeof(meta));
        if (!st.ok) return st;
      }
      FtsDictEntry entry;
      entry.term = std::string_view(*min_term);
      entry.df = df;
      entry.blocks = static_cast<uint32_t>(metas.size());
      entry.payload_offset = payload_start;
      entry.meta_offset = meta_start;
      fts_dict.push_back(std::move(entry));
      term_to_id.emplace(std::string_view(*min_term),
                         static_cast<uint32_t>(fts_dict.size() - 1));
      const uint64_t dict_bytes = EstimateDictEntryBytes(*min_term);
      if (!mem.Add(dict_bytes)) {
        if (error) *error = "compaction dictionary budget exceeded (fts)";
        return Status::Error("compaction memory budget exceeded");
      }
    }
    mem.Sub(static_cast<uint64_t>(metas.size()) * sizeof(FtsBlockMeta));
    mem.Sub(active);
    for (size_t input_idx : group) ++term_pos[input_idx];
  }

  const uint64_t fts_dict_offset = out.size() - fts_off;
  {
    BufWriter dict;
    dict.Varint(fts_dict.size());
    for (const auto& e : fts_dict) {
      dict.String(e.term);
      dict.Varint(e.df);
      dict.Varint(e.blocks);
      dict.Varint(e.payload_offset);
      dict.Varint(e.meta_offset);
    }
    s = write_hashed_str(dict.data());
    if (!s.ok) return s;
  }
  const uint64_t fts_dict_size = out.size() - fts_off - fts_dict_offset;
  FtsFooterV2 fts_footer{kFtsFooterMagic, kVersionV2, fts_dict_offset,
                        fts_dict_size, static_cast<uint32_t>(fts_dict.size()),
                        static_cast<uint32_t>(fts_block_cursor)};
  s = write_hashed(&fts_footer, sizeof(fts_footer));
  if (!s.ok) return s;
  const uint64_t fts_size = out.size() - fts_off;

  // -------------------------------- Docs ---------------------------------
  const uint64_t docs_off = out.size();
  {
    BufWriter count_w;
    count_w.Varint(live_docs);
    s = write_hashed_str(count_w.data());
    if (!s.ok) return s;
  }
  uint64_t prev_doc_id = 0;
  uint64_t doc_count = 0;
  uint64_t total_tokens = 0;
  uint64_t first_doc = 0;
  uint64_t last_doc = 0;
  for (const auto& segment : inputs) {
    for (const auto& doc : segment->docs()) {
      if (tombstones.count(doc.id) != 0) continue;
      BufWriter record;
      record.Varint(doc.id - prev_doc_id);
      prev_doc_id = doc.id;
      record.Varint(doc.dl);
      record.String(doc.pk);
      std::vector<uint32_t> new_ids;
      new_ids.reserve(doc.term_ids.size());
      for (uint32_t source_tid : doc.term_ids) {
        const std::string& term = segment->terms()[source_tid].term;
        const auto it = term_to_id.find(std::string_view(term));
        if (it != term_to_id.end()) new_ids.push_back(it->second);
      }
      std::sort(new_ids.begin(), new_ids.end());
      new_ids.erase(std::unique(new_ids.begin(), new_ids.end()), new_ids.end());
      record.Varint(new_ids.size());
      uint32_t prev_tid = 0;
      for (uint32_t tid : new_ids) {
        record.Varint(tid - prev_tid);
        prev_tid = tid;
      }
      s = write_hashed_str(record.data());
      if (!s.ok) return s;
      if (doc_count == 0) first_doc = doc.id;
      last_doc = doc.id;
      ++doc_count;
      total_tokens += doc.dl;
    }
  }
  const uint64_t docs_size = out.size() - docs_off;
  if (doc_count != live_docs) {
    if (error) *error = "docs count mismatch";
    return Status::Error("docs count mismatch");
  }
  // Release the FTS mapping before the INVERT pass; peak is already recorded.
  const uint64_t fts_term_count = fts_dict.size();
  term_to_id.clear();
  fts_dict.clear();

  // ------------------------------- INVERT --------------------------------
  std::set<std::string> field_names;
  for (const auto& segment : inputs) {
    for (const auto& field : segment->fields()) field_names.insert(field.name);
  }
  const uint64_t invert_off = out.size();
  std::vector<InvertFieldDir> field_dirs;

  for (const auto& field_name : field_names) {
    const uint64_t field_offset = out.size() - invert_off;
    std::vector<const InvertFieldRef*> fields(inputs.size(), nullptr);
    std::vector<size_t> value_pos(inputs.size(), 0);
    for (size_t i = 0; i < inputs.size(); ++i) {
      fields[i] = inputs[i]->FindField(field_name);
    }
    std::vector<InvertValueDict> value_dict;
    uint64_t field_block_count = 0;

    while (true) {
      const std::string* min_value = nullptr;
      for (size_t i = 0; i < inputs.size(); ++i) {
        if (!fields[i] || value_pos[i] >= fields[i]->values.size()) continue;
        const std::string& v = fields[i]->values[value_pos[i]].value;
        if (!min_value || v < *min_value) min_value = &v;
      }
      if (!min_value) break;
      std::vector<size_t> group;
      for (size_t i = 0; i < inputs.size(); ++i) {
        if (!fields[i] || value_pos[i] >= fields[i]->values.size()) continue;
        if (fields[i]->values[value_pos[i]].value == *min_value) {
          group.push_back(i);
        }
      }
      const uint64_t active = EstimateActiveValueBytes(group.size());
      if (!mem.Add(active)) {
        if (error) *error = "compaction memory budget exceeded (invert)";
        return Status::Error("compaction memory budget exceeded");
      }
      std::vector<std::unique_ptr<ValueCursor>> cursors;
      cursors.reserve(group.size());
      for (size_t input_idx : group) {
        const auto* field = fields[input_idx];
        cursors.emplace_back(std::make_unique<ValueCursor>(
            inputs[input_idx], &field->values[value_pos[input_idx]]));
      }
      const uint64_t payload_rel = out.size() - invert_off - field_offset;
      std::vector<DocId> block;
      block.reserve(bs);
      std::vector<InvertBlockMeta> metas;
      uint64_t df = 0;
      uint64_t local_payload = 0;
      auto flush_value_block = [&]() -> Status {
        if (block.empty()) return Status::OK();
        std::string payload;
        InvertBlockMeta meta;
        Status st = EncodeInvertBlock(block, &payload, &meta, local_payload);
        if (!st.ok) return st;
        st = write_hashed_str(payload);
        if (!st.ok) return st;
        local_payload += payload.size();
        metas.push_back(meta);
        if (!mem.Add(sizeof(InvertBlockMeta))) {
          if (error) *error = "compaction memory budget exceeded (invert metas)";
          return Status::Error("compaction memory budget exceeded");
        }
        block.clear();
        return Status::OK();
      };
      while (true) {
        DocId min_doc = kNoDoc;
        for (auto& c : cursors) {
          if (c->valid()) min_doc = std::min(min_doc, c->doc());
        }
        if (min_doc == kNoDoc) break;
        bool emitted = false;
        for (auto& c : cursors) {
          if (!c->valid() || c->doc() != min_doc) continue;
          if (tombstones.count(min_doc) == 0 && !emitted) {
            block.push_back(min_doc);
            ++df;
            emitted = true;
            if (block.size() >= bs) {
              Status st = flush_value_block();
              if (!st.ok) return st;
            }
          }
          c->Next();
        }
      }
      Status st = flush_value_block();
      if (!st.ok) return st;
      if (df > 0) {
        const uint64_t meta_rel = out.size() - invert_off - field_offset;
        for (const auto& meta : metas) {
          st = write_hashed(&meta, sizeof(meta));
          if (!st.ok) return st;
        }
        InvertValueDict entry;
        entry.value = std::string_view(*min_value);
        entry.df = df;
        entry.blocks = static_cast<uint32_t>(metas.size());
        entry.payload_rel = payload_rel;
        entry.meta_rel = meta_rel;
        value_dict.push_back(std::move(entry));
        field_block_count += metas.size();
        const uint64_t dict_bytes = EstimateDictEntryBytes(*min_value);
        if (!mem.Add(dict_bytes)) {
          if (error) *error = "compaction dictionary budget exceeded (invert)";
          return Status::Error("compaction memory budget exceeded");
        }
      }
      mem.Sub(static_cast<uint64_t>(metas.size()) * sizeof(InvertBlockMeta));
      mem.Sub(active);
      for (size_t input_idx : group) ++value_pos[input_idx];
    }

    const uint64_t dict_offset = out.size() - invert_off;
    {
      BufWriter dict;
      dict.Varint(value_dict.size());
      for (const auto& e : value_dict) {
        dict.String(e.value);
        dict.Varint(e.df);
        dict.Varint(e.blocks);
        dict.Varint(e.payload_rel);
        dict.Varint(e.meta_rel);
      }
      s = write_hashed_str(dict.data());
      if (!s.ok) return s;
    }
    InvertFieldDir dir;
    dir.name = field_name;
    dir.field_offset = field_offset;
    dir.field_size = out.size() - invert_off - field_offset;
    dir.dict_offset = dict_offset;
    dir.dict_size = out.size() - invert_off - dict_offset;
    dir.value_count = value_dict.size();
    dir.block_count = field_block_count;
    field_dirs.push_back(std::move(dir));
  }

  const uint64_t dir_offset = out.size() - invert_off;
  {
    BufWriter dir;
    dir.Varint(field_dirs.size());
    for (const auto& f : field_dirs) {
      dir.String(f.name);
      dir.Varint(f.field_offset);
      dir.Varint(f.field_size);
      dir.Varint(f.dict_offset);
      dir.Varint(f.dict_size);
      dir.Varint(f.value_count);
      dir.Varint(f.block_count);
    }
    s = write_hashed_str(dir.data());
    if (!s.ok) return s;
  }
  const uint64_t dir_size = out.size() - invert_off - dir_offset;
  InvertFooterV2 inv_footer{kInvertFooterMagic, kVersionV2, dir_offset,
                           dir_size, static_cast<uint32_t>(field_dirs.size()),
                           0};
  s = write_hashed(&inv_footer, sizeof(inv_footer));
  if (!s.ok) return s;
  const uint64_t invert_size = out.size() - invert_off;

  stats->output_docs = doc_count;
  stats->total_tokens = total_tokens;
  stats->fts_terms = fts_term_count;
  stats->fts_blocks = fts_block_cursor;
  stats->temporary_bytes_written = 0;
  stats->tracked_peak_bytes = mem.peak();
  stats->dictionary_bytes =
      mem.current() > kIoBufferCharge ? mem.current() - kIoBufferCharge : 0;

  s = out.Flush();
  if (!s.ok) return s;
  std::string header(kHeaderSize, '\0');
  PutU32(&header, 0, kMagic);
  PutU32(&header, 4, kVersionV2);
  PutU32(&header, 8, bs);
  PutU32(&header, 12, 0);
  PutU64(&header, 16, options.run_id);
  PutU64(&header, 24, options.level);
  PutU64(&header, 32, first_doc);
  PutU64(&header, 40, last_doc);
  PutU64(&header, 48, doc_count);
  PutU64(&header, 56, total_tokens);
  PutU64(&header, 64, docs_off);
  PutU64(&header, 72, docs_size);
  PutU64(&header, 80, fts_off);
  PutU64(&header, 88, fts_size);
  PutU64(&header, 96, invert_off);
  PutU64(&header, 104, invert_size);
  PutU64(&header, 112, hash);
  s = WriteAllAt(out.fd(), header.data(), header.size(), 0);
  if (!s.ok) return s;
  s = out.FsyncClose();
  if (!s.ok) return s;
  if (::rename(final_tmp.c_str(), path.c_str()) != 0) {
    return Status::Error("rename streamed segment: " +
                         std::string(std::strerror(errno)));
  }
  tmp_guard.Release();
  const char* fault = std::getenv("M4_FAULT");
  if (fault && std::string(fault) == "after_rename") {
    return Status::Error("injected fault after rename");
  }
  return Status::OK();
}

}  // namespace m4
