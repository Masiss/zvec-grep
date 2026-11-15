#!/usr/bin/env python3
"""Evidence-role grounding prototype.

The policy sees only the question, frozen source and an independently produced
SQL-binding artifact. It does not read evaluation roles, required groups or
answerability labels.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from refint_20261115 import RefIndex, qterms, intent_of, SQL_RE, blocks
from selection_s1_20261115 import Source, code_tokens, run_counter, sha256_file, trim_tokens

def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())

def overlaps(a,b): return a['path']==b['path'] and a['start_line']<=b['end_line'] and b['start_line']<=a['end_line']

def analyze(question, refs, binds, source_ref):
    qt=set(qterms(question)); intent=intent_of(question); qlow=question.lower()
    ct=[x for x in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', question) if len(x)>=4 and (x[0].isupper() or '_' in x or any(c.isupper() for c in x[1:]))]
    # exact declarations named in the question
    methods=[]; types=[]; sqls=[]; props=[]
    for name in ct:
        methods += [m for m in refs.method_by.get(name,[]) if name.lower() in m['name'].lower() or m['name'].lower()==name.lower()]
        types += [t for t in refs.type_by.get(name,[]) if t['name'].lower()==name.lower()]
        props += [p for p in refs.prop_by_name.get(name,[]) if p['name'].lower()==name.lower() and (p.get('is_dbset') or p['parent'] in ('CoreDBContext','HRMLocalContext'))]
    # SQL names are case-insensitive and may be prefixed
    for s in refs.sqls:
        sn=norm(s['name'].replace('sp_','')); 
        if any(norm(x).replace('sp','')==sn or norm(x)==norm(s['name']) for x in ct):
            sqls.append(s)
    # explicit sp_ token is a hard SQL symbol even if code_tokens drops it
    sp=re.findall(r'\bsp_[A-Za-z0-9_]+', question)
    for name in sp:
        for s in refs.sqls:
            if norm(s['name'])==norm(name): sqls.append(s)
    unknown_sql = bool(sp) and not sqls
    # view exact name
    views=[s for s in refs.sqls if re.search(r'CREATE\s+VIEW', s.get('text',''), re.I) and any(norm(x)==norm(s['name']) or norm(x) in norm(s['name']) for x in ct)]
    negative = bool(re.search(r'(?:does|có|co)\b.{0,80}\b(?:invoke|call|goi|gọi|use|dùng|dung)\b|\b(?:không|khong)\s*\?', qlow, re.I)) and not re.search(r'only through|indirect|gián tiếp|gian tiep', qlow, re.I)
    wants_property = bool(re.search(r'\b(property|dbset|dbcontext|entity|maps?|type)\b', qlow, re.I))
    wants_sql = (bool(sp) or ('sql' in intent) or bool(re.search(r'\b(procedure|proc|sql|table|bảng)\b', qlow, re.I))) and not wants_property and not (negative and methods)
    if wants_property and not re.search(r'\b(write|writes|saved?|update|delete|create|read|call|invoke|method|function|gọi)\b', qlow, re.I):
        methods = []
    if negative and methods:
        sqls = []
    # verified bindings by source key
    bmap={}
    for b in binds.get('bindings',[]):
        if b['state']=='VERIFIED':
            k=(b['source']['path'],b['source']['start'],b['source']['end']); bmap.setdefault(k,[]).append(b)
    cands=[]
    def add(path,start,end,role,score,source,chain=None):
        if start<1 or end<start: return
        cands.append({'path':path,'start_line':start,'end_line':end,'last_line':end,'role':role,'score':score,'source':source,'chain':chain or []})
    # Exact type -> DbSet/entity properties and their type declarations.
    if wants_property or (types and not methods and not sqls):
        for t in types:
            add(t['path'],t['start'],t['end'],'TYPE',3.0,{'type':t['name']})
            for p in refs.props:
                if p.get('is_dbset') and p['type']==t['name'] and p['parent'] in ('CoreDBContext','HRMLocalContext'):
                    add(p['path'],p['start'],p['end'],'PROPERTY',3.1,{'property':p['name'],'type':t['name']})
        for p in props:
            add(p['path'],p['start'],p['end'],'PROPERTY',3.1,{'property':p['name']})
            for t in refs.type_target(p['type']):
                add(t['path'],t['start'],t['end'],'TYPE',3.0,{'type':t['name']})
    # Pick one primary declaration per exact method name: a bound service
    # implementation outranks a thin controller wrapper; otherwise the larger
    # body is the implementation.
    if methods:
        maxlen=max(len(m['name']) for m in methods)
        methods=[m for m in methods if len(m['name'])>=max(5,int(maxlen*0.7))]
    primary={}
    for m in methods:
        key=(m['path'],m['start'],m['end']); bs=bmap.get(key,[])
        rank=(1 if bs else 0, m['end']-m['start']+1, 1 if '/Services/' in m['path'] else 0)
        if m['name'] not in primary or rank>primary[m['name']][0]: primary[m['name']]=(rank,m,bs)
    for name,(rank,m,bs) in primary.items():
        if (not wants_sql) and (m['end']-m['start']+1)>80:
            lines=source_ref.text_lines(m['path'])
            saves=[i+1 for i in range(m['start']-1,m['end']) if 'SaveChanges' in lines[i]]
            if saves:
                prev=m['start']-1
                for sv in saves:
                    if sv>prev+1:
                        a0,b0=prev+1,sv
                        if b0-a0+1>80:
                            add(m['path'],a0,a0+70,'CONTROL',3.35,{'method':m['name'],'window':'savechanges_half'})
                            add(m['path'],b0-70,b0,'CONTROL',3.30,{'method':m['name'],'window':'savechanges_half'})
                        else:
                            add(m['path'],a0,b0,'CONTROL',3.35,{'method':m['name'],'window':'savechanges'})
                    prev=sv
            bl = blocks(source_ref, m['path'], m['start'], m['end'], 4)
            for idx,(bs_,be_) in enumerate(bl):
                text='\n'.join(source_ref.text_lines(m['path'])[bs_-1:be_])
                bonus=0.5 if idx==0 else 0.0
                if 'SaveChanges' in text: bonus += 0.4
                if 'EmployeeDetails' in text: bonus += 0.2
                add(m['path'],bs_,be_,'CONTROL',2.7+bonus,{'method':m['name']})
        else:
            add(m['path'],m['start'],m['end'],'CONTROL',3.0,{'method':m['name']})
        if wants_sql:
            for b in bs[:4]:
                for mid in b['chain'][1:]:
                    for mm in refs.method_by.get(mid,[]):
                        if mm.get('parent')==m.get('parent'):
                            add(mm['path'],mm['start'],mm['end'],'WRAPPER',2.75,{'chain_method':mid},b['chain'])
                for t in b['targets']:
                    role='SQL' if t['path'].endswith('.sql') else 'DATA'
                    add(t['path'],t['start'],t['end'],role,2.8,{'binding':b['reason']},b['chain'])
    for s in sqls:
        add(s['path'],s['start'],s['end'],'SQL',3.0,{'sql':s['name']})
        for b in binds.get('bindings',[]):
            if b['state']=='VERIFIED' and b['proc']==s['name'] and b.get('depth',0)==0:
                add(b['source']['path'],b['source']['start'],b['source']['end'],'WRAPPER',2.7,{'binding':b['reason']},b['chain'])
    for s in views:
        add(s['path'],s['start'],s['end'],'VIEW',3.0,{'sql':s['name']})
    # de-duplicate exact ranges, prefer stronger role
    rank={'SQL':5,'VIEW':5,'TYPE':4,'PROPERTY':4,'CONTROL':4,'WRAPPER':3,'DATA':1}
    uniq={}
    for c in cands:
        k=(c['path'],c['start_line'],c['end_line'])
        if k not in uniq or rank.get(c['role'],0)>rank.get(uniq[k]['role'],0): uniq[k]=c
    cands=list(uniq.values())
    # relevance filters by question intent; do not add entity noise
    if wants_property and not sqls and not views:
        cands=[c for c in cands if c['role'] in ('TYPE','PROPERTY','CONTROL','DATA')]
    elif sqls or views or ('sql' in intent):
        keep=('SQL','VIEW','WRAPPER','CONTROL') if methods else ('SQL','VIEW','WRAPPER')
        cands=[c for c in cands if c['role'] in keep]
    elif types or props:
        cands=[c for c in cands if c['role'] in ('TYPE','PROPERTY','CONTROL','DATA')]
    elif methods:
        cands=[c for c in cands if c['role'] in ('CONTROL','WRAPPER','SQL','DATA')]
    else:
        cands=[]
    abstain = (not cands) and (unknown_sql or not methods)
    return {'intent':sorted(intent),'code_tokens':ct,'unknown_sql':unknown_sql,'methods':[m['name'] for m in methods],'sqls':[s['name'] for s in sqls],'views':[s['name'] for s in views],'properties':[p['name'] for p in props],'candidates':cands,'abstain':abstain}

def select(analysis, source, counter, tokenizer, budget):
    c=[x for x in analysis['candidates'] if x['role'] in ('SQL','VIEW','TYPE','PROPERTY','WRAPPER','CONTROL','DATA')]
    c.sort(key=lambda x:(-x['score'], -int(x['role'] in ('SQL','VIEW','WRAPPER')), x['end_line']-x['start_line']+1, x['path']))
    out=[]; seen=[]
    for x in c:
        if any(overlaps(x,y) and x['path']==y['path'] for y in out): continue
        out.append(x)
        if len(out)>=4: break
    if out:
        counts=run_counter(counter, tokenizer, [source.pack_text([x]) for x in out])
        for x,n in zip(out,counts): x['tokens']=n
        while out and sum(x['tokens'] for x in out)>budget: out.pop()
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--cases',type=Path,required=True); ap.add_argument('--bindings',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--token-counter',type=Path,required=True); ap.add_argument('--tokenizer',type=Path,required=True); ap.add_argument('--budget',type=int,default=3000); a=ap.parse_args()
    refs=RefIndex(a.root); source=Source(a.root); binds=json.loads(a.bindings.read_text()); rows=[]
    for case in json.loads(a.cases.read_text())['cases']:
        an=analyze(case['question'],refs,binds,source); chosen=select(an,source,a.token_counter,a.tokenizer,a.budget)
        rows.append({'id':case['id'],'question':case['question'],'abstain':bool(an['abstain'] or not chosen),'unknown_sql':an['unknown_sql'],'intent':an['intent'],'units':chosen,'candidate_count':len(an['candidates'])})
    out={'schema':'rag-evidence-grounding/v1','policy':'reference-binding-grounding','config':{'budget':a.budget,'uses_roles_in_runtime':False,'uses_gold_in_runtime':False},'identity':{'cases_sha256':sha256_file(a.cases),'bindings_sha256':sha256_file(a.bindings),'corpus_manifest_sha256':sha256_file(a.root/'.zvec-grep'/'manifest.json')},'results':rows}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,ensure_ascii=False,indent=1)+'\n'); print(json.dumps({'cases':len(rows),'abstain':sum(r['abstain'] for r in rows),'units':sum(len(r['units']) for r in rows)},indent=1))
if __name__=='__main__': raise SystemExit(main())
