#!/usr/bin/env python3
"""Evaluate H1/H2/H4 candidate coverage on the independent dev set, cap 500."""
from __future__ import annotations
import json, resource, sys, threading, time
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_symbol_probe as ds
import discovery_file_range_probe as h2
import discovery_reference_probe as h4
CASES=Path(__import__('os').environ.get('DISCOVERY_CASES', str(RAG/'cases'/'dev-independent.json'))); OUT=RAG/'results'/'discovery-independent-probe.json'
CAP=500
class Sampler(threading.Thread):
    def __init__(self): super().__init__(daemon=True); self.value=0; self.stop=False
    def run(self):
        while not self.stop:
            try:
                for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
                    if line.startswith('Pss:'): self.value=max(self.value,int(line.split()[1])); break
            except OSError: pass
            time.sleep(0.01)
def read_io():
    out={}
    for line in Path('/proc/self/io').read_text().splitlines():
        k,v=line.split(':',1); out[k]=int(v.strip())
    return out
def cov(units,gold):
    files=sum(1 for p,s,e in gold if any(u['path']==p for u in units))
    spans=sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in units))
    return files,spans
def h2_from(h1):
    pool=list(h1[:CAP]); seen={(u['path'],u['start_line'],u['end_line']) for u in pool}; added=0
    for path in {u['path'] for u in h1}:
        target=dd.CORPUS/path
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        for p,s,e in h2.ranges(lines,path):
            if len(pool)>=CAP: break
            if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e}); added+=1
        if len(pool)>=CAP: break
    return pool,added
def h4_from(h1,env):
    pool=list(h1[:CAP]); seen={(u['path'],u['start_line'],u['end_line']) for u in pool}; refs=h4.refs(pool); queries=0; added=0
    for name in refs:
        for cmd,kind in (([dd.BIN,'--fts',name,'--limit','50','--preview','detail'],'fts'),([dd.BIN,'--rg','-n','-F',name],'rg')):
            if len(pool)>=CAP: break
            queries+=1; text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
            for u in parsed:
                if len(pool)>=CAP: break
                k=(u['path'],u['start_line'],u['end_line'])
                if k not in seen: seen.add(k); pool.append(u); added+=1
    # fill remaining budget with ranges, preserving reference evidence first
    for path in {u['path'] for u in h1}:
        if len(pool)>=CAP: break
        target=dd.CORPUS/path
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        for p,s,e in h2.ranges(lines,path):
            if len(pool)>=CAP: break
            if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e}); added+=1
    return pool,refs,queries,added
def main():
    cases=json.loads(CASES.read_text())['cases']; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    sampler=Sampler(); sampler.start(); io0=read_io(); cpu0=resource.getrusage(resource.RUSAGE_SELF); rows=[]; started_all=time.perf_counter()
    for case in cases:
        gold=[]
        for ref in case['source_refs']:
            for sp in ref['spans']: gold.append((ref['path'],sp['start_line'],sp['end_line']))
        t0=time.perf_counter(); h1=h2.pool_units(case['question'],env); t1=time.perf_counter(); h2p,added2=h2_from(h1); t2=time.perf_counter(); h4p,refs,queries,added4=h4_from(h1,env); t3=time.perf_counter()
        rows.append({'id':case['id'],'gold':len(gold),'h1_units':len(h1[:CAP]),'h1_cov':cov(h1[:CAP],gold)[1],'h2_units':len(h2p),'h2_added':added2,'h2_cov':cov(h2p,gold)[1],'h4_units':len(h4p),'h4_added':added4,'h4_cov':cov(h4p,gold)[1],'h4_refs':refs,'h4_queries':queries,'h1_wall_ms':(t1-t0)*1000,'h2_wall_ms':(t2-t1)*1000,'h4_wall_ms':(t3-t2)*1000})
    sampler.stop=True; sampler.join(); io1=read_io(); cpu1=resource.getrusage(resource.RUSAGE_SELF); walls=[r['h1_wall_ms']+r['h2_wall_ms']+r['h4_wall_ms'] for r in rows]
    receipt={'schema_version':1,'status':'DISCOVERY_INDEPENDENT_DEV','cases':rows,'totals':{'gold':sum(r['gold'] for r in rows),'h1_cov':sum(r['h1_cov'] for r in rows),'h2_cov':sum(r['h2_cov'] for r in rows),'h4_cov':sum(r['h4_cov'] for r in rows)},'macro':{'h1':sum(r['h1_cov']/r['gold'] for r in rows)/len(rows),'h2':sum(r['h2_cov']/r['gold'] for r in rows)/len(rows),'h4':sum(r['h4_cov']/r['gold'] for r in rows)/len(rows)},'resources':{'peak_pss_kib':sampler.value,'cpu_user_s':cpu1.ru_utime-cpu0.ru_utime,'cpu_sys_s':cpu1.ru_stime-cpu0.ru_stime,'io':{k:io1.get(k,0)-io0.get(k,0) for k in io0},'p95_case_wall_ms':sorted(walls)[max(0,int(round(.95*(len(walls)-1))))]},'limits':['5 independent dev cases; held-out locked','cap 500 ranges/case','gold measurement only','no top10 selection']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'resources':receipt['resources']},indent=2))
    for r in rows: print(r['id'],'h1',r['h1_cov'],'h2',r['h2_cov'],'h4',r['h4_cov'],'/',r['gold'],'refs',r['h4_refs'])
    return 0
if __name__=='__main__': raise SystemExit(main())
