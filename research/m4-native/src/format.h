// M4 on-disk segment format.
//
// A run is an immutable, self-describing file. The layout is deliberately not
// compatible with the production zvec FTS/INVERT format: this is a research
// format with a single shared version/tombstone model for both index kinds.
//
// Layout (little-endian, research prototype):
//   header (fixed 120 bytes)
//   docs section      : doc_id/dl/pk/unique-term-ids
//   fts section       : dictionary + per-block raw maxima + postings
//   invert section    : per-field sorted values + postings
//
// FTS block metadata intentionally stores raw max_tf/min_dl, not an
// IDF-weighted score. The query-time bound is computed from current global
// live statistics, so corpus growth can never invalidate a stored bound.

#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "../include/m4/index.h"

namespace m4 {

struct BuildPosting {
  DocId doc{0};
  uint32_t tf{0};
  uint32_t dl{0};
  std::vector<uint32_t> positions;
};

struct BuildDoc {
  DocId id{0};
  uint32_t dl{0};
  std::string pk;
  std::vector<uint32_t> term_ids;
};

struct BuildValue {
  std::string value;
  std::vector<DocId> docs;
};

struct BuildField {
  std::string name;
  std::vector<BuildValue> values;  // sorted by value
};

struct SegmentBuild {
  uint64_t run_id{0};
  uint64_t level{0};
  uint64_t total_tokens{0};
  uint32_t block_size{128};
  std::vector<std::string> terms;                    // sorted
  std::vector<std::vector<BuildPosting>> postings;   // parallel to terms
  std::vector<BuildDoc> docs;                        // sorted by id
  std::vector<BuildField> fields;                    // sorted by name
};

struct FtsBlockMeta {
  uint64_t last_doc{0};
  uint32_t max_tf{0};
  uint32_t min_dl{0};
  uint64_t payload_offset{0};
  uint32_t payload_size{0};
  uint32_t reserved{0};
};
static_assert(sizeof(FtsBlockMeta) == 32, "fixed 32-byte block meta");

struct InvertBlockMeta {
  uint64_t last_doc{0};
  uint64_t payload_offset{0};
  uint32_t payload_size{0};
  uint32_t reserved{0};
};
static_assert(sizeof(InvertBlockMeta) == 24, "fixed 24-byte block meta");

struct TermRef {
  std::string term;
  uint64_t df{0};
  uint32_t first_block{0};   // v1 meta-table index
  uint32_t block_count{0};
  // Absolute offsets in the segment file. v1 fills these from the global
  // meta/payload areas; v2 stores per-term chunk offsets.
  uint64_t meta_base{0};
  uint64_t payload_base{0};
};

struct InvertValueRef {
  std::string value;
  uint64_t df{0};
  uint32_t first_block{0};   // v1 field meta-table index
  uint32_t block_count{0};
  uint64_t meta_base{0};     // absolute offset in the segment file
  uint64_t payload_base{0};  // absolute offset in the segment file
};

struct InvertFieldRef {
  std::string name;
  uint64_t meta_base{0};     // offset inside segment file
  uint64_t payload_base{0};  // offset inside segment file
  // Persisted order is lexical by value (WriteSegment sorts before encode);
  // prefix filters may seek this vector with lower_bound.
  std::vector<InvertValueRef> values;
  std::unordered_map<std::string, uint32_t> value_index;
};

struct DocMeta {
  DocId id{0};
  uint32_t dl{0};
  std::string pk;
  std::vector<uint32_t> term_ids;
};

class Segment;
using SegmentPtr = std::shared_ptr<Segment>;

// Build + persist a run atomically (tmp file, fsync, rename).
Status WriteSegment(const std::string& path, SegmentBuild build,
                    std::string* error);

constexpr uint64_t kStreamMergeFixedIoBufferChargeBytes = 4ull << 20;

struct StreamMergeOptions {
  uint64_t run_id{0};
  uint64_t level{0};
  uint32_t block_size{128};
  std::string fts_field{"body"};
  uint64_t memory_limit_bytes{128ull << 20};
  // Caller-owned immutable snapshots already allocated before this merge.
  uint64_t external_memory_charge_bytes{0};
};

struct StreamMergeStats {
  uint64_t output_docs{0};
  uint64_t total_tokens{0};
  uint64_t fts_terms{0};
  uint64_t fts_blocks{0};
  uint64_t dictionary_bytes{0};
  uint64_t tracked_peak_bytes{0};
  // Bytes written to temporary section files (dict/meta/payload/docs) before
  // the final segment is assembled. The final segment size is accounted by
  // the caller.
  uint64_t temporary_bytes_written{0};
};

// Stream-merge inputs into one new segment. Only one decoded block per input
// per active term/value is resident, plus the new term dictionary. The
// memory budget is enforced; on failure no manifest is published and the
// caller keeps the old runs.
Status MergeSegmentsStreaming(const std::string& path,
                              const std::vector<SegmentPtr>& inputs,
                              const std::unordered_set<DocId>& tombstones,
                              const StreamMergeOptions& options,
                              StreamMergeStats* stats, std::string* error);

class Segment {
 public:
  static Status Open(const std::string& path, SegmentPtr* out,
                     std::string* error);
  ~Segment();

  uint64_t run_id() const { return run_id_; }
  uint64_t level() const { return level_; }
  uint64_t first_doc_id() const { return first_doc_id_; }
  uint64_t last_doc_id() const { return last_doc_id_; }
  uint64_t doc_count() const { return docs_.size(); }
  uint64_t total_tokens() const { return total_tokens_; }
  uint32_t block_size() const { return block_size_; }
  const std::string& path() const { return path_; }
  uint64_t file_size() const { return file_size_; }
  const std::vector<DocMeta>& docs() const { return docs_; }
  const std::vector<TermRef>& terms() const { return terms_; }
  const std::vector<InvertFieldRef>& fields() const { return fields_; }

  const DocMeta* FindDoc(DocId id) const;
  const TermRef* FindTerm(const std::string& term) const;
  const InvertFieldRef* FindField(const std::string& field) const;
  const InvertValueRef* FindValue(const InvertFieldRef& field,
                                  const std::string& value) const;

  // Accessors used by iterators. All offsets are absolute file offsets.
  const char* data() const { return map_; }
  size_t size() const { return static_cast<size_t>(file_size_); }
  uint64_t fts_meta_base() const { return fts_meta_base_; }
  uint64_t fts_payload_base() const { return fts_payload_base_; }
  FtsBlockMeta fts_meta(uint32_t index) const;
  FtsBlockMeta fts_meta_at(uint64_t absolute_offset) const;
  InvertBlockMeta invert_meta(const InvertFieldRef& field,
                              uint32_t index) const;
  InvertBlockMeta invert_meta_at(uint64_t absolute_offset) const;

 private:
  Segment() = default;

  std::string path_;
  const char* map_{nullptr};
  size_t mapped_size_{0};
  uint64_t file_size_{0};
  uint64_t run_id_{0};
  uint64_t level_{0};
  uint64_t first_doc_id_{0};
  uint64_t last_doc_id_{0};
  uint64_t total_tokens_{0};
  uint32_t block_size_{128};

  std::vector<DocMeta> docs_;
  std::vector<TermRef> terms_;
  std::unordered_map<std::string, uint32_t> term_index_;
  std::vector<InvertFieldRef> fields_;
  std::unordered_map<std::string, uint32_t> field_index_;
  uint64_t fts_meta_base_{0};
  uint64_t fts_payload_base_{0};
  uint32_t fts_meta_count_{0};
};

// One term's postings inside one run, decoded one block at a time.
class SegmentTermIterator {
 public:
  SegmentTermIterator(const SegmentPtr& segment, const TermRef* term,
                      bool decode_positions);

  bool valid() const { return valid_; }
  DocId doc() const { return doc_; }
  uint32_t tf() const { return tf_; }
  uint32_t dl() const { return dl_; }
  const std::vector<uint32_t>& positions() const {
    return current_positions_;
  }
  uint64_t memory_usage_bytes() const;  // Decoded vector capacities; excludes object size.

  bool Next();
  bool Advance(DocId target);

  // Query-time upper bound for the current block. IDF is supplied by the
  // caller and therefore always reflects current live corpus statistics.
  float BlockMaxScore(double idf, double k1, double b, double avg_dl) const;
  uint32_t block_index() const { return block_; }

 private:
  bool LoadBlock(uint32_t block);
  bool SeekInBlock(DocId target);
  void UpdateCurrentPositions();

  SegmentPtr segment_;
  const TermRef* term_{nullptr};
  bool decode_positions_{true};
  bool valid_{false};
  uint32_t block_{0};
  uint64_t block_last_doc_{0};
  uint32_t block_max_tf_{0};
  uint32_t block_min_dl_{0};
  std::vector<uint32_t> offsets_;  // entry offsets within decoded block
  std::vector<DocId> doc_ids_;
  std::vector<uint32_t> tfs_;
  std::vector<uint32_t> dls_;
  std::vector<uint32_t> pos_begin_;  // offsets into positions_
  std::vector<uint32_t> positions_;
  std::vector<uint32_t> current_positions_;
  size_t entry_{0};
  DocId doc_{0};
  uint32_t tf_{0};
  uint32_t dl_{0};
};

}  // namespace m4
