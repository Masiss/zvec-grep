#!/usr/bin/env python3
"""H2 bounded cap 500: coverage, provenance, CPU/wall/PSS/I/O."""
from __future__ import annotations
import json, re, sys, threading, time, resource
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_file_range_probe as h2
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-h2-bounded.json'; CAP=500
TYPE_RE=re.compile(r'^\s*(?:public|private|protected|internal|static|partial|\s)*(?:class|struct|interface)\s+(\w+)')
class Sampler(threading.Thread):
    def __init__(self): super().__init__(daemon=True); self.value=0; self.stop=False
    def run(self):
        while not self.stop:
            try:
                for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
                    if line.startswith('Pss:'): self.value=max(self.value,int(line.split()[1])); break
            except OSError: pass
            time.sleep(0.01)
def io():
    out={}
    for line in Path('/proc/self/io').read_text().splitlines():
        k,v=line.split(':',1); out[k]=int(v.strip())
    return out
def cov(units,gold):
    return sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in units))
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    sampler=Sampler(); sampler.start(); io0=io(); cpu0=resource.getrusage(resource.RUSAGE_SELF); rows=[]
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        t0=time.perf_counter(); cpu_b=resource.getrusage(resource.RUSAGE_SELF); h1=h2.pool_units(case['question'],env); pool=list(h1[:CAP]); seen={(u['path'],u['start_line'],u['end_line']) for u in pool}; orig={'search':len(pool),'method':0,'type':0}; added=0
        if len(pool)<CAP:
            for path in {u['path'] for u in h1}:
                if len(pool)>=CAP: break
                target=dd.CORPUS/path
                if not target.is_file(): continue
                lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
                for i,line in enumerate(lines):
                    if len(pool)>=CAP: break
                    kind='type' if TYPE_RE.match(line) else ('method' if h2.METHOD_RE.match(line) else None)
                    if not kind: continue
                    brace=i
                    while brace<len(lines) and '{' not in lines[brace] and brace-i<=3: brace+=1
                    if brace>=len(lines) or '{' not in lines[brace]: continue
                    depth=0; end=brace
                    for j in range(brace,len(lines)):
                        depth+=lines[j].count('{')-lines[j].count('}'); end=j
                        if depth<=0: break
                    key=(path,i+1,end+1)
                    if key not in seen and len(pool)<CAP: seen.add(key); pool.append({'path':path,'start_line':i+1,'end_line':end+1}); orig[kind]=orig.get(kind,0)+1; added+=1
        wall=(time.perf_counter()-t0)*1000; cpu_e=resource.getrusage(resource.RUSAGE_SELF); rows.append({'id':cid,'gold':len(gold),'pool_units':len(pool),'added':added,'coverage':cov(pool,gold),'origins':orig,'wall_ms':wall,'cpu_s':(cpu_e.ru_utime-cpu_b.ru_utime)+(cpu_e.ru_stime-cpu_b.ru_stime)})
    sampler.stop=True; sampler.join(); io1=io(); cpu1=resource.getrusage(resource.RUSAGE_SELF); walls=[r['wall_ms'] for r in rows]; cpus=[r['cpu_s'] for r in rows]
    receipt={'schema_version':1,'status':'DISCOVERY_H2_BOUNDED','rows':rows,'totals':{'gold':sum(r['gold'] for r in rows),'coverage':sum(r['coverage'] for r in rows),'pool_units':sum(r['pool_units'] for r in rows),'added':sum(r['added'] for r in rows),'origins':{k:sum(r['origins'].get(k,0) for r in rows) for k in ('search','method','type')}},'macro':{'coverage':sum(r['coverage']/r['gold'] for r in rows)/len(rows)},'resources':{'p95_case_wall_ms':sorted(walls)[max(0,int(round(.95*(len(walls)-1))))],'p95_case_cpu_s':sorted(cpus)[max(0,int(round(.95*(len(cpus)-1))))],'peak_pss_kib':sampler.value,'cpu_user_s':cpu1.ru_utime-cpu0.ru_utime,'cpu_sys_s':cpu1.ru_stime-cpu0.ru_stime,'io':{k:io1.get(k,0)-io0.get(k,0) for k in io0}},'limits':['cap 500 ranges/case','dev-only','no gold in policy']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'resources':receipt['resources']},indent=2))
    for r in rows: print(r['id'],r['coverage'],'/',r['gold'],'pool',r['pool_units'],'wall',round(r['wall_ms']))
    return 0
if __name__=='__main__': raise SystemExit(main())
