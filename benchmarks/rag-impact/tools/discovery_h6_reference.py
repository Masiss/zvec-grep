#!/usr/bin/env python3
"""H6: structural references from observed H2 source + H5 ordering, cap 500."""
from __future__ import annotations
import collections, json, re, sys, time, resource
from pathlib import Path
RAG=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(RAG/'tools'))
import discovery_depth_probe as dd
import discovery_file_range_probe as h2
import discovery_h5_ordering as h5
import discovery_symbol_probe as ds
DEV=RAG/'cases'/'dev.json'; B0=RAG/'results'/'b0-official.json'; OUT=RAG/'results'/'discovery-h6-reference.json'; CAP=500
TYPE_DECL=re.compile(r'^\s*(?:public|private|protected|internal|static|partial|\s)*(?:class|struct|interface)\s+(\w+)')
FIELD=re.compile(r'^\s*(?:public|private|protected|internal|static|readonly|\s)+([A-Z]\w+)\s+\w+')
NEW=re.compile(r'\bnew\s+([A-Z]\w+)\b')
PROC=re.compile(r'\b((?:sp|fn|func)_[A-Za-z0-9_]+)\b',re.I)
SQL=re.compile(r'execute\s+([A-Za-z0-9_\.]+)',re.I)
def refs_from(files):
    counts=collections.Counter(); kinds={}
    for path in files:
        target=dd.CORPUS/path
        if not target.is_file(): continue
        for line in target.read_text(encoding='utf-8',errors='replace').splitlines():
            for regex,kind in ((PROC,'proc'),(SQL,'proc'),(NEW,'type'),(FIELD,'type'),(TYPE_DECL,'type')):
                m=regex.search(line)
                if m:
                    name=m.group(1)
                    if len(name)>=4 and name not in {'Public','Private','Protected','Internal','Static','Class','Struct','Interface','String','Object','List','Guid'}:
                        counts[name]+=1; kinds[name]=kind
    ranked=sorted(counts.items(), key=lambda kv:(0 if kinds.get(kv[0])=='proc' else 1, kv[1], kv[0]))
    return [name for name,_ in ranked[:6]]
def main():
    dev=json.loads(DEV.read_text()); b0=json.loads(B0.read_text()); cases={c['id']:c for c in dev['cases']}; env=dd.os.environ.copy(); env['LD_LIBRARY_PATH']=str(Path.home()/'.local/share/zvec-grep/rollback-20261101T130300Z'); env['ZVEC_GREP_HOME']=str(dd.CORPUS.parent/'home'); env['ZVEC_GREP_MODEL_CACHE']=str(dd.MODEL_CACHE); env['TMPDIR']=str(dd.CORPUS.parent/'tmp'); env['ZVEC_GREP_MODE']='direct'; io0=h5.q.read_io(); cpu0=resource.getrusage(resource.RUSAGE_SELF); rows=[]
    for bc in b0['quality']['dev_real_cases']:
        cid=bc['id']; case=cases[cid]; gold=[]
        for ref in case.get('source_refs',[]):
            for sp in ref.get('spans',[]): gold.append((ref['path'],sp['start_line'],sp['end_line']))
        t0=time.perf_counter(); query=h5.terms(case['question']); h1=h2.pool_units(case['question'],env)[:CAP]; seen={(u['path'],u['start_line'],u['end_line']) for u in h1}; ranges=[]
        for path in sorted({u['path'] for u in h1}):
            target=dd.CORPUS/path
            if not target.is_file(): continue
            lines=target.read_text(encoding='utf-8',errors='replace').splitlines()
            for p,s,e,kind in h5.q.labeled_ranges(lines,path):
                if (p,s,e) in seen: continue
                content='\n'.join(lines[max(0,s-1):e]).lower(); name=lines[s-1] if s-1<len(lines) else ''
                score=(100 if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in h1) else 0)+(20*sum(1 for x in query if x in name.lower()))+(5*sum(1 for x in query if x in content))+(2*sum(1 for x in query if x in p.lower()))+(5 if kind=='method' else 3)
                ranges.append((score,kind,p,s,e))
        ranges.sort(key=lambda x:(-x[0],x[1],x[2],x[3],x[4])); pool=list(h1)+[{'path':p,'start_line':s,'end_line':e} for _,_,p,s,e in ranges]
        refs=refs_from({u['path'] for u in pool}); ref_units=[]
        for name in refs:
            for cmd,kind in (([dd.BIN,'--fts',name,'--limit','50','--preview','detail'],'fts'),([dd.BIN,'--rg','-n','-F',name],'rg')):
                text=ds.run(cmd,env); parsed=(dd.parse_fts(text) if kind=='fts' else dd.parse_rg(text))[:50]
                for u in parsed:
                    k=(u['path'],u['start_line'],u['end_line'])
                    if k not in seen: seen.add(k); ref_units.append(u)
        # references first, then H5-ranked pool; cap 500
        pool=ref_units+pool; pool=pool[:CAP]
        cov=sum(1 for p,s,e in gold if any(u['path']==p and dd.overlap(u['start_line'],u['end_line'],s,e) for u in pool))
        rows.append({'id':cid,'gold':len(gold),'pool_units':len(pool),'refs':refs,'ref_units':len(ref_units),'coverage':cov,'wall_ms':(time.perf_counter()-t0)*1000})
    io1=h5.q.read_io(); cpu1=resource.getrusage(resource.RUSAGE_SELF); walls=[r['wall_ms'] for r in rows]
    receipt={'schema_version':1,'status':'DISCOVERY_H6_STRUCTURAL_REFERENCE','rows':rows,'totals':{'gold':sum(r['gold'] for r in rows),'coverage':sum(r['coverage'] for r in rows),'pool_units':sum(r['pool_units'] for r in rows)},'macro':{'coverage':sum(r['coverage']/r['gold'] for r in rows)/len(rows)},'resources':{'p95_case_wall_ms':sorted(walls)[max(0,int(round(.95*(len(walls)-1))))],'cpu_user_s':cpu1.ru_utime-cpu0.ru_utime,'cpu_sys_s':cpu1.ru_stime-cpu0.ru_stime,'peak_rss_kib':int([x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmHWM:')][0]),'io':{k:io1.get(k,0)-io0.get(k,0) for k in io0}},'limits':['cap 500','dev-only','structural refs from observed source','no gold in policy']}
    OUT.write_text(json.dumps(receipt,indent=2)+'\n'); print(json.dumps({'totals':receipt['totals'],'macro':receipt['macro'],'resources':receipt['resources']},indent=2))
    for r in rows: print(r['id'],r['coverage'],'/',r['gold'],'refs',r['refs'])
    return 0
if __name__=='__main__': raise SystemExit(main())
