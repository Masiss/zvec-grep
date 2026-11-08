#!/usr/bin/env python3
"""H2: expand H1 pool files to local method/type/SQL ranges."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_symbol_probe as ds
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-file-range-probe.json'
METHOD_RE=re.compile(r'^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|\s)+[\w<>,\[\]\?\.]+\s+(\w+)\s*\([^;]*\)\s*$')
TYPE_RE=re.compile(r'^\s*(?:public|private|protected|internal|static|partial|\s)*(?:class|struct|interface)\s+(\w+)')
SQL_RE=re.compile(r'^\s*(?:CREATE|ALTER)\s+(?:PROCEDURE|VIEW)\s+',re.I)
def ranges(lines,path):
    out=[]
    for i,line in enumerate(lines):
        m=METHOD_RE.match(line) or TYPE_RE.match(line)
        if not m: continue
        brace=i
        while brace<len(lines) and '{' not in lines[brace] and brace-i<=3: brace+=1
        if brace>=len(lines) or '{' not in lines[brace]: continue
        depth=0; end=brace
        for j in range(brace,len(lines)):
            depth+=lines[j].count('{')-lines[j].count('}'); end=j
            if depth<=0: break
        out.append((path,i+1,end+1))
    if path.lower().endswith('.sql'):
        for i,line in enumerate(lines):
            if SQL_RE.match(line):
                out.append((path,i+1,len(lines)))
    return out
def pool_units(query,env):
    units=[]; seen=set()
    for _,cmd,kind in dd.query_calls(query):
        text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
        for u in parsed:
            k=(u['path'],u['start_line'],u['end_line'])
            if k not in seen: seen.add(k); units.append(u)
    for sym in ds.question_symbols(query):
        for _,cmd,kind in (('f',[dd.BIN,'--fts',sym,'--limit','50','--preview','detail'],'fts'),('r',[dd.BIN,'--rg','-n','-F',sym],'rg')):
            text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
            for u in parsed:
                k=(u['path'],u['start_line'],u['end_line'])
                if k not in seen: seen.add(k); units.append(u)
    return units
def coverage(units,gold):
    files=0; spans=0
    for p,s,e in gold:
        if any(u['path']==p for u in units): files+=1
        if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in units): spans+=1
    return files,spans
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}
    env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'
    rows=[]
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        pool=pool_units(case['question'],env); h1_files,h1_spans=coverage(pool,gold)
        seen={(u['path'],u['start_line'],u['end_line']) for u in pool}; files_read=0; bytes_read=0; added=0
        for path in {u['path'] for u in pool}:
            target=dd.CORPUS/path
            if not target.is_file(): continue
            data=target.read_text(encoding='utf-8',errors='replace'); files_read+=1; bytes_read+=len(data.encode()); lines=data.splitlines()
            for p,s,e in ranges(lines,path):
                if (p,s,e) not in seen: seen.add((p,s,e)); pool.append({'path':p,'start_line':s,'end_line':e}); added+=1
        files,spans=coverage(pool,gold)
        rows.append({'id':cid,'gold':len(gold),'h1_file':h1_files,'h1_spans':h1_spans,'file_coverage':files,'range_coverage':spans,'pool_units':len(pool),'added_ranges':added,'files_read':files_read,'bytes_read':bytes_read})
    tot=sum(r['gold'] for r in rows)
    receipt={'schema_version':1,'status':'DISCOVERY_H2_FILE_RANGE','rows':rows,'totals':{'gold':tot,'h1_spans':sum(r['h1_spans'] for r in rows),'range_coverage':sum(r['range_coverage'] for r in rows),'file_coverage':sum(r['file_coverage'] for r in rows)},'macro':{'h1':sum(r['h1_spans']/r['gold'] for r in rows)/len(rows),'range_coverage':sum(r['range_coverage']/r['gold'] for r in rows)/len(rows),'file_coverage':sum(r['file_coverage']/r['gold'] for r in rows)/len(rows)},'cost':{'files_read':sum(r['files_read'] for r in rows),'bytes_read':sum(r['bytes_read'] for r in rows)}}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'cost':receipt['cost']},indent=2))
    for r in rows: print(r['id'],'h1',r['h1_spans'],'range',r['range_coverage'],'file',r['file_coverage'],'/',r['gold'],'added',r['added_ranges'])
    return 0
if __name__=='__main__': raise SystemExit(main())
