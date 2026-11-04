"""MD-09: tách camelCase trước khi đưa văn bản vào embedder (potion) có cải thiện dense/hybrid không.
Chạy: python3 -I eval_camel_split.py   (cần potion_lib.py; dùng proxy git-history của eval_git_proxy.py)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.dont_write_bytecode = True
import re, math, subprocess, collections, pickle
from potion_lib import *
import numpy as np
T = load_table()
src = open(HERE + '/eval_git_proxy.py').read()
head = src.split("bm={")[0]
head = head.replace("chunks,meta,toks=pickle.load(open(S+'toks.pkl','rb')); V_full,V_np=pickle.load(open(S+'vecs.pkl','rb'))",
                    "chunks,meta=corpus_chunks()[0],corpus_chunks()[1];toks=None;V_full=V_np=None")
head = head.replace("T=load_table(); N=len(chunks); corpus_files=set(meta)", "N=len(chunks); corpus_files=set(meta)")
exec(compile("import pickle\n" + head, 'h', 'exec'), globals())

def rrf(lists, k=60):
    sc = collections.defaultdict(float)
    for l in lists:
        for r, i in enumerate(l): sc[i] += 1.0 / (k + r + 1)
    return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])]

CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
def split_camel(t): return CAMEL.sub(' ', t)
def pool(text):
    ids = [i for i in encode(text) if i != UNK]
    if not ids: return np.zeros(256, np.float32)
    v = T[ids].astype(np.float64).mean(0); n = np.linalg.norm(v)
    return (v / n if n > 0 else v).astype(np.float32)

V0 = np.stack([pool(c) for c in chunks])
V1 = np.stack([pool(split_camel(c)) for c in chunks])
b_sub = BM25(chunks, tok_std_sub)
res = collections.defaultdict(list)
def files(lst):
    seen = []
    for i in lst:
        f = meta[i]
        if f not in seen: seen.append(f)
    return seen
for text, gold in Q:
    r0 = list(np.argsort(-(V0 @ pool(text)))[:200]); r1 = list(np.argsort(-(V1 @ pool(split_camel(text))))[:200])
    rs = list(np.argsort(-b_sub.score(text))[:200])
    out = {'dense': files(r0), 'dense+camel_split': files(r1),
           'RRF(dense+std+sub)': files(rrf([r0, rs])), 'RRF(dense+camel_split+std+sub)': files(rrf([r1, rs]))}
    for k, lst in out.items():
        hit = [1 if f in gold else 0 for f in lst[:10]]
        rr = next((1.0 / (i + 1) for i, h in enumerate(hit) if h), 0.0)
        res[k].append((hit[0], any(hit[:5]), any(hit[:10]), rr))
arr = {k: np.array(v, float) for k, v in res.items()}
print('%-34s %7s %7s %7s %7s' % ('method', 'Hit@1', 'Hit@5', 'Hit@10', 'MRR@10'))
for k, a in arr.items(): print('%-34s %7.3f %7.3f %7.3f %7.3f' % (k, *a.mean(0)))
rng = np.random.default_rng(0)
def boot(a, b, col, n=2000):
    d = a[:, col] - b[:, col]; m = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)]
    return d.mean(), np.percentile(m, [2.5, 97.5])
for A, B in (('dense+camel_split', 'dense'), ('RRF(dense+camel_split+std+sub)', 'RRF(dense+std+sub)')):
    for col, nm in ((3, 'MRR@10'), (2, 'Hit@10')):
        d, ci = boot(arr[A], arr[B], col); print('%-32s - %-20s %s %+.3f [%+.3f,%+.3f]' % (A, B, nm, d, *ci))
