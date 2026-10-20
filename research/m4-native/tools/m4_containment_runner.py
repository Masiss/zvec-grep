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
import csv
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any

CGROOT = pathlib.Path('/sys/fs/cgroup')
POLICY_PATH = pathlib.Path(__file__).resolve().parents[1] / 'docs' / 'benchmark-policy.json'
BENCHMARK_POLICY = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
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
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8').strip()


def parse_kv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                out[fields[0]] = int(fields[1])
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
    load = [float(x) for x in pathlib.Path('/proc/loadavg').read_text().split()[:3]]
    st = os.statvfs(resource_root)
    io_pressure = parse_pressure(pathlib.Path('/proc/pressure/io'))
    mem_pressure = parse_pressure(pathlib.Path('/proc/pressure/memory'))
    if not io_pressure or 'full' not in io_pressure or 'avg10' not in io_pressure['full']:
        raise RuntimeError('missing host /proc/pressure/io full avg10')
    if not mem_pressure or 'full' not in mem_pressure or 'some' not in mem_pressure:
        raise RuntimeError('missing host /proc/pressure/memory some/full')
    if 'MemAvailable_kib' not in mem:
        raise RuntimeError('missing host MemAvailable')
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
    if reason == 'runtime_timeout':
        return 'TIMEOUT_ABORT'
    if status == 'PREFLIGHT_BLOCKED':
        return 'OPERATION_GUARD_REFUSAL'
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
    pressure = state.get('memory_pressure') or {}
    for resource in ('some', 'full'):
        observed = (pressure.get(resource) or {}).get('avg10')
        bound = limits[f'memory_{resource}_avg10_max']
        if observed is None or observed > bound:
            failures.append(f'memory PSI {resource} avg10={observed!r}% > baseline ceiling {bound}%')
    return failures


def load_pressure_baseline(path: pathlib.Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    required = ('swapfree_min_kib', 'memory_some_avg10_max', 'memory_full_avg10_max')
    for key in required:
        if (key not in value or isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                or not math.isfinite(value[key]) or value[key] < 0):
            raise ValueError(f'pressure baseline requires non-negative finite numeric {key}')
    if not isinstance(value['swapfree_min_kib'], int):
        raise ValueError('swapfree_min_kib must be an integer')
    return {key: value[key] for key in required}


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
        passed = value is not None and (value >= threshold if operator == '>=' else value <= threshold)
        decisions.append({
            'gate_id': gate_id, 'threshold_key': key, 'metric': spec['metric'], 'unit': spec['unit'],
            'operator': operator, 'observed': value, 'threshold': threshold,
            'status': 'PASS' if passed else 'FAIL',
            'enforcement': spec['enforcement'], 'evidence_class': spec['evidence_class'],
            'source_of_authority': BENCHMARK_POLICY['authority']['historical_envelope_source'],
            'failure_scope': spec['failure_scope'], 'recovery': spec['recovery'],
            'owner_approval_required_to_change': spec['owner_approval_required_to_change'],
        })
    return decisions


CONTENTION_MODE = 'contention-aware'
CONTENTION_EVIDENCE = 'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING'


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
    if not re.fullmatch(r'm4-contention-[a-zA-Z0-9_-]+', operation):
        raise ValueError('invalid contention operation_id')
    for key in ('operation_root', 'resource_root'):
        if str(pathlib.Path(context[key]).resolve()) != context[key]:
            raise ValueError(f'{key} must be a canonical absolute path')
    if pathlib.Path(context['operation_root']).name != operation:
        raise ValueError('operation root/id mismatch')
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


def contention_context_digest(context: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def contention_budget(context: dict[str, Any]) -> dict[str, Any]:
    validate_contention_context(context)
    return {key: context[key] for key in ('approval_reference', 'memory_max_bytes',
            'memory_swap_max_bytes', 'cpu_max', 'pids_max', 'workspace_max_allocated_bytes',
            'workspace_max_apparent_bytes')}


def contention_decision(state: dict[str, Any], context: dict[str, Any], phase: str,
                        workspace_bytes: int = 0, owned_swap_bytes: int = 0) -> dict[str, Any]:
    """Retain raw envelope FAILs; enforce capacity based on the approved workload plan."""
    validate_contention_context(context)
    if phase not in ('baseline', 'recovery', 'aa-first', 'aa-second', 'runtime-first', 'runtime-second'):
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
    for name in ('MemAvailable_kib', 'SwapFree_kib', 'MemTotal_kib', 'SwapTotal_kib'):
        number(mem.get(name), name)
    if mem['MemAvailable_kib'] > mem['MemTotal_kib'] or mem['SwapFree_kib'] > mem['SwapTotal_kib']:
        raise ValueError('inconsistent host memory/swap telemetry')
    number(state.get('resource_root_free_bytes'), 'resource_root_free_bytes')
    load = state.get('loadavg') or []
    number(load[0] if load else None, 'load1')
    for family in ('io_pressure', 'memory_pressure', 'cpu_pressure'):
        pressure = state.get(family) or {}
        # CPU full is not available on every kernel; CPU some is required.
        for kind in (('some',) if family == 'cpu_pressure' else ('some', 'full')):
            item = pressure.get(kind) or {}
            number(item.get('avg10'), f'{family}.{kind}.avg10', 100)
            number(item.get('total'), f'{family}.{kind}.total')
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
    runtime = phase.startswith('runtime-')
    memory_required = context['host_memory_reserve_bytes'] + (0 if runtime else context['memory_max_bytes'])
    # Retain first-arm roots; reserve space for both arms before the first, one before second.
    arms = 2 if phase in ('baseline', 'aa-first', 'runtime-first') else 1
    disk_required = context['disk_reserve_bytes'] + max(
        0, arms * context['workspace_max_allocated_bytes'] - workspace_bytes)
    failures = []
    if mem['MemAvailable_kib'] * 1024 < memory_required:
        failures.append({'gate_id': 'M4-CA-MEM-CAPACITY', 'observed_bytes': mem['MemAvailable_kib'] * 1024,
                         'required_bytes': memory_required, 'attribution': 'host capacity; cause not established'})
    swap_required = max(0, context['memory_swap_max_bytes'] - owned_swap_bytes)
    if mem['SwapFree_kib'] * 1024 < swap_required:
        failures.append({'gate_id': 'M4-CA-SWAP-CAPACITY', 'observed_bytes': mem['SwapFree_kib'] * 1024,
                         'required_bytes': swap_required, 'attribution': 'remaining approved swap capacity; cause not established'})
    if state['resource_root_free_bytes'] < disk_required:
        failures.append({'gate_id': 'M4-CA-DISK-CAPACITY', 'observed_bytes': state['resource_root_free_bytes'],
                         'required_bytes': disk_required, 'attribution': 'shared filesystem capacity; cause not established'})
    raw = resource_guard_decisions(state)
    for gate in raw:
        gate['enforced_in_selected_mode'] = False
    return {'mode': CONTENTION_MODE, 'operation_id': context['operation_id'], 'phase': phase,
            'timestamp_utc': state.get('timestamp_utc'), 'monotonic_ns': state.get('monotonic_ns'),
            'historical_gate_decisions': raw, 'historical_guard_failures': [g for g in raw if g['status'] == 'FAIL'],
            'blocking_failures': failures, 'continuation_allowed': not failures,
            'continuation_reason': 'approved workload capacity and containment plan, not an idle-envelope waiver or safety certification',
            'evidence_class': CONTENTION_EVIDENCE, 'approval_reference': context['approval_reference']}


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
    failures: list[str] = []
    available = state.get('meminfo_kib', {}).get('MemAvailable_kib')
    if available is None or available < GUARDS['memavailable_min_kib']:
        failures.append(f"MemAvailable={available!r} KiB < {GUARDS['memavailable_min_kib']} KiB")
    free = state.get('resource_root_free_bytes')
    if free is None or free < GUARDS['var_tmp_free_min_bytes']:
        failures.append(f"resource_root_free={free!r} B < {GUARDS['var_tmp_free_min_bytes']} B")
    try:
        io_full = state['io_pressure']['full']['avg10']
    except (KeyError, TypeError):
        io_full = None
    if io_full is None or io_full > GUARDS['io_full_avg10_max']:
        failures.append(f"host io full avg10={io_full!r}% > {GUARDS['io_full_avg10_max']}%")
    load1 = (state.get('loadavg') or [None])[0]
    if load1 is None or load1 > GUARDS['load1_max']:
        failures.append(f"load-1={load1!r} > {GUARDS['load1_max']}")
    return failures


def hard_gate_failures(state: dict[str, Any]) -> list[str]:
    return [f"{gate['metric']}={gate['observed']!r} {gate['operator']} {gate['threshold']} {gate['unit']} ({gate['gate_id']})"
            for gate in resource_guard_decisions(state) if gate['status'] == 'FAIL']


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
    pids = [int(x) for x in read_text(cg / 'cgroup.procs').splitlines() if x.strip()]
    proc_io_read = 0
    proc_io_write = 0
    proc_io_complete = True
    proc_pss = proc_rss = proc_swap = 0
    proc_memory_complete = True
    expected_rel = str(cg.relative_to(CGROOT))
    for pid in pids:
        try:
            if not proc_cgroup_contains(pid, expected_rel):
                proc_io_complete = proc_memory_complete = False
                continue
            values = parse_kv(pathlib.Path(f'/proc/{pid}/io').read_text())
            if 'read_bytes' not in values or 'write_bytes' not in values:
                proc_io_complete = False
            proc_io_read += values.get('read_bytes', 0)
            proc_io_write += values.get('write_bytes', 0)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            # A process may exit between cgroup.procs and /proc reads.
            proc_io_complete = False
        try:
            smaps = pathlib.Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines()
            memory = {line.split(':', 1)[0]: int(line.split()[1])
                      for line in smaps if ':' in line and len(line.split()) >= 2
                      and line.split()[1].isdigit()}
            proc_pss += memory['Pss']
            proc_rss += memory['Rss']
            proc_swap += memory.get('Swap', 0)
        except (FileNotFoundError, ProcessLookupError, PermissionError, KeyError, ValueError):
            proc_memory_complete = False
    events = parse_kv(read_text(cg / 'memory.events'))
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
    start = time.monotonic_ns()
    apparent = allocated = generation_apparent = staging_apparent = 0
    files = directories = symlinks = 0
    seen: set[tuple[int, int]] = set()
    generations: set[str] = set()
    errors: list[str] = []
    try:
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = pathlib.Path(current)
            dirnames[:] = [d for d in dirnames if not (current_path / d).is_symlink()]
            for name in dirnames:
                p = current_path / name
                try:
                    s = p.stat(follow_symlinks=False)
                    directories += 1
                    key = (s.st_dev, s.st_ino)
                    if key not in seen:
                        seen.add(key)
                        allocated += getattr(s, 'st_blocks', 0) * 512
                    for part in p.relative_to(root).parts:
                        if part.startswith('generation-'):
                            generations.add(part)
                except OSError as exc:
                    errors.append(f'{p}: {exc}')
            for name in filenames:
                p = current_path / name
                try:
                    s = p.stat(follow_symlinks=False)
                    if pathlib.Path(p).is_symlink():
                        symlinks += 1
                        continue
                    files += 1
                    rel = p.relative_to(root)
                    key = (s.st_dev, s.st_ino)
                    if key not in seen:
                        seen.add(key)
                        allocated += getattr(s, 'st_blocks', 0) * 512
                    apparent += s.st_size
                    if any(part.startswith('generation-') for part in rel.parts):
                        generation_apparent += s.st_size
                    low = str(rel).lower()
                    if any(token in low for token in ('.tmp.', '.tmp', 'staging', '/stage', 'compact', 'temporary')):
                        staging_apparent += s.st_size
                except OSError as exc:
                    errors.append(f'{p}: {exc}')
    except OSError as exc:
        errors.append(f'{root}: {exc}')
    return {
        'root': str(root), 'apparent_bytes': apparent,
        'allocated_st_blocks_bytes_gross': allocated,
        'allocated_semantics': 'sum st_blocks*512, deduplicated hardlinks; Btrfs shared extents may be counted more than once',
        'file_count': files, 'directory_count': directories, 'symlink_count': symlinks,
        'generation_directory_names': sorted(generations),
        'generation_apparent_bytes': generation_apparent,
        'staging_named_apparent_bytes': staging_apparent,
        'scan_duration_ns': time.monotonic_ns() - start,
        'scan_complete': not errors, 'scan_errors': errors,
    }


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
    budget = json.loads(path.read_text(encoding='utf-8'))
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
        if not file.exists() or not os.access(file, os.W_OK):
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
        if not targets:
            if preserve or not pids:
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
        payload = json.loads(line)
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
        if not final_line or json.loads(final_line).get('finalize') is not True:
            return 125
        # subprocess uses -signal; sys.exit(-signal) would wrap modulo 256.
        # Keep the signed child result in the protocol/receipt, but expose the
        # conventional 128+signal failure status to systemd for the bootstrap.
        return 128 - child_rc if child_rc < 0 else child_rc


def cgroup_populated(cg: pathlib.Path) -> int:
    return parse_kv(read_text(cg / 'cgroup.events')).get('populated', -1)


def read_watchdog_config(path: pathlib.Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding='utf-8'))
    for key in ('cgroup_path', 'cgroup_rel', 'workspace_root', 'resource_root', 'sample_interval_ms',
                'runtime_limit_seconds', 'watchdog_output', 'controller_pid', 'bootstrap_pid', 'start_marker',
                'command_done_marker', 'watchdog_ready_path', 'socket_dir', 'pressure_limits'):
        if key not in config:
            raise ValueError(f'watchdog config missing {key}')
    context = config.get('policy_context')
    if (config.get('policy_mode') == CONTENTION_MODE) != (context is not None):
        raise ValueError('watchdog policy mode/context mismatch')
    if context is not None:
        validate_contention_context(context)
        arm = config.get('arm_name')
        expected = pathlib.Path(context['operation_root']) / str(arm) / 'containment'
        if (arm not in ('aa-first', 'aa-second') or
                pathlib.Path(config['watchdog_output']).resolve() != expected or
                str(pathlib.Path(config['resource_root']).resolve()) != context['resource_root'] or
                not config.get('enforce_host_gates', True) or
                config.get('budget') != contention_budget(context) or
                config['runtime_limit_seconds'] != context['runtime_limit_seconds'] or
                config.get('policy_context_digest') != contention_context_digest(context)):
            raise ValueError('watchdog contention context/budget/operation binding mismatch')
    return config


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
        import select
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
    start_marker = pathlib.Path(config['start_marker'])
    command_done_marker = pathlib.Path(config['command_done_marker'])
    ready_marker = pathlib.Path(config['watchdog_ready_path'])
    socket_dir = pathlib.Path(config['socket_dir'])
    while abort is None:
        if poller is not None and poller.poll(0):
            if cg.exists() and cgroup_populated(cg) == 1:
                abort = {'reason': 'supervisor_lost', 'timestamp_utc': utc_now()}
                break
        if started_ns is None and start_marker.exists():
            started_ns = time.monotonic_ns()
        try:
            sample_started_ns = time.monotonic_ns()
            if not cg.exists():
                abort = {'reason': 'cgroup_telemetry_disappeared', 'timestamp_utc': utc_now()}
                break
            cgroup_row = read_cgroup_metric(cg)
            host_row = host_snapshot(resource_root)
            work_row = workspace_usage(workspace)
            row = {
                'timestamp_utc': utc_now(), 'monotonic_ns': time.monotonic_ns(),
                'elapsed_ns': None if started_ns is None else time.monotonic_ns() - started_ns,
                'cgroup': cgroup_row, 'host': host_row, 'workspace': work_row,
            }
            if config.get('policy_context') is not None:
                row['sample_start_monotonic_ns'] = sample_started_ns
                row['sample_duration_ns'] = row['monotonic_ns'] - sample_started_ns
                if row['sample_duration_ns'] > config['policy_context']['max_monitor_gap_seconds']*1e9:
                    raise ValueError('contention sample read exceeds approved monitoring gap')
            samples.append(row)
            context = config.get('policy_context')
            context_failures = []
            if context is not None:
                verify_contention_caps(cg, context)
                phase = ('runtime-first' if config['arm_name'] == 'aa-first' else 'runtime-second') if started_ns is not None else config['arm_name']
                decision = contention_decision(host_row, context, phase, work_row['allocated_st_blocks_bytes_gross'],
                                                cgroup_row['memory_swap_current_bytes'])
                row['diagnostic_decision'] = decision
                previous = samples[-2] if len(samples) > 1 else None
                distress = contention_runtime_failures(cgroup_row, None if previous is None else previous['cgroup'])
                if previous is not None:
                    elapsed = (row['monotonic_ns'] - previous['monotonic_ns']) / 1e9
                    if elapsed <= 0 or elapsed > context['max_monitor_gap_seconds']:
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
            if not ready_marker.exists() and abort is None:
                if context is not None:
                    write_json(ready_marker, {'mode': CONTENTION_MODE, 'policy_context_digest': contention_context_digest(context)})
                else:
                    ready_marker.write_text(str(time.monotonic_ns()), encoding='ascii')
            if not work_row['scan_complete']:
                abort = {'reason': 'workspace_telemetry_incomplete', 'errors': work_row['scan_errors'], 'timestamp_utc': utc_now()}
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
                elif config.get('budget'):
                    b = config['budget']
                    if b.get('workspace_max_allocated_bytes') is not None and work_row['allocated_st_blocks_bytes_gross'] > b['workspace_max_allocated_bytes']:
                        abort = {'reason': 'workspace_allocated_watchdog', 'observed_bytes': work_row['allocated_st_blocks_bytes_gross'], 'limit_bytes': b['workspace_max_allocated_bytes'], 'timestamp_utc': utc_now()}
                    if b.get('workspace_max_apparent_bytes') is not None and work_row['apparent_bytes'] > b['workspace_max_apparent_bytes']:
                        abort = {'reason': 'workspace_apparent_watchdog', 'observed_bytes': work_row['apparent_bytes'], 'limit_bytes': b['workspace_max_apparent_bytes'], 'timestamp_utc': utc_now()}
                if abort is None and time.monotonic_ns() - started_ns >= int(runtime_limit * 1e9):
                    abort = {'reason': 'runtime_timeout', 'limit_seconds': runtime_limit, 'timestamp_utc': utc_now()}
            baseline_events = config.get('baseline_memory_events', {})
            for k in ('oom', 'oom_kill', 'oom_group_kill'):
                if cgroup_row['memory_events'].get(k, 0) > baseline_events.get(k, 0):
                    abort = {'reason': 'cgroup_oom_event', 'event': k, 'observed': cgroup_row['memory_events'].get(k), 'timestamp_utc': utc_now()}
                    break
            if cgroup_row.get('populated') == 0 or command_done_marker.exists():
                break
        except Exception as exc:
            abort = {'reason': 'required_telemetry_missing', 'detail': f'{type(exc).__name__}: {exc}', 'timestamp_utc': utc_now()}
        if abort is not None:
            break
        time.sleep(interval)
    kill_result = None
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
    fiemap = inspect_shared_extents([workspace]) if workspace.exists() else {'available': False, 'reason': 'workspace absent'}
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
    summary = {
        'schema_version': SCHEMA_VERSION, 'samples': len(samples),
        'policy_mode': config.get('policy_mode', 'legacy'),
        'policy_context_digest': config.get('policy_context_digest'),
        'measurement_quality': 'COMPLETE' if samples and process_samples_complete and isinstance(final_cgroup, dict) and not final_cgroup.get('error') else 'INCOMPLETE',
        'process_tree_samples_complete': process_samples_complete,
        'safety_outcome': 'ABORTED_CONTAINED' if abort is not None else 'WATCHDOG_COMPLETED_NO_ABORT',
        'sample_interval_ms_requested': config['sample_interval_ms'],
        'sampling_scope': 'external watchdog; workspace scans are sampled and may miss short-lived files',
        'abort': abort, 'final_cgroup': final_cgroup,
        'workspace_shared_extent_inspection': fiemap,
        'peak_sampled_workspace_apparent_bytes': max((r['workspace']['apparent_bytes'] for r in samples), default=None),
        'peak_sampled_workspace_allocated_st_blocks_bytes_gross': max((r['workspace']['allocated_st_blocks_bytes_gross'] for r in samples), default=None),
        'peak_sampled_staging_named_apparent_bytes': max((r['workspace']['staging_named_apparent_bytes'] for r in samples), default=None),
        'peak_sampled_generation_apparent_bytes': max((r['workspace']['generation_apparent_bytes'] for r in samples), default=None),
        'peak_cgroup_memory_current_bytes_sampled': max((r['cgroup']['memory_current_bytes'] for r in samples), default=None),
        'peak_cgroup_memory_peak_bytes_observed': max((r['cgroup']['memory_peak_bytes'] for r in samples), default=None),
        'peak_cgroup_swap_current_bytes_sampled': max((r['cgroup']['memory_swap_current_bytes'] for r in samples), default=None),
        'peak_cgroup_swap_peak_bytes_observed': max((r['cgroup']['memory_swap_peak_bytes'] for r in samples), default=None),
        'cgroup_io_stat_available': any(r['cgroup']['io_stat'] is not None for r in samples),
        'final_cgroup_sampling': 'captured while transient unit remains active after ExecStart; systemd unit is stopped after this receipt is durable',
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
            shutil.rmtree(socket_dir, ignore_errors=True)
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
                props.append(f'CPUQuota={100.0 * int(quota) / int(period):.3f}%')
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


def run_arm(command: list[str], receipt_dir: pathlib.Path, resource_root: pathlib.Path,
            workspace_root: pathlib.Path, cwd: pathlib.Path, runtime_seconds: float,
            sample_interval_ms: int = 100, budget: dict[str, Any] | None = None,
            env: dict[str, str] | None = None, enforce_host_gates: bool = True,
            pressure_limits: dict[str, Any] | None = None,
            policy_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if policy_context is not None:
        validate_contention_context(policy_context)
        arm_name = receipt_dir.parent.name
        if (arm_name not in ('aa-first', 'aa-second') or
                receipt_dir.resolve() != pathlib.Path(policy_context['operation_root']) / arm_name / 'containment' or
                str(resource_root.resolve()) != policy_context['resource_root'] or
                not enforce_host_gates or budget != contention_budget(policy_context) or
                runtime_seconds != policy_context['runtime_limit_seconds'] or
                len(command) != 6 or command[-3:] != ['--incremental-fts-sustained', '500', '20'] or
                pathlib.Path(command[1]).resolve() != workspace_root.resolve() / 'root' or
                pathlib.Path(command[1]).exists() or
                hashlib.sha256(pathlib.Path(command[0]).read_bytes()).hexdigest() != policy_context['binary_sha256'] or
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
        decision['enforced'] = enforce_host_gates and policy_context is None
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
            'evidence_class': CONTENTION_EVIDENCE if policy_context is not None else None,
            'preflight': start_state, 'gate_failures': failures,
            'resource_guard_failures': [g for g in gate_decisions if g['status'] == 'FAIL'] if enforce_host_gates else [],
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
        'evidence_class': CONTENTION_EVIDENCE if policy_context is not None else None,
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
        wconfig = {
            'cgroup_path': str(cg), 'cgroup_rel': expected_path,
            'workspace_root': str(workspace_root), 'resource_root': str(resource_root),
            'sample_interval_ms': sample_interval_ms, 'runtime_limit_seconds': runtime_seconds,
            'watchdog_output': str(receipt_dir), 'controller_pid': os.getpid(), 'bootstrap_pid': pid,
            'start_marker': str(start_marker), 'command_done_marker': str(socket_dir / 'command.done'),
            'watchdog_ready_path': str(socket_dir / 'watchdog.ready'), 'socket_dir': str(socket_dir),
            'baseline_memory_events': baseline_events, 'budget': budget,
            'pressure_limits': pressure_limits,
            'enforce_host_gates': enforce_host_gates, 'pressure_limits': pressure_limits, 'unit': unit,
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
        if abort_requested:
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
        )
        if policy_context is not None:
            measurement_complete = (measurement_complete and watchdog_summary.get('measurement_quality') == 'COMPLETE'
                                    and watchdog_summary.get('policy_mode') == CONTENTION_MODE
                                    and watchdog_summary.get('policy_context_digest') == contention_context_digest(policy_context))
            if status == 'PASS' and not measurement_complete:
                status, reason = 'TELEMETRY_INCOMPLETE', 'incomplete required contention process telemetry'
        measurement_quality = 'COMPLETE' if measurement_complete else 'INCOMPLETE'
        if status == 'PREFLIGHT_BLOCKED':
            safety_outcome = 'NOT_STARTED'
        elif status == 'PASS':
            safety_outcome = 'SAFE_COMPLETE'
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
            'evidence_class': CONTENTION_EVIDENCE if policy_context is not None else None,
            'operation_outcome': ('OPERATION_COMPLETE' if status == 'PASS' else
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
            'controller_cleanup': controller_cleanup,
            'command_start': json.loads((receipt_dir / 'command-start.json').read_text()) if (receipt_dir / 'command-start.json').exists() else None,
            'command_end': json.loads((receipt_dir / 'command-exit.json').read_text()) if (receipt_dir / 'command-exit.json').exists() else None,
            'final_process_status': 'PASS only when exit=0, no watchdog/OOM abort, and telemetry is complete',
        }
        post_state = host_snapshot(resource_root)
        if policy_context is not None:
            phase = 'runtime-first' if arm_name == 'aa-first' else 'runtime-second'
            post_decision = contention_decision(post_state, policy_context, phase,
                                                workspace_usage(workspace_root)['allocated_st_blocks_bytes_gross'])
            result['postflight_decision'] = post_decision
            if not post_decision['continuation_allowed']:
                result.update(status='POSTFLIGHT_BLOCKED', operation_outcome='OPERATION_BLOCKED', performance_eligible=False)
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
            try:
                stream.close()
            except OSError:
                pass
        if conn is not None:
            conn.close()
        stdout.close(); stderr.close()
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
        'memory_max_writable': os.access(leaf / 'memory.max', os.W_OK) if (leaf / 'memory.max').exists() else False,
        'memory_swap_max_writable': os.access(leaf / 'memory.swap.max', os.W_OK) if (leaf / 'memory.swap.max').exists() else False,
        'cgroup_kill_writable': os.access(leaf / 'cgroup.kill', os.W_OK) if (leaf / 'cgroup.kill').exists() else False,
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
