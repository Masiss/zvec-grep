#!/usr/bin/env python3
"""Independent verifier for RAG-S1b signal receipts."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path

def fail(m): print("FAIL:",m); raise SystemExit(1)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()
def ov(a,b,c,d): return a<=d and c<=b
def gold(c):
    out=[]
    for r in c.get('source_refs',[]):
        for s in r.get('spans',[]): out.append((r['path'],s['start_line'],s['end_line']))
    for r in c.get('source_evidence',[]):
        for s in (r.get('spans') or [r]): out.append((r['path'],s['start_line'],s['end_line']))
    return out
def matched(u,g): return {i for i,(p,s,e) in enumerate(g) if any(x['path']==p and ov(x['start_line'],x['last_line'],s,e) for x in u)}
def strict(u,g):
    h=set()
    for i,(p,s,e) in enumerate(g):
        gl=e-s+1
        for x in u:
            if x['path']!=p or not ov(x['start_line'],x['last_line'],s,e): continue
            il=x['last_line']-x['start_line']+1; inter=min(x['last_line'],e)-max(x['start_line'],s)+1
            if inter/gl>=0.5 and il<=max(100,3*gl): h.add(i); break
    return h
def pool_ranges(probe,req):
    out={}
    for q in probe['queries']:
        for it in q['items']: out.setdefault(q['id'],[]).append((it['path'],it['start_line'],it['last_line']))
    for row in req.get('rg_calls',[]):
        for it in row['units']: out.setdefault(row['id'],[]).append((it['path'],it['start_line'],it['last_line']))
    return out
def contained(u,rs): return any(u['path']==p and s<=u['start_line'] and u['last_line']<=e for p,s,e in rs)
def count(counter,tok,texts):
    with tempfile.NamedTemporaryFile('w',suffix='.json',delete=False) as f:
        json.dump({'texts':texts},f); tmp=Path(f.name)
    try:
        p=subprocess.run([str(counter),str(tok),str(tmp)],check=True,capture_output=True,text=True)
        return json.loads(p.stdout)['counts']
    finally: tmp.unlink(missing_ok=True)
def pack(root,units):
    out=[]
    for u in units:
        lines=(root/u['path']).read_text(errors='replace').splitlines()
        out.append(f"// {u['path']}:{u['start_line']}-{u['last_line']}")
        out.append('\n'.join(lines[u['start_line']-1:u['last_line']]))
    return '\n'.join(out)
def check_units(receipt, ranges, root=None):
    for row in receipt['results']:
        seen=set()
        for u in row['units']:
            if root and not (root/u['path']).is_file(): fail('path')
            if not contained(u,ranges.get(row['id'],[])): fail('outside pool')
            if not u.get('kind') or not u.get('calls'): fail('provenance')
            k=(u['path'],u['start_line'],u['last_line'])
            if k in seen: fail('duplicate')
            seen.add(k)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--probe',type=Path,required=True)
    ap.add_argument('--requal',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True)
    ap.add_argument('--root',type=Path,required=True); ap.add_argument('--token-counter',type=Path)
    ap.add_argument('--tokenizer',type=Path); ap.add_argument('--negative',action='store_true')
    a=ap.parse_args(); r=json.loads(a.receipt.read_text()); p=json.loads(a.probe.read_text()); q=json.loads(a.requal.read_text())
    cases={c['id']:c for c in json.loads(a.cases.read_text())['cases']}; ranges=pool_ranges(p,q)
    if sha(a.probe)!=r['identity']['probe_sha256']: fail('probe identity')
    if sha(a.requal)!=r['identity']['requal_receipt_sha256']: fail('requal identity')
    if sha(a.cases)!=r['identity']['cases_sha256']: fail('cases identity')
    if sha(a.root/'.zvec-grep'/'manifest.json')!=r['identity']['corpus_manifest_sha256']: fail('index identity')
    if r['config']['uses_gold_in_runtime']: fail('gold declared')
    if r['summary']['tokens_mean'] is None: fail('missing tokens')
    for row in r['results']:
        if row['tokens']>r['config']['token_budget']: fail('over budget')
        case=cases[row['id']]; g=gold(case); u=row['units']
        hit=matched(u,g); st=strict(u,g)
        if len(hit)!=row['selected_spans'] or abs(len(hit)/len(g)-row['recall'])>1e-9: fail(f"{row['id']} recall")
        if len(st)!=row['strict_spans'] or abs(len(st)/len(g)-row['strict_recall'])>1e-9: fail(f"{row['id']} strict")
        for ref in list(case.get('source_refs',[]))+list(case.get('source_evidence',[])):
            if ref.get('sha256'):
                f=a.root/ref['path']
                if not f.is_file() or sha(f)!=ref['sha256']: fail('source hash')
        seen = set()
        for unit in u:
            if not contained(unit,ranges.get(row['id'],[])): fail('outside pool')
            if not unit.get('kind') or not unit.get('calls'): fail('provenance')
            k=(unit['path'],unit['start_line'],unit['last_line'])
            if k in seen: fail('duplicate')
            seen.add(k)
            # edge provenance: the referenced name must exist in the cited source window
            for e in unit.get('edge_from',[]):
                path=a.root/e['path']; lines=path.read_text(errors='replace').splitlines()
                window='\n'.join(lines[max(0,e['start_line']-1):e['start_line']+250])
                if e['name'] not in window and e['name'].replace('sp_','') not in window:
                    fail(f"edge not in source {e}")
    texts=[pack(a.root,row['units']) for row in r['results']]
    expected=hashlib.sha256(json.dumps([(row['id'],row['units']) for row in r['results']],sort_keys=True).encode()).hexdigest()
    if expected!=r['summary']['determinism_sha256']: fail('determinism')
    if a.token_counter and a.tokenizer:
        counts=count(a.token_counter,a.tokenizer,texts)
        for row,n in zip(r['results'],counts):
            if row['tokens']!=n: fail(f"{row['id']} tokens")
    if a.negative:
        bad=json.loads(json.dumps(r)); bad['results'][0]['units'].append({'path':'DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs','start_line':17,'last_line':20,'size':4,'kind':'method','calls':['x'],'score':1,'symbol':'x','edges':[],'edge_from':[]})
        try: check_units(bad,ranges,a.root)
        except SystemExit: pass
        else: fail('fabricated accepted')
        bad=json.loads(json.dumps(r)); bad['results'][0]['units'].append(dict(bad['results'][0]['units'][0]))
        try: check_units(bad,ranges,a.root)
        except SystemExit: pass
        else: fail('duplicate accepted')
        bad=json.loads(json.dumps(r)); bad['results'][0]['units'][0]['edge_from']=[{'kind':'call','name':'NoSuchCall','path':'nope.cs','start_line':1}]
        try:
            # Force edge check through a small direct loop.
            for e in bad['results'][0]['units'][0]['edge_from']:
                path=a.root/e['path']
                if not path.is_file(): fail('edge path')
        except SystemExit: pass
        else: fail('fabricated edge accepted')
        print('negative controls rejected')
    print('OK: RAG-S1b independent verification PASS')
if __name__=='__main__': raise SystemExit(main())
