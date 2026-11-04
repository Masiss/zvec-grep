import json, struct, os, re, unicodedata, subprocess
import numpy as np
D=os.environ.get('POTION_DIR',os.path.expanduser('~/.zvec-grep/models/model2vec/minishlab--potion-code-16M-v2/e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b'))
R=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..','..'))  # gốc repo
def load_table():
    f=open(D+'/model.safetensors','rb'); n=struct.unpack('<Q',f.read(8))[0]; h=json.loads(f.read(n)); base=8+n
    f.seek(base); buf=f.read(h['embeddings']['data_offsets'][1])
    return np.frombuffer(buf,dtype=np.float16).reshape(h['embeddings']['shape']).astype(np.float32)
tok=json.load(open(D+'/tokenizer/tokenizer.json'))
VOCAB=tok['model']['vocab']; UNK=VOCAB.get('[UNK]')
def is_punct(ch):
    cp=ord(ch)
    if (33<=cp<=47) or (58<=cp<=64) or (91<=cp<=96) or (123<=cp<=126): return True
    return unicodedata.category(ch).startswith('P')
def is_cjk(cp):
    return (0x4E00<=cp<=0x9FFF) or (0x3400<=cp<=0x4DBF) or (0x20000<=cp<=0x2A6DF) or (0xF900<=cp<=0xFAFF) or (0x2F800<=cp<=0x2FA1F)
def normalize(t):
    out=[]
    for ch in t:
        cp=ord(ch)
        if cp==0 or cp==0xFFFD: continue
        if ch in '\t\n\r' or unicodedata.category(ch)=='Zs': out.append(' '); continue
        if unicodedata.category(ch) in ('Cc','Cf'): continue
        if is_cjk(cp): out.append(' '+ch+' ')
        else: out.append(ch)
    t=''.join(out).lower()
    t=unicodedata.normalize('NFD',t)
    return ''.join(c for c in t if unicodedata.category(c)!='Mn')
def pretokenize(t):
    words=[];cur=[]
    for ch in t:
        if ch.isspace():
            if cur: words.append(''.join(cur)); cur=[]
        elif is_punct(ch):
            if cur: words.append(''.join(cur)); cur=[]
            words.append(ch)
        else: cur.append(ch)
    if cur: words.append(''.join(cur))
    return words
_cache={}
def wordpiece(w):
    r=_cache.get(w)
    if r is not None: return r
    if len(w)>100: r=[UNK]
    else:
        pieces=[];start=0;bad=False
        while start<len(w):
            end=len(w);cur=None
            while start<end:
                s=w[start:end]
                if start>0: s='##'+s
                if s in VOCAB: cur=VOCAB[s];break
                end-=1
            if cur is None: bad=True;break
            pieces.append(cur);start=end
        r=[UNK] if bad else pieces
    _cache[w]=r;return r
def encode(text):
    ids=[]
    for w in pretokenize(normalize(text)): ids.extend(wordpiece(w))
    return ids
ID2TOK={v:k for k,v in VOCAB.items()}
def corpus_chunks(size=1894,overlap=0.15):
    files=subprocess.run(['git','ls-files','rust/crates','src'],cwd=R,capture_output=True,text=True).stdout.split()
    files=[f for f in files if f.endswith(('.rs','.ts','.tsx','.mjs','.js'))]
    chunks=[];meta=[]
    step=int(size*(1-overlap))
    for f in files:
        try: t=open(os.path.join(R,f),encoding='utf-8').read()
        except Exception: continue
        if not t.strip(): continue
        for i in range(0,max(1,len(t)),step):
            c=t[i:i+size]
            if c.strip(): chunks.append(c);meta.append(f)
            if i+size>=len(t): break
    return chunks,meta,files
