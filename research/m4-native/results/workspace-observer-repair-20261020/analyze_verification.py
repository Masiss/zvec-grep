#!/usr/bin/env python3
"""File-only observer-repair verification digest; never launches a workload."""
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Cannot verify retained evidence {path}: {exc}') from exc


checks = load_json(ROOT / 'verification-v3/test-receipt.json')
for source, digest in checks['source_sha256'].items():
    assert hashlib.sha256((REPO / source).read_bytes()).hexdigest() == digest
assert (ROOT / 'final-ty-exit.txt').read_text().strip() == '0'
consumer = load_json(ROOT / 'consumer-final/test-receipt.json')
controller = (REPO / 'research/m4-native/results/m4-search-execution-marathon-20261018/'
              'phase-b-cost-model/writer-tail-causal-20261019/run_host_carryover_aa.py')
assert consumer['source_unchanged'] and all(c['exit_code'] == 0 for c in consumer['checks'])
assert hashlib.sha256(controller.read_bytes()).hexdigest() == consumer['controller_sha256']
rows = []
for path in sorted((ROOT / 'verification-v3').rglob('receipt.json')):
    receipt = load_json(path)
    rows.append({
        'path': str(path.relative_to(ROOT)), 'status': receipt.get('status'),
        'unit': receipt.get('unit'), 'benchmark_started': receipt.get('benchmark_started'),
        'workload_returncode': receipt.get('workload_returncode'),
        'workload_signal': receipt.get('workload_signal'),
        'unit_cleanup': receipt.get('unit_cleanup'),
        'controller_cleanup': receipt.get('controller_cleanup'),
        'note': 'Includes deliberately mocked negative fixtures; not all rows represent real services.',
    })
active = subprocess.run(['systemctl', '--user', 'list-units', 'm4-contained-*', '--all',
                         '--no-legend', '--no-pager'], capture_output=True, text=True)
fs = subprocess.run(['findmnt', '-T', str(REPO / 'research/m4-native/.work/tmp'),
                     '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'], capture_output=True, text=True)
ledger = REPO / 'research/m4-native/results/autonomous-campaign-20261020/campaign-ledger.json'
result = {
    'checks': checks, 'final_consumer_rerun': consumer,
    'test_count': sum(c['tests'] or 0 for c in checks['checks']),
    'all_checks_pass': all(c['exit_code'] == 0 for c in checks['checks']),
    'direct_typecheck': {'exit_code': 0, 'paths': 4, 'log': 'final-ty.log'},
    'receipts': rows,
    'closure_owned_units_query': {'returncode': active.returncode, 'stdout': active.stdout,
                                 'stderr': active.stderr},
    'filesystem_observation': {'returncode': fs.returncode, 'stdout': fs.stdout},
    'campaign_ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(),
    'native_launch_ready': False,
    'recovery_condition': 'Independently verified correctly scoped allocated AND apparent capacity evidence; no backend implemented. Fresh separate native authority also required.',
    'lsp': {'checked_paths': ['tools/m4_containment_runner.py', 'tools/test_m4_workspace_observer.py'],
            'errors': 0, 'hints': 2, 'auxiliary_numeric_parse_warnings': 30,
            'scope': 'Active two-path probe; analyzer subsequently confirmed clean. Final A/A controller probe had zero diagnostics but inconclusive clean confirmation; not a warning-free repository claim.'},
}
assert result['all_checks_pass'] and result['test_count'] == 72
assert active.returncode == 0 and not active.stdout.strip(), 'owned units remain or closure unknown'
(ROOT / 'verification-summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'tests': result['test_count'], 'owned_units_absent': True,
                  'native_launch_ready': False}, sort_keys=True))
