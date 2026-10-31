#!/usr/bin/env python3
"""Focused tests for the copied native query/flush lock-split transform."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'research/m4-native/tools'))
from native_query_lock_split import (
    apply_benchmark_source,
    apply_fts_column_test_hooks,
    apply_fts_seal_test_hook,
    apply_optimize_read_safe_dump,
    apply_query_lock_split,
    apply_rocksdb_flush_test_hooks,
    vector_lock_plan,
)  # noqa: E402
import build_native_schema_lock_trace as builder  # noqa: E402

SOURCE = ROOT / '.local/native/zvec-native-profile/src/db/collection.cc'
SEGMENT_SOURCE = ROOT / '.local/native/zvec-native-profile/src/db/index/segment/segment.cc'
FTS_INDEXER_SOURCE = ROOT / '.local/native/zvec-native-profile/src/db/index/column/fts_column/fts_indexer.cc'
FTS_COLUMN_INDEXER_SOURCE = ROOT / '.local/native/zvec-native-profile/src/db/index/column/fts_column/fts_column_indexer.cc'
ROCKSDB_CONTEXT_SOURCE = ROOT / '.local/native/zvec-native-profile/src/db/common/rocksdb_context.cc'
TEST_HOOK_SOURCE = ROOT / 'research/m4-native/tests/native_query_lock_split_smoke.cc.in'


class NativeQueryLockSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original = SOURCE.read_text(encoding='utf-8')
        cls.instrumented, cls.lock_records = builder.instrument_collection(cls.original)
        cls.transformed = apply_query_lock_split(cls.instrumented)
        cls.segment_transformed = apply_optimize_read_safe_dump(
            SEGMENT_SOURCE.read_text(encoding='utf-8'))
        cls.hooked = apply_query_lock_split(
            cls.instrumented, test_hooks=True,
            test_hook_source=TEST_HOOK_SOURCE.read_text(encoding='utf-8'))

    def method(self, source: str, start: str, end: str) -> str:
        i = source.index(start)
        j = source.index(end, i + len(start))
        return source[i:j]

    def test_flush_shares_schema_and_excludes_only_vector_access(self) -> None:
        body = self.method(
            self.transformed,
            'Status CollectionImpl::flush() {',
            'Status CollectionImpl::flush_unsafe() {')
        self.assertIn('std::shared_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);', body)
        self.assertIn('std::unique_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);', body)
        self.assertIn('std::shared_lock<std::shared_mutex> write_lock(write_mtx_);', body)
        self.assertLess(body.index('schema_lock(schema_handle_mtx_)'),
                        body.index('vector_access_lock(vector_access_mtx_)'))
        self.assertLess(body.index('vector_access_lock(vector_access_mtx_)'),
                        body.index('write_lock(write_mtx_)'))
        self.assertEqual(body.count('std::unique_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);'), 0)

    def test_all_vector_query_surfaces_take_shared_guard(self) -> None:
        ranges = (
            ('Result<DocPtrList> CollectionImpl::query(const SearchQuery &query) const {',
             'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {',
             'query_requires_vector_access(query)', 'vector_access_lock.lock();'),
            ('Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {',
             'template <typename Query>\nResult<internal::QueryResultSnapshot>',
             'query_requires_vector_access(query)', 'vector_access_lock.lock();'),
            ('CollectionImpl::query_result_snapshot_impl(const Query &query) const {',
             'Result<DocPtrList> CollectionImpl::query_unsafe(',
             'query_requires_vector_access(query)', 'vector_access_lock.lock();'),
            ('Result<GroupResults> CollectionImpl::group_by_query(',
             'Result<DocPtrMap> CollectionImpl::fetch(',
             'group_vector_access_lock_wait', 'vector_access_lock(vector_access_mtx_)'),
            ('Result<DocPtrMap> CollectionImpl::fetch(',
             'Result<std::string> CollectionImpl::debug_get_hnsw_storage_mode(',
             'if (include_vector)', 'vector_access_lock.lock();'),
            ('Result<std::string> CollectionImpl::debug_get_hnsw_storage_mode(',
             'Status CollectionImpl::recovery() {',
             'debug_vector_access_lock_wait', 'vector_access_lock(vector_access_mtx_)'),
        )
        for start, end, condition, guard in ranges:
            with self.subTest(start=start):
                body = self.method(self.transformed, start, end)
                self.assertIn(condition, body)
                self.assertIn(guard, body)
                self.assertLess(body.index(guard), body.index('get_all_segments') if 'get_all_segments' in body else len(body))

    def test_vector_access_classifier_covers_vector_output_and_multiquery(self) -> None:
        self.assertIn('query.include_vector_ || query.target_.get_vector_view().has_value()',
                      self.transformed)
        multi = self.transformed[self.transformed.index('bool query_requires_vector_access(const MultiQuery'):
                                  self.transformed.index('inline void native_lock_split_test_event')]
        self.assertIn('if (query.include_vector)', multi)
        self.assertIn('subquery.target_.get_vector_view().has_value()', multi)

    def test_writers_remain_serialized_and_expose_wait_in_test_overlay(self) -> None:
        writer = self.method(
            self.hooked,
            'Result<WriteResults> CollectionImpl::write_impl(',
            'bool CollectionImpl::need_switch_to_new_segment() const {')
        self.assertLess(writer.index('native_lock_split_test_event("write_lock_wait")'),
                        writer.index('std::lock_guard write_lock(write_mtx_);'))

    def test_non_vector_query_and_writer_paths_do_not_take_vector_guard(self) -> None:
        search = self.method(
            self.transformed,
            'Result<DocPtrList> CollectionImpl::query(const SearchQuery &query) const {',
            'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {')
        self.assertIn('if (query_requires_vector_access(query))', search)
        writer = self.method(
            self.transformed,
            'Result<WriteResults> CollectionImpl::write_impl(',
            'Result<DocPtrList> CollectionImpl::query(const SearchQuery &query) const {')
        self.assertNotIn('vector_access_mtx_', writer)

    def test_test_hooks_are_present_only_in_test_variant(self) -> None:
        self.assertNotIn('zvec_m4_lock_split_set_test_hook', self.transformed)
        self.assertIn('zvec_m4_lock_split_set_test_hook', self.hooked)
        self.assertIn('flush_vector_and_write_locks_acquired', self.hooked)
        self.assertIn('search_vector_guard_wait', self.hooked)
        self.assertIn('fetch_vector_guard_wait', self.hooked)
        self.assertIn('zvec_m4_lock_split_run_smoke', self.hooked)
        self.assertIn('std::condition_variable', self.hooked)

    def test_refuses_source_drift_instead_of_partially_transforming(self) -> None:
        modified = self.instrumented.replace(
            'The vector index interface does not promise',
            'The vector index interface unexpectedly promises', 1)
        with self.assertRaisesRegex(ValueError, 'expected one source anchor'):
            apply_query_lock_split(modified)

    def test_optimize_phase_publishes_temporary_fts_query_snapshot(self) -> None:
        optimize = self.method(
            self.transformed,
            'Status CollectionImpl::optimize(const OptimizeOptions &options) {',
            '  if (persist_segments.size() == 0) {')
        self.assertIn('std::shared_lock<std::shared_mutex> schema_lock(schema_handle_mtx_);', optimize)
        self.assertIn('std::unique_lock<std::shared_mutex> vector_access_lock(vector_access_mtx_);', optimize)
        self.assertIn('std::unique_lock<std::shared_mutex> optimize_query_gate_lock(', optimize)
        self.assertIn('std::unique_lock<std::shared_mutex> write_lock(write_mtx_);', optimize)
        self.assertIn('optimize_query_snapshot_active_ = true;', optimize)
        self.assertIn('optimize_query_snapshot_active_ = false;', optimize)

    def test_unfiltered_fts_only_queries_bypass_optimize_gate(self) -> None:
        self.assertIn('query.target_.get_fts_clause() == nullptr || query.include_vector_ ||',
                      self.transformed)
        self.assertIn('!query.filter_.empty()', self.transformed)
        search = self.method(
            self.transformed,
            'Result<DocPtrList> CollectionImpl::query(const SearchQuery &query) const {',
            'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {')
        self.assertIn('if (query_requires_optimize_gate(query))', search)
        self.assertIn('optimize_query_gate_lock.lock();', search)
        multi = self.method(
            self.transformed,
            'Result<DocPtrList> CollectionImpl::query(const MultiQuery &query) const {',
            'template <typename Query>\nResult<internal::QueryResultSnapshot>')
        self.assertIn('optimize_query_gate_mtx_', multi)

    def test_segment_snapshot_is_checked_before_collection_write_lock(self) -> None:
        getter = self.method(
            self.transformed,
            'std::vector<Segment::Ptr> CollectionImpl::get_all_segments() const {',
            'std::vector<Segment::Ptr> CollectionImpl::get_all_segments_unsafe() const {')
        self.assertLess(getter.index('optimize_query_snapshot_active_'),
                        getter.index('write_lock(write_mtx_)'))
        self.assertIn('return optimize_query_snapshot_;', getter)

    def test_optimize_dump_drops_segment_mutex_during_flush_and_seal(self) -> None:
        dump = self.method(
            self.segment_transformed,
            'Status SegmentImpl::dump() {',
            'void SegmentImpl::remove_writing_forward_block() {')
        self.assertIn('flush_impl(false)', dump)
        self.assertNotIn('flush_locked()', dump)
        inverted = dump.index('invert_indexers_->seal()')
        fts = dump.index('dump_fts_indexers()')
        first_lock_end = dump.index('  auto s = flush_impl(false);')
        self.assertLess(first_lock_end, inverted)
        self.assertLess(inverted, fts)
        self.assertIn('sealed_ = true;', dump)

    def test_fts_conversion_and_search_hooks_are_test_only(self) -> None:
        original = FTS_COLUMN_INDEXER_SOURCE.read_text(encoding='utf-8')
        self.assertNotIn('fts_seal_conversion_before_terms',
                         apply_fts_column_test_hooks(original))
        hooked = apply_fts_column_test_hooks(original, test_hooks=True)
        self.assertIn('fts_seal_conversion_before_terms', hooked)
        self.assertIn('fts_search_iterator_ready', hooked)
        self.assertLess(hooked.index('fts_search_iterator_ready'),
                        hooked.index('// Candidate-driven mode'))
        self.assertLess(hooked.index('fts_seal_conversion_before_terms'),
                        hooked.index('std::string current_term;'))

    def test_fts_side_cf_pause_hook_is_test_only(self) -> None:
        original = FTS_INDEXER_SOURCE.read_text(encoding='utf-8')
        self.assertNotIn('optimize_fts_side_cf_reset_before_drop',
                         apply_fts_seal_test_hook(original))
        hooked = apply_fts_seal_test_hook(original, test_hooks=True)
        reset_pos = hooked.index('indexer->reset_side_cfs();')
        hook_pos = hooked.index('optimize_fts_side_cf_reset_before_drop')
        drop_pos = hooked.index('fts_ctx_->drop_cf(name + suffix)', hook_pos)
        self.assertLess(reset_pos, hook_pos)
        self.assertLess(hook_pos, drop_pos)

    def test_segment_test_hooks_are_only_added_to_test_overlay(self) -> None:
        hooked = apply_optimize_read_safe_dump(
            SEGMENT_SOURCE.read_text(encoding='utf-8'), test_hooks=True)
        self.assertIn('zvec_m4_lock_split_emit_segment_test_hook', hooked)
        self.assertIn('optimize_dump_before_inverted_seal', hooked)
        self.assertNotIn('zvec_m4_lock_split_emit_segment_test_hook', self.segment_transformed)

    def test_smoke_reopens_after_flush_and_optimize(self) -> None:
        self.assertIn('Collection::Open(collection_path, CollectionOptions{false, true})',
                      TEST_HOOK_SOURCE.read_text(encoding='utf-8'))
        smoke = TEST_HOOK_SOURCE.read_text(encoding='utf-8')
        self.assertIn('after_flush_reopen.value().size() != 3', smoke)
        self.assertIn('after_optimize_reopen.value().size() != 3', smoke)
        self.assertIn('writer_waiting', smoke)
        self.assertIn('writer_thread.join();', smoke)
        self.assertIn('iterator_during_flush = collection->create_iterator()', smoke)
        self.assertIn('std::shared_lock<std::shared_mutex> write_lock(write_mtx_)',
                      self.transformed)
        flush_body = self.method(
            self.hooked,
            'Status CollectionImpl::flush() {',
            'Status CollectionImpl::flush_unsafe() {')
        self.assertLess(
            flush_body.index('write_lock(write_mtx_)'),
            flush_body.index('flush_vector_and_write_locks_acquired'))

    def test_benchmark_fragment_is_injected_only_for_benchmark_builds(self) -> None:
        bench_path = ROOT / 'research/m4-native/tests/native_query_lock_split_bench.cc.in'
        bench = bench_path.read_text(encoding='utf-8')
        self.assertIn('zvec_m4_lock_split_run_bench', bench)
        self.assertIn('bench_query phase=', bench)
        self.assertIn('bench_cycle phase=', bench)
        self.assertIn('zvec_m4_lock_split_run_bench_cycles', bench)
        injected = apply_benchmark_source(self.transformed, bench)
        self.assertIn('zvec_m4_lock_split_run_bench', injected)
        self.assertIn('#include <atomic>', injected)
        self.assertNotIn('zvec_m4_lock_split_run_bench', self.transformed)

    def test_rocksdb_flush_hooks_are_test_only_and_bracket_cf_flushes(self) -> None:
        original = ROCKSDB_CONTEXT_SOURCE.read_text(encoding='utf-8')
        self.assertEqual(apply_rocksdb_flush_test_hooks(original), original)
        hooked = apply_rocksdb_flush_test_hooks(original, test_hooks=True)
        self.assertIn('rocksdb_flush_cf_after', hooked)
        self.assertIn('rocksdb_flush_complete', hooked)
        cf_after = hooked.index('rocksdb_flush_cf_after')
        full_flush = hooked.index('if (auto s = db_->Flush(flush_opts_); s.ok())')
        self.assertLess(cf_after, full_flush)
        self.assertIn('cf->GetName().c_str()', hooked)

    def test_failure_injection_is_confined_to_test_overlay(self) -> None:
        self.assertNotIn('zvec_m4_lock_split_inject_failure', self.transformed)
        self.assertNotIn('zvec_m4_lock_split_inject_failure', self.segment_transformed)
        for event in ('writer_creation', 'optimize_version_apply'):
            with self.subTest(event=event):
                self.assertIn(
                    f'zvec_m4_lock_split_inject_failure("{event}")', self.hooked)
        segment_hooked = apply_optimize_read_safe_dump(
            SEGMENT_SOURCE.read_text(encoding='utf-8'), test_hooks=True)
        self.assertIn('optimize_inverted_seal', segment_hooked)
        fts_hooked = apply_fts_seal_test_hook(
            FTS_INDEXER_SOURCE.read_text(encoding='utf-8'), test_hooks=True)
        for event in ('fts_flush', 'fts_seal_all', 'fts_seal_conversion',
                      'fts_side_cf_reset'):
            with self.subTest(event=event):
                self.assertIn(
                    f'zvec_m4_lock_split_inject_failure("{event}")', fts_hooked)
        column_hooked = apply_fts_column_test_hooks(
            FTS_COLUMN_INDEXER_SOURCE.read_text(encoding='utf-8'), test_hooks=True)
        for event in ('fts_postings_conversion', 'fts_postings_flush'):
            with self.subTest(event=event):
                self.assertIn(
                    f'zvec_m4_lock_split_inject_failure("{event}")', column_hooked)

    def test_smoke_proves_mid_flush_io_overlap_and_failure_entry(self) -> None:
        smoke = TEST_HOOK_SOURCE.read_text(encoding='utf-8')
        self.assertIn('native_lock_split_smoke_cf_hook', smoke)
        self.assertIn('"rocksdb_flush_cf_after"', smoke)
        self.assertIn('flush_io_entered', smoke)
        self.assertIn('query_begin_time', smoke)
        self.assertIn('overlap_verified', smoke)
        self.assertIn('native_lock_split_run_failure_smoke', smoke)
        self.assertIn('zvec_m4_lock_split_run_failure_smoke', smoke)
        self.assertIn('native_lock_split_run_failure_case', smoke)

    def test_vector_lock_plan_changes_only_vector_flush_mode(self) -> None:
        base = (
            ('close', 'unique_lock', 'close', 'existing'),
            ('flush', 'unique_lock', 'flush_vector', 'existing'),
            ('flush', 'shared_lock', 'flush_scalar', 'existing'),
        )
        updated = vector_lock_plan(base)
        self.assertEqual(updated[1], ('flush', 'shared_lock', 'flush_vector', 'existing'))
        self.assertEqual(updated[2], base[2])
        with self.assertRaisesRegex(ValueError, 'expected exactly'):
            vector_lock_plan(base[:2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
