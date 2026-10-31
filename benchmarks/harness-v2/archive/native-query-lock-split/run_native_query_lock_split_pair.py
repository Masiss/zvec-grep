#!/usr/bin/env python3
"""Run the policy-bound multi-pair native lock-split diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys
from collections.abc import Sequence

def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

ROOT = _repo_root()
HERE = pathlib.Path(__file__).resolve().parent
WORK_ROOT = (ROOT / 'research/m4-native/.work').resolve()
POLICY_PATH = ROOT / 'research/m4-native/docs/benchmark-policy.json'
CONTAINMENT = HERE / 'm4_containment_runner.py'
WORKER = HERE / 'run_native_query_lock_split_bench.py'


def require_work_path(path: pathlib.Path, label: str) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(WORK_ROOT):
        raise ValueError(f'{label} must resolve beneath {WORK_ROOT}')
    return resolved


def read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'cannot read JSON {path}: {error}') from error


def host_snapshot() -> dict:
    meminfo = {}
    for line in pathlib.Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
        key, _, rest = line.partition(':')
        value = rest.strip().split()
        if value and value[0].isdigit():
            meminfo[key] = int(value[0])
    pressure = {}
    for name in ('memory', 'io'):
        values = {}
        for line in pathlib.Path(f'/proc/pressure/{name}').read_text(encoding='utf-8').splitlines():
            fields = dict(part.split('=', 1) for part in line.split()[1:])
            values[line.split()[0]] = fields
        pressure[name] = values
    load = pathlib.Path('/proc/loadavg').read_text(encoding='utf-8').split()[:3]
    usage = subprocess.run(['df', '-B1', '--output=avail', str(ROOT)],
                           check=True, text=True, capture_output=True).stdout.splitlines()
    return {
        'MemAvailable_kib': meminfo.get('MemAvailable'),
        'SwapFree_kib': meminfo.get('SwapFree'),
        'load1': float(load[0]),
        'pressure': pressure,
        'resource_root_avail_bytes': int(usage[1]) if len(usage) > 1 else None,
    }


QUERY_RE = re.compile(
    r'bench_query phase=(\S+) cycle=(\d+) reader=(\d+) us=(-?\d+) '
    r'overlap=(\d+) valid=(\d+)')
CYCLE_RE = re.compile(
    r'bench_cycle phase=(\S+) cycle=(\d+) maintenance_us=(\d+) '
    r'readers=(\d+) overlap=(\d+) operation_ok=(\d+)')


def parse_bench(stdout: str) -> dict:
    samples: dict[str, dict[int, list[int]]] = {}
    cycles: dict[str, list[dict]] = {}
    for line in stdout.splitlines():
        match = QUERY_RE.match(line)
        if match:
            phase = match.group(1)
            cycle = int(match.group(2))
            samples.setdefault(phase, {}).setdefault(cycle, []).append(
                int(match.group(4)))
            continue
        match = CYCLE_RE.match(line)
        if match:
            cycles.setdefault(match.group(1), []).append({
                'cycle': int(match.group(2)),
                'maintenance_us': int(match.group(3)),
                'readers': int(match.group(4)),
                'overlap': int(match.group(5)),
                'operation_ok': int(match.group(6)),
            })
    return {'samples': samples, 'cycles': cycles}


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[index]


def phase_stats(samples: dict[int, list[int]]) -> dict:
    all_values = [value for values in samples.values() for value in values]
    per_cycle = {
        cycle: statistics.median(values)
        for cycle, values in samples.items() if values
    }
    return {
        'count': len(all_values),
        'cycles': len(per_cycle),
        'p50': percentile(all_values, 0.50),
        'p95': percentile(all_values, 0.95),
        'max': max(all_values) if all_values else None,
        'per_cycle_median': per_cycle,
    }


def bootstrap_ci(diffs: list[float], iterations: int = 10000,
                 seed: int = 20261030) -> dict:
    if not diffs:
        return {'n': 0, 'mean': None, 'ci_low': None, 'ci_high': None}
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iterations):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    return {
        'n': n,
        'mean': statistics.fmean(diffs),
        'median': statistics.median(diffs),
        'ci_low': means[int(0.025 * iterations)],
        'ci_high': means[int(0.975 * iterations)],
    }


def paired_effect(control: dict, treatment: dict) -> dict:
    control_cycles = control.get('per_cycle_median', {})
    treatment_cycles = treatment.get('per_cycle_median', {})
    shared = sorted(set(control_cycles) & set(treatment_cycles))
    diffs = [treatment_cycles[c] - control_cycles[c] for c in shared]
    return {'cycles': shared, 'diffs': diffs, **bootstrap_ci(diffs)}


def run_arm(arm: str, arm_root: pathlib.Path, library_dir: pathlib.Path,
            policy: dict, resource_root: pathlib.Path,
            workspace_root: pathlib.Path, budget_path: pathlib.Path,
            pressure_path: pathlib.Path) -> dict:
    workload = policy['workload']
    collection_path = arm_root / 'collection'
    containment_dir = arm_root / 'containment'
    command = [
        sys.executable, '-I', str(CONTAINMENT), 'run',
        '--receipt-dir', str(containment_dir),
        '--resource-root', str(resource_root),
        '--workspace-root', str(workspace_root),
        '--cwd', str(ROOT),
        '--runtime-limit-seconds',
        str(policy['resource_controls']['runtime_limit_seconds']),
        '--approved-budget', str(budget_path),
        '--pressure-limits-json', str(pressure_path),
        '--', sys.executable, str(WORKER),
        '--library-dir', str(library_dir),
        '--collection-path', str(collection_path),
        '--documents', str(workload['documents']),
        '--cycles', str(workload['cycles']),
        '--readers', str(workload['readers']),
        '--batch', str(workload['batch_docs']),
        '--tokens', str(workload['tokens_per_document']),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, cwd=ROOT)
    (arm_root / 'containment-invocation.json').write_text(json.dumps({
        'command': command,
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
    }, indent=2) + '\n', encoding='utf-8')
    if completed.returncode != 0:
        return {
            'arm': arm,
            'status': 'OPERATION_BLOCKED',
            'reason': completed.stderr.strip() or completed.stdout.strip(),
        }
    try:
        containment = json.loads(completed.stdout)
    except json.JSONDecodeError:
        containment = {'raw_stdout': completed.stdout}
    stdout_log = containment_dir / 'stdout.log'
    child_stdout = stdout_log.read_text(encoding='utf-8') if stdout_log.is_file() else ''
    (arm_root / 'raw-stdout.log').write_text(child_stdout, encoding='utf-8')
    parsed = parse_bench(child_stdout)
    library = library_dir / 'libzvec_c_api.so'
    phase = {
        name: phase_stats(parsed['samples'].get(name, {}))
        for name in ('idle', 'flush', 'optimize')
    }
    return {
        'arm': arm,
        'status': 'PASS',
        'library': str(library),
        'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
        'containment': containment,
        'phase': phase,
        'cycle_summary': parsed['cycles'],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--control-library-dir', required=True, type=pathlib.Path)
    parser.add_argument('--treatment-library-dir', required=True, type=pathlib.Path)
    parser.add_argument('--receipt-root', required=True, type=pathlib.Path)
    parser.add_argument('--pairs', type=int, default=3)
    args = parser.parse_args()

    policy = read_json(POLICY_PATH)['native_query_lock_split_pair']
    workload = policy['workload']
    if args.pairs != policy['max_pairs']:
        raise SystemExit('pair count does not match the registered policy contract')
    control_dir = require_work_path(args.control_library_dir, 'control library directory')
    treatment_dir = require_work_path(args.treatment_library_dir, 'treatment library directory')
    receipt_root = args.receipt_root.resolve()
    if not receipt_root.is_relative_to(WORK_ROOT):
        raise SystemExit('receipt root must remain beneath .work')
    if receipt_root.exists() and any(receipt_root.iterdir()):
        raise SystemExit('receipt root must be fresh')

    resource_root = pathlib.Path(policy['resource_root']).resolve(strict=True)
    workspace_root = resource_root / (receipt_root.name + '-workspace')
    workspace_root.mkdir(parents=True, exist_ok=False)
    receipt_root.mkdir(parents=True, exist_ok=True)

    budget = {
        'schema_version': 1,
        'approval_status': 'approved',
        'approval_reference': policy['approval_reference'],
        **policy['resource_controls'],
    }
    budget_path = receipt_root / 'approved-budget.json'
    budget_path.write_text(json.dumps(budget, indent=2) + '\n', encoding='utf-8')
    snapshot = host_snapshot()
    pressure = {
        'swapfree_min_kib': snapshot['SwapFree_kib'],
        'memory_some_avg10_max': 100.0,
        'memory_full_avg10_max': 100.0,
    }
    pressure_path = receipt_root / 'pressure-baseline.json'
    pressure_path.write_text(json.dumps(pressure, indent=2) + '\n', encoding='utf-8')

    summary = {
        'policy_purpose': policy['purpose'],
        'evidence_class': policy['evidence_class'],
        'operation_root': str(receipt_root),
        'workload': workload,
        'pairs': {},
        'aa_pair': {},
        'host_before': snapshot,
        'host_after': None,
        'status': 'RUNNING',
    }

    def run_pair(label: str, root: pathlib.Path,
                 order: list[tuple[str, str]]) -> dict:
        root.mkdir(parents=True, exist_ok=False)
        pair = {'order': [role for role, _ in order], 'arms': {}}
        for position, (role, arm_key) in enumerate(order):
            library_dir = control_dir if role == 'control' else treatment_dir
            arm_root = root / f'{position}-{arm_key}'
            arm_root.mkdir(parents=True, exist_ok=False)
            result = run_arm(arm_key, arm_root, library_dir, policy, resource_root,
                             workspace_root, budget_path, pressure_path)
            pair['arms'][arm_key] = result
            if result['status'] != 'PASS':
                summary['status'] = 'OPERATION_BLOCKED'
                summary['blocked_pair'] = label
                summary['blocked_reason'] = result.get('reason', '')
                return pair
        return pair

    for index in range(1, args.pairs + 1):
        label = f'pair-{index}'
        order = ([('control', 'control'), ('treatment', 'treatment')]
                 if index % 2 == 1
                 else [('treatment', 'treatment'), ('control', 'control')])
        pair = run_pair(label, receipt_root / label, order)
        if pair['arms'].get('control', {}).get('status') == 'PASS' and \
                pair['arms'].get('treatment', {}).get('status') == 'PASS':
            control = pair['arms']['control']['phase']
            treatment = pair['arms']['treatment']['phase']
            pair['effect'] = {
                phase: paired_effect(control[phase], treatment[phase])
                for phase in ('idle', 'flush', 'optimize')
            }
        summary['pairs'][label] = pair
        if summary['status'] == 'OPERATION_BLOCKED':
            break

    if summary['status'] != 'OPERATION_BLOCKED':
        aa = run_pair(
            'aa', receipt_root / 'aa',
            [('control', 'control-a'), ('control', 'control-b')])
        summary['aa_pair'] = aa
        if (aa['arms'].get('control-a', {}).get('status') != 'PASS' or
                aa['arms'].get('control-b', {}).get('status') != 'PASS'):
            summary['status'] = 'OPERATION_BLOCKED'
            summary['blocked_reason'] = (
                aa['arms'].get('control-a', {}).get('reason', '')
                or aa['arms'].get('control-b', {}).get('reason', ''))
        else:
            first = aa['arms']['control-a']['phase']
            second = aa['arms']['control-b']['phase']
            summary['aa_pair']['effect'] = {
                phase: paired_effect(first[phase], second[phase])
                for phase in ('idle', 'flush', 'optimize')
            }

    if summary['status'] != 'OPERATION_BLOCKED':
        flush_effects = [p['effect']['flush'] for p in summary['pairs'].values()
                         if 'effect' in p]
        optimize_effects = [p['effect']['optimize'] for p in summary['pairs'].values()
                            if 'effect' in p]
        aa_flush = summary['aa_pair']['effect']['flush']
        aa_optimize = summary['aa_pair']['effect']['optimize']
        min_samples = min(
            arm['phase'][phase]['count']
            for pair in summary['pairs'].values()
            for arm in pair['arms'].values()
            for phase in ('idle', 'flush', 'optimize')
        )
        summary['qualification'] = {
            'min_samples_per_phase': min_samples,
            'required_samples': workload['min_samples_per_phase'],
            'aa_flush_ci': [aa_flush['ci_low'], aa_flush['ci_high']],
            'aa_optimize_ci': [aa_optimize['ci_low'], aa_optimize['ci_high']],
            'flush_effect_cis': [
                [effect['ci_low'], effect['ci_high']] for effect in flush_effects],
            'optimize_effect_cis': [
                [effect['ci_low'], effect['ci_high']] for effect in optimize_effects],
        }
        aa_ok = (aa_flush['ci_low'] is not None and aa_flush['ci_high'] is not None
                 and aa_flush['ci_low'] <= 0 <= aa_flush['ci_high'])
        aa_optimize_ok = (aa_optimize['ci_low'] is not None and
                          aa_optimize['ci_high'] is not None and
                          aa_optimize['ci_low'] <= 0 <= aa_optimize['ci_high'])
        flush_ok = all(effect['ci_high'] is not None and effect['ci_high'] < 0
                       for effect in flush_effects)
        optimize_ok = all(effect['ci_high'] is not None and effect['ci_high'] < 0
                          for effect in optimize_effects)
        samples_ok = min_samples >= workload['min_samples_per_phase']
        summary['qualification']['aa_flush_ok'] = aa_ok
        summary['qualification']['aa_optimize_ok'] = aa_optimize_ok
        summary['qualification']['flush_effect_ok'] = flush_ok
        summary['qualification']['optimize_effect_ok'] = optimize_ok
        summary['qualification']['samples_ok'] = samples_ok
        summary['qualification']['status'] = (
            'QUALIFIED_DIAGNOSTIC'
            if aa_ok and aa_optimize_ok and flush_ok and optimize_ok and samples_ok
            else 'INCONCLUSIVE')
        summary['status'] = summary['qualification']['status']
    summary['host_after'] = host_snapshot()
    summary['limitations'] = [
        'shared host; pressure is context only',
        'diagnostic scope only; no production qualification',
        'counterbalanced pairs share the same binaries and workload',
    ]
    (receipt_root / 'pair-summary.json').write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return 0 if summary['status'] == 'QUALIFIED_DIAGNOSTIC' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'lock-split pair failed: {error}', file=sys.stderr)
        raise SystemExit(1) from None
