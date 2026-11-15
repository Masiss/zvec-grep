#!/usr/bin/env python3
"""Adversarial tests for evaluation contract v2 semantics."""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    td=Path(tempfile.mkdtemp(prefix='gs-eval-')); src=td/'src.cs'; src.write_text('\n'.join(f'line {i}' for i in range(1,30))+'\n')
    def u(s,e,role,kind): return {'path':str(src),'start_line':s,'end_line':e,'role':role,'entity_kind':kind}
    cases={'cases':[
      {'id':'E1','question':'x','answerable':True,'expected_abstain':False,'required_groups':[], 'units':[], 'claims':[]},
      {'id':'E2','question':'y','answerable':True,'expected_abstain':False,
       'required_groups':[{'id':'g','role':'REQUIRED','description':'p','any_of':[u(1,2,'REQUIRED','PROPERTY')]}],
       'units':[u(1,2,'REQUIRED','PROPERTY')], 'claims':[]},
      {'id':'E3','question':'z','answerable':True,'expected_abstain':False,
       'required_groups':[{'id':'ev','role':'REQUIRED','description':'m','any_of':[u(10,12,'REQUIRED','METHOD')]}],
       'units':[u(10,12,'REQUIRED','METHOD')],
       'claims':[{'id':'c','text':'x does not call sp_X','status':'REFUTED','evidence_groups':['ev'],'negative':True}]},
      {'id':'E4','question':'w','answerable':True,'expected_abstain':False,
       'required_groups':[{'id':'ev','role':'REQUIRED','description':'m','any_of':[u(10,12,'REQUIRED','METHOD')]}],
       'units':[u(10,12,'REQUIRED','METHOD')],
       'claims':[{'id':'c','text':'x does not call sp_X','status':'REFUTED','evidence_groups':['ev'],'negative':True,'negative_basis':'closed_world'}]},
    ]}
    receipt={'results':[
      {'id':'E1','abstain':False,'units':[u(20,21,'UNKNOWN','TYPE')]},
      {'id':'E2','abstain':False,'units':[u(1,2,'VIEW','VIEW')]},
      {'id':'E3','abstain':False,'units':[u(10,12,'CONTROL','METHOD')]},
      {'id':'E4','abstain':False,'units':[u(10,12,'CONTROL','METHOD')],'negative_basis':'method_body_complete','negative_scope':{'path':str(src),'start':10,'end':12,'dynamic_sql':False}},
    ]}
    cp=td/'cases.json'; rp=td/'receipt.json'; op=td/'eval.json'
    cp.write_text(json.dumps(cases)); rp.write_text(json.dumps(receipt))
    r=subprocess.run([sys.executable,str(Path(__file__).parent/'evaluate_gq_v2_20261115.py'),'--receipt',str(rp),'--cases',str(cp),'--out',str(op)],capture_output=True,text=True)
    if r.returncode: print(r.stderr); return 1
    d=json.loads(op.read_text()); rows={x['id']:x for x in d['results']}
    checks=[
      ('non_gold_unknown', rows['E1']['unknown']==1 and rows['E1']['misleading']==0),
      ('wrong_entity_kind_separate', rows['E2']['wrong_kind']==1 and rows['E2']['misleading']==0),
      ('open_world_negative_not_safe', rows['E3']['negative_safe']==0 and rows['E3']['unsupported_acceptance']>=1),
      ('closed_world_negative_safe', rows['E4']['negative_safe']==1 and rows['E4']['unsupported_acceptance']==0),
      ('negative_safety_metric', d['summary']['negative_safe']==1 and d['summary']['negative_claims']==2),
    ]
    out={'schema':'rag-gs-eval-contract-test/v1','checks':[{'name':n,'pass':bool(v)} for n,v in checks], 'PASS':all(v for _,v in checks)}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'PASS':out['PASS'],'checks':len(checks)},indent=1)); return 0 if out['PASS'] else 1
if __name__=='__main__': raise SystemExit(main())
