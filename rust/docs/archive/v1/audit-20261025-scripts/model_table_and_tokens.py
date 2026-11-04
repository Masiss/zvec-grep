import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('AUDIT_WORK', '/tmp/zg-audit-work')   # thư mục tạm cho pickle; không ghi vào repo
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, HERE)
import sys; from potion_lib import *
import numpy as np, time
t0=time.time()
T=load_table(); print('table',T.shape,T.dtype,'nan',int(np.isnan(T).sum()),'inf',int(np.isinf(T).sum()))
norms=np.linalg.norm(T,axis=1); print('row norm mean %.3f p5 %.3f p50 %.3f p95 %.3f max %.3f zero_rows %d'%(norms.mean(),*np.percentile(norms,[5,50,95]),norms.max(),(norms==0).sum()))
# punctuation vs word rows
punct_ids=[i for t,i in VOCAB.items() if len(t)==1 and is_punct(t)]
cont_ids=[i for t,i in VOCAB.items() if t.startswith('##')]
print('vocab',len(VOCAB),'single-char punct',len(punct_ids),'## pieces',len(cont_ids))
print('mean norm punct %.3f, ## %.3f, others %.3f'%(norms[punct_ids].mean(),norms[cont_ids].mean(),np.delete(norms,punct_ids+cont_ids).mean()))
# anisotropy of token rows
rng=np.random.default_rng(0); idx=rng.choice(len(T),4000,replace=False); S=T[idx]/np.maximum(norms[idx,None],1e-9)
C=S@S.T; iu=np.triu_indices(len(S),1); print('token-row mean pairwise cosine %.3f sd %.3f'%(C[iu].mean(),C[iu].std()))
# effective dimensionality
U,s,Vt=np.linalg.svd(T[idx]-T[idx].mean(0),full_matrices=False); e=s**2/np.sum(s**2); print('PCA top1 %.3f top5 %.3f top10 %.3f top50 %.3f; participation ratio %.1f of 256'%(e[0],e[:5].sum(),e[:10].sum(),e[:50].sum(),1/np.sum(e**2)))
mean_dir=T[idx].mean(0); print('norm of mean token vector %.3f vs avg row norm %.3f'%(np.linalg.norm(mean_dir),norms.mean()))
chunks,meta,files=corpus_chunks(); print('files',len(files),'chunks',len(chunks))
toks=[encode(c) for c in chunks]
L=np.array([len(x) for x in toks]); cpt=np.array([len(c) for c in chunks])/np.maximum(L,1)
print('tokens/chunk mean %.0f p50 %.0f p95 %.0f max %d; chars/token mean %.2f p5 %.2f'%(L.mean(),np.median(L),np.percentile(L,95),L.max(),cpt.mean(),np.percentile(cpt,5)))
print('chunks >1024 tokens:',int((L>1024).sum()),'(%.2f%%)'%(100*(L>1024).mean()))
allt=np.concatenate([np.array(x) for x in toks]); 
isp=np.zeros(len(VOCAB)+5,bool); isp[punct_ids]=True; isc=np.zeros_like(isp); isc[cont_ids]=True
print('token share: unk %.2f%%, punct %.1f%%, ## continuation %.1f%%, other word %.1f%%'%(100*(allt==UNK).mean(),100*isp[allt].mean(),100*isc[allt].mean(),100*(1-isp[allt].mean()-isc[allt].mean()-(allt==UNK).mean())))
print('time %.0fs'%(time.time()-t0))
import pickle; pickle.dump((chunks,meta,toks),open(WORK+'/toks.pkl','wb'))
