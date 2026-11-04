#!/usr/bin/env python3
"""Build an isolated native library with schema-lock owner tracing for M4 Phase 3.

Only copied native sources are instrumented. The profile source and CMake build
are read-only inputs; generated files stay under research/m4-native/.work.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
from native_query_lock_split import (
    apply_benchmark_source,
    apply_fts_column_test_hooks,
    apply_fts_seal_test_hook,
    apply_optimize_read_safe_dump,
    apply_query_lock_split,
    apply_rocksdb_flush_test_hooks,
)

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = ROOT / '.local/native/zvec-native-profile'
QUERY_LOCK_SPLIT_TEST_SOURCE = ROOT / 'research/m4-native/tests/native_query_lock_split_smoke.cc.in'
QUERY_LOCK_SPLIT_BENCH_SOURCE = ROOT / 'research/m4-native/tests/native_query_lock_split_bench.cc.in'
BUILD_ROOT = Path(os.environ.get(
    'M4_NATIVE_BUILD_ROOT', str(ROOT / '.local/native/zvec-native-build'))).resolve()
WORK_ROOT = ROOT / 'research/m4-native/.work/native-schema-lock-trace-v4'
OVERLAY_ROOT = WORK_ROOT / 'source'
BUILD_OUTPUT = WORK_ROOT / 'build'
LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
               'm4-native-schema-lock-trace-v4/out/zvec-prebuilt')
MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                 'native-schema-lock-trace-build-v4.json')
SEAL_STAGE_PROBE = False
CONTEXT_SWITCH_PROBE = False
OPTIMIZE_INNER_STAGE_PROBE = False
QUERY_LOCK_SPLIT_PROBE = False
QUERY_LOCK_SPLIT_TEST_HOOKS = False
QUERY_LOCK_SPLIT_BENCH = False
QUERY_LOCK_SPLIT_TSAN = False

# Source-order inventory from collection.cc. Flush already has precise spans;
# SearchQuery has a wait span and receives an additional shared-lock hold span.
LOCK_PLAN: tuple[tuple[str, str, str, str], ...] = (
    ('close', 'unique_lock', 'close', 'trace'),
    ('close_internal', 'unique_lock', 'close_internal', 'special'),
    ('destroy', 'unique_lock', 'destroy', 'trace'),
    ('flush', 'unique_lock', 'flush_vector', 'existing'),
    ('flush', 'shared_lock', 'flush_shared', 'existing'),
    ('stats', 'shared_lock', 'stats', 'trace'),
    ('schema', 'shared_lock', 'schema', 'trace'),
    ('options', 'shared_lock', 'options', 'trace'),
    ('create_index', 'unique_lock', 'create_index', 'trace'),
    ('drop_index', 'unique_lock', 'drop_index', 'trace'),
    ('optimize', 'unique_lock', 'optimize_seal', 'trace'),
    ('optimize', 'unique_lock', 'optimize_commit', 'trace'),
    ('add_column', 'unique_lock', 'add_column', 'trace'),
    ('drop_column', 'unique_lock', 'drop_column', 'trace'),
    ('alter_column', 'unique_lock', 'alter_column', 'trace'),
    ('write_impl', 'shared_lock', 'write_impl', 'trace'),
    ('delete_', 'shared_lock', 'delete', 'trace'),
    ('delete_by_filter', 'shared_lock', 'delete_by_filter', 'trace'),
    ('query', 'shared_lock', 'query_search', 'query_hold'),
    ('query', 'shared_lock', 'query_multi', 'trace'),
    ('query_result_snapshot_impl', 'shared_lock', 'query_snapshot', 'trace'),
    ('group_by_query', 'shared_lock', 'group_by_query', 'trace'),
    ('fetch', 'shared_lock', 'fetch', 'trace'),
    ('debug_get_hnsw_storage_mode', 'shared_lock', 'debug_hnsw_mode', 'trace'),
    ('create_iterator', 'unique_lock', 'create_iterator', 'trace'),
    ('decrement_active_iterators', 'unique_lock', 'decrement_iterators', 'trace'),
)

LOCK_PLAN_PREFLUSH_PROBE = (
    *LOCK_PLAN[:10],
    ('optimize', 'unique_lock', 'optimize_iterator_guard', 'trace'),
    ('optimize', 'shared_lock', 'optimize_preflush', 'trace'),
    *LOCK_PLAN[10:],
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _method_before(source: str, offset: int) -> str:
    marker = 'CollectionImpl::'
    start = source.rfind(marker, 0, offset)
    if start < 0:
        raise ValueError(f'cannot identify schema-lock method at offset {offset}')
    start += len(marker)
    end = start
    while end < len(source) and (source[end].isalnum() or source[end] == '_'):
        end += 1
    if end == start or source[end:end + 1] != '(':
        raise ValueError(f'cannot parse CollectionImpl method near offset {offset}')
    return source[start:end]


def _lock_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith(('std::unique_lock', 'std::shared_lock')):
        return None
    if '(schema_handle_mtx_);' not in stripped:
        return None
    kind = 'unique_lock' if stripped.startswith('std::unique_lock') else 'shared_lock'
    before_paren = stripped.split('(schema_handle_mtx_);', 1)[0]
    name = before_paren.split()[-1]
    if not name.isidentifier():
        raise ValueError(f'unexpected schema-lock variable declaration: {stripped}')
    return kind, name


def _traced_lock(indent: str, kind: str, name: str, site: str) -> str:
    mode = 'exclusive' if kind == 'unique_lock' else 'shared'
    lock_type = 'std::unique_lock' if kind == 'unique_lock' else 'std::shared_lock'
    return (
        f'{indent}native_timeline::Span {name}_trace_wait(\n'
        f'    "schema_handle_lock_owner", "{site}_{mode}_wait", this);\n'
        f'{indent}{lock_type}<std::shared_mutex> {name}(schema_handle_mtx_);\n'
        f'{indent}{name}_trace_wait.finish();\n'
        f'{indent}[[maybe_unused]] native_timeline::Span {name}_trace_hold(\n'
        f'    "schema_handle_lock_owner", "{site}_{mode}_hold", this);'
    )


def instrument_collection(
    source: str,
    lock_plan: tuple[tuple[str, str, str, str], ...] = LOCK_PLAN,
) -> tuple[str, list[dict[str, str]]]:
    lines = source.splitlines(keepends=True)
    observed: list[tuple[int, str, str, str, str, str]] = []
    offset = 0
    for line_number, line in enumerate(lines):
        parsed = _lock_line(line)
        if parsed is not None:
            kind, name = parsed
            method = _method_before(source, offset)
            observed.append((line_number, method, kind, name, line[:len(line) - len(line.lstrip())], line.rstrip('\n')))
        offset += len(line)

    if len(observed) != len(lock_plan):
        raise ValueError(f'expected {len(lock_plan)} schema locks, found {len(observed)}')

    records: list[dict[str, str]] = []
    for item, (expected_method, expected_kind, site, action) in zip(observed, lock_plan, strict=True):
        line_number, method, kind, name, indent, _original = item
        if (method, kind) != (expected_method, expected_kind):
            raise ValueError(
                f'schema lock inventory changed at line {line_number + 1}: expected '
                f'{expected_method}/{expected_kind}, found {method}/{kind}')
        records.append({'method': method, 'lock_kind': kind, 'site': site, 'action': action})
        if action in ('trace', 'special'):
            lines[line_number] = _traced_lock(indent, kind, name, site) + '\n'

    result = ''.join(lines)
    close_wait = (
        '    LOG_WARN(\n'
        '        "the collection is being destroyed while %d iterator(s) are "\n'
        '        "open; waiting for them to close",\n'
        '        active_iterators_);\n'
        '    iterator_cv_.wait(lock, [&] { return active_iterators_ == 0; });'
    )
    close_wait_trace = (
        '    lock_trace_hold.finish();\n'
        '    LOG_WARN(\n'
        '        "the collection is being destroyed while %d iterator(s) are "\n'
        '        "open; waiting for them to close",\n'
        '        active_iterators_);\n'
        '    native_timeline::Span iterator_wait_span(\n'
        '        "schema_handle_lock_owner", "close_internal_iterator_wait", this);\n'
        '    iterator_cv_.wait(lock, [&] { return active_iterators_ == 0; });\n'
        '    iterator_wait_span.finish();\n'
        '    native_timeline::Span lock_after_wait_span(\n'
        '        "schema_handle_lock_owner", "close_internal_exclusive_hold_after_wait", this);\n'
        '    return close_locked();'
    )
    if result.count(close_wait) != 1:
        raise ValueError('close_internal iterator wait block changed; refusing instrumentation')
    result = result.replace(close_wait, close_wait_trace, 1)

    query_wait_finish = '  schema_lock_span.finish();\n'
    query_hold = (
        query_wait_finish +
        '  native_timeline::Span schema_lock_hold_span(\n'
        '      "schema_handle_lock_owner", "query_search_shared_hold", this, -1, trace_fts);\n'
    )
    if result.count(query_wait_finish) != 1:
        raise ValueError('FTS SearchQuery schema wait span changed; refusing instrumentation')
    result = result.replace(query_wait_finish, query_hold, 1)

    query_checks = (
        '  CHECK_DESTROY_RETURN_STATUS_EXPECTED(destroyed_, false);\n'
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n\n'
        '  auto result = query_unsafe(query);'
    )
    query_checks_trace = (
        '  native_timeline::Span query_state_check_span(\n'
        '      "collection_query_stage", "query_state_check", this, -1, trace_fts);\n'
        '  CHECK_DESTROY_RETURN_STATUS_EXPECTED(destroyed_, false);\n'
        '  CHECK_CLOSED_RETURN_STATUS_EXPECTED(closed_, false);\n'
        '  query_state_check_span.finish();\n\n'
        '  auto result = query_unsafe(query);'
    )
    if result.count(query_checks) != 1:
        raise ValueError('SearchQuery state-check block changed; refusing instrumentation')
    result = result.replace(query_checks, query_checks_trace, 1)

    unsafe_start = (
        'Result<DocPtrList> CollectionImpl::query_unsafe(\n'
        '    const SearchQuery &query) const {\n'
        '  const bool trace_fts = query.target_.get_fts_clause() != nullptr;'
    )
    unsafe_start_trace = (
        'Result<DocPtrList> CollectionImpl::query_unsafe(\n'
        '    const SearchQuery &query) const {\n'
        '  native_timeline::Span query_setup_span(\n'
        '      "collection_query_stage", "query_unsafe_setup", this);\n'
        '  const bool trace_fts = query.target_.get_fts_clause() != nullptr;'
    )
    if result.count(unsafe_start) != 1:
        raise ValueError('query_unsafe start changed; refusing instrumentation')
    result = result.replace(unsafe_start, unsafe_start_trace, 1)

    validate_marker = (
        '  bool need_sanitize = false;\n'
        '  Status s;\n'
        '  {\n'
        '    native_timeline::Span validate_span("collection_query_stage",\n'
        '                                        "query_validate", this, -1, trace_fts);'
    )
    validate_marker_trace = (
        '  bool need_sanitize = false;\n'
        '  Status s;\n'
        '  query_setup_span.finish();\n'
        '  {\n'
        '    native_timeline::Span validate_span("collection_query_stage",\n'
        '                                        "query_validate", this, -1, trace_fts);'
    )
    if result.count(validate_marker) != 1:
        raise ValueError('query_validate boundary changed; refusing instrumentation')
    result = result.replace(validate_marker, validate_marker_trace, 1)

    remaining = sum(_lock_line(line) is not None for line in result.splitlines())
    if remaining != len(lock_plan):
        raise ValueError(f'schema lock declarations changed during instrumentation: {remaining}')
    for _method, kind, site, action in lock_plan:
        if action in ('trace', 'special'):
            mode = 'exclusive' if kind == 'unique_lock' else 'shared'
            if result.count(f'"{site}_{mode}_wait"') != 1 or result.count(
                    f'"{site}_{mode}_hold"') != 1:
                raise ValueError(f'instrumented wait/hold span missing for {site}')
    if result.count('"query_search_shared_hold"') != 1 or result.count(
            '"close_internal_iterator_wait"') != 1:
        raise ValueError('special query/iterator lock spans are incomplete')
    return result, records


def prepare_optimize_read_safe_preflush_probe(source: str) -> str:
    """Move non-vector writer flushing before optimize's exclusive seal phase."""
    old = (
        '  std::vector<Segment::Ptr> persist_segments;\n\n'
        '  // Phase 1: exclusively seal the current writing segment and snapshot\n'
        '  // the persisted segment set.\n'
        '  {\n'
        '    std::unique_lock schema_lock(schema_handle_mtx_);\n'
        '    // Nothing has been built yet, so failing fast leaves nothing to clean.\n'
        '    CHECK_RETURN_STATUS(check_no_active_iterators("optimize"));\n'
        '    std::lock_guard write_lock(write_mtx_);\n'
    )
    new = (
        '  std::vector<Segment::Ptr> persist_segments;\n\n'
        '  // Validate before any flush side effects. maintenance_mtx_ prevents a\n'
        '  // new iterator or schema-maintenance operation until optimize finishes.\n'
        '  {\n'
        '    std::unique_lock iterator_guard(schema_handle_mtx_);\n'
        '    CHECK_RETURN_STATUS(check_no_active_iterators("optimize"));\n'
        '  }\n\n'
        '  // Match the existing reader-safe flush path for non-vector collections. Drain\n'
        '  // existing component I/O before exclusive seal; writes arriving after\n'
        '  // this phase remain covered by the subsequent writer switch.\n'
        '  {\n'
        '    std::shared_lock schema_lock(schema_handle_mtx_);\n'
        '    CHECK_DESTROY_RETURN_STATUS(destroyed_, false);\n'
        '    CHECK_CLOSED_RETURN_STATUS(closed_, false);\n'
        '    if (schema_->vector_fields().empty()) {\n'
        '      std::shared_lock write_lock(write_mtx_);\n'
        '      native_timeline::Span preflush_span(\n'
        '          "collection_optimize_stage", "read_safe_preflush", this);\n'
        '      auto s = flush_unsafe();\n'
        '      preflush_span.finish();\n'
        '      CHECK_RETURN_STATUS(s);\n'
        '    }\n'
        '  }\n\n'
        '  // Phase 1: exclusively seal the current writing segment and snapshot\n'
        '  // the persisted segment set.\n'
        '  {\n'
        '    std::unique_lock schema_lock(schema_handle_mtx_);\n'
        '    std::lock_guard write_lock(write_mtx_);\n'
    )
    if source.count(old) != 1:
        raise ValueError('optimize phase-1 seal block changed; refusing preflush prototype')
    return source.replace(old, new, 1)


def instrument_optimize_switch_stages(source: str) -> str:
    start = """Status CollectionImpl::switch_to_new_segment_for_writing(
    const CollectionSchema::Ptr &schema) {
"""
    if source.count(start) != 1:
        raise ValueError('writer-switch function changed; refusing stage instrumentation')
    source = source.replace(
        start,
        start + """  native_timeline::Span switch_total_span(
      "collection_switch_stage", "total", this);
""",
        1,
    )

    dump_and_add = """  auto s = writing_segment_->dump();
  CHECK_RETURN_STATUS(s);

  s = segment_manager_->add_segment(writing_segment_);
  CHECK_RETURN_STATUS(s);
"""
    dump_and_add_trace = """  native_timeline::Span dump_span(
      "collection_switch_stage", "writer_dump", this);
  auto s = writing_segment_->dump();
  dump_span.finish();
  CHECK_RETURN_STATUS(s);

  native_timeline::Span add_segment_span(
      "collection_switch_stage", "manager_add_segment", this);
  s = segment_manager_->add_segment(writing_segment_);
  add_segment_span.finish();
  CHECK_RETURN_STATUS(s);
"""
    if source.count(dump_and_add) != 1:
        raise ValueError('writer dump/publication block changed; refusing stage instrumentation')
    source = source.replace(dump_and_add, dump_and_add_trace, 1)

    create_writer = """  auto new_segment = Segment::CreateAndOpen(
      path_, schema == nullptr ? *schema_ : *schema, allocate_segment_id(),
      writing_segment_->meta()->max_doc_id() + 1, id_map_, delete_store_,
      version_manager_,
      SegmentOptions{false, options_.enable_mmap_, options_.max_buffer_size_});"""
    create_writer_trace = """  native_timeline::Span create_writer_span(
      "collection_switch_stage", "create_new_writer", this);
""" + create_writer + """
  create_writer_span.finish();"""
    if source.count(create_writer) != 1:
        raise ValueError('new-writer creation block changed; refusing stage instrumentation')
    source = source.replace(create_writer, create_writer_trace, 1)

    transition_metadata = """  auto writing_segment_meta = writing_segment_->meta();
  writing_segment_->remove_writing_forward_block();
  s = version.add_persisted_segment_meta(writing_segment_meta);
  CHECK_RETURN_STATUS(s);

  writing_segment_ = new_segment.value();
  version.reset_writing_segment_meta(writing_segment_->meta());
  version.set_next_segment_id(segment_id_allocator_.load());
"""
    transition_metadata_trace = """  native_timeline::Span metadata_span(
      "collection_switch_stage", "writer_transition_metadata", this);
  auto writing_segment_meta = writing_segment_->meta();
  writing_segment_->remove_writing_forward_block();
  s = version.add_persisted_segment_meta(writing_segment_meta);
  CHECK_RETURN_STATUS(s);

  writing_segment_ = new_segment.value();
  version.reset_writing_segment_meta(writing_segment_->meta());
  version.set_next_segment_id(segment_id_allocator_.load());
  metadata_span.finish();
"""
    if source.count(transition_metadata) != 1:
        raise ValueError('writer transition metadata block changed; refusing stage instrumentation')
    source = source.replace(transition_metadata, transition_metadata_trace, 1)

    version_publish = """  s = version_manager_->apply(version);
  CHECK_RETURN_STATUS(s);
  s = version_manager_->flush();
  CHECK_RETURN_STATUS(s);
"""
    version_publish_trace = """  native_timeline::Span version_apply_span(
      "collection_switch_stage", "version_apply", this);
  s = version_manager_->apply(version);
  version_apply_span.finish();
  CHECK_RETURN_STATUS(s);
  native_timeline::Span version_flush_span(
      "collection_switch_stage", "version_flush", this);
  s = version_manager_->flush();
  version_flush_span.finish();
  CHECK_RETURN_STATUS(s);
"""
    if source.count(version_publish) != 1:
        raise ValueError('writer version publication block changed; refusing stage instrumentation')
    return source.replace(version_publish, version_publish_trace, 1)


def instrument_segment_dump(source: str) -> str:
    include_marker = """#include "segment.h"
"""
    if source.count(include_marker) != 1:
        raise ValueError('segment.cc include block changed; refusing dump-stage instrumentation')
    source = source.replace(
        include_marker,
        include_marker + '#include "db/common/native_timeline.h"' + chr(10),
        1,
    )
    start = source.find('Status SegmentImpl::dump() {')
    next_function = source.find('void SegmentImpl::remove_writing_forward_block()', start)
    if start < 0 or next_function < 0:
        raise ValueError('SegmentImpl::dump boundaries changed; refusing stage instrumentation')
    end = source.rfind(chr(10), start, next_function)
    body = source[start:end]
    body = body.replace(
        'Status SegmentImpl::dump() {' + chr(10),
        'Status SegmentImpl::dump() {' + chr(10)
        + '  native_timeline::Span dump_total_span(' + chr(10)
        + '      "segment_dump_stage", "total", this, static_cast<int64_t>(id()));' + chr(10),
        1,
    )
    flush_call = '  auto s = flush_locked();' + chr(10) + '  CHECK_RETURN_STATUS(s);'
    flush_trace = (
        '  native_timeline::Span flush_span(' + chr(10)
        + '      "segment_dump_stage", "flush_locked", this, static_cast<int64_t>(id()));' + chr(10)
        + '  auto s = flush_locked();' + chr(10)
        + '  flush_span.finish();' + chr(10)
        + '  CHECK_RETURN_STATUS(s);'
    )
    invert_call = '    s = invert_indexers_->seal();' + chr(10) + '    CHECK_RETURN_STATUS(s);'
    invert_trace = (
        '    native_timeline::Span inverted_seal_span(' + chr(10)
        + '        "segment_dump_stage", "inverted_seal", this, static_cast<int64_t>(id()));' + chr(10)
        + '    s = invert_indexers_->seal();' + chr(10)
        + '    inverted_seal_span.finish();' + chr(10)
        + '    CHECK_RETURN_STATUS(s);'
    )
    fts_call = '  s = dump_fts_indexers();' + chr(10) + '  CHECK_RETURN_STATUS(s);'
    fts_trace = (
        '  native_timeline::Span fts_seal_span(' + chr(10)
        + '      "segment_dump_stage", "fts_seal_all", this, static_cast<int64_t>(id()));' + chr(10)
        + '  s = dump_fts_indexers();' + chr(10)
        + '  fts_seal_span.finish();' + chr(10)
        + '  CHECK_RETURN_STATUS(s);'
    )
    for old, new, label in (
        (flush_call, flush_trace, 'flush_locked'),
        (invert_call, invert_trace, 'inverted seal'),
        (fts_call, fts_trace, 'FTS seal'),
    ):
        if body.count(old) != 1:
            raise ValueError(f'SegmentImpl::dump {label} call changed; refusing stage instrumentation')
        body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


def instrument_segment_create(source: str) -> str:
    include = '#include "db/common/native_timeline.h"'
    if include not in source:
        raise ValueError('segment timeline include missing before create instrumentation')
    start = source.find('Result<Segment::Ptr> Segment::CreateAndOpen(')
    end = source.find('\nResult<Segment::Ptr> Segment::Open(', start)
    if start < 0 or end < 0:
        raise ValueError('Segment::CreateAndOpen boundaries changed; refusing nested trace')
    body = source[start:end]
    constructor = '''  auto segment = std::shared_ptr<SegmentImpl>(
      new SegmentImpl(path, schema, SegmentMeta(segment_id), id_map,
                      delete_store, version_manager));'''
    constructor_trace = '''  native_timeline::Span constructor_span(
      "segment_create_stage", "construct_segment_impl", nullptr,
      static_cast<int64_t>(segment_id));
''' + constructor + '''
  constructor_span.finish();'''
    if body.count(constructor) != 1:
        raise ValueError('SegmentImpl construction changed; refusing nested trace')
    body = body.replace(constructor, constructor_trace, 1)
    directory = '''  auto segment_path = FileHelper::MakeSegmentPath(path, segment_id);
  // check or create path
  if (FileHelper::DirectoryExists(segment_path)) {
    return tl::make_unexpected(Status::InternalError(
        "Segment create failed: segment path already exists [", segment_path,
        "]"));
  } else {
    if (!FileHelper::CreateDirectory(segment_path)) {
      return tl::make_unexpected(Status::InternalError(
          "Segment create failed: unable to create segment directory [",
          segment_path, "]"));
    }
  }

  auto s = segment->create(options, min_doc_id);'''
    directory_trace = '''  native_timeline::Span directory_span(
      "segment_create_stage", "prepare_segment_directory", nullptr,
      static_cast<int64_t>(segment_id));
  auto segment_path = FileHelper::MakeSegmentPath(path, segment_id);
  // check or create path
  if (FileHelper::DirectoryExists(segment_path)) {
    return tl::make_unexpected(Status::InternalError(
        "Segment create failed: segment path already exists [", segment_path,
        "]"));
  } else {
    if (!FileHelper::CreateDirectory(segment_path)) {
      return tl::make_unexpected(Status::InternalError(
          "Segment create failed: unable to create segment directory [",
          segment_path, "]"));
    }
  }
  directory_span.finish();

  native_timeline::Span initialize_span(
      "segment_create_stage", "initialize_segment", nullptr,
      static_cast<int64_t>(segment_id));
  auto s = segment->create(options, min_doc_id);
  initialize_span.finish();'''
    if body.count(directory) != 1:
        raise ValueError('Segment directory/create block changed; refusing nested trace')
    body = body.replace(directory, directory_trace, 1)
    source = source[:start] + body + source[end:]

    start = source.find('Status SegmentImpl::create(const SegmentOptions &options, uint64_t min_doc_id) {')
    end = source.find('\nStatus SegmentImpl::close()', start)
    if start < 0 or end < 0:
        raise ValueError('SegmentImpl::create boundaries changed; refusing nested trace')
    body = source[start:end]
    scalar = '''  auto s = load_scalar_index_blocks(true);
  CHECK_RETURN_STATUS(s);

  s = open_fts_indexers(true);
  CHECK_RETURN_STATUS(s);'''
    scalar_trace = '''  native_timeline::Span scalar_span(
      "new_writer_init_stage", "create_scalar_indexes", this,
      static_cast<int64_t>(id()));
  auto s = load_scalar_index_blocks(true);
  scalar_span.finish();
  CHECK_RETURN_STATUS(s);

  native_timeline::Span fts_span(
      "new_writer_init_stage", "create_fts_indexers", this,
      static_cast<int64_t>(id()));
  s = open_fts_indexers(true);
  fts_span.finish();
  CHECK_RETURN_STATUS(s);'''
    if body.count(scalar) != 1:
        raise ValueError('SegmentImpl::create index initialization changed; refusing nested trace')
    body = body.replace(scalar, scalar_trace, 1)
    return source[:start] + body + source[end:]


def instrument_inverted_indexer(source: str) -> str:
    include = '#include "inverted_indexer.h"\n'
    if source.count(include) != 1:
        raise ValueError('InvertedIndexer include changed; refusing nested trace')
    source = source.replace(include, include + '#include "db/common/native_timeline.h"\n', 1)

    start = source.find('Status InvertedIndexer::open(bool create_dir_if_missing, bool read_only) {')
    end = source.find('\nStatus InvertedIndexer::flush()', start)
    if start < 0 or end < 0:
        raise ValueError('InvertedIndexer::open boundaries changed; refusing nested trace')
    body = source[start:end]
    open_call = '''    s = rocksdb_context_.open(working_dir_, cf_names, read_only,
                              std::make_shared<InvertedRocksdbValueMerger>());'''
    open_trace = '''    native_timeline::Span db_span(
        "new_writer_init_stage", "open_inverted_rocksdb", this);
''' + open_call + '''
    db_span.finish();'''
    create_call = '''    s = rocksdb_context_.create(working_dir_, cf_names,
                                std::make_shared<InvertedRocksdbValueMerger>());'''
    create_trace = '''    native_timeline::Span db_span(
        "new_writer_init_stage", "create_inverted_rocksdb", this);
''' + create_call + '''
    db_span.finish();'''
    indexer_call = '''    auto column_indexer = InvertedColumnIndexer::CreateAndOpen(
        collection_name_, field, rocksdb_context_, read_only);'''
    indexer_trace = '''    std::string column_stage = "open_inverted_column_" + field.name();
    native_timeline::Span column_span(
        "new_writer_init_stage", column_stage.c_str(), this);
''' + indexer_call + '''
    column_span.finish();'''
    for old, new, label in (
        (open_call, open_trace, 'RocksDB open'),
        (create_call, create_trace, 'RocksDB create'),
        (indexer_call, indexer_trace, 'column-indexer open'),
    ):
        if body.count(old) != 1:
            raise ValueError(f'InvertedIndexer {label} block changed; refusing nested trace')
        body = body.replace(old, new, 1)
    source = source[:start] + body + source[end:]

    start = source.find('Status InvertedIndexer::seal() {')
    end = source.find('\nStatus InvertedIndexer::create_column_indexer(', start)
    if start < 0 or end < 0:
        raise ValueError('InvertedIndexer::seal boundaries changed; refusing nested trace')
    body = source[start:end]
    column_call = '''  for (const auto &[_, indexer] : indexers_) {
    if (indexer->is_sealed()) {
      continue;
    }
    if (s = indexer->seal(); !s.ok()) {'''
    column_trace = '''  for (const auto &[field_name, indexer] : indexers_) {
    if (indexer->is_sealed()) {
      continue;
    }
    std::string column_stage = "seal_inverted_column_" + field_name;
    native_timeline::Span column_span(
        "inverted_seal_stage", column_stage.c_str(), this);
    s = indexer->seal();
    column_span.finish();
    if (!s.ok()) {'''
    flush_call = '''  if (s = flush(); !s.ok()) {'''
    flush_trace = '''  native_timeline::Span flush_span(
      "inverted_seal_stage", "flush_after_column_seal", this);
  s = flush();
  flush_span.finish();
  if (!s.ok()) {'''
    compact_call = '''  if (s = rocksdb_context_.compact(); s.ok()) {'''
    compact_trace = '''  native_timeline::Span compact_span(
      "inverted_seal_stage", "rocksdb_compact_all", this);
  s = rocksdb_context_.compact();
  compact_span.finish();
  if (s.ok()) {'''
    for old, new, label in (
        (column_call, column_trace, 'column seal loop'),
        (flush_call, flush_trace, 'post-seal flush'),
        (compact_call, compact_trace, 'compaction call'),
    ):
        if body.count(old) != 1:
            raise ValueError(f'InvertedIndexer {label} changed; refusing nested trace')
        body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


def instrument_inverted_column_seal(source: str) -> str:
    include = '#include "inverted_column_indexer.h"\n'
    if source.count(include) != 1:
        raise ValueError('InvertedColumnIndexer include changed; refusing nested trace')
    source = source.replace(include, include + '#include "db/common/native_timeline.h"\n', 1)
    start = source.find('Status InvertedColumnIndexer::seal() {')
    namespace_end = source.find('}  // namespace zvec', start)
    end = source.rfind('\n}', start, namespace_end)
    if start < 0 or namespace_end < 0 or end < 0:
        raise ValueError('InvertedColumnIndexer::seal boundaries changed; refusing nested trace')
    body = source[start:end]
    special = '''  Status status = flush_special_values();'''
    special_trace = '''  std::string special_stage = "flush_special_values_" + field_.name();
  native_timeline::Span special_span(
      "inverted_column_seal_stage", special_stage.c_str(), this);
  Status status = flush_special_values();
  special_span.finish();'''
    stats = '''    status = generate_statistical_indexes();'''
    stats_trace = '''    std::string stats_stage = "generate_statistics_" + field_.name();
    native_timeline::Span stats_span(
        "inverted_column_seal_stage", stats_stage.c_str(), this);
    status = generate_statistical_indexes();
    stats_span.finish();'''
    marker = '''  auto rs = ctx_.db_->Put(ctx_.write_opts_, key_sealed(), "sealed");'''
    marker_trace = '''  std::string marker_stage = "write_sealed_marker_" + field_.name();
  native_timeline::Span marker_span(
      "inverted_column_seal_stage", marker_stage.c_str(), this);
  auto rs = ctx_.db_->Put(ctx_.write_opts_, key_sealed(), "sealed");
  marker_span.finish();'''
    for old, new, label in (
        (special, special_trace, 'special-value flush'),
        (stats, stats_trace, 'range-statistics generation'),
        (marker, marker_trace, 'sealed marker write'),
    ):
        if body.count(old) != 1:
            raise ValueError(f'InvertedColumnIndexer {label} changed; refusing nested trace')
        body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


def instrument_rocksdb_context(source: str) -> str:
    include = '#include "rocksdb_context.h"\n'
    if source.count(include) != 1:
        raise ValueError('RocksdbContext include changed; refusing nested trace')
    source = source.replace(include, include + '#include "db/common/native_timeline.h"\n', 1)

    start = source.find('Status RocksdbContext::create(Args args) {')
    end = source.find('\nStatus RocksdbContext::create(\n    const std::string &db_path, const std::vector<std::string> &column_names,', start)
    if start < 0 or end < 0:
        raise ValueError('RocksdbContext::create boundaries changed; refusing nested trace')
    body = source[start:end]
    db_open = '''  rocksdb::DB *db;
  rocksdb::Status s = rocksdb::DB::Open(create_opts_, args.db_path, &db);'''
    db_open_trace = '''  rocksdb::DB *db;
  native_timeline::Span db_open_span(
      "rocksdb_create_stage", "db_open", this);
  rocksdb::Status s = rocksdb::DB::Open(create_opts_, args.db_path, &db);
  db_open_span.finish();'''
    cf_create = '''    s = db->CreateColumnFamily(cf_options, column_name, &cf_handle);'''
    cf_create_trace = '''    std::string cf_stage = "create_cf_" + column_name;
    native_timeline::Span cf_span(
        "rocksdb_create_stage", cf_stage.c_str(), this);
    s = db->CreateColumnFamily(cf_options, column_name, &cf_handle);
    cf_span.finish();'''
    for old, new, label in ((db_open, db_open_trace, 'DB open'),
                            (cf_create, cf_create_trace, 'column-family creation')):
        if body.count(old) != 1:
            raise ValueError(f'RocksdbContext {label} changed; refusing nested trace')
        body = body.replace(old, new, 1)
    source = source[:start] + body + source[end:]

    start = source.find('Status RocksdbContext::flush_unlocked() {')
    end = source.find('\nStatus RocksdbContext::flush() {', start)
    if start < 0 or end < 0:
        raise ValueError('RocksdbContext::flush_unlocked boundaries changed; refusing nested trace')
    body = source[start:end]
    flush = '''  for (const auto &cf : cf_handles_) {
    if (auto s = db_->Flush(flush_opts_, cf); !s.ok()) {'''
    flush_trace = '''  int64_t cf_index = 0;
  for (const auto &cf : cf_handles_) {
    std::string cf_stage = "flush_cf_" + cf->GetName();
    native_timeline::Span cf_span(
        "rocksdb_flush_stage", cf_stage.c_str(), this, cf_index++);
    auto s = db_->Flush(flush_opts_, cf);
    cf_span.finish();
    if (!s.ok()) {'''
    db_flush = '''  if (auto s = db_->Flush(flush_opts_); s.ok()) {'''
    db_flush_trace = '''  native_timeline::Span db_flush_span(
      "rocksdb_flush_stage", "flush_db_wide", this);
  auto db_flush_status = db_->Flush(flush_opts_);
  db_flush_span.finish();
  if (db_flush_status.ok()) {'''
    for old, new, label in ((flush, flush_trace, 'per-CF flush loop'),
                            (db_flush, db_flush_trace, 'DB-wide flush')):
        if body.count(old) != 1:
            raise ValueError(f'RocksdbContext {label} changed; refusing nested trace')
        body = body.replace(old, new, 1)
    flush_error = '''    LOG_ERROR("Failed to flush Rocksdb[%s], code[%d], reason[%s]",
              db_path_.c_str(), s.code(), s.ToString().c_str());'''
    flush_error_trace = '''    LOG_ERROR("Failed to flush Rocksdb[%s], code[%d], reason[%s]",
              db_path_.c_str(), db_flush_status.code(),
              db_flush_status.ToString().c_str());'''
    if body.count(flush_error) != 1:
        raise ValueError('RocksdbContext DB-wide flush error path changed; refusing nested trace')
    body = body.replace(flush_error, flush_error_trace, 1)
    source = source[:start] + body + source[end:]

    start = source.find('Status RocksdbContext::compact() {')
    end = source.find('\nsize_t RocksdbContext::sst_file_size()', start)
    if start < 0 or end < 0:
        raise ValueError('RocksdbContext::compact boundaries changed; refusing nested trace')
    body = source[start:end]
    compact = '''  for (auto cf : cf_handles_) {
    auto s = db_->CompactRange(compact_range_opts_, cf, nullptr, nullptr);'''
    compact_trace = '''  int64_t cf_index = 0;
  for (auto cf : cf_handles_) {
    std::string cf_stage = "compact_cf_" + cf->GetName();
    native_timeline::Span cf_span(
        "rocksdb_compact_stage", cf_stage.c_str(), this, cf_index++);
    auto s = db_->CompactRange(compact_range_opts_, cf, nullptr, nullptr);
    cf_span.finish();'''
    db_compact = '''  auto s = db_->CompactRange(compact_range_opts_, nullptr, nullptr);'''
    db_compact_trace = '''  native_timeline::Span db_span(
      "rocksdb_compact_stage", "compact_db_wide", this);
  auto s = db_->CompactRange(compact_range_opts_, nullptr, nullptr);
  db_span.finish();'''
    for old, new, label in ((compact, compact_trace, 'per-CF compaction loop'),
                            (db_compact, db_compact_trace, 'DB-wide compaction')):
        if body.count(old) != 1:
            raise ValueError(f'RocksdbContext {label} changed; refusing nested trace')
        body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


def instrument_fts_indexer(source: str) -> str:
    include = '#include "fts_indexer.h"\n'
    if source.count(include) != 1:
        raise ValueError('FtsIndexer include changed; refusing nested trace')
    source = source.replace(include, include + '#include "db/common/native_timeline.h"\n', 1)
    start = source.find('Status FtsIndexer::open(const FieldSchemaPtrList &fts_fields, bool create,')
    end = source.find('\nfts::FtsColumnIndexerPtr FtsIndexer::get(', start)
    if start < 0 or end < 0:
        raise ValueError('FtsIndexer::open boundaries changed; refusing nested trace')
    body = source[start:end]
    create_call = '''    s = fts_ctx_->create(RocksdbContext::Args{working_dir_, cf_names, nullptr,
                                              per_cf_merge_ops, true});'''
    create_trace = '''    native_timeline::Span db_span(
        "new_writer_init_stage", "create_fts_rocksdb", this);
''' + create_call + '''
    db_span.finish();'''
    open_call = '''    s = fts_ctx_->open(
        RocksdbContext::Args{working_dir_, {}, nullptr, per_cf_merge_ops, true},
        read_only);'''
    open_trace = '''    native_timeline::Span db_span(
        "new_writer_init_stage", "open_fts_rocksdb", this);
''' + open_call + '''
    db_span.finish();'''
    column_call = '''    auto ret = indexer->open(field, fts_ctx_.get(), postings_cf, positions_cf,
                             term_freq_cf, max_tf_cf, doc_len_cf, stat_cf);'''
    column_trace = '''    std::string column_stage = "open_fts_column_" + name;
    native_timeline::Span column_span(
        "new_writer_init_stage", column_stage.c_str(), this);
''' + column_call + '''
    column_span.finish();'''
    for old, new, label in ((create_call, create_trace, 'RocksDB create'),
                            (open_call, open_trace, 'RocksDB open'),
                            (column_call, column_trace, 'column-indexer open')):
        if body.count(old) != 1:
            raise ValueError(f'FtsIndexer {label} block changed; refusing nested trace')
        body = body.replace(old, new, 1)
    return source[:start] + body + source[end:]


def instrument_timeline_header(source: str, context_switch_probe: bool = False) -> str:
    include_marker = '#include <cstdlib>\n'
    if source.count(include_marker) != 1:
        raise ValueError('native_timeline include block changed; refusing instrumentation')
    includes = '#include <cerrno>\n#include <fcntl.h>\n'
    if context_switch_probe:
        includes += '#include <sys/resource.h>\n#include <cstring>\n'
    source = source.replace(include_marker, include_marker + includes, 1)

    class_marker = 'class Span {\n public:\n'
    helper = r'''inline int trace_output_fd() noexcept {
  static const int fd = []() noexcept {
    const char *path = std::getenv("M4_NATIVE_PROFILE_LOG_PATH");
    if (path == nullptr || path[0] == '\0') {
      return STDERR_FILENO;
    }
    const int opened = ::open(path, O_WRONLY | O_CREAT | O_EXCL | O_APPEND | O_CLOEXEC,
                              0600);
    if (opened < 0) {
      constexpr char error[] = "NATIVE_PROFILE_ERROR trace_file_open_failed\n";
      (void)::write(STDERR_FILENO, error, sizeof(error) - 1);
    }
    return opened;
  }();
  return fd;
}

class Span {
 public:
'''
    if source.count(class_marker) != 2:
        raise ValueError('native_timeline platform-specific Span declarations changed; refusing instrumentation')
    if context_switch_probe:
        context_helper = r'''inline bool context_switch_probe_enabled(const char *event,
                                        const char *stage) noexcept {
  return (std::strcmp(event, "collection_query_stage") == 0 &&
          std::strcmp(stage, "schema_handle_lock") == 0) ||
         (std::strcmp(event, "collection_flush_stage") == 0 &&
          std::strcmp(stage, "schema_handle_lock_held") == 0);
}

'''
        helper = helper.replace(class_marker, context_helper + class_marker, 1)
        constructor_old = (
            '      wall_start_ns_ = clock_ns(CLOCK_MONOTONIC);\n'
            '      cpu_start_ns_ = clock_ns(CLOCK_THREAD_CPUTIME_ID);\n'
            '      thread_id_ = static_cast<uint64_t>(::syscall(SYS_gettid));'
        )
        constructor_new = (
            '      thread_id_ = static_cast<uint64_t>(::syscall(SYS_gettid));\n'
            '      context_switch_probe_ = context_switch_probe_enabled(event_, stage_);\n'
            '      if (context_switch_probe_) {\n'
            '        rusage usage{};\n'
            '        if (::getrusage(RUSAGE_THREAD, &usage) == 0) {\n'
            '          context_switch_start_nvcsw_ = usage.ru_nvcsw;\n'
            '          context_switch_start_nivcsw_ = usage.ru_nivcsw;\n'
            '          context_switch_start_valid_ = true;\n'
            '        }\n'
            '      }\n'
            '      wall_start_ns_ = clock_ns(CLOCK_MONOTONIC);\n'
            '      cpu_start_ns_ = clock_ns(CLOCK_THREAD_CPUTIME_ID);'
        )
        if source.count(constructor_old) != 1:
            raise ValueError('native_timeline Span constructor changed; refusing context-switch instrumentation')
        source = source.replace(constructor_old, constructor_new, 1)
        fields_old = '  uint64_t thread_id_{0};'
        fields_new = (
            fields_old + '\n'
            '  bool context_switch_probe_{false};\n'
            '  bool context_switch_start_valid_{false};\n'
            '  long context_switch_start_nvcsw_{0};\n'
            '  long context_switch_start_nivcsw_{0};'
        )
        if source.count(fields_old) != 1:
            raise ValueError('native_timeline Span fields changed; refusing context-switch instrumentation')
        source = source.replace(fields_old, fields_new, 1)

    source = source.replace(class_marker, helper, 1)

    old_output = r'''    std::fprintf(
        stderr,
        "NATIVE_PROFILE event=%s stage=%s object_id=%p item_id=%lld "
        "thread_id=%llu wall_start_ns=%llu wall_end_ns=%llu wall_ns=%llu "
        "thread_cpu_ns=%llu clock_ok=%d\n",
        event_, stage_, object_id_, static_cast<long long>(item_id_),
        static_cast<unsigned long long>(thread_id_),
        static_cast<unsigned long long>(wall_start_ns_),
        static_cast<unsigned long long>(wall_end_ns),
        static_cast<unsigned long long>(wall_ns),
        static_cast<unsigned long long>(thread_cpu_ns), clock_ok ? 1 : 0);
    std::fflush(stderr);'''
    new_output = r'''    char line[512];
    const int length = std::snprintf(
        line, sizeof(line),
        "NATIVE_PROFILE event=%s stage=%s object_id=%p item_id=%lld "
        "thread_id=%llu wall_start_ns=%llu wall_end_ns=%llu wall_ns=%llu "
        "thread_cpu_ns=%llu clock_ok=%d\n",
        event_, stage_, object_id_, static_cast<long long>(item_id_),
        static_cast<unsigned long long>(thread_id_),
        static_cast<unsigned long long>(wall_start_ns_),
        static_cast<unsigned long long>(wall_end_ns),
        static_cast<unsigned long long>(wall_ns),
        static_cast<unsigned long long>(thread_cpu_ns), clock_ok ? 1 : 0);
    const int fd = trace_output_fd();
    if (length <= 0 || static_cast<size_t>(length) >= sizeof(line) || fd < 0) {
      return;
    }
    ssize_t written;
    do {
      written = ::write(fd, line, static_cast<size_t>(length));
    } while (written < 0 && errno == EINTR);'''
    if context_switch_probe:
        new_output = r'''    long context_nvcsw = -1;
    long context_nivcsw = -1;
    int context_probe_status = context_switch_probe_ ? -1 : 0;
    if (context_switch_probe_ && context_switch_start_valid_) {
      rusage usage{};
      if (::getrusage(RUSAGE_THREAD, &usage) == 0) {
        context_nvcsw = usage.ru_nvcsw - context_switch_start_nvcsw_;
        context_nivcsw = usage.ru_nivcsw - context_switch_start_nivcsw_;
        context_probe_status = 1;
      }
    }
    char line[512];
    const int length = std::snprintf(
        line, sizeof(line),
        "NATIVE_PROFILE event=%s stage=%s object_id=%p item_id=%lld "
        "thread_id=%llu wall_start_ns=%llu wall_end_ns=%llu wall_ns=%llu "
        "thread_cpu_ns=%llu clock_ok=%d ctx_probe=%d ctx_nvcsw=%ld ctx_nivcsw=%ld\n",
        event_, stage_, object_id_, static_cast<long long>(item_id_),
        static_cast<unsigned long long>(thread_id_),
        static_cast<unsigned long long>(wall_start_ns_),
        static_cast<unsigned long long>(wall_end_ns),
        static_cast<unsigned long long>(wall_ns),
        static_cast<unsigned long long>(thread_cpu_ns), clock_ok ? 1 : 0,
        context_probe_status, context_nvcsw, context_nivcsw);
    const int fd = trace_output_fd();
    if (length <= 0 || static_cast<size_t>(length) >= sizeof(line) || fd < 0) {
      return;
    }
    ssize_t written;
    do {
      written = ::write(fd, line, static_cast<size_t>(length));
    } while (written < 0 && errno == EINTR);'''
    if source.count(old_output) != 1:
        raise ValueError('native_timeline output block changed; refusing instrumentation')
    return source.replace(old_output, new_output, 1)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_overlay(
    preflush_probe: bool = False,
    seal_stage_probe: bool = False,
    context_switch_probe: bool = False,
    optimize_inner_stage_probe: bool = False,
    query_lock_split_probe: bool = False,
    query_lock_split_test_hooks: bool = False,
    query_lock_split_bench: bool = False,
) -> tuple[list[dict[str, str]], dict[str, str | None]]:
    global SEAL_STAGE_PROBE, CONTEXT_SWITCH_PROBE, OPTIMIZE_INNER_STAGE_PROBE
    global QUERY_LOCK_SPLIT_PROBE, QUERY_LOCK_SPLIT_TEST_HOOKS
    global QUERY_LOCK_SPLIT_BENCH, QUERY_LOCK_SPLIT_TSAN
    if query_lock_split_probe and (seal_stage_probe or context_switch_probe or optimize_inner_stage_probe):
        raise ValueError('query-lock-split and optimize-stage probes are separate build variants')
    if preflush_probe and (seal_stage_probe or context_switch_probe or optimize_inner_stage_probe or query_lock_split_probe):
        raise ValueError('preflush and other optimize/query-lock probes are separate build variants')
    if context_switch_probe and not seal_stage_probe:
        raise ValueError('context-switch probe requires optimize-seal stage instrumentation')
    if optimize_inner_stage_probe and not seal_stage_probe:
        raise ValueError('nested optimize stages require optimize-seal stage instrumentation')
    if query_lock_split_test_hooks and not query_lock_split_probe:
        raise ValueError('query-lock-split test hooks require the lock-split prototype')
    SEAL_STAGE_PROBE = seal_stage_probe
    CONTEXT_SWITCH_PROBE = context_switch_probe
    OPTIMIZE_INNER_STAGE_PROBE = optimize_inner_stage_probe
    QUERY_LOCK_SPLIT_PROBE = query_lock_split_probe
    QUERY_LOCK_SPLIT_TEST_HOOKS = query_lock_split_test_hooks
    QUERY_LOCK_SPLIT_BENCH = query_lock_split_bench
    if not SOURCE_ROOT.is_dir() or not BUILD_ROOT.is_dir():
        raise FileNotFoundError('expected native profile source and build trees are unavailable')
    if WORK_ROOT.exists():
        raise FileExistsError(f'refusing to overwrite isolated build: {WORK_ROOT}')
    WORK_ROOT.mkdir(parents=True, mode=0o700)
    source_collection = SOURCE_ROOT / 'src/db/collection.cc'
    source_header = SOURCE_ROOT / 'src/db/common/native_timeline.h'
    source_fts = SOURCE_ROOT / 'src/db/sqlengine/planner/fts_recall_node.cc'
    overlay_collection = OVERLAY_ROOT / 'src/db/collection.cc'
    overlay_header = OVERLAY_ROOT / 'src/db/common/native_timeline.h'
    overlay_fts = OVERLAY_ROOT / 'src/db/sqlengine/planner/fts_recall_node.cc'
    overlay_collection.parent.mkdir(parents=True, mode=0o700)
    overlay_header.parent.mkdir(parents=True, mode=0o700)
    overlay_fts.parent.mkdir(parents=True, mode=0o700)
    original_collection = source_collection.read_bytes()
    original_header = source_header.read_bytes()
    original_fts = source_fts.read_bytes()
    collection_text = original_collection.decode('utf-8')
    lock_plan = LOCK_PLAN
    if preflush_probe:
        collection_text = prepare_optimize_read_safe_preflush_probe(collection_text)
        lock_plan = LOCK_PLAN_PREFLUSH_PROBE
    instrumented_collection, lock_records = instrument_collection(
        collection_text, lock_plan)
    query_lock_split_test_source = None
    if query_lock_split_test_hooks:
        query_lock_split_test_source = QUERY_LOCK_SPLIT_TEST_SOURCE.read_text(
            encoding='utf-8')
    if query_lock_split_probe:
        instrumented_collection = apply_query_lock_split(
            instrumented_collection, test_hooks=query_lock_split_test_hooks,
            test_hook_source=query_lock_split_test_source)
        for record in lock_records:
            if record['site'] == 'flush_vector':
                if record['lock_kind'] != 'unique_lock':
                    raise ValueError('unexpected original vector-flush lock mode')
                record['lock_kind'] = 'shared_lock'
    inner_source_records: dict[str, tuple[bytes, str]] = {}
    if seal_stage_probe:
        instrumented_collection = instrument_optimize_switch_stages(instrumented_collection)
        source_segment = SOURCE_ROOT / 'src/db/index/segment/segment.cc'
        overlay_segment = OVERLAY_ROOT / 'src/db/index/segment/segment.cc'
        original_segment = source_segment.read_bytes()
        instrumented_segment = instrument_segment_dump(original_segment.decode('utf-8'))
        if optimize_inner_stage_probe:
            instrumented_segment = instrument_segment_create(instrumented_segment)
        overlay_segment.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        overlay_segment.write_text(instrumented_segment, encoding='utf-8')
    elif query_lock_split_probe:
        source_segment = SOURCE_ROOT / 'src/db/index/segment/segment.cc'
        overlay_segment = OVERLAY_ROOT / 'src/db/index/segment/segment.cc'
        original_segment = source_segment.read_bytes()
        instrumented_segment = apply_optimize_read_safe_dump(
            original_segment.decode('utf-8'),
            test_hooks=query_lock_split_test_hooks)
        overlay_segment.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        overlay_segment.write_text(instrumented_segment, encoding='utf-8')
    else:
        original_segment = None
        instrumented_segment = None
    if query_lock_split_test_hooks:
        source_fts_indexer = SOURCE_ROOT / 'src/db/index/column/fts_column/fts_indexer.cc'
        overlay_fts_indexer = OVERLAY_ROOT / 'src/db/index/column/fts_column/fts_indexer.cc'
        original_fts_indexer = source_fts_indexer.read_bytes()
        instrumented_fts_indexer = apply_fts_seal_test_hook(
            original_fts_indexer.decode('utf-8'), test_hooks=True)
        overlay_fts_indexer.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        overlay_fts_indexer.write_text(instrumented_fts_indexer, encoding='utf-8')
        source_fts_column_indexer = SOURCE_ROOT / 'src/db/index/column/fts_column/fts_column_indexer.cc'
        overlay_fts_column_indexer = OVERLAY_ROOT / 'src/db/index/column/fts_column/fts_column_indexer.cc'
        original_fts_column_indexer = source_fts_column_indexer.read_bytes()
        instrumented_fts_column_indexer = apply_fts_column_test_hooks(
            original_fts_column_indexer.decode('utf-8'), test_hooks=True)
        overlay_fts_column_indexer.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        overlay_fts_column_indexer.write_text(
            instrumented_fts_column_indexer, encoding='utf-8')
        source_rocksdb_context = SOURCE_ROOT / 'src/db/common/rocksdb_context.cc'
        overlay_rocksdb_context = OVERLAY_ROOT / 'src/db/common/rocksdb_context.cc'
        original_rocksdb_context = source_rocksdb_context.read_bytes()
        instrumented_rocksdb_context = apply_rocksdb_flush_test_hooks(
            original_rocksdb_context.decode('utf-8'), test_hooks=True)
        overlay_rocksdb_context.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        overlay_rocksdb_context.write_text(
            instrumented_rocksdb_context, encoding='utf-8')
    else:
        original_fts_indexer = None
        instrumented_fts_indexer = None
        original_fts_column_indexer = None
        instrumented_fts_column_indexer = None
        original_rocksdb_context = None
        instrumented_rocksdb_context = None
    if optimize_inner_stage_probe:
        transforms = {
            'db/index/column/inverted_column/inverted_indexer.cc': instrument_inverted_indexer,
            'db/index/column/inverted_column/inverted_column_indexer_write.cc': instrument_inverted_column_seal,
            'db/common/rocksdb_context.cc': instrument_rocksdb_context,
            'db/index/column/fts_column/fts_indexer.cc': instrument_fts_indexer,
        }
        for relative, transform in transforms.items():
            source_path = SOURCE_ROOT / 'src' / relative
            overlay_path = OVERLAY_ROOT / 'src' / relative
            original = source_path.read_bytes()
            instrumented = transform(original.decode('utf-8'))
            overlay_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            overlay_path.write_text(instrumented, encoding='utf-8')
            inner_source_records[relative] = (original, instrumented)
    query_lock_split_bench_source = None
    if query_lock_split_bench:
        query_lock_split_bench_source = QUERY_LOCK_SPLIT_BENCH_SOURCE.read_text(
            encoding='utf-8')
        instrumented_collection = apply_benchmark_source(
            instrumented_collection, query_lock_split_bench_source)
    instrumented_header = instrument_timeline_header(
        original_header.decode('utf-8'), context_switch_probe=context_switch_probe)
    overlay_collection.write_text(instrumented_collection, encoding='utf-8')
    overlay_header.write_text(instrumented_header, encoding='utf-8')
    overlay_fts.write_bytes(original_fts)
    hashes = {
        'source_collection_sha256': _sha256_bytes(original_collection),
        'source_timeline_header_sha256': _sha256_bytes(original_header),
        'source_fts_recall_node_sha256': _sha256_bytes(original_fts),
        'overlay_collection_sha256': _sha256_bytes(instrumented_collection.encode()),
        'source_segment_sha256': (
            _sha256_bytes(original_segment) if original_segment is not None else None),
        'overlay_segment_sha256': (
            _sha256_bytes(instrumented_segment.encode())
            if instrumented_segment is not None else None),
        'source_fts_indexer_sha256': (
            _sha256_bytes(original_fts_indexer)
            if original_fts_indexer is not None else None),
        'overlay_fts_indexer_sha256': (
            _sha256_bytes(instrumented_fts_indexer.encode())
            if instrumented_fts_indexer is not None else None),
        'source_fts_column_indexer_sha256': (
            _sha256_bytes(original_fts_column_indexer)
            if original_fts_column_indexer is not None else None),
        'overlay_fts_column_indexer_sha256': (
            _sha256_bytes(instrumented_fts_column_indexer.encode())
            if instrumented_fts_column_indexer is not None else None),
        'source_rocksdb_context_sha256': (
            _sha256_bytes(original_rocksdb_context)
            if original_rocksdb_context is not None else None),
        'overlay_rocksdb_context_sha256': (
            _sha256_bytes(instrumented_rocksdb_context.encode())
            if instrumented_rocksdb_context is not None else None),
        'query_lock_split_test_source_sha256': (
            _sha256_bytes(query_lock_split_test_source.encode())
            if query_lock_split_test_source is not None else None),
        'query_lock_split_bench_source_sha256': (
            _sha256_bytes(query_lock_split_bench_source.encode())
            if query_lock_split_bench_source is not None else None),
        'overlay_timeline_header_sha256': _sha256_bytes(instrumented_header.encode()),
        'overlay_fts_recall_node_sha256': _sha256_bytes(original_fts),
        'optimize_read_safe_preflush_probe': 'enabled' if preflush_probe else 'disabled',
        'optimize_seal_stage_probe': 'enabled' if seal_stage_probe else 'disabled',
        'optimize_inner_stage_probe': 'enabled' if optimize_inner_stage_probe else 'disabled',
        'context_switch_probe': 'enabled' if context_switch_probe else 'disabled',
        'query_lock_split_probe': 'enabled' if query_lock_split_probe else 'disabled',
        'query_lock_split_test_hooks': 'enabled' if query_lock_split_test_hooks else 'disabled',
        'query_lock_split_bench': 'enabled' if query_lock_split_bench else 'disabled',
    }
    if seal_stage_probe:
        assert original_segment is not None and instrumented_segment is not None
        hashes.update({
            'source_segment_sha256': _sha256_bytes(original_segment),
            'overlay_segment_sha256': _sha256_bytes(instrumented_segment.encode()),
        })
    for relative, (original, instrumented) in inner_source_records.items():
        key = relative.replace('/', '_').replace('.', '_')
        hashes[f'source_{key}_sha256'] = _sha256_bytes(original)
        hashes[f'overlay_{key}_sha256'] = _sha256_bytes(instrumented.encode())
    return lock_records, hashes


def smoke_trace_writer() -> dict[str, Any]:
    BUILD_OUTPUT.mkdir(parents=True, mode=0o700, exist_ok=True)
    source = BUILD_OUTPUT / 'trace_file_smoke.cc'
    binary = BUILD_OUTPUT / 'trace_file_smoke'
    trace = BUILD_OUTPUT / 'trace_file_smoke.jsonl'
    source.write_text(
        '#include "db/common/native_timeline.h"' + chr(10) +
        'int main() { zvec::native_timeline::Span span("smoke", "single_write"); '
        'span.finish(); return 0; }' + chr(10), encoding='utf-8')
    subprocess.run([
        shutil.which('c++') or 'c++', '-std=c++20',
        f'-I{(OVERLAY_ROOT / "src").resolve()}', str(source), '-o', str(binary),
    ], check=True)
    environment = os.environ.copy()
    environment['M4_NATIVE_FLUSH_TIMELINE'] = '1'
    environment['M4_NATIVE_PROFILE_LOG_PATH'] = str(trace)
    subprocess.run([str(binary)], env=environment, check=True)
    lines = trace.read_text(encoding='utf-8').splitlines()
    if len(lines) != 1 or not lines[0].startswith(
            'NATIVE_PROFILE event=smoke stage=single_write '):
        raise ValueError('dedicated trace-file writer smoke test did not produce one intact event')
    return {'status': 'PASS', 'bytes': trace.stat().st_size,
            'sha256': sha256(trace), 'event_lines': len(lines)}


def compile_overlay_source(compile_commands: list[dict[str, Any]], relative_source: str,
                           object_name: str, extra_args: tuple[str, ...] = ()) -> Path:
    suffix = f'/src/{relative_source}'
    entry = next(item for item in compile_commands if item['file'].endswith(suffix))
    args = shlex.split(entry['command'])
    original_source = str((SOURCE_ROOT / 'src' / relative_source).resolve())
    if args.count(original_source) != 1:
        raise ValueError(f'compile command does not uniquely reference {relative_source}')
    args[args.index(original_source)] = str((OVERLAY_ROOT / 'src' / relative_source).resolve())
    out_index = args.index('-o')
    object_path = BUILD_OUTPUT / object_name
    if object_path.exists():
        raise FileExistsError(f'refusing to overwrite native object: {object_path}')
    object_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    args[out_index + 1] = str(object_path)
    args.insert(1, f'-I{(OVERLAY_ROOT / "src").resolve()}')
    args.insert(2, f'-I{Path(original_source).parent.resolve()}')
    args.extend(extra_args)
    subprocess.run(args, cwd=Path(entry['directory']), check=True)
    return object_path


def build(
    lock_records: list[dict[str, str]],
    hashes: dict[str, str | None],
    build_kind: str = 'isolated-native-schema-lock-owner-trace',
) -> dict[str, Any]:
    trace_writer_smoke = smoke_trace_writer()
    compile_db = BUILD_ROOT / 'compile_commands.json'
    try:
        compile_commands = json.loads(compile_db.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f'cannot read native compile database {compile_db}: {error}') from error
    tsan_flags = ('-fsanitize=thread', '-g', '-O1') if QUERY_LOCK_SPLIT_TSAN else ()
    hook_flags = (
        ('-DM4_NATIVE_LOCK_SPLIT_TEST_HOOKS=1', '-fsanitize=thread', '-g', '-O1')
        if QUERY_LOCK_SPLIT_TSAN
        else ('-DM4_NATIVE_LOCK_SPLIT_TEST_HOOKS=1', '-O0'))
    object_paths = {
        'collection.cc.o': compile_overlay_source(
            compile_commands, 'db/collection.cc', 'collection.cc.o',
            hook_flags if QUERY_LOCK_SPLIT_TEST_HOOKS else tsan_flags),
        'fts_recall_node.cc.o': compile_overlay_source(
            compile_commands, 'db/sqlengine/planner/fts_recall_node.cc',
            'fts_recall_node.cc.o', tsan_flags),
    }
    if SEAL_STAGE_PROBE or QUERY_LOCK_SPLIT_PROBE:
        object_paths['segment.cc.o'] = compile_overlay_source(
            compile_commands, 'db/index/segment/segment.cc', 'segment.cc.o',
            hook_flags if QUERY_LOCK_SPLIT_TEST_HOOKS else tsan_flags)
    if QUERY_LOCK_SPLIT_TEST_HOOKS:
        object_paths['fts_indexer.cc.o'] = compile_overlay_source(
            compile_commands,
            'db/index/column/fts_column/fts_indexer.cc', 'fts_indexer.cc.o',
            hook_flags)
        object_paths['fts_column_indexer.cc.o'] = compile_overlay_source(
            compile_commands,
            'db/index/column/fts_column/fts_column_indexer.cc',
            'fts_column_indexer.cc.o', hook_flags)
        object_paths['rocksdb_context.cc.o'] = compile_overlay_source(
            compile_commands, 'db/common/rocksdb_context.cc',
            'rocksdb_context.cc.o', hook_flags)
    if OPTIMIZE_INNER_STAGE_PROBE:
        inner_sources = (
            ('db/index/column/inverted_column/inverted_indexer.cc', 'inverted_indexer.cc.o'),
            ('db/index/column/inverted_column/inverted_column_indexer_write.cc',
             'inverted_column_indexer_write.cc.o'),
            ('db/common/rocksdb_context.cc', 'rocksdb_context.cc.o'),
            ('db/index/column/fts_column/fts_indexer.cc', 'fts_indexer.cc.o'),
        )
        for relative, object_name in inner_sources:
            object_paths[object_name] = compile_overlay_source(
                compile_commands, relative, object_name)

    archive_source = BUILD_ROOT / 'lib/libzvec.a'
    archive = BUILD_OUTPUT / 'libzvec.a'
    shutil.copy2(archive_source, archive)
    members = subprocess.run(['ar', 't', str(archive)], check=True, text=True,
                             capture_output=True).stdout.splitlines()
    for member in object_paths:
        if members.count(member) != 1:
            raise ValueError(f'expected exactly one {member} member in libzvec.a')
        subprocess.run(['ar', 'd', str(archive), member], check=True)
    for object_path in object_paths.values():
        subprocess.run(['ar', 'q', str(archive), str(object_path)], check=True)
    subprocess.run(['ranlib', str(archive)], check=True)
    members_after = subprocess.run(['ar', 't', str(archive)], check=True, text=True,
                                   capture_output=True).stdout.splitlines()
    if any(members_after.count(member) != 1 for member in object_paths):
        raise ValueError('copied archive does not contain exactly one replacement for each timeline TU')

    link_file = BUILD_ROOT / 'src/binding/c/CMakeFiles/zvec_c_api.dir/link.txt'
    link_command = link_file.read_text(encoding='utf-8').strip()
    test_hook_export_script_sha256 = None
    if QUERY_LOCK_SPLIT_TEST_HOOKS or QUERY_LOCK_SPLIT_BENCH:
        export_script_source = SOURCE_ROOT / 'src/binding/c/exported_symbols.lds'
        export_script = BUILD_OUTPUT / 'exported_symbols_lock_split_test.lds'
        export_text = export_script_source.read_text(encoding='utf-8')
        local_block = '  local: *;\n};'
        if export_text.count(local_block) != 1:
            raise ValueError('native exported-symbol map changed; refusing test-hook export')
        export_symbols = []
        if QUERY_LOCK_SPLIT_TEST_HOOKS:
            export_symbols.extend((
                'zvec_m4_lock_split_set_test_hook;',
                'zvec_m4_lock_split_set_failure;',
                'zvec_m4_lock_split_clear_failures;',
                'zvec_m4_lock_split_run_smoke;',
                'zvec_m4_lock_split_run_failure_smoke;',
                'zvec_m4_lock_split_run_crash_child;',
                'zvec_m4_lock_split_verify_after_crash;',
                'zvec_m4_lock_split_run_multifield_smoke;',
                'zvec_m4_lock_split_verify_vector_after_crash;',
                'zvec_m4_lock_split_run_ddl_crash_child;',
                'zvec_m4_lock_split_run_crash_cycle;',
                'zvec_m4_lock_split_verify_crash_loop;'))
        if QUERY_LOCK_SPLIT_BENCH:
            export_symbols.append('zvec_m4_lock_split_run_bench;')
            export_symbols.append('zvec_m4_lock_split_run_bench_cycles;')
        export_text = export_text.replace(
            local_block,
            ''.join(f'    {symbol}\n' for symbol in export_symbols) + local_block, 1)
        export_script.write_text(export_text, encoding='utf-8')
        source_flag = f'--version-script={export_script_source}'
        if link_command.count(source_flag) != 1:
            raise ValueError('native link command version-script input changed')
        link_command = link_command.replace(
            source_flag, f'--version-script={export_script.resolve()}', 1)
        test_hook_export_script_sha256 = sha256(export_script)
    old_archive = '../../../lib/libzvec.a'
    if link_command.count(old_archive) != 1:
        raise ValueError('native C API link command no longer has one zvec archive input')
    link_command = link_command.replace(old_archive, shlex.quote(str(archive.resolve())), 1)
    old_output = '-o ../../../lib/libzvec_c_api.so'
    output = BUILD_OUTPUT / 'libzvec_c_api.so'
    if link_command.count(old_output) != 1:
        raise ValueError('native C API link output token changed')
    link_command = link_command.replace(old_output, f'-o {shlex.quote(str(output.resolve()))}', 1)
    dependency_prefix = '-Wl,--dependency-file='
    dep_start = link_command.find(dependency_prefix)
    if dep_start >= 0:
        dep_end = link_command.find(' ', dep_start)
        if dep_end < 0:
            dep_end = len(link_command)
        new_dep = dependency_prefix + shlex.quote(str((BUILD_OUTPUT / 'zvec_c_api.link.d').resolve()))
        link_command = link_command[:dep_start] + new_dep + link_command[dep_end:]
        if link_command.find(dependency_prefix, dep_start + len(new_dep)) >= 0:
            raise ValueError('unexpected multiple linker dependency-file arguments')
    if QUERY_LOCK_SPLIT_TSAN:
        link_command += ' -fsanitize=thread'
    subprocess.run(['/bin/bash', '-o', 'pipefail', '-c', link_command],
                   cwd=BUILD_ROOT / 'src/binding/c', check=True)
    linked_strings = subprocess.run(['strings', '-a', str(output)], check=True,
                                    capture_output=True).stdout
    required_trace_strings = [
        b'M4_NATIVE_PROFILE_LOG_PATH', b'schema_handle_lock_owner',
        b'query_unsafe_setup', b'fts_search',
    ]
    if SEAL_STAGE_PROBE:
        required_trace_strings.extend((b'segment_dump_stage', b'collection_switch_stage'))
    if OPTIMIZE_INNER_STAGE_PROBE:
        required_trace_strings.extend((
            b'inverted_seal_stage', b'inverted_column_seal_stage',
            b'rocksdb_compact_stage', b'rocksdb_flush_stage',
            b'rocksdb_create_stage', b'segment_create_stage',
            b'new_writer_init_stage', b'create_new_writer'))
    if CONTEXT_SWITCH_PROBE:
        required_trace_strings.extend((b'ctx_nvcsw', b'ctx_nivcsw'))
    if QUERY_LOCK_SPLIT_PROBE:
        required_trace_strings.extend((b'vector_access_lock_wait',
                                       b'query_requires_vector_access'))
    if QUERY_LOCK_SPLIT_TEST_HOOKS:
        required_trace_strings.extend((b'zvec_m4_lock_split_set_test_hook',
                                       b'zvec_m4_lock_split_set_failure',
                                       b'zvec_m4_lock_split_clear_failures',
                                       b'zvec_m4_lock_split_run_smoke',
                                       b'zvec_m4_lock_split_run_failure_smoke',
                                       b'zvec_m4_lock_split_run_crash_child',
                                       b'zvec_m4_lock_split_verify_after_crash',
                                       b'zvec_m4_lock_split_run_multifield_smoke',
                                       b'zvec_m4_lock_split_verify_vector_after_crash',
                                       b'zvec_m4_lock_split_run_ddl_crash_child',
                                       b'zvec_m4_lock_split_run_crash_cycle',
                                       b'zvec_m4_lock_split_verify_crash_loop',
                                       b'flush_vector_and_write_locks_acquired',
                                       b'write_lock_wait',
                                       b'search_vector_guard_wait',
                                       b'optimize_dump_before_inverted_seal',
                                       b'optimize_fts_side_cf_reset_before_drop',
                                       b'fts_seal_conversion_before_terms',
                                       b'fts_search_iterator_ready',
                                       b'rocksdb_flush_cf_after',
                                       b'zvec_m4_lock_split_emit_segment_test_hook',
                                       b'zvec_m4_lock_split_emit_cf_test_hook'))
    if QUERY_LOCK_SPLIT_BENCH:
        required_trace_strings.extend((b'bench_query phase=', b'bench_cycle phase=',
                                       b'zvec_m4_lock_split_run_bench',
                                       b'zvec_m4_lock_split_run_bench_cycles'))
    if any(marker not in linked_strings for marker in required_trace_strings):
        missing = [marker.decode() for marker in required_trace_strings
                   if marker not in linked_strings]
        raise ValueError(f'linked library omitted required trace implementation strings: {missing}')
    if QUERY_LOCK_SPLIT_TEST_HOOKS or QUERY_LOCK_SPLIT_BENCH:
        dynamic_symbols = subprocess.run(
            ['nm', '-D', '--defined-only', str(output)], check=True,
            capture_output=True).stdout
        required_symbols = []
        if QUERY_LOCK_SPLIT_TEST_HOOKS:
            required_symbols.extend((
                b'zvec_m4_lock_split_set_test_hook',
                b'zvec_m4_lock_split_set_failure',
                b'zvec_m4_lock_split_clear_failures',
                b'zvec_m4_lock_split_run_smoke',
                b'zvec_m4_lock_split_run_failure_smoke',
                b'zvec_m4_lock_split_run_crash_child',
                b'zvec_m4_lock_split_verify_after_crash',
                b'zvec_m4_lock_split_run_multifield_smoke',
                b'zvec_m4_lock_split_verify_vector_after_crash',
                b'zvec_m4_lock_split_run_ddl_crash_child',
                b'zvec_m4_lock_split_run_crash_cycle',
                b'zvec_m4_lock_split_verify_crash_loop'))
        if QUERY_LOCK_SPLIT_BENCH:
            required_symbols.extend((b'zvec_m4_lock_split_run_bench',
                                     b'zvec_m4_lock_split_run_bench_cycles'))
        for symbol in required_symbols:
            if symbol not in dynamic_symbols:
                raise ValueError(
                    f'test-hook symbol is not dynamically exported: {symbol.decode()}')

    library_file = LIBRARY_DIR / 'libzvec_c_api.so'
    if library_file.exists():
        raise FileExistsError(f'refusing to overwrite diagnostic library: {library_file}')
    LIBRARY_DIR.mkdir(parents=True, mode=0o700)
    shutil.copy2(output, library_file)
    manifest: dict[str, Any] = {
        'schema_version': 1,
        'build_kind': build_kind,
        'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_root': str(SOURCE_ROOT),
        'source_head': subprocess.run(
            ['git', '-C', str(SOURCE_ROOT), 'rev-parse', 'HEAD'], check=True,
            text=True, capture_output=True).stdout.strip(),
        'build_root': str(BUILD_ROOT),
        'build_root_written': False,
        'source_root_written': False,
        'work_root': str(WORK_ROOT),
        'lock_sites': lock_records,
        'lock_site_count': len(lock_records),
        'query_lock_split_probe': QUERY_LOCK_SPLIT_PROBE,
        'query_lock_split_test_hooks': QUERY_LOCK_SPLIT_TEST_HOOKS,
        'query_lock_split_bench': QUERY_LOCK_SPLIT_BENCH,
        'test_hook_export_script_sha256': test_hook_export_script_sha256,
        'query_lock_split_test_source_sha256': hashes.get(
            'query_lock_split_test_source_sha256'),
        'trace_writer_smoke': trace_writer_smoke,
        'timeline_header_translation_units': [
            'src/db/collection.cc', 'src/db/sqlengine/planner/fts_recall_node.cc',
            *(['src/db/index/segment/segment.cc'] if SEAL_STAGE_PROBE else []),
            *(['src/db/index/column/inverted_column/inverted_indexer.cc',
               'src/db/index/column/inverted_column/inverted_column_indexer_write.cc',
               'src/db/common/rocksdb_context.cc',
               'src/db/index/column/fts_column/fts_indexer.cc']
              if OPTIMIZE_INNER_STAGE_PROBE else [])],
        'linked_trace_string_check': {
            'status': 'PASS',
            'required_strings': [marker.decode() for marker in required_trace_strings],
        },
        **hashes,
        'instrumented_object_sha256': {
            name: sha256(path) for name, path in object_paths.items()},
        'instrumented_archive_sha256': sha256(archive),
        'native_library_sha256': sha256(library_file),
        'native_library_bytes': library_file.stat().st_size,
        'library_path': str(LIBRARY_DIR),
        'library_file': str(library_file),
    }
    if MANIFEST_PATH.exists():
        raise FileExistsError(f'refusing to overwrite build manifest: {MANIFEST_PATH}')
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def finalize_existing() -> dict[str, Any]:
    if not WORK_ROOT.is_dir():
        raise FileNotFoundError(f'no isolated build to finalize: {WORK_ROOT}')
    if LIBRARY_DIR.exists() or MANIFEST_PATH.exists():
        raise FileExistsError('refusing to overwrite an existing library or build manifest')

    source_collection = SOURCE_ROOT / 'src/db/collection.cc'
    source_header = SOURCE_ROOT / 'src/db/common/native_timeline.h'
    source_fts = SOURCE_ROOT / 'src/db/sqlengine/planner/fts_recall_node.cc'
    overlay_collection = OVERLAY_ROOT / 'src/db/collection.cc'
    overlay_header = OVERLAY_ROOT / 'src/db/common/native_timeline.h'
    overlay_fts = OVERLAY_ROOT / 'src/db/sqlengine/planner/fts_recall_node.cc'
    raw_collection = source_collection.read_bytes()
    raw_header = source_header.read_bytes()
    raw_fts = source_fts.read_bytes()
    instrumented_collection, lock_records = instrument_collection(raw_collection.decode())
    instrumented_header = instrument_timeline_header(raw_header.decode())
    if (overlay_collection.read_text(encoding='utf-8') != instrumented_collection or
            overlay_header.read_text(encoding='utf-8') != instrumented_header or
            overlay_fts.read_bytes() != raw_fts):
        raise ValueError('retained v4 overlay differs from the reproducible reviewed source transform')

    hashes = {
        'source_collection_sha256': _sha256_bytes(raw_collection),
        'source_timeline_header_sha256': _sha256_bytes(raw_header),
        'source_fts_recall_node_sha256': _sha256_bytes(raw_fts),
        'overlay_collection_sha256': sha256(overlay_collection),
        'overlay_timeline_header_sha256': sha256(overlay_header),
        'overlay_fts_recall_node_sha256': sha256(overlay_fts),
    }
    object_paths = {
        'collection.cc.o': BUILD_OUTPUT / 'collection.cc.o',
        'fts_recall_node.cc.o': BUILD_OUTPUT / 'fts_recall_node.cc.o',
    }
    archive = BUILD_OUTPUT / 'libzvec.a'
    library_output = BUILD_OUTPUT / 'libzvec_c_api.so'
    if not archive.is_file() or not library_output.is_file() or any(
            not path.is_file() for path in object_paths.values()):
        raise FileNotFoundError('retained v4 archive, both replacement objects, or linked image is missing')
    members = subprocess.run(['ar', 't', str(archive)], check=True, text=True,
                             capture_output=True).stdout.splitlines()
    for member, object_path in object_paths.items():
        if members.count(member) != 1:
            raise ValueError(f'retained archive does not contain exactly one {member}')
        archived = subprocess.run(['ar', 'p', str(archive), member], check=True,
                                  capture_output=True).stdout
        if _sha256_bytes(archived) != sha256(object_path):
            raise ValueError(f'archive member {member} differs from the compiled v4 overlay object')

    required_trace_strings = (
        b'M4_NATIVE_PROFILE_LOG_PATH', b'schema_handle_lock_owner',
        b'query_unsafe_setup', b'fts_search',
    )
    linked_strings = subprocess.run(['strings', '-a', str(library_output)], check=True,
                                    capture_output=True).stdout
    if any(marker not in linked_strings for marker in required_trace_strings):
        missing = [marker.decode() for marker in required_trace_strings
                   if marker not in linked_strings]
        raise ValueError(f'linked library omitted required trace implementation strings: {missing}')

    smoke_trace = BUILD_OUTPUT / 'trace_file_smoke.jsonl'
    smoke_lines = smoke_trace.read_text(encoding='utf-8').splitlines()
    if len(smoke_lines) != 1 or not smoke_lines[0].startswith(
            'NATIVE_PROFILE event=smoke stage=single_write '):
        raise ValueError('retained logger smoke file is absent or malformed')

    library_file = LIBRARY_DIR / 'libzvec_c_api.so'
    LIBRARY_DIR.mkdir(parents=True, mode=0o700)
    shutil.copy2(library_output, library_file)
    manifest: dict[str, Any] = {
        'schema_version': 1,
        'build_kind': 'isolated-native-schema-lock-owner-trace',
        'finalized_existing_build': True,
        'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source_root': str(SOURCE_ROOT),
        'source_head': subprocess.run(
            ['git', '-C', str(SOURCE_ROOT), 'rev-parse', 'HEAD'], check=True,
            text=True, capture_output=True).stdout.strip(),
        'build_root': str(BUILD_ROOT),
        'build_root_written': False,
        'source_root_written': False,
        'work_root': str(WORK_ROOT),
        'lock_sites': lock_records,
        'lock_site_count': len(lock_records),
        'timeline_header_translation_units': [
            'src/db/collection.cc', 'src/db/sqlengine/planner/fts_recall_node.cc'],
        'trace_writer_smoke': {
            'status': 'PASS', 'bytes': smoke_trace.stat().st_size,
            'sha256': sha256(smoke_trace), 'event_lines': len(smoke_lines)},
        'linked_trace_string_check': {
            'status': 'PASS',
            'required_strings': [marker.decode() for marker in required_trace_strings]},
        **hashes,
        'instrumented_object_sha256': {
            name: sha256(path) for name, path in object_paths.items()},
        'instrumented_archive_sha256': sha256(archive),
        'native_library_sha256': sha256(library_file),
        'native_library_bytes': library_file.stat().st_size,
        'library_path': str(LIBRARY_DIR),
        'library_file': str(library_file),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + chr(10), encoding='utf-8')
    return manifest


def configure_preflush_probe_paths() -> None:
    global WORK_ROOT, OVERLAY_ROOT, BUILD_OUTPUT, LIBRARY_DIR, MANIFEST_PATH
    WORK_ROOT = ROOT / '.work/native-optimize-preflush-probe-v1'
    OVERLAY_ROOT = WORK_ROOT / 'source'
    BUILD_OUTPUT = WORK_ROOT / 'build'
    LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
                   'm4-native-optimize-preflush-probe-v1/out/zvec-prebuilt')
    MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                     'native-schema-lock-trace-build-v5.json')


def configure_seal_stage_probe_paths() -> None:
    global WORK_ROOT, OVERLAY_ROOT, BUILD_OUTPUT, LIBRARY_DIR, MANIFEST_PATH
    global SEAL_STAGE_PROBE, CONTEXT_SWITCH_PROBE
    SEAL_STAGE_PROBE = True
    CONTEXT_SWITCH_PROBE = False
    WORK_ROOT = ROOT / '.work/native-seal-stage-trace-v2'
    OVERLAY_ROOT = WORK_ROOT / 'source'
    BUILD_OUTPUT = WORK_ROOT / 'build'
    LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
                   'm4-native-seal-stage-trace-v2/out/zvec-prebuilt')
    MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                     'native-schema-lock-trace-build-v7.json')


def configure_seal_stage_context_switch_probe_paths() -> None:
    global WORK_ROOT, OVERLAY_ROOT, BUILD_OUTPUT, LIBRARY_DIR, MANIFEST_PATH
    global SEAL_STAGE_PROBE, CONTEXT_SWITCH_PROBE
    SEAL_STAGE_PROBE = True
    CONTEXT_SWITCH_PROBE = True
    WORK_ROOT = ROOT / 'research/m4-native/.work/build/native-seal-stage-trace-v4'
    OVERLAY_ROOT = WORK_ROOT / 'source'
    BUILD_OUTPUT = WORK_ROOT / 'build'
    LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
                   'm4-native-seal-stage-trace-v4/out/zvec-prebuilt')
    MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                     'native-schema-lock-trace-build-v9.json')


def configure_optimize_inner_stage_probe_paths() -> None:
    global WORK_ROOT, OVERLAY_ROOT, BUILD_OUTPUT, LIBRARY_DIR, MANIFEST_PATH
    global SEAL_STAGE_PROBE, CONTEXT_SWITCH_PROBE, OPTIMIZE_INNER_STAGE_PROBE
    SEAL_STAGE_PROBE = True
    CONTEXT_SWITCH_PROBE = False
    OPTIMIZE_INNER_STAGE_PROBE = True
    WORK_ROOT = ROOT / 'research/m4-native/.work/build/native-optimize-inner-stage-trace-v1'
    OVERLAY_ROOT = WORK_ROOT / 'source'
    BUILD_OUTPUT = WORK_ROOT / 'build'
    LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
                   'm4-native-optimize-inner-stage-trace-v1/out/zvec-prebuilt')
    MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                     'native-schema-lock-trace-build-v10.json')


def configure_query_lock_split_paths(
    test_hooks: bool, inner_stage_probe: bool, *, variant_override: str | None = None,
    apply_lock_split: bool = True, bench: bool = False, tsan: bool = False,
) -> None:
    global WORK_ROOT, OVERLAY_ROOT, BUILD_OUTPUT, LIBRARY_DIR, MANIFEST_PATH
    global SEAL_STAGE_PROBE, CONTEXT_SWITCH_PROBE, OPTIMIZE_INNER_STAGE_PROBE
    global QUERY_LOCK_SPLIT_PROBE, QUERY_LOCK_SPLIT_TEST_HOOKS, QUERY_LOCK_SPLIT_BENCH
    global QUERY_LOCK_SPLIT_TSAN
    variant = variant_override or (
        'native-query-lock-split-v1-test-hooks-v8' if test_hooks
        else 'native-query-lock-split-v1')
    SEAL_STAGE_PROBE = inner_stage_probe
    CONTEXT_SWITCH_PROBE = False
    OPTIMIZE_INNER_STAGE_PROBE = inner_stage_probe
    QUERY_LOCK_SPLIT_PROBE = apply_lock_split
    QUERY_LOCK_SPLIT_TEST_HOOKS = test_hooks
    QUERY_LOCK_SPLIT_BENCH = bench
    QUERY_LOCK_SPLIT_TSAN = tsan
    WORK_ROOT = ROOT / 'research/m4-native/.work/build' / variant
    OVERLAY_ROOT = WORK_ROOT / 'source'
    BUILD_OUTPUT = WORK_ROOT / 'build'
    LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
                   / variant / 'out/zvec-prebuilt')
    MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                     / f'{variant}-manifest.json')


def main() -> int:
    if sys.argv[1:] == ['--finalize-existing']:
        manifest = finalize_existing()
    elif len(sys.argv) == 1:
        lock_records, hashes = prepare_overlay()
        manifest = build(lock_records, hashes)
    elif sys.argv[1:] == ['--preflush-probe']:
        configure_preflush_probe_paths()
        lock_records, hashes = prepare_overlay(preflush_probe=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-schema-lock-trace-with-read-safe-preflush-probe')
    elif sys.argv[1:] == ['--seal-stage-probe']:
        configure_seal_stage_probe_paths()
        lock_records, hashes = prepare_overlay(seal_stage_probe=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-schema-lock-trace-with-optimize-seal-substages')
    elif sys.argv[1:] == ['--seal-stage-context-switch-probe']:
        configure_seal_stage_context_switch_probe_paths()
        lock_records, hashes = prepare_overlay(
            seal_stage_probe=True, context_switch_probe=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-seal-stage-trace-with-targeted-context-switch-counters')
    elif sys.argv[1:] == ['--optimize-inner-stage-probe']:
        configure_optimize_inner_stage_probe_paths()
        lock_records, hashes = prepare_overlay(
            seal_stage_probe=True, optimize_inner_stage_probe=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-optimize-inner-stage-attribution')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks']:
        configure_query_lock_split_paths(test_hooks=True, inner_stage_probe=False)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v2']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v10')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-test-hook-v2')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v3']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v11')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-test-hook-v3')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v4']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v12')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-test-hook-v4')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v5']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v13')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-test-hook-v5')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v6']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v14')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-flush-io-and-failure-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v7']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v15')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-flush-io-and-failure-test-hook-v2')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v8']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v16')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-flush-io-and-failure-test-hook-v3')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v9']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v17')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-flush-io-and-failure-test-hook-v4')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v10']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v18')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-flush-io-and-failure-test-hook-v5')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v15']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v23')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-crash-loop-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v14']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v22')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-ddl-crash-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v13']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v21')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-vector-crash-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v12']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v20')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-multifield-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-test-hooks-v11']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-v19')
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-crash-recovery-test-hook')
    elif sys.argv[1:] == ['--query-lock-split-bench-control']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-control-v1',
            apply_lock_split=False, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=False, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-control')
    elif sys.argv[1:] == ['--query-lock-split-bench-treatment']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-treatment-v1',
            apply_lock_split=True, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-treatment')
    elif sys.argv[1:] == ['--query-lock-split-bench-control-v2']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-control-v2',
            apply_lock_split=False, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=False, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-control-v2')
    elif sys.argv[1:] == ['--query-lock-split-bench-treatment-v2']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-treatment-v2',
            apply_lock_split=True, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-treatment-v2')
    elif sys.argv[1:] == ['--query-lock-split-bench-control-v3']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-control-v3',
            apply_lock_split=False, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=False, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-control-v3')
    elif sys.argv[1:] == ['--query-lock-split-bench-treatment-v3']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-treatment-v3',
            apply_lock_split=True, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-treatment-v3')
    elif sys.argv[1:] == ['--query-lock-split-tsan-v1']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-tsan-v1',
            tsan=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-partial-tsan')
    elif sys.argv[1:] == ['--query-lock-split-tsan-full-v1']:
        configure_query_lock_split_paths(
            test_hooks=True, inner_stage_probe=False,
            variant_override='native-query-lock-split-v1-test-hooks-tsan-full-v1',
            tsan=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_test_hooks=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-correctness-full-tsan')
    elif sys.argv[1:] == ['--query-lock-split-bench-control-v4']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-control-v4',
            apply_lock_split=False, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=False, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-control-v4')
    elif sys.argv[1:] == ['--query-lock-split-bench-treatment-v4']:
        configure_query_lock_split_paths(
            test_hooks=False, inner_stage_probe=False,
            variant_override='native-query-lock-split-bench-treatment-v4',
            apply_lock_split=True, bench=True)
        lock_records, hashes = prepare_overlay(
            query_lock_split_probe=True, query_lock_split_bench=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-bench-treatment-v4')
    elif sys.argv[1:] == ['--query-lock-split-probe']:
        configure_query_lock_split_paths(test_hooks=False, inner_stage_probe=True)
        lock_records, hashes = prepare_overlay(
            seal_stage_probe=True, optimize_inner_stage_probe=True,
            query_lock_split_probe=True)
        manifest = build(
            lock_records, hashes,
            build_kind='isolated-native-query-lock-split-with-nested-stage-tracing')
    else:
        print('Usage: build_native_schema_lock_trace.py '
              '[--finalize-existing | --preflush-probe | --seal-stage-probe | '
              '--seal-stage-context-switch-probe | --optimize-inner-stage-probe | '
              '--query-lock-split-test-hooks | --query-lock-split-test-hooks-v2 | '
              '--query-lock-split-test-hooks-v3 | --query-lock-split-test-hooks-v4 | '
              '--query-lock-split-test-hooks-v5 | --query-lock-split-test-hooks-v6 | '
              '--query-lock-split-test-hooks-v7 | --query-lock-split-test-hooks-v8 | '
              '--query-lock-split-test-hooks-v9 | --query-lock-split-test-hooks-v10 | '
              '--query-lock-split-test-hooks-v11 | --query-lock-split-test-hooks-v12 | '
              '--query-lock-split-test-hooks-v13 | --query-lock-split-test-hooks-v14 | '
              '--query-lock-split-test-hooks-v15 | '
              '--query-lock-split-bench-control | --query-lock-split-bench-treatment | '
              '--query-lock-split-bench-control-v2 | --query-lock-split-bench-treatment-v2 | '
              '--query-lock-split-bench-control-v3 | --query-lock-split-bench-treatment-v3 | '
              '--query-lock-split-bench-control-v4 | --query-lock-split-bench-treatment-v4 | '
              '--query-lock-split-tsan-v1 | --query-lock-split-tsan-full-v1 | '
              '--query-lock-split-probe]',
              file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
