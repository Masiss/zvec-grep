#!/usr/bin/env python3
"""Synthetic counter-bound and operation/clock validation; no host sampling."""
import copy
import unittest
import stage_counter_bounds as analysis


def row(t_ms,value):
    return {'sample_start_monotonic_ns':int((t_ms-1)*1e6), 'monotonic_ns':int((t_ms+1)*1e6),
        'diagnostic_decision':{'operation_id':'synthetic-only','mode':'contention-aware'},
        'cgroup':{'cpu_stat':{'usage_usec':value,'throttled_usec':0},
                  **{key:{'some':{'total':value}} for key in ('cpu_pressure','io_pressure','memory_pressure')}}}


class BoundTests(unittest.TestCase):
    def setUp(self):
        self.samples=[row(10,0),row(20,10),row(30,30),row(40,50)]

    def test_full_spans_give_conservative_bounds_not_interpolation(self):
        d=analysis.counter_bounds(self.samples,15e6,35e6,('cpu_stat','usage_usec'))
        self.assertEqual((d['lower'],d['upper']),(20,50))
        self.assertTrue(d['boundary_coverage'])

    def test_subsample_stage_remains_broad(self):
        d=analysis.counter_bounds(self.samples,12e6,18e6,('cpu_stat','usage_usec'))
        self.assertEqual((d['lower'],d['upper']),(0,10))

    def test_serialized_clock_error_shrinks_lower_and_expands_coverage(self):
        path=('cpu_stat','usage_usec')
        exact=analysis.counter_bounds(self.samples,19e6,31e6,path)
        uncertain=analysis.counter_bounds(self.samples,19e6,31e6,path,1e6)
        self.assertEqual(exact['lower'],20)
        self.assertEqual(uncertain['lower'],0)
        boundary=analysis.counter_bounds(self.samples,11e6,39e6,path,1)
        self.assertIsNone(boundary['upper'])
        self.assertGreaterEqual(analysis.serialized_clock_error_ns(100000,100010),5000)

    def test_missing_boundary_never_means_low_cpu(self):
        d=analysis.counter_bounds(self.samples,0,35e6,('cpu_stat','usage_usec'))
        self.assertEqual(d['status'],'UNBOUNDED')
        self.assertIsNone(d['upper'])

    def test_invalid_or_reset_counter_is_rejected(self):
        for value in (-1,float('nan'),float('inf'),True,None):
            data=copy.deepcopy(self.samples);data[1]['cgroup']['cpu_stat']['usage_usec']=value
            with self.subTest(value=value), self.assertRaises(ValueError):
                analysis.counter_bounds(data,15e6,35e6,('cpu_stat','usage_usec'))
        data=copy.deepcopy(self.samples);data[2]['sample_start_monotonic_ns']=0
        with self.assertRaises(ValueError):
            analysis.counter_bounds(data,15e6,35e6,('cpu_stat','usage_usec'))

    def test_current_payload_join_and_scope_fail_closed(self):
        origin=1_000_000_000
        samples=[row(i*100+10,i*10) for i in range(21)]
        for r in samples:
            r['monotonic_ns']+=origin;r['sample_start_monotonic_ns']+=origin
        payload={'whole_start_monotonic_ns':origin,
            'native_vector_build_writer_thread_cpu_samples_ms':[0.001]*20,
            'writer_stage_timeline_ms':[{'round':i,'compacted':False,
                'vector_start_ms':i*100+15,'vector_end_ms':i*100+35} for i in range(20)]}
        result=analysis.analyze(payload,samples,'synthetic-only',2)
        self.assertEqual(len(result['cycles']),20)
        self.assertEqual(result['cycles'][0]['stages']['post_m4_sync']['status'],'NOT_EMITTED')
        self.assertIn('NON-QUALIFYING',result['evidence_class'])
        other=copy.deepcopy(samples);other[2]['diagnostic_decision']['operation_id']='other'
        with self.assertRaises(ValueError):analysis.analyze(payload,other,'synthetic-only',2)
        other=copy.deepcopy(samples);other[2]['cgroup']['cpu_stat'].pop('usage_usec')
        with self.assertRaises(KeyError):analysis.analyze(payload,other,'synthetic-only',2)
        truncated=copy.deepcopy(payload)
        truncated_events=truncated['writer_stage_timeline_ms']
        assert isinstance(truncated_events,list)
        truncated_events.pop()
        with self.assertRaises(ValueError):analysis.analyze(truncated,samples,'synthetic-only',2)


if __name__=='__main__':
    unittest.main(verbosity=2)
