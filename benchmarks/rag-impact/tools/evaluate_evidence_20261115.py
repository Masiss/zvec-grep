#!/usr/bin/env python3
"""Evaluate evidence roles, required groups, precision and abstention."""
import argparse, json
from pathlib import Path

def overlap_len(u,v):
    if u['path']!=v['path']: return 0
    return max(0,min(u['end_line'],v['end_line'])-max(u['start_line'],v['start_line'])+1)

def ov(u,v): return overlap_len(u,v)>0

def covers(u,v,threshold=0.5):
    return overlap_len(u,v)/(v['end_line']-v['start_line']+1) >= threshold

def role_of(sel, labels):
    hits=[u for u in labels if ov(sel,u)]
    if not hits: return 'UNKNOWN'
    rank={'REQUIRED':4,'SUPPORTING':3,'MISLEADING':2,'OPTIONAL':1,'IRRELEVANT':0}
    return max(hits,key=lambda u:rank.get(u['role'],0))['role']

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    cases={c['id']:c for c in json.loads(a.cases.read_text())['cases']}; rec=json.loads(a.receipt.read_text()); rows=[]; agg={'required_groups':0,'required_groups_hit':0,'selected':0,'required_units':0,'required_units_hit':0,'supporting':0,'misleading':0,'unknown':0,'correct_abstain':0,'false_abstain':0,'false_answer':0,'misleading_admissions':0,'tokens':0}
    for r in rec['results']:
        c=cases[r['id']]; labels=c['units']; selected=r['units']; roles=[role_of(u,labels) for u in selected]
        groups=c['required_groups']; hit_groups=[]
        for g in groups:
            hit=any(covers(u,x) for u in selected for x in g['any_of'])
            if hit: hit_groups.append(g['id'])
        required_units=[u for u in labels if u['role']=='REQUIRED']
        hit_required=[u for u in required_units if any(covers(s,u) for s in selected)]
        # selected required hit may cover a group but not every labeled required unit
        misleading=[s for s,z in zip(selected,roles) if z=='MISLEADING']
        sup=sum(z=='SUPPORTING' for z in roles); mis=sum(z=='MISLEADING' for z in roles); unk=sum(z=='UNKNOWN' for z in roles)
        abst=r['abstain']; exp=c['expected_abstain']
        row={'id':r['id'],'required_groups':len(groups),'groups_hit':len(hit_groups),'group_recall':len(hit_groups)/len(groups) if groups else None,'required_units':len(required_units),'required_units_hit':len(hit_required),'required_unit_recall':len(hit_required)/len(required_units) if required_units else None,'selected':len(selected),'required_selected':sum(z=='REQUIRED' for z in roles),'supporting_selected':sup,'misleading_selected':mis,'unknown_selected':unk,'misleading_admissions':len(misleading),'abstain':abst,'expected_abstain':exp,'correct_abstain':bool(abst==exp and (not exp or abst)),'false_abstain':bool(not exp and abst),'false_answer':bool(exp and not abst),'tokens':sum(u.get('tokens',0) for u in selected),'units':[{'path':u['path'],'start_line':u['start_line'],'end_line':u['end_line'],'role':z} for u,z in zip(selected,roles)]}
        rows.append(row)
        agg['required_groups']+=len(groups); agg['required_groups_hit']+=len(hit_groups); agg['selected']+=len(selected); agg['required_units']+=len(required_units); agg['required_units_hit']+=len(hit_required); agg['supporting']+=sup; agg['misleading']+=mis; agg['unknown']+=unk; agg['correct_abstain']+=row['correct_abstain']; agg['false_abstain']+=row['false_abstain']; agg['false_answer']+=row['false_answer']; agg['misleading_admissions']+=len(misleading); agg['tokens']+=row['tokens']
    n=len(rows)
    summary={**agg,'cases':n,'group_recall':agg['required_groups_hit']/agg['required_groups'] if agg['required_groups'] else None,'required_unit_recall':agg['required_units_hit']/agg['required_units'] if agg['required_units'] else None,'role_precision':(agg['selected']-agg['misleading']-agg['unknown'])/agg['selected'] if agg['selected'] else None,'misleading_rate':agg['misleading']/agg['selected'] if agg['selected'] else None,'tokens_mean':agg['tokens']/n if n else None}
    out={'schema':'rag-evidence-eval/v1','receipt':str(a.receipt),'cases':str(a.cases),'summary':summary,'results':rows}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(summary,indent=1))
if __name__=='__main__': raise SystemExit(main())
