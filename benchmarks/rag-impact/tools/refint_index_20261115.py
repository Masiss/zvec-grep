#!/usr/bin/env python3
"""Export a provenance-bearing lightweight source reference index."""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from refint_20261115 import RefIndex, HELPER
from selection_s1_20261115 import sha256_file

p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, required=True); p.add_argument('--out', type=Path, required=True); a = p.parse_args()
refs = RefIndex(a.root)
types = [{k: v for k, v in t.items() if k != 'depth'} for t in refs.types]
methods = [{'path': m['path'], 'name': m['name'], 'start': m['start'], 'end': m['end'], 'parent': m['parent'], 'ns': m['ns'], 'calls': sorted(m['calls']), 'procs': sorted(m['procs'])} for m in refs.methods]
props = [{'path': p['path'], 'name': p['name'], 'type': p['type'], 'start': p['start'], 'end': p['end'], 'parent': p['parent'], 'ns': p['ns']} for p in refs.props]
sqls = [{'path': s['path'], 'name': s['name'], 'start': s['start'], 'end': s['end'], 'tables': sorted(s['tables'])} for s in refs.sqls]
edges = []
for p in refs.props:
    targets = refs.type_target(p['type'])
    edges.append({'kind': 'property_type', 'source': {'path': p['path'], 'start': p['start'], 'end': p['end']}, 'name': p['name'], 'type': p['type'], 'targets': [{'path': t['path'], 'start': t['start'], 'end': t['end'], 'name': t['name']} for t in targets], 'resolved': bool(targets)})
for m in refs.methods:
    for call in sorted(m['calls']):
        targets = refs.method_by.get(call, [])
        edges.append({'kind': 'call', 'source': {'path': m['path'], 'start': m['start'], 'end': m['end']}, 'name': call, 'targets': [{'path': t['path'], 'start': t['start'], 'end': t['end'], 'name': t['name']} for t in targets[:8]], 'target_count': len(targets), 'resolved': bool(targets), 'helper': bool(HELPER.match(call))})
    for proc in sorted(m['procs']):
        targets = refs.sql_for_proc(proc)
        edges.append({'kind': 'sql_proc', 'source': {'path': m['path'], 'start': m['start'], 'end': m['end']}, 'name': proc, 'targets': [{'path': t['path'], 'start': t['start'], 'end': t['end'], 'name': t['name']} for t in targets], 'resolved': bool(targets)})
out = {'schema': 'rag-refint-index/v1', 'root': str(a.root), 'counts': {'types': len(types), 'methods': len(methods), 'properties': len(props), 'sql': len(sqls), 'edges': len(edges), 'resolved_edges': sum(e['resolved'] for e in edges)}, 'types': types, 'methods': methods, 'properties': props, 'sql': sqls, 'edges': edges}
a.out.parent.mkdir(parents=True, exist_ok=True); a.out.write_text(json.dumps(out, ensure_ascii=False) + '\n')
print(json.dumps(out['counts'], indent=1))
