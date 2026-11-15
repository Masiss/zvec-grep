#!/usr/bin/env python3
"""Gate A verifier: source liveness, binding integrity, property discovery."""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from refint_20261115 import RefIndex
from selection_s1_20261115 import Source

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--bindings',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    src=Source(a.root); refs=RefIndex(a.root); binds=json.loads(a.bindings.read_text()); fails=[]; checks=0
    def ok(c,m):
        nonlocal checks; checks+=1
        if not c: fails.append(m)
    # Commented declarations must not be indexed.
    ok('ChangeWorkProcess' not in refs.method_by,'commented ChangeWorkProcess indexed')
    m=next((x for x in refs.methods if x['name']=='EditEmployeeJobAndGroup'),None)
    ok(m is not None,'missing live EditEmployeeJobAndGroup')
    if m:
        ok('ChangeWorkProcess' not in m['calls'],'commented ChangeWorkProcess call indexed')
        ok('sp_UpdateOrgTotalEmployee' not in m['procs'],'commented SQL body indexed')
        ok(m['liveness']=='LIVE','EditEmployeeJobAndGroup liveness')
    # Property/type resolution.
    props=[p for p in refs.props if p['name']=='EmployeeChangeRequestDetails']
    ok(props and props[0]['type']=='EmployeeChangeRequestDetail','DbSet property resolution')
    ok(any(t['name']=='EmployeeChangeRequestDetail' for t in refs.types),'type declaration')
    # Binding integrity.
    for b in binds.get('bindings',[]):
        ok(b['state'] in ('VERIFIED','AMBIGUOUS','UNRESOLVED'),'binding state')
        s=b['source']; text='\n'.join(src.text_lines(s['path'])[s['start']-1:s['end']])
        ok(text.strip()!='','empty binding source')
        if b['state']=='VERIFIED':
            ok(b['reason']!='name_only_not_invocation','name-only verified')
            if b['reason'].startswith('wrapper'):
                for name in b['chain']:
                    ok(name in refs.method_by,f'unlive chain member {name}')
            if b['reason'].startswith('explicit'):
                ok(re.search(r'\bexec(?:ute)?\s+(?:\[?dbo\]?\.*)?\[?'+re.escape(b['proc']),text,re.I) is not None,'direct binding without explicit exec')
        for t in b.get('targets',[]): ok((a.root/t['path']).is_file(),'missing binding target')
    ok(not any(b['reason']=='name_only_not_invocation' and b['state']=='VERIFIED' for b in binds.get('bindings',[])),'name-only promoted')
    # Negative control: a fabricated commented method must not resolve.
    ok('CommentedCall' not in {c for x in refs.methods for c in x['calls']},'commented synthetic call index')
    out={'schema':'rag-gate-a-verify/v1','checks':checks,'failures':fails,'methods':len(refs.methods),'types':len(refs.types),'properties':len(refs.props),'bindings':binds.get('counts',{}),'PASS':not fails}
    a.out.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=1)); return 0 if not fails else 1
if __name__=='__main__': raise SystemExit(main())
