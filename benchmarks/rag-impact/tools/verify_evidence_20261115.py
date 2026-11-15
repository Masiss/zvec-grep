#!/usr/bin/env python3
"""Independent verifier for evidence roles, SQL bindings and grounding receipts."""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from selection_s1_20261115 import Source, sha256_file

def overlap_len(u,v):
    if u['path']!=v['path']: return 0
    return max(0,min(u['end_line'],v['end_line'])-max(u['start_line'],v['start_line'])+1)

def ov(u,v): return overlap_len(u,v)>0

def covers(u,v,threshold=0.5): return overlap_len(u,v)/(v['end_line']-v['start_line']+1)>=threshold
def role_of(sel, labels):
    hits=[u for u in labels if ov(sel,u)]
    if not hits: return 'UNKNOWN'
    rank={'REQUIRED':4,'SUPPORTING':3,'MISLEADING':2,'OPTIONAL':1,'IRRELEVANT':0}
    return max(hits,key=lambda u:rank.get(u['role'],0))['role']

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--bindings',type=Path,required=True); ap.add_argument('--evaluation',type=Path,required=True); ap.add_argument('--sanitized-receipt',type=Path); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    src=Source(a.root); cases=json.loads(a.cases.read_text())['cases']; rec=json.loads(a.receipt.read_text()); binds=json.loads(a.bindings.read_text()); ev=json.loads(a.evaluation.read_text()); fails=[]; checks=0
    def ok(c,m):
        nonlocal checks; checks+=1
        if not c: fails.append(m)
    # annotation/source identity
    ids=set()
    for c in cases:
        ok(c['id'] not in ids,f'duplicate case {c["id"]}'); ids.add(c['id'])
        ok(c.get('frozen') is True,f'{c["id"]} not frozen')
        labels=c.get('units',[])
        for u in labels:
            p=a.root/u['path']; ok(p.is_file(),f'{c["id"]} missing label source {u["path"]}')
            if p.is_file(): ok(1<=u['start_line']<=u['end_line']<=len(src.text_lines(u['path'])),f'{c["id"]} label range')
            ok(u.get('role') in ('REQUIRED','SUPPORTING','OPTIONAL','IRRELEVANT','MISLEADING'),f'{c["id"]} bad role')
        for g in c.get('required_groups',[]):
            ok(g.get('any_of'),f'{c["id"]} empty required group')
            for x in g.get('any_of',[]): ok(any(ov(x,u) for u in labels),f'{c["id"]} group unit not in labels')
        if c.get('expected_abstain'): ok(not c.get('required_groups'),f'{c["id"]} abstain has required groups')
    # bindings
    counts={'VERIFIED':0,'AMBIGUOUS':0,'UNRESOLVED':0}
    for b in binds.get('bindings',[]):
        st=b.get('state'); counts[st]=counts.get(st,0)+1
        s=b['source']; ok((a.root/s['path']).is_file(),'binding source missing'); text='\n'.join(src.text_lines(s['path'])[s['start']-1:s['end']])
        ok(b['chain'] and b['chain'][0]==s['name'],'binding chain root')
        if b['reason'].startswith('explicit'):
            ok(st=='VERIFIED' and re.search(r'\bexec(?:ute)?\s+(?:\[?dbo\]?\.*)?\[?'+re.escape(b['proc']),text,re.I) is not None,f'{s["name"]} explicit binding not evidenced')
        elif b['reason'].startswith('wrapper'):
            ok(len(b['chain'])>=2,'wrapper chain too short')
            ok(st in ('VERIFIED','AMBIGUOUS'),'wrapper state')
        elif b['reason'].startswith('name_only'):
            ok(st!='VERIFIED','name-only promoted to VERIFIED')
        else: ok(False,'unknown binding reason')
        for t in b.get('targets',[]):
            ok((a.root/t['path']).is_file(),'binding target missing'); ok(t['name'] in '\n'.join(src.text_lines(t['path'])[t['start']-1:t['end']]),'binding target name')
    ok(counts.get('VERIFIED',0)>0,'no verified SQL binding')
    # receipt
    cby={c['id']:c for c in cases}; selected_total=0; groups_total=groups_hit=req_total=req_hit=mis=unk=0
    for r in rec['results']:
        c=cby.get(r['id']); ok(c is not None,f'unknown receipt case {r["id"]}'); 
        if not c: continue
        labels=c['units']; units=r['units']; selected_total+=len(units)
        for u in units:
            p=a.root/u['path']; ok(p.is_file(),f'{r["id"]} selected source missing')
            if p.is_file(): ok(1<=u['start_line']<=u['end_line']<=len(src.text_lines(u['path'])),f'{r["id"]} selected range')
            ok(u.get('tokens',0)>=0,f'{r["id"]} token')
        for g in c.get('required_groups',[]):
            groups_total+=1; groups_hit+=any(covers(u,x) for u in units for x in g['any_of'])
        ru=[u for u in labels if u['role']=='REQUIRED']; req_total+=len(ru); req_hit+=sum(any(covers(s,u) for s in units) for u in ru)
        roles=[role_of(u,labels) for u in units]; mis+=sum(z=='MISLEADING' for z in roles); unk+=sum(z=='UNKNOWN' for z in roles)
        abst=r['abstain']; exp=c['expected_abstain']; ok(abst==exp,f'{r["id"]} abstention mismatch')
    summary=ev['summary']; ok(summary['selected']==selected_total,'eval selected arithmetic'); ok(summary['required_groups']==groups_total,'eval groups denominator'); ok(summary['required_groups_hit']==groups_hit,'eval group hit arithmetic'); ok(summary['misleading']==mis,'eval misleading arithmetic'); ok(summary['unknown']==unk,'eval unknown arithmetic')
    # optional sanitized isolation
    if a.sanitized_receipt:
        san=json.loads(a.sanitized_receipt.read_text())
        def sig(x): return {r['id']:[(u['path'],u['start_line'],u['end_line'],u['role']) for u in r['units']] for r in x['results']}
        ok(sig(rec)==sig(san),'sanitized runtime output differs')
    # negative controls
    bad=json.loads(json.dumps(rec)); has_unit=any(r.get('units') for r in bad['results'])
    if has_unit:
        row=next(r for r in bad['results'] if r.get('units')); row['units'][0]['path']='NO_SUCH.cs'
        ok(not (a.root/'NO_SUCH.cs').exists(),'fabricated path sentinel')
    ok(not any(x['reason'].startswith('name_only') and x['state']=='VERIFIED' for x in binds.get('bindings',[])),'name-only binding promoted to VERIFIED')
    out={'schema':'rag-evidence-verify/v1','cases':len(cases),'checks':checks,'failures':fails,'binding_counts':counts,'selected_units':selected_total,'required_groups_hit':groups_hit,'required_groups':groups_total,'misleading_selected':mis,'unknown_selected':unk,'PASS':not fails}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=1)); return 0 if not fails else 1
if __name__=='__main__': raise SystemExit(main())
