// M4 research prototype: unified incremental FTS + inverted index.
//
// Research-only. This target is not linked into zvec and does not read or
// write the production index format. The public surface intentionally mirrors
// the production search contract (BM25 top-k, boolean/phrase FTS, scalar
// value/range filters, hybrid merge) so the same correctness oracle can be
// applied to both.

#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace m4 {

using DocId = uint64_t;

struct Status {
  bool ok{true};
  std::string message;

  static Status OK() { return {}; }
  static Status Error(std::string msg) { return {false, std::move(msg)}; }
};

struct ScalarValue {
  std::string field;
  std::string value;

  // Opaque, tagged byte encoding for typed Uint32 equality lookups. This is
  // deliberately not a numeric range encoding; use the typed query API below.
  static ScalarValue Unsigned32(std::string field, uint32_t value);
};

struct ArrayUint32Value {
  std::string field;
  std::vector<uint32_t> values;
};

struct Document {
  std::string text;
  // Scalar values are stored as length-prefixed keys. String values remain
  // unchanged; typed factories may provide opaque keys without changing the
  // posting layout.
  std::vector<ScalarValue> values;
  // Typed arrays are normalized to opaque values plus a non-null presence
  // marker before WAL/index insertion. Empty arrays therefore remain distinct
  // from missing fields without changing the segment or WAL format.
  std::vector<ArrayUint32Value> array_uint32_values;
};

enum class MutationKind { UPSERT, DELETE };

struct Mutation {
  Mutation() : kind(MutationKind::UPSERT) {}
  Mutation(MutationKind mutation_kind, std::string primary_key,
           Document mutation_document = {})
      : kind(mutation_kind),
        pk(std::move(primary_key)),
        document(std::move(mutation_document)) {}

  MutationKind kind;
  std::string pk;
  Document document;
};

struct Options {
  std::string fts_field{"body"};

  // Research analyzer configuration. Jieba requires an explicit dictionary
  // directory. The analyzer/filter configuration and content digests of the
  // Jieba dictionary/model files are persisted in the manifest; reopening with
  // a different identity is rejected before WAL replay.
  std::string fts_tokenizer_name{"standard"};
  std::string jieba_dict_dir;

  // Segment block size. Stored in the format header so a reader can reject a
  // mismatched build.
  uint32_t block_size{128};

  // Active delta bounds. A delta is flushed when either bound is exceeded, so
  // RAM is a function of the change rate, never of collection size.
  uint32_t max_delta_docs{512};
  uint64_t max_delta_bytes{8ull << 20};

  // Tiered leveling. Query fan-out is bounded by
  // max_runs_per_level * levels, and max_total_runs is a hard cap.
  uint32_t max_runs_per_level{4};
  uint32_t max_total_runs{12};

  // Compact a run/level earlier when tombstones dominate. This bounds disk
  // amplification by a constant factor.
  double tombstone_compact_ratio{0.30};

  // Research-only structural debt policy. Disabled by default so the S4
  // baseline is unchanged. When enabled, at most one bounded merge is
  // attempted per flush after a cooldown, based on per-run/global tombstones.
  bool debt_policy_enabled{false};
  double debt_compact_ratio{0.02};
  uint32_t debt_min_runs{3};
  uint32_t debt_max_merges_per_flush{1};
  double debt_cooldown_ms{1000.0};
  // 0 means live_docs / 4. This caps the input size of a maintenance merge.
  uint64_t debt_max_merge_docs{0};

  // Full live-stat checkpoint is written only when the delta log exceeds this
  // size, making checkpoint cost amortized over at least that many changes.
  uint64_t stats_log_threshold{4ull << 20};

  double bm25_k1{1.2};
  double bm25_b{0.75};

  bool fsync_wal{true};
  bool wal_enabled{true};
  bool decode_positions{true};

  // Research-only, bounded one-entry cache for immutable query-filter
  // candidates. Disabled by default; any document/liveness mutation invalidates it.
  bool cache_filter_candidates{false};

  // Research harness only: force a full merge on every flush. Used to
  // demonstrate the O(collection) behavior the design removes; never a
  // production mode.
  bool rebuild_on_flush{false};

  // Research-only: reuse an immutable analyzer instance when its verified
  // content fingerprint matches a live index in this process.
  bool reuse_analyzer_cache{false};
  // Research-only B3.1 candidate: on an initial weak-registry hit, the cached
  // analyzer was already initialized and verified for this content identity,
  // so skip the post-init dictionary reread. Cold/racing misses still verify.
  bool skip_cached_analyzer_fingerprint_verification{false};

  // Hard cap for the tracked working set of a compaction merge. If the
  // merge cannot fit, it aborts before publishing; old runs stay active.
  uint64_t compaction_memory_limit_bytes{128ull << 20};
};

struct Hit {
  DocId doc_id{0};
  std::string pk;
  float score{0.0f};
};

struct SearchResult {
  std::vector<Hit> hits;
  uint64_t examined{0};  // postings examined (including tombstoned candidates)
};

// Research-only write telemetry. Strictly opt-in; a null pointer adds no
// diagnostic timers to the mutation path.
struct WriteDiagnostics {
  bool capture_timing{true};
  double normalize_ms{0.0};
  double normalize_cpu_ms{0.0};
  double state_mutex_wait_ms{0.0};
  double state_mutex_wait_cpu_ms{0.0};
  double wal_encode_ms{0.0};
  double wal_encode_cpu_ms{0.0};
  double wal_append_ms{0.0};
  double wal_append_cpu_ms{0.0};
  double query_mutex_wait_ms{0.0};
  double query_mutex_wait_cpu_ms{0.0};
  double apply_ms{0.0};
  double apply_cpu_ms{0.0};
  double auto_flush_ms{0.0};
  double auto_flush_cpu_ms{0.0};
};

// Research-only query telemetry. Strictly opt-in; null diagnostics must not
// incur counter increments, diagnostic timers, or per-query diagnostic state.
struct QueryDiagnostics {
  // Input controls for isolating operation counters from timing-hook cost.
  // A non-null diagnostics pointer defaults to both enabled; set either false
  // before the call to measure the other independently.
  bool collect_counters{true};
  bool capture_timing{true};
  uint64_t runs_read{0};            // unique runs touched by the query
  uint64_t run_term_parts{0};       // segment cursors opened (term x run)
  uint64_t postings_scanned{0};     // posting positions entered by cursors
  uint64_t cursor_next_calls{0};
  uint64_t cursor_advance_calls{0};
  uint64_t dead_postings_filtered{0};
  uint64_t liveness_lookups{0};
  uint64_t filter_checks{0};
  uint64_t filter_rejections{0};
  uint64_t candidates{0};           // live candidates offered to ranking
  uint64_t score_evaluations{0};    // BM25 term contributions evaluated
  uint64_t upper_bound_evaluations{0};
  uint64_t wand_pivots{0};
  uint64_t topk_insertions{0};
  uint64_t topk_replacements{0};
  uint64_t topk_rejections{0};
  uint64_t topk_pushes{0};
  uint64_t topk_pops{0};
  uint64_t topk_finalize_pops{0};
  uint64_t sorted_hits{0};
  uint64_t filter_candidates{0};    // Boolean-filter candidate set size
  bool filter_cache_hit{false};
  uint64_t filter_candidate_bytes{0};  // Final DocId buffer lower bound only
  uint64_t filter_like_strings_scanned{0};  // per-run/delta field values visited
  uint64_t filter_like_candidate_capacity_bytes{0};  // sum of LIKE leaf DocId capacities
  // Coarse logical DocId payload bound; excludes vector slack and Hit/PK data.
  uint64_t filter_docid_temp_payload_upper_bound_bytes{0};
  uint64_t examined{0};
  // Optional phase timing. CPU fields use CLOCK_THREAD_CPUTIME_ID and are
  // populated only when the caller requests diagnostics.
  double mutex_wait_ms{0};
  double mutex_wait_cpu_ms{0};
  double filter_ms{0};
  double filter_cpu_ms{0};
  double query_parse_ms{0};
  double query_parse_cpu_ms{0};
  double setup_ms{0};
  double setup_cpu_ms{0};
  double execution_ms{0};
  double execution_cpu_ms{0};
  double finalize_ms{0};
  double finalize_cpu_ms{0};
};

enum class CompareOp { EQ, LT, LE, GT, GE };

// Small research-only Boolean filter language matching zg's current path
// filter composition needs. Negation is SQL-style field-present complement;
// array NOT CONTAIN ANY is kept as its own primitive because missing arrays
// are excluded while empty arrays remain in its universe.
enum class FilterKind {
  ALL,
  NONE,
  STRING_EQ,
  STRING_PREFIX,
  STRING_SUFFIX,
  UINT32_EQ,
  UINT32_IN,
  ARRAY_UINT32_CONTAINS_ANY,
  ARRAY_UINT32_NOT_CONTAINS_ANY,
  AND,
  OR,
  NOT,
};

struct FilterExpr {
  FilterKind op{FilterKind::ALL};
  std::string field;
  std::string string_value;
  uint32_t uint32_value{0};
  std::vector<uint32_t> uint32_values;
  std::vector<FilterExpr> children;

  static FilterExpr All();
  static FilterExpr None();
  static FilterExpr StringEq(std::string field, std::string value);
  static FilterExpr StringPrefix(std::string field, std::string value);
  static FilterExpr StringSuffix(std::string field, std::string value);
  static FilterExpr Uint32Eq(std::string field, uint32_t value);
  static FilterExpr Uint32In(std::string field, std::vector<uint32_t> values);
  static FilterExpr ArrayContainsAny(std::string field,
                                     std::vector<uint32_t> values);
  static FilterExpr ArrayNotContainsAny(std::string field,
                                        std::vector<uint32_t> values);
  static FilterExpr And(std::vector<FilterExpr> children);
  static FilterExpr Or(std::vector<FilterExpr> children);
  static FilterExpr Not(FilterExpr child);
};

// Optional open-path attribution for the research harness. `_ms` fields are
// steady-clock wall times; `_cpu_ms` fields are current-thread CPU times.
struct OpenDiagnostics {
  double total_ms{0.0};
  double total_cpu_ms{0.0};
  double analyzer_fingerprint_ms{0.0};
  double analyzer_fingerprint_cpu_ms{0.0};
  double manifest_and_analyzer_ms{0.0};
  double manifest_and_analyzer_cpu_ms{0.0};
  double analyzer_init_ms{0.0};
  double analyzer_init_cpu_ms{0.0};
  double fingerprint_verification_ms{0.0};
  double fingerprint_verification_cpu_ms{0.0};
  double segment_open_ms{0.0};
  double segment_open_cpu_ms{0.0};
  double tombstones_ms{0.0};
  double tombstones_cpu_ms{0.0};
  double stats_load_ms{0.0};
  double stats_load_cpu_ms{0.0};
  double wal_replay_ms{0.0};
  double wal_replay_cpu_ms{0.0};
  double live_key_map_ms{0.0};
  double live_key_map_cpu_ms{0.0};
  double garbage_collect_ms{0.0};
  double garbage_collect_cpu_ms{0.0};
  uint64_t segment_count{0};
  uint64_t segment_bytes{0};
  uint64_t analyzer_fingerprint_calls{0};
  uint64_t analyzer_fingerprint_sha256_calls{0};
  uint64_t analyzer_fingerprint_dictionary_bytes{0};
  uint64_t analyzer_fingerprint_sha256_bytes{0};
  bool analyzer_cache_hit{false};
  bool analyzer_cache_miss{false};
  bool analyzer_cache_race_reuse{false};
  bool analyzer_cache_waited{false};
  bool analyzer_cache_wait_reuse{false};
  bool analyzer_dictionary_snapshot_used{false};
  bool analyzer_cache_expired_entry{false};
  bool analyzer_initialization_attempted{false};
  bool analyzer_initialization_succeeded{false};
  bool fingerprint_verification_skipped{false};
};

struct IndexStats {
  uint64_t live_docs{0};
  uint64_t live_tokens{0};
  uint64_t dead_docs{0};
  uint64_t runs{0};
  uint64_t segments_bytes{0};
  uint64_t tombstone_bytes{0};
  uint64_t stats_log_bytes{0};
  uint64_t wal_bytes{0};
  uint64_t wal_fsyncs{0};  // successful WAL sync calls since this Index opened
  uint64_t fsyncs{0};
  uint64_t compactions{0};
  uint64_t compacted_docs{0};
  uint64_t bytes_written{0};       // all prototype writes (segment+meta+wal)
  uint64_t segment_bytes_written{0};
  uint64_t run_ids_created{0};
  // Compaction telemetry. Cumulative over the index lifetime.
  double compaction_wall_ms{0.0};
  uint64_t compaction_merges{0};
  uint64_t compaction_input_docs{0};
  uint64_t compaction_output_docs{0};
  uint64_t compaction_peak_bytes{0};
  std::vector<uint32_t> runs_per_level;
};

class Index {
 public:
  static Status Open(const std::string& directory, const Options& options,
                     std::unique_ptr<Index>* out,
                     OpenDiagnostics* diagnostics = nullptr);
  ~Index();

  Index(const Index&) = delete;
  Index& operator=(const Index&) = delete;

  // Upsert keyed by a stable primary key. An existing key is tombstoned and a
  // new global DocId is appended; no existing posting is rewritten.
  Status Upsert(const std::string& pk, const Document& document);
  Status Upsert(const std::string& pk, const Document& document,
                WriteDiagnostics* diagnostics);

  // Tombstone the live version of |pk|. Postings are untouched until a
  // bounded compaction rewrites the owning run.
  Status Delete(const std::string& pk);

  // Atomically apply 1..256 mutations as one WAL transaction. The batch is
  // fully durable on return when fsync_wal is enabled. The encoded batch is
  // capped at 8 MiB; existing single-document methods retain their behavior.
  Status ApplyBatch(const std::vector<Mutation>& mutations);

  // Seal the active delta into an immutable run, commit a manifest, then apply
  // the compaction policy. Cost is O(change), not O(collection).
  Status Flush();

  // Research/maintenance hook: merge all runs and drop tombstones. Cost is
  // O(live collection); used by tests and compaction benchmarks.
  Status CompactNow();

  // Explicit, non-production prototype: snapshot inputs, release the writer
  // state lock during the merge, then abort publication if the snapshot changed.
  Status CompactNowSnapshotPrototype();

  // BM25 top-k. Query supports whitespace terms, uppercase AND/OR, and quoted
  // phrases. Default is OR with WAND.
  Status SearchFts(const std::string& query, uint32_t top_k,
                   SearchResult* out,
                   QueryDiagnostics* diagnostics = nullptr) const;
  // Boolean-filtered BM25; filter candidates are admitted before top-k heap
  // updates, not applied to an already truncated result list.
  Status SearchFtsFiltered(const std::string& query, uint32_t top_k,
                           const FilterExpr& filter, SearchResult* out,
                           QueryDiagnostics* diagnostics = nullptr) const;
  // Evaluate a typed Boolean filter and return live candidates in DocId order.
  Status SearchFilter(const FilterExpr& filter, SearchResult* out) const;

  // Scalar inverted lookup. Returns live docs in ascending DocId order.
  Status SearchInvert(const std::string& field, const std::string& value,
                      CompareOp op, SearchResult* out) const;

  // Typed Uint32 point/set lookups. These use exact opaque keys, never the
  // generic string-lexicographic range comparisons. IN over an empty list
  // returns an empty result.
  Status SearchInvertUint32(const std::string& field, uint32_t value,
                            SearchResult* out) const;
  Status SearchInvertUint32In(const std::string& field,
                              const std::vector<uint32_t>& values,
                              SearchResult* out) const;

  // Typed ArrayUint32 membership. CONTAIN_ANY on an empty operand list is
  // empty; NOT CONTAIN ANY returns the non-null (present) field universe.
  // Native-compatible query validation limits operands to 32.
  Status SearchArrayUint32ContainsAny(const std::string& field,
                                      const std::vector<uint32_t>& values,
                                      SearchResult* out) const;
  Status SearchArrayUint32NotContainsAny(
      const std::string& field, const std::vector<uint32_t>& values,
      SearchResult* out) const;

  // Hybrid contract: the vector branch is supplied by the caller (HNSW is
  // intentionally untouched by M4). FTS and vector scores are min-max
  // normalized and linearly blended, matching the existing rank-merge
  // semantics.
  Status SearchHybrid(const std::string& query, uint32_t top_k, double alpha,
                      const std::unordered_map<std::string, float>&
                          vector_scores,
                      SearchResult* out) const;

  IndexStats GetStats() const;


 private:
  class Impl;
  explicit Index(Impl* impl);
  std::unique_ptr<Impl> impl_;
};

}  // namespace m4
