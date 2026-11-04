import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('AUDIT_WORK', '/tmp/zg-audit-work')   # thư mục tạm cho pickle; không ghi vào repo
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, HERE)
import sys,pickle,time,re,math,subprocess,collections
from potion_lib import *
import numpy as np
S=WORK+'/'
chunks,meta,toks=pickle.load(open(S+'toks.pkl','rb'))
T=load_table(); N=len(chunks)
punct_ids=set(i for t,i in VOCAB.items() if len(t)==1 and is_punct(t))
def pool(ids,drop=None):
    ids=[i for i in ids if i!=UNK and (drop is None or i not in drop)]
    if not ids: return np.zeros(256,np.float32)
    v=T[ids].astype(np.float64).mean(0); n=np.linalg.norm(v)
    return (v/n if n>0 else v).astype(np.float32)
V_full=np.stack([pool(t) for t in toks]); V_np=np.stack([pool(t,punct_ids) for t in toks])
cs=(V_full*V_np).sum(1); print('cos(full, no-punct pooled): mean %.3f p5 %.3f p50 %.3f'%(cs.mean(),*np.percentile(cs,[5,50])))
rng=np.random.default_rng(1); q=rng.choice(N,300,replace=False)
def topk(V,qidx,k):
    sc=V[qidx]@V.T; sc[np.arange(len(qidx)),qidx]=-9; return np.argsort(-sc,axis=1)[:,:k]
iu=rng.choice(N,1500,replace=False); C=V_full[iu]@V_full[iu].T; print('chunk-vector mean pairwise cosine %.3f (sd %.3f)'%(C[np.triu_indices(len(iu),1)].mean(),C[np.triu_indices(len(iu),1)].std()))
a=topk(V_full,q,10); b=topk(V_np,q,10)
j=[len(set(x)&set(y))/len(set(x)|set(y)) for x,y in zip(a,b)]; print('neighbor top-10 Jaccard full vs no-punct pooling: mean %.3f'%np.mean(j))
# int8 per-vector symmetric quantization
def q8(V): 
    s=np.abs(V).max(1,keepdims=True)/127.0; return np.round(V/s).astype(np.int8).astype(np.float32)*s
Vq=q8(V_full)
for k in (10,200):
    ref=topk(V_full,q,k); got=topk(Vq,q,k)
    print('int8 (no rescore) recall@%d vs fp32: %.4f'%(k,np.mean([len(set(x)&set(y))/k for x,y in zip(ref,got)])))
# top-1..top-10 score gaps
sc=V_full[q]@V_full.T; sc[np.arange(len(q)),q]=-9; ss=-np.sort(-sc,axis=1)[:,:12]; print('score gap top1-top10 mean %.3f; top10-top11 mean %.4f'%((ss[:,0]-ss[:,9]).mean(),(ss[:,9]-ss[:,10]).mean()))
print('quant RMS cosine err %.5f'%np.sqrt(np.mean((V_full@V_full.T)[:200,:200]-(Vq@Vq.T)[:200,:200])**2) if False else '')
err=((V_full[:300]@V_full.T)-(Vq[:300]@Vq.T)); print('RMS score error %.5f'%np.sqrt((err**2).mean()))
pickle.dump((V_full,V_np),open(S+'vecs.pkl','wb'))
