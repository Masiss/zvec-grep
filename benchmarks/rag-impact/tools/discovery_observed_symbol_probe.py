#!/usr/bin/env python3
"""H3: rare observed symbols from the H2 pool, exact probes, coverage only."""
from __future__ import annotations
import collections, json, re, sys
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_symbol_probe as ds
import discovery_file_range_probe as h2
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-observed-symbol-probe.json'
IDENT_RE=re.compile(r'\b[A-Za-z_][A-Za-z0-9_]{3,}\b')
PROC_RE=re.compile(r'\b(?:sp|fn|func)_[A-Za-z0-9_]+\b',re.I)
STOP={'public','private','protected','internal','static','virtual','override','async','return','class','namespace','using','this','base','void','string','int','bool','true','false','null','var','new','get','set'}
def observed(files,env):
    counts=collections.Counter(); text_cache={}
    for path in files:
        target=dd.CORPUS/path
        if not target.is_file(): continue
        data=target.read_text(encoding='utf-8',errors='replace'); text_cache[path]=data
        for match in IDENT_RE.findall(data):
            if match[0].isupper() or '_' in match:
                counts[match]+=1
        for match in PROC_RE.findall(data): counts[match]+=1
    return [k for k,v in sorted(counts.items(),key=lambda kv:(kv[1],kv[0])) if k not in STOP][:4]
def coverage(pool,gold):
    files=0; spans=0
    for p,s,e in gold:
        if any(u['path']==p for u in pool): files+=1
        if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in pool): spans+=1
    return files,spans
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}
    env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        pool=h2.pool_units(case['question'],env); files={u['path'] for u in pool}; seen={(u['path'],u['start_line'],u['end_line']) for u in pool}
        for path in list(files):
            target=dd.CORPUS/path
            if not target.is_file(): continue
            data=target.read_text(encoding='utf-8',errors='replace'); lines=data.splitlines()
            for p,s,e in h2.ranges(lines,path):
                if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e})
        h2_files,h2_spans=coverage(pool,gold)
        symbols=observed(files,env)
        extra=0
        for sym in symbols:
            for cmd,kind in (([dd.BIN,'--fts',sym,'--limit','50','--preview','detail'],'fts'),([dd.BIN,'--rg','-n','-F',sym],'rg')):
                text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
                for u in parsed:
                    k=(u['path'],u['start_line'],u['end_line'])
                    if k not in seen: seen.add(k); pool.append(u); extra+=1
        files_cov,spans_cov=coverage(pool,gold)
        rows.append({'id':cid,'gold':len(gold),'h2_spans':h2_spans,'observed_symbols':symbols,'extra_units':extra,'file_coverage':files_cov,'range_coverage':spans_cov,'pool_units':len(pool)})
    tot=sum(r['gold'] for r in rows)
    receipt={'schema_version':1,'status':'DISCOVERY_H3_OBSERVED_SYMBOL','rows':rows,'totals':{'gold':tot,'h2_spans':sum(r['h2_spans'] for r in rows),'range_coverage':sum(r['range_coverage'] for r in rows),'file_coverage':sum(r['file_coverage'] for r in rows)},'macro':{'h2':sum(r['h2_spans']/r['gold'] for r in rows)/len(rows),'range_coverage':sum(r['range_coverage']/r['gold'] for r in rows)/len(rows)},'limits':['dev-only','gold measurement only','no model/index','no top10 selection']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro']},indent=2))
    for r in rows: print(r['id'],'h2',r['h2_spans'],'h3',r['range_coverage'],'/',r['gold'],'symbols',r['observed_symbols'])
    return 0
if __name__=='__main__': raise SystemExit(main())
