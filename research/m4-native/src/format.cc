// M4 segment reader/writer.

#include "format.h"

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>

#include "util.h"

namespace m4 {
namespace {

constexpr uint32_t kMagic = 0x4D345347;  // "M4SG"
constexpr uint32_t kVersion = 1;
constexpr uint32_t kVersionV2 = 2;
constexpr size_t kHeaderSize = 120;

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

constexpr uint32_t kFtsFooterMagic = 0x46325452;   // "F2TR"
constexpr uint32_t kInvertFooterMagic = 0x49325452;  // "I2TR"

void PutU32At(std::string* s, size_t off, uint32_t v) {
  std::memcpy(s->data() + off, &v, 4);
}
void PutU64At(std::string* s, size_t off, uint64_t v) {
  std::memcpy(s->data() + off, &v, 8);
}

uint32_t ReadU32At(const char* s, size_t off) {
  uint32_t v = 0;
  std::memcpy(&v, s + off, 4);
  return v;
}
uint64_t ReadU64At(const char* s, size_t off) {
  uint64_t v = 0;
  std::memcpy(&v, s + off, 8);
  return v;
}

Status WriteAll(int fd, const char* data, size_t size) {
  size_t done = 0;
  while (done < size) {
    const ssize_t n = ::write(fd, data + done, size - done);
    if (n < 0) {
      if (errno == EINTR) continue;
      return Status::Error(std::string("write failed: ") + std::strerror(errno));
    }
    done += static_cast<size_t>(n);
  }
  return Status::OK();
}

Status SyncDir(const std::string& path) {
  const size_t slash = path.find_last_of('/');
  const std::string dir = slash == std::string::npos ? "." : path.substr(0, slash);
  const int dfd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY);
  if (dfd < 0) {
    return Status::Error("open directory for fsync " + dir + ": " +
                         std::strerror(errno));
  }
  const char* fault = std::getenv("M4_FAULT");
  int result = 0;
  if (fault && std::strcmp(fault, "segment_directory_fsync") == 0) {
    errno = EIO;
    result = -1;
  } else {
    do {
      result = ::fsync(dfd);
    } while (result != 0 && errno == EINTR);
  }
  const int saved_errno = errno;
  ::close(dfd);
  if (result != 0) {
    return Status::Error("fsync directory " + dir + ": " +
                         std::strerror(saved_errno));
  }
  return Status::OK();
}

Status AtomicWriteFile(const std::string& path, const std::string& data) {
  const std::string tmp = path + ".tmp." + std::to_string(::getpid());
  const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) {
    return Status::Error("open " + tmp + ": " + std::strerror(errno));
  }
  Status s = WriteAll(fd, data.data(), data.size());
  if (s.ok) {
    const char* fault = std::getenv("M4_FAULT");
    int sync_result = 0;
    if (fault && std::strcmp(fault, "segment_file_fsync") == 0) {
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
    return Status::Error("rename " + tmp + ": " + err);
  }
  return SyncDir(path);
}

struct FtsBuildResult {
  std::string bytes;
  uint32_t block_count{0};
};

FtsBuildResult BuildFtsSection(const SegmentBuild& build) {
  const uint32_t bs = build.block_size == 0 ? 128 : build.block_size;

  struct TermDict {
    uint32_t first_block{0};
    uint32_t block_count{0};
    uint64_t df{0};
  };
  std::vector<TermDict> dict(build.terms.size());
  std::vector<std::string> meta_bytes;
  std::string payload;
  uint32_t block_cursor = 0;

  std::vector<uint32_t> doc_dl;
  doc_dl.reserve(build.docs.size());
  std::unordered_map<DocId, uint32_t> dl_by_doc;
  dl_by_doc.reserve(build.docs.size() * 2);
  for (const auto& d : build.docs) dl_by_doc[d.id] = d.dl;

  for (size_t t = 0; t < build.terms.size(); ++t) {
    const auto& postings = build.postings[t];
    dict[t].first_block = block_cursor;
    dict[t].block_count =
        static_cast<uint32_t>((postings.size() + bs - 1) / bs);
    dict[t].df = postings.size();

    for (size_t start = 0; start < postings.size(); start += bs) {
      const size_t end = std::min(postings.size(), start + bs);
      uint32_t max_tf = 0;
      uint32_t min_dl = std::numeric_limits<uint32_t>::max();
      for (size_t i = start; i < end; ++i) {
        max_tf = std::max(max_tf, postings[i].tf);
        min_dl = std::min(min_dl, postings[i].dl);
      }
      if (min_dl == std::numeric_limits<uint32_t>::max()) min_dl = 0;

      BufWriter payload_writer;
      payload_writer.Varint(end - start);
      uint64_t prev_doc = 0;
      for (size_t i = start; i < end; ++i) {
        const auto& p = postings[i];
        payload_writer.Varint(p.doc - prev_doc);
        prev_doc = p.doc;
        payload_writer.Varint(p.tf);
        payload_writer.Varint(p.dl);
        payload_writer.Varint(p.positions.size());
        uint32_t prev_pos = 0;
        for (uint32_t pos : p.positions) {
          payload_writer.Varint(pos - prev_pos);
          prev_pos = pos;
        }
      }

      FtsBlockMeta meta;
      meta.last_doc = postings[end - 1].doc;
      meta.max_tf = max_tf;
      meta.min_dl = min_dl;
      meta.payload_offset = payload.size();
      meta.payload_size = static_cast<uint32_t>(payload_writer.size());
      meta.reserved = 0;
      meta_bytes.emplace_back(reinterpret_cast<const char*>(&meta),
                              sizeof(meta));
      payload += payload_writer.data();
      ++block_cursor;
    }
  }

  BufWriter head;
  head.String(build.terms.empty() ? std::string("body") : std::string("body"));
  head.Varint(build.terms.size());
  head.Varint(block_cursor);
  for (size_t t = 0; t < build.terms.size(); ++t) {
    head.String(build.terms[t]);
    head.Varint(dict[t].df);
    head.Varint(dict[t].first_block);
    head.Varint(dict[t].block_count);
  }

  std::string out = head.data();
  for (const auto& m : meta_bytes) out += m;
  out += payload;

  FtsBuildResult r;
  r.bytes = std::move(out);
  r.block_count = block_cursor;
  return r;
}

std::string BuildInvertField(const BuildField& field) {
  BufWriter head;
  head.String(field.name);
  head.Varint(field.values.size());

  uint32_t total_blocks = 0;
  struct ValueDict {
    uint64_t df{0};
    uint32_t first_block{0};
    uint32_t block_count{0};
  };
  std::vector<ValueDict> dict(field.values.size());
  std::vector<std::string> metas;
  std::string payload;
  constexpr uint32_t kDocsPerBlock = 128;

  for (size_t v = 0; v < field.values.size(); ++v) {
    const auto& val = field.values[v];
    dict[v].first_block = total_blocks;
    dict[v].df = val.docs.size();
    dict[v].block_count =
        static_cast<uint32_t>((val.docs.size() + kDocsPerBlock - 1) /
                              kDocsPerBlock);
    for (size_t start = 0; start < val.docs.size(); start += kDocsPerBlock) {
      const size_t end = std::min(val.docs.size(), start + kDocsPerBlock);
      BufWriter w;
      w.Varint(end - start);
      uint64_t prev = 0;
      for (size_t i = start; i < end; ++i) {
        w.Varint(val.docs[i] - prev);
        prev = val.docs[i];
      }
      InvertBlockMeta meta;
      meta.last_doc = val.docs[end - 1];
      meta.payload_offset = payload.size();
      meta.payload_size = static_cast<uint32_t>(w.size());
      meta.reserved = 0;
      metas.emplace_back(reinterpret_cast<const char*>(&meta), sizeof(meta));
      payload += w.data();
      ++total_blocks;
    }
  }
  head.Varint(total_blocks);
  for (size_t v = 0; v < field.values.size(); ++v) {
    head.String(field.values[v].value);
    head.Varint(dict[v].df);
    head.Varint(dict[v].first_block);
    head.Varint(dict[v].block_count);
  }
  std::string out = head.data();
  for (const auto& m : metas) out += m;
  out += payload;
  return out;
}

std::string BuildInvertSection(const SegmentBuild& build) {
  BufWriter head;
  head.Varint(build.fields.size());
  std::string out = head.data();
  for (const auto& field : build.fields) out += BuildInvertField(field);
  return out;
}

std::string BuildDocsSection(const SegmentBuild& build) {
  BufWriter w;
  w.Varint(build.docs.size());
  uint64_t prev_id = 0;
  for (const auto& d : build.docs) {
    w.Varint(d.id - prev_id);
    prev_id = d.id;
    w.Varint(d.dl);
    w.String(d.pk);
    w.Varint(d.term_ids.size());
    uint32_t prev_tid = 0;
    for (uint32_t tid : d.term_ids) {
      w.Varint(tid - prev_tid);
      prev_tid = tid;
    }
  }
  return w.data();
}

}  // namespace

Status WriteSegment(const std::string& path, SegmentBuild build,
                    std::string* error) {
  if (build.docs.empty()) {
    if (error) *error = "empty segment";
    return Status::Error("empty segment");
  }
  std::sort(build.docs.begin(), build.docs.end(),
            [](const BuildDoc& a, const BuildDoc& b) { return a.id < b.id; });
  std::sort(build.fields.begin(), build.fields.end(),
            [](const BuildField& a, const BuildField& b) {
              return a.name < b.name;
            });
  for (auto& field : build.fields) {
    std::sort(field.values.begin(), field.values.end(),
              [](const BuildValue& a, const BuildValue& b) {
                return a.value < b.value;
              });
    for (auto& value : field.values) {
      std::sort(value.docs.begin(), value.docs.end());
    }
  }

  const std::string docs = BuildDocsSection(build);
  const FtsBuildResult fts = BuildFtsSection(build);
  const std::string invert = BuildInvertSection(build);
  (void)fts.block_count;

  std::string file(kHeaderSize, '\0');
  const uint64_t docs_off = file.size();
  file += docs;
  const uint64_t fts_off = file.size();
  file += fts.bytes;
  const uint64_t invert_off = file.size();
  file += invert;
  const uint64_t checksum = Fnv1a(file.data() + kHeaderSize,
                                  file.size() - kHeaderSize);

  PutU32At(&file, 0, kMagic);
  PutU32At(&file, 4, kVersion);
  PutU32At(&file, 8, build.block_size);
  PutU32At(&file, 12, 0);
  PutU64At(&file, 16, build.run_id);
  PutU64At(&file, 24, build.level);
  PutU64At(&file, 32, build.docs.front().id);
  PutU64At(&file, 40, build.docs.back().id);
  PutU64At(&file, 48, build.docs.size());
  PutU64At(&file, 56, build.total_tokens);
  PutU64At(&file, 64, docs_off);
  PutU64At(&file, 72, docs.size());
  PutU64At(&file, 80, fts_off);
  PutU64At(&file, 88, fts.bytes.size());
  PutU64At(&file, 96, invert_off);
  PutU64At(&file, 104, invert.size());
  PutU64At(&file, 112, checksum);

  const Status s = AtomicWriteFile(path, file);
  if (!s.ok && error) *error = s.message;
  return s;
}

Segment::~Segment() {
  if (map_ && mapped_size_ > 0) {
    ::munmap(const_cast<char*>(map_), mapped_size_);
  }
}

Status Segment::Open(const std::string& path, SegmentPtr* out,
                     std::string* error) {
  const int fd = ::open(path.c_str(), O_RDONLY);
  if (fd < 0) {
    if (error) *error = std::strerror(errno);
    return Status::Error("open segment: " + path);
  }
  struct stat st {};
  if (::fstat(fd, &st) != 0 || st.st_size < static_cast<off_t>(kHeaderSize)) {
    ::close(fd);
    if (error) *error = "segment too small";
    return Status::Error("segment too small");
  }
  const size_t size = static_cast<size_t>(st.st_size);
  void* map = ::mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
  ::close(fd);
  if (map == MAP_FAILED) {
    if (error) *error = std::strerror(errno);
    return Status::Error("mmap segment");
  }

  SegmentPtr seg(new Segment());
  seg->path_ = path;
  seg->map_ = static_cast<const char*>(map);
  seg->mapped_size_ = size;
  seg->file_size_ = size;

  const char* p = seg->map_;
  if (ReadU32At(p, 0) != kMagic) {
    if (error) *error = "bad segment magic";
    return Status::Error("bad segment magic");
  }
  const uint32_t version = ReadU32At(p, 4);
  if (version != kVersion && version != kVersionV2) {
    if (error) *error = "bad segment version";
    return Status::Error("bad segment version");
  }
  seg->block_size_ = ReadU32At(p, 8);
  if (seg->block_size_ == 0 || seg->block_size_ > 4096) {
    if (error) *error = "bad block size";
    return Status::Error("bad block size");
  }
  seg->run_id_ = ReadU64At(p, 16);
  seg->level_ = ReadU64At(p, 24);
  seg->first_doc_id_ = ReadU64At(p, 32);
  seg->last_doc_id_ = ReadU64At(p, 40);
  seg->total_tokens_ = ReadU64At(p, 56);
  const uint64_t docs_off = ReadU64At(p, 64);
  const uint64_t docs_size = ReadU64At(p, 72);
  const uint64_t fts_off = ReadU64At(p, 80);
  const uint64_t fts_size = ReadU64At(p, 88);
  const uint64_t invert_off = ReadU64At(p, 96);
  const uint64_t invert_size = ReadU64At(p, 104);
  const uint64_t want_checksum = ReadU64At(p, 112);
  if (docs_off < kHeaderSize || fts_off < kHeaderSize ||
      invert_off < kHeaderSize || docs_off + docs_size > size ||
      fts_off + fts_size > size || invert_off + invert_size > size) {
    if (error) *error = "segment section bounds invalid";
    return Status::Error("segment section bounds invalid");
  }
  const uint64_t data_start =
      std::min(docs_off, std::min(fts_off, invert_off));
  const uint64_t got_checksum = Fnv1a(p + data_start, size - data_start);
  if (got_checksum != want_checksum) {
    if (error) *error = "segment checksum mismatch";
    return Status::Error("segment checksum mismatch");
  }

  // Docs.
  {
    BufReader r(p + docs_off, docs_size);
    const uint64_t count = r.Varint();
    if (!r.ok() || count > 100000000ull) {
      if (error) *error = "invalid docs section";
      return Status::Error("invalid docs section");
    }
    seg->docs_.reserve(count);
    uint64_t prev = 0;
    for (uint64_t i = 0; i < count; ++i) {
      DocMeta d;
      d.id = prev + r.Varint();
      prev = d.id;
      d.dl = static_cast<uint32_t>(r.Varint());
      d.pk = r.String();
      const uint64_t nt = r.Varint();
      d.term_ids.reserve(nt);
      uint32_t prev_tid = 0;
      for (uint64_t k = 0; k < nt; ++k) {
        prev_tid += static_cast<uint32_t>(r.Varint());
        d.term_ids.push_back(prev_tid);
      }
      if (!r.ok()) break;
      seg->docs_.push_back(std::move(d));
    }
    if (!r.ok() || seg->docs_.size() != count) {
      if (error) *error = "truncated docs section";
      return Status::Error("truncated docs section");
    }
  }

  // FTS (single prototype field).
  if (version == kVersionV2) {
    if (fts_size < sizeof(FtsFooterV2)) {
      if (error) *error = "v2 fts too small";
      return Status::Error("v2 fts too small");
    }
    FtsFooterV2 footer;
    std::memcpy(&footer, p + fts_off + fts_size - sizeof(FtsFooterV2),
                sizeof(footer));
    if (footer.magic != kFtsFooterMagic || footer.version != kVersionV2 ||
        footer.dict_offset + footer.dict_size > fts_size ||
        footer.term_count > 50000000ull) {
      if (error) *error = "invalid v2 fts footer";
      return Status::Error("invalid v2 fts footer");
    }
    {
      BufReader fr(p + fts_off, fts_size);
      (void)fr.String();  // field name
    }
    BufReader r(p + fts_off + footer.dict_offset, footer.dict_size);
    const uint64_t term_count = r.Varint();
    if (!r.ok() || term_count != footer.term_count) {
      if (error) *error = "invalid v2 fts dictionary";
      return Status::Error("invalid v2 fts dictionary");
    }
    seg->terms_.reserve(term_count);
    seg->term_index_.reserve(term_count * 2);
    for (uint64_t i = 0; i < term_count; ++i) {
      TermRef t;
      t.term = r.String();
      t.df = r.Varint();
      t.block_count = static_cast<uint32_t>(r.Varint());
      t.payload_base = fts_off + r.Varint();
      t.meta_base = fts_off + r.Varint();
      if (!r.ok() || t.meta_base + static_cast<uint64_t>(t.block_count) * 32 >
                         fts_off + fts_size ||
          t.payload_base > fts_off + fts_size) {
        if (error) *error = "invalid v2 fts entry";
        return Status::Error("invalid v2 fts entry");
      }
      seg->term_index_[t.term] = static_cast<uint32_t>(seg->terms_.size());
      seg->terms_.push_back(std::move(t));
    }
    if (!r.ok() || seg->terms_.size() != term_count) {
      if (error) *error = "truncated v2 fts dictionary";
      return Status::Error("truncated v2 fts dictionary");
    }
  } else {
    BufReader r(p + fts_off, fts_size);
    (void)r.String();  // field name
    const uint64_t term_count = r.Varint();
    const uint64_t meta_count = r.Varint();
    if (!r.ok() || term_count > 50000000ull || meta_count > 50000000ull) {
      if (error) *error = "invalid fts section";
      return Status::Error("invalid fts section");
    }
    seg->terms_.reserve(term_count);
    seg->term_index_.reserve(term_count * 2);
    for (uint64_t i = 0; i < term_count; ++i) {
      TermRef t;
      t.term = r.String();
      t.df = r.Varint();
      t.first_block = static_cast<uint32_t>(r.Varint());
      t.block_count = static_cast<uint32_t>(r.Varint());
      if (!r.ok()) break;
      seg->term_index_[t.term] = static_cast<uint32_t>(seg->terms_.size());
      seg->terms_.push_back(std::move(t));
    }
    seg->fts_meta_base_ = fts_off + r.offset();
    seg->fts_meta_count_ = static_cast<uint32_t>(meta_count);
    seg->fts_payload_base_ = seg->fts_meta_base_ + meta_count * 32;
    for (auto& t : seg->terms_) {
      t.meta_base = seg->fts_meta_base_ + static_cast<uint64_t>(t.first_block) * 32;
      t.payload_base = seg->fts_payload_base_;
    }
    if (!r.ok() || seg->terms_.size() != term_count ||
        seg->fts_payload_base_ > fts_off + fts_size) {
      if (error) *error = "truncated fts section";
      return Status::Error("truncated fts section");
    }
  }

  // Invert fields.
  if (version == kVersionV2) {
    if (invert_size < sizeof(InvertFooterV2)) {
      if (error) *error = "v2 invert too small";
      return Status::Error("v2 invert too small");
    }
    InvertFooterV2 footer;
    std::memcpy(&footer,
                p + invert_off + invert_size - sizeof(InvertFooterV2),
                sizeof(footer));
    if (footer.magic != kInvertFooterMagic || footer.version != kVersionV2 ||
        footer.dir_offset + footer.dir_size > invert_size ||
        footer.field_count > 100000ull) {
      if (error) *error = "invalid v2 invert footer";
      return Status::Error("invalid v2 invert footer");
    }
    BufReader dr(p + invert_off + footer.dir_offset, footer.dir_size);
    const uint64_t field_count = dr.Varint();
    if (!dr.ok() || field_count != footer.field_count) {
      if (error) *error = "invalid v2 invert directory";
      return Status::Error("invalid v2 invert directory");
    }
    for (uint64_t f = 0; f < field_count; ++f) {
      InvertFieldRef field;
      field.name = dr.String();
      const uint64_t field_offset = dr.Varint();
      const uint64_t field_size = dr.Varint();
      const uint64_t dict_offset = dr.Varint();
      const uint64_t dict_size = dr.Varint();
      const uint64_t value_count = dr.Varint();
      const uint64_t block_count = dr.Varint();
      if (!dr.ok() || field_offset + field_size > invert_size ||
          dict_offset + dict_size > invert_size ||
          value_count > 50000000ull ||
          field_offset + field_size > invert_size) {
        if (error) *error = "invalid v2 invert field directory";
        return Status::Error("invalid v2 invert field directory");
      }
      BufReader vr(p + invert_off + dict_offset, dict_size);
      const uint64_t vc = vr.Varint();
      if (!vr.ok() || vc != value_count) {
        if (error) *error = "invalid v2 invert dictionary";
        return Status::Error("invalid v2 invert dictionary");
      }
      field.values.reserve(value_count);
      field.value_index.reserve(value_count * 2);
      for (uint64_t v = 0; v < value_count; ++v) {
        InvertValueRef value;
        value.value = vr.String();
        value.df = vr.Varint();
        value.block_count = static_cast<uint32_t>(vr.Varint());
        const uint64_t payload_rel = vr.Varint();
        const uint64_t meta_rel = vr.Varint();
        value.payload_base = invert_off + field_offset + payload_rel;
        value.meta_base = invert_off + field_offset + meta_rel;
        if (!vr.ok() ||
            meta_rel + static_cast<uint64_t>(value.block_count) * 24 >
                field_size ||
            payload_rel > field_size) {
          if (error) *error = "invalid v2 invert value";
          return Status::Error("invalid v2 invert value");
        }
        field.value_index[value.value] =
            static_cast<uint32_t>(field.values.size());
        field.values.push_back(std::move(value));
      }
      if (!vr.ok() || field.values.size() != value_count) {
        if (error) *error = "truncated v2 invert dictionary";
        return Status::Error("truncated v2 invert dictionary");
      }
      (void)block_count;
      field.meta_base = invert_off + field_offset;
      field.payload_base = invert_off + field_offset;
      seg->field_index_[field.name] =
          static_cast<uint32_t>(seg->fields_.size());
      seg->fields_.push_back(std::move(field));
    }
    if (!dr.ok() || seg->fields_.size() != field_count) {
      if (error) *error = "truncated v2 invert directory";
      return Status::Error("truncated v2 invert directory");
    }
  } else {
    BufReader r(p + invert_off, invert_size);
    const uint64_t field_count = r.Varint();
    if (!r.ok() || field_count > 100000ull) {
      if (error) *error = "invalid invert section";
      return Status::Error("invalid invert section");
    }
    for (uint64_t f = 0; f < field_count; ++f) {
      InvertFieldRef field;
      field.name = r.String();
      const uint64_t value_count = r.Varint();
      const uint64_t meta_count = r.Varint();
      if (!r.ok() || value_count > 50000000ull || meta_count > 50000000ull) {
        if (error) *error = "invalid invert field";
        return Status::Error("invalid invert field");
      }
      field.values.reserve(value_count);
      field.value_index.reserve(value_count * 2);
      for (uint64_t v = 0; v < value_count; ++v) {
        InvertValueRef value;
        value.value = r.String();
        value.df = r.Varint();
        value.first_block = static_cast<uint32_t>(r.Varint());
        value.block_count = static_cast<uint32_t>(r.Varint());
        if (!r.ok()) break;
        field.value_index[value.value] =
            static_cast<uint32_t>(field.values.size());
        field.values.push_back(std::move(value));
      }
      field.meta_base = invert_off + r.offset();
      field.payload_base = field.meta_base + meta_count * 24;
      for (auto& v : field.values) {
        v.meta_base =
            field.meta_base + static_cast<uint64_t>(v.first_block) * 24;
        v.payload_base = field.payload_base;
      }
      // Skip the payload by summing the meta sizes.
      for (uint64_t m = 0; m < meta_count; ++m) {
        const auto meta = seg->invert_meta(field, static_cast<uint32_t>(m));
        (void)meta;
      }
      // payload length is the max(payload_offset+size); compute conservatively
      // from the last meta.
      uint64_t payload_size = 0;
      for (uint64_t m = 0; m < meta_count; ++m) {
        const auto meta = seg->invert_meta(field, static_cast<uint32_t>(m));
        payload_size =
            std::max<uint64_t>(payload_size,
                               meta.payload_offset + meta.payload_size);
      }
      if (!r.ok() || field.values.size() != value_count) {
        if (error) *error = "truncated invert field";
        return Status::Error("truncated invert field");
      }
      r.Skip(static_cast<size_t>(meta_count) * 24 + payload_size);
      if (!r.ok()) {
        if (error) *error = "truncated invert payload";
        return Status::Error("truncated invert payload");
      }
      seg->field_index_[field.name] =
          static_cast<uint32_t>(seg->fields_.size());
      seg->fields_.push_back(std::move(field));
    }
    if (!r.ok() || seg->fields_.size() != field_count) {
      if (error) *error = "truncated invert section";
      return Status::Error("truncated invert section");
    }
  }

  *out = std::move(seg);
  return Status::OK();
}

const DocMeta* Segment::FindDoc(DocId id) const {
  auto it = std::lower_bound(
      docs_.begin(), docs_.end(), id,
      [](const DocMeta& d, DocId target) { return d.id < target; });
  if (it == docs_.end() || it->id != id) return nullptr;
  return &*it;
}

const TermRef* Segment::FindTerm(const std::string& term) const {
  const auto it = term_index_.find(term);
  if (it == term_index_.end()) return nullptr;
  return &terms_[it->second];
}

const InvertFieldRef* Segment::FindField(const std::string& field) const {
  const auto it = field_index_.find(field);
  if (it == field_index_.end()) return nullptr;
  return &fields_[it->second];
}

const InvertValueRef* Segment::FindValue(const InvertFieldRef& field,
                                         const std::string& value) const {
  const auto it = field.value_index.find(value);
  if (it == field.value_index.end()) return nullptr;
  return &field.values[it->second];
}

FtsBlockMeta Segment::fts_meta(uint32_t index) const {
  if (index >= fts_meta_count_) return {};
  FtsBlockMeta m;
  std::memcpy(&m, map_ + fts_meta_base_ + static_cast<size_t>(index) * 32,
              sizeof(m));
  return m;
}

FtsBlockMeta Segment::fts_meta_at(uint64_t absolute_offset) const {
  FtsBlockMeta m;
  if (absolute_offset + sizeof(m) > file_size_) return m;
  std::memcpy(&m, map_ + absolute_offset, sizeof(m));
  return m;
}

InvertBlockMeta Segment::invert_meta_at(uint64_t absolute_offset) const {
  InvertBlockMeta m;
  if (absolute_offset + sizeof(m) > file_size_) return m;
  std::memcpy(&m, map_ + absolute_offset, sizeof(m));
  return m;
}

InvertBlockMeta Segment::invert_meta(const InvertFieldRef& field,
                                     uint32_t index) const {
  InvertBlockMeta m;
  std::memcpy(&m, map_ + field.meta_base + static_cast<size_t>(index) * 24,
              sizeof(m));
  return m;
}

SegmentTermIterator::SegmentTermIterator(const SegmentPtr& segment,
                                         const TermRef* term,
                                         bool decode_positions)
    : segment_(segment),
      term_(term),
      decode_positions_(decode_positions) {
  if (!segment_ || !term_ || term_->block_count == 0) {
    valid_ = false;
    return;
  }
  if (!LoadBlock(0)) {
    valid_ = false;
    return;
  }
  valid_ = true;
}

uint64_t SegmentTermIterator::memory_usage_bytes() const {
  uint64_t total = 0;
  auto add_capacity = [&](size_t capacity, size_t element_size) {
    if (capacity > (std::numeric_limits<uint64_t>::max() - total) /
                       element_size) {
      total = std::numeric_limits<uint64_t>::max();
      return;
    }
    total += static_cast<uint64_t>(capacity) * element_size;
  };
  add_capacity(offsets_.capacity(), sizeof(uint32_t));
  add_capacity(doc_ids_.capacity(), sizeof(DocId));
  add_capacity(tfs_.capacity(), sizeof(uint32_t));
  add_capacity(dls_.capacity(), sizeof(uint32_t));
  add_capacity(pos_begin_.capacity(), sizeof(uint32_t));
  add_capacity(positions_.capacity(), sizeof(uint32_t));
  add_capacity(current_positions_.capacity(), sizeof(uint32_t));
  return total;
}

bool SegmentTermIterator::LoadBlock(uint32_t block) {
  if (!segment_ || !term_ || block >= term_->block_count) return false;
  const FtsBlockMeta meta =
      segment_->fts_meta_at(term_->meta_base + static_cast<uint64_t>(block) * 32);
  const char* payload = segment_->data() + term_->payload_base +
                        meta.payload_offset;
  if (term_->payload_base + meta.payload_offset + meta.payload_size >
      segment_->size()) {
    return false;
  }
  BufReader r(payload, meta.payload_size);
  const uint64_t count = r.Varint();
  if (!r.ok() || count == 0 || count > 4096) return false;

  doc_ids_.clear();
  tfs_.clear();
  dls_.clear();
  pos_begin_.clear();
  positions_.clear();
  doc_ids_.reserve(count);
  tfs_.reserve(count);
  dls_.reserve(count);
  uint64_t prev_doc = 0;
  for (uint64_t i = 0; i < count; ++i) {
    prev_doc += r.Varint();
    doc_ids_.push_back(prev_doc);
    tfs_.push_back(static_cast<uint32_t>(r.Varint()));
    dls_.push_back(static_cast<uint32_t>(r.Varint()));
    const uint64_t np = r.Varint();
    pos_begin_.push_back(static_cast<uint32_t>(positions_.size()));
    uint32_t prev = 0;
    for (uint64_t k = 0; k < np; ++k) {
      prev += static_cast<uint32_t>(r.Varint());
      if (decode_positions_) positions_.push_back(prev);
    }
  }
  if (!r.ok() || doc_ids_.size() != count) return false;

  block_ = block;
  block_last_doc_ = meta.last_doc;
  block_max_tf_ = meta.max_tf;
  block_min_dl_ = meta.min_dl;
  entry_ = 0;
  doc_ = doc_ids_[0];
  tf_ = tfs_[0];
  dl_ = dls_[0];
  UpdateCurrentPositions();
  return true;
}

void SegmentTermIterator::UpdateCurrentPositions() {
  current_positions_.clear();
  if (entry_ >= pos_begin_.size()) return;
  const size_t begin = pos_begin_[entry_];
  const size_t end = entry_ + 1 < pos_begin_.size()
                         ? pos_begin_[entry_ + 1]
                         : positions_.size();
  if (begin <= end && end <= positions_.size()) {
    current_positions_.assign(positions_.begin() +
                                  static_cast<std::ptrdiff_t>(begin),
                              positions_.begin() +
                                  static_cast<std::ptrdiff_t>(end));
  }
}

bool SegmentTermIterator::SeekInBlock(DocId target) {
  auto it = std::lower_bound(doc_ids_.begin(), doc_ids_.end(), target);
  if (it == doc_ids_.end()) return false;
  entry_ = static_cast<size_t>(it - doc_ids_.begin());
  doc_ = *it;
  tf_ = tfs_[entry_];
  dl_ = dls_[entry_];
  UpdateCurrentPositions();
  return true;
}

bool SegmentTermIterator::Next() {
  if (!valid_) return false;
  if (entry_ + 1 < doc_ids_.size()) {
    ++entry_;
    doc_ = doc_ids_[entry_];
    tf_ = tfs_[entry_];
    dl_ = dls_[entry_];
    UpdateCurrentPositions();
    return true;
  }
  const uint32_t next_block = block_ + 1;
  if (next_block >= term_->block_count) {
    valid_ = false;
    return false;
  }
  if (!LoadBlock(next_block)) {
    valid_ = false;
    return false;
  }
  return true;
}

bool SegmentTermIterator::Advance(DocId target) {
  if (!valid_) return false;
  if (target <= doc_) return true;
  if (target <= block_last_doc_) {
    if (!SeekInBlock(target)) {
      // Move to the next block (the target fell into a gap after this block).
      const uint32_t next_block = block_ + 1;
      if (next_block >= term_->block_count || !LoadBlock(next_block)) {
        valid_ = false;
        return false;
      }
      return true;
    }
    return true;
  }
  // Binary search the block index for the first block whose last_doc >= target.
  uint32_t lo = block_ + 1;
  uint32_t hi = term_->block_count;
  while (lo < hi) {
    const uint32_t mid = lo + (hi - lo) / 2;
    const FtsBlockMeta meta =
        segment_->fts_meta_at(term_->meta_base + static_cast<uint64_t>(mid) * 32);
    if (meta.last_doc < target) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (lo >= term_->block_count) {
    valid_ = false;
    return false;
  }
  if (!LoadBlock(lo)) {
    valid_ = false;
    return false;
  }
  return SeekInBlock(target);
}

float SegmentTermIterator::BlockMaxScore(double idf, double k1, double b,
                                         double avg_dl) const {
  if (!valid_ || block_max_tf_ == 0 || avg_dl <= 0.0) return 0.0f;
  const double tf = block_max_tf_;
  const double dl = block_min_dl_;
  const double denom = tf + k1 * (1.0 - b + b * dl / avg_dl);
  if (denom <= 0.0) return 0.0f;
  return static_cast<float>((idf * tf * (k1 + 1.0)) / denom);
}

}  // namespace m4
