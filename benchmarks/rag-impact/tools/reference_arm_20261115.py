#!/usr/bin/env python3
"""Reference-aware retrieval arm: graph neighbors without binding/role grounding."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from refint_20261115 import RefIndex, code_tokens, qterms
from selection_s1_20261115 import Source, run_counter
p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); p.add_argument('--cases',type=Path,required=True); p.add_argument('--out',type=Path,required=True); p.add_argument('--token-counter',type=Path,required=True); p.add_argument('--tokenizer',type=Path,required=True); p.add_argument('--budget',type=int,default=3000); a=p.parse_args()
refs=RefIndex(a.root); src=Source(a.root); rows=[]
for c in json.loads(a.cases.read_text())['cases']:
 q=c['question']; qs=set(qterms(q)); cands=[]
 def add(cand): cand['last_line']=cand['end_line']; cands.append(cand)
 for name in [x for x in code_tokens(q) if len(x)>=4]:
  for m in refs.method_by.get(name,[]): add({'path':m['path'],'start_line':m['start'],'end_line':m['end'],'role':'REFERENCE','source':'exact_method'})
  for t in refs.type_by.get(name,[]): add({'path':t['path'],'start_line':t['start'],'end_line':t['end'],'role':'REFERENCE','source':'exact_type'})
  for s in refs.sqls:
   if name.lower() in s['name'].lower(): add({'path':s['path'],'start_line':s['start'],'end_line':s['end'],'role':'REFERENCE','source':'exact_sql'})
 # one-hop property/call neighbors from exact methods/types
 seed=list(cands)
 for x in seed[:4]:
  for m in refs.methods:
   if m['path']==x['path'] and x['start_line']<=m['start'] and m['end']<=x['end_line']:
    for call in m['calls']:
     for t in refs.method_by.get(call,[])[:2]: add({'path':t['path'],'start_line':t['start'],'end_line':t['end'],'role':'REFERENCE','source':'call'})
    for pn in refs.props:
     if pn['path']==m['path'] and pn['parent']==m.get('parent'):
      for t in refs.type_target(pn['type'])[:1]: add({'path':t['path'],'start_line':t['start'],'end_line':t['end'],'role':'REFERENCE','source':'property'})
  for t in refs.types:
   if t['path']==x['path'] and x['start_line']<=t['start'] and t['end']<=x['end_line']:
    for pn in refs.props:
     if pn['path']==t['path'] and pn['parent']==t['name']:
      for z in refs.type_target(pn['type'])[:1]: add({'path':z['path'],'start_line':z['start'],'end_line':z['end'],'role':'REFERENCE','source':'property'})
 # dedupe/limit
 out=[]; seen=set()
 for x in cands:
  k=(x['path'],x['start_line'],x['end_line'])
  if k in seen: continue
  seen.add(k); out.append(x)
  if len(out)>=4: break
 if out:
  counts=run_counter(a.token_counter,a.tokenizer,[src.pack_text([x]) for x in out])
  for x,n in zip(out,counts): x['tokens']=n
  while out and sum(x['tokens'] for x in out)>a.budget: out.pop()
 rows.append({'id':c['id'],'abstain':not out,'units':out})
out={'schema':'rag-gq-arm/v1','policy':'reference-aware-no-grounding','results':rows}; a.out.write_text(json.dumps(out,indent=1)+'\n'); print(json.dumps({'cases':len(rows),'units':sum(len(r['units']) for r in rows),'abstain':sum(r['abstain'] for r in rows)}))
