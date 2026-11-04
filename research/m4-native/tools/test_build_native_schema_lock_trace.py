#!/usr/bin/env python3
"""Focused tests for the isolated native schema-lock trace overlay."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
BUILDER_PATH = ROOT / 'research/m4-native/tools/build_native_schema_lock_trace.py'
SOURCE_ROOT = ROOT / '.local/native/zvec-native-profile'
spec = importlib.util.spec_from_file_location('build_native_schema_lock_trace', BUILDER_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


class NativeSchemaLockTraceBuildTests(unittest.TestCase):
    def test_overlay_traces_every_schema_lock_and_query_setup_gap(self) -> None:
        source = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        instrumented, records = builder.instrument_collection(source)
        self.assertEqual(len(records), 26)
        self.assertEqual(sum(item['lock_kind'] == 'unique_lock' for item in records), 13)
        self.assertEqual(sum(item['lock_kind'] == 'shared_lock' for item in records), 13)
        self.assertEqual(instrumented.count('schema_handle_lock_owner'), 49)
        self.assertIn('query_search_shared_hold', instrumented)
        self.assertIn('query_state_check', instrumented)
        self.assertIn('query_unsafe_setup', instrumented)
        self.assertIn('close_internal_iterator_wait', instrumented)
        self.assertIn('query_setup_span.finish();', instrumented)
        self.assertIn('iterator_wait_span.finish();', instrumented)
        self.assertIn('std::unique_lock lock(schema_handle_mtx_);', source)

    def test_overlay_refuses_unreviewed_lock_inventory_drift(self) -> None:
        source = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'schema locks'):
            builder.instrument_collection(source.replace(
                'std::unique_lock lock(schema_handle_mtx_);',
                'std::unique_lock lock(other_mutex_);', 1))

    def test_preflush_probe_uses_the_existing_reader_safe_flush_locks(self) -> None:
        source = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        candidate = builder.prepare_optimize_read_safe_preflush_probe(source)
        start = candidate.index('Status CollectionImpl::optimize(')
        guard = candidate.index(
            'std::unique_lock iterator_guard(schema_handle_mtx_);', start)
        guard_check = candidate.index(
            'check_no_active_iterators("optimize")', guard)
        preflush_lock = candidate.index(
            'std::shared_lock schema_lock(schema_handle_mtx_);', guard_check)
        vector_gate = candidate.index(
            'if (schema_->vector_fields().empty())', preflush_lock)
        write_lock = candidate.index(
            'std::shared_lock write_lock(write_mtx_);', vector_gate)
        flush = candidate.index('auto s = flush_unsafe();', write_lock)
        exclusive_seal = candidate.index(
            'std::unique_lock schema_lock(schema_handle_mtx_);', flush)
        self.assertLess(guard, guard_check)
        self.assertLess(guard_check, preflush_lock)
        self.assertLess(preflush_lock, vector_gate)
        self.assertLess(vector_gate, write_lock)
        self.assertLess(write_lock, flush)
        self.assertLess(flush, exclusive_seal)
        self.assertEqual(candidate.count(
            'CHECK_RETURN_STATUS(check_no_active_iterators("optimize"));'), 1)

        instrumented, records = builder.instrument_collection(
            candidate, builder.LOCK_PLAN_PREFLUSH_PROBE)
        self.assertEqual(len(records), 28)
        self.assertEqual(
            [record['site'] for record in records if record['method'] == 'optimize'],
            ['optimize_iterator_guard', 'optimize_preflush',
             'optimize_seal', 'optimize_commit'])
        self.assertIn('optimize_preflush_shared_wait', instrumented)
        self.assertIn('read_safe_preflush', instrumented)

    def test_optimize_switch_stage_probe_covers_dump_and_publication(self) -> None:
        source = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        instrumented = builder.instrument_optimize_switch_stages(source)
        stages = (
            '"collection_switch_stage", "writer_dump"',
            '"collection_switch_stage", "manager_add_segment"',
            '"collection_switch_stage", "create_new_writer"',
            '"collection_switch_stage", "writer_transition_metadata"',
            '"collection_switch_stage", "version_apply"',
            '"collection_switch_stage", "version_flush"',
        )
        positions = [instrumented.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('switch_total_span', instrumented)
        self.assertIn('writing_segment_->dump();', instrumented)
        self.assertIn('segment_manager_->add_segment(writing_segment_);', instrumented)
        self.assertIn('Segment::CreateAndOpen(', instrumented)
        self.assertIn('version_manager_->flush();', instrumented)

    def test_segment_dump_stage_probe_covers_exclusive_seal_steps(self) -> None:
        source = (SOURCE_ROOT / 'src/db/index/segment/segment.cc').read_text(encoding='utf-8')
        instrumented = builder.instrument_segment_dump(source)
        stages = (
            '"segment_dump_stage", "total"',
            '"segment_dump_stage", "flush_locked"',
            '"segment_dump_stage", "inverted_seal"',
            '"segment_dump_stage", "fts_seal_all"',
        )
        positions = [instrumented.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('#include "db/common/native_timeline.h"', instrumented)
        dump = instrumented[instrumented.index('Status SegmentImpl::dump() {'):
                            instrumented.index('void SegmentImpl::remove_writing_forward_block()')]
        self.assertIn('std::lock_guard<std::shared_mutex> lock(seg_mtx_);', dump)
        self.assertIn('s = invert_indexers_->seal();', dump)
        self.assertIn('s = dump_fts_indexers();', dump)

    def test_optimize_inner_stage_paths_stay_inside_research_workspace(self) -> None:
        names = ('WORK_ROOT', 'OVERLAY_ROOT', 'BUILD_OUTPUT', 'LIBRARY_DIR',
                 'MANIFEST_PATH', 'SEAL_STAGE_PROBE', 'CONTEXT_SWITCH_PROBE',
                 'OPTIMIZE_INNER_STAGE_PROBE')
        original = {name: getattr(builder, name) for name in names}
        try:
            builder.configure_optimize_inner_stage_probe_paths()
            expected_root = ROOT / 'research/m4-native/.work/build/native-optimize-inner-stage-trace-v1'
            self.assertEqual(builder.WORK_ROOT, expected_root)
            self.assertTrue(builder.WORK_ROOT.is_relative_to(ROOT / 'research/m4-native/.work'))
            self.assertEqual(builder.OVERLAY_ROOT, expected_root / 'source')
            self.assertEqual(builder.BUILD_OUTPUT, expected_root / 'build')
            self.assertTrue(builder.SEAL_STAGE_PROBE)
            self.assertFalse(builder.CONTEXT_SWITCH_PROBE)
            self.assertTrue(builder.OPTIMIZE_INNER_STAGE_PROBE)
        finally:
            for name, value in original.items():
                setattr(builder, name, value)

    def test_optimize_inner_stage_probe_decomposes_seal_and_writer_creation(self) -> None:
        source_root = SOURCE_ROOT / 'src'
        segment = builder.instrument_segment_create(builder.instrument_segment_dump(
            (source_root / 'db/index/segment/segment.cc').read_text(encoding='utf-8')))
        inverted = builder.instrument_inverted_indexer(
            (source_root / 'db/index/column/inverted_column/inverted_indexer.cc').read_text(
                encoding='utf-8'))
        column = builder.instrument_inverted_column_seal(
            (source_root / 'db/index/column/inverted_column/inverted_column_indexer_write.cc')
            .read_text(encoding='utf-8'))
        rocksdb = builder.instrument_rocksdb_context(
            (source_root / 'db/common/rocksdb_context.cc').read_text(encoding='utf-8'))
        fts = builder.instrument_fts_indexer(
            (source_root / 'db/index/column/fts_column/fts_indexer.cc').read_text(
                encoding='utf-8'))

        for stage in ('construct_segment_impl', 'prepare_segment_directory',
                      'initialize_segment', 'create_scalar_indexes',
                      'create_fts_indexers'):
            self.assertIn(stage, segment)
        for stage in ('seal_inverted_column_', 'flush_after_column_seal',
                      'rocksdb_compact_all', 'create_inverted_rocksdb',
                      'open_inverted_rocksdb', 'open_inverted_column_'):
            self.assertIn(stage, inverted)
        for stage in ('flush_special_values_', 'generate_statistics_',
                      'write_sealed_marker_'):
            self.assertIn(stage, column)
        for stage in ('rocksdb_create_stage', 'rocksdb_flush_stage',
                      'rocksdb_compact_stage', 'compact_cf_', 'compact_db_wide'):
            self.assertIn(stage, rocksdb)
        self.assertIn('db_flush_status.code()', rocksdb)
        self.assertIn('db_flush_status.ToString()', rocksdb)
        for stage in ('create_fts_rocksdb', 'open_fts_rocksdb', 'open_fts_column_'):
            self.assertIn(stage, fts)

    def test_seal_stage_probe_refuses_source_block_drift(self) -> None:
        collection = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        segment = (SOURCE_ROOT / 'src/db/index/segment/segment.cc').read_text(encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'new-writer creation block'):
            builder.instrument_optimize_switch_stages(collection.replace(
                'auto new_segment = Segment::CreateAndOpen(',
                'auto replacement = Segment::CreateAndOpen(', 1))
        with self.assertRaisesRegex(ValueError, 'FTS seal call changed'):
            builder.instrument_segment_dump(segment.replace(
                's = dump_fts_indexers();', 's = flush_fts_indexers();', 1))

    def test_preflush_probe_refuses_unreviewed_optimize_block_drift(self) -> None:
        source = (SOURCE_ROOT / 'src/db/collection.cc').read_text(encoding='utf-8')
        changed = source.replace(
            '// Phase 1: exclusively seal the current writing segment and snapshot',
            '// Phase 1: changed seal protocol', 1)
        with self.assertRaisesRegex(ValueError, 'optimize phase-1 seal block'):
            builder.prepare_optimize_read_safe_preflush_probe(changed)

    def test_trace_header_uses_exclusive_per_operation_file_and_single_write(self) -> None:
        source = (SOURCE_ROOT / 'src/db/common/native_timeline.h').read_text(encoding='utf-8')
        instrumented = builder.instrument_timeline_header(source)
        self.assertIn('M4_NATIVE_PROFILE_LOG_PATH', instrumented)
        self.assertIn('O_EXCL | O_APPEND', instrumented)
        self.assertIn('::write(fd, line, static_cast<size_t>(length))', instrumented)
        self.assertNotIn('std::fprintf(\n        stderr,', instrumented)
        self.assertNotIn('std::fflush(stderr)', instrumented)
        self.assertIn('NATIVE_PROFILE_ERROR trace_file_open_failed', instrumented)
        self.assertNotIn('RUSAGE_THREAD', instrumented)

    def test_context_switch_probe_is_limited_to_query_and_flush_lock_spans(self) -> None:
        source = (SOURCE_ROOT / 'src/db/common/native_timeline.h').read_text(encoding='utf-8')
        instrumented = builder.instrument_timeline_header(source, context_switch_probe=True)
        self.assertIn('#include <sys/resource.h>', instrumented)
        self.assertIn('RUSAGE_THREAD', instrumented)
        self.assertIn('ru_nvcsw', instrumented)
        self.assertIn('ru_nivcsw', instrumented)
        self.assertIn('ctx_nvcsw=%ld ctx_nivcsw=%ld\\n', instrumented)
        self.assertIn('event, "collection_query_stage"', instrumented)
        self.assertIn('stage, "schema_handle_lock"', instrumented)
        self.assertIn('event, "collection_flush_stage"', instrumented)
        self.assertIn('stage, "schema_handle_lock_held"', instrumented)

    def test_compile_overlay_source_uses_overlay_src_for_each_timeline_header_includer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source_root = base / 'native'
            overlay_root = base / 'overlay'
            build_output = base / 'objects'
            relatives = (
                'db/collection.cc', 'db/sqlengine/planner/fts_recall_node.cc',
                'db/index/segment/segment.cc')
            entries = []
            for relative in relatives:
                original = source_root / 'src' / relative
                overlaid = overlay_root / 'src' / relative
                original.parent.mkdir(parents=True, exist_ok=True)
                overlaid.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(b'source')
                overlaid.write_bytes(b'overlay')
                entries.append({
                    'directory': str(base), 'file': str(original),
                    'command': f'c++ -o old.o -c {original}',
                })
            with mock.patch.object(builder, 'SOURCE_ROOT', source_root), \
                 mock.patch.object(builder, 'OVERLAY_ROOT', overlay_root), \
                 mock.patch.object(builder, 'BUILD_OUTPUT', build_output), \
                 mock.patch.object(builder.subprocess, 'run') as run:
                for relative, object_name in zip(
                        relatives, ('collection.cc.o', 'fts_recall_node.cc.o', 'segment.cc.o'), strict=True):
                    output = builder.compile_overlay_source(entries, relative, object_name)
                    self.assertEqual(output, build_output / object_name)
                    args = run.call_args.args[0]
                    self.assertEqual(args[1], f'-I{(overlay_root / "src").resolve()}')
                    self.assertEqual(
                        args[2], f'-I{(source_root / "src" / relative).parent.resolve()}')
                    self.assertIn(str((overlay_root / 'src' / relative).resolve()), args)
                    self.assertNotIn(str((source_root / 'src' / relative).resolve()), args)
            self.assertEqual(run.call_count, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
