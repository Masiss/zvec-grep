#!/usr/bin/env python3
"""Contained one-process-per-arm profiler for the M4 immutable write-amplification A/B."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'benchmark-policy.json'
BENCHMARK_POLICY = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
RESOURCE_THRESHOLDS = BENCHMARK_POLICY['resource_guards']
DISK_RESERVE_BYTES = RESOURCE_THRESHOLDS['var_tmp_bytes_min']
WRITE_AMP_CONTRACT = BENCHMARK_POLICY['immutable_write_amplification_pair']
WRITE_AMP_BASE_WORKLOAD = WRITE_AMP_CONTRACT['workload']
WRITE_AMP_PROFILES = WRITE_AMP_CONTRACT.get('workload_profiles', {})
WRITE_AMP_WORKLOAD = WRITE_AMP_BASE_WORKLOAD
WRITE_AMP_RESOURCE_CONTROLS = WRITE_AMP_CONTRACT['resource_controls']
MAX_APPLY_BATCH_MUTATIONS = 256
P4_CGROUP_BUDGET = {
    'schema_version': 1, 'approval_status': 'approved',
    'approval_reference': WRITE_AMP_CONTRACT['approval_reference'],
    **{key: WRITE_AMP_RESOURCE_CONTROLS[key] for key in (
        'memory_max_bytes', 'memory_swap_max_bytes', 'cpu_max', 'pids_max',
        'workspace_max_allocated_bytes', 'workspace_max_apparent_bytes')},
}
RUNNER_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'm4_containment_runner.py'
RUNNER_SPEC = importlib.util.spec_from_file_location('m4_containment_runner_p4', RUNNER_PATH)
assert RUNNER_SPEC and RUNNER_SPEC.loader
CONTAINMENT = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(CONTAINMENT)


def write_amp_workload_profile(name: str) -> dict[str, Any]:
    workload = dict(WRITE_AMP_BASE_WORKLOAD)
    if name == 'standard':
        return workload
    try:
        workload.update(WRITE_AMP_PROFILES[name])
    except KeyError as exc:
        raise ValueError(f'unknown immutable-write-amplification profile: {name}') from exc
    return workload


def utc_now():
    return datetime.now(UTC).isoformat()


def read_meminfo():
    out = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if value.strip().endswith('kB'):
            out[key] = int(value.strip().split()[0]) * 1024
    return out


def read_psi(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def host_snapshot(root: Path, label: str):
    mem = read_meminfo()
    fs_usage = shutil.disk_usage(root)
    ps = subprocess.run(
        ['ps', '-eo', 'pid,comm,pcpu,pmem,rss,args', '--sort=-pcpu'],
        check=True, text=True, capture_output=True
    ).stdout.splitlines()
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    try:
        loadavg = Path('/proc/loadavg').read_text().strip()
    except OSError:
        loadavg = None
    try:
        filesystem = subprocess.run(
            ['findmnt', '-no', 'SOURCE,FSTYPE,OPTIONS', '--target', str(root)],
            check=True, text=True, capture_output=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        filesystem = None
    snapshot = {
        'timestamp': utc_now(), 'label': label,
        'filesystem': filesystem,
        'cpu_count': os.cpu_count(), 'cpu_affinity': affinity,
        'loadavg': loadavg,
        'mem_available_bytes': mem.get('MemAvailable'),
        'mem_free_bytes': mem.get('MemFree'),
        'swap_free_bytes': mem.get('SwapFree'),
        'cpu_pressure': read_psi('/proc/pressure/cpu'),
        'memory_pressure': read_psi('/proc/pressure/memory'),
        'io_pressure': read_psi('/proc/pressure/io'),
        'disk': {'total_bytes': fs_usage.total, 'used_bytes': fs_usage.used,
                 'free_bytes': fs_usage.free, 'device': os.stat(root).st_dev},
        'cpu_count': os.cpu_count(),
        'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'top_processes': ps[:13],
    }
    snapshot['resource_guard_observations'] = resource_guard_observations(snapshot)
    return snapshot


def parse_psi_full_avg10(value):
    if not value:
        return None
    match = re.search(r'^full\s+avg10=([0-9]+(?:\.[0-9]+)?)', value, re.MULTILINE)
    return float(match.group(1)) if match else None


def resource_guard_observations(snapshot):
    mem = snapshot.get('mem_available_bytes')
    try:
        load1 = float((snapshot.get('loadavg') or '').split()[0])
    except (ValueError, IndexError):
        load1 = None
    observed = {
        'MemAvailable_kib_min': None if mem is None else mem / 1024,
        'var_tmp_bytes_min': (snapshot.get('disk') or {}).get('free_bytes'),
        'io_full_avg10_max': parse_psi_full_avg10(snapshot.get('io_pressure')),
        'load1_max': load1,
    }
    result = {}
    for key, value in observed.items():
        threshold = RESOURCE_THRESHOLDS[key]
        passed = (value is not None and
                  (value >= threshold if key in ('MemAvailable_kib_min', 'var_tmp_bytes_min')
                   else value <= threshold))
        result[key] = {
            'observed': value, 'threshold': threshold,
            'status': 'PASS' if passed else ('FAIL' if value is not None else 'UNKNOWN'),
            'disposition': 'enforced_retained_disk_reserve' if key == 'var_tmp_bytes_min'
                           else 'telemetry_only_never_blocks',
        }
    return result


def gate(snapshot):
    """Enforce policy disk reserve only; pressure, load and RAM remain telemetry."""
    if snapshot['disk']['free_bytes'] < DISK_RESERVE_BYTES:
        return f"filesystem free below {DISK_RESERVE_BYTES} bytes"
    return None


def proc_sample(pid, ticks_per_sec):
    base = Path(f'/proc/{pid}')
    try:
        stat = (base / 'stat').read_text()
        tail = stat[stat.rfind(')') + 2:].split()
        ticks = int(tail[11]) + int(tail[12])
        status = (base / 'status').read_text().splitlines()
        rss = None
        for line in status:
            if line.startswith('VmRSS:'):
                rss = int(line.split()[1]) * 1024
                break
        swap = None
        for line in status:
            if line.startswith('VmSwap:'):
                swap = int(line.split()[1]) * 1024
                break
        pss = None
        try:
            for line in (base / 'smaps_rollup').read_text().splitlines():
                if line.startswith('Pss:'):
                    pss = int(line.split()[1]) * 1024
                    break
        except OSError:
            pass
        io = {}
        try:
            for line in (base / 'io').read_text().splitlines():
                key, value = line.split(':', 1)
                if key in ('read_bytes', 'write_bytes', 'rchar', 'wchar'):
                    io[key] = int(value.strip())
        except OSError:
            pass
        return {'timestamp_monotonic': time.monotonic(), 'cpu_ticks': ticks,
                'cpu_seconds': ticks / ticks_per_sec, 'rss_bytes': rss,
                'pss_bytes': pss, 'swap_bytes': swap, 'io': io}
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        return None


def run_contained_arm(command, receipt_dir, resource_root, workspace_root, cwd, env,
                      runtime_seconds=180, policy_context=None):
    """Run one P4 arm under the shared cgroup-v2 watchdog and sample its child."""
    budget = (CONTAINMENT.contention_budget(policy_context) if policy_context is not None
              else dict(P4_CGROUP_BUDGET))
    pressure_limits = None if policy_context is not None else {
        'swapfree_min_kib': 0, 'memory_some_avg10_max': 0.0,
        'memory_full_avg10_max': 0.0,
    }
    resource_root = resource_root.resolve(strict=True)
    workspace_root = workspace_root.resolve(strict=True)
    cwd = cwd.resolve(strict=True)
    if receipt_dir.exists():
        raise FileExistsError(f'refusing pre-existing containment receipt directory: {receipt_dir}')
    outcome: dict[str, Any] = {}

    def invoke():
        try:
            outcome['receipt'] = CONTAINMENT.run_arm(
                command, receipt_dir, resource_root, workspace_root, cwd, runtime_seconds,
                sample_interval_ms=100, budget=budget, env=env,
                pressure_limits=pressure_limits, policy_context=policy_context)
        except BaseException as exc:
            outcome['error'] = exc

    worker = threading.Thread(target=invoke, name='m4-p4-contained-arm')
    worker.start()
    samples = []
    last = None
    ticks = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
    child_pid = None
    started_path = receipt_dir / 'command-start.json'
    while worker.is_alive():
        if child_pid is None and started_path.exists():
            try:
                child_pid = json.loads(started_path.read_text(encoding='utf-8'))['pid']
            except (OSError, ValueError, KeyError, TypeError):
                pass
        current = proc_sample(child_pid, ticks) if child_pid is not None else None
        if current:
            if last:
                dt = current['timestamp_monotonic'] - last['timestamp_monotonic']
                dc = current['cpu_seconds'] - last['cpu_seconds']
                current['cpu_pct_one_core'] = 100.0 * dc / dt if dt > 0 else 0.0
                current['cpu_pct_host'] = current['cpu_pct_one_core'] / (os.cpu_count() or 1)
            else:
                current['cpu_pct_one_core'] = current['cpu_pct_host'] = 0.0
            samples.append(current)
            last = current
        time.sleep(0.1)
    worker.join()
    if 'error' in outcome:
        raise outcome['error']
    receipt = outcome.get('receipt')
    if not isinstance(receipt, dict):
        raise RuntimeError('containment runner returned no receipt')
    return receipt, samples, budget


def percentile(values, p):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return float(vals[0])
    x = (len(vals) - 1) * p
    lo = int(math.floor(x))
    hi = int(math.ceil(x))
    return vals[lo] + (vals[hi] - vals[lo]) * (x - lo)


def parse_output(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]], dict[tuple[str, str], list[dict[str, Any]]]]:
    metrics, latencies, snapshots, write_diagnostics, hits, expected = [], [], [], [], {}, {}
    for line in path.read_text(errors='replace').splitlines():
        parts = line.split('\t')
        if not parts:
            continue
        if parts[0] == 'M' and len(parts) == 4:
            metrics.append({'stage': parts[1], 'name': parts[2], 'value': float(parts[3])})
        elif parts[0] == 'L' and len(parts) == 4:
            latencies.append({'stage': parts[1], 'kind': parts[2], 'ms': float(parts[3])})
        elif parts[0] == 'S' and len(parts) == 7:
            snapshots.append({'stage': parts[1], 'live_docs': int(parts[2]),
                              'tokens': int(parts[3]), 'files': int(parts[4]),
                              'runs': int(parts[5]), 'index_bytes': int(parts[6])})
        elif parts[0] == 'W' and len(parts) >= 4:
            values = {}
            for field in parts[3:]:
                if '=' in field:
                    key, value = field.split('=', 1)
                    try:
                        values[key] = int(value)
                    except ValueError:
                        try:
                            values[key] = float(value)
                        except ValueError:
                            values[key] = value
            write_diagnostics.append({'stage': parts[1], 'engine': parts[2], **values})
        elif parts[0] == 'H' and len(parts) == 5:
            hits.setdefault((parts[1], parts[2]), []).append({'pk': parts[3], 'score': float(parts[4])})
        elif parts[0] == 'E' and len(parts) == 4:
            expected.setdefault((parts[1], parts[2]), []).append({'pk': parts[3], 'score': 0.0})
        elif parts[0] == 'FATAL':
            raise RuntimeError(f"benchmark fatal in {path}: {' '.join(parts[1:])}")
    return metrics, latencies, snapshots, write_diagnostics, hits, expected


REQUIRED_SNAPSHOT_STAGES = frozenset({'initial', 'burst1', 'burst2', 'burst3', 'reopen'})


def snapshots_complete(snapshots: list[dict[str, Any]]) -> bool:
    return {snapshot['stage'] for snapshot in snapshots} == REQUIRED_SNAPSHOT_STAGES


def compare_retrieval(actual, expected):
    rows = []
    for stage, query in sorted(set(actual) | set(expected)):
        actual_ids = {hit['pk'] for hit in actual.get((stage, query), [])}
        expected_ids = {hit['pk'] for hit in expected.get((stage, query), [])}
        rows.append({'stage': stage, 'query': query,
                     'actual_hits': len(actual_ids), 'expected_hits': len(expected_ids),
                     'missing_from_actual': len(expected_ids - actual_ids),
                     'extra_in_actual': len(actual_ids - expected_ids),
                     'retrieval_equal': actual_ids == expected_ids})
    return rows


def compare_hits(native, m4):
    rows = []
    keys = sorted(set(native) | set(m4))
    for stage, query in keys:
        a, b = native.get((stage, query), []), m4.get((stage, query), [])
        amap = {x['pk']: x for x in a}
        bmap = {x['pk']: x for x in b}
        aset, bset = set(amap), set(bmap)
        common = aset & bset
        topn = 10
        a_top = [x['pk'] for x in a[:topn]]
        b_top = [x['pk'] for x in b[:topn]]
        denom = len(set(a_top) | set(b_top))
        rows.append({
            'stage': stage, 'query': query,
            'native_hits': len(a), 'm4_hits': len(b),
            'native_only': len(aset - bset), 'm4_only': len(bset - aset),
            'retrieval_equal': aset == bset,
            'top10_overlap': len(set(a_top) & set(b_top)),
            'top10_jaccard': len(set(a_top) & set(b_top)) / denom if denom else 1.0,
            'top10_order_equal': a_top == b_top,
            'common_score_absdiff_max': max((abs(amap[pk]['score'] - bmap[pk]['score']) for pk in common), default=0.0),
            'common_score_absdiff_p95': percentile([abs(amap[pk]['score'] - bmap[pk]['score']) for pk in common], .95),
        })
    return rows


def validate_mutation_batch_docs(role: str, value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_APPLY_BATCH_MUTATIONS:
        raise ValueError(
            f'{role} M4 mutation-batch-docs must be 1..{MAX_APPLY_BATCH_MUTATIONS}; '
            'm4::Index::ApplyBatch supports at most 256 mutations')
    return value


def m4_compaction_environment(role: str, workload: dict[str, Any]) -> dict[str, str]:
    if role not in ('control', 'candidate'):
        raise ValueError(f'unknown M4 compaction-budget role: {role}')
    total_runs = workload[f'{role}_max_total_runs']
    level_runs = workload[f'{role}_max_runs_per_level']
    stats_threshold = workload[f'{role}_stats_log_threshold']
    mutation_batch_docs = validate_mutation_batch_docs(
        role, workload[f'{role}_mutation_batch_docs'])
    coalesce_stats = workload[f'{role}_coalesce_stats']
    wal_compression_lz4 = workload[f'{role}_wal_compression_lz4']
    wal_lz4_acceleration = workload[f'{role}_wal_lz4_acceleration']
    wal_replay_probe = workload['wal_replay_probe']
    trace_mutation_latency = workload.get('trace_mutation_latency', False)
    if not isinstance(wal_replay_probe, bool):
        raise ValueError('WAL replay probe setting must be boolean')
    if not isinstance(trace_mutation_latency, bool):
        raise ValueError(f'{role} mutation-latency tracing setting must be boolean')
    if not isinstance(coalesce_stats, bool):
        raise ValueError(f'{role} M4 coalesce-stats setting must be boolean')
    if not isinstance(wal_compression_lz4, bool):
        raise ValueError(f'{role} M4 WAL-compression setting must be boolean')
    if not isinstance(wal_lz4_acceleration, int) or not 1 <= wal_lz4_acceleration <= 0x7FFFFFFF:
        raise ValueError(f'{role} M4 LZ4 acceleration must be a positive int')
    for name, value in (('max-total-runs', total_runs),
                        ('max-runs-per-level', level_runs)):
        if value <= 0 or value > 0xFFFFFFFF:
            raise ValueError(f'{role} M4 {name} must be a positive uint32')
    if stats_threshold <= 0 or stats_threshold > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f'{role} M4 stats-log-threshold must be a positive uint64')
    environment = {'M4_MAX_TOTAL_RUNS': str(total_runs),
                   'M4_MAX_RUNS_PER_LEVEL': str(level_runs),
                   'M4_STATS_LOG_THRESHOLD': str(stats_threshold),
                   'M4_MUTATION_BATCH_DOCS': str(mutation_batch_docs),
                   'M4_COALESCE_STATS': '1' if coalesce_stats else '0',
                   'M4_WAL_COMPRESSION_LZ4': '1' if wal_compression_lz4 else '0',
                   'M4_WAL_LZ4_ACCELERATION': str(wal_lz4_acceleration),
                   'M4_WAL_REPLAY_PROBE': '1' if wal_replay_probe else '0'}
    if trace_mutation_latency:
        environment['M4_P42_LATENCY'] = '1'
    return environment


def make_write_amplification_context(root: Path, resource_root: Path, quota_proof: dict[str, Any],
                                     binary: Path, dictionary: Path, workload: dict[str, Any],
                                     arm_role: str, temporary_directory: Path,
                                     execution_order: list[str] | None = None,
                                     workload_profile: str = 'standard'):
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    dictionary_sha = CONTAINMENT.directory_sha256(dictionary)
    return {
        'schema_version': 1, 'mode': CONTAINMENT.CONTENTION_MODE,
        'approval_status': 'approved',
        'approval_reference': WRITE_AMP_CONTRACT['approval_reference'],
        'execution_authorization_reference': WRITE_AMP_CONTRACT['execution_authorization_reference'],
        'risk_acceptance_reference': WRITE_AMP_CONTRACT['risk_acceptance_reference'],
        'resource_basis': 'bounded two-arm exploratory comparison; per-arm cgroup caps and retained disk reserve are hard; workspace traversal uncertainty remains explicit',
        'policy_sha256': hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
        'operation_id': root.name, 'operation_root': str(root), 'resource_root': str(resource_root),
        'workspace_quota_proof': quota_proof,
        'purpose': WRITE_AMP_CONTRACT['purpose'], 'workload': workload,
        'workload_profile': workload_profile, 'arm_role': arm_role,
        'execution_order': list(execution_order or WRITE_AMP_CONTRACT['default_arm_order']),
        'temporary_directory': str(temporary_directory.resolve()),
        'binary_path': str(binary), 'binary_sha256': binary_sha,
        'approved_binary_sha256': binary_sha, 'dictionary_path': str(dictionary),
        'dictionary_tree_sha256': dictionary_sha, 'approved_dictionary_tree_sha256': dictionary_sha,
        'io_pressure_mode': WRITE_AMP_CONTRACT['io_pressure_mode'], 'io_max': None,
        **WRITE_AMP_RESOURCE_CONTROLS,
    }


def write_amp_workload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Build the complete policy-bound workload identity used by main()."""
    return {
        'files': args.files, 'runs': args.runs,
        'control_arm': args.control_arm, 'candidate_arm': args.candidate_arm,
        'control_max_delta_docs': args.control_max_delta_docs,
        'candidate_max_delta_docs': args.candidate_max_delta_docs,
        'control_max_total_runs': args.control_max_total_runs,
        'candidate_max_total_runs': args.candidate_max_total_runs,
        'control_max_runs_per_level': args.control_max_runs_per_level,
        'candidate_max_runs_per_level': args.candidate_max_runs_per_level,
        'control_stats_log_threshold': args.control_stats_log_threshold,
        'candidate_stats_log_threshold': args.candidate_stats_log_threshold,
        'control_mutation_batch_docs': args.control_mutation_batch_docs,
        'candidate_mutation_batch_docs': args.candidate_mutation_batch_docs,
        'control_coalesce_stats': bool(args.control_coalesce_stats),
        'candidate_coalesce_stats': bool(args.candidate_coalesce_stats),
        'control_wal_compression_lz4': bool(args.control_wal_compression_lz4),
        'candidate_wal_compression_lz4': bool(args.candidate_wal_compression_lz4),
        'control_wal_lz4_acceleration': args.control_wal_lz4_acceleration,
        'candidate_wal_lz4_acceleration': args.candidate_wal_lz4_acceleration,
        'wal_replay_probe': WRITE_AMP_WORKLOAD['wal_replay_probe'],
        'trace_mutation_latency': WRITE_AMP_WORKLOAD['trace_mutation_latency'],
        'trace_fsync': args.trace_fsync,
        'm4_debt_policy': args.m4_debt_policy,
        'm4_debt_ratio': args.m4_debt_ratio,
        'm4_debt_cap': args.m4_debt_cap,
    }


def main():
    global WRITE_AMP_WORKLOAD
    profile_names = ['standard', *WRITE_AMP_PROFILES.keys()]
    profile_parser = argparse.ArgumentParser(add_help=False)
    profile_parser.add_argument('--workload-profile', choices=profile_names, default='standard')
    selected_profile, _ = profile_parser.parse_known_args()
    try:
        WRITE_AMP_WORKLOAD = write_amp_workload_profile(selected_profile.workload_profile)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    ap = argparse.ArgumentParser()
    ap.add_argument('--workload-profile', choices=profile_names, default='standard')
    ap.add_argument('--validate-only', action='store_true',
                    help='validate the quota, workload, binary and policy binding without creating files or launching')
    ap.add_argument('--binary', type=Path, required=True)
    ap.add_argument('--dictionary', type=Path, required=True)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--files', type=int, default=WRITE_AMP_WORKLOAD['files'])
    ap.add_argument('--runs', type=int, default=WRITE_AMP_WORKLOAD['runs'])
    ap.add_argument('--control-arm', default=WRITE_AMP_WORKLOAD['control_arm'])
    ap.add_argument('--candidate-arm', default=WRITE_AMP_WORKLOAD['candidate_arm'])
    ap.add_argument('--arm-order', choices=('control-first', 'candidate-first'),
                    default='control-first')
    ap.add_argument('--control-max-delta-docs', type=int, default=WRITE_AMP_WORKLOAD['control_max_delta_docs'])
    ap.add_argument('--candidate-max-delta-docs', type=int, default=WRITE_AMP_WORKLOAD['candidate_max_delta_docs'])
    ap.add_argument('--control-max-total-runs', type=int,
                    default=WRITE_AMP_WORKLOAD['control_max_total_runs'])
    ap.add_argument('--candidate-max-total-runs', type=int,
                    default=WRITE_AMP_WORKLOAD['candidate_max_total_runs'])
    ap.add_argument('--control-max-runs-per-level', type=int,
                    default=WRITE_AMP_WORKLOAD['control_max_runs_per_level'])
    ap.add_argument('--candidate-max-runs-per-level', type=int,
                    default=WRITE_AMP_WORKLOAD['candidate_max_runs_per_level'])
    ap.add_argument('--control-stats-log-threshold', type=int,
                    default=WRITE_AMP_WORKLOAD['control_stats_log_threshold'])
    ap.add_argument('--candidate-stats-log-threshold', type=int,
                    default=WRITE_AMP_WORKLOAD['candidate_stats_log_threshold'])
    ap.add_argument('--control-mutation-batch-docs', type=int,
                    default=WRITE_AMP_WORKLOAD['control_mutation_batch_docs'])
    ap.add_argument('--candidate-mutation-batch-docs', type=int,
                    default=WRITE_AMP_WORKLOAD['candidate_mutation_batch_docs'])
    ap.add_argument('--control-coalesce-stats', type=int, choices=(0, 1),
                    default=int(WRITE_AMP_WORKLOAD['control_coalesce_stats']))
    ap.add_argument('--candidate-coalesce-stats', type=int, choices=(0, 1),
                    default=int(WRITE_AMP_WORKLOAD['candidate_coalesce_stats']))
    ap.add_argument('--control-wal-compression-lz4', type=int, choices=(0, 1),
                    default=int(WRITE_AMP_WORKLOAD['control_wal_compression_lz4']))
    ap.add_argument('--candidate-wal-compression-lz4', type=int, choices=(0, 1),
                    default=int(WRITE_AMP_WORKLOAD['candidate_wal_compression_lz4']))
    ap.add_argument('--control-wal-lz4-acceleration', type=int,
                    default=WRITE_AMP_WORKLOAD['control_wal_lz4_acceleration'])
    ap.add_argument('--candidate-wal-lz4-acceleration', type=int,
                    default=WRITE_AMP_WORKLOAD['candidate_wal_lz4_acceleration'])
    ap.add_argument('--trace-fsync', action='store_true',
                    help='trace fsync/fdatasync calls with detached strace (adds tracing overhead)')
    ap.add_argument('--m4-debt-policy', action='store_true',
                    help='enable the opt-in M4 structural debt policy for the candidate arm only')
    ap.add_argument('--m4-debt-ratio', type=float, default=0.01)
    ap.add_argument('--m4-debt-cap', type=int, default=250000)
    args = ap.parse_args()
    binary = args.binary.resolve()
    dictionary = args.dictionary.resolve()
    quota_contract = BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota']
    resource_root = Path(quota_contract['workspace_subvolume']).resolve(strict=True)
    quota_proof = CONTAINMENT.verify_workspace_quota(resource_root, quota_contract)
    root = args.root.resolve()
    try:
        root.relative_to(resource_root)
    except ValueError as exc:
        raise SystemExit(f'result root must be inside the quota-backed workspace {resource_root}') from exc
    if root.parent != resource_root:
        raise SystemExit('result root must be a direct child of the quota-backed workspace')
    dictionary_files = ['jieba.dict.utf8', 'hmm_model.utf8']
    if any(not (dictionary / name).is_file() for name in dictionary_files):
        raise SystemExit(f"missing pinned Jieba dictionary files under {dictionary}")
    if root.exists():
        raise SystemExit(f"refusing pre-existing result root: {root}")
    for role, value in (('control', args.control_max_delta_docs),
                        ('candidate', args.candidate_max_delta_docs)):
        if value <= 0 or value > 0xFFFFFFFF:
            raise SystemExit(f'{role} M4 max-delta-docs must be a positive uint32')
    for role, value in (('control', args.control_max_total_runs),
                        ('candidate', args.candidate_max_total_runs),
                        ('control', args.control_max_runs_per_level),
                        ('candidate', args.candidate_max_runs_per_level)):
        if value <= 0 or value > 0xFFFFFFFF:
            raise SystemExit(f'{role} M4 compaction run limit must be a positive uint32')
    if args.m4_debt_policy and args.candidate_arm == 'native':
        raise SystemExit('M4 debt policy cannot be assigned to the native candidate arm')
    if args.m4_debt_ratio <= 0 or args.m4_debt_cap <= 0:
        raise SystemExit('M4 debt ratio and cap must be positive')
    for role, value in (('control', args.control_wal_lz4_acceleration),
                        ('candidate', args.candidate_wal_lz4_acceleration)):
        if value <= 0 or value > 0x7FFFFFFF:
            raise SystemExit(f'{role} M4 LZ4 acceleration must be a positive int')
    workload = write_amp_workload_from_args(args)
    try:
        for role in ('control', 'candidate'):
            validate_mutation_batch_docs(role, workload[f'{role}_mutation_batch_docs'])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if workload != WRITE_AMP_WORKLOAD:
        raise SystemExit('workload differs from the bounded immutable-write-amplification policy contract')
    if (args.control_arm == args.candidate_arm and
            args.control_max_delta_docs == args.candidate_max_delta_docs and
            args.control_max_total_runs == args.candidate_max_total_runs and
            args.control_max_runs_per_level == args.candidate_max_runs_per_level and
            args.control_stats_log_threshold == args.candidate_stats_log_threshold and
            args.control_mutation_batch_docs == args.candidate_mutation_batch_docs and
            args.control_coalesce_stats == args.candidate_coalesce_stats and
            args.control_wal_compression_lz4 == args.candidate_wal_compression_lz4 and
            args.control_wal_lz4_acceleration == args.candidate_wal_lz4_acceleration):
        raise SystemExit('control and candidate need distinct arms or M4 option settings')
    operation_id = root.name
    if not operation_id.startswith('m4-write-amplification-pair-'):
        raise SystemExit('result root must be a fresh m4-write-amplification-pair-<operation-id> directory')
    execution_order = (['control', 'candidate'] if args.arm_order == 'control-first'
                       else ['candidate', 'control'])
    if execution_order not in WRITE_AMP_CONTRACT['arm_orders']:
        raise SystemExit('arm order is not allowed by the immutable-write-amplification policy contract')
    arm_by_role = {'control': args.control_arm, 'candidate': args.candidate_arm}
    run_specs: list[tuple[int, str, str]] = []
    for pair in range(1, args.runs + 1):
        for role in execution_order:
            run_specs.append((pair, role, arm_by_role[role]))

    validation_contexts = []
    for role in ('control', 'candidate'):
        phase = 'pair-first' if role == execution_order[0] else 'pair-second'
        temporary_directory = root / phase / 'workspace' / 'tmp'
        context = make_write_amplification_context(
            root, resource_root, quota_proof, binary, dictionary, workload, role,
            temporary_directory, execution_order, args.workload_profile)
        budget = CONTAINMENT.contention_budget(context)
        validation_contexts.append({'role': role, 'budget': budget})
    if args.validate_only:
        print(json.dumps({
            'status': 'VALIDATED_NO_LAUNCH', 'operation_id': operation_id,
            'quota_proof': quota_proof, 'workload': workload,
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'dictionary_tree_sha256': CONTAINMENT.directory_sha256(dictionary),
            'policy_sha256': hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
            'resource_controls': WRITE_AMP_RESOURCE_CONTROLS,
            'validation_contexts': validation_contexts,
        }, sort_keys=True))
        return
    root.mkdir(parents=True, exist_ok=False)

    config = {
        'created_at': utc_now(), 'binary': str(binary),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'dictionary_dir': str(dictionary),
        'dictionary_sha256': {name: hashlib.sha256((dictionary / name).read_bytes()).hexdigest()
                              for name in dictionary_files},
        'root': str(root), 'files': args.files, 'paired_runs': args.runs,
        'workload_profile': args.workload_profile,
        'control_arm': args.control_arm, 'candidate_arm': args.candidate_arm,
        'execution_order': execution_order,
        'm4_max_delta_docs': {'control': args.control_max_delta_docs,
                              'candidate': args.candidate_max_delta_docs},
        'm4_max_total_runs': {'control': args.control_max_total_runs,
                              'candidate': args.candidate_max_total_runs},
        'm4_max_runs_per_level': {'control': args.control_max_runs_per_level,
                                  'candidate': args.candidate_max_runs_per_level},
        'm4_stats_log_threshold': {'control': args.control_stats_log_threshold,
                                   'candidate': args.candidate_stats_log_threshold},
        'm4_mutation_batch_docs': {'control': args.control_mutation_batch_docs,
                                   'candidate': args.candidate_mutation_batch_docs},
        'm4_coalesce_stats': {'control': bool(args.control_coalesce_stats),
                              'candidate': bool(args.candidate_coalesce_stats)},
        'm4_wal_compression_lz4': {'control': bool(args.control_wal_compression_lz4),
                                   'candidate': bool(args.candidate_wal_compression_lz4)},
        'm4_wal_lz4_acceleration': {'control': args.control_wal_lz4_acceleration,
                                    'candidate': args.candidate_wal_lz4_acceleration},
        'wal_replay_probe': workload['wal_replay_probe'],
        'trace_fsync': args.trace_fsync,
        'benchmark_policy_id': BENCHMARK_POLICY['policy_id'],
        'execution_model': 'each arm runs through the operation-bound contention-aware m4_containment_runner with verified cgroup-v2 ownership, runtime timeout, host OOM/disk/swap controls, and explicit workspace-uncertainty receipts',
        'cgroup_resource_budget': P4_CGROUP_BUDGET,
        'evidence_class': CONTAINMENT.CONTENTION_EVIDENCE,
        'workspace_policy': WRITE_AMP_CONTRACT['workspace_policy'],
        'm4_debt_policy': {'enabled': args.m4_debt_policy,
                           'ratio': args.m4_debt_ratio,
                           'max_merge_docs': args.m4_debt_cap,
                           'applies_to': {'role': 'candidate', 'arm': args.candidate_arm}
                           if args.m4_debt_policy else None},
        'run_order': [{'pair': p, 'role': role, 'arm': arm} for p, role, arm in run_specs],
        'stop_conditions': {
            'host_context_telemetry_only': {
                'policy_thresholds': {key: RESOURCE_THRESHOLDS[key] for key in
                                      ('MemAvailable_kib_min', 'io_full_avg10_max', 'load1_max')},
                'disposition': 'recorded and compared for context only; never blocks launch or aborts an arm',
            },
            'before_each_arm': {'filesystem_free_min_bytes': DISK_RESERVE_BYTES},
            'during_arm': {
                'filesystem_free_min_bytes': DISK_RESERVE_BYTES,
                'cgroup_budget': P4_CGROUP_BUDGET,
                'runtime_limit_seconds': 180,
                'runner_enforces': ['cgroup cap/distress', 'host OOM', 'disk reserve', 'swap capacity', 'required telemetry', 'watchdog cleanup'],
                'workspace': 'monitored soft budget; incomplete live-write traversal remains UNKNOWN/INCOMPLETE, never a capacity PASS',
                'action': 'shared cgroup-v2 runner aborts and verifies cleanup; stop suite',
            },
        },
        'environment': {k: os.environ.get(k) for k in (
            'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'ZVEC_RESEARCH_SEGMENT_ONLY',
            'ZVEC_RESEARCH_MAX_SEGMENTS', 'ZVEC_RESEARCH_SCALAR_REUSE',
            'ZVEC_SCALAR_BATCH', 'M4_FAULT', 'M4_DEBT_POLICY', 'M4_BENCH_ROOT',
            'M4_P42_LATENCY', 'M4_P41_DIAGNOSTICS')},
        'temporary_directory_policy': 'per-arm <workspace>/tmp is passed as TMPDIR; sibling index/ directory and tmp/ are both workspace-accounted',
        'query_timing': {'warmup_passes_per_stage': 1,
                         'scope': 'every configured query once, untimed, before collecting timed samples'},
        'host_before_suite': host_snapshot(root, 'suite_before'),
    }
    reason = gate(config['host_before_suite'])
    if reason:
        raise SystemExit(f"preflight stop: {reason}")
    (root / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    runs: list[dict[str, Any]] = []
    stop_reason = None
    for arm_position, (pair, role, arm) in enumerate(run_specs):
        label = 'pair-first' if arm_position == 0 else 'pair-second'
        arm_dir = root / label
        arm_workspace = arm_dir / 'workspace'
        index_dir = arm_workspace / 'index'
        arm_workspace.mkdir(parents=True)
        index_dir.mkdir()
        temp_dir = arm_workspace / 'tmp'
        temp_dir.mkdir()
        before = host_snapshot(root, label + '_before')
        reason = gate(before)
        (arm_dir / 'host-before.json').write_text(json.dumps(before, indent=2) + '\n')
        if reason:
            stop_reason = f'{label} not started: {reason}'
            break
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'LD_LIBRARY_PATH'}}
        if arm != 'native':
            max_delta = (args.control_max_delta_docs if role == 'control'
                         else args.candidate_max_delta_docs)
            env['M4_MAX_DELTA_DOCS'] = str(max_delta)
            env.update(m4_compaction_environment(role, workload))
        if arm != 'native':
            env['M4_P41_DIAGNOSTICS'] = '1'
        if args.m4_debt_policy and role == 'candidate' and arm != 'native':
            env['M4_DEBT_POLICY'] = '1'
            env['M4_DEBT_RATIO'] = str(args.m4_debt_ratio)
            env['M4_DEBT_CAP'] = str(args.m4_debt_cap)
        env['LC_ALL'] = 'C.UTF-8'
        env['TMPDIR'] = str(temp_dir.resolve())
        benchmark_cmd = [str(binary), arm, str(index_dir), str(args.files), str(dictionary)]
        trace_path = arm_dir / 'syscalls.log' if args.trace_fsync else None
        cmd = benchmark_cmd
        if trace_path is not None:
            cmd = ['strace', '-D', '-f', '-ttt', '-T', '-yy',
                   '-e', 'trace=fsync,fdatasync', '-o', str(trace_path),
                   '--', *benchmark_cmd]
        containment_dir = arm_dir / 'containment'
        log_path = arm_dir / 'stdout.log'
        err_path = arm_dir / 'stderr.log'
        process_start = time.monotonic()
        policy_context = make_write_amplification_context(
            root, resource_root, quota_proof, binary, dictionary, workload, role,
            temp_dir, execution_order, args.workload_profile)
        receipt, samples, _budget = run_contained_arm(
            cmd, containment_dir, resource_root, arm_workspace, arm_workspace, env,
            runtime_seconds=WRITE_AMP_RESOURCE_CONTROLS['runtime_limit_seconds'],
            policy_context=policy_context)
        process_end = time.monotonic()
        for source, target in ((containment_dir / 'stdout.log', log_path),
                               (containment_dir / 'stderr.log', err_path)):
            if source.exists():
                shutil.copyfile(source, target)
            else:
                target.write_text('', encoding='utf-8')
        rc = receipt.get('workload_returncode')
        completed_with_caveats = receipt.get('status') in {
            'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'COMPLETED_WITH_WORKSPACE_WARNINGS'}
        abort_reason = (None if receipt.get('status') == 'PASS' or
                        (completed_with_caveats and rc == 0) else
                        receipt.get('reason') or receipt.get('status'))
        profile_path = arm_dir / 'profile.jsonl'
        profile_path.write_text(''.join(json.dumps(s) + '\n' for s in samples))
        after = host_snapshot(root, label + '_after')
        (arm_dir / 'host-after.json').write_text(json.dumps(after, indent=2) + '\n')
        io_final = samples[-1].get('io', {}) if samples else {}
        watchdog_summary = receipt.get('watchdog_summary') or {}
        final_cgroup = watchdog_summary.get('final_cgroup') or {}
        sync_counts = None
        if trace_path is not None and trace_path.exists():
            sync_lines = [line for line in trace_path.read_text(errors='replace').splitlines()
                          if re.search(r'\b(?:f|fd)sync\(', line)]
            wal_lines = [line for line in sync_lines if 'wal' in line.lower()]
            sync_counts = {
                'fsync_calls': sum('fsync(' in line and 'fdatasync(' not in line for line in sync_lines),
                'fdatasync_calls': sum('fdatasync(' in line for line in sync_lines),
                'wal_sync_calls': len(wal_lines),
                'wal_sync_successes': sum('= 0' in line for line in wal_lines),
                'trace_path': str(trace_path),
            }
        run = {'pair': pair, 'role': role, 'arm': arm, 'max_delta_docs':
                   args.control_max_delta_docs if role == 'control' else args.candidate_max_delta_docs,
               'stats_log_threshold': (args.control_stats_log_threshold if role == 'control'
                                       else args.candidate_stats_log_threshold),
               'mutation_batch_docs': (args.control_mutation_batch_docs if role == 'control'
                                       else args.candidate_mutation_batch_docs),
               'coalesce_stats': bool(args.control_coalesce_stats if role == 'control'
                                      else args.candidate_coalesce_stats),
               'wal_compression_lz4': bool(args.control_wal_compression_lz4 if role == 'control'
                                           else args.candidate_wal_compression_lz4),
               'wal_lz4_acceleration': (args.control_wal_lz4_acceleration if role == 'control'
                                        else args.candidate_wal_lz4_acceleration),
               'label': label, 'cmd': cmd,
               'returncode': rc, 'abort_reason': abort_reason,
               'containment_status': receipt.get('status'),
               'measurement_quality': receipt.get('measurement_quality'),
               'evidence_class': receipt.get('evidence_class'),
               'policy_context_digest': CONTAINMENT.contention_context_digest(policy_context),
               'containment_receipt': str(containment_dir / 'receipt.json'),
               'cgroup_peak_memory_bytes': watchdog_summary.get('peak_cgroup_memory_peak_bytes_observed'),
               'cgroup_peak_swap_bytes': watchdog_summary.get('peak_cgroup_swap_peak_bytes_observed'),
               'process_tree_write_bytes': final_cgroup.get('process_tree_write_bytes'),
               'cgroup_io_controller_available': receipt.get('cgroup_io_controller_available'),
               'syscall_sync_counts': sync_counts,
               'started_before': before['timestamp'], 'ended_after': after['timestamp'],
               'process_wall_seconds': process_end - process_start,
               'samples': len(samples), 'peak_rss_bytes': max((s.get('rss_bytes') or 0 for s in samples), default=0),
               'peak_pss_bytes': max((s.get('pss_bytes') or 0 for s in samples), default=0),
               'peak_swap_bytes': max((s.get('swap_bytes') or 0 for s in samples), default=0),
               'mean_cpu_pct_one_core': statistics.fmean([s.get('cpu_pct_one_core', 0) for s in samples[1:]]) if len(samples) > 1 else 0,
               'peak_cpu_pct_one_core': max((s.get('cpu_pct_one_core', 0) for s in samples), default=0),
               'final_io': io_final, 'stdout': str(log_path), 'stderr': str(err_path),
               'profile': str(profile_path), 'host_before': str(arm_dir / 'host-before.json'),
               'host_after': str(arm_dir / 'host-after.json')}
        runs.append(run)
        (root / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
        if abort_reason or rc != 0:
            stop_reason = abort_reason or f'{label} exited {rc}'
            break
    config['host_after_suite'] = host_snapshot(root, 'suite_after')
    config['stopped_reason'] = stop_reason
    (root / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    (root / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    if stop_reason:
        raise SystemExit(f"profile suite stopped: {stop_reason}")

    parsed: list[dict[str, Any]] = []
    for run in runs:
        metrics, latencies, snaps, write_diagnostics, hits, expected = parse_output(Path(run['stdout']))
        parsed.append({'run': run, 'metrics': metrics, 'latencies': latencies,
                       'snapshots': snaps, 'write_diagnostics': write_diagnostics,
                       'hits': hits, 'expected': expected})
    pair_results: list[dict[str, Any]] = []
    for pair in range(1, args.runs + 1):
        nr = next(x for x in parsed if x['run']['pair'] == pair and x['run']['role'] == 'control')
        mr = next(x for x in parsed if x['run']['pair'] == pair and x['run']['role'] == 'candidate')
        ns = {x['stage']: x for x in nr['snapshots']}
        ms = {x['stage']: x for x in mr['snapshots']}
        snapshot_parity = []
        for stage in sorted(set(ns) & set(ms)):
            snapshot_parity.append({'stage': stage,
                'docs_equal': ns[stage]['live_docs'] == ms[stage]['live_docs'],
                'tokens_equal': ns[stage]['tokens'] == ms[stage]['tokens'],
                'files_equal': ns[stage]['files'] == ms[stage]['files'],
                'control': ns[stage], 'candidate': ms[stage]})
        hit_comparisons = compare_hits(nr['hits'], mr['hits'])
        control_vs_oracle = compare_retrieval(nr['hits'], nr['expected'])
        candidate_vs_oracle = compare_retrieval(mr['hits'], mr['expected'])
        oracle_keys_equal = set(nr['expected']) == set(mr['expected']) and all(
            {h['pk'] for h in nr['expected'].get(key, [])} ==
            {h['pk'] for h in mr['expected'].get(key, [])}
            for key in set(nr['expected']) | set(mr['expected']))
        pair_results.append({'pair': pair,
                             'snapshots_complete': snapshots_complete(nr['snapshots']) and snapshots_complete(mr['snapshots']),
                             'snapshot_parity': snapshot_parity,
                             'oracle_outputs_equal': oracle_keys_equal,
                             'control_vs_oracle': control_vs_oracle,
                             'candidate_vs_oracle': candidate_vs_oracle,
                             'hit_comparisons': hit_comparisons})

    latency_groups: dict[tuple[str, str, str, str], list[float]] = {}
    run_profiles = []
    for item in parsed:
        run = item['run']
        arm = run['arm']
        role = run['role']
        for latency in item['latencies']:
            key = (role, arm, latency['stage'], latency['kind'])
            latency_groups.setdefault(key, []).append(latency['ms'])
        stage_cpu: dict[str, float] = {}
        stage_values: dict[str, dict[str, float]] = {}
        for metric in item['metrics']:
            stage_values.setdefault(metric['stage'], {})[metric['name']] = metric['value']
            if metric['name'] == 'cpu_ms':
                stage_cpu[metric['stage']] = metric['value']
        indexing_cpu = sum(value for stage, value in stage_cpu.items()
                           if stage == 'initial_indexing' or stage.endswith('_mutations'))
        maintenance_cpu = sum(value for stage, value in stage_cpu.items()
                              if stage == 'initial_flush' or stage.endswith('_flush_maintenance'))
        search_cpu = sum(value for stage, value in stage_cpu.items() if stage.endswith('_search'))
        validation_cpu = sum(value for stage, value in stage_cpu.items()
                             if stage.endswith('_validation'))
        total_cpu = stage_values.get('whole_workload', {}).get('cpu_ms', 0.0)
        total_write = stage_values.get('whole_workload', {}).get('physical_write_bytes', 0.0)
        logical_text = sum(values.get('logical_input_text_bytes', 0.0)
                           for values in stage_values.values())
        run['cpu_breakdown_ms'] = {'indexing_and_mutations': indexing_cpu,
                                   'flush_maintenance': maintenance_cpu,
                                   'search': search_cpu,
                                   'oracle_and_snapshot_validation': validation_cpu,
                                   'other_setup_and_fixture': max(0.0, total_cpu - indexing_cpu - maintenance_cpu - search_cpu - validation_cpu)}
        run['process_cpu_seconds'] = total_cpu / 1000.0
        run['physical_write_bytes'] = total_write
        run['logical_text_bytes_written'] = logical_text
        run['text_byte_write_amplification'] = total_write / logical_text if logical_text else None
        run_profiles.append({'pair': run['pair'], 'role': role, 'arm': arm,
                             'process_wall_seconds': run['process_wall_seconds'],
                             'process_cpu_seconds': run['process_cpu_seconds'],
                             'cpu_breakdown_ms': run['cpu_breakdown_ms'],
                             'physical_write_bytes': total_write,
                             'logical_text_bytes_written': logical_text,
                             'text_byte_write_amplification': run['text_byte_write_amplification'],
                             'stage_metrics': stage_values,
                             'snapshots': item['snapshots']})

    write_diagnostics = []
    for item in parsed:
        run = item['run']
        for diagnostic in item['write_diagnostics']:
            write_diagnostics.append({'pair': run['pair'], 'role': run['role'],
                                      'arm': run['arm'], **diagnostic})
    latency_summary = []
    for (role, arm, stage, kind), values in sorted(latency_groups.items()):
        latency_summary.append({'role': role, 'arm': arm, 'stage': stage, 'kind': kind, 'n': len(values),
                                'p50_ms': percentile(values, .50),
                                'p95_ms': percentile(values, .95),
                                'p99_ms': percentile(values, .99)})
    all_parity = (len(pair_results) == args.runs and
                  all(p['snapshots_complete'] and all(
                      x['docs_equal'] and x['tokens_equal'] and x['files_equal']
                      for x in p['snapshot_parity']) for p in pair_results))
    workspace_uncertain = any(run.get('containment_status') != 'PASS' for run in runs)
    report = {'pair_results': pair_results, 'runs': runs, 'run_profiles': run_profiles,
              'write_diagnostics': write_diagnostics,
              'latency_summary': latency_summary,
              'evidence_class': CONTAINMENT.CONTENTION_EVIDENCE,
              'summary': {'run_count': len(runs), 'all_docs_tokens_files_equal': all_parity,
                  'measurement_quality': 'INCOMPLETE_WORKSPACE_CAVEAT' if workspace_uncertain else 'COMPLETE',
                  'workspace_accounting_complete': not workspace_uncertain,
                  'performance_eligible': False,
                  'oracle_outputs_equal_across_arms': len(pair_results) == args.runs and all(p['oracle_outputs_equal'] for p in pair_results),
                  'control_oracle_mismatch_cases': sum(
                      not h['retrieval_equal'] for pair in pair_results for h in pair['control_vs_oracle']),
                  'candidate_oracle_mismatch_cases': sum(
                      not h['retrieval_equal'] for pair in pair_results for h in pair['candidate_vs_oracle']),
                  'retrieval_mismatch_cases': sum(
                      not h['retrieval_equal'] for pair in pair_results for h in pair['hit_comparisons']),
                  'top10_order_mismatch_cases': sum(
                      not h['top10_order_equal'] for pair in pair_results for h in pair['hit_comparisons']),
                  'top10_overlap_mean': statistics.fmean(
                      h['top10_jaccard'] for pair in pair_results for h in pair['hit_comparisons'])
                      if any(pair['hit_comparisons'] for pair in pair_results) else None}}
    (root / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    (root / 'write-diagnostics.json').write_text(json.dumps(write_diagnostics, indent=2) + '\n')
    lines = [f"# {args.control_arm} vs {args.candidate_arm} paired workload profile", '',
             f"M4 stats-event coalescing: control={bool(args.control_coalesce_stats)}, candidate={bool(args.candidate_coalesce_stats)}.",
             f"M4 LZ4 WAL compression: control={bool(args.control_wal_compression_lz4)}, candidate={bool(args.candidate_wal_compression_lz4)}; "
             f"acceleration={args.control_wal_lz4_acceleration} vs {args.candidate_wal_lz4_acceleration}.",
             f"Control max_delta_docs: {args.control_max_delta_docs}; candidate max_delta_docs: {args.candidate_max_delta_docs}; "
             f"stats_log_threshold: {args.control_stats_log_threshold} vs {args.candidate_stats_log_threshold} bytes; "
             f"mutation batches: {args.control_mutation_batch_docs} vs {args.candidate_mutation_batch_docs}.",
             f"Runs: {len(runs)}; stop reason: none.",
             f"Evidence class: **{CONTAINMENT.CONTENTION_EVIDENCE}**; measurement quality: **{report['summary']['measurement_quality']}**; performance-eligible: **false**.",
             'Raw logs, profiles, host snapshots, and query-level comparisons are retained alongside this report.', '',
             '## Resource profile', '', '| Pair | Role | Arm | Process wall (s) | Process CPU (s) | Peak RSS (MiB) | Peak PSS (MiB) | Peak swap (MiB) | Cgroup peak memory (MiB) | Process-tree write bytes | Mean CPU (% one core) | Peak CPU (% one core) | Physical writes (B) | Text-byte WA |',
             '|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in runs:
        lines.append(f"| {r['pair']} | {r['role']} | {r['arm']} | {r['process_wall_seconds']:.2f} | {r.get('process_cpu_seconds', 0):.2f} | {r['peak_rss_bytes']/2**20:.1f} | {r['peak_pss_bytes']/2**20:.1f} | {r.get('peak_swap_bytes', 0)/2**20:.1f} | {(r.get('cgroup_peak_memory_bytes') or 0)/2**20:.1f} | {r.get('process_tree_write_bytes') or 0} | {r['mean_cpu_pct_one_core']:.1f} | {r['peak_cpu_pct_one_core']:.1f} | {r.get('physical_write_bytes', 0):.0f} | {r.get('text_byte_write_amplification', 0):.2f}x |")
    lines += ['', '## CPU attribution (process CPU milliseconds, per run)', '',
              '| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |',
              '|---:|---|---|---:|---:|---:|---:|---:|']
    for r in runs:
        cpu = r.get('cpu_breakdown_ms', {})
        lines.append(f"| {r['pair']} | {r['role']} | {r['arm']} | {cpu.get('indexing_and_mutations', 0):.1f} | {cpu.get('flush_maintenance', 0):.1f} | {cpu.get('search', 0):.1f} | {cpu.get('oracle_and_snapshot_validation', 0):.1f} | {cpu.get('other_setup_and_fixture', 0):.1f} |")
    lines += ['', '## M4 snapshot write attribution', '',
              'These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.', '',
              '| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |',
              '|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for d in write_diagnostics:
        lines.append(f"| {d['pair']} | {d['role']} | {d['arm']} | {d['stage']} | {d.get('wal_bytes', 0)} | {d.get('wal_bytes_appended', 0)} | {d.get('segment_bytes', 0)} | {d.get('tombstone_bytes', 0)} | {d.get('stats_log_bytes', 0)} | {d.get('reported_bytes_written_ex_wal', 0)} | {d.get('segment_bytes_written', 0)} | {d.get('wal_fsyncs', 0)} | {d.get('stats_log_fsync_counter', 0)} | {d.get('compactions', 0)} | {d.get('compaction_merges', 0)} | {d.get('dead_docs', 0)} |")
    lines += ['', '## LZ4 WAL codec attribution', '',
              'Cumulative per-index diagnostics count only eligible frames passed to LZ4; accepted-frame byte savings compare original payload bytes with compressed payload bytes. Compression CPU uses the calling writer thread clock.',
              '', '| Pair | Role | Arm | Stage | Attempts | Attempted input B | Compressed frames | Accepted input B | Output B | Saved B | Codec CPU ms |',
              '|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for profile in run_profiles:
        stages = profile.get('stage_metrics', {})
        if not isinstance(stages, dict):
            continue
        for stage, metrics in stages.items():
            if not isinstance(metrics, dict):
                continue
            attempts = metrics.get('wal_lz4_attempts', 0.0)
            if attempts > 0:
                input_bytes = metrics.get('wal_lz4_input_bytes', 0.0)
                output_bytes = metrics.get('wal_lz4_output_bytes', 0.0)
                lines.append(f"| {profile['pair']} | {profile['role']} | {profile['arm']} | {stage.removeprefix('snapshot_')} | {attempts:.0f} | {metrics.get('wal_lz4_attempted_input_bytes', 0.0):.0f} | {metrics.get('wal_lz4_compressed_frames', 0.0):.0f} | {input_bytes:.0f} | {output_bytes:.0f} | {input_bytes - output_bytes:.0f} | {metrics.get('wal_lz4_compress_cpu_ms', 0.0):.3f} |")
    def stage_metrics_of(profile: Any) -> dict[str, Any]:
        value = profile.get('stage_metrics') if isinstance(profile, dict) else None
        return value if isinstance(value, dict) else {}

    def has_wal_replay_probe(profile: Any) -> bool:
        stage = stage_metrics_of(profile).get('wal_replay_probe_write')
        return isinstance(stage, dict)

    wal_replay_profiles = [p for p in run_profiles if has_wal_replay_probe(p)]
    if wal_replay_profiles:
        lines += ['', '## Durable WAL replay probe', '',
                  'The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.',
                  '', '| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |',
                  '|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for p in wal_replay_profiles:
            stages: dict[str, Any] = stage_metrics_of(p)
            write_stage = stages.get('wal_replay_probe_write', {})
            cache_stage = stages.get('wal_replay_probe_cold_cache', {})
            open_stage = stages.get('wal_replay_probe_open', {})
            replay_stage = stages.get('wal_replay_probe', {})
            write_stage = write_stage if isinstance(write_stage, dict) else {}
            cache_stage = cache_stage if isinstance(cache_stage, dict) else {}
            open_stage = open_stage if isinstance(open_stage, dict) else {}
            replay_stage = replay_stage if isinstance(replay_stage, dict) else {}
            ack = latency_groups.get((p['role'], p['arm'], 'wal_replay_probe',
                                      'durable_apply_batch_ack_ms'), [])
            ack_p95 = percentile(ack, .95) if ack else None
            ack_p95_text = f'{ack_p95:.3f}' if ack_p95 is not None else 'n/a'
            lines.append(
                f"| {p['pair']} | {p['role']} | {p['arm']} | "
                f"{replay_stage.get('wal_bytes_before_replay', 0):.0f} | "
                f"{write_stage.get('process_cpu_ms', 0):.1f} | {ack_p95_text} | "
                f"{cache_stage.get('index_file_bytes', 0):.0f} | "
                f"{cache_stage.get('resident_pages_after_advice', 0):.0f}/"
                f"{cache_stage.get('total_pages', 0):.0f} | "
                f"{open_stage.get('wall_ms', 0):.3f} | "
                f"{open_stage.get('process_cpu_ms', 0):.1f} | "
                f"{open_stage.get('replayed_live_docs', 0):.0f} |")
    lines += ['', '## Latency percentiles (ms; pooled by role, arm and stage)', '',
              '| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |',
              '|---|---|---|---|---:|---:|---:|---:|']
    for item in latency_summary:
        lines.append(f"| {item['role']} | {item['arm']} | {item['stage']} | {item['kind']} | {item['n']} | {item['p50_ms']:.3f} | {item['p95_ms']:.3f} | {item['p99_ms']:.3f} |")
    lines += ['', '## WAL/system sync counts', '',
              'Counts below come from `strace -D` on the target process; the syscall trace adds wall-time overhead and is not used as an untraced baseline.',
              '| Pair | Role | Arm | fsync | fdatasync | path contains WAL | WAL sync succeeded |',
              '|---:|---|---|---:|---:|---:|---:|']
    for r in runs:
        counts = r.get('syscall_sync_counts') or {}
        lines.append(f"| {r['pair']} | {r['role']} | {r['arm']} | {counts.get('fsync_calls', 'not traced')} | {counts.get('fdatasync_calls', 'not traced')} | {counts.get('wal_sync_calls', 'not traced')} | {counts.get('wal_sync_successes', 'not traced')} |")
    lines += ['', '## Correctness and logical parity', '',
              f"All live-doc/token/file snapshots equal: **{report['summary']['all_docs_tokens_files_equal']}**.",
              f"Workspace accounting complete: **{report['summary']['workspace_accounting_complete']}**. Incomplete live-write traversal is retained as UNKNOWN/INCOMPLETE, never converted to a capacity PASS.",
              f"Independent logical-oracle mismatches: {args.control_arm} **{report['summary']['control_oracle_mismatch_cases']}**, {args.candidate_arm} **{report['summary']['candidate_oracle_mismatch_cases']}**; oracle outputs equal between arms: **{report['summary']['oracle_outputs_equal_across_arms']}**.",
              f"Control-vs-candidate retrieval-mismatch stage/query/pair cases: **{report['summary']['retrieval_mismatch_cases']}**.",
              f"Top-10 order mismatch cases: **{report['summary']['top10_order_mismatch_cases']}**; mean top-10 Jaccard: **{report['summary']['top10_overlap_mean']:.3f}**.",
              'Per-query PK set differences, ranking differences, and BM25 score deltas are in `comparison.json`; none are suppressed.', '']
    for pair in pair_results:
        for label, key in ((args.control_arm, 'control_vs_oracle'),
                           (args.candidate_arm, 'candidate_vs_oracle')):
            diagnostic = [row for row in pair[key]
                          if row['query'] == 'known_json_parse_directory']
            for row in diagnostic:
                lines.append(f"Known `json parse` diagnostic, pair {pair['pair']}, {label}, {row['stage']}: actual={row['actual_hits']} oracle={row['expected_hits']} missing={row['missing_from_actual']} extra={row['extra_in_actual']}; equality={row['retrieval_equal']}.")
    lines += ['', '## Guardrails and caveats', '',
              f"- This operation runs one pair in {'→'.join(arm_by_role[role] for role in execution_order)} order; before each timed P4 stage, every query is issued once untimed to reduce cold-page/arm-order contamination. P4 stages do not drop OS page cache. When enabled, the separate WAL replay probe advises only that fresh index's files with `POSIX_FADV_DONTNEED` and records `mincore` residency; zero resident pages are required to call those target files cold, while system/library caches remain warm. Counterbalanced repeats require fresh operation roots.",
              '- RSS/PSS and CPU are sampled every ~100 ms; CPU utilization is percent of one logical core.',
              '- `/proc/self/io` `write_bytes` is process-attributed physical I/O; cgroup process-tree `write_bytes` is recorded separately. Text-byte WA = process-attributed physical writes / UTF-8 source-text bytes ingested or updated. Neither is a device-wide physical-media attribution; host diskstats deltas are context only.',
              '- The selected arms, mutation APIs, explicit flushes, sync tracing, and optional debt-policy settings are in `config.json`; native collection-batch DML and M4 ApplyBatch are not cross-engine atomic transactions.',
              '- When explicitly enabled, the S5 debt policy is applied only to the M4 candidate after the base flush/reopen; default remains OFF.',
              '- Durability boundaries differ: native appends batched writes and explicitly flushes at stages; M4 ApplyBatch fsyncs each bounded transaction when configured. WAL/system sync counts are reported separately; do not interpret this as equal durability latency.',
              '- FTS/INVERT only: vectors are excluded from both arms; non-indexed `document_id` is omitted as a duplicate of logical PK.',
              '- No native `optimize` or M4 `CompactNow` was invoked; automatic work triggered by each arm’s flush is measured as maintenance.',
              '- Synthetic baseline mix: 20% JSON, 20% logs, 35% code, 25% Markdown/text; 4–10 fragments/file, ~2.8k text characters/fragment. Each burst adds 8 JSON, 8 log, and 4 code files.',
              '- Workspace budgets are monitored soft budgets in this owner-authorized exploratory mode. Live writes may make traversal incomplete; uncertainty is preserved, the receipt remains non-PASS, and the report is never performance-eligible or qualification evidence.',
              '- See `config.json`, raw logs, profiles, containment receipts, and per-arm host-before/after snapshots for provenance.']
    (root / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(f"profiled {len(runs)} arm runs; result root: {root}")
    print(f"snapshot parity={report['summary']['all_docs_tokens_files_equal']} retrieval-mismatch-cases={report['summary']['retrieval_mismatch_cases']}")


if __name__ == '__main__':
    main()
