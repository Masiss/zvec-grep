#!/usr/bin/env python3
"""H5: deterministic relevance-aware ordering of the H2 pool under cap 500."""
from __future__ import annotations
import json, re, sys, time, resource
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_file_range_probe as h2
import discovery_h2_qualification as q
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-h5-ordering.json'; CAP=500
WORD=re.compile(r'[\wÀ-ỹ]+',re.UNICODE)
def terms(text): return {w.lower() for w in WORD.findall(text) if len(w)>=2}
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]; io0=q.read_io(); cpu0=resource.getrusage(resource.RUSAGE_SELF)
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        query=terms(case['question']); t0=time.perf_counter(); h1=h2.pool_units(case['question'],env); h1=h1[:CAP]; seen={(u['path'],u['start_line'],u['end_line']) for u in h1}; ranges=[]; files=0; bytes_read=0
        for path in sorted({u['path'] for u in h1}):
            target=dd.CORPUS/path
            if not target.is_file(): continue
            data=target.read_text(encoding='utf-8',errors='replace'); files+=1; bytes_read+=len(data.encode()); lines=data.splitlines()
            for p,s,e,kind in q.labeled_ranges(lines,path):
                if (p,s,e) in seen: continue
                content='\n'.join(lines[max(0,s-1):e]).lower(); name=lines[s-1] if s-1<len(lines) else ''
                score=(100 if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in h1) else 0)+(20*sum(1 for x in query if x in name.lower()))+(5*sum(1 for x in query if x in content))+(2*sum(1 for x in query if x in p.lower()))+(5 if kind=='method' else 3)
                ranges.append((score,kind,p,s,e))
        ranges.sort(key=lambda x:(-x[0],x[1],x[2],x[3],x[4]))
        pool=list(h1)+[{'path':p,'start_line':s,'end_line':e} for _,_,p,s,e in ranges]
        pool=pool[:CAP]
        def cov(n): return sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in pool[:n]))
        rows.append({'id':cid,'gold':len(gold),'h1_units':len(h1),'pool_units':len(pool),'coverage':cov(len(pool)),'coverage_at_budget':{str(n):cov(n) for n in (100,250,500)},'files':files,'bytes':bytes_read,'wall_ms':(time.perf_counter()-t0)*1000,'selected_hash':__import__('hashlib').sha256(json.dumps(pool,separators=(',',':')).encode()).hexdigest()})
    io1=q.read_io(); cpu1=resource.getrusage(resource.RUSAGE_SELF); walls=[r['wall_ms'] for r in rows]
    receipt={'schema_version':1,'status':'DISCOVERY_H5_DETERMINISTIC_ORDER','rows':rows,'totals':{'gold':sum(r['gold'] for r in rows),'coverage':sum(r['coverage'] for r in rows),'pool_units':sum(r['pool_units'] for r in rows)},'macro':{'coverage':sum(r['coverage']/r['gold'] for r in rows)/len(rows)},'resources':{'p95_case_wall_ms':sorted(walls)[max(0,int(round(.95*(len(walls)-1))))],'cpu_user_s':cpu1.ru_utime-cpu0.ru_utime,'cpu_sys_s':cpu1.ru_stime-cpu0.ru_stime,'peak_rss_kib':int([x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmHWM:')][0]),'io':{k:io1.get(k,0)-io0.get(k,0) for k in io0}},'limits':['cap 500','dev-only','no gold in order','sorted files/ranges deterministic']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'resources':receipt['resources']},indent=2))
    for r in rows: print(r['id'],r['coverage'],'/',r['gold'],'budget',r['coverage_at_budget'],'hash',r['selected_hash'][:10])
    return 0
if __name__=='__main__': raise SystemExit(main())
