#!/usr/bin/env python3
"""Independent GQ evaluator: roles, required groups, claims and abstention."""
import argparse,json
from pathlib import Path

def iou(a,b):
    if a['path']!=b['path']: return 0
    return max(0,min(a['end_line'],b['end_line'])-max(a['start_line'],b['start_line'])+1)
def covers(a,b,t=0.5): return iou(a,b)/(b['end_line']-b['start_line']+1)>=t
def role_of(u,labels):
    hs=[x for x in labels if iou(u,x)>0]
    if not hs: return 'UNKNOWN'
    rank={'REQUIRED':4,'SUPPORTING':3,'MISLEADING':2,'OPTIONAL':1,'IRRELEVANT':0}
    return max(hs,key=lambda x:rank.get(x['role'],0))['role']
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    cases={c['id']:c for c in json.loads(a.cases.read_text())['cases']}; rec=json.loads(a.receipt.read_text()); rows=[]
    agg={k:0 for k in ['groups','groups_hit','required_units','required_units_hit','selected','misleading','unknown','correct_abstain','false_abstain','false_answer','claims','claim_supported','claim_unsafe','negative_claims','negative_safe','unsupported_acceptance','tokens']}
    for r in rec['results']:
        c=cases[r['id']]; labels=c['units']; units=r['units']; roles=[role_of(u,labels) for u in units]
        hitg=[]
        for g in c.get('required_groups',[]):
            if any(covers(u,x) for u in units for x in g['any_of']): hitg.append(g['id'])
        ru=[u for u in labels if u['role']=='REQUIRED']; hur=[u for u in ru if any(covers(s,u) for s in units)]
        mis=[u for u,z in zip(units,roles) if z=='MISLEADING']; unk=sum(z=='UNKNOWN' for z in roles)
        claim_results=[]
        for cl in c.get('claims',[]):
            groups={g['id']:g for g in c.get('required_groups',[])}
            all_hit=all(any(covers(u,x) for u in units for x in groups[gid]['any_of']) for gid in cl.get('evidence_groups',[]) if gid in groups)
            if cl['status']=='SUPPORTED':
                cs='SUPPORTED' if all_hit else 'INSUFFICIENT'
            elif cl['status']=='REFUTED':
                cs='UNSAFE_REFUTATION' if mis else ('REFUTED_SUPPORTED' if all_hit else 'INSUFFICIENT')
            elif cl['status']=='INSUFFICIENT':
                cs='UNSAFE_ANSWER' if (units and mis) else ('CORRECT_INSUFFICIENT' if r['abstain'] else 'INSUFFICIENT')
            else:
                cs='CORRECT_UNKNOWN' if r['abstain'] else 'UNSAFE_ANSWER'
            claim_results.append({'id':cl['id'],'expected':cl['status'],'evidence':cs,'required_groups':cl.get('evidence_groups',[]),'all_groups_hit':all_hit})
        failure=[]
        if c.get('expected_abstain') and not r['abstain']: failure.append('INCORRECT_ABSTENTION')
        if (not c.get('expected_abstain')) and r['abstain']: failure.append('FALSE_ABSTENTION')
        if mis: failure.append('MISLEADING_ADMISSION')
        if len(hitg)<len(c.get('required_groups',[])): failure.append('MISSING_REQUIRED_GROUP')
        if any(x['expected']=='SUPPORTED' and x['evidence']=='INSUFFICIENT' and not r['abstain'] for x in claim_results): failure.append('UNSUPPORTED_EVIDENCE')
        if any(x['expected']=='REFUTED' and x['evidence']=='UNSAFE_REFUTATION' for x in claim_results): failure.append('NEGATIVE_CLAIM_UNSAFE')
        row={'id':r['id'],'abstain':r['abstain'],'required_groups':len(c.get('required_groups',[])),'groups_hit':len(hitg),'group_recall':len(hitg)/len(c['required_groups']) if c.get('required_groups') else None,'required_units':len(ru),'required_units_hit':len(hur),'selected':len(units),'misleading':len(mis),'unknown':unk,'correct_abstain':r['abstain']==c.get('expected_abstain'),'false_abstain':(not c.get('expected_abstain') and r['abstain']),'false_answer':(c.get('expected_abstain') and not r['abstain']),'claims':claim_results,'failure':failure,'tokens':sum(u.get('tokens',0) for u in units),'units':[{'path':u['path'],'start_line':u['start_line'],'end_line':u['end_line'],'role':z} for u,z in zip(units,roles)]}
        rows.append(row)
        agg['groups']+=len(c.get('required_groups',[])); agg['groups_hit']+=len(hitg); agg['required_units']+=len(ru); agg['required_units_hit']+=len(hur); agg['selected']+=len(units); agg['misleading']+=len(mis); agg['unknown']+=unk; agg['correct_abstain']+=row['correct_abstain']; agg['false_abstain']+=row['false_abstain']; agg['false_answer']+=row['false_answer']; agg['claims']+=len(claim_results)
        agg['claim_supported']+=sum(x['evidence'] in ('SUPPORTED','REFUTED_SUPPORTED','CORRECT_INSUFFICIENT','CORRECT_UNKNOWN') for x in claim_results)
        agg['claim_unsafe']+=sum(x['evidence'].startswith('UNSAFE') for x in claim_results)
        for x in claim_results:
            if x['expected']=='REFUTED': agg['negative_claims']+=1; agg['negative_safe']+=x['evidence']=='REFUTED_SUPPORTED'
        agg['unsupported_acceptance']+=sum(x['evidence']=='INSUFFICIENT' and not r['abstain'] for x in claim_results); agg['tokens']+=row['tokens']
    n=len(rows); summary={**agg,'cases':n,'group_recall':agg['groups_hit']/agg['groups'] if agg['groups'] else None,'required_unit_recall':agg['required_units_hit']/agg['required_units'] if agg['required_units'] else None,'role_precision':(agg['selected']-agg['misleading']-agg['unknown'])/agg['selected'] if agg['selected'] else None,'misleading_rate':agg['misleading']/agg['selected'] if agg['selected'] else None,'token_mean':agg['tokens']/n if n else None}
    out={'schema':'rag-gq-eval/v1','receipt':str(a.receipt),'cases':str(a.cases),'summary':summary,'results':rows}; a.out.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(summary,indent=1))
if __name__=='__main__': raise SystemExit(main())
