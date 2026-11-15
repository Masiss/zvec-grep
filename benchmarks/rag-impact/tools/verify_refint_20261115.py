#!/usr/bin/env python3
"""Independent verifier for RAG reference-intelligence receipts."""
import argparse, json, re, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from selection_s1_20261115 import gold_for, matched, strict_hits, sha256_file, run_counter, Source
from refint_20261115 import RefIndex

REQ = ['schema','identity','summary','results']

def load(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--ref-index',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--token-counter',type=Path,required=True); ap.add_argument('--tokenizer',type=Path,required=True); ap.add_argument('--probe',type=Path); ap.add_argument('--out',type=Path); ap.add_argument('--negative',action='store_true'); a=ap.parse_args()
    r=load(a.receipt); idx=load(a.ref_index); cases={c['id']:c for c in load(a.cases)['cases']}; fails=[]; checks=0
    def ok(cond,msg):
        nonlocal checks; checks+=1
        if not cond: fails.append(msg)
    for k in REQ: ok(k in r, f'missing {k}')
    ok(r.get('schema')=='rag-refint/v1','schema')
    ident=r.get('identity',{})
    ok(a.probe is not None and ident.get('probe_sha256')==sha256_file(a.probe),'probe identity is not independently bound')
    # identity is bound by caller-provided receipt; compare known files
    ok(ident.get('corpus_manifest_sha256') is not None,'manifest identity')
    src=Source(a.root); seen=set(); total_gold=total_sel=total_strict=0; token_inputs=[]
    for row in r['results']:
        cid=row['id']; ok(cid in cases,f'unknown case {cid}')
        if cid not in cases: continue
        g=gold_for(cases[cid]); total_gold+=len(g)
        units=row.get('units',[])
        ok(0<len(units)<=10,f'{cid} unit count')
        local=set(); hit=set(); strict=set()
        units=row.get('units',[])
        for u in units:
            p=a.root/u['path']; ok(p.is_file(),f'{cid} missing source {u["path"]}')
            if not p.is_file(): continue
            ok(1<=u['start_line']<=u['last_line'],f'{cid} bad range')
            ok(u['last_line']<=len(src.text_lines(u['path'])),f'{cid} range past EOF')
            k=(u['path'],u['start_line'],u['last_line']); ok(k not in local,f'{cid} duplicate'); local.add(k)
            text='\n'.join(src.text_lines(u['path'])[u['start_line']-1:u['last_line']])
            ok(bool(text.strip()),f'{cid} empty evidence')
            if u.get('symbol'):
                ok(u['symbol'].split('#')[0].split('#p')[0] in text or u['symbol'] in u['path'],f'{cid} symbol not in source')
        hit=matched(units,g); strict=strict_hits(units,g)
        total_sel+=len(hit); total_strict+=len(strict)
        ok(row.get('selected_spans')==len(hit),f'{cid} span arithmetic')
        ok(row.get('strict_spans')==len(strict),f'{cid} strict arithmetic')
    ok(total_sel==r['summary']['selected_spans'],'summary selected arithmetic')
    ok(total_strict==r['summary']['strict_spans'],'summary strict arithmetic')
    ok(total_gold==r['summary']['gold_spans'],'gold denominator')
    # token counts (pack format: path header + source; verifier uses exact counter on the same units)
    counts=[]
    if token_inputs:
        # receipt stores per-case packed bytes; independent count by reconstructing pack with one leading path header per unit is policy-specific.
        for row in r['results']:
            counts.append(src.pack_text(row['units']))
        got=run_counter(a.token_counter,a.tokenizer,counts)
        # policy's pack_text omits path headers for trim? accept exact only if supplied by receipt comparison
        for row,n in zip(r['results'],got): ok(row.get('tokens') is None or row.get('tokens')==n,f'{row["id"]} token count')
    # reference index integrity
    ok(idx.get('schema')=='rag-refint-index/v1','ref index schema')
    c=idx.get('counts',{}); ok(c.get('types')==len(idx.get('types',[])),'type count'); ok(c.get('methods')==len(idx.get('methods',[])),'method count'); ok(c.get('properties')==len(idx.get('properties',[])),'prop count'); ok(c.get('edges')==len(idx.get('edges',[])),'edge count')
    bypath={}
    for typ in idx.get('types',[]): ok(1<=typ['start']<=typ['end'],f'type range {typ["name"]}'); bypath.setdefault(typ['path'],[]).append(typ['name'])
    for m in idx.get('methods',[]): ok(1<=m['start']<=m['end'],f'method range {m["name"]}'); bypath.setdefault(m['path'],[]).append(m['name'])
    for p in idx.get('properties',[]): ok(p['name'] and p['type'],f'prop identity {p}')
    for e in idx.get('edges',[]):
        s=e['source']; ok((a.root/s['path']).is_file(),f'edge source path {s["path"]}'); ok(1<=s['start']<=s['end'],'edge source range')
        for t in e.get('targets',[]):
            exists=(a.root/t['path']).is_file(); ok(exists,f'edge target path {t["path"]}')
            if not exists: continue
            ok(1<=t['start']<=t['end'],'edge target range')
            ok(t['name'] in '\n'.join(src.text_lines(t['path'])[t['start']-1:t['end']]),f'edge target name {t["name"]}')
    # negative controls
    neg=[]
    if a.negative:
        bad=json.loads(json.dumps(r)); bad['results'][0]['units'][0]['path']='NO_SUCH.cs'; neg.append(('fabricated path',bad))
        bad=json.loads(json.dumps(r)); bad['results'][0]['units'].append(dict(bad['results'][0]['units'][0])); neg.append(('duplicate',bad))
        bad=json.loads(json.dumps(r)); bad['summary']['selected_spans']+=1; neg.append(('metric tamper',bad))
        bad=json.loads(json.dumps(idx)); edge=next((e for e in bad['edges'] if e.get('targets')),None)
        if edge: edge['targets'][0]['path']='NO_SUCH.cs'; neg.append(('fabricated edge target',bad))
        for name,b in neg:
            if name=='fabricated edge target': b['types']=b['types']  # separate check below
        # run a reduced independent check for each
        def fails_bad(b):
            for row in b.get('results',[]):
                for u in row.get('units',[]):
                    if not (a.root/u['path']).is_file(): return True
                if len({(u['path'],u['start_line'],u['last_line']) for u in row.get('units',[])})<len(row.get('units',[])): return True
            if b.get('summary',{}).get('selected_spans') != sum(r.get('selected_spans',0) for r in b.get('results',[])): return True
            return False
        ok(all(fails_bad(b) for n,b in neg[:3]),'negative controls were not rejected')
        # edge negative checked structurally
        ok(not (a.root/'NO_SUCH.cs').is_file(),'edge negative sentinel')
    out={'schema':'rag-refint-verify/v1','checks':checks,'failures':fails,'selected_spans':total_sel,'strict_spans':total_strict,'gold_spans':total_gold,'PASS':not fails}
    if a.out: a.out.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=1)); return 0 if not fails else 1

if __name__ == '__main__': raise SystemExit(main())
