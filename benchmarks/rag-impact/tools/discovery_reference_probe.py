#!/usr/bin/env python3
"""H4: resolve references observed in the H2 pool; coverage only."""
from __future__ import annotations
import collections, json, re, sys, time
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_symbol_probe as ds
import discovery_file_range_probe as h2
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-reference-probe.json'
TYPE_REF=re.compile(r'\b(?:new\s+)?([A-Z][A-Za-z0-9_]{3,})\b')
PROC_REF=re.compile(r'\b((?:sp|fn|func)_[A-Za-z0-9_]+)\b',re.I)
SQL_EXEC=re.compile(r'execute\s+([A-Za-z0-9_\.]+)',re.I)
STOP={'Public','Private','Protected','Internal','Static','Class','Namespace','Using','This','Base','Void','String','Int','Bool','True','False','Null','Var','New','Get','Set','Context'}
def h2_pool(query,env):
    pool=h2.pool_units(query,env); seen={(u['path'],u['start_line'],u['end_line']) for u in pool}
    for path in {u['path'] for u in pool}:
        target=dd.CORPUS/path
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        for p,s,e in h2.ranges(lines,path):
            if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e})
    return pool
def refs(pool):
    counts=collections.Counter(); kinds={}
    for unit in pool:
        target=dd.CORPUS/unit['path']
        if not target.is_file(): continue
        lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
        content='\n'.join(lines[max(0,unit['start_line']-1):unit['end_line']])
        for match in PROC_REF.findall(content): counts[match]+=1; kinds[match]='proc'
        for match in SQL_EXEC.findall(content): counts[match]+=1; kinds[match]='proc'
        for match in TYPE_REF.findall(content):
            if match not in STOP: counts[match]+=1; kinds.setdefault(match,'type')
    ranked=sorted(counts.items(), key=lambda kv:(0 if kinds.get(kv[0])=='proc' else 1, kv[1], kv[0]))
    return [name for name,_ in ranked[:6]]
def coverage(pool,gold):
    files=0; spans=0
    for p,s,e in gold:
        if any(u['path']==p for u in pool): files+=1
        if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in pool): spans+=1
    return files,spans
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}
    env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]; total_queries=0; started=time.perf_counter()
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        pool=h2_pool(case['question'],env); h2_files,h2_spans=coverage(pool,gold)
        seen={(u['path'],u['start_line'],u['end_line']) for u in pool}; names=refs(pool); extra=0; queries=0
        for name in names:
            for cmd,kind in (([dd.BIN,'--fts',name,'--limit','50','--preview','detail'],'fts'),([dd.BIN,'--rg','-n','-F',name],'rg')):
                queries+=1; text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
                for u in parsed:
                    k=(u['path'],u['start_line'],u['end_line'])
                    if k not in seen: seen.add(k); pool.append(u); extra+=1
        total_queries+=queries
        files,spans=coverage(pool,gold)
        resolved=[]
        for p,s,e in gold:
            if not any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in pool):
                resolved.append({'path':p,'start_line':s,'end_line':e})
        rows.append({'id':cid,'gold':len(gold),'h2_spans':h2_spans,'references':names,'extra_units':extra,'queries':queries,'file_coverage':files,'range_coverage':spans,'pool_units':len(pool),'missing':resolved})
    tot=sum(r['gold'] for r in rows)
    receipt={'schema_version':1,'status':'DISCOVERY_H4_REFERENCE','rows':rows,'totals':{'gold':tot,'h2_spans':sum(r['h2_spans'] for r in rows),'range_coverage':sum(r['range_coverage'] for r in rows),'file_coverage':sum(r['file_coverage'] for r in rows)},'macro':{'h2':sum(r['h2_spans']/r['gold'] for r in rows)/len(rows),'range_coverage':sum(r['range_coverage']/r['gold'] for r in rows)/len(rows)},'cost':{'queries':total_queries,'wall_ms':(time.perf_counter()-started)*1000.0},'limits':['dev-only','gold measurement only','no model/index','no top10 selection']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'cost':receipt['cost']},indent=2))
    for r in rows: print(r['id'],'h2',r['h2_spans'],'h4',r['range_coverage'],'/',r['gold'],'refs',r['references'])
    return 0
if __name__=='__main__': raise SystemExit(main())
