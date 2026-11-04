import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('AUDIT_WORK', '/tmp/zg-audit-work')   # thư mục tạm cho pickle; không ghi vào repo
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, HERE)
import sys,re,math,subprocess,collections,types
src=open(''+HERE+'/eval_git_proxy.py').read()
from potion_lib import *
import numpy as np
T=load_table()
# reuse helper definitions from eval3 (queries, BM25 classes, tokenizers) without running its main loop
head=src.split("bm={")[0]
head=head.replace("chunks,meta,toks=pickle.load(open(S+'toks.pkl','rb')); V_full,V_np=pickle.load(open(S+'vecs.pkl','rb'))","chunks,meta,toks=corpus_chunks()[0],corpus_chunks()[1],None;V_full=V_np=None")
head=head.replace("T=load_table(); N=len(chunks); corpus_files=set(meta)","N=len(chunks); corpus_files=set(meta)")
ns={}
exec(compile("import pickle\n"+head,'h','exec'),globals())
def rrf(rank_lists,k=60):
    sc=collections.defaultdict(float)
    for lst in rank_lists:
        for r,i in enumerate(lst): sc[i]+=1.0/(k+r+1)
    return [i for i,_ in sorted(sc.items(),key=lambda x:-x[1])]
def run(size,weights):
    ch,me,_=corpus_chunks(size=size)
    global chunks,meta
    tk=[encode(c) for c in ch]
    def pool(ids):
        ids=[i for i in ids if i!=UNK]
        if not ids: return np.zeros(256,np.float32)
        v=T[ids].astype(np.float64).mean(0); n=np.linalg.norm(v); return (v/n if n>0 else v).astype(np.float32)
    V=np.stack([pool(t) for t in tk]); L=np.array([len(t) for t in tk])
    b_sub=BM25(ch,tok_std_sub); b_j=BM25(ch,tok_jieba)
    res=collections.defaultdict(list)
    def files(lst):
        seen=[]
        for i in lst:
            f=me[i]
            if f not in seen: seen.append(f)
        return seen
    for text,gold in Q:
        d=V@pool(encode(text)); s=b_sub.score(text); j=b_j.score(text)
        rk={'dense':list(np.argsort(-d)[:200]),'sub':list(np.argsort(-s)[:200]),'jieba':list(np.argsort(-j)[:200])}
        out={'dense':files(rk['dense']),'bm25_std+sub':files(rk['sub']),'bm25_jieba_like':files(rk['jieba']),'RRF_prod(dense+jieba)':files(rrf([rk['dense'],rk['jieba']]))}
        for w in weights:
            sc=collections.defaultdict(float)
            for r,i in enumerate(rk['dense']): sc[i]+=1.0/(60+r+1)
            for r,i in enumerate(rk['sub']): sc[i]+=w/(60+r+1)
            out['wRRF(dense+%.1f*std+sub)'%w]=files([i for i,_ in sorted(sc.items(),key=lambda x:-x[1])])
        for k,lst in out.items():
            hit=[1 if f in gold else 0 for f in lst[:10]]
            rr=next((1.0/(i+1) for i,h in enumerate(hit) if h),0.0)
            res[k].append((hit[0],any(hit[:5]),any(hit[:10]),rr))
    print('--- chunk size %d chars: chunks %d, tokens/chunk mean %.0f p95 %.0f max %d, >1024 tokens: %d'%(size,len(ch),L.mean(),np.percentile(L,95),L.max(),(L>1024).sum()))
    print('%-34s %7s %7s %7s %7s'%('method','Hit@1','Hit@5','Hit@10','MRR@10'))
    for k,v in res.items(): print('%-34s %7.3f %7.3f %7.3f %7.3f'%(k,*np.array(v,float).mean(0)))
    return res
run(1894,[1.0,1.5,2.0,3.0])
run(3500,[1.0,2.0])
