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

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = ROOT / '.local/native/zvec-native-profile'
BUILD_ROOT = ROOT / '.local/native/zvec-native-build'
WORK_ROOT = ROOT / 'research/m4-native/.work/native-schema-lock-trace-v4'
OVERLAY_ROOT = WORK_ROOT / 'source'
BUILD_OUTPUT = WORK_ROOT / 'build'
LIBRARY_DIR = (ROOT / 'research/m4-native/.work/build/rust-bridge-target/debug/build/'
               'm4-native-schema-lock-trace-v4/out/zvec-prebuilt')
MANIFEST_PATH = (ROOT / 'research/m4-native/results/native-query-timeline-20261029/'
                 'native-schema-lock-trace-build-v4.json')

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


def instrument_collection(source: str) -> tuple[str, list[dict[str, str]]]:
    lines = source.splitlines(keepends=True)
    observed: list[tuple[int, str, str, str, str, str]] = []
    per_method: dict[tuple[str, str], int] = {}
    offset = 0
    for line_number, line in enumerate(lines):
        parsed = _lock_line(line)
        if parsed is not None:
            kind, name = parsed
            method = _method_before(source, offset)
            observed.append((line_number, method, kind, name, line[:len(line) - len(line.lstrip())], line.rstrip('\n')))
        offset += len(line)

    if len(observed) != len(LOCK_PLAN):
        raise ValueError(f'expected {len(LOCK_PLAN)} schema locks, found {len(observed)}')

    records: list[dict[str, str]] = []
    for item, (expected_method, expected_kind, site, action) in zip(observed, LOCK_PLAN, strict=True):
        line_number, method, kind, name, indent, original = item
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
    if remaining != len(LOCK_PLAN):
        raise ValueError(f'schema lock declarations changed during instrumentation: {remaining}')
    for method, kind, site, action in LOCK_PLAN:
        if action in ('trace', 'special'):
            mode = 'exclusive' if kind == 'unique_lock' else 'shared'
            if result.count(f'"{site}_{mode}_wait"') != 1 or result.count(
                    f'"{site}_{mode}_hold"') != 1:
                raise ValueError(f'instrumented wait/hold span missing for {site}')
    if result.count('"query_search_shared_hold"') != 1 or result.count(
            '"close_internal_iterator_wait"') != 1:
        raise ValueError('special query/iterator lock spans are incomplete')
    return result, records


def instrument_timeline_header(source: str) -> str:
    include_marker = '#include <cstdlib>\n'
    if source.count(include_marker) != 1:
        raise ValueError('native_timeline include block changed; refusing instrumentation')
    source = source.replace(include_marker,
                            include_marker + '#include <cerrno>\n#include <fcntl.h>\n', 1)

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
    if source.count(old_output) != 1:
        raise ValueError('native_timeline output block changed; refusing instrumentation')
    return source.replace(old_output, new_output, 1)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_overlay() -> tuple[list[dict[str, str]], dict[str, str]]:
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
    instrumented_collection, lock_records = instrument_collection(
        original_collection.decode('utf-8'))
    instrumented_header = instrument_timeline_header(original_header.decode('utf-8'))
    overlay_collection.write_text(instrumented_collection, encoding='utf-8')
    overlay_header.write_text(instrumented_header, encoding='utf-8')
    overlay_fts.write_bytes(original_fts)
    hashes = {
        'source_collection_sha256': _sha256_bytes(original_collection),
        'source_timeline_header_sha256': _sha256_bytes(original_header),
        'source_fts_recall_node_sha256': _sha256_bytes(original_fts),
        'overlay_collection_sha256': _sha256_bytes(instrumented_collection.encode()),
        'overlay_timeline_header_sha256': _sha256_bytes(instrumented_header.encode()),
        'overlay_fts_recall_node_sha256': _sha256_bytes(original_fts),
    }
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
                           object_name: str) -> Path:
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
    subprocess.run(args, cwd=Path(entry['directory']), check=True)
    return object_path


def build(lock_records: list[dict[str, str]], hashes: dict[str, str]) -> dict[str, Any]:
    trace_writer_smoke = smoke_trace_writer()
    compile_db = BUILD_ROOT / 'compile_commands.json'
    compile_commands = json.loads(compile_db.read_text(encoding='utf-8'))
    object_paths = {
        'collection.cc.o': compile_overlay_source(
            compile_commands, 'db/collection.cc', 'collection.cc.o'),
        'fts_recall_node.cc.o': compile_overlay_source(
            compile_commands, 'db/sqlengine/planner/fts_recall_node.cc', 'fts_recall_node.cc.o'),
    }

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
    subprocess.run(['/bin/bash', '-o', 'pipefail', '-c', link_command],
                   cwd=BUILD_ROOT / 'src/binding/c', check=True)
    linked_strings = subprocess.run(['strings', '-a', str(output)], check=True,
                                    capture_output=True).stdout
    required_trace_strings = (
        b'M4_NATIVE_PROFILE_LOG_PATH', b'schema_handle_lock_owner',
        b'query_unsafe_setup', b'fts_search',
    )
    if any(marker not in linked_strings for marker in required_trace_strings):
        missing = [marker.decode() for marker in required_trace_strings
                   if marker not in linked_strings]
        raise ValueError(f'linked library omitted required trace implementation strings: {missing}')

    library_file = LIBRARY_DIR / 'libzvec_c_api.so'
    if library_file.exists():
        raise FileExistsError(f'refusing to overwrite diagnostic library: {library_file}')
    LIBRARY_DIR.mkdir(parents=True, mode=0o700)
    shutil.copy2(output, library_file)
    manifest: dict[str, Any] = {
        'schema_version': 1,
        'build_kind': 'isolated-native-schema-lock-owner-trace',
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
        'trace_writer_smoke': trace_writer_smoke,
        'timeline_header_translation_units': [
            'src/db/collection.cc', 'src/db/sqlengine/planner/fts_recall_node.cc'],
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


def main() -> int:
    if sys.argv[1:] == ['--finalize-existing']:
        manifest = finalize_existing()
    elif len(sys.argv) == 1:
        lock_records, hashes = prepare_overlay()
        manifest = build(lock_records, hashes)
    else:
        print('Usage: build_native_schema_lock_trace.py [--finalize-existing]',
              file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
