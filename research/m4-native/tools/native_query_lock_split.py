#!/usr/bin/env python3
"""Apply fail-closed flush and optimize-reader lock splits to copied native source."""
from __future__ import annotations


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f'{label}: expected one source anchor, found {count}')
    return source.replace(old, new, 1)


def _edit_method(source: str, start_sig: str, end_sig: str,
                 old: str, new: str, label: str) -> str:
    start = source.find(start_sig)
    end = source.find(end_sig, start + len(start_sig)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError(f'{label}: method boundaries changed; refusing lock split')
    body = source[start:end]
    updated = _replace_once(body, old, new, label)
    return source[:start] + updated + source[end:]


def _apply_optimize_query_snapshot(source: str) -> str:
    start = source.find('Status CollectionImpl::optimize(const OptimizeOptions &options) {')
    phase_start = source.find('  // Phase 1: exclusively seal the current writing segment', start)
    phase_end = source.find('  if (persist_segments.size() == 0) {', phase_start)
    if start < 0 or phase_start < 0 or phase_end < 0:
        raise ValueError('optimize phase-one boundaries changed; refusing query snapshot')
    old_phase = source[phase_start:phase_end]
    required = (
        'schema_handle_mtx_', 'check_no_active_iterators("optimize")',
        'write_mtx_', 'switch_to_new_segment_for_writing()',
        'get_all_persist_segments()')
    if any(old_phase.count(token) != 1 for token in required):
        raise ValueError('optimize phase-one lock/state inventory changed')
    new_phase = '''  // Phase 1: keep schema stable, exclude vector/index readers, and publish a
  // temporary segment snapshot so unfiltered FTS-only readers can finish while
  // the current writer is flushed and sealed.
  {
    // The iterator count is guarded exclusively. maintenance_mtx_ prevents new
    // iterators and schema DDL after this short check.
    {
      std::unique_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);
      CHECK_RETURN_STATUS(check_no_active_iterators("optimize"));
    }
    std::shared_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);
    std::unique_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);
    std::unique_lock<std::shared_mutex> optimize_query_gate_lock(
        optimize_query_gate_mtx_);
    std::unique_lock<std::shared_mutex> write_lock(write_mtx_);

    CHECK_DESTROY_RETURN_STATUS(destroyed_, false);
    CHECK_CLOSED_RETURN_STATUS(closed_, false);

    if (writing_segment_->has_record()) {
      {
        std::lock_guard<std::mutex> snapshot_lock(optimize_query_snapshot_mtx_);
        optimize_query_snapshot_ = get_all_segments_unsafe();
        optimize_query_snapshot_active_ = true;
      }
      auto s = switch_to_new_segment_for_writing();
      {
        std::lock_guard<std::mutex> snapshot_lock(optimize_query_snapshot_mtx_);
        optimize_query_snapshot_active_ = false;
        optimize_query_snapshot_.clear();
      }
      if (!s.ok()) {
        return s;
      }
    }

    persist_segments =
        get_all_persist_segments();  // will not return writing segment
  }

'''
    return source[:phase_start] + new_phase + source[phase_end:]


def apply_query_lock_split(source: str, *, test_hooks: bool = False,
                           test_hook_source: str | None = None) -> str:
    """Transform copied sources for reader-safe flush and optimize sealing.

    Vector-index readers acquire a dedicated shared guard; flush/optimize take
    its exclusive side. Only unfiltered FTS-only queries bypass the optimize
    gate and use a temporary segment snapshot while the writer is sealed. The
    native checkout is never written.
    """
    if 'class CollectionImpl : public Collection {' not in source:
        raise ValueError('CollectionImpl declaration missing; refusing lock split')

    helpers = '''
// A vector query/read must not race a flush or optimize seal of the vector
// index, but FTS-only readers do not access that index.
bool query_requires_vector_access(const SearchQuery &query) {
  return query.include_vector_ || query.target_.get_vector_view().has_value();
}

bool query_requires_vector_access(const MultiQuery &query) {
  if (query.include_vector) {
    return true;
  }
  for (const auto &subquery : query.queries) {
    if (subquery.target_.get_vector_view().has_value()) {
      return true;
    }
  }
  return false;
}

// Only unfiltered FTS-only searches can overlap the isolated mutable-segment
// seal prototype. Scalar-index queries and all other collection reads share a
// gate with optimize so they cannot race indexer sealing or metadata changes.
bool query_requires_optimize_gate(const SearchQuery &query) {
  return query.target_.get_fts_clause() == nullptr || query.include_vector_ ||
         !query.filter_.empty();
}

bool query_requires_optimize_gate(const MultiQuery &) { return true; }

'''
    if test_hooks:
        if not test_hook_source:
            raise ValueError('test-hook build requires the native smoke test source')
        source = _replace_once(
            source,
            '#include <condition_variable>\n',
            '#include <condition_variable>\n#include <chrono>\n#include <cstdio>\n#include <cstring>\n#include <fstream>\n#include <map>\n#include <thread>\n'
            '#include <zvec/ailego/buffer/block_eviction_queue.h>\n',
            'test-hook runner includes')
        hooks = '''#ifdef M4_NATIVE_LOCK_SPLIT_TEST_HOOKS
namespace {
using NativeLockSplitTestHook = void (*)(const char *);
using NativeLockSplitTestCfHook = void (*)(const char *, const char *);
std::atomic<NativeLockSplitTestHook> g_native_lock_split_test_hook{nullptr};
std::atomic<NativeLockSplitTestCfHook> g_native_lock_split_test_cf_hook{
    nullptr};
std::mutex g_native_lock_split_failure_mutex;
std::map<std::string, int> g_native_lock_split_failures;
}  // namespace

extern "C" __attribute__((visibility("default")))
void zvec_m4_lock_split_set_test_hook(void (*hook)(const char *)) {
  g_native_lock_split_test_hook.store(hook, std::memory_order_release);
}

void native_lock_split_test_event(const char *event) {
  auto hook = g_native_lock_split_test_hook.load(std::memory_order_acquire);
  if (hook != nullptr) {
    hook(event);
  }
}

extern "C" __attribute__((visibility("hidden")))
void zvec_m4_lock_split_emit_segment_test_hook(const char *event) {
  native_lock_split_test_event(event);
}

extern "C" __attribute__((visibility("hidden")))
void zvec_m4_lock_split_emit_cf_test_hook(const char *event, const char *cf) {
  auto hook = g_native_lock_split_test_cf_hook.load(std::memory_order_acquire);
  if (hook != nullptr) {
    hook(event, cf);
  }
}

extern "C" __attribute__((visibility("default")))
void zvec_m4_lock_split_set_failure(const char *event, int countdown) {
  std::lock_guard lock(g_native_lock_split_failure_mutex);
  g_native_lock_split_failures[event] = countdown;
}

extern "C" __attribute__((visibility("default")))
void zvec_m4_lock_split_clear_failures() {
  std::lock_guard lock(g_native_lock_split_failure_mutex);
  g_native_lock_split_failures.clear();
}

extern "C" __attribute__((visibility("hidden")))
bool zvec_m4_lock_split_inject_failure(const char *event) {
  std::lock_guard lock(g_native_lock_split_failure_mutex);
  auto found = g_native_lock_split_failures.find(event);
  if (found == g_native_lock_split_failures.end()) {
    return false;
  }
  if (found->second < 0) {
    return false;
  }
  if (found->second == 0) {
    found->second = -1;
    return true;
  }
  --found->second;
  return false;
}
#else
inline void native_lock_split_test_event(const char *) noexcept {}
#endif

'''
        helpers += hooks
    else:
        helpers += 'inline void native_lock_split_test_event(const char *) noexcept {}\n\n'
    source = _replace_once(
        source,
        'enum class WriteMode : uint8_t {\n'
        '  UNDEFINED = 0,\n  INSERT,\n  UPDATE,\n  UPSERT,\n};\n',
        'enum class WriteMode : uint8_t {\n'
        '  UNDEFINED = 0,\n  INSERT,\n  UPDATE,\n  UPSERT,\n};\n' + helpers,
        'query/lock-split helpers')

    if test_hooks:
        source = _replace_once(
            source,
            'Status CollectionImpl::switch_to_new_segment_for_writing(\n'
            '    const CollectionSchema::Ptr &schema) {\n',
            'Status CollectionImpl::switch_to_new_segment_for_writing(\n'
            '    const CollectionSchema::Ptr &schema) {\n'
            '  if (zvec_m4_lock_split_inject_failure("writer_creation")) {\n'
            '    return Status::InternalError("injected writer creation failure");\n'
            '  }\n',
            'writer creation failure injection')
        source = _replace_once(
            source,
            '    s = version_manager_->apply(new_version);\n'
            '    CHECK_RETURN_STATUS(s);\n\n'
            '    s = version_manager_->flush();\n',
            '    if (zvec_m4_lock_split_inject_failure("optimize_version_apply")) {\n'
            '      return Status::InternalError("injected optimize version apply failure");\n'
            '    }\n'
            '    s = version_manager_->apply(new_version);\n'
            '    CHECK_RETURN_STATUS(s);\n\n'
            '    s = version_manager_->flush();\n',
            'optimize version apply failure injection')

    source = _replace_once(
        source,
        '  mutable std::shared_mutex schema_handle_mtx_;\n',
        '  mutable std::shared_mutex schema_handle_mtx_;\n'
        '  // Excludes vector-index reads during flush and optimize seal.\n'
        '  // Lock order: schema -> vector access -> optimize query gate -> write.\n'
        '  mutable std::shared_mutex vector_access_mtx_;\n'
        '  mutable std::shared_mutex optimize_query_gate_mtx_;\n'
        '  mutable std::mutex optimize_query_snapshot_mtx_;\n'
        '  bool optimize_query_snapshot_active_{false};\n'
        '  std::vector<Segment::Ptr> optimize_query_snapshot_;\n',
        'vector access mutex declaration')
    source = _replace_once(
        source,
        '// Lock order: maintenance -> schema -> write -> SegmentManager; never\n',
        '// Lock order: maintenance -> schema -> vector access -> optimize query gate -> write -> SegmentManager; never\n',
        'collection lock-order comment')

    source = _edit_method(
        source,
        'Status CollectionImpl::flush() {',
        'Status CollectionImpl::flush_unsafe() {',
        '  // The vector index interface does not promise that flush() is safe beside\n'
        '  // search(). Keep the old reader exclusion for vector collections. For\n'
        '  // non-vector collections, shared schema/write locks exclude DDL and writers\n'
        '  // while allowing read-only queries to proceed during component I/O.\n',
        '  // Keep DDL and writers excluded for every flush. A vector collection also\n'
        '  // excludes vector-index readers, but FTS-only readers share schema/write\n'
        '  // access and do not acquire vector_access_mtx_.\n',
        'flush safety comment')
    source = _edit_method(
        source,
        'Status CollectionImpl::flush() {',
        'Status CollectionImpl::flush_unsafe() {',
        '    std::unique_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);\n',
        '    std::shared_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);\n',
        'vector flush schema lock mode')
    source = _edit_method(
        source,
        'Status CollectionImpl::flush() {',
        'Status CollectionImpl::flush_unsafe() {',
        '    CHECK_CLOSED_RETURN_STATUS(closed_, false);\n'
        '    native_timeline::Span write_wait_span("collection_flush_stage",\n'
        '                                          "write_lock_wait", this);\n',
        '    CHECK_CLOSED_RETURN_STATUS(closed_, false);\n'
        '    native_timeline::Span vector_wait_span("collection_flush_stage",\n'
        '                                            "vector_access_lock_wait", this);\n'
        '    std::unique_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);\n'
        '    vector_wait_span.finish();\n'
        '    native_timeline::Span write_wait_span("collection_flush_stage",\n'
        '                                          "write_lock_wait", this);\n',
        'vector flush access lock')
    source = _edit_method(
        source,
        'Status CollectionImpl::flush() {',
        'Status CollectionImpl::flush_unsafe() {',
        '    std::shared_lock<std::shared_mutex> write_lock(write_mtx_);\n'
        '    write_wait_span.finish();\n'
        '    native_timeline::Span write_hold_span("collection_flush_stage",\n'
        '                                          "write_lock_held", this);\n',
        '    std::shared_lock<std::shared_mutex> write_lock(write_mtx_);\n'
        '    write_wait_span.finish();\n'
        '    native_lock_split_test_event("flush_vector_and_write_locks_acquired");\n'
        '    native_timeline::Span write_hold_span("collection_flush_stage",\n'
        '                                          "write_lock_held", this);\n',
        'flush test gate after collection locks')

    source = _edit_method(
        source,
        'Result<WriteResults> CollectionImpl::write_impl(',
        'bool CollectionImpl::need_switch_to_new_segment() const {',
        '  std::lock_guard write_lock(write_mtx_);',
        '  native_lock_split_test_event("write_lock_wait");\n'
        '  std::lock_guard write_lock(write_mtx_);',
        'write lock wait test event')

    query_anchor = (
        '  CHECK_DESTROY_RETURN_STATUS_EXPECTED(destroyed_, false);\n'
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n')
    search_anchor = (
        '  query_state_check_span.finish();\n\n'
        '  auto result = query_unsafe(query);')
    search_guard = (
        '  query_state_check_span.finish();\n\n'
        '  std::shared_lock<std::shared_mutex> vector_access_lock(\n'
        '      vector_access_mtx_, std::defer_lock);\n'
        '  if (query_requires_vector_access(query)) {\n'
        '    native_timeline::Span vector_wait_span(\n'
        '        "collection_query_stage", "vector_access_lock_wait", this, -1, trace_fts);\n'
        '    native_lock_split_test_event("search_vector_guard_wait");\n'
        '    vector_access_lock.lock();\n'
        '    vector_wait_span.finish();\n'
        '  }\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_, std::defer_lock);\n'
        '  if (query_requires_optimize_gate(query)) {\n'
        '    optimize_query_gate_lock.lock();\n'
        '  }\n\n'
        '  auto result = query_unsafe(query);')
    source = _edit_method(
        source,
        'Result<DocPtrList> CollectionImpl::query(const SearchQuery &query) const {',
        'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {',
        search_anchor, search_guard, 'SearchQuery vector guard')

    multi_guard = query_anchor + (
        '  std::shared_lock<std::shared_mutex> vector_access_lock(\n'
        '      vector_access_mtx_, std::defer_lock);\n'
        '  if (query_requires_vector_access(query)) {\n'
        '    native_timeline::Span vector_wait_span(\n'
        '        "collection_query_stage", "vector_access_lock_wait", this);\n'
        '    native_lock_split_test_event("multi_query_vector_guard_wait");\n'
        '    vector_access_lock.lock();\n'
        '    vector_wait_span.finish();\n'
        '  }\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_);\n\n')
    source = _edit_method(
        source,
        'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {',
        'template <typename Query>\nResult<internal::QueryResultSnapshot>',
        query_anchor, multi_guard, 'MultiQuery vector guard')

    snapshot_guard = query_anchor + (
        '  std::shared_lock<std::shared_mutex> vector_access_lock(\n'
        '      vector_access_mtx_, std::defer_lock);\n'
        '  if (query_requires_vector_access(query)) {\n'
        '    native_timeline::Span vector_wait_span(\n'
        '        "collection_query_stage", "snapshot_vector_access_lock_wait", this);\n'
        '    native_lock_split_test_event("snapshot_vector_guard_wait");\n'
        '    vector_access_lock.lock();\n'
        '    vector_wait_span.finish();\n'
        '  }\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_, std::defer_lock);\n'
        '  if (query_requires_optimize_gate(query)) {\n'
        '    optimize_query_gate_lock.lock();\n'
        '  }\n\n')
    source = _edit_method(
        source,
        'CollectionImpl::query_result_snapshot_impl(const Query &query) const {',
        'Result<DocPtrList> CollectionImpl::query_unsafe(',
        query_anchor, snapshot_guard, 'query snapshot vector guard')

    group_anchor = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  auto segments = get_all_segments();\n')
    group_guard = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  native_timeline::Span vector_wait_span(\n'
        '      "collection_query_stage", "group_vector_access_lock_wait", this);\n'
        '  native_lock_split_test_event("group_by_vector_guard_wait");\n'
        '  std::shared_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);\n'
        '  vector_wait_span.finish();\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_);\n\n'
        '  auto segments = get_all_segments();\n')
    source = _edit_method(
        source,
        'Result<GroupResults> CollectionImpl::group_by_query(',
        'Result<DocPtrMap> CollectionImpl::fetch(',
        group_anchor, group_guard, 'group-by vector guard')

    fetch_anchor = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  auto segments = get_all_segments();\n')
    fetch_guard = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  std::shared_lock<std::shared_mutex> vector_access_lock(\n'
        '      vector_access_mtx_, std::defer_lock);\n'
        '  if (include_vector) {\n'
        '    native_timeline::Span vector_wait_span(\n'
        '        "collection_query_stage", "fetch_vector_access_lock_wait", this);\n'
        '    native_lock_split_test_event("fetch_vector_guard_wait");\n'
        '    vector_access_lock.lock();\n'
        '    vector_wait_span.finish();\n'
        '  }\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_);\n\n'
        '  auto segments = get_all_segments();\n')
    source = _edit_method(
        source,
        'Result<DocPtrMap> CollectionImpl::fetch(',
        'Result<std::string> CollectionImpl::debug_get_hnsw_storage_mode(',
        fetch_anchor, fetch_guard, 'fetch vector guard')

    debug_anchor = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  // Try all segments (including the writing one). The first segment that has\n')
    debug_guard = (
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  native_timeline::Span vector_wait_span(\n'
        '      "collection_query_stage", "debug_vector_access_lock_wait", this);\n'
        '  native_lock_split_test_event("debug_vector_guard_wait");\n'
        '  std::shared_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);\n'
        '  vector_wait_span.finish();\n'
        '  std::shared_lock<std::shared_mutex> optimize_query_gate_lock(\n'
        '      optimize_query_gate_mtx_);\n\n'
        '  // Try all segments (including the writing one). The first segment that has\n')
    source = _edit_method(
        source,
        'Result<std::string> CollectionImpl::debug_get_hnsw_storage_mode(',
        'Status CollectionImpl::recovery() {',
        debug_anchor, debug_guard, 'debug HNSW vector guard')

    source = _apply_optimize_query_snapshot(source)

    source = _edit_method(
        source,
        'std::vector<Segment::Ptr> CollectionImpl::get_all_segments() const {',
        'std::vector<Segment::Ptr> CollectionImpl::get_all_segments_unsafe() const {',
        '  std::shared_lock<std::shared_mutex> write_lock(write_mtx_);\n'
        '  return get_all_segments_unsafe();',
        '  {\n'
        '    std::lock_guard<std::mutex> snapshot_lock(optimize_query_snapshot_mtx_);\n'
        '    if (optimize_query_snapshot_active_) {\n'
        '      return optimize_query_snapshot_;\n'
        '    }\n'
        '  }\n'
        '  std::shared_lock<std::shared_mutex> write_lock(write_mtx_);\n'
        '  return get_all_segments_unsafe();',
        'temporary optimize segment snapshot')

    if test_hooks:
        source = _replace_once(
            source,
            '''  auto new_schema = std::make_shared<CollectionSchema>(*schema_);
  auto s = new_schema->drop_index(column_name);
  CHECK_RETURN_STATUS(s);''',
            '''  auto new_schema = std::make_shared<CollectionSchema>(*schema_);
  auto s = new_schema->drop_index(column_name);
  CHECK_RETURN_STATUS(s);
  native_lock_split_test_event("ddl_drop_schema_prepared");''',
            'DDL drop schema-prepared hook')
        source = _replace_once(
            source,
            '''    } else if (std::holds_alternative<DropFtsIndexTask>(task_info)) {
      auto fts_task = std::get<DropFtsIndexTask>(task_info);
      s = new_version.update_persisted_segment_meta(
          fts_task.output_segment_meta_);
    }
    CHECK_RETURN_STATUS(s);
  }

  s = commit_schema_change_with_new_writing_segment(
      new_schema, old_writing_segment, old_version, &new_version,
      writing_min_doc_id);
  CHECK_RETURN_STATUS(s);

  // 4. remove old segments or block''',
            '''    } else if (std::holds_alternative<DropFtsIndexTask>(task_info)) {
      auto fts_task = std::get<DropFtsIndexTask>(task_info);
      s = new_version.update_persisted_segment_meta(
          fts_task.output_segment_meta_);
    }
    CHECK_RETURN_STATUS(s);
  }

  s = commit_schema_change_with_new_writing_segment(
      new_schema, old_writing_segment, old_version, &new_version,
      writing_min_doc_id);
  CHECK_RETURN_STATUS(s);
  native_lock_split_test_event("ddl_drop_after_commit");

  // 4. remove old segments or block''',
            'DDL drop after-commit hook')

    if test_hooks:
        hook_source = str(test_hook_source or '')
        if not hook_source:
            raise ValueError('test-hook build requires the native smoke test source')
        source = _replace_once(
            source,
            '}  // namespace zvec\n',
            hook_source.rstrip() + '\n\n}  // namespace zvec\n',
            'test-hook runner insertion')
    if source.count('vector_access_mtx_') != 10:
        raise ValueError('unexpected vector-access mutex use count; refusing incomplete lock split')
    if 'std::unique_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);' in source[
            source.find('Status CollectionImpl::flush() {'):
            source.find('Status CollectionImpl::flush_unsafe() {')]:
        raise ValueError('vector flush still exclusively holds schema lock')
    return source


def apply_optimize_read_safe_dump(source: str, *, test_hooks: bool = False) -> str:
    """Allow FTS-only fetch/search to overlap a writer's seal in copied source."""
    if test_hooks:
        source = _replace_once(
            source,
            'namespace zvec {\n',
            'namespace zvec {\n'
            'extern "C" void zvec_m4_lock_split_emit_segment_test_hook(const char *);\n'
            'extern "C" bool zvec_m4_lock_split_inject_failure(const char *);\n',
            'segment test-hook declaration')
        before_inverted = (
            '  zvec_m4_lock_split_emit_segment_test_hook(\n'
            '      "optimize_dump_before_inverted_seal");\n')
        before_fts = (
            '  zvec_m4_lock_split_emit_segment_test_hook(\n'
            '      "optimize_dump_before_fts_seal");\n')
        invert_failure = (
            '  if (zvec_m4_lock_split_inject_failure("optimize_inverted_seal")) {\n'
            '    return Status::InternalError("injected inverted seal failure");\n'
            '  }\n')
    else:
        before_inverted = ''
        before_fts = ''
        invert_failure = ''
    old_dump = '''Status SegmentImpl::dump() {
  // Exclusive: flush() below resets memory_store_ and rewrites persist_stores_,
  // the block metadata and the indexer maps. Lock order matches Insert (the
  // caller already holds the collection's exclusive write_mtx_).
  std::lock_guard<std::shared_mutex> lock(seg_mtx_);

  if (sealed_) {
    return Status::NotSupported("Segment has been dumped.");
  }
  auto s = flush_locked();
  CHECK_RETURN_STATUS(s);

  if (invert_indexers_) {
    s = invert_indexers_->seal();
    CHECK_RETURN_STATUS(s);
  }

  s = dump_fts_indexers();
  CHECK_RETURN_STATUS(s);

  sealed_ = true;

  return Status::OK();
}'''
    new_dump = '''Status SegmentImpl::dump() {
  // The collection's exclusive write lock excludes mutations. Public flush's
  // segment-lock protocol lets immutable fetch/search readers continue while
  // component I/O and read-independent index sealing run.
  {
    std::unique_lock<std::shared_mutex> lock(seg_mtx_);
    if (sealed_) {
      return Status::NotSupported("Segment has been dumped.");
    }
  }
  auto s = flush_impl(false);
  CHECK_RETURN_STATUS(s);

''' + before_inverted + invert_failure + '''  if (invert_indexers_) {
    s = invert_indexers_->seal();
    CHECK_RETURN_STATUS(s);
  }

''' + before_fts + '''  s = dump_fts_indexers();
  CHECK_RETURN_STATUS(s);

  {
    std::unique_lock<std::shared_mutex> lock(seg_mtx_);
    sealed_ = true;
  }
  return Status::OK();
}'''
    if test_hooks:
        old_vector = """  // flush vector indexer
  for (const auto &indexer : memory_vector_indexers_) {
    if (indexer.second) {
      s = trace_native_flush_stage("vector_indexer", id(),
                                   [&]() { return indexer.second->flush(); });
      CHECK_RETURN_STATUS(s);
    }
  }"""
        new_vector = """  // flush vector indexer
  for (const auto &indexer : memory_vector_indexers_) {
    if (indexer.second) {
      zvec_m4_lock_split_emit_segment_test_hook(
          "vector_indexer_before_flush");
      s = trace_native_flush_stage("vector_indexer", id(),
                                   [&]() { return indexer.second->flush(); });
      CHECK_RETURN_STATUS(s);
      zvec_m4_lock_split_emit_segment_test_hook(
          "vector_indexer_after_flush");
    }
  }"""
        source = _replace_once(source, old_vector, new_vector,
                               'vector flush test hooks')
    return _replace_once(source, old_dump, new_dump,
                         'read-safe SegmentImpl::dump')


def apply_fts_column_test_hooks(source: str, *, test_hooks: bool = False) -> str:
    """Add test-only barriers around FTS search and postings conversion."""
    if not test_hooks:
        return source
    source = _replace_once(
        source,
        'namespace zvec::fts {\n',
        'namespace zvec::fts {\n'
        'extern "C" void zvec_m4_lock_split_emit_segment_test_hook(const char *);\n'
        'extern "C" void zvec_m4_lock_split_emit_cf_test_hook(const char *, const char *);\n'
        'extern "C" bool zvec_m4_lock_split_inject_failure(const char *);\n',
        'FTS column test-hook declaration')
    source = _replace_once(
        source,
        'Result<void> FtsColumnIndexer::convert_postings_to_bitpacked() {\n',
        'Result<void> FtsColumnIndexer::convert_postings_to_bitpacked() {\n'
        '  if (zvec_m4_lock_split_inject_failure("fts_postings_conversion")) {\n'
        '    return tl::make_unexpected(Status::InternalError(\n'
        '        "injected FTS postings conversion failure"));\n'
        '  }\n',
        'FTS postings conversion failure injection')
    source = _replace_once(
        source,
        '  rocksdb::FlushOptions flush_options;\n',
        '  if (zvec_m4_lock_split_inject_failure("fts_postings_flush")) {\n'
        '    return tl::make_unexpected(Status::InternalError(\n'
        '        "injected FTS postings flush failure"));\n'
        '  }\n'
        '  rocksdb::FlushOptions flush_options;\n',
        'FTS postings flush failure injection')
    source = _replace_once(
        source,
        '  auto flush_status = ctx_->db_->Flush(flush_options, postings_cf_);\n'
        '  if (!flush_status.ok()) {\n',
        '  zvec_m4_lock_split_emit_cf_test_hook("fts_seal_postings_flush_begin",\n'
        '                                       field_name_.c_str());\n'
        '  auto flush_status = ctx_->db_->Flush(flush_options, postings_cf_);\n'
        '  if (!flush_status.ok()) {\n',
        'FTS postings flush begin hook')
    source = _replace_once(
        source,
        '  // Statistics must also survive before $TF is dropped. Keep auxiliary data\n',
        '  zvec_m4_lock_split_emit_cf_test_hook("fts_seal_postings_flush_complete",\n'
        '                                       field_name_.c_str());\n'
        '  // Statistics must also survive before $TF is dropped. Keep auxiliary data\n',
        'FTS postings flush complete hook')
    search_anchor = (
        '  DocIteratorPtr root_iter = std::move(iter_result.value());\n'
        '  if (!root_iter) {\n'
        '    // No matching terms found — valid empty result, not an error.\n'
        '    return std::vector<FtsResult>{};\n'
        '  }\n\n'
        '  // Candidate-driven mode: AND a CandidateDocIterator into the root so the\n')
    search_replacement = (
        '  DocIteratorPtr root_iter = std::move(iter_result.value());\n'
        '  if (!root_iter) {\n'
        '    // No matching terms found — valid empty result, not an error.\n'
        '    return std::vector<FtsResult>{};\n'
        '  }\n'
        '  zvec_m4_lock_split_emit_segment_test_hook(\n'
        '      "fts_search_iterator_ready");\n\n'
        '  // Candidate-driven mode: AND a CandidateDocIterator into the root so the\n')
    source = _replace_once(source, search_anchor, search_replacement,
                           'FTS search iterator test hook')
    return _replace_once(
        source,
        '  std::string current_term;\n'
        '  std::vector<uint32_t> doc_ids;\n',
        '  zvec_m4_lock_split_emit_segment_test_hook(\n'
        '      "fts_seal_conversion_before_terms");\n'
        '  std::string current_term;\n'
        '  std::vector<uint32_t> doc_ids;\n',
        'FTS postings conversion test hook')


def apply_fts_seal_test_hook(source: str, *, test_hooks: bool = False) -> str:
    """Expose a deterministic pause after FTS side-CF readers are drained."""
    if not test_hooks:
        return source
    source = _replace_once(
        source,
        'namespace zvec {\n',
        'namespace zvec {\n'
        'extern "C" void zvec_m4_lock_split_emit_segment_test_hook(const char *);\n'
        'extern "C" bool zvec_m4_lock_split_inject_failure(const char *);\n',
        'FTS seal test-hook declaration')
    source = _replace_once(
        source,
        'Status FtsIndexer::flush() {\n',
        'Status FtsIndexer::flush() {\n'
        '  if (zvec_m4_lock_split_inject_failure("fts_flush")) {\n'
        '    return Status::InternalError("injected FTS flush failure");\n'
        '  }\n',
        'FTS flush failure injection')
    source = _replace_once(
        source,
        'Status FtsIndexer::seal_all() {\n',
        'Status FtsIndexer::seal_all() {\n'
        '  if (zvec_m4_lock_split_inject_failure("fts_seal_all")) {\n'
        '    return Status::InternalError("injected FTS seal failure");\n'
        '  }\n',
        'FTS seal-all failure injection')
    source = _replace_once(
        source,
        '  // Convert all postings to bitpacked format.\n',
        '  if (zvec_m4_lock_split_inject_failure("fts_seal_conversion")) {\n'
        '    return Status::InternalError(\n'
        '        "injected FTS seal conversion failure");\n'
        '  }\n'
        '  // Convert all postings to bitpacked format.\n',
        'FTS seal conversion failure injection')
    return _replace_once(
        source,
        '  for (const auto &[name, indexer] : indexers_) {\n'
        '    indexer->reset_side_cfs();\n'
        '  }\n'
        '  for (const auto &[name, _] : indexers_) {',
        '  for (const auto &[name, indexer] : indexers_) {\n'
        '    indexer->reset_side_cfs();\n'
        '  }\n'
        '  zvec_m4_lock_split_emit_segment_test_hook(\n'
        '      "optimize_fts_side_cf_reset_before_drop");\n'
        '  if (zvec_m4_lock_split_inject_failure("fts_side_cf_reset")) {\n'
        '    return Status::InternalError(\n'
        '        "injected FTS side-CF reset failure");\n'
        '  }\n'
        '  for (const auto &[name, _] : indexers_) {',
        'FTS side-CF reset/drop test hook')


def apply_rocksdb_flush_test_hooks(source: str, *, test_hooks: bool = False) -> str:
    """Expose per-CF RocksDB flush boundaries from copied source only."""
    if not test_hooks:
        return source
    source = _replace_once(
        source,
        'namespace zvec {\n',
        'namespace zvec {\n'
        'extern "C" void zvec_m4_lock_split_emit_cf_test_hook(const char *, const char *);\n',
        'RocksDB flush test-hook declaration')
    old_loop = '''  for (const auto &cf : cf_handles_) {
    if (auto s = db_->Flush(flush_opts_, cf); !s.ok()) {
      LOG_ERROR("Failed to flush cf[%s] of RocksDB[%s], code[%d], reason[%s]",
                cf->GetName().c_str(), db_path_.c_str(), s.code(),
                s.ToString().c_str());
      return Status::InternalError();
    }
  }

  if (auto s = db_->Flush(flush_opts_); s.ok()) {
    LOG_DEBUG("Flushed RocksDB[%s]", db_path_.c_str());
    return Status::OK();
  } else {'''
    new_loop = '''  zvec_m4_lock_split_emit_cf_test_hook("rocksdb_flush_begin", db_path_.c_str());
  for (const auto &cf : cf_handles_) {
    if (auto s = db_->Flush(flush_opts_, cf); !s.ok()) {
      LOG_ERROR("Failed to flush cf[%s] of RocksDB[%s], code[%d], reason[%s]",
                cf->GetName().c_str(), db_path_.c_str(), s.code(),
                s.ToString().c_str());
      return Status::InternalError();
    }
    zvec_m4_lock_split_emit_cf_test_hook("rocksdb_flush_cf_after",
                                         cf->GetName().c_str());
  }

  zvec_m4_lock_split_emit_cf_test_hook("rocksdb_flush_cf_loop_complete",
                                       db_path_.c_str());
  if (auto s = db_->Flush(flush_opts_); s.ok()) {
    zvec_m4_lock_split_emit_cf_test_hook("rocksdb_flush_complete",
                                         db_path_.c_str());
    LOG_DEBUG("Flushed RocksDB[%s]", db_path_.c_str());
    return Status::OK();
  } else {'''
    return _replace_once(source, old_loop, new_loop,
                         'RocksDB per-CF flush test hooks')


def apply_benchmark_source(source: str, bench_source: str) -> str:
    """Inject the diagnostic workload fragment into copied collection.cc only."""
    if not bench_source.strip():
        raise ValueError('benchmark source is empty')
    source = _replace_once(
        source,
        '#include <condition_variable>\n',
        '#include <condition_variable>\n#include <algorithm>\n#include <atomic>\n'
        '#include <chrono>\n#include <cstdio>\n#include <filesystem>\n'
        '#include <string>\n#include <thread>\n#include <utility>\n'
        '#include <vector>\n',
        'benchmark includes')
    return _replace_once(
        source,
        '}  // namespace zvec\n',
        bench_source.rstrip() + '\n\n}  // namespace zvec\n',
        'benchmark workload injection')


def vector_lock_plan(base_plan: tuple[tuple[str, str, str, str], ...]
                     ) -> tuple[tuple[str, str, str, str], ...]:
    """Return source inventory with the vector flush schema lock now shared."""
    flush = [i for i, item in enumerate(base_plan) if item[0] == 'flush']
    if len(flush) != 2 or base_plan[flush[0]][1] != 'unique_lock':
        raise ValueError('expected exactly the vector and non-vector flush lock sites')
    changed = list(base_plan)
    changed[flush[0]] = (changed[flush[0]][0], 'shared_lock',
                         changed[flush[0]][2], changed[flush[0]][3])
    return tuple(changed)


def apply_test_hook_compile_flag(args: list[str]) -> list[str]:
    """Add test-hook macro once to a copied collection.cc compile command."""
    flag = '-DM4_NATIVE_LOCK_SPLIT_TEST_HOOKS=1'
    if flag in args:
        raise ValueError('lock-split test-hook compile flag already present')
    return [*args, flag]


def main() -> int:
    raise SystemExit('This module is imported by the isolated native builder; do not run it directly.')


if __name__ == '__main__':
    main()
