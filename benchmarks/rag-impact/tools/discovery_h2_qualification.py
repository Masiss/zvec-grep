#!/usr/bin/env python3
"""H2 pool qualification: provenance, dedup, resources, coverage@budget."""
from __future__ import annotations
import json, re, statistics, sys, threading, time, resource
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_file_range_probe as h2
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-h2-qualification.json'
TYPE_RE=re.compile(r'^\s*(?:public|private|protected|internal|static|partial|\s)*(?:class|struct|interface)\s+(\w+)')
SQL_RE=re.compile(r'^\s*(?:CREATE|ALTER)\s+(?:PROCEDURE|VIEW)\s+',re.I)
class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.value=0; self.stop=False
    def run(self):
        while not self.stop:
            try:
                for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
                    if line.startswith('Pss:'):
                        self.value=max(self.value,int(line.split()[1])); break
            except OSError: pass
            time.sleep(0.01)
def read_io():
    out={}
    try:
        for line in Path('/proc/self/io').read_text().splitlines():
            k,v=line.split(':',1); out[k]=int(v.strip())
    except OSError: pass
    return out
def labeled_ranges(lines,path):
    out=[]
    for i,line in enumerate(lines):
        kind=None
        if TYPE_RE.match(line): kind='type'
        elif h2.METHOD_RE.match(line): kind='method'
        if kind:
            brace=i
            while brace<len(lines) and '{' not in lines[brace] and brace-i<=3: brace+=1
            if brace>=len(lines) or '{' not in lines[brace]: continue
            depth=0; end=brace
            for j in range(brace,len(lines)):
                depth+=lines[j].count('{')-lines[j].count('}'); end=j
                if depth<=0: break
            out.append((path,i+1,end+1,kind))
    if path.lower().endswith('.sql'):
        for i,line in enumerate(lines):
            if SQL_RE.match(line): out.append((path,i+1,len(lines),'sql'))
    return out
def coverage(units,gold):
    return sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in units))
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}
    env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]; sampler=Sampler(); sampler.start(); io0=read_io(); cpu0=resource.getrusage(resource.RUSAGE_SELF)
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        started=time.perf_counter(); cpu_before=resource.getrusage(resource.RUSAGE_SELF)
        h1=h2.pool_units(case['question'],env); seen={(u['path'],u['start_line'],u['end_line']) for u in h1}; pool=list(h1); origins={'search':len(h1),'method':0,'type':0,'sql':0}; duplicates=0; added=0
        for path in {u['path'] for u in h1}:
            target=dd.CORPUS/path
            if not target.is_file(): continue
            lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
            for p,s,e,kind in labeled_ranges(lines,path):
                key=(p,s,e)
                if key in seen: duplicates+=1; continue
                seen.add(key); pool.append({'path':p,'start_line':s,'end_line':e}); origins[kind]=origins.get(kind,0)+1; added+=1
        wall=(time.perf_counter()-started)*1000.0; cpu=resource.getrusage(resource.RUSAGE_SELF)
        budgets={}; full=coverage(pool,gold)
        for n in (100,500,1000,5000,10000,len(pool)):
            budgets[str(n)]=coverage(pool[:n],gold)
        rows.append({'id':cid,'gold':len(gold),'h1_units':len(h1),'pool_units':len(pool),'added':added,'duplicates':duplicates,'origins':origins,'coverage':full,'coverage_at_budget':budgets,'wall_ms':wall,'cpu_user_s':cpu.ru_utime-cpu_before.ru_utime,'cpu_sys_s':cpu.ru_stime-cpu_before.ru_stime})
    sampler.stop=True; sampler.join(); io1=read_io(); cpu1=resource.getrusage(resource.RUSAGE_SELF)
    walls=[r['wall_ms'] for r in rows]; cpus=[r['cpu_user_s']+r['cpu_sys_s'] for r in rows]
    def p95(v): return sorted(v)[max(0,int(round(.95*(len(v)-1))))]
    receipt={'schema_version':1,'status':'DISCOVERY_H2_QUALIFICATION','rows':rows,'totals':{'gold':sum(r['gold'] for r in rows),'coverage':sum(r['coverage'] for r in rows),'pool_units':sum(r['pool_units'] for r in rows),'added':sum(r['added'] for r in rows),'duplicates':sum(r['duplicates'] for r in rows),'origins':{k:sum(r['origins'].get(k,0) for r in rows) for k in ('search','method','type','sql')}},'macro':{'coverage':sum(r['coverage']/r['gold'] for r in rows)/len(rows)},'resources':{'p95_case_wall_ms':p95(walls),'p95_case_cpu_s':p95(cpus),'peak_pss_kib':sampler.value,'cpu_user_s':cpu1.ru_utime-cpu0.ru_utime,'cpu_sys_s':cpu1.ru_stime-cpu0.ru_stime,'io':{k:io1.get(k,0)-io0.get(k,0) for k in io0}},'limits':['dev-only','no targeted selection','coverage@budget uses insertion order']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'resources':receipt['resources']},indent=2))
    for r in rows: print(r['id'],'pool',r['pool_units'],'added',r['added'],'dup',r['duplicates'],'coverage',r['coverage'],'/',r['gold'],'wall',round(r['wall_ms'],1))
    return 0
if __name__=='__main__': raise SystemExit(main())
