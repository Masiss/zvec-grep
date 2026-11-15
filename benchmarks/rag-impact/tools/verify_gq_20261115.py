#!/usr/bin/env python3
"""Independent GQ verifier: dataset, candidate identity, binding validity, metrics."""
import argparse,json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from selection_s1_20261115 import Source, sha256_file

def iou(a,b):
    if a['path']!=b['path']: return 0
    return max(0,min(a['end_line'],b['end_line'])-max(a['start_line'],b['start_line'])+1)
def covers(a,b,t=.5): return iou(a,b)/(b['end_line']-b['start_line']+1)>=t
def role(u,labels):
    hs=[x for x in labels if iou(u,x)>0]
    if not hs: return 'UNKNOWN'
    rank={'REQUIRED':4,'SUPPORTING':3,'MISLEADING':2,'OPTIONAL':1,'IRRELEVANT':0}
    return max(hs,key=lambda x:rank.get(x['role'],0))['role']
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--bindings',type=Path,required=True); ap.add_argument('--eval',type=Path,required=True); ap.add_argument('--freeze',type=Path,required=True); ap.add_argument('--gates',type=Path,required=True); ap.add_argument('--sanitized',type=Path); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    src=Source(a.root); cases={c['id']:c for c in json.loads(a.cases.read_text())['cases']}; rec=json.loads(a.receipt.read_text()); binds=json.loads(a.bindings.read_text()); ev=json.loads(a.eval.read_text()); freeze=json.loads(a.freeze.read_text()); gates=json.loads(a.gates.read_text())['gates']; fails=[]; checks=0
    def ok(c,m):
        nonlocal checks; checks+=1
        if not c: fails.append(m)
    # candidate identity
    ok(freeze['frozen'] is True,'candidate freeze not frozen')
    ok(rec['identity']['bindings_sha256']==sha256_file(a.bindings),'candidate receipt binding identity mismatch')
    ok(rec['identity']['cases_sha256']==sha256_file(a.cases),'candidate receipt case identity mismatch')
    ok(freeze['frozen_bindings_sha256']==sha256_file(a.bindings),'frozen binding identity mismatch')
    # dataset source identity and claims
    for c in cases.values():
        ok(c.get('frozen') is True,f'{c["id"]} not frozen')
        labels=c.get('units',[])
        for u in labels:
            p=a.root/u['path']; ok(p.is_file(),f'{c["id"]} missing label source')
            if p.is_file(): ok(1<=u['start_line']<=u['end_line']<=len(src.text_lines(u['path'])),f'{c["id"]} label range')
            ok(u.get('role') in ('REQUIRED','SUPPORTING','OPTIONAL','IRRELEVANT','MISLEADING'),f'{c["id"]} role')
        for g in c.get('required_groups',[]):
            for x in g.get('any_of',[]): ok(any(iou(x,u)>0 for u in labels),f'{c["id"]} group unit not labeled')
        for cl in c.get('claims',[]):
            ok(cl.get('status') in ('SUPPORTED','REFUTED','INSUFFICIENT','UNKNOWN','CONFLICTING'),f'{c["id"]} claim status')
            ok(cl.get('adjudication',c.get('adjudication')) in ('VERIFIED','DISPUTED','UNKNOWN','machine-assisted source review'),f'{c["id"]} claim adjudication')
    # binding validity + comment blindness
    counts={'VERIFIED':0,'AMBIGUOUS':0,'UNRESOLVED':0}; false_commented=[]
    for b in binds.get('bindings',[]):
        counts[b['state']]=counts.get(b['state'],0)+1
        s=b['source']; text='\n'.join(src.text_lines(s['path'])[s['start']-1:s['end']])
        ok(text.strip()!='', 'empty binding source')
        if b['reason'].startswith('explicit'):
            ok(b['state']=='VERIFIED' and re.search(r'\bexec(?:ute)?\s+(?:\[?dbo\]?\.*)?\[?'+re.escape(b['proc']),text,re.I) is not None,f'{s["name"]} false explicit binding')
        if b['reason'].startswith('wrapper'):
            ok(len(b['chain'])>=2,'short wrapper chain')
            # any named chain member whose declaration starts with // is not executable
            for name in b['chain'][1:]:
                decl=[i for i,line in enumerate(src.text_lines(s['path']),1) if re.search(r'\b'+re.escape(name)+r'\s*\(',line)]
                if decl:
                    line=src.text_lines(s['path'])[decl[0]-1]
                    if line.lstrip().startswith('//') or line.lstrip().startswith('*'):
                        false_commented.append({'source':s['name'],'chain_member':name,'line':decl[0],'proc':b['proc']})
        for t in b.get('targets',[]): ok((a.root/t['path']).is_file(),'binding target missing')
    ok(not false_commented,'false VERIFIED SQL binding through commented method: '+json.dumps(false_commented[:2]))
    # metrics recompute
    selected_total=groups_total=groups_hit=mis=unk=0; claim_total=claim_supported=0; neg_total=neg_safe=0
    for r in rec['results']:
        c=cases[r['id']]; labels=c['units']; units=r['units']; selected_total+=len(units)
        for u in units: ok(1<=u['start_line']<=u['end_line']<=len(src.text_lines(u['path'])),f'{r["id"]} selected range')
        for g in c.get('required_groups',[]): groups_total+=1; groups_hit+=any(covers(u,x) for u in units for x in g['any_of'])
        mis+=sum(role(u,labels)=='MISLEADING' for u in units); unk+=sum(role(u,labels)=='UNKNOWN' for u in units)
        for cl in c.get('claims',[]):
            groups={g['id']:g for g in c.get('required_groups',[])}
            allhit=all(any(covers(u,x) for u in units for x in groups[gid]['any_of']) for gid in cl.get('evidence_groups',[]) if gid in groups)
            claim_total+=1
            if cl['status']=='SUPPORTED': claim_supported+=allhit
            elif cl['status']=='REFUTED':
                neg_total+=1; safe=bool(not any(role(u,labels)=='MISLEADING' for u in units) and allhit); neg_safe+=safe; claim_supported+=safe
            elif cl['status']=='INSUFFICIENT': claim_supported+=bool(r['abstain'] and not units)
            else: claim_supported+=bool(r['abstain'])
        ok(r['abstain']==c['expected_abstain'],f'{r["id"]} abstention mismatch')
    s=ev['summary']; ok(s['selected']==selected_total,'eval selected arithmetic'); ok(s['groups']==groups_total,'eval group denominator'); ok(s['groups_hit']==groups_hit,'eval group arithmetic'); ok(s['misleading']==mis,'eval misleading arithmetic'); ok(s['claims']==claim_total,'eval claim denominator'); ok(s['claim_supported']==claim_supported,'eval claim supported arithmetic'); ok(s['negative_safe']==neg_safe,'eval negative safety arithmetic')
    # gate evaluation (not used to alter labels)
    gate={'required_group_recall_dev':None,'required_group_recall_heldout':None,'role_precision':s['role_precision'],'misleading_admissions':s['misleading'],'correct_abstention':s['correct_abstain'],'negative_claim_safety':(neg_safe/neg_total if neg_total else None)}
    ok(gate['misleading_admissions']<=gates['misleading_admissions_max'],'misleading admission gate')
    ok(gate['role_precision'] is None or gate['role_precision']>=gates['role_precision'],'role precision gate')
    if neg_total: ok(gate['negative_claim_safety']>=gates['negative_claim_safety'],'negative claim safety gate')
    # sanitized
    if a.sanitized:
        san=json.loads(a.sanitized.read_text())
        def sig(d): return {r['id']:[(u['path'],u['start_line'],u['end_line'],u['role']) for u in r['units']] for r in d['results']}
        ok(sig(rec)==sig(san),'sanitized runtime differs')
    out={'schema':'rag-gq-verify/v1','checks':checks,'failures':fails,'binding_counts':counts,'false_commented_bindings':false_commented,'selected':selected_total,'groups_hit':groups_hit,'groups':groups_total,'misleading':mis,'unknown':unk,'claims':claim_total,'claims_supported':claim_supported,'negative_safe':neg_safe,'negative_claims':neg_total,'PASS':not fails}
    a.out.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=1)); return 0 if not fails else 1
if __name__=='__main__': raise SystemExit(main())
