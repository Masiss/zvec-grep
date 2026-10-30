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

    def test_trace_header_uses_exclusive_per_operation_file_and_single_write(self) -> None:
        source = (SOURCE_ROOT / 'src/db/common/native_timeline.h').read_text(encoding='utf-8')
        instrumented = builder.instrument_timeline_header(source)
        self.assertIn('M4_NATIVE_PROFILE_LOG_PATH', instrumented)
        self.assertIn('O_EXCL | O_APPEND', instrumented)
        self.assertIn('::write(fd, line, static_cast<size_t>(length))', instrumented)
        self.assertNotIn('std::fprintf(\n        stderr,', instrumented)
        self.assertNotIn('std::fflush(stderr)', instrumented)
        self.assertIn('NATIVE_PROFILE_ERROR trace_file_open_failed', instrumented)

    def test_compile_overlay_source_uses_overlay_src_for_each_timeline_header_includer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source_root = base / 'native'
            overlay_root = base / 'overlay'
            build_output = base / 'objects'
            relatives = ('db/collection.cc', 'db/sqlengine/planner/fts_recall_node.cc')
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
                        relatives, ('collection.cc.o', 'fts_recall_node.cc.o'), strict=True):
                    output = builder.compile_overlay_source(entries, relative, object_name)
                    self.assertEqual(output, build_output / object_name)
                    args = run.call_args.args[0]
                    self.assertEqual(args[1], f'-I{(overlay_root / "src").resolve()}')
                    self.assertIn(str((overlay_root / 'src' / relative).resolve()), args)
                    self.assertNotIn(str((source_root / 'src' / relative).resolve()), args)
            self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
