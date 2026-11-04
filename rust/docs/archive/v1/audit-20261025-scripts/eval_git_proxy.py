import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('AUDIT_WORK', '/tmp/zg-audit-work')   # thư mục tạm cho pickle; không ghi vào repo
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, HERE)
import sys,pickle,re,math,subprocess,collections
from potion_lib import *
import numpy as np
S=WORK+'/'
chunks,meta,toks=pickle.load(open(S+'toks.pkl','rb')); V_full,V_np=pickle.load(open(S+'vecs.pkl','rb'))
T=load_table(); N=len(chunks); corpus_files=set(meta)
punct_ids=set(i for t,i in VOCAB.items() if len(t)==1 and is_punct(t))
# ---- queries from git history
out=subprocess.run(['git','log','--no-merges','--format=@@%H\t%s','--name-only'],cwd=R,capture_output=True,text=True).stdout
commits=[];cur=None
for line in out.split('\n'):
    if line.startswith('@@'):
        h,s=line[2:].split('\t',1); cur={'s':s,'f':[]}; commits.append(cur)
    elif line.strip() and cur is not None: cur['f'].append(line.strip())
Q=[]
for c in commits:
    gold=sorted(set(f for f in c['f'] if f in corpus_files))
    s=re.sub(r'\(#\d+\)','',c['s']); m=re.match(r'^(\w+)(?:\(([^)]*)\))?!?:\s*(.*)$',s)
    text=(m.group(2) or '')+' '+m.group(3) if m else s
    text=text.strip()
    if m and m.group(1) in ('docs','chore','ci','build','revert','test','results'): continue
    if 1<=len(gold)<=8 and len(re.findall(r'[A-Za-z]{3,}',text))>=3: Q.append((text,set(gold)))
print('commits',len(commits),'eval queries',len(Q),'corpus files',len(corpus_files),'chunks',N)
# ---- BM25
def tok_jieba(t):
    t=t.lower(); out=[]; pos=0
    for m in re.finditer(r'[a-z][a-z0-9]*|[0-9][a-z0-9]*(?:\.[0-9]+)?',t):
        out.extend(list(t[pos:m.start()])); out.append(m.group()); pos=m.end()
    out.extend(list(t[pos:])); return out
def tok_std(t): return re.findall(r'[a-z0-9_]+',t.lower())
def subwords(w):
    parts=[]
    for p in w.split('_'):
        parts+=re.findall(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+',p)
    return [x.lower() for x in parts]
def tok_std_sub(t):
    out=[]
    for w in re.findall(r'[A-Za-z0-9_]+',t):
        out.append(w.lower()); sw=subwords(w)
        if len(sw)>1: out.extend(sw)
    return out
class BM25:
    def __init__(s,docs,tk,k1=1.2,b=0.75):
        s.tk=tk;s.k1=k1;s.b=b; s.inv=collections.defaultdict(list); s.dl=np.zeros(len(docs))
        for i,d in enumerate(docs):
            tf=collections.Counter(tk(d)); s.dl[i]=sum(tf.values())
            for t,c in tf.items(): s.inv[t].append((i,c))
        s.N=len(docs); s.avg=s.dl.mean()
    def score(s,q):
        sc=np.zeros(s.N)
        for t in set(s.tk(q)):
            p=s.inv.get(t)
            if not p: continue
            df=len(p); idf=math.log((s.N-df+0.5)/(df+0.5)+1)
            for i,c in p: sc[i]+=idf*c*(s.k1+1)/(c+s.k1*(1-s.b+s.b*s.dl[i]/s.avg))
        return sc
bm={'bm25_jieba_like':BM25(chunks,tok_jieba),'bm25_standard':BM25(chunks,tok_std),'bm25_standard+subwords':BM25(chunks,tok_std_sub)}
def qpool(text,drop=None):
    ids=[i for i in encode(text) if i!=UNK and (drop is None or i not in drop)]
    if not ids: return np.zeros(256,np.float32)
    v=T[ids].astype(np.float64).mean(0); n=np.linalg.norm(v); return (v/n if n>0 else v).astype(np.float32)
meta_arr=np.array(meta)
def file_rank(chunk_scores,depth=200):
    top=np.argsort(-chunk_scores)[:depth]; seen=[];
    for i in top:
        f=meta[i]
        if f not in seen: seen.append(f)
    return seen
def rrf(rank_lists,k=60):
    sc=collections.defaultdict(float)
    for lst in rank_lists:
        for r,i in enumerate(lst): sc[i]+=1.0/(k+r+1)
    return [i for i,_ in sorted(sc.items(),key=lambda x:-x[1])]
methods=collections.OrderedDict()
res=collections.defaultdict(list)
for text,gold in Q:
    cs={}
    cs['dense_potion']=V_full@qpool(text)
    cs['dense_potion_nopunct']=V_np@qpool(text,punct_ids)
    for k,m in bm.items(): cs[k]=m.score(text)
    ranks={k:list(np.argsort(-v)[:200]) for k,v in cs.items()}
    def to_files(chunk_list):
        seen=[]
        for i in chunk_list:
            f=meta[i]
            if f not in seen: seen.append(f)
        return seen
    fr={k:to_files(r) for k,r in ranks.items()}
    for combo in (('dense_potion','bm25_jieba_like'),('dense_potion','bm25_standard'),('dense_potion','bm25_standard+subwords'),('dense_potion_nopunct','bm25_standard+subwords')):
        name='RRF('+'+'.join(combo)+')'; fr[name]=to_files(rrf([ranks[c] for c in combo]))
    for k,lst in fr.items():
        hit=[1 if f in gold else 0 for f in lst[:10]]
        rr=next((1.0/(i+1) for i,h in enumerate(hit) if h),0.0)
        res[k].append((hit[:1]!=[] and hit[0],any(hit[:5]),any(hit[:10]),rr))
print('%-48s %7s %7s %7s %7s'%('method','Hit@1','Hit@5','Hit@10','MRR@10'))
rng=np.random.default_rng(0)
arr={k:np.array(v,float) for k,v in res.items()}
for k,a in arr.items(): print('%-48s %7.3f %7.3f %7.3f %7.3f'%(k,*a.mean(0)))
def boot(a,b,col,n=2000):
    d=a[:,col]-b[:,col]; m=[d[rng.integers(0,len(d),len(d))].mean() for _ in range(n)]; return d.mean(),np.percentile(m,[2.5,97.5])
print('\npaired bootstrap (A-B) on MRR@10 and Hit@10, 95% CI:')
pairs=[('RRF(dense_potion+bm25_jieba_like)','bm25_jieba_like'),('RRF(dense_potion+bm25_standard+subwords)','bm25_standard+subwords'),('bm25_standard','bm25_jieba_like'),('bm25_standard+subwords','bm25_standard'),('bm25_standard+subwords','bm25_jieba_like'),('dense_potion_nopunct','dense_potion'),('dense_potion','bm25_standard+subwords'),('RRF(dense_potion+bm25_standard+subwords)','dense_potion'),('RRF(dense_potion+bm25_standard+subwords)','RRF(dense_potion+bm25_jieba_like)'),('RRF(dense_potion_nopunct+bm25_standard+subwords)','RRF(dense_potion+bm25_standard+subwords)')]
for A,B in pairs:
    for col,nm in ((3,'MRR@10'),(2,'Hit@10')):
        d,ci=boot(arr[A],arr[B],col); print('%-44s - %-36s %s %+.3f [%+.3f,%+.3f]'%(A,B,nm,d,*ci))
