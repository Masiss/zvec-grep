#!/usr/bin/env python3
"""Research-only per-arm cgroup-v2 runner for M4 measurements.

The public CLI enforces operation-level resource guards from
research/m4-native/docs/benchmark-policy.json. Their values come from a
historical successful-run envelope, not a universal host-safety model. Resource
ceilings are applied only from an explicitly approved JSON budget; absent a
budget this is process-tree containment/telemetry, not a claim that use is bounded.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from typing import Any


def strict_json_text(text: str | bytes) -> Any:
    """Malformed control/evidence data must raise, never become a default/PASS."""
    try:
        return json.loads(text)
    except (ValueError, UnicodeError):
        raise


def strict_json_file(path: pathlib.Path) -> Any:
    try:
        return strict_json_text(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        raise


def writable_capability(path: pathlib.Path) -> bool:
    try:
        return path.exists() and os.access(path, os.W_OK)
    except OSError:
        return False  # absent/unreadable capability cannot authorize a resource cap


CGROOT = pathlib.Path('/sys/fs/cgroup')
BTRFS_SYSFS_ROOT = pathlib.Path('/sys/fs/btrfs')
POLICY_PATH = pathlib.Path(__file__).resolve().parents[1] / 'docs' / 'benchmark-policy.json'
BENCHMARK_POLICY = strict_json_file(POLICY_PATH)
if BENCHMARK_POLICY.get('schema_version') != 1 or BENCHMARK_POLICY.get('policy_id') != 'm4-benchmark-operation-policy-v1':
    raise RuntimeError(f'unsupported M4 benchmark policy: {POLICY_PATH}')
RESOURCE_THRESHOLDS = BENCHMARK_POLICY['resource_guards']
GUARDS = {
    'memavailable_min_kib': RESOURCE_THRESHOLDS['MemAvailable_kib_min'],
    'var_tmp_free_min_bytes': RESOURCE_THRESHOLDS['var_tmp_bytes_min'],
    'io_full_avg10_max': RESOURCE_THRESHOLDS['io_full_avg10_max'],
    'load1_max': RESOURCE_THRESHOLDS['load1_max'],
}
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec='microseconds')


def boottime_ns() -> int:
    """Return suspend-inclusive monotonic time for watchdog gap checks."""
    clock_id = getattr(time, 'CLOCK_BOOTTIME', None)
    if clock_id is None:
        raise RuntimeError('CLOCK_BOOTTIME is required for contention monitoring')
    return time.clock_gettime_ns(clock_id)


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8').strip()


def directory_sha256(path: pathlib.Path) -> str:
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f'expected directory for tree digest: {root}')
    digest = hashlib.sha256()
    files = sorted(root.rglob('*'))
    for item in files:
        if item.is_symlink():
            raise ValueError(f'symlink not allowed in pinned input tree: {item}')
        if item.is_file():
            relative = item.relative_to(root).as_posix().encode('utf-8')
            digest.update(len(relative).to_bytes(8, 'big'))
            digest.update(relative)
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


def parse_kv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                out[fields[0].removesuffix(':')] = int(fields[1])
            except ValueError:
                continue
    return out


def parse_pressure(path: pathlib.Path) -> dict[str, dict[str, float]] | None:
    try:
        text = read_text(path)
    except OSError:
        return None
    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for field in fields[1:]:
            if '=' not in field:
                continue
            key, value = field.split('=', 1)
            try:
                values[key] = float(value)
            except ValueError:
                continue
        result[fields[0]] = values
    return result or None


def read_meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        m = re.match(r'^(MemAvailable|SwapFree|MemTotal|SwapTotal):\s+(\d+)', line)
        if m:
            result[m.group(1) + '_kib'] = int(m.group(2))
    return result


def read_vmstat() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in pathlib.Path('/proc/vmstat').read_text().splitlines():
        key, _, value = line.partition(' ')
        if key in {'pswpin', 'pswpout'}:
            result[key + '_pages'] = int(value)
        elif key in {'oom_kill', 'pgscan_direct', 'pgsteal_direct'} or key.startswith('allocstall_'):
            result[key] = int(value)
    return result


def read_diskstats() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in pathlib.Path('/proc/diskstats').read_text().splitlines():
        f = line.split()
        if len(f) < 14:
            continue
        name = f[2]
        result[name] = {
            'major': int(f[0]), 'minor': int(f[1]),
            'reads_completed': int(f[3]), 'sectors_read': int(f[5]),
            'read_ms': int(f[6]), 'writes_completed': int(f[7]),
            'sectors_written': int(f[9]), 'write_ms': int(f[10]),
            'io_in_progress': int(f[11]), 'io_ms': int(f[12]),
            'weighted_io_ms': int(f[13]),
        }
    return result


def host_snapshot(resource_root: pathlib.Path) -> dict[str, Any]:
    mem = read_meminfo()
    try:
        load = [float(x) for x in pathlib.Path('/proc/loadavg').read_text().split()[:3]]
    except (OSError, ValueError):
        load = []
    st = os.statvfs(resource_root)
    # These host context metrics are optional observations, never an admission,
    # runtime-abort, or recovery prerequisite.
    io_pressure = parse_pressure(pathlib.Path('/proc/pressure/io'))
    mem_pressure = parse_pressure(pathlib.Path('/proc/pressure/memory'))
    return {
        'timestamp_utc': utc_now(), 'monotonic_ns': time.monotonic_ns(),
        'meminfo_kib': mem, 'loadavg': load,
        'io_pressure': io_pressure, 'memory_pressure': mem_pressure,
        'resource_root_free_bytes': st.f_bavail * st.f_frsize,
        'vmstat': read_vmstat(), 'diskstats': read_diskstats(),
        'cpu_pressure': parse_pressure(pathlib.Path('/proc/pressure/cpu')),
    }


def failure_class(status: str | None = None, reason: str | None = None,
                  returncode: int | None = None) -> str:
    if reason == 'cgroup_oom_event':
        return 'OOM_ABORT'
    if reason == 'supervisor_lost':
        return 'CONTROLLER_CRASH_ABORT'
    if reason == 'operator_requested_stop':
        return 'OPERATOR_ABORT'
    if reason == 'runtime_timeout':
        return 'TIMEOUT_ABORT'
    if reason == 'workspace_capacity_unverifiable':
        return 'WORKSPACE_CAPACITY_UNVERIFIABLE'
    if reason in ('workspace_allocated_watchdog', 'workspace_apparent_watchdog'):
        return 'WORKSPACE_LIMIT_OBSERVED_EXCEEDED'
    if status == 'PREFLIGHT_BLOCKED':
        return 'OPERATION_GUARD_REFUSAL'
    if status in {'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'COMPLETED_WITH_WORKSPACE_WARNINGS'} and returncode == 0:
        return 'NORMAL_EXIT_WITH_WORKSPACE_CAVEATS'
    if status == 'PASS' and returncode == 0:
        return 'NORMAL_EXIT'
    if status == 'SIGNAL_EXIT' or (returncode is not None and returncode < 0):
        return 'SIGNAL_EXIT'
    if status == 'NONZERO_EXIT' or (returncode not in (None, 0)):
        return 'NONZERO_EXIT'
    if status in {'LAUNCH_FAILED', 'SUPERVISOR_ERROR'}:
        return 'CONTROLLER_ERROR'
    if status == 'WATCHDOG_ABORT':
        return 'SAFETY_OR_TELEMETRY_ABORT'
    return status or 'UNKNOWN'


def pressure_baseline_failures(state: dict[str, Any], limits: dict[str, Any] | None) -> list[str]:
    if limits is None:
        return []
    failures: list[str] = []
    swapfree = state.get('meminfo_kib', {}).get('SwapFree_kib')
    if swapfree is None or swapfree < limits['swapfree_min_kib']:
        failures.append(f"SwapFree={swapfree!r} KiB < baseline floor {limits['swapfree_min_kib']} KiB")
    # PSI is retained as host-context telemetry only.  It is neither an
    # admission criterion nor an in-run abort condition.
    return failures


def load_pressure_baseline(path: pathlib.Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = strict_json_file(path)
    swap_floor = value.get('swapfree_min_kib')
    if (isinstance(swap_floor, bool) or not isinstance(swap_floor, int) or swap_floor < 0):
        raise ValueError('pressure baseline requires non-negative integer swapfree_min_kib')
    result: dict[str, int | float | None] = {'swapfree_min_kib': swap_floor}
    for key in ('memory_some_avg10_max', 'memory_full_avg10_max'):
        observed = value.get(key)
        if (isinstance(observed, bool) or not isinstance(observed, (int, float))
                or not math.isfinite(observed) or observed < 0):
            observed = None
        result[key] = observed
    return result


def resource_guard_decisions(state: dict[str, Any]) -> list[dict[str, Any]]:
    available = state.get('meminfo_kib', {}).get('MemAvailable_kib')
    free = state.get('resource_root_free_bytes')
    try:
        io_full = state['io_pressure']['full']['avg10']
    except (KeyError, TypeError):
        io_full = None
    load1 = (state.get('loadavg') or [None])[0]
    observed = {
        'MemAvailable_kib_min': available, 'var_tmp_bytes_min': free,
        'io_full_avg10_max': io_full, 'load1_max': load1,
    }
    decisions = []
    for gate_id, spec in BENCHMARK_POLICY['resource_guard_metadata'].items():
        key = spec['threshold_key']
        value, threshold, operator = observed[key], RESOURCE_THRESHOLDS[key], spec['operator']
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value))
        passed = valid and (value >= threshold if operator == '>=' else value <= threshold)
        decisions.append({
            'gate_id': gate_id, 'threshold_key': key, 'metric': spec['metric'], 'unit': spec['unit'],
            'operator': operator, 'observed': value, 'threshold': threshold,
            'status': 'PASS' if passed else ('FAIL' if valid else 'UNKNOWN'),
            'enforced': gate_id == 'M4-RG-DISK',
            'disposition': ('hard retained-filesystem reserve' if gate_id == 'M4-RG-DISK'
                            else 'observation-only; never blocks launch or aborts an arm'),
            'enforcement': spec['enforcement'], 'evidence_class': spec['evidence_class'],
            'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
            'failure_scope': spec['failure_scope'], 'recovery': spec['recovery'],
            'owner_approval_required_to_change': spec['owner_approval_required_to_change'],
        })
    return decisions


CONTENTION_MODE = 'contention-aware'
CONTENTION_EVIDENCE = 'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING'
M4_SCALAR_RESOURCE_PURPOSE = 'm4-scalar-open-resource-rehearsal'
M4_SCALAR_LATENCY_PURPOSE = 'm4-scalar-open-latency-diagnostic'
M4_SHARED_COLLECTION_PAIR_PURPOSE = 'm4-shared-collection-pair'
M4_LOCK_CONTENTION_MICROBENCH_PURPOSE = 'm4-lock-contention-microbench'
M4_WRITE_AMPLIFICATION_PURPOSE = 'immutable-write-amplification-pair'
M4_RUST_LIFECYCLE_PURPOSE = 'm4-rust-full-lifecycle-pair'
M4_SCALAR_OPEN_PURPOSES = frozenset({M4_SCALAR_RESOURCE_PURPOSE, M4_SCALAR_LATENCY_PURPOSE})


def validate_rust_lifecycle_context(context: dict[str, Any]) -> None:
    contract = BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']
    operation_id = context.get('operation_id')
    operation_root = pathlib.Path(context.get('operation_root', '')).resolve()
    resource_root = pathlib.Path(context.get('resource_root', '')).resolve()
    order = context.get('execution_order')
    role = context.get('arm_role')
    arm_name = 'pair-first' if order and role == order[0] else 'pair-second'
    arm_root = operation_root / arm_name
    workspace_root = arm_root / 'workspace'
    expected_source = workspace_root / 'source'
    binary_path = pathlib.Path(context.get('binary_path', '')).resolve()
    expected_resource_controls = contract['resource_controls']
    zvec_memory_limit = contract['workload'].get('zvec_native_memory_limit_bytes')
    if (isinstance(zvec_memory_limit, bool) or not isinstance(zvec_memory_limit, int) or
            zvec_memory_limit <= 0 or zvec_memory_limit >= expected_resource_controls['memory_max_bytes']):
        raise ValueError('Rust lifecycle Zvec memory setting must be positive and below the cgroup hard cap')
    if (not isinstance(operation_id, str) or not re.fullmatch(r'm4-rust-lifecycle-pair-[a-z0-9-]{8,48}', operation_id) or
            operation_root.name != operation_id or not operation_root.is_relative_to(resource_root) or
            context.get('arm_name') != arm_name or
            context.get('purpose') != M4_RUST_LIFECYCLE_PURPOSE or
            context.get('approval_reference') != contract['approval_reference'] or
            context.get('execution_authorization_reference') != contract['execution_authorization_reference'] or
            context.get('risk_acceptance_reference') != contract['risk_acceptance_reference'] or
            context.get('workload') != contract['workload'] or
            context.get('reader_query_interval_ms') != contract['workload']['reader_query_interval_ms'] or
            context.get('reader_query_max_samples_per_pass') != contract['workload']['reader_query_max_samples_per_pass'] or
            context.get('reader_query_queue_capacity') != contract['workload']['reader_query_queue_capacity'] or
            context.get('zvec_native_memory_limit_bytes') != zvec_memory_limit or
            context.get('arm_role') not in contract['arm_roles'] or
            context.get('execution_order') not in contract['arm_orders'] or
            context.get('resource_root') != contract['resource_root'] or
            pathlib.Path(context.get('workspace_root', '')).resolve() != workspace_root.resolve() or
            pathlib.Path(context.get('source_root', '')).resolve() != expected_source.resolve() or
            pathlib.Path(context.get('index_root', '')).resolve() != (workspace_root / 'index').resolve() or
            pathlib.Path(context.get('report_path', '')).resolve() != (workspace_root / 'gate-b-report.json').resolve() or
            pathlib.Path(context.get('temporary_directory', '')).resolve() != (workspace_root / 'tmp').resolve() or
            binary_path.name != context.get('binary_name') or
            not binary_path.is_relative_to(pathlib.Path(__file__).resolve().parents[3] / 'rust/target/debug/deps') or
            not binary_path.is_file() or
            context.get('binary_sha256') != hashlib.sha256(binary_path.read_bytes()).hexdigest() or
            context.get('approved_binary_sha256') != context.get('binary_sha256') or
            context.get('io_pressure_mode') != contract['io_pressure_mode'] or
            any(context.get(key) != value for key, value in expected_resource_controls.items())):
        raise ValueError('Rust lifecycle context differs from its explicit bounded exploratory contract')
    quota = verify_workspace_quota(resource_root, contract['workspace_quota'])
    proof = context.get('workspace_quota_proof')
    identity_fields = ('status', 'attestation_path', 'workspace_root', 'filesystem_uuid',
                       'subvolume_id', 'qgroup_id', 'max_referenced_bytes',
                       'accounting_mode', 'attestation_sha256')
    if (not isinstance(proof, dict) or
            any(quota.get(key) != proof.get(key) for key in identity_fields) or
            not isinstance(proof.get('live_headroom'), dict) or
            proof['live_headroom'].get('required_free_bytes') !=
            contract['workspace_quota'].get('minimum_free_bytes')):
        raise ValueError('Rust lifecycle context lacks the current root-attested workspace quota proof')


def lock_contention_workload(context: dict[str, Any]) -> dict[str, Any]:
    contract = BENCHMARK_POLICY['lock_contention_microbench']
    profile = context.get('workload_profile', 'contention')
    if profile == 'contention':
        return contract['workload']
    if profile in ('memory_probe', 'memory_trace', 'memory_trace_after_snapshot_fix',
                   'memory_trace_scale2_after_snapshot_fix', 'memory_churn_two_versions',
                   'memory_churn_two_versions_eviction',
                   'memory_churn_two_versions_memory_opt',
                   'memory_churn_two_versions_trie_stream',
                   'memory_churn_two_versions_inline_child',
                   'memory_churn_two_versions_concurrent_open',
                   'memory_churn_two_versions_concurrent_allocator',
                   'writer_group_commit',
                   'writer_group_commit_per_upsert',
                   'writer_group_commit_snapshot_rebase',
                   'flush_reader_staging_pair',
                   'writer_group_commit_flush_reader_pair',
                   'writer_group_commit_pending_flush_reader_pair'):
        return contract[profile]['workload']
    raise ValueError('unknown lock-contention workload profile')
M4_SCALAR_LATENCY_EVIDENCE = 'EXPLORATORY / NON-QUALIFYING'


def is_m4_scalar_open_exploratory(context: dict[str, Any] | None) -> bool:
    return bool(context and context.get('purpose') in M4_SCALAR_OPEN_PURPOSES)


def contention_evidence_class(context: dict[str, Any] | None) -> str:
    return M4_SCALAR_LATENCY_EVIDENCE if context and context.get('purpose') == M4_SCALAR_LATENCY_PURPOSE else CONTENTION_EVIDENCE


def is_m4_single_arm_capacity_plan(context: dict[str, Any] | None) -> bool:
    return is_m4_scalar_open_exploratory(context) or bool(
        context and context.get('purpose') == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE)


# Fixed, tiny workload only; never an alternate admission path for native commands.
REHEARSAL_WORKLOAD = {'synthetic_only': True, 'payload_bytes': 32768, 'hold_seconds': 1}
REHEARSAL_CODE = (
    'import os,pathlib,json,threading; '
    'p=pathlib.Path("synthetic-rehearsal.bin"); data=b"r"*32768; '
    'f=p.open("wb"); f.write(data); f.flush(); os.fsync(f.fileno()); f.close(); '
    'assert p.read_bytes()==data; '
    'print(json.dumps({"synthetic_only":True,"proc_io":pathlib.Path("/proc/self/io").read_text(),'
    '"cgroup":pathlib.Path("/proc/self/cgroup").read_text()}),flush=True); '
    'threading.Event().wait(1); p.unlink()'
)


def validate_contention_context(context: dict[str, Any]) -> dict[str, Any]:
    """An explicit, immutable per-operation resource plan; never an ambient override."""
    if not isinstance(context, dict) or context.get('mode') != CONTENTION_MODE:
        raise ValueError('invalid contention policy context')
    for key in ('approval_reference', 'execution_authorization_reference', 'risk_acceptance_reference',
                'resource_basis', 'operation_root', 'resource_root'):
        if not isinstance(context.get(key), str) or not context[key].strip():
            raise ValueError(f'contention plan requires {key}')
    if context.get('approval_status') != 'approved' or context.get('schema_version') != 1:
        raise ValueError('contention plan must be explicitly approved, schema_version=1')
    if context.get('policy_sha256') != hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest():
        raise ValueError('contention plan policy identity mismatch')
    operation = context.get('operation_id', '')
    if not re.fullmatch(r'(?:m4-contention-[a-zA-Z0-9_-]+|m4-scalar-open-(?:rehearsal|latency-diagnostic)-[a-zA-Z0-9_-]+|m4-shared-collection-pair-[a-zA-Z0-9_-]+|m4-lock-contention-microbench-[a-zA-Z0-9_-]+|m4-write-amplification-pair-[a-zA-Z0-9_-]+|m4-rust-lifecycle-pair-[a-zA-Z0-9_-]+)', operation):
        raise ValueError('invalid contention operation_id')
    for key in ('operation_root', 'resource_root'):
        if str(pathlib.Path(context[key]).resolve()) != context[key]:
            raise ValueError(f'{key} must be a canonical absolute path')
    if pathlib.Path(context['operation_root']).name != operation:
        raise ValueError('operation root/id mismatch')
    if context.get('purpose') == 'synthetic-containment-rehearsal':
        if (not operation.startswith('m4-contention-rehearsal-') or
                context.get('workload') != REHEARSAL_WORKLOAD or
                context.get('binary_sha256') != hashlib.sha256(pathlib.Path(sys.executable).read_bytes()).hexdigest()):
            raise ValueError('rehearsal requires fixed synthetic workload and current Python identity')
    elif context.get('purpose') in M4_SCALAR_OPEN_PURPOSES:
        expected = {'scalar_counts': list(range(7)), 'vector_types': ['HNSW', 'FLAT'],
                    'repetitions': 3, 'api_opens': 84, 'documents': 0, 'queries': 0}
        binary_path = pathlib.Path(context.get('binary_path', '')).resolve()
        latency_mode = context.get('purpose') == M4_SCALAR_LATENCY_PURPOSE
        expected_name = ('m4_native_scalar_open_latency_diagnostic' if latency_mode
                         else 'm4_native_scalar_open_resource_rehearsal')
        id_prefix = 'm4-scalar-open-latency-diagnostic-' if latency_mode else 'm4-scalar-open-rehearsal-'
        if (not operation.startswith(id_prefix) or context.get('workload') != expected or
                binary_path.name != expected_name or not binary_path.is_file() or
                context.get('binary_sha256') != hashlib.sha256(binary_path.read_bytes()).hexdigest() or
                context.get('approved_binary_sha256') != context.get('binary_sha256')):
            raise ValueError('M4 scalar-open mode requires exact 84-open workload and pinned binary identity')
    elif context.get('purpose') == M4_RUST_LIFECYCLE_PURPOSE:
        validate_rust_lifecycle_context(context)
    elif context.get('purpose') == M4_SHARED_COLLECTION_PAIR_PURPOSE:
        contract = BENCHMARK_POLICY['shared_collection_pair']
        binary_path = pathlib.Path(context.get('binary_path', '')).resolve()
        dictionary_path = pathlib.Path(context.get('dictionary_path', '')).resolve()
        controls = contract['resource_controls']
        if (not operation.startswith('m4-shared-collection-pair-') or
                context.get('approval_reference') != contract['approval_reference'] or
                context.get('execution_authorization_reference') != contract['execution_authorization_reference'] or
                context.get('risk_acceptance_reference') != contract['risk_acceptance_reference'] or
                context.get('workload') != contract['workload'] or
                context.get('arm_role') not in contract['arm_roles'] or
                context.get('resource_root') != contract['resource_root'] or
                binary_path.name != contract['binary_name'] or not binary_path.is_file() or
                context.get('binary_sha256') != hashlib.sha256(binary_path.read_bytes()).hexdigest() or
                context.get('approved_binary_sha256') != context.get('binary_sha256') or
                not dictionary_path.is_dir() or
                context.get('dictionary_tree_sha256') != context.get('approved_dictionary_tree_sha256') or
                context.get('dictionary_tree_sha256') != directory_sha256(dictionary_path) or
                context.get('io_pressure_mode') != contract['io_pressure_mode'] or
                any(context.get(key) != value for key, value in controls.items())):
            raise ValueError('shared-collection pair context differs from the owner-approved operation contract')
    elif context.get('purpose') == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE:
        contract = BENCHMARK_POLICY['lock_contention_microbench']
        expected_workload = lock_contention_workload(context)
        binary_path = pathlib.Path(context.get('binary_path', '')).resolve()
        dictionary_path = pathlib.Path(context.get('dictionary_path', '')).resolve()
        if (not operation.startswith('m4-lock-contention-microbench-') or
                context.get('approval_reference') != contract['approval_reference'] or
                context.get('execution_authorization_reference') != contract['execution_authorization_reference'] or
                context.get('risk_acceptance_reference') != contract['risk_acceptance_reference'] or
                context.get('workload') != expected_workload or
                context.get('arm_role') != 'microbench' or
                context.get('resource_root') != contract['resource_root'] or
                binary_path.name != contract['binary_name'] or not binary_path.is_file() or
                context.get('binary_sha256') != hashlib.sha256(binary_path.read_bytes()).hexdigest() or
                context.get('approved_binary_sha256') != context.get('binary_sha256') or
                not dictionary_path.is_dir() or
                context.get('dictionary_tree_sha256') != context.get('approved_dictionary_tree_sha256') or
                context.get('dictionary_tree_sha256') != directory_sha256(dictionary_path) or
                context.get('io_pressure_mode') != contract['io_pressure_mode'] or
                any(context.get(key) != value for key, value in contract['resource_controls'].items())):
            raise ValueError('lock-contention microbenchmark context differs from its explicit operation contract')
        quota = contract.get('workspace_quota', {})
        proof = context.get('workspace_quota_proof')
        if (not isinstance(proof, dict) or proof.get('status') != 'VERIFIED' or
                proof.get('attestation_path') != quota.get('attestation_path') or
                proof.get('workspace_root') != quota.get('workspace_subvolume') or
                proof.get('filesystem_uuid') != quota.get('filesystem_uuid') or
                proof.get('subvolume_id') != quota.get('subvolume_id') or
                proof.get('qgroup_id') != quota.get('qgroup_id') or
                proof.get('max_referenced_bytes') != quota.get('max_referenced_bytes') or
                proof.get('accounting_mode') != quota.get('accounting_mode') or
                not isinstance(proof.get('live_headroom'), dict) or
                proof['live_headroom'].get('required_free_bytes') != quota.get('minimum_free_bytes') or
                not re.fullmatch(r'[0-9a-f]{64}', str(proof.get('attestation_sha256', '')))):
            raise ValueError('lock-contention microbenchmark lacks its verified workspace quota proof')
    elif context.get('purpose') == M4_WRITE_AMPLIFICATION_PURPOSE:
        contract = BENCHMARK_POLICY['immutable_write_amplification_pair']
        binary_path = pathlib.Path(context.get('binary_path', '')).resolve()
        dictionary_path = pathlib.Path(context.get('dictionary_path', '')).resolve()
        execution_order = context.get('execution_order')
        arm_role = context.get('arm_role')
        pair_label = ('pair-first' if execution_order and arm_role == execution_order[0]
                      else 'pair-second')
        profile_name = context.get('workload_profile', 'standard')
        profile_overrides = contract.get('workload_profiles', {}).get(profile_name)
        if profile_name == 'standard':
            expected_workload = contract['workload']
        elif isinstance(profile_overrides, dict):
            expected_workload = {**contract['workload'], **profile_overrides}
        else:
            raise ValueError('unknown write-amplification workload profile')
        expected_tmpdir = pathlib.Path(context.get('operation_root', '')) / pair_label / 'workspace' / 'tmp'
        if (not operation.startswith('m4-write-amplification-pair-') or
                context.get('approval_reference') != contract['approval_reference'] or
                context.get('execution_authorization_reference') != contract['execution_authorization_reference'] or
                context.get('risk_acceptance_reference') != contract['risk_acceptance_reference'] or
                context.get('workload_profile', 'standard') != profile_name or
                context.get('workload') != expected_workload or
                context.get('arm_role') not in contract['arm_roles'] or
                context.get('resource_root') != BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota']['workspace_subvolume'] or
                context.get('execution_order') not in contract['arm_orders'] or
                pathlib.Path(context.get('temporary_directory', '')).resolve() != expected_tmpdir.resolve() or
                binary_path.name != contract['binary_name'] or not binary_path.is_file() or
                context.get('binary_sha256') != hashlib.sha256(binary_path.read_bytes()).hexdigest() or
                context.get('approved_binary_sha256') != context.get('binary_sha256') or
                not dictionary_path.is_dir() or
                context.get('dictionary_tree_sha256') != context.get('approved_dictionary_tree_sha256') or
                context.get('dictionary_tree_sha256') != directory_sha256(dictionary_path) or
                context.get('io_pressure_mode') != contract['io_pressure_mode'] or
                any(context.get(key) != value for key, value in contract['resource_controls'].items())):
            raise ValueError('write-amplification context differs from its explicit bounded exploratory contract')
        verified_quota = verify_workspace_quota(
            pathlib.Path(context['resource_root']),
            BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota'])
        proof = context.get('workspace_quota_proof')
        identity_fields = ('status', 'attestation_path', 'workspace_root', 'filesystem_uuid',
                           'subvolume_id', 'qgroup_id', 'max_referenced_bytes',
                           'accounting_mode', 'attestation_sha256')
        if (not isinstance(proof, dict) or
                any(verified_quota.get(key) != proof.get(key) for key in identity_fields) or
                not isinstance(proof.get('live_headroom'), dict) or
                proof['live_headroom'].get('required_free_bytes') !=
                BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota'].get('minimum_free_bytes')):
            raise ValueError('write-amplification context lacks the current root-attested workspace quota proof')
    else:
        if context.get('purpose') is not None:
            raise ValueError('unknown contention purpose')
        if context.get('workload') != {'docs': 500, 'rounds': 20, 'tracing': False}:
            raise ValueError('contention context is only for tracing-off 500x20 same-binary A/A')
        if context.get('binary_sha256') != BENCHMARK_POLICY['contention_aware']['full_sync_binary_sha256']:
            raise ValueError('contention context requires the verified full-sync binary')
    for key in ('memory_max_bytes', 'pids_max', 'workspace_max_allocated_bytes', 'workspace_max_apparent_bytes',
                'host_memory_reserve_bytes', 'disk_reserve_bytes', 'runtime_limit_seconds',
                'max_monitor_gap_seconds'):
        v = context.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f'contention plan requires positive integer {key}')
    swap = context.get('memory_swap_max_bytes')
    if isinstance(swap, bool) or not isinstance(swap, int) or swap < 0:
        raise ValueError('contention plan requires nonnegative memory_swap_max_bytes')
    cpu = context.get('cpu_max', '')
    if not isinstance(cpu, str) or not re.fullmatch(r'[1-9]\d* [1-9]\d*', cpu):
        raise ValueError('contention plan requires a finite cgroup CPU quota/period')
    if context['runtime_limit_seconds'] > BENCHMARK_POLICY['contention_aware']['max_arm_runtime_seconds']:
        raise ValueError('contention runtime exceeds bounded existing workload limit')
    if context['max_monitor_gap_seconds'] >= min(10, context['runtime_limit_seconds']):
        raise ValueError('monitor gap must be smaller than the 10s observation window and arm runtime')
    if context.get('io_max') is not None:
        raise ValueError('no delegated I/O controller; cannot enforce io.max')
    return context


def validate_workspace_quota_attestation(attestation: dict[str, Any], expected: dict[str, Any],
                                         owner_uid: int, mode: int) -> dict[str, Any]:
    """Validate root-owned evidence for the lock-microbench's hard Btrfs cap."""
    fields = ('filesystem_uuid', 'subvolume_id', 'qgroup_id', 'max_referenced_bytes', 'accounting_mode')
    if owner_uid != expected.get('attestation_owner_uid') or mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError('workspace quota attestation is not protected by root ownership/mode')
    if type(attestation.get('schema_version')) is not int or attestation['schema_version'] != 1 or attestation.get('mountpoint') != '/':
        raise ValueError('invalid workspace quota attestation schema/mountpoint')
    for key in ('subvolume_id', 'max_referenced_bytes'):
        if type(attestation.get(key)) is not int or attestation[key] <= 0:
            raise ValueError(f'invalid workspace quota attestation integer: {key}')
    for key in fields:
        if attestation.get(key) != expected.get(key):
            raise ValueError(f'workspace quota attestation mismatch: {key}')
    if attestation.get('workspace_subvolume') != expected.get('workspace_subvolume'):
        raise ValueError('workspace quota attestation path mismatch')
    timestamp = attestation.get('verified_at_utc')
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError('workspace quota attestation lacks verification time')
    try:
        verified_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('workspace quota attestation timestamp is invalid') from exc
    if verified_at.tzinfo is None:
        raise ValueError('workspace quota attestation timestamp must include a timezone')
    age_seconds = (datetime.now(UTC) - verified_at.astimezone(UTC)).total_seconds()
    if (age_seconds < -300 or
            age_seconds > expected.get('max_attestation_age_seconds', 0)):
        raise ValueError('workspace quota attestation is stale or from the future')
    return {
        'status': 'VERIFIED', 'filesystem_uuid': attestation['filesystem_uuid'],
        'subvolume_id': attestation['subvolume_id'], 'qgroup_id': attestation['qgroup_id'],
        'max_referenced_bytes': attestation['max_referenced_bytes'],
        'accounting_mode': attestation['accounting_mode'], 'verified_at_utc': timestamp,
    }


def verify_workspace_quota(resource_root: pathlib.Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Verify the exact root-attested simple-quota contract before an operation."""
    root = pathlib.Path(resource_root).resolve(strict=True)
    expected_root = pathlib.Path(expected['workspace_subvolume']).resolve(strict=True)
    if root != expected_root:
        raise ValueError('quota workspace root differs from the registered subvolume')
    attestation_path = pathlib.Path(expected['attestation_path'])
    info = attestation_path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('workspace quota attestation is not a regular file')
    data = attestation_path.read_bytes()
    attestation = strict_json_text(data)
    proof = validate_workspace_quota_attestation(attestation, expected, info.st_uid, stat.S_IMODE(info.st_mode))
    proof['attestation_path'] = str(attestation_path)
    proof['attestation_sha256'] = hashlib.sha256(data).hexdigest()
    proof['workspace_root'] = str(root)

    fs = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE,UUID', '-T', str(root)],
                        capture_output=True, text=True, timeout=5)
    if fs.returncode != 0 or fs.stdout.split() != ['btrfs', expected['filesystem_uuid']]:
        raise ValueError('quota workspace is not on the attested Btrfs filesystem')
    rootid = subprocess.run(['btrfs', 'inspect-internal', 'rootid', str(root)],
                            capture_output=True, text=True, timeout=5)
    if rootid.returncode != 0 or rootid.stdout.strip() != str(expected['subvolume_id']):
        raise ValueError('quota workspace subvolume ID differs from the root attestation')
    status = subprocess.run(['btrfs', 'quota', 'status', '/'], capture_output=True, text=True, timeout=5)
    if status.returncode != 0:
        raise ValueError('cannot verify Btrfs quota status: ' + status.stderr.strip())
    if (not re.search(r'^\s*Enabled:\s+yes\s*$', status.stdout, re.MULTILINE | re.IGNORECASE) or
            not re.search(r'^\s*Mode:\s+squota(?:\s|$)', status.stdout, re.MULTILINE | re.IGNORECASE) or
            not re.search(r'^\s*Inconsistent:\s+no\s*$', status.stdout, re.MULTILINE | re.IGNORECASE)):
        raise ValueError('Btrfs simple-quota status is disabled, inconsistent, or wrong mode')
    # A provisioning attestation proves the cap, not current headroom. Read the
    # kernel's live qgroup counters so validation cannot pass on a nearly full
    # workspace just because the original limit remains configured.
    proof['live_headroom'] = verify_workspace_quota_headroom(expected)
    return proof


def verify_workspace_quota_headroom(expected: dict[str, Any]) -> dict[str, int]:
    """Read kernel-maintained squota usage and enforce the operation reserve."""
    qgroup_name = str(expected.get('qgroup_id', '')).replace('/', '_')
    if not re.fullmatch(r'0_\d+', qgroup_name):
        raise ValueError('invalid simple-quota qgroup ID')
    qgroup = BTRFS_SYSFS_ROOT / str(expected['filesystem_uuid']) / 'qgroups' / qgroup_name
    try:
        enabled = (qgroup.parent / 'enabled').read_text().strip()
        mode = (qgroup.parent / 'mode').read_text().strip()
        inconsistent = (qgroup.parent / 'inconsistent').read_text().strip()
        referenced = int((qgroup / 'referenced').read_text().strip())
        max_referenced = int((qgroup / 'max_referenced').read_text().strip())
    except (OSError, ValueError) as exc:
        raise ValueError(f'cannot read live Btrfs qgroup headroom from {qgroup}: {exc}') from exc
    if enabled != '1' or mode != 'squota' or inconsistent != '0':
        raise ValueError('live Btrfs quota state is disabled, inconsistent, or not simple-quota mode')
    if (max_referenced != expected.get('max_referenced_bytes') or
            referenced < 0 or referenced > max_referenced):
        raise ValueError('live Btrfs qgroup usage/limit differs from the attested quota contract')
    available = max_referenced - referenced
    required = expected.get('minimum_free_bytes', 0)
    if isinstance(required, bool) or not isinstance(required, int) or required < 0:
        raise ValueError('quota contract minimum_free_bytes must be a nonnegative integer')
    if available < required:
        raise ValueError(
            f'insufficient live Btrfs qgroup headroom: referenced={referenced}, '
            f'limit={max_referenced}, available={available}, required={required}'
        )
    return {'referenced_bytes': referenced, 'max_referenced_bytes': max_referenced,
            'available_bytes': available, 'required_free_bytes': required}


def contention_context_digest(context: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_amplification_phase_role(context: dict[str, Any], arm_name: str) -> str | None:
    phase_index = {'pair-first': 0, 'pair-second': 1}.get(arm_name)
    order = context.get('execution_order', [])
    return order[phase_index] if phase_index is not None and len(order) == 2 else None


def rust_lifecycle_expected_environment(context: dict[str, Any]) -> dict[str, str]:
    return {
        'M4_GATE_B_ARM': context['arm_role'],
        'M4_GATE_B_WORKSPACE': str(pathlib.Path(context['source_root']).resolve()),
        'M4_GATE_B_INDEX_ROOT': str(pathlib.Path(context['index_root']).resolve()),
        'M4_GATE_B_REPORT': str(pathlib.Path(context['report_path']).resolve()),
        'TMPDIR': str(pathlib.Path(context['temporary_directory']).resolve()),
        'LD_LIBRARY_PATH': context['library_path'],
        'M4_GATE_B_READER_QUERY_INTERVAL_MS': str(context['reader_query_interval_ms']),
        'M4_GATE_B_READER_QUERY_MAX_SAMPLES': str(context['reader_query_max_samples_per_pass']),
        'M4_GATE_B_READER_QUERY_QUEUE_CAPACITY': str(context['reader_query_queue_capacity']),
        'ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES': str(context['zvec_native_memory_limit_bytes']),
    }


def contention_budget(context: dict[str, Any]) -> dict[str, Any]:
    validate_contention_context(context)
    budget = {key: context[key] for key in ('approval_reference', 'memory_max_bytes',
              'memory_swap_max_bytes', 'cpu_max', 'pids_max', 'workspace_max_allocated_bytes',
              'workspace_max_apparent_bytes')}
    if 'purpose' in context:
        budget['purpose'] = context['purpose']
    if context.get('purpose') in (M4_LOCK_CONTENTION_MICROBENCH_PURPOSE,
                                   M4_WRITE_AMPLIFICATION_PURPOSE,
                                   M4_RUST_LIFECYCLE_PURPOSE):
        budget['workspace_quota_proof'] = context['workspace_quota_proof']
    return budget


def contention_decision(state: dict[str, Any], context: dict[str, Any], phase: str,
                        workspace_bytes: int = 0, owned_swap_bytes: int = 0) -> dict[str, Any]:
    """Retain raw envelope FAILs; enforce capacity based on the approved workload plan."""
    validate_contention_context(context)
    if phase not in ('baseline', 'recovery', 'aa-first', 'aa-second', 'pair-first', 'pair-second', 'microbench', 'runtime-first', 'runtime-second'):
        raise ValueError('invalid contention phase')
    def number(value, name, maximum=None):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum)):
            raise ValueError(f'invalid required contention telemetry: {name}')
        return value
    number(state.get('monotonic_ns'), 'monotonic_ns')
    if not isinstance(state.get('timestamp_utc'), str) or not state['timestamp_utc']:
        raise ValueError('missing contention sample timestamp')
    mem = state.get('meminfo_kib', {})
    # SwapFree/SwapTotal support the remaining approved swap-capacity check;
    # MemAvailable/total are observational context and may be absent.
    for name in ('SwapFree_kib', 'SwapTotal_kib'):
        number(mem.get(name), name)
    if mem['SwapFree_kib'] > mem['SwapTotal_kib']:
        raise ValueError('inconsistent host swap telemetry')
    number(state.get('resource_root_free_bytes'), 'resource_root_free_bytes')
    host_context_errors = []
    available = mem.get('MemAvailable_kib')
    total = mem.get('MemTotal_kib')
    if (isinstance(available, bool) or not isinstance(available, (int, float)) or
            not math.isfinite(available) or available < 0):
        host_context_errors.append('MemAvailable_kib')
        available = None
    if (isinstance(total, bool) or not isinstance(total, (int, float)) or
            not math.isfinite(total) or total <= 0):
        host_context_errors.append('MemTotal_kib')
        total = None
    if available is not None and total is not None and available > total:
        host_context_errors.append('MemAvailable_kib>MemTotal_kib')
    load = state.get('loadavg') or []
    load1 = load[0] if load else None
    if (isinstance(load1, bool) or not isinstance(load1, (int, float)) or
            not math.isfinite(load1) or load1 < 0):
        host_context_errors.append('load1')
        load1 = None
    host_context = {'MemAvailable_kib': available, 'MemTotal_kib': total,
                    'load1': load1, 'pressure': {}, 'invalid_or_missing': host_context_errors,
                    'disposition': 'observational-only; never blocks launch, aborts, or delays recovery'}
    for family in ('io_pressure', 'memory_pressure', 'cpu_pressure'):
        pressure = state.get(family) or {}
        host_context['pressure'][family] = {}
        for kind in ('some', 'full'):
            item = pressure.get(kind) or {}
            avg10, total_us = item.get('avg10'), item.get('total')
            valid = (not isinstance(avg10, bool) and isinstance(avg10, (int, float)) and
                     math.isfinite(avg10) and 0 <= avg10 <= 100 and
                     not isinstance(total_us, bool) and isinstance(total_us, (int, float)) and
                     math.isfinite(total_us) and total_us >= 0)
            if valid:
                host_context['pressure'][family][kind] = {'avg10': avg10, 'total': total_us}
            else:
                host_context['pressure'][family][kind] = None
                host_context_errors.append(f'{family}.{kind}')
    for name in ('pswpin_pages', 'pswpout_pages', 'oom_kill', 'pgscan_direct'):
        number(state.get('vmstat', {}).get(name), name)
    disk = state.get('diskstats')
    if not isinstance(disk, dict) or not disk:
        raise ValueError('missing host diskstats')
    for device in disk.values():
        for name in ('sectors_read', 'sectors_written', 'io_ms'):
            number(device.get(name), f'diskstats.{name}')
    number(workspace_bytes, 'workspace_bytes')
    number(owned_swap_bytes, 'owned_swap_bytes')
    single_arm_capacity = is_m4_single_arm_capacity_plan(context)
    single_rehearsal = is_m4_scalar_open_exploratory(context)
    # Host MemAvailable/load/PSI values are telemetry, not preflight gates.
    # Per-operation cgroup memory limits and runtime OOM monitoring remain hard.
    # Paired studies reserve retained roots; one-arm scalar-open exploration
    # applies its explicitly approved free-space floor directly. Workspace
    # soft budgets never substitute for the disk-capacity proof.
    arms = 1 if single_arm_capacity else (2 if phase in ('baseline', 'aa-first', 'pair-first', 'runtime-first') else 1)
    disk_required = (context['disk_reserve_bytes'] if single_rehearsal else
                     context['disk_reserve_bytes'] + arms * context['workspace_max_allocated_bytes'])
    failures = []
    swap_required = max(0, context['memory_swap_max_bytes'] - owned_swap_bytes)
    if mem['SwapFree_kib'] * 1024 < swap_required:
        failures.append({'gate_id': 'M4-CA-SWAP-CAPACITY', 'observed_bytes': mem['SwapFree_kib'] * 1024,
                         'required_bytes': swap_required, 'attribution': 'remaining approved swap capacity; cause not established'})
    if state['resource_root_free_bytes'] < disk_required:
        failures.append({'gate_id': 'M4-CA-DISK-CAPACITY', 'observed_bytes': state['resource_root_free_bytes'],
                         'required_bytes': disk_required, 'attribution': 'shared filesystem capacity; cause not established'})
    raw = resource_guard_decisions(state)
    for gate in raw:
        gate['enforced_in_selected_mode'] = gate['gate_id'] == 'M4-RG-DISK'
        gate['selected_mode_disposition'] = (
            'hard retained-filesystem capacity reserve' if gate['gate_id'] == 'M4-RG-DISK'
            else 'host context telemetry only; never blocks launch or aborts an arm')
    return {'mode': CONTENTION_MODE, 'operation_id': context['operation_id'], 'phase': phase,
            'timestamp_utc': state.get('timestamp_utc'), 'monotonic_ns': state.get('monotonic_ns'),
            'host_context_telemetry': host_context,
            'historical_gate_decisions': raw, 'historical_guard_failures': [g for g in raw if g['status'] == 'FAIL'],
            'blocking_failures': failures, 'continuation_allowed': not failures,
            'continuation_reason': 'approved workload capacity and containment plan, not an idle-envelope waiver or safety certification',
            'evidence_class': contention_evidence_class(context), 'approval_reference': context['approval_reference'],
            'host_io_pressure_observation': ({
                'full_avg10_percent': ((state.get('io_pressure') or {}).get('full') or {}).get('avg10'),
                'full_avg60_percent': ((state.get('io_pressure') or {}).get('full') or {}).get('avg60'),
                'full_avg300_percent': ((state.get('io_pressure') or {}).get('full') or {}).get('avg300'),
                'some_avg10_percent': ((state.get('io_pressure') or {}).get('some') or {}).get('avg10'),
                'disposition': 'observational-only; elevated, missing or arm-mismatched host pressure is not an admission/abort/recovery gate',
            } if context.get('purpose') in (M4_SHARED_COLLECTION_PAIR_PURPOSE,
                                                M4_LOCK_CONTENTION_MICROBENCH_PURPOSE,
                                                M4_RUST_LIFECYCLE_PURPOSE) else None)}


def m4_scalar_rehearsal_host_observation(state: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Record host swap/reclaim deltas; only OOM or telemetry-integrity loss blocks."""
    current = state.get('vmstat', {})
    required = ('pswpin_pages', 'pswpout_pages', 'pgscan_direct', 'oom_kill')
    errors = []
    deltas = {}
    for key in required:
        if key not in baseline or key not in current:
            errors.append(f'host vmstat telemetry missing/invalid for {key}')
    for key in sorted(set(baseline) | set(current)):
        before, after = baseline.get(key), current.get(key)
        if isinstance(before, bool) or not isinstance(before, int) or isinstance(after, bool) or not isinstance(after, int):
            if key in required:
                errors.append(f'host vmstat telemetry missing/invalid for {key}')
            continue
        if after < before:
            errors.append(f'host vmstat counter regressed: {key}')
            continue
        deltas[key] = after - before
    blocking = list(errors)
    if deltas.get('oom_kill', 0) > 0:
        blocking.append('host OOM kill observed; attribution to M4 not established')
    return {
        'baseline_to_sample_deltas': deltas,
        'positive_swap_or_reclaim_deltas_are_observational': True,
        'blocking_failures': blocking,
        'telemetry_errors': errors,
        'oom_kill_delta': deltas.get('oom_kill'),
    }


def verify_contention_caps(cg: pathlib.Path, context: dict[str, Any]) -> None:
    for key, name in (('memory_max_bytes', 'memory.max'), ('memory_swap_max_bytes', 'memory.swap.max'),
                      ('cpu_max', 'cpu.max'), ('pids_max', 'pids.max')):
        if read_text(cg / name).split() != str(context[key]).split():
            raise ValueError(f'contention cgroup cap not applied or changed: {name}')


def contention_runtime_failures(cgroup: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """Fail closed on unavailable attribution telemetry or observed M4 cap/reclaim distress."""
    if not cgroup.get('process_io_sample_complete') or not cgroup.get('process_memory_sample_complete'):
        raise ValueError('incomplete process-tree telemetry')
    for key in ('memory_current_bytes', 'memory_swap_current_bytes', 'pids_current'):
        if isinstance(cgroup.get(key), bool) or not isinstance(cgroup.get(key), int) or cgroup[key] < 0:
            raise ValueError(f'invalid cgroup {key}')
    for family in ('memory_pressure', 'io_pressure', 'cpu_pressure'):
        for kind in (('some',) if family == 'cpu_pressure' else ('some','full')):
            item = (cgroup.get(family) or {}).get(kind, {})
            for key in ('avg10','total'):
                val = item.get(key)
                if (isinstance(val, bool) or not isinstance(val, (float, int)) or not math.isfinite(val)
                        or val < 0 or (key == 'avg10' and val > 100)):
                    raise ValueError(f'invalid cgroup {family}.{kind}.{key}')
    for family, keys in (('memory_events', ('max', 'oom', 'oom_kill', 'oom_group_kill')),
                         ('memory_swap_events', ('max', 'fail')), ('pids_events', ('max',)),
                         ('cpu_stat', ('usage_usec',))):
        for key in keys:
            metrics = cgroup.get(family)
            if not isinstance(metrics, dict):
                raise ValueError(f'missing cgroup {family}')
            val = metrics.get(key)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ValueError(f'missing/invalid cgroup {family}.{key}')
            old = (previous or {}).get(family, {}).get(key, 0)
            if val < old:
                raise ValueError(f'nonmonotonic cgroup {family}.{key}')
    return [f'{family}.{key} increased; M4 allocation/swap/task limit distress'
            for family, keys in (('memory_events', ('max', 'oom', 'oom_kill', 'oom_group_kill')),
                                 ('memory_swap_events', ('max', 'fail')), ('pids_events', ('max',)))
            for key in keys if cgroup[family][key] > (previous or {}).get(family, {}).get(key, 0)]


def legacy_hard_gate_failures(state: dict[str, Any]) -> list[str]:
    # Compatibility name for legacy callers; only the retained-disk reserve
    # remains an operational host-side guard. Pressure/load/RAM are telemetry.
    return hard_gate_failures(state)


def hard_gate_failures(state: dict[str, Any]) -> list[str]:
    """Return actual retained-disk reserve violations, not host-pressure signals."""
    return [f"{gate['metric']}={gate['observed']!r} {gate['operator']} {gate['threshold']} {gate['unit']} ({gate['gate_id']})"
            for gate in resource_guard_decisions(state) if gate['gate_id'] == 'M4-RG-DISK' and gate['status'] == 'FAIL']


def cgroup_relative(pid: int | None = None) -> str:
    path = pathlib.Path(f'/proc/{pid}/cgroup' if pid else '/proc/self/cgroup')
    for line in path.read_text().splitlines():
        if line.startswith('0::'):
            return line[3:].lstrip('/')
    raise RuntimeError(f'no unified cgroup membership in {path}')


def cgroup_path_from_unit(unit: str) -> pathlib.Path:
    result = subprocess.run(
        ['systemctl', '--user', 'show', unit, '-p', 'ControlGroup', '--value'],
        check=True, capture_output=True, text=True, timeout=5,
    )
    rel = result.stdout.strip().lstrip('/')
    if not rel or '..' in pathlib.PurePosixPath(rel).parts:
        raise RuntimeError(f'systemd returned unsafe/empty ControlGroup for {unit}: {rel!r}')
    path = (CGROOT / rel).resolve(strict=True)
    if path != CGROOT and CGROOT not in path.parents:
        raise RuntimeError(f'ControlGroup escaped cgroup2 mount: {path}')
    return path


def validate_required_cgroup_metrics(cg: pathlib.Path) -> None:
    required = ('memory.current', 'memory.peak', 'memory.events', 'memory.swap.current',
                'memory.swap.peak', 'memory.swap.events', 'memory.pressure', 'cpu.stat',
                'cpu.pressure', 'io.pressure', 'cgroup.procs', 'cgroup.events', 'cgroup.kill')
    missing = [name for name in required if not (cg / name).exists()]
    if missing:
        raise RuntimeError('required delegated telemetry/control missing: ' + ', '.join(missing))


def read_cgroup_metric(cg: pathlib.Path) -> dict[str, Any]:
    required = ['cgroup.procs', 'cgroup.events', 'memory.current', 'memory.peak',
                'memory.events', 'memory.swap.current', 'memory.swap.peak',
                'memory.swap.events', 'cpu.stat', 'memory.pressure', 'io.pressure']
    missing = [name for name in required if not (cg / name).is_file()]
    if missing:
        raise RuntimeError('missing required cgroup telemetry: ' + ', '.join(missing))
    expected_rel = str(cg.relative_to(CGROOT))
    # cgroup.procs and /proc are separate snapshots. Retry a bounded number of
    # times if membership changes while reading process counters; persistent
    # churn or unreadable live members remains incomplete and fail-closed.
    proc_io_read = proc_io_write = 0
    proc_io_complete = proc_memory_complete = False
    proc_pss = proc_rss = proc_swap = 0
    pids: list[int] = []
    for _attempt in range(3):
        pids = [int(x) for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
        attempt_io_read = attempt_io_write = 0
        attempt_pss = attempt_rss = attempt_swap = 0
        attempt_io_complete = attempt_memory_complete = True
        for pid in pids:
            try:
                if not proc_cgroup_contains(pid, expected_rel):
                    attempt_io_complete = attempt_memory_complete = False
                    continue
                values = parse_kv(pathlib.Path(f'/proc/{pid}/io').read_text())
                if 'read_bytes' not in values or 'write_bytes' not in values:
                    attempt_io_complete = False
                attempt_io_read += values.get('read_bytes', 0)
                attempt_io_write += values.get('write_bytes', 0)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                attempt_io_complete = False
            try:
                smaps = pathlib.Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines()
                memory = {line.split(':', 1)[0]: int(line.split()[1])
                          for line in smaps if ':' in line and len(line.split()) >= 2
                          and line.split()[1].isdigit()}
                attempt_pss += memory['Pss']
                attempt_rss += memory['Rss']
                attempt_swap += memory.get('Swap', 0)
            except (FileNotFoundError, ProcessLookupError, PermissionError, KeyError, ValueError):
                attempt_memory_complete = False
        after_pids = [int(x) for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
        if after_pids == pids:
            proc_io_read, proc_io_write = attempt_io_read, attempt_io_write
            proc_pss, proc_rss, proc_swap = attempt_pss, attempt_rss, attempt_swap
            proc_io_complete, proc_memory_complete = attempt_io_complete, attempt_memory_complete
            break
        pids = after_pids
    events = parse_kv(read_text(cg / 'memory.events'))
    memory_stat_path = cg / 'memory.stat'
    memory_stat = parse_kv(read_text(memory_stat_path)) if memory_stat_path.is_file() else None
    swap_events = parse_kv(read_text(cg / 'memory.swap.events'))
    cpu = parse_kv(read_text(cg / 'cpu.stat'))
    ev = parse_kv(read_text(cg / 'cgroup.events'))
    controllers = read_text(cg / 'cgroup.controllers').split() if (cg / 'cgroup.controllers').exists() else []
    return {
        'controllers': controllers, 'pids': pids, 'populated': ev.get('populated'),
        'pids_current': int(read_text(cg / 'pids.current')) if (cg / 'pids.current').exists() else None,
        'pids_events': parse_kv(read_text(cg / 'pids.events')) if (cg / 'pids.events').exists() else None,
        'memory_current_bytes': int(read_text(cg / 'memory.current')),
        'memory_peak_bytes': int(read_text(cg / 'memory.peak')),
        'memory_events': events,
        'memory_stat_bytes': memory_stat,
        'memory_swap_current_bytes': int(read_text(cg / 'memory.swap.current')),
        'memory_swap_peak_bytes': int(read_text(cg / 'memory.swap.peak')),
        'memory_swap_events': swap_events,
        'memory_pressure': parse_pressure(cg / 'memory.pressure'),
        'io_pressure': parse_pressure(cg / 'io.pressure'),
        'cpu_stat': cpu, 'cpu_pressure': parse_pressure(cg / 'cpu.pressure'),
        'io_stat': read_text(cg / 'io.stat') if (cg / 'io.stat').exists() else None,
        'io_max': read_text(cg / 'io.max') if (cg / 'io.max').exists() else None,
        'cgroup_io_controller': 'io' in controllers,
        'process_tree_read_bytes': proc_io_read,
        'process_tree_write_bytes': proc_io_write,
        'process_io_sample_complete': proc_io_complete,
        'process_tree_pss_kib': proc_pss,
        'process_tree_rss_kib': proc_rss,
        'process_tree_swap_kib': proc_swap,
        'process_memory_sample_complete': proc_memory_complete,
    }


def workspace_usage(root: pathlib.Path) -> dict[str, Any]:
    """One bounded, no-follow descriptor walk; never an atomic/capacity snapshot."""
    limits = BENCHMARK_POLICY['workspace_observer']
    start = time.monotonic_ns()
    deadline = start + int(limits['scan_time_budget_seconds'] * 1e9)
    apparent = allocated = generation_apparent = staging_apparent = 0
    files = directories = symlinks = entries = issue_count = 0
    seen: set[tuple[int, int]] = set()
    generations: set[str] = set()
    issues: list[dict[str, Any]] = []
    stopped = False
    root_stat = None
    notify_fd = -1
    libc = ctypes.CDLL(None, use_errno=True)
    # Names/content events detect known mutations; mmap/writeback are NOT covered.
    watch_mask = 0x00000002 | 0x00000004 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800 | 0x00002000

    def issue(path, kind, detail, race=False):
        nonlocal issue_count, stopped
        issue_count += 1
        if len(issues) < limits['max_issue_examples']:
            issues.append({'path': str(path), 'kind': kind, 'detail': str(detail), 'known_race': race})
        if not race:
            stopped = True

    def version(s):
        return (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink, s.st_size)

    def visit(fd, relative, depth):
        nonlocal apparent, allocated, generation_apparent, staging_apparent
        nonlocal files, directories, symlinks, entries
        if depth > limits['max_depth']:
            issue(root / relative, 'WORK_BUDGET_EXHAUSTED', 'directory depth')
            return
        before = os.fstat(fd)
        if libc.inotify_add_watch(notify_fd, os.fsencode(f'/proc/self/fd/{fd}'), watch_mask | 0x01000000) < 0:
            raise OSError(ctypes.get_errno(), 'cannot establish directory mutation observation')
        with os.scandir(fd) as iterator:
            for entry in iterator:
                if stopped:
                    break
                if entries >= limits['max_entries_per_scan'] or time.monotonic_ns() > deadline:
                    issue(root / relative, 'WORK_BUDGET_EXHAUSTED', 'entry/time budget')
                    break
                entries += 1
                rel = relative / entry.name
                path = root / rel
                try:
                    s = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(s.st_mode):
                        symlinks += 1
                        continue
                    if s.st_dev != before.st_dev:
                        issue(path, 'SCOPE_UNVERIFIABLE', 'cross-device entry')
                        continue
                    key = (s.st_dev, s.st_ino)
                    if key not in seen:
                        seen.add(key)
                        allocated += s.st_blocks * 512
                    if stat.S_ISDIR(s.st_mode):
                        directories += 1
                        generations.update(part for part in rel.parts if part.startswith('generation-'))
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev, opened.st_ino) != key:
                                issue(path, 'ENTRY_REPLACED', 'directory identity changed', race=True)
                            else:
                                visit(child, rel, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(s.st_mode):
                        files += 1
                        apparent += s.st_size
                        if any(part.startswith('generation-') for part in rel.parts):
                            generation_apparent += s.st_size
                        low = str(rel).lower()
                        if any(token in low for token in ('.tmp.', '.tmp', 'staging', '/stage', 'compact', 'temporary')):
                            staging_apparent += s.st_size
                    else:
                        issue(path, 'SCOPE_UNVERIFIABLE', 'unsupported special file')
                except FileNotFoundError as exc:
                    issue(path, 'ENTRY_DISAPPEARED', exc, race=True)
                except OSError as exc:
                    # Permission/EIO/ENOTDIR/ELOOP are not silently reclassified as ENOENT.
                    issue(path, 'FILESYSTEM_ERROR', exc)
        if version(os.fstat(fd)) != version(before):
            issue(root / relative, 'DIRECTORY_CHANGED', 'metadata changed during traversal', race=True)

    try:
        notify_fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if notify_fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify unavailable; no mutation coverage')
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            root_stat = os.fstat(fd)
            visit(fd, pathlib.Path(), 0)
            current = root.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                issue(root, 'SCOPE_UNVERIFIABLE', 'workspace root replaced')
        finally:
            os.close(fd)
    except OSError as exc:
        issue(root, 'ROOT_OR_TRAVERSAL_ERROR', exc)
    finally:
        if notify_fd >= 0:
            try:
                try:
                    events = os.read(notify_fd, limits['max_event_bytes_per_scan'])  # one bounded drain
                except BlockingIOError:
                    events = b''
                offset = 0
                while offset < len(events):
                    _, mask, _, length = struct.unpack_from('iIII', events, offset)
                    if offset + 16 + length > len(events):
                        issue(root, 'MUTATION_COVERAGE_LOST', 'truncated event record')
                        break
                    offset += 16 + length
                    if mask & (0x00004000 | 0x00002000):
                        issue(root, 'MUTATION_COVERAGE_LOST', 'event overflow or unmount')
                    else:
                        issue(root, 'DIRECTORY_ACTIVITY', f'inotify mask={mask}', race=True)
                if select.select([notify_fd], [], [], 0)[0]:
                    issue(root, 'MUTATION_COVERAGE_LOST', 'events exceed one bounded drain')
            except (OSError, struct.error) as exc:
                issue(root, 'MUTATION_COVERAGE_LOST', exc)
            finally:
                os.close(notify_fd)
    if time.monotonic_ns() > deadline:
        issue(root, 'WORK_BUDGET_EXHAUSTED', 'cooperative time deadline exceeded')
    complete = issue_count == 0
    known_race_only = bool(issues) and all(item['known_race'] for item in issues) and not stopped
    return {
        'schema_version': 2, 'root': str(root),
        'root_identity': None if root_stat is None else [root_stat.st_dev, root_stat.st_ino],
        'apparent_bytes': apparent, 'allocated_st_blocks_bytes_gross': allocated,
        'allocated_semantics': 'observed sum st_blocks*512, deduplicated hardlinks; Btrfs shared extents may be counted more than once',
        'file_count': files, 'directory_count': directories, 'symlink_count': symlinks,
        'generation_directory_names': sorted(generations), 'generation_apparent_bytes': generation_apparent,
        'staging_named_apparent_bytes': staging_apparent,
        'scan_duration_ns': time.monotonic_ns() - start, 'entries_observed': entries,
        'scan_complete': complete, 'scan_errors': [f"{i['path']}: {i['detail']}" for i in issues],
        'inventory_status': 'COMPLETE_TRAVERSAL_NOT_ATOMIC' if complete else
                            'PARTIAL_KNOWN_RACE' if known_race_only else 'UNVERIFIABLE',
        'complete_snapshot': False, 'issues': issues, 'issue_count': issue_count,
        'mutation_observer': 'bounded per-pass inotify; not independent capacity proof; mmap/writeback not covered',
        'independent_capacity_upper_bound': None, 'verified_peak': None,
        'reservation_credit_bytes': 0,
        'limitations': 'No atomic snapshot/quota backend. A traversal can miss creates/growth/mmap/replacements between observations; no upper bound or verified lifetime peak. One pass, no retry. Deadline is cooperative, not a bound on one blocked kernel syscall.',
    }


def registered_workspace_quota_contract(budget: dict[str, Any] | None) -> dict[str, Any] | None:
    purpose = (budget or {}).get('purpose')
    if purpose == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE:
        return BENCHMARK_POLICY['lock_contention_microbench'].get('workspace_quota')
    if purpose == M4_RUST_LIFECYCLE_PURPOSE:
        return BENCHMARK_POLICY['rust_pipeline_lifecycle_pair'].get('workspace_quota')
    return None


def workspace_quota_bound_bytes(budget: dict[str, Any] | None) -> int | None:
    proof = (budget or {}).get('workspace_quota_proof')
    contract = registered_workspace_quota_contract(budget)
    if (not isinstance(contract, dict) or not isinstance(proof, dict) or proof.get('status') != 'VERIFIED' or
            proof.get('attestation_path') != contract.get('attestation_path') or
            proof.get('workspace_root') != contract.get('workspace_subvolume') or
            proof.get('filesystem_uuid') != contract.get('filesystem_uuid') or
            proof.get('subvolume_id') != contract.get('subvolume_id') or
            proof.get('qgroup_id') != contract.get('qgroup_id') or
            proof.get('max_referenced_bytes') != contract.get('max_referenced_bytes') or
            proof.get('accounting_mode') != contract.get('accounting_mode') or
            not re.fullmatch(r'[0-9a-f]{64}', str(proof.get('attestation_sha256', '')))):
        return None
    return contract['max_referenced_bytes']


def workspace_quota_live_sample(budget: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read kernel-maintained qgroup headroom for a previously verified contract."""
    contract = registered_workspace_quota_contract(budget)
    if contract is None or workspace_quota_bound_bytes(budget) is None:
        return None
    try:
        headroom = verify_workspace_quota_headroom(contract)
        return {'status': 'PASS', **headroom}
    except (OSError, ValueError) as exc:
        return {'status': 'UNVERIFIABLE', 'error': str(exc)}


def workspace_quota_bounded_observation(root: pathlib.Path) -> dict[str, Any]:
    """Cheap scope check when an exact registered qgroup is the capacity bound.

    It intentionally does not claim a workspace inventory. The caller must retain
    the qgroup sample separately and classify soft workspace budgets UNKNOWN.
    """
    started = time.monotonic_ns()
    root_stat = None
    issues: list[dict[str, Any]] = []
    try:
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode):
            issues.append({'path': str(root), 'kind': 'SCOPE_UNVERIFIABLE',
                           'detail': 'workspace root is not a real directory', 'known_race': False})
    except OSError as exc:
        issues.append({'path': str(root), 'kind': 'ROOT_OR_TRAVERSAL_ERROR',
                       'detail': str(exc), 'known_race': False})
    complete_scope = not issues
    return {
        'schema_version': 3, 'root': str(root),
        'root_identity': None if root_stat is None else [root_stat.st_dev, root_stat.st_ino],
        'apparent_bytes': None, 'allocated_st_blocks_bytes_gross': None,
        'allocated_semantics': 'not sampled; kernel qgroup is the independent capacity bound',
        'file_count': None, 'directory_count': None, 'symlink_count': None,
        'generation_directory_names': None, 'generation_apparent_bytes': None,
        'staging_named_apparent_bytes': None, 'scan_duration_ns': time.monotonic_ns() - started,
        'entries_observed': 0, 'scan_complete': False,
        'scan_errors': [f"{item['path']}: {item['detail']}" for item in issues],
        'inventory_status': 'NOT_SAMPLED_KERNEL_QUOTA_BOUND' if complete_scope else 'UNVERIFIABLE',
        'complete_snapshot': False, 'issues': issues, 'issue_count': len(issues),
        'mutation_observer': 'not sampled; registered kernel qgroup is the capacity bound',
        'independent_capacity_upper_bound': None, 'verified_peak': None,
        'reservation_credit_bytes': 0,
        'limitations': 'Workspace inventory and soft-budget peaks are UNKNOWN; qgroup capacity proof covers only its registered Btrfs subvolume and is not physical-device usage.'}


def workspace_capacity_decision(observation: dict[str, Any], budget: dict[str, Any] | None,
                                policy_mode: str = 'legacy',
                                quota_sample: dict[str, Any] | None = None) -> dict[str, Any]:
    """Combine inventory observations with an exact registered kernel quota bound."""
    if not isinstance(observation.get('scan_complete'), bool):
        raise ValueError('missing/invalid workspace scan completeness')
    for key in ('allocated_st_blocks_bytes_gross', 'apparent_bytes'):
        value = observation.get(key)
        if value is None and not observation['scan_complete']:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('missing/invalid workspace counter: ' + key)
    if policy_mode == CONTENTION_MODE:
        observer_contract = BENCHMARK_POLICY['workspace_observer']['contention_aware']
        if observer_contract['enforcement'] != 'monitored-soft-budgets':
            raise ValueError('unknown exploratory workspace contract')
        warnings = []
        indeterminate_overages = []
        for key, limit_key in (('allocated_st_blocks_bytes_gross', 'workspace_max_allocated_bytes'),
                               ('apparent_bytes', 'workspace_max_apparent_bytes')):
            limit = (budget or {}).get(limit_key)
            value = observation.get(key)
            if limit is None:
                continue
            if isinstance(value, int) and not isinstance(value, bool) and value > limit:
                overage = {'metric': key, 'observed_bytes': value, 'budget_bytes': limit,
                           'excess_bytes': value - limit, 'scan_complete': observation['scan_complete']}
                (warnings if observation['scan_complete'] else indeterminate_overages).append(overage)
            elif value is None:
                indeterminate_overages.append({'metric': key, 'observed_bytes': None,
                                               'budget_bytes': limit, 'excess_bytes': None,
                                               'scan_complete': False, 'reason': 'inventory_not_sampled'})
        known_race = observation.get('inventory_status') == observer_contract['race_continuation_inventory_status']
        issues = observation.get('issues', [])
        pending = observer_contract['pending_event_drain_issue']
        pending_only = (observation.get('root_identity') is not None and bool(issues)
                        and observation.get('issue_count') == len(issues)
                        and all(item.get('known_race') is True or
                                (item.get('kind') == pending['kind'] and item.get('detail') == pending['detail'])
                                for item in issues))
        quota_bound = workspace_quota_bound_bytes(budget)
        purpose = (budget or {}).get('purpose')
        quota_contract = registered_workspace_quota_contract(budget)
        quota_valid = False
        if quota_bound is not None and quota_contract is not None and quota_sample is not None:
            referenced = quota_sample.get('referenced_bytes')
            maximum = quota_sample.get('max_referenced_bytes')
            available = quota_sample.get('available_bytes')
            required = quota_sample.get('required_free_bytes')
            if (quota_sample.get('status') == 'PASS'
                    and isinstance(referenced, int) and not isinstance(referenced, bool) and referenced >= 0
                    and isinstance(maximum, int) and not isinstance(maximum, bool)
                    and isinstance(available, int) and not isinstance(available, bool)
                    and isinstance(required, int) and not isinstance(required, bool)):
                quota_valid = (
                    maximum == quota_bound
                    and available == maximum - referenced
                    and required == quota_contract.get('minimum_free_bytes')
                    and available >= required
                )
        quota_required = quota_bound is not None
        rust_quota_fallback = purpose == M4_RUST_LIFECYCLE_PURPOSE and quota_required
        if quota_required and not quota_valid:
            return {'state': 'CAPACITY_UNVERIFIABLE', 'continuation_allowed': False,
                    'reason': 'workspace_quota_telemetry_unverified',
                    'inventory_status': observation.get('inventory_status'),
                    'errors': observation.get('scan_errors', []),
                    'independent_capacity_upper_bound': quota_bound}
        budget_only_failure = (
            observation.get('root_identity') is not None and bool(issues)
            and observation.get('issue_count') == len(issues)
            and all(item.get('kind') == 'WORK_BUDGET_EXHAUSTED' for item in issues)
        )
        quota_only_inventory = (
            rust_quota_fallback and quota_valid
            and observation.get('inventory_status') == 'NOT_SAMPLED_KERNEL_QUOTA_BOUND'
            and observation.get('root_identity') is not None and not issues
        )
        quota_bound_inventory_failure = rust_quota_fallback and quota_valid and budget_only_failure
        tolerated_uncertainty = known_race or pending_only or quota_only_inventory or quota_bound_inventory_failure
        continuation_allowed = observation['scan_complete'] or tolerated_uncertainty
        if quota_only_inventory:
            continuation_basis = 'VERIFIED_KERNEL_QGROUP; INVENTORY_AND_SOFT_BUDGETS_UNKNOWN'
        elif quota_bound_inventory_failure:
            continuation_basis = 'VERIFIED_KERNEL_QGROUP; WALK_BUDGET_EXHAUSTED; INVENTORY_UNKNOWN'
        elif known_race or pending_only:
            continuation_basis = 'OWNER_APPROVED_MUTATION_ACCOUNTING_UNCERTAINTY'
        else:
            continuation_basis = None
        return {'state': ('SOFT_BUDGET_EXCEEDED' if warnings else 'SAMPLED_SOFT_BUDGET_NOT_EXCEEDED')
                        if observation['scan_complete'] else 'UNKNOWN',
                'continuation_allowed': continuation_allowed,
                'reason': None if continuation_allowed else 'workspace_capacity_unverifiable',
                'continuation_basis': continuation_basis,
                'inventory_status': observation.get('inventory_status'),
                'measurement_quality': ('HARD_QUOTA_BOUND' if quota_valid else
                                        'COMPLETE_TRAVERSAL_NOT_PROOF' if observation['scan_complete'] else 'INCOMPLETE'),
                'warnings': warnings,
                'indeterminate_overages': indeterminate_overages,
                'errors': observation.get('scan_errors', []),
                'workspace_inventory_complete': observation['scan_complete'],
                'kernel_quota_sample': quota_sample,
                'proof_semantics': ('root-attested Btrfs squota max-referenced bound; not a physical-device bound; retained-disk reserve remains enforced'
                                     if quota_bound is not None else
                                     'owner-approved monitored soft budgets only; NOT hard enforcement/capacity proof'),
                'independent_capacity_upper_bound': quota_bound,
                'independent_capacity_upper_bound_semantics': ('Btrfs qgroup max referenced bytes, scoped to registered subvolume'
                                                               if quota_bound is not None else None)}
    for key, limit_key, reason in (
            ('allocated_st_blocks_bytes_gross', 'workspace_max_allocated_bytes', 'workspace_allocated_watchdog'),
            ('apparent_bytes', 'workspace_max_apparent_bytes', 'workspace_apparent_watchdog')):
        limit = (budget or {}).get(limit_key)
        value = observation.get(key)
        if limit is not None and isinstance(value, int) and not isinstance(value, bool) and value > limit:
            return {'state': 'OBSERVED_LIMIT_EXCEEDED', 'continuation_allowed': False,
                    'reason': reason, 'observed_bytes': value, 'limit_bytes': limit,
                    'proof_semantics': 'sampled aggregate breach; not an atomic/coexisting inventory proof'}
    if not observation['scan_complete']:
        return {'state': 'CAPACITY_UNVERIFIABLE', 'continuation_allowed': False,
                'reason': 'workspace_capacity_unverifiable', 'inventory_status': observation['inventory_status'],
                'errors': observation['scan_errors'], 'independent_capacity_upper_bound': None}
    return {'state': 'SAMPLED_NOT_EXCEEDED', 'continuation_allowed': True,
            'proof_semantics': 'existing sampled-abort policy only; NOT a capacity proof/atomic snapshot/verified peak',
            'independent_capacity_upper_bound': None}


def workspace_reservation_credit(_observation: dict[str, Any]) -> int:
    # Gross shared st_blocks and time-skewed inventory are not physical allocation lower bounds.
    return 0


def inspect_shared_extents(roots: list[pathlib.Path]) -> dict[str, Any]:
    exe = shutil.which('filefrag')
    if not exe:
        return {'available': False, 'reason': 'filefrag unavailable; Btrfs shared extents not classified'}
    files: list[str] = []
    for root in roots:
        for current, _, names in os.walk(root, followlinks=False):
            for name in names:
                p = pathlib.Path(current) / name
                if p.is_file() and not p.is_symlink():
                    files.append(str(p))
    shared_files: set[str] = set()
    errors: list[str] = []
    for i in range(0, len(files), 64):
        batch = files[i:i+64]
        try:
            result = subprocess.run([exe, '-v', *batch], capture_output=True, text=True, timeout=30)
            current_file: str | None = None
            for line in result.stdout.splitlines():
                if line.startswith('File size of '):
                    current_file = line[len('File size of '):].split(' is ', 1)[0]
                elif current_file and re.match(r'^\s*\d+:', line) and re.search(r'\bshared\b', line):
                    shared_files.add(current_file)
            if result.returncode not in (0, 1):
                errors.append(result.stderr[-500:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
    return {
        'available': True, 'method': 'filefrag -v FIEMAP flags on final workspace files',
        'files_examined': len(files), 'files_with_shared_extent_flag': len(shared_files),
        'shared_file_samples': sorted(shared_files)[:20], 'errors': errors,
        'scope': 'final tree only; not a historical peak/shared-byte de-duplication measurement',
    }


def write_json(path: pathlib.Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_approved_budget(path: pathlib.Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    budget = strict_json_file(path)
    if budget.get('schema_version') != SCHEMA_VERSION or budget.get('approval_status') != 'approved':
        raise ValueError('budget must have schema_version=1 and approval_status=approved')
    if not isinstance(budget.get('approval_reference'), str) or not budget['approval_reference'].strip():
        raise ValueError('approved budget needs a non-empty approval_reference')
    if budget.get('io_max') is not None:
        raise ValueError('cgroup io.max is unavailable on this delegated user slice; refusing an unenforceable I/O ceiling')
    for key in ('memory_max_bytes', 'memory_swap_max_bytes', 'workspace_max_allocated_bytes', 'workspace_max_apparent_bytes'):
        value = budget.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise ValueError(f'{key} must be a positive integer or null')
    tasks = budget.get('pids_max')
    if tasks is not None and (isinstance(tasks, bool) or not isinstance(tasks, int) or tasks <= 0):
        raise ValueError('pids_max must be a positive integer or null')
    cpu_max = budget.get('cpu_max')
    if cpu_max is not None and (not isinstance(cpu_max, str) or not re.fullmatch(r'(?:max|[1-9]\d*)\s+[1-9]\d*', cpu_max)):
        raise ValueError('cpu_max must have cgroup-v2 syntax "QUOTA PERIOD"')
    return budget


def apply_budget(cg: pathlib.Path, budget: dict[str, Any] | None) -> dict[str, Any]:
    applied: dict[str, Any] = {'approval_reference': None, 'memory_max_bytes': None,
                              'memory_swap_max_bytes': None, 'cpu_max': None, 'pids_max': None,
                              'workspace_limits': 'none'}
    if budget is None:
        return applied
    applied['approval_reference'] = budget['approval_reference']
    mapping = {
        'memory_max_bytes': ('memory.max', 'memory_max_bytes'),
        'memory_swap_max_bytes': ('memory.swap.max', 'memory_swap_max_bytes'),
        'cpu_max': ('cpu.max', 'cpu_max'),
        'pids_max': ('pids.max', 'pids_max'),
    }
    for source, (filename, target) in mapping.items():
        value = budget.get(source)
        if value is None:
            continue
        file = cg / filename
        if not writable_capability(file):
            raise RuntimeError(f'approved {source} cannot be enforced: {file} absent/not writable')
        file.write_text(str(value) + '\n', encoding='ascii')
        applied[target] = str(value)
    if budget.get('workspace_max_allocated_bytes') is not None or budget.get('workspace_max_apparent_bytes') is not None:
        applied['workspace_limits'] = 'sampled-abort-only; no kernel/filesystem quota is available'
    return applied


def proc_cgroup_contains(pid: int, expected: str) -> bool:
    return cgroup_relative(pid).strip('/') == expected.strip('/')


def signal_verified_member(pid: int, expected_rel: str, sig: int) -> bool:
    """Use pidfd plus a fresh cgroup check so exit/PID reuse cannot redirect a signal."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('pidfd signaling is required for safe process-tree termination')
    pidfd = os.pidfd_open(pid)
    try:
        if not proc_cgroup_contains(pid, expected_rel):
            return False
        signal.pidfd_send_signal(pidfd, sig)
        return True
    finally:
        os.close(pidfd)


def kill_owned_cgroup(cg: pathlib.Path, expected_rel: str, grace_seconds: float = 1.0,
                      preserve_pids: set[int] | None = None) -> dict[str, Any]:
    """Signal only members verified in this arm; optionally keep its tiny bootstrap alive for final sampling."""
    preserve = preserve_pids or set()
    signalled: list[int] = []
    killed: list[int] = []
    errors: list[str] = []
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            pids = [int(x) for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
        except OSError:
            pids = []
        targets = [pid for pid in pids if pid not in preserve]
        if not targets and (preserve or not pids):
            return {'term_pids': signalled, 'kill_pids': killed, 'preserved_pids': sorted(preserve & set(pids)), 'kill_fallback': False, 'errors': errors}
        for pid in targets:
            try:
                if signal_verified_member(pid, expected_rel, signal.SIGTERM):
                    signalled.append(pid)
            except (ProcessLookupError, FileNotFoundError):
                continue
            except (PermissionError, OSError) as exc:
                errors.append(f'pid {pid}: {exc}')
        time.sleep(0.025)
    if preserve:
        # Never cgroup.kill while preserving the receipt/bootstrap process. Recheck membership per PID.
        for _ in range(40):
            try:
                pids = [int(x) for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
            except OSError:
                pids = []
            targets = [pid for pid in pids if pid not in preserve]
            if not targets:
                break
            for pid in targets:
                try:
                    if signal_verified_member(pid, expected_rel, signal.SIGKILL):
                        killed.append(pid)
                except (ProcessLookupError, FileNotFoundError):
                    continue
                except (PermissionError, OSError) as exc:
                    errors.append(f'pid {pid}: {exc}')
            time.sleep(0.025)
        return {'term_pids': signalled, 'kill_pids': killed, 'preserved_pids': sorted(preserve), 'kill_fallback': False, 'errors': errors}
    try:
        kill_file = cg / 'cgroup.kill'
        if kill_file.exists() and os.access(kill_file, os.W_OK):
            kill_file.write_text('1\n', encoding='ascii')
            return {'term_pids': signalled, 'kill_pids': killed, 'kill_fallback': True, 'errors': errors}
        errors.append('cgroup.kill unavailable/unwritable')
    except OSError as exc:
        errors.append(f'cgroup.kill: {exc}')
    return {'term_pids': signalled, 'kill_pids': killed, 'kill_fallback': False, 'errors': errors}


def _bootstrap(sock_path: str, command: list[str]) -> int:
    if not command:
        return 125
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(sock_path)
        client.sendall((json.dumps({'pid': os.getpid(), 'cgroup': cgroup_relative()}) + '\n').encode())
        stream = client.makefile('rwb', buffering=0)
        line = stream.readline()
        if not line:
            return 125
        payload = strict_json_text(line)
        if payload.get('go') is not True:
            return 125
        env = payload.get('env')
        done_marker = pathlib.Path(payload['command_done_marker'])
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            return 125
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, env=env)
        child_started = {
            'pid': child.pid, 'cgroup_relative_path': cgroup_relative(child.pid),
            'timestamp_utc': utc_now(), 'monotonic_ns': time.monotonic_ns(),
        }
        client.sendall((json.dumps({'child_pid': child.pid, 'child_cgroup': child_started['cgroup_relative_path'],
                                    'child_started': child_started}) + '\n').encode())
        child_rc = child.wait()
        write_json(done_marker, {'returncode': child_rc, 'timestamp_utc': utc_now(), 'pid': child.pid})
        client.sendall((json.dumps({'command_returncode': child_rc}) + '\n').encode())
        final_line = stream.readline()
        if not final_line or strict_json_text(final_line).get('finalize') is not True:
            return 125
        # subprocess uses -signal; sys.exit(-signal) would wrap modulo 256.
        # Keep the signed child result in the protocol/receipt, but expose the
        # conventional 128+signal failure status to systemd for the bootstrap.
        return 128 - child_rc if child_rc < 0 else child_rc


def cgroup_populated(cg: pathlib.Path) -> int:
    return parse_kv(read_text(cg / 'cgroup.events')).get('populated', -1)


def watchdog_observer_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    sample_starts = [row.get('watchdog_sample_start_monotonic_ns') for row in samples]
    sample_starts = [value for value in sample_starts if isinstance(value, int)]
    gaps = [right - left for left, right in zip(sample_starts, sample_starts[1:], strict=False)]
    loop_cpu_ns = [row.get('watchdog_loop_cpu_ns') for row in samples]
    loop_cpu_ns = [value for value in loop_cpu_ns if isinstance(value, int) and value >= 0]
    loop_work_ns = [row.get('watchdog_loop_work_ns') for row in samples]
    loop_work_ns = [value for value in loop_work_ns if isinstance(value, int) and value >= 0]
    total_cpu_ns = sum(loop_cpu_ns)
    total_work_ns = sum(loop_work_ns)
    return {
        'telemetry_complete': bool(samples) and len(sample_starts) == len(samples)
        and len(loop_cpu_ns) == len(samples) and len(loop_work_ns) == len(samples),
        'sample_start_count': len(sample_starts),
        'loop_cpu_sample_count': len(loop_cpu_ns),
        'loop_work_sample_count': len(loop_work_ns),
        'sample_start_gap_max_ns': max(gaps, default=None),
        'watchdog_loop_cpu_ns_sum': total_cpu_ns,
        'watchdog_loop_work_ns_sum': total_work_ns,
        'watchdog_cpu_to_active_work_ratio': total_cpu_ns / total_work_ns if total_work_ns else None,
        'watchdog_loop_cpu_ns_max': max(loop_cpu_ns, default=None),
        'watchdog_loop_work_ns_max': max(loop_work_ns, default=None),
    }


def read_watchdog_config(path: pathlib.Path) -> dict[str, Any]:
    config = strict_json_file(path)
    for key in ('cgroup_path', 'cgroup_rel', 'workspace_root', 'resource_root', 'sample_interval_ms',
                'runtime_limit_seconds', 'watchdog_output', 'controller_pid', 'bootstrap_pid', 'start_marker',
                'command_done_marker', 'watchdog_ready_path', 'socket_dir', 'pressure_limits'):
        if key not in config:
            raise ValueError(f'watchdog config missing {key}')
    validate_watchdog_operation_binding(config)
    return config


def request_operator_stop(receipt_dir: pathlib.Path, confirm_unit: str) -> dict[str, Any]:
    receipt_dir = receipt_dir.resolve()
    config = read_watchdog_config(receipt_dir / 'watchdog-config.json')
    if not confirm_unit or config.get('unit') != confirm_unit:
        raise ValueError('confirmation unit does not match the operation-bound systemd unit')
    if pathlib.Path(config['watchdog_output']).resolve() != receipt_dir:
        raise ValueError('watchdog output is not bound to the supplied receipt directory')
    run_state = strict_json_file(receipt_dir / 'run-state.json')
    if run_state.get('unit') != confirm_unit or run_state.get('state') != 'delegated_and_armed':
        raise ValueError('run-state is not an active armed operation for the confirmed unit')
    if not pathlib.Path(config['watchdog_ready_path']).is_file():
        raise RuntimeError('watchdog has not completed its initial hard-safety telemetry sample')
    if not pathlib.Path(config['start_marker']).is_file():
        raise RuntimeError('workload has not reached the started state')
    cg = pathlib.Path(config['cgroup_path'])
    if cgroup_path_from_unit(confirm_unit) != cg or not cg.exists():
        raise RuntimeError('confirmed unit no longer maps to the operation-owned cgroup')
    if str(cg.relative_to(CGROOT)) != config['cgroup_rel'] or cgroup_populated(cg) != 1:
        raise RuntimeError('operation cgroup identity/population is not active')
    if pathlib.Path(config['command_done_marker']).exists():
        raise RuntimeError('workload has already completed; refusing a late stop request')
    if (receipt_dir / 'watchdog-abort.json').exists() or (receipt_dir / 'receipt.json').exists():
        raise RuntimeError('operation already has a terminal receipt')

    marker = receipt_dir / 'operator-stop-request.json'
    temporary = receipt_dir / f'.operator-stop-request-{uuid.uuid4().hex}.tmp'
    request = {
        'schema_version': 1,
        'unit': confirm_unit,
        'cgroup_relative_path': config['cgroup_rel'],
        'controller_pid': config['controller_pid'],
        'policy_context_digest': config.get('policy_context_digest'),
        'requested_at_utc': utc_now(),
        'requested_monotonic_ns': time.monotonic_ns(),
    }
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    published = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(request, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, marker)  # atomic, no-overwrite publication for the watchdog
        published = True
        temporary.unlink()
        dir_fd = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        if not published:
            raise
        # Publication already reached the watchdog: preserve the request if a
        # later durability operation reports an error rather than retract it.
        raise
    return request


def validate_watchdog_operation_binding(config: dict[str, Any]) -> dict[str, Any]:
    context = config.get('policy_context')
    if (config.get('policy_mode') == CONTENTION_MODE) != (context is not None):
        raise ValueError('watchdog policy mode/context mismatch')
    if context is None:
        return config
    validate_contention_context(context)
    arm = config.get('arm_name')
    expected = pathlib.Path(context['operation_root']) / str(arm) / 'containment'
    allowed_arms = (('pair-first', 'pair-second')
                    if context.get('purpose') in (M4_SHARED_COLLECTION_PAIR_PURPOSE,
                                                   M4_WRITE_AMPLIFICATION_PURPOSE,
                                                   M4_RUST_LIFECYCLE_PURPOSE)
                    else ('microbench',) if context.get('purpose') == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE
                    else ('aa-first', 'aa-second'))
    if (arm not in allowed_arms or
            pathlib.Path(config['watchdog_output']).resolve() != expected or
            str(pathlib.Path(config['resource_root']).resolve()) != context['resource_root'] or
            not config.get('enforce_host_gates', True) or
            config.get('budget') != contention_budget(context) or
            config['runtime_limit_seconds'] != context['runtime_limit_seconds'] or
            config.get('policy_context_digest') != contention_context_digest(context)):
        raise ValueError('watchdog contention context/budget/operation binding mismatch')
    return config


def watchdog_phase(arm_name: str, started: bool) -> str:
    if arm_name not in ('aa-first', 'aa-second', 'pair-first', 'pair-second', 'microbench'):
        raise ValueError(f'invalid contention arm name: {arm_name}')
    if not started:
        return arm_name
    return 'runtime-second' if arm_name in ('aa-second', 'pair-second') else 'runtime-first'


def watchdog_main(config_path: pathlib.Path) -> int:
    config = read_watchdog_config(config_path)
    cg = pathlib.Path(config['cgroup_path'])
    expected = config['cgroup_rel']
    workspace = pathlib.Path(config['workspace_root'])
    resource_root = pathlib.Path(config['resource_root'])
    interval = max(20, int(config['sample_interval_ms'])) / 1000.0
    runtime_limit = float(config['runtime_limit_seconds'])
    controller_pid = int(config['controller_pid'])
    bootstrap_pid = int(config['bootstrap_pid'])
    samples: list[dict[str, Any]] = []
    abort: dict[str, Any] | None = None
    started_ns: int | None = None
    try:
        pidfd = os.pidfd_open(controller_pid)
    except (AttributeError, OSError) as exc:
        # A pidfd is required: PID polling is vulnerable to PID reuse.
        abort = {'reason': 'pidfd_unavailable', 'detail': str(exc), 'timestamp_utc': utc_now()}
        pidfd = -1
    poller = None
    if pidfd >= 0:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
    start_marker = pathlib.Path(config['start_marker'])
    command_done_marker = pathlib.Path(config['command_done_marker'])
    ready_marker = pathlib.Path(config['watchdog_ready_path'])
    socket_dir = pathlib.Path(config['socket_dir'])
    operator_stop_path = pathlib.Path(config['watchdog_output']) / 'operator-stop-request.json'
    while abort is None:
        if operator_stop_path.exists() and not command_done_marker.exists():
            try:
                request = strict_json_file(operator_stop_path)
                expected_request = {
                    'schema_version': 1,
                    'unit': config['unit'],
                    'cgroup_relative_path': expected,
                    'controller_pid': controller_pid,
                    'policy_context_digest': config.get('policy_context_digest'),
                }
                if any(request.get(key) != value for key, value in expected_request.items()):
                    raise ValueError('operator stop request identity does not match watchdog operation')
                abort = {
                    'reason': 'operator_requested_stop',
                    'operator_stop_request': request,
                    'timestamp_utc': utc_now(),
                }
            except Exception as exc:
                abort = {
                    'reason': 'operator_stop_request_invalid',
                    'detail': f'{type(exc).__name__}: {exc}',
                    'timestamp_utc': utc_now(),
                }
            break
        loop_started_ns = time.monotonic_ns()
        loop_cpu_started_ns = time.process_time_ns()
        row: dict[str, Any] | None = None
        loop_done = False
        if poller is not None and poller.poll(0) and cg.exists() and cgroup_populated(cg) == 1:
            abort = {'reason': 'supervisor_lost', 'timestamp_utc': utc_now()}
            break
        if started_ns is None and start_marker.exists():
            started_ns = time.monotonic_ns()
        try:
            sample_started_ns = time.monotonic_ns()
            sample_started_boottime_ns = (
                boottime_ns()
                if config.get('policy_context') is not None
                else None
            )
            sample_ended_boottime_ns: int | None = None
            if not cg.exists():
                abort = {'reason': 'cgroup_telemetry_disappeared', 'timestamp_utc': utc_now()}
                break
            cgroup_row = read_cgroup_metric(cg)
            host_row = host_snapshot(resource_root)
            budget = config.get('budget')
            quota_sample = workspace_quota_live_sample(budget)
            policy_context = config.get('policy_context')
            if (policy_context is not None
                    and policy_context.get('purpose') == M4_RUST_LIFECYCLE_PURPOSE
                    and workspace_quota_bound_bytes(budget) is not None):
                work_row = workspace_quota_bounded_observation(workspace)
            else:
                work_row = workspace_usage(workspace)
            workspace_decision = workspace_capacity_decision(
                work_row, budget, config.get('policy_mode', 'legacy'), quota_sample)
            row = {
                'timestamp_utc': utc_now(), 'monotonic_ns': time.monotonic_ns(),
                'watchdog_sample_start_monotonic_ns': sample_started_ns,
                'elapsed_ns': None if started_ns is None else time.monotonic_ns() - started_ns,
                'cgroup': cgroup_row, 'host': host_row, 'workspace': work_row,
                'workspace_quota': quota_sample,
                'workspace_capacity_decision': workspace_decision,
            }
            if config.get('policy_context') is not None:
                sample_ended_boottime_ns = boottime_ns()
                row['boottime_ns'] = sample_ended_boottime_ns
            host_observation: dict[str, Any] | None = None
            if is_m4_scalar_open_exploratory(config.get('policy_context')):
                host_observation = m4_scalar_rehearsal_host_observation(host_row, config['baseline_host_vmstat'])
                row['host_vmstat_observation'] = host_observation
                row['load1_disposition'] = 'observation-only; not an admission or abort gate'
            if config.get('policy_context') is not None:
                assert sample_started_boottime_ns is not None
                assert sample_ended_boottime_ns is not None
                row['sample_start_monotonic_ns'] = sample_started_ns
                row['sample_duration_ns'] = row['monotonic_ns'] - sample_started_ns
                row['sample_start_boottime_ns'] = sample_started_boottime_ns
                row['sample_duration_boottime_ns'] = (
                    sample_ended_boottime_ns - sample_started_boottime_ns
                )
                max_gap_ns = config['policy_context']['max_monitor_gap_seconds'] * 1e9
                if (row['sample_duration_ns'] > max_gap_ns or
                        row['sample_duration_boottime_ns'] > max_gap_ns):
                    raise ValueError('contention sample read exceeds approved monitoring gap')
            samples.append(row)
            context = config.get('policy_context')
            context_failures = []
            if context is not None:
                verify_contention_caps(cg, context)
                phase = watchdog_phase(config['arm_name'], started_ns is not None)
                decision = contention_decision(host_row, context, phase, workspace_reservation_credit(work_row),
                                                cgroup_row['memory_swap_current_bytes'])
                row['diagnostic_decision'] = decision
                previous = samples[-2] if len(samples) > 1 else None
                distress = contention_runtime_failures(cgroup_row, None if previous is None else previous['cgroup'])
                if is_m4_scalar_open_exploratory(context):
                    if host_observation is None:
                        raise ValueError('missing resource-only host observation')
                    distress.extend(host_observation['blocking_failures'])
                if previous is not None:
                    elapsed = (row['monotonic_ns'] - previous['monotonic_ns']) / 1e9
                    assert sample_ended_boottime_ns is not None
                    previous_boottime_ns = previous.get('boottime_ns')
                    if not isinstance(previous_boottime_ns, int):
                        raise ValueError('missing/noninteger contention CLOCK_BOOTTIME')
                    boottime_elapsed = (
                        sample_ended_boottime_ns - previous_boottime_ns
                    ) / 1e9
                    if (elapsed <= 0 or elapsed > context['max_monitor_gap_seconds'] or
                            boottime_elapsed <= 0 or
                            boottime_elapsed > context['max_monitor_gap_seconds']):
                        raise ValueError('contention monitor cadence incomplete/nonmonotonic')
                    for key in ('pswpin_pages', 'pswpout_pages', 'oom_kill', 'pgscan_direct'):
                        if host_row['vmstat'][key] < previous['host']['vmstat'][key]:
                            raise ValueError('nonmonotonic host swap/reclaim telemetry')
                    if host_row['vmstat']['oom_kill'] > previous['host']['vmstat']['oom_kill']:
                        distress.append('host OOM kill observed; attribution to M4 not established')
                baseline_oom = (config.get('pressure_limits') or {}).get('contention_baseline_host_oom_kill')
                if baseline_oom is not None and host_row['vmstat']['oom_kill'] > baseline_oom:
                    distress.append('host OOM since ambient baseline; attribution to M4 not established')
                context_failures = decision['blocking_failures'] + distress
                if context_failures:
                    abort = {'reason': 'contention_resource_risk', 'failures': context_failures, 'timestamp_utc': utc_now()}
            if not workspace_decision['continuation_allowed']:
                abort = dict(workspace_decision, timestamp_utc=utc_now())
            elif started_ns is not None:
                context = config.get('policy_context')
                if context is not None:
                    failures = context_failures
                    pressure_failures = []  # historical baseline bounds are observations, not safety facts
                else:
                    failures = hard_gate_failures(host_row) if config.get('enforce_host_gates', True) else []
                    pressure_failures = pressure_baseline_failures(host_row, config.get('pressure_limits'))
                if failures or pressure_failures:
                    abort = {
                        'reason': 'existing_host_guard' if failures else 'memory_swap_pressure_guard',
                        'failures': failures, 'pressure_failures': pressure_failures,
                        'timestamp_utc': utc_now(),
                    }
                if abort is None and time.monotonic_ns() - started_ns >= int(runtime_limit * 1e9):
                    abort = {'reason': 'runtime_timeout', 'limit_seconds': runtime_limit, 'timestamp_utc': utc_now()}
            baseline_events = config.get('baseline_memory_events', {})
            for k in ('oom', 'oom_kill', 'oom_group_kill'):
                if cgroup_row['memory_events'].get(k, 0) > baseline_events.get(k, 0):
                    abort = {'reason': 'cgroup_oom_event', 'event': k, 'observed': cgroup_row['memory_events'].get(k), 'timestamp_utc': utc_now()}
                    break
            if not ready_marker.exists() and abort is None:
                if context is not None:
                    write_json(ready_marker, {'mode': CONTENTION_MODE, 'policy_context_digest': contention_context_digest(context)})
                else:
                    ready_marker.write_text(str(time.monotonic_ns()), encoding='ascii')
            loop_done = cgroup_row.get('populated') == 0 or command_done_marker.exists()
        except Exception as exc:
            abort = {'reason': 'required_telemetry_missing', 'detail': f'{type(exc).__name__}: {exc}', 'timestamp_utc': utc_now()}
        if row is not None and samples and samples[-1] is row:
            row['watchdog_loop_work_ns'] = time.monotonic_ns() - loop_started_ns
            row['watchdog_loop_cpu_ns'] = time.process_time_ns() - loop_cpu_started_ns
        if abort is not None or loop_done:
            break
        time.sleep(interval)
    if abort is not None and cg.exists():
        kill_result = kill_owned_cgroup(cg, expected, preserve_pids={bootstrap_pid})
        abort['termination'] = kill_result
    final_cgroup = None
    try:
        if cg.exists():
            final_cgroup = read_cgroup_metric(cg)
            if config.get('policy_context') is not None:
                final_distress = contention_runtime_failures(final_cgroup, samples[-1]['cgroup'] if samples else None)
                if final_distress and abort is None:
                    abort = {'reason': 'contention_resource_risk', 'failures': final_distress, 'timestamp_utc': utc_now()}
    except Exception as exc:
        final_cgroup = {'error': f'{type(exc).__name__}: {exc}'}
    fiemap = {'available': False, 'reason': 'No extra recursive FIEMAP walk in bounded observer; extent sharing unknown, never capacity proof'}
    outdir = pathlib.Path(config['watchdog_output'])
    runtime_path = outdir / 'runtime.jsonl'
    tmp = runtime_path.with_suffix('.jsonl.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        for row in samples:
            f.write(json.dumps(row, sort_keys=True) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(runtime_path)
    if abort is not None:
        abort['failure_class'] = failure_class(reason=abort.get('reason'))
    process_samples_complete = bool(samples) and all(
        row['cgroup'].get('process_io_sample_complete') and
        row['cgroup'].get('process_memory_sample_complete') for row in samples
    )
    workspace_samples_complete = bool(samples) and all(r['workspace']['scan_complete'] for r in samples)
    workspace_quota_bound = workspace_quota_bound_bytes(config.get('budget'))
    workspace_quota_rows = [r.get('workspace_quota') for r in samples]
    workspace_quota_samples_complete = (
        workspace_quota_bound is None
        or bool(workspace_quota_rows and all(
            isinstance(row, dict) and row.get('status') == 'PASS' for row in workspace_quota_rows))
    )
    complete_workspace_rows = [r['workspace'] for r in samples if r['workspace']['scan_complete']]
    memory_stat_rows = [r['cgroup']['memory_stat_bytes'] for r in samples
                        if isinstance(r['cgroup'].get('memory_stat_bytes'), dict)]
    memory_stat_keys = set().union(*(row.keys() for row in memory_stat_rows)) if memory_stat_rows else set()
    summary = {
        'schema_version': SCHEMA_VERSION, 'samples': len(samples),
        'observer': watchdog_observer_summary(samples),
        'workspace_samples_complete': workspace_samples_complete,
        'workspace_accounting_status': ('SAMPLED_NOT_PROOF' if workspace_samples_complete else
                                        'UNKNOWN_INVENTORY_KERNEL_QUOTA_BOUND' if workspace_quota_samples_complete and workspace_quota_bound is not None else
                                        'UNKNOWN'),
        'workspace_capacity_upper_bound_bytes': workspace_quota_bound,
        'workspace_quota_samples_complete': workspace_quota_samples_complete,
        'workspace_quota_referenced_peak_bytes': max(
            (row['referenced_bytes'] for row in workspace_quota_rows
             if isinstance(row, dict) and row.get('status') == 'PASS'), default=None),
        'workspace_quota_minimum_headroom_bytes': min(
            (row['available_bytes'] for row in workspace_quota_rows
             if isinstance(row, dict) and row.get('status') == 'PASS'), default=None),
        'workspace_capacity_bound_semantics': ('root-attested Btrfs squota max referenced bytes; excludes host/global metadata/chunk overhead'
                                               if workspace_quota_bound is not None else None),
        'workspace_soft_budget_warning_samples': sum(bool(r['workspace_capacity_decision'].get('warnings')) for r in samples),
        'workspace_soft_budget_indeterminate_samples': sum(bool(r['workspace_capacity_decision'].get('indeterminate_overages')) for r in samples),
        'hard_safety_telemetry_complete': bool(
            samples and process_samples_complete and isinstance(final_cgroup, dict) and not final_cgroup.get('error')
            and workspace_quota_samples_complete),
        'workspace_snapshot_semantics': ('sampled traversal, NOT atomic snapshot or verified lifetime peak'
                                         if workspace_samples_complete else
                                         'workspace inventory NOT sampled; live qgroup capacity samples only; soft-budget peaks UNKNOWN'),
        'workspace_verified_peak': None,
        'max_observed_complete_traversal_apparent_bytes': max((w['apparent_bytes'] for w in complete_workspace_rows), default=None),
        'max_observed_complete_traversal_allocated_bytes': max((w['allocated_st_blocks_bytes_gross'] for w in complete_workspace_rows), default=None),
        'policy_mode': config.get('policy_mode', 'legacy'),
        'policy_context_digest': config.get('policy_context_digest'),
        'measurement_quality': ('HARD_QUOTA_BOUND' if samples and process_samples_complete and workspace_quota_bound is not None and workspace_quota_samples_complete and isinstance(final_cgroup, dict) and not final_cgroup.get('error') else
                                'COMPLETE' if samples and process_samples_complete and workspace_samples_complete and isinstance(final_cgroup, dict) and not final_cgroup.get('error') else
                                'INCOMPLETE'),
        'process_tree_samples_complete': process_samples_complete,
        'safety_outcome': 'ABORTED_CONTAINED' if abort is not None else 'WATCHDOG_COMPLETED_NO_ABORT',
        'sample_interval_ms_requested': config['sample_interval_ms'],
        'sampling_scope': ('external watchdog; workspace scans are sampled and may miss short-lived files'
                           if workspace_samples_complete else
                           'external watchdog; live kernel qgroup capacity sampled; workspace inventory and soft-budget peaks UNKNOWN'),
        'abort': abort, 'final_cgroup': final_cgroup,
        'workspace_shared_extent_inspection': fiemap,
        'peak_sampled_workspace_apparent_bytes': max((w['apparent_bytes'] for w in complete_workspace_rows), default=None) if workspace_samples_complete else None,
        'peak_sampled_workspace_allocated_st_blocks_bytes_gross': max((w['allocated_st_blocks_bytes_gross'] for w in complete_workspace_rows), default=None) if workspace_samples_complete else None,
        'peak_sampled_staging_named_apparent_bytes': max((w['staging_named_apparent_bytes'] for w in complete_workspace_rows), default=None) if workspace_samples_complete else None,
        'peak_sampled_generation_apparent_bytes': max((w['generation_apparent_bytes'] for w in complete_workspace_rows), default=None) if workspace_samples_complete else None,
        'peak_cgroup_memory_current_bytes_sampled': max((r['cgroup']['memory_current_bytes'] for r in samples), default=None),
        'peak_cgroup_memory_peak_bytes_observed': max((r['cgroup']['memory_peak_bytes'] for r in samples), default=None),
        'cgroup_memory_stat_available': bool(samples) and len(memory_stat_rows) == len(samples),
        'peak_sampled_cgroup_memory_stat_bytes_by_field': {
            key: max((row.get(key, 0) for row in memory_stat_rows), default=None)
            for key in sorted(memory_stat_keys)
        } if memory_stat_rows else None,
        'memory_stat_peak_semantics': 'per-field maxima may come from different samples; not an atomic decomposition of memory.current',
        'peak_cgroup_swap_current_bytes_sampled': max((r['cgroup']['memory_swap_current_bytes'] for r in samples), default=None),
        'peak_cgroup_swap_peak_bytes_observed': max((r['cgroup']['memory_swap_peak_bytes'] for r in samples), default=None),
        'cgroup_io_stat_available': any(r['cgroup']['io_stat'] is not None for r in samples),
        'final_cgroup_sampling': 'captured while transient unit remains active after ExecStart; systemd unit is stopped after this receipt is durable',
    }
    if is_m4_scalar_open_exploratory(config.get('policy_context')):
        load_samples = [r['host'].get('loadavg', [None])[0] for r in samples
                        if isinstance(r['host'].get('loadavg'), list) and r['host']['loadavg']]
        final_host = samples[-1]['host'] if samples else {}
        summary['host_resource_observations'] = {
            'load1_min': min(load_samples) if load_samples else None,
            'load1_max': max(load_samples) if load_samples else None,
            'load1_samples': len(load_samples),
            'load1_disposition': 'observational-only; not admission or abort criterion',
            'vmstat_baseline_to_final': m4_scalar_rehearsal_host_observation(
                final_host, config['baseline_host_vmstat']) if samples else None,
            'vmstat_interpretation': 'positive swap/reclaim deltas are recorded temporal observations, not standalone distress proof; OOM increments and counter-integrity failures block; no host-wide attribution to M4',
        }
    write_json(outdir / 'watchdog-summary.json', summary)
    if abort is not None:
        abort['final_cleanup'] = {'method': 'cgroup.kill', 'attempted': True, 'success': False}
        abort['cleanup_complete'] = False
        write_json(outdir / 'watchdog-abort.json', abort)
        try:
            kill_file = cg / 'cgroup.kill'
            if kill_file.exists() and os.access(kill_file, os.W_OK):
                kill_file.write_text('1\n', encoding='ascii')
                abort['final_cleanup']['success'] = True
            else:
                result = subprocess.run(
                    ['systemctl', '--user', 'stop', str(config['unit'])],
                    capture_output=True, text=True, timeout=5,
                )
                abort['final_cleanup'] = {
                    'method': 'systemctl-user-stop', 'attempted': True,
                    'success': result.returncode == 0, 'returncode': result.returncode,
                    'stderr': result.stderr[-500:],
                }
        except (OSError, subprocess.TimeoutExpired) as exc:
            abort['final_cleanup']['error'] = f'{type(exc).__name__}: {exc}'
        if abort.get('reason') == 'supervisor_lost':
            try:
                shutil.rmtree(socket_dir, ignore_errors=True)
            except OSError as exc:
                abort['socket_cleanup_error'] = str(exc)
            abort['socket_cleanup_verified'] = not socket_dir.exists()
            if not abort['socket_cleanup_verified']:
                abort['final_cleanup']['success'] = False
        abort['cleanup_complete'] = True  # terminal publication, not merely abort initiation
        write_json(outdir / 'watchdog-abort.json', abort)
    if pidfd >= 0:
        os.close(pidfd)
    return 0


def service_properties(budget: dict[str, Any] | None, runtime_seconds: float) -> list[str]:
    props = [
        'Type=exec', 'Delegate=yes', 'ProtectControlGroups=yes',
        'MemoryAccounting=yes', 'KillMode=control-group', 'TimeoutStopSec=2s',
        f'RuntimeMaxSec={runtime_seconds + 5.0:.3f}s',
    ]
    if budget:
        if budget.get('pids_max') is not None:
            props.append(f"TasksMax={budget['pids_max']}")
        if budget.get('memory_max_bytes') is not None:
            props.append(f"MemoryMax={budget['memory_max_bytes']}")
        if budget.get('memory_swap_max_bytes') is not None:
            props.append(f"MemorySwapMax={budget['memory_swap_max_bytes']}")
        if budget.get('cpu_max') is not None:
            quota, period = budget['cpu_max'].split()
            if quota == 'max':
                props.append('CPUQuota=infinity')
            else:
                # systemd-run rejects e.g. 200.000% on this host. Preserve exact
                # integer percentages; do not round or broaden the approved quota.
                percent, remainder = divmod(100 * int(quota), int(period))
                value = str(percent) if remainder == 0 else f'{100.0 * int(quota) / int(period):.3f}'
                props.append(f'CPUQuota={value}%')
    return props


def make_receipt_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(path, 0o700)


def retire_unit(unit: str, cg: pathlib.Path | None, receipt_dir: pathlib.Path,
                receipt: dict[str, Any]) -> None:
    """Retire only this arm's unit, after preserving its result and receipt."""
    cleanup: dict[str, Any] = {'verified': False, 'unit_collected': False}
    try:
        # Do not use --collect: failed-unit metadata must survive until read.
        metadata = receipt_dir / 'systemd-final.txt'
        if not metadata.exists():
            props = subprocess.run(
                ['systemctl', '--user', 'show', unit, '-p', 'Result', '-p', 'ExecMainCode',
                 '-p', 'ExecMainStatus', '-p', 'ControlGroup'],
                capture_output=True, text=True, timeout=5)
            metadata.write_text(props.stdout + props.stderr, encoding='utf-8')
        if not (receipt_dir / 'receipt.json').is_file():
            raise RuntimeError('refusing unit retirement before receipt persistence')
        stopped = subprocess.run(['systemctl', '--user', 'stop', unit],
                                 capture_output=True, text=True, timeout=3)
        cleanup['stop_returncode'] = stopped.returncode
        state = subprocess.run(
            ['systemctl', '--user', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState'],
            capture_output=True, text=True, timeout=3)
        props = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
        absent = props.get('LoadState') == 'not-found'
        empty = (cg is not None and (not cg.exists() or
                 (not read_text(cg / 'cgroup.procs').strip() and cgroup_populated(cg) == 0)))
        cleanup['verified'] = bool((absent or props.get('ActiveState') in {'inactive', 'failed'})
                                   and (empty if cg is not None else absent))
        # Persist cleanup verification before clearing failed-unit state.
        write_json(receipt_dir / 'unit-cleanup.json', cleanup)
        if not cleanup['verified']:
            raise RuntimeError('unit/cgroup cleanup not verified; retaining failed-unit state')
        if not absent:
            reset = subprocess.run(['systemctl', '--user', 'reset-failed', unit],
                                   capture_output=True, text=True, timeout=3)
            cleanup['reset_failed_returncode'] = reset.returncode
        state = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'LoadState', '--value'],
                               capture_output=True, text=True, timeout=3)
        cleanup['unit_collected'] = state.stdout.strip() == 'not-found'
        if not cleanup['unit_collected']:
            cleanup['error'] = 'owned transient unit still loaded after retirement'
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        cleanup['error'] = f'{type(exc).__name__}: {exc}'
    receipt['unit_cleanup'] = cleanup
    if not cleanup['verified'] or not cleanup['unit_collected']:
        receipt['pre_retirement_outcome'] = {key: receipt.get(key) for key in
                                             ('status', 'failure_class', 'safety_outcome', 'operation_outcome')}
        receipt.update(status='CLEANUP_FAILED', failure_class='CONTAINMENT_UNVERIFIED',
                       safety_outcome='CONTAINMENT_UNVERIFIED', operation_outcome='SAFETY_STOP',
                       performance_eligible=False)
    write_json(receipt_dir / 'unit-cleanup.json', cleanup)
    write_json(receipt_dir / 'receipt.json', receipt)


def contention_completion_status(status: str, hard_complete: bool, measurement_complete: bool,
                                workspace_warning_samples: int,
                                reason: str | None = None,
                                workspace_samples_complete: bool = True) -> tuple[str, str | None]:
    """Keep workspace caveats distinct from PASS in exploratory receipts."""
    if status != 'PASS':
        return status, reason
    if not hard_complete:
        return 'TELEMETRY_INCOMPLETE', 'incomplete required contention safety/process telemetry'
    if not measurement_complete or not workspace_samples_complete:
        return 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'workspace accounting INCOMPLETE/UNKNOWN'
    if workspace_warning_samples:
        return 'COMPLETED_WITH_WORKSPACE_WARNINGS', 'workspace soft budget exceeded; see raw warnings'
    return status, None


def run_arm(command: list[str], receipt_dir: pathlib.Path, resource_root: pathlib.Path,
            workspace_root: pathlib.Path, cwd: pathlib.Path, runtime_seconds: float,
            sample_interval_ms: int = 100, budget: dict[str, Any] | None = None,
            env: dict[str, str] | None = None, enforce_host_gates: bool = True,
            pressure_limits: dict[str, Any] | None = None,
            policy_context: dict[str, Any] | None = None) -> dict[str, Any]:
    arm_name = receipt_dir.parent.name
    if policy_context is not None:
        validate_contention_context(policy_context)
        rehearsal = policy_context.get('purpose') == 'synthetic-containment-rehearsal'
        m4_rehearsal = policy_context.get('purpose') == M4_SCALAR_RESOURCE_PURPOSE
        m4_latency = policy_context.get('purpose') == M4_SCALAR_LATENCY_PURPOSE
        m4_scalar_open = m4_rehearsal or m4_latency
        m4_shared_pair = policy_context.get('purpose') == M4_SHARED_COLLECTION_PAIR_PURPOSE
        m4_lock_microbench = policy_context.get('purpose') == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE
        m4_write_amplification = policy_context.get('purpose') == M4_WRITE_AMPLIFICATION_PURPOSE
        m4_rust_lifecycle = policy_context.get('purpose') == M4_RUST_LIFECYCLE_PURPOSE
        expected_flag = ('--m4-scalar-open-latency-diagnostic' if m4_latency
                         else '--m4-scalar-open-resource-rehearsal')
        binary_path = pathlib.Path(policy_context.get('binary_path', '')).resolve()
        if rehearsal:
            command_bound = command == [sys.executable, '-c', REHEARSAL_CODE]
        elif m4_scalar_open:
            command_bound = (
                len(command) == 3 and command[1] == str(workspace_root.resolve() / 'root') and
                command[2] == expected_flag and binary_path == pathlib.Path(command[0]).resolve() and
                hashlib.sha256(binary_path.read_bytes()).hexdigest() == policy_context['binary_sha256'])
        elif m4_shared_pair:
            expected_mode = (BENCHMARK_POLICY['shared_collection_pair']['workload']['baseline_mode']
                             if policy_context['arm_role'] == 'immutable-shard' else
                             BENCHMARK_POLICY['shared_collection_pair']['workload']['candidate_mode'])
            command_bound = (
                len(command) == 6 and command[1] == str(workspace_root.resolve() / 'root') and
                binary_path == pathlib.Path(command[0]).resolve() and
                pathlib.Path(command[2]).resolve() == pathlib.Path(policy_context['dictionary_path']).resolve() and
                command[3:] == [expected_mode, '500', '5'] and
                not pathlib.Path(command[1]).exists() and
                hashlib.sha256(binary_path.read_bytes()).hexdigest() == policy_context['binary_sha256'])
        elif m4_lock_microbench:
            contract_workload = lock_contention_workload(policy_context)
            command_bound = (
                len(command) == 6 and command[1] == str(workspace_root.resolve() / 'root') and
                binary_path == pathlib.Path(command[0]).resolve() and
                pathlib.Path(command[2]).resolve() == pathlib.Path(policy_context['dictionary_path']).resolve() and
                command[3:] == [contract_workload['mode'], str(contract_workload['documents']),
                                str(contract_workload['rounds'])] and
                not pathlib.Path(command[1]).exists() and
                hashlib.sha256(binary_path.read_bytes()).hexdigest() == policy_context['binary_sha256'])
        elif m4_rust_lifecycle:
            expected_workspace = pathlib.Path(policy_context['workspace_root']).resolve()
            expected_binary = pathlib.Path(policy_context['binary_path']).resolve()
            expected_command = [str(expected_binary), '--exact', policy_context['workload']['test_name'], '--ignored', '--nocapture']
            expected_env = rust_lifecycle_expected_environment(policy_context)
            command_bound = (
                command == expected_command and cwd.resolve() == expected_workspace and
                (env or {}) == expected_env and expected_workspace.is_dir() and
                pathlib.Path(policy_context['source_root']).is_dir() and
                not pathlib.Path(policy_context['index_root']).exists() and
                not pathlib.Path(policy_context['report_path']).exists() and
                pathlib.Path(policy_context['temporary_directory']).is_dir() and
                pathlib.Path(policy_context['binary_path']).is_file() and
                hashlib.sha256(expected_binary.read_bytes()).hexdigest() == policy_context['binary_sha256'])
        elif m4_write_amplification:
            workload = policy_context['workload']
            role = policy_context['arm_role']
            expected_arm = workload['control_arm'] if role == 'control' else workload['candidate_arm']
            expected_phase_role = write_amplification_phase_role(policy_context, arm_name)
            expected_delta_docs = workload[f'{role}_max_delta_docs']
            expected_total_runs = workload[f'{role}_max_total_runs']
            expected_level_runs = workload[f'{role}_max_runs_per_level']
            expected_stats_threshold = workload[f'{role}_stats_log_threshold']
            expected_mutation_batch_docs = workload[f'{role}_mutation_batch_docs']
            expected_wal_replay_batch_docs = workload.get(
                f'{role}_wal_replay_batch_docs', expected_mutation_batch_docs)
            expected_wal_replay_probe = '1' if workload.get('wal_replay_probe', False) else '0'
            expected_wal_replay_torn_tail = '1' if workload.get(
                'wal_replay_torn_tail', False) else '0'
            expected_wal_replay_mixed_frames = '1' if workload.get(
                f'{role}_wal_replay_mixed_frames', False) else '0'
            expected_coalesce_stats = '1' if workload[f'{role}_coalesce_stats'] else '0'
            expected_wal_compression_lz4 = '1' if workload[f'{role}_wal_compression_lz4'] else '0'
            expected_wal_lz4_acceleration = str(workload[f'{role}_wal_lz4_acceleration'])
            arm_env = env or {}
            expected_index_dir = workspace_root / 'index'
            expected_tmpdir = workspace_root / 'tmp'
            fresh_workspace = (
                workspace_root.is_dir() and {p.name for p in workspace_root.iterdir()} == {'index', 'tmp'} and
                expected_index_dir.is_dir() and not any(expected_index_dir.iterdir()) and
                expected_tmpdir.is_dir() and not any(expected_tmpdir.iterdir()))
            command_bound = (
                len(command) == 5 and arm_name in ('pair-first', 'pair-second') and
                policy_context['arm_role'] == expected_phase_role and command[1] == expected_arm and
                pathlib.Path(command[2]).resolve() == expected_index_dir.resolve() and
                command[3:] == [str(workload['files']), str(pathlib.Path(policy_context['dictionary_path']).resolve())] and
                arm_env.get('M4_MAX_DELTA_DOCS') == str(expected_delta_docs) and
                arm_env.get('M4_MAX_TOTAL_RUNS') == str(expected_total_runs) and
                arm_env.get('M4_MAX_RUNS_PER_LEVEL') == str(expected_level_runs) and
                arm_env.get('M4_STATS_LOG_THRESHOLD') == str(expected_stats_threshold) and
                arm_env.get('M4_MUTATION_BATCH_DOCS') == str(expected_mutation_batch_docs) and
                arm_env.get('M4_WAL_REPLAY_BATCH_DOCS') == str(expected_wal_replay_batch_docs) and
                arm_env.get('M4_WAL_REPLAY_PROBE') == expected_wal_replay_probe and
                arm_env.get('M4_WAL_REPLAY_TORN_TAIL') == expected_wal_replay_torn_tail and
                arm_env.get('M4_WAL_REPLAY_MIXED_FRAMES') == expected_wal_replay_mixed_frames and
                arm_env.get('M4_COALESCE_STATS') == expected_coalesce_stats and
                arm_env.get('M4_WAL_COMPRESSION_LZ4') == expected_wal_compression_lz4 and
                arm_env.get('M4_WAL_LZ4_ACCELERATION') == expected_wal_lz4_acceleration and
                arm_env.get('TMPDIR') == str(expected_tmpdir.resolve()) and
                not arm_env.get('M4_DEBT_POLICY') and fresh_workspace and
                binary_path == pathlib.Path(command[0]).resolve() and
                hashlib.sha256(binary_path.read_bytes()).hexdigest() == policy_context['binary_sha256'])
        else:
            command_bound = (
                len(command) == 6 and command[-3:] == ['--incremental-fts-sustained', '500', '20'] and
                pathlib.Path(command[1]).resolve() == workspace_root.resolve() / 'root' and
                not pathlib.Path(command[1]).exists() and
                hashlib.sha256(pathlib.Path(command[0]).read_bytes()).hexdigest() == policy_context['binary_sha256'])
        if (arm_name not in ('aa-first', 'aa-second', 'pair-first', 'pair-second', 'microbench') or
                receipt_dir.resolve() != pathlib.Path(policy_context['operation_root']) / arm_name / 'containment' or
                str(resource_root.resolve()) != policy_context['resource_root'] or
                not enforce_host_gates or budget != contention_budget(policy_context) or
                runtime_seconds != policy_context['runtime_limit_seconds'] or
                not command_bound or
                ((rehearsal or m4_scalar_open or m4_shared_pair or m4_lock_microbench or m4_write_amplification or m4_rust_lifecycle) and cwd.resolve() != workspace_root.resolve()) or
                (env or {}).get('LD_PRELOAD') or any(k.startswith('M4_FAULT') for k in (env or {}))):
            raise ValueError('contained arm contention operation/command/budget binding mismatch')
    if not command:
        raise ValueError('empty command')
    if enforce_host_gates and pressure_limits is None and policy_context is None:
        raise ValueError('host-safe runs require validated 60-second memory/swap pressure limits')
    make_receipt_dir(receipt_dir)
    resource_root = resource_root.resolve(strict=True)
    workspace_root = workspace_root.resolve(strict=True)
    cwd = cwd.resolve(strict=True)
    if (policy_context is not None and
            policy_context.get('purpose') in (M4_LOCK_CONTENTION_MICROBENCH_PURPOSE,
                                               M4_RUST_LIFECYCLE_PURPOSE)):
        quota_contract = (BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota']
                          if policy_context.get('purpose') == M4_LOCK_CONTENTION_MICROBENCH_PURPOSE
                          else BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['workspace_quota'])
        verified_quota = verify_workspace_quota(resource_root, quota_contract)
        proof = policy_context.get('workspace_quota_proof')
        identity_fields = ('status', 'attestation_path', 'workspace_root', 'filesystem_uuid',
                           'subvolume_id', 'qgroup_id', 'max_referenced_bytes',
                           'accounting_mode', 'attestation_sha256')
        if (not isinstance(proof, dict) or
                any(verified_quota.get(key) != proof.get(key) for key in identity_fields)):
            raise ValueError('lock-contention quota identity changed after operation-context validation')
    if not workspace_root.is_dir() or not cwd.is_dir() or not resource_root.is_dir():
        raise ValueError('resource-root, workspace-root, and cwd must be directories')
    if not workspace_root.is_relative_to(resource_root):
        raise ValueError('workspace-root must be inside resource-root')
    if not enforce_host_gates:
        fixture_executable = pathlib.Path(command[0])
        fixture_command = (command[0] == sys.executable and '-c' in command)
        fixture_script = False
        if fixture_executable.is_file() and not fixture_command:
            try:
                fixture_executable = fixture_executable.resolve(strict=True)
                source = fixture_executable.read_text(encoding='utf-8')
                fixture_script = (fixture_executable.is_relative_to(resource_root) and source.startswith('#!')
                                  and 'fixture-marker' in source and 'synthetic-only' in source)
                fixture_command = fixture_script
            except (OSError, UnicodeDecodeError):
                fixture_command = False
        test_mode = os.environ.get('M4_TEST_FIXTURE_MODE') == '1'
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
        workload_args = ' '.join(str(part).lower() for part in command[1:])
        benchmark_args = any(token in workload_args for token in
                             ('--incremental', '--churn', '--reader-search', '500 20', 'benchmark'))
        executable_name = pathlib.Path(command[0]).name.lower()
        benchmark_name = 'm4' in executable_name or 'benchmark' in executable_name
        approved_fixture = (test_mode and resource_root.is_relative_to(temp_root) and fixture_command
                            and (not benchmark_args or fixture_script) and not benchmark_name)
        if not approved_fixture:
            result = {
                'schema_version': SCHEMA_VERSION, 'status': 'PREFLIGHT_BLOCKED',
                'failure_class': 'TEST_FIXTURE_OVERRIDE_REFUSED',
                'operation_outcome': 'OPERATION_BLOCKED', 'failure_scope': 'this arm only',
                'mission_status': 'NOT_EVALUATED_BY_RUNNER', 'benchmark_started': False,
                'safety_outcome': 'NOT_STARTED', 'measurement_quality': 'NOT_APPLICABLE',
                'policy_id': BENCHMARK_POLICY['policy_id'],
                'reason': 'host-guard override is restricted to explicit synthetic fixtures in the OS temp tree',
            }
            write_json(receipt_dir / 'receipt.json', result)
            return result
    if sample_interval_ms < 20 or sample_interval_ms > 10_000:
        raise ValueError('sample interval must be in [20, 10000] ms')
    if runtime_seconds <= 0:
        raise ValueError('runtime limit must be positive')
    for exe in ('systemd-run', 'systemctl'):
        if not shutil.which(exe):
            raise RuntimeError(f'{exe} is unavailable')
    if not (CGROOT / 'cgroup.controllers').exists():
        raise RuntimeError('cgroup v2 not mounted')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('safe process-tree termination requires pidfd_open and pidfd_send_signal')
    user_service = CGROOT / f'user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service'
    controllers_root = read_text(user_service / 'cgroup.controllers') if (user_service / 'cgroup.controllers').exists() else ''
    unit = 'm4-contained-' + uuid.uuid4().hex[:16] + '.service'
    write_json(receipt_dir / 'run-state.json', {
        'timestamp_utc': utc_now(), 'controller_pid': os.getpid(), 'unit': unit, 'state': 'starting',
    })
    diagnostic_decision = None
    try:
        start_state = host_snapshot(resource_root)
        if policy_context is not None:
            diagnostic_decision = contention_decision(start_state, policy_context, arm_name)
    except Exception as exc:
        result = {
            'schema_version': SCHEMA_VERSION, 'status': 'TELEMETRY_INCOMPLETE',
            'failure_class': 'TELEMETRY_FAILURE', 'reason': f'{type(exc).__name__}: {exc}',
            'operation_outcome': 'OPERATION_BLOCKED', 'failure_scope': 'this arm only',
            'mission_status': 'NOT_EVALUATED_BY_RUNNER', 'benchmark_started': False,
            'safety_outcome': 'NOT_STARTED', 'measurement_quality': 'INCOMPLETE',
            'policy_id': BENCHMARK_POLICY['policy_id'], 'unit': None,
        }
        write_json(receipt_dir / 'receipt.json', result)
        return result
    gate_decisions = resource_guard_decisions(start_state)
    for decision in gate_decisions:
        decision['enforced'] = (enforce_host_gates and policy_context is None and
                                decision['gate_id'] == 'M4-RG-DISK')
    if policy_context is not None:
        assert diagnostic_decision is not None  # validated inside the fail-closed snapshot block above
        failures = diagnostic_decision['blocking_failures']
        pressure_failures = []
    else:
        failures = hard_gate_failures(start_state) if enforce_host_gates else []
        pressure_failures = pressure_baseline_failures(start_state, pressure_limits)
    failures.extend(pressure_failures)
    if failures:
        write_json(receipt_dir / 'run-state.json', {
            'timestamp_utc': utc_now(), 'controller_pid': os.getpid(), 'unit': None,
            'state': 'preflight_blocked', 'gate_failures': failures,
        })
        result = {
            'schema_version': SCHEMA_VERSION, 'status': 'PREFLIGHT_BLOCKED',
            'failure_class': failure_class('PREFLIGHT_BLOCKED'),
            'safety_outcome': 'NOT_STARTED', 'measurement_quality': 'NOT_APPLICABLE',
            'timestamp_utc': utc_now(), 'unit': None, 'command': command,
            'policy_id': BENCHMARK_POLICY['policy_id'],
            'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
            'gate_decisions': gate_decisions, 'hard_guards': GUARDS,
            'diagnostic_decision': diagnostic_decision, 'policy_context': policy_context,
            'evidence_class': contention_evidence_class(policy_context) if policy_context is not None else None,
            'preflight': start_state, 'gate_failures': failures,
            'resource_guard_failures': [g for g in gate_decisions if g.get('enforced') and g['status'] == 'FAIL'] if enforce_host_gates else [],
            'operation_outcome': 'OPERATION_BLOCKED', 'failure_scope': 'this arm or benchmark operation only',
            'mission_status': 'NOT_EVALUATED_BY_RUNNER',
            'recovery': 'satisfy the failed guard(s) under the unchanged policy; no automatic retry',
            'benchmark_started': False,
        }
        write_json(receipt_dir / 'receipt.json', result)
        return result
    provenance = {
        'schema_version': SCHEMA_VERSION, 'timestamp_utc': utc_now(),
        'kernel': platform.release(), 'python': sys.version,
        'systemd_run': shutil.which('systemd-run'), 'cgroup2_root_controllers': read_text(CGROOT / 'cgroup.controllers'),
        'user_slice_controllers': controllers_root.split(), 'unit': unit,
        'command': command, 'cwd': str(cwd), 'resource_root': str(resource_root),
        'workspace_root': str(workspace_root), 'runtime_limit_seconds': runtime_seconds,
        'sample_interval_ms': sample_interval_ms, 'hard_guards': GUARDS,
        'policy_id': BENCHMARK_POLICY['policy_id'],
        'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
        'gate_decisions': gate_decisions, 'test_fixture_mode': not enforce_host_gates,
        'policy_context': policy_context, 'diagnostic_decision': diagnostic_decision,
        'evidence_class': contention_evidence_class(policy_context) if policy_context is not None else None,
        'performance_eligible': False if not enforce_host_gates or policy_context is not None else None,
        'failure_scope': 'this arm only', 'mission_status': 'NOT_EVALUATED_BY_RUNNER',
        'approved_budget': budget, 'pressure_limits': pressure_limits,
        'environment_names': sorted((env if env is not None else {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'LD_LIBRARY_PATH'}}).keys()),
        'runner_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    }
    write_json(receipt_dir / 'provenance.json', provenance)
    write_json(receipt_dir / 'host-before.json', start_state)
    socket_dir = pathlib.Path(tempfile.mkdtemp(prefix='m4-cgroup-', dir=f'/run/user/{os.getuid()}'))
    sock_path = socket_dir / 's'
    start_marker = socket_dir / 'go'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    server.listen(1)
    server.settimeout(10)
    bootstrap = [sys.executable, str(pathlib.Path(__file__).resolve()), '__bootstrap', str(sock_path), '--', *command]
    argv = ['systemd-run', '--user', '--pipe', '--wait', '--unit', unit]
    for prop in service_properties(budget, runtime_seconds):
        argv.extend(['--property', prop])
    argv.extend(['--working-directory', str(cwd), '--', *bootstrap])
    stdout = (receipt_dir / 'stdout.log').open('wb')
    stderr = (receipt_dir / 'stderr.log').open('wb')
    watchdog_stdout = None
    watchdog_stderr = None
    proc: subprocess.Popen[bytes] | None = None
    watchdog: subprocess.Popen[bytes] | None = None
    conn: socket.socket | None = None
    stream: Any = None
    child_rc: int | None = None
    cg: pathlib.Path | None = None
    cg_rel: str | None = None
    launched = False
    abort_requested: str | None = None
    controller_cleanup: dict[str, Any] | None = None
    applied: dict[str, Any] = {}
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
        conn, _ = server.accept()
        stream = conn.makefile('rwb', buffering=0)
        hello = json.loads(stream.readline())
        pid = int(hello['pid'])
        cg_rel = str(hello['cgroup']).strip('/')
        cg = cgroup_path_from_unit(unit)
        expected_path = str(cg.relative_to(CGROOT))
        write_json(receipt_dir / 'run-state.json', {
            'timestamp_utc': utc_now(), 'controller_pid': os.getpid(), 'unit': unit,
            'cgroup_relative_path': expected_path, 'state': 'delegated_and_armed',
        })
        if cg_rel != expected_path or not proc_cgroup_contains(pid, expected_path):
            raise RuntimeError(f'bootstrap outside expected cgroup: pid={pid} reported={cg_rel} expected={expected_path}')
        validate_required_cgroup_metrics(cg)
        depth = cg / 'cgroup.max.depth'
        if not os.access(depth, os.W_OK):
            raise RuntimeError('cannot prevent child cgroup creation; refusing workload start')
        if not os.access(cg / 'cgroup.kill', os.W_OK):
            raise RuntimeError('cannot guarantee owned-cgroup cleanup; refusing workload start')
        depth.write_text('0\n', encoding='ascii')
        if (cg / 'memory.oom.group').exists() and os.access(cg / 'memory.oom.group', os.W_OK):
            (cg / 'memory.oom.group').write_text('1\n', encoding='ascii')
        for peak in ('memory.peak', 'memory.swap.peak'):
            file = cg / peak
            if os.access(file, os.W_OK):
                file.write_text('0\n', encoding='ascii')
        baseline_events = parse_kv(read_text(cg / 'memory.events'))
        applied = apply_budget(cg, budget)
        if policy_context is not None:
            verify_contention_caps(cg, policy_context)
            applied['workspace_limits'] = ('monitored soft inventory budgets; exact registered kernel qgroup is an independent hard capacity bound'
                                           if policy_context.get('purpose') in (
                                               M4_LOCK_CONTENTION_MICROBENCH_PURPOSE,
                                               M4_RUST_LIFECYCLE_PURPOSE)
                                           else 'monitored-soft-budgets; warnings only; no kernel/filesystem quota')
        wconfig = {
            'cgroup_path': str(cg), 'cgroup_rel': expected_path,
            'workspace_root': str(workspace_root), 'resource_root': str(resource_root),
            'sample_interval_ms': sample_interval_ms, 'runtime_limit_seconds': runtime_seconds,
            'watchdog_output': str(receipt_dir), 'controller_pid': os.getpid(), 'bootstrap_pid': pid,
            'start_marker': str(start_marker), 'command_done_marker': str(socket_dir / 'command.done'),
            'watchdog_ready_path': str(socket_dir / 'watchdog.ready'), 'socket_dir': str(socket_dir),
            'baseline_memory_events': baseline_events, 'baseline_host_vmstat': start_state['vmstat'], 'budget': budget,
            'pressure_limits': pressure_limits,
            'enforce_host_gates': enforce_host_gates, 'unit': unit,
            'policy_context': policy_context,
            'policy_mode': CONTENTION_MODE if policy_context is not None else 'legacy',
            'policy_context_digest': contention_context_digest(policy_context) if policy_context is not None else None,
            'arm_name': receipt_dir.parent.name,
        }
        config_path = receipt_dir / 'watchdog-config.json'
        write_json(config_path, wconfig)
        watchdog_stdout = (receipt_dir / 'watchdog.stdout').open('wb')
        watchdog_stderr = (receipt_dir / 'watchdog.stderr').open('wb')
        watchdog = subprocess.Popen(
            [sys.executable, str(pathlib.Path(__file__).resolve()), '__watchdog', str(config_path)],
            cwd=cwd, stdin=subprocess.DEVNULL, stdout=watchdog_stdout,
            stderr=watchdog_stderr, start_new_session=True,
        )
        ready_path = socket_dir / 'watchdog.ready'
        ready_deadline = time.monotonic() + 10.0
        while not ready_path.exists() and watchdog.poll() is None and time.monotonic() < ready_deadline:
            time.sleep(0.01)
        if policy_context is not None and ready_path.exists():
            ready = json.loads(read_text(ready_path))
            if ready.get('mode') != CONTENTION_MODE or ready.get('policy_context_digest') != contention_context_digest(policy_context):
                raise RuntimeError('independent watchdog did not acknowledge the exact contention policy context')
        if not ready_path.exists():
            raise RuntimeError('independent watchdog did not establish baseline telemetry before workload start')
        launch_env = env if env is not None else {
            k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'LD_LIBRARY_PATH'}
        }
        start_marker.write_text(str(time.monotonic_ns()), encoding='ascii')
        stream.write((json.dumps({'go': True, 'env': launch_env,
                                  'command_done_marker': str(socket_dir / 'command.done')}) + '\n').encode())
        launched = True
        child_hello_line = stream.readline()
        if not child_hello_line:
            abort_requested = 'contained_workload_terminated_before_identity'
        else:
            child_hello = json.loads(child_hello_line)
            child_pid = int(child_hello['child_pid'])
            child_rel = str(child_hello['child_cgroup']).strip('/')
            if child_hello.get('child_started'):
                write_json(receipt_dir / 'command-start.json', child_hello['child_started'])
            if child_rel != expected_path or not proc_cgroup_contains(child_pid, expected_path):
                raise RuntimeError(f'workload child outside owned cgroup: pid={child_pid} cgroup={child_rel} expected={expected_path}')
            command_result_line = stream.readline()
            if command_result_line:
                command_result = json.loads(command_result_line)
                child_rc = int(command_result['command_returncode'])
                write_json(receipt_dir / 'command-exit.json', {
                    'pid': child_pid, 'returncode': child_rc, 'timestamp_utc': utc_now(),
                    'monotonic_ns': time.monotonic_ns(), 'cgroup_relative_path': child_rel,
                })
            else:
                abort_requested = 'contained_workload_exit_status_missing'
        if watchdog is not None:
            try:
                watchdog.wait(timeout=runtime_seconds + 12.0)
            except subprocess.TimeoutExpired:
                abort_requested = 'watchdog_did_not_finalize'
                if cg is not None and cg_rel is not None:
                    kill_owned_cgroup(cg, cg_rel)
                    subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=3)
                watchdog.terminate()
                watchdog.wait(timeout=3)
        abort_file = receipt_dir / 'watchdog-abort.json'
        if child_rc is not None and not abort_file.exists() and stream is not None:
            stream.write((json.dumps({'finalize': True}) + '\n').encode())
        try:
            rc = proc.wait(timeout=runtime_seconds + 8.0)
        except subprocess.TimeoutExpired:
            abort_requested = abort_requested or 'controller_wait_timeout'
            kill_result = None
            stop_result = None
            cleanup_error = None
            if cg is not None and cg_rel is not None:
                try:
                    kill_result = kill_owned_cgroup(cg, cg_rel)
                    stop_result = subprocess.run(['systemctl', '--user', 'stop', unit],
                                                 capture_output=True, text=True, timeout=3)
                    if cg.exists():
                        remaining = [x for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
                        populated = parse_kv(read_text(cg / 'cgroup.events')).get('populated')
                        verified = not remaining and populated == 0
                    else:
                        load_state = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'LoadState', '--value'],
                                                    capture_output=True, text=True, timeout=3)
                        verified = load_state.returncode == 0 and load_state.stdout.strip() == 'not-found'
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    verified = False
                    cleanup_error = f'{type(exc).__name__}: {exc}'
            else:
                verified = False
            controller_cleanup = {
                'method': 'owned-cgroup-kill + systemctl-stop', 'kill_result': kill_result,
                'systemd_stop_returncode': None if stop_result is None else stop_result.returncode,
                'verified': verified, 'error': cleanup_error,
            }
            rc = proc.wait(timeout=5)
        # Capture systemd's final unit result before removing transient state.
        props = subprocess.run(
            ['systemctl', '--user', 'show', unit, '-p', 'Result', '-p', 'ExecMainCode',
             '-p', 'ExecMainStatus', '-p', 'MemoryPeak', '-p', 'MemorySwapPeak', '-p', 'CPUUsageNSec',
             '-p', 'ControlGroup'], capture_output=True, text=True, timeout=5,
        )
        (receipt_dir / 'systemd-final.txt').write_text(props.stdout + props.stderr, encoding='utf-8')
        watchdog_summary_path = receipt_dir / 'watchdog-summary.json'
        watchdog_summary = json.loads(watchdog_summary_path.read_text()) if watchdog_summary_path.exists() else None
        abort = json.loads(abort_file.read_text()) if abort_file.exists() else None
        if abort and abort.get('reason') == 'operator_requested_stop':
            status = 'WATCHDOG_ABORT'
            reason = 'operator_requested_stop'
        elif abort_requested:
            status = 'WATCHDOG_ABORT'
            reason = abort_requested
        elif abort:
            status = 'WATCHDOG_ABORT'
            reason = abort.get('reason')
        elif child_rc is not None and child_rc < 0:
            status, reason = 'SIGNAL_EXIT', None
        elif child_rc not in (None, 0) or rc != 0:
            status, reason = 'NONZERO_EXIT', None
        elif watchdog_summary is None or not watchdog_summary.get('samples'):
            status, reason = 'TELEMETRY_INCOMPLETE', 'watchdog summary/samples missing'
        elif not isinstance(watchdog_summary.get('final_cgroup'), dict) or watchdog_summary['final_cgroup'].get('error'):
            status, reason = 'TELEMETRY_INCOMPLETE', 'final cgroup counters unavailable'
        elif watchdog_summary.get('abort') is not None:
            status, reason = 'WATCHDOG_ABORT', watchdog_summary['abort'].get('reason')
        else:
            status, reason = 'PASS', None
        measurement_complete = bool(
            watchdog_summary is not None and watchdog_summary.get('samples', 0) > 0
            and isinstance(watchdog_summary.get('final_cgroup'), dict)
            and not watchdog_summary['final_cgroup'].get('error')
            and (receipt_dir / 'runtime.jsonl').exists()
            and (watchdog_summary.get('workspace_samples_complete') is True or
                 workspace_quota_bound_bytes(budget) is not None)
        )
        if policy_context is not None:
            summary = watchdog_summary or {}
            hard_complete = (summary.get('hard_safety_telemetry_complete') is True
                             and summary.get('policy_mode') == CONTENTION_MODE
                             and summary.get('policy_context_digest') == contention_context_digest(policy_context))
            measurement_complete = (measurement_complete and hard_complete and
                                    summary.get('measurement_quality') in {'COMPLETE', 'HARD_QUOTA_BOUND'})
            status, reason = contention_completion_status(
                status, hard_complete, measurement_complete,
                summary.get('workspace_soft_budget_warning_samples', 0), reason,
                summary.get('workspace_samples_complete') is True)
        completed_statuses = {'PASS', 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'COMPLETED_WITH_WORKSPACE_WARNINGS'}
        measurement_quality = ('HARD_QUOTA_BOUND' if measurement_complete and
                               workspace_quota_bound_bytes(budget) is not None else
                               'COMPLETE' if measurement_complete else 'INCOMPLETE')
        if status == 'PREFLIGHT_BLOCKED':
            safety_outcome = 'NOT_STARTED'
        elif status in completed_statuses:
            safety_outcome = 'SAFE_COMPLETE' if status == 'PASS' else 'NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED'
        elif status == 'WATCHDOG_ABORT':
            watchdog_cleanup = (abort or {}).get('final_cleanup', {})
            cleanup_verified = bool(watchdog_cleanup.get('success') or
                                    (controller_cleanup and controller_cleanup.get('verified')))
            safety_outcome = 'ABORTED_CONTAINED' if cleanup_verified else 'CONTAINMENT_UNVERIFIED'
        elif status in {'NONZERO_EXIT', 'SIGNAL_EXIT'}:
            safety_outcome = 'WORKLOAD_FAILED'
        else:
            safety_outcome = 'CONTAINMENT_UNVERIFIED'
        result = {
            'schema_version': SCHEMA_VERSION, 'timestamp_utc': utc_now(), 'unit': unit,
            'status': status, 'reason': reason, 'failure_class': failure_class(status, reason, child_rc),
            'safety_outcome': safety_outcome,
            'measurement_quality': measurement_quality, 'process_returncode': rc,
            'workload_returncode': child_rc,
            'workload_signal': -child_rc if child_rc is not None and child_rc < 0 else None,
            'bootstrap_exit_status': (128 - child_rc if child_rc < 0 else child_rc) if child_rc is not None else None,
            'benchmark_started': launched, 'hard_guards': GUARDS,
            'policy_id': BENCHMARK_POLICY['policy_id'],
            'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
            'gate_decisions': gate_decisions, 'test_fixture_mode': not enforce_host_gates,
            'performance_eligible': bool(status == 'PASS' and enforce_host_gates and policy_context is None),
            'policy_context': policy_context,
            'diagnostic_decision': diagnostic_decision,
            'evidence_class': contention_evidence_class(policy_context) if policy_context is not None else None,
            'operation_outcome': ('OPERATION_COMPLETE_WITH_WORKSPACE_CAVEATS' if status in completed_statuses - {'PASS'} else
                                  'OPERATION_COMPLETE' if status == 'PASS' else
                                  'OPERATION_BLOCKED' if status in {'PREFLIGHT_BLOCKED', 'WATCHDOG_ABORT', 'TELEMETRY_INCOMPLETE'} else
                                  'OPERATION_FAILED'),
            'failure_scope': 'this arm only', 'mission_status': 'NOT_EVALUATED_BY_RUNNER',
            'preflight': start_state, 'cgroup_relative_path': cg_rel,
            'cgroup_io_controller_available': bool(cg and (cg / 'io.stat').exists()),
            'io_control': {
                'status': 'AVAILABLE' if cg and (cg / 'io.stat').exists() else 'UNAVAILABLE',
                'io_limit_applied': bool(applied.get('io_max')),
                'fallback': 'process-tree /proc/<pid>/io plus host /proc/diskstats; not a per-arm physical-write attribution',
            },
            'limits_applied': applied, 'approved_budget': budget,
            'memory_swap_pressure_limits': pressure_limits,
            'watchdog_abort': abort, 'watchdog_summary': watchdog_summary,
            'operator_stop_request': (json.loads((receipt_dir / 'operator-stop-request.json').read_text())
                                     if (receipt_dir / 'operator-stop-request.json').exists() else None),
            'workspace_capacity_status': ('WORKSPACE_CAPACITY_UNVERIFIED'
                                          if policy_context and policy_context.get('purpose') == M4_SCALAR_LATENCY_PURPOSE
                                          else None),
            'controller_cleanup': controller_cleanup,
            'command_start': json.loads((receipt_dir / 'command-start.json').read_text()) if (receipt_dir / 'command-start.json').exists() else None,
            'command_end': json.loads((receipt_dir / 'command-exit.json').read_text()) if (receipt_dir / 'command-exit.json').exists() else None,
            'final_process_status': 'exit=0 and complete hard safety telemetry may complete exploratory operation with workspace caveats; INCOMPLETE is never PASS',
        }
        post_state = host_snapshot(resource_root)
        if policy_context is not None:
            phase = 'runtime-first' if arm_name in ('aa-first', 'pair-first') else 'runtime-second'
            post_workspace = workspace_usage(workspace_root)
            post_quota_sample = workspace_quota_live_sample(budget)
            post_workspace_decision = workspace_capacity_decision(
                post_workspace, budget, CONTENTION_MODE, post_quota_sample)
            post_decision = contention_decision(post_state, policy_context, phase,
                                                workspace_reservation_credit(post_workspace))
            result['postflight_workspace'] = post_workspace
            result['postflight_workspace_quota'] = post_quota_sample
            result['postflight_workspace_capacity'] = post_workspace_decision
            result['postflight_decision'] = post_decision
            if status in completed_statuses:
                if not post_workspace_decision['continuation_allowed']:
                    result.update(status='POSTFLIGHT_BLOCKED', reason=post_workspace_decision['reason'],
                                  operation_outcome='OPERATION_BLOCKED', performance_eligible=False,
                                  measurement_quality='INCOMPLETE', safety_outcome='WORKSPACE_SCOPE_UNVERIFIED')
                elif (not post_workspace['scan_complete'] and
                      workspace_quota_bound_bytes(budget) is None):
                    result.update(status='COMPLETED_WITH_WORKSPACE_UNCERTAINTY', measurement_quality='INCOMPLETE',
                                  reason='postflight workspace accounting INCOMPLETE/UNKNOWN',
                                  operation_outcome='OPERATION_COMPLETE_WITH_WORKSPACE_CAVEATS',
                                  safety_outcome='NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED')
                elif post_workspace_decision['warnings'] and result['status'] == 'PASS':
                    result.update(status='COMPLETED_WITH_WORKSPACE_WARNINGS',
                                  reason='postflight workspace soft budget exceeded',
                                  operation_outcome='OPERATION_COMPLETE_WITH_WORKSPACE_CAVEATS',
                                  safety_outcome='NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED')
            if not post_decision['continuation_allowed']:
                result.update(status='POSTFLIGHT_BLOCKED', reason='postflight hard reserve failure',
                              operation_outcome='OPERATION_BLOCKED', performance_eligible=False,
                              safety_outcome='POSTFLIGHT_CAPACITY_FAILURE')
        write_json(receipt_dir / 'receipt.json', result)
        write_json(receipt_dir / 'host-after.json', post_state)
        retire_unit(unit, cg, receipt_dir, result)
        return result
    except BaseException as exc:
        kill_result = None
        stop_result = None
        cleanup_error = None
        cleanup_verified = cg is None and not launched
        if cg is not None and cg_rel is not None and cg.exists():
            try:
                kill_result = kill_owned_cgroup(cg, cg_rel)
            except Exception as cleanup_exc:
                cleanup_error = f'{type(cleanup_exc).__name__}: {cleanup_exc}'
        try:
            kill_result_unit = subprocess.run(
                ['systemctl', '--user', 'kill', '--kill-whom=all', '--signal=SIGTERM', unit],
                capture_output=True, text=True, timeout=3)
            stop_result = subprocess.run(['systemctl', '--user', 'stop', unit],
                                         capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired) as cleanup_exc:
            cleanup_error = f'{type(cleanup_exc).__name__}: {cleanup_exc}'
            kill_result_unit = None
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if watchdog is not None and watchdog.poll() is None:
            try:
                watchdog.wait(timeout=3)
            except subprocess.TimeoutExpired:
                watchdog.terminate()
                watchdog.wait(timeout=3)
        if cg is not None:
            try:
                if cg.exists():
                    remaining = [x for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
                    populated = parse_kv(read_text(cg / 'cgroup.events')).get('populated')
                    cleanup_verified = not remaining and populated == 0
                else:
                    state = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'LoadState', '--value'],
                                           capture_output=True, text=True, timeout=3)
                    cleanup_verified = state.returncode == 0 and state.stdout.strip() == 'not-found'
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as cleanup_exc:
                cleanup_error = f'{type(cleanup_exc).__name__}: {cleanup_exc}'
                cleanup_verified = False
        failure_status = 'LAUNCH_FAILED' if not launched else 'SUPERVISOR_ERROR'
        safety_outcome = 'NOT_STARTED' if not launched and cleanup_verified else 'CONTAINMENT_UNVERIFIED'
        failure = {
            'schema_version': SCHEMA_VERSION, 'timestamp_utc': utc_now(),
            'status': failure_status, 'failure_class': failure_class(failure_status),
            'safety_outcome': safety_outcome,
            'operation_outcome': 'OPERATION_BLOCKED' if safety_outcome == 'NOT_STARTED' else 'SAFETY_STOP',
            'failure_scope': 'this arm only', 'mission_status': 'NOT_EVALUATED_BY_RUNNER',
            'benchmark_started': launched, 'exception': f'{type(exc).__name__}: {exc}',
            'unit': unit, 'cgroup_relative_path': cg_rel, 'limits_applied': applied,
            'policy_id': BENCHMARK_POLICY['policy_id'],
            'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
            'controller_cleanup': {
                'kill_result': kill_result,
                'systemd_kill_returncode': None if kill_result_unit is None else kill_result_unit.returncode,
                'systemd_stop_returncode': None if stop_result is None else stop_result.returncode,
                'verified': cleanup_verified, 'error': cleanup_error,
            },
        }
        write_json(receipt_dir / 'receipt.json', failure)
        retire_unit(unit, cg, receipt_dir, failure)
        raise
    finally:
        server.close()
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()
        if conn is not None:
            conn.close()
        stdout.close()
        stderr.close()
        if watchdog_stdout is not None:
            watchdog_stdout.close()
        if watchdog_stderr is not None:
            watchdog_stderr.close()
        shutil.rmtree(socket_dir, ignore_errors=True)


def probe() -> dict[str, Any]:
    out: dict[str, Any] = {
        'cgroup_v2_mounted': (CGROOT / 'cgroup.controllers').exists(),
        'cgroup2_mount': next((line for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines() if ' - cgroup2 ' in line), None),
        'self_cgroup': cgroup_relative(), 'root_controllers': [],
        'user_service_controllers': [], 'systemd_run_available': shutil.which('systemd-run') is not None,
        'systemctl_available': shutil.which('systemctl') is not None,
    }
    if out['cgroup_v2_mounted']:
        out['root_controllers'] = read_text(CGROOT / 'cgroup.controllers').split()
    user_service = CGROOT / f'user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service'
    if (user_service / 'cgroup.controllers').exists():
        out['user_service_controllers'] = read_text(user_service / 'cgroup.controllers').split()
        out['user_service_subtree_control'] = read_text(user_service / 'cgroup.subtree_control').split()
    unit = pathlib.Path('/proc/self/cgroup').read_text().split('::', 1)[-1].strip().lstrip('/')
    leaf = CGROOT / unit
    out['current_unit'] = subprocess.run(
        ['systemctl', '--user', 'show', pathlib.Path(unit).name, '-p', 'ControlGroup', '-p', 'Delegate',
         '-p', 'KillMode', '-p', 'IOAccounting'], capture_output=True, text=True, timeout=5,
    ).stdout.strip().splitlines()
    out['current_leaf'] = {
        'controllers': read_text(leaf / 'cgroup.controllers').split() if (leaf / 'cgroup.controllers').exists() else [],
        'io_stat': (leaf / 'io.stat').exists(), 'io_max': (leaf / 'io.max').exists(),
        'io_pressure': (leaf / 'io.pressure').exists(),
        'memory_max_writable': writable_capability(leaf / 'memory.max'),
        'memory_swap_max_writable': writable_capability(leaf / 'memory.swap.max'),
        'cgroup_kill_writable': writable_capability(leaf / 'cgroup.kill'),
    }
    out['host_telemetry'] = {
        'vmstat_swap': pathlib.Path('/proc/vmstat').exists(),
        'diskstats': pathlib.Path('/proc/diskstats').exists(),
        'host_memory_psi': pathlib.Path('/proc/pressure/memory').exists(),
        'host_io_psi': pathlib.Path('/proc/pressure/io').exists(),
    }
    out['systemd_runtime'] = subprocess.run(['systemctl', '--user', '--version'], capture_output=True, text=True, timeout=5).stdout.splitlines()[:1]
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == '__bootstrap':
        try:
            separator = sys.argv.index('--', 2)
        except ValueError:
            return 125
        return _bootstrap(sys.argv[2], sys.argv[separator + 1:])
    if len(sys.argv) > 1 and sys.argv[1] == '__watchdog':
        return watchdog_main(pathlib.Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('probe', help='report actual user cgroup/systemd capabilities (read-only)')
    stop = sub.add_parser('stop', help='request a watchdog-recorded stop for one active operation')
    stop.add_argument('--receipt-dir', type=pathlib.Path, required=True)
    stop.add_argument('--confirm-unit', required=True,
                      help='exact transient systemd unit recorded in the active operation receipt')
    run = sub.add_parser('run', help='run one command in an isolated transient user service')
    run.add_argument('--receipt-dir', type=pathlib.Path, required=True)
    run.add_argument('--resource-root', type=pathlib.Path, required=True)
    run.add_argument('--workspace-root', type=pathlib.Path, required=True)
    run.add_argument('--cwd', type=pathlib.Path, default=pathlib.Path.cwd())
    run.add_argument('--runtime-limit-seconds', type=float, required=True)
    run.add_argument('--sample-interval-ms', type=int, default=100)
    run.add_argument('--approved-budget', type=pathlib.Path)
    run.add_argument('--pressure-limits-json', type=pathlib.Path, required=True,
                     help='validated 60-second baseline limits: swapfree_min_kib, memory_some_avg10_max, memory_full_avg10_max')
    run.add_argument('--pass-env', action='append', default=[], help='forward one named environment variable; values are not stored')
    run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'probe':
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return 0
    if args.action == 'stop':
        request = request_operator_stop(args.receipt_dir, args.confirm_unit)
        print(json.dumps({'status': 'STOP_REQUESTED', 'request': request}, indent=2, sort_keys=True))
        return 0
    command = list(args.command)
    if command and command[0] == '--':
        command = command[1:]
    env_names = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'LD_LIBRARY_PATH'} | set(args.pass_env)
    child_env = {k: v for k, v in os.environ.items() if k in env_names}
    budget = load_approved_budget(args.approved_budget)
    pressure_limits = load_pressure_baseline(args.pressure_limits_json)
    result = run_arm(command, args.receipt_dir, args.resource_root, args.workspace_root,
                     args.cwd, args.runtime_limit_seconds, args.sample_interval_ms, budget, child_env,
                     pressure_limits=pressure_limits)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
