#!/usr/bin/env python3
"""Trace H2 full-prefix 500 vs bounded 500 losses."""
from __future__ import annotations
import json, sys
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_file_range_probe as h2
import discovery_h2_qualification as q
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-h2-prefix-audit.json'; CAP=500
def add_ranges(pool):
    seen={(u['path'],u['start_line'],u['end_line']) for u in pool}
    for path in {u['path'] for u in pool}:
        target=dd.CORPUS/path
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        for p,s,e,kind in q.labeled_ranges(lines,path):
            if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e})
    return pool
def bounded(h1):
    pool=list(h1[:CAP]); seen={(u['path'],u['start_line'],u['end_line']) for u in pool}
    for path in {u['path'] for u in h1}:
        if len(pool)>=CAP: break
        target=dd.CORPUS/path
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        for p,s,e in h2.ranges(lines,path):
            if len(pool)>=CAP: break
            if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e})
    return pool
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        h1=h2.pool_units(case['question'],env); full=add_ranges(list(h1)); fp=full[:CAP]; bp=bounded(h1)
        fcov=sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in fp))
        bcov=sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in bp))
        print(cid,'h1',len(h1),'full500',fcov,'bounded',bcov,'gold',len(gold),flush=True)
        for p,s,e in gold:
            fhit=any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in fp)
            bhit=any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in bp)
            if fhit and not bhit:
                covering=next((u for u in fp if u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e)),None)
                index=fp.index(covering) if covering else None
                in_full=full.index(covering) if covering in full else None
                rows.append({'case':cid,'path':p,'start':s,'end':e,'covering':covering,'prefix_index':index,'full_index':in_full,'h1_units':len(h1),'full_prefix_units':len(fp),'bounded_units':len(bp)})
    OUT.write_text(json.dumps({'schema_version':1,'status':'H2_PREFIX_AUDIT','lost':rows},indent=2)+'\n')
    print(json.dumps(rows,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
