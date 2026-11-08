#!/usr/bin/env python3
"""Profile H1 search vs source read/pool stages for the 12 dev cases."""
from __future__ import annotations
import json, resource, sys, time
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_symbol_probe as ds
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-stage-profile.json'
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]; total_search_bytes=0; total_source_bytes=0; total_search_wall=0; total_source_wall=0
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; query=case['question']; search_bytes=0; units=[]; seen=set(); cpu0=resource.getrusage(resource.RUSAGE_CHILDREN); t0=time.perf_counter()
        for _,cmd,kind in dd.query_calls(query):
            proc=__import__('subprocess').run(cmd,cwd=dd.CORPUS,env=env,capture_output=True,text=True,timeout=120); search_bytes+=len(proc.stdout.encode())
            parsed=(dd.parse_fts(proc.stdout) if kind=='fts' else dd.parse_rg(proc.stdout))[:50]
            for u in parsed:
                k=(u['path'],u['start_line'],u['end_line'])
                if k not in seen: seen.add(k); units.append(u)
        for sym in ds.question_symbols(query):
            for _,cmd,kind in (('f',[dd.BIN,'--fts',sym,'--limit','50','--preview','detail'],'fts'),('r',[dd.BIN,'--rg','-n','-F',sym],'rg')):
                proc=__import__('subprocess').run(cmd,cwd=dd.CORPUS,env=env,capture_output=True,text=True,timeout=120); search_bytes+=len(proc.stdout.encode()); parsed=(dd.parse_fts(proc.stdout) if kind=='fts' else dd.parse_rg(proc.stdout))[:50]
                for u in parsed:
                    k=(u['path'],u['start_line'],u['end_line'])
                    if k not in seen: seen.add(k); units.append(u)
        search_wall=(time.perf_counter()-t0)*1000; cpu1=resource.getrusage(resource.RUSAGE_CHILDREN); t1=time.perf_counter(); source_bytes=0; files=0
        for path in {u['path'] for u in units}:
            target=dd.CORPUS/path
            if target.is_file(): data=target.read_text(encoding='utf-8',errors='replace'); source_bytes+=len(data.encode()); files+=1
        source_wall=(time.perf_counter()-t1)*1000; rows.append({'id':cid,'pool_units':len(units),'search_bytes':search_bytes,'source_bytes':source_bytes,'source_files':files,'search_wall_ms':search_wall,'source_wall_ms':source_wall,'child_cpu_s':(cpu1.ru_utime-cpu0.ru_utime)+(cpu1.ru_stime-cpu0.ru_stime)})
        total_search_bytes+=search_bytes; total_source_bytes+=source_bytes; total_search_wall+=search_wall; total_source_wall+=source_wall
    receipt={'schema_version':1,'status':'DISCOVERY_STAGE_PROFILE','rows':rows,'totals':{'search_bytes':total_search_bytes,'source_bytes':total_source_bytes,'search_wall_ms':total_search_wall,'source_wall_ms':total_source_wall},'limits':['parent rchar is not a stage split; subprocess stdout is search payload','dev-only']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps(receipt['totals'],indent=2))
    for r in rows: print(r['id'],'pool',r['pool_units'],'searchMB',round(r['search_bytes']/1e6,1),'sourceMB',round(r['source_bytes']/1e6,1),'searchms',round(r['search_wall_ms']),'sourcems',round(r['source_wall_ms']))
    return 0
if __name__=='__main__': raise SystemExit(main())
