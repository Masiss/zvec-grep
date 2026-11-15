#!/usr/bin/env python3
"""Naive exact-symbol baseline: no binding chain, roles or abstention logic."""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from refint_20261115 import RefIndex, code_tokens
from selection_s1_20261115 import Source, run_counter
p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); p.add_argument('--cases',type=Path,required=True); p.add_argument('--out',type=Path,required=True); p.add_argument('--token-counter',type=Path,required=True); p.add_argument('--tokenizer',type=Path,required=True); p.add_argument('--budget',type=int,default=3000); a=p.parse_args()
r=RefIndex(a.root); s=Source(a.root); rows=[]
for c in json.loads(a.cases.read_text())['cases']:
 q=c['question']; units=[]
 for name in [x for x in code_tokens(q) if len(x)>=4]:
  for m in r.method_by.get(name,[]): units.append({'path':m['path'],'start_line':m['start'],'last_line':m['end'],'end_line':m['end'],'role':'CONTROL'})
  for t in r.type_by.get(name,[]): units.append({'path':t['path'],'start_line':t['start'],'last_line':t['end'],'end_line':t['end'],'role':'DATA'})
  for x in r.sqls:
   if name.lower() in x['name'].lower(): units.append({'path':x['path'],'start_line':x['start'],'last_line':x['end'],'end_line':x['end'],'role':'SQL'})
 # dedupe and keep max 4
 u={}; 
 for x in units: u[(x['path'],x['start_line'],x['end_line'])]=x
 units=list(u.values())[:4]
 if units:
  counts=run_counter(a.token_counter,a.tokenizer,[s.pack_text([x]) for x in units])
  for x,n in zip(units,counts): x['tokens']=n
  while units and sum(x['tokens'] for x in units)>a.budget: units.pop()
 rows.append({'id':c['id'],'abstain':not units,'units':units})
out={'schema':'rag-evidence-grounding/v1','policy':'naive-exact-symbol','results':rows}
a.out.write_text(json.dumps(out,ensure_ascii=False,indent=1)+'\n'); print(json.dumps({'cases':len(rows),'abstain':sum(r['abstain'] for r in rows),'units':sum(len(r['units']) for r in rows)}))
