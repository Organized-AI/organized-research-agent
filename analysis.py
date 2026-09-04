"""Deterministic vector math for the bounded pilot; vectors are persisted locally only."""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering

ROOT=Path(__file__).parent; RAW=ROOT/'data/evidence.jsonl'; VECTORS=ROOT/'data/vectors.jsonl'
MODEL={"provider":"sentence-transformers","model":"sentence-transformers/all-MiniLM-L6-v2","dimension":384,"note":"local pretrained semantic embedding model; normalized vectors"}

def cosine(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float); d=np.linalg.norm(a)*np.linalg.norm(b)
    return float(np.dot(a,b)/d) if d else 0.0

def analyze(rows):
    if not rows:return {"model":MODEL,"records":[],"clusters":[],"coverage":{"n":0}}
    matrix=SentenceTransformer(MODEL['model']).encode([r['text'] for r in rows], normalize_embeddings=True, show_progress_bar=False)
    assert np.isfinite(matrix).all()
    labels=np.zeros(len(rows),dtype=int) if len(rows)<3 else AgglomerativeClustering(n_clusters=min(3,len(rows)),metric='cosine',linkage='average').fit_predict(matrix)
    for r,v,label in zip(rows,matrix,labels): r['vector']=v.tolist(); r['cluster']=int(label)
    clusters=[]
    for label in sorted(set(labels)):
        group=[r for r in rows if r['cluster']==int(label)]; clusters.append({"id":int(label),"count":len(group),"unique_contributors":len({r.get('author') for r in group if r.get('author')}),"topics":sorted({t for r in group for t in r.get('topics',[])})})
    VECTORS.write_text('\n'.join(json.dumps(r) for r in rows)+'\n'); return {"model":MODEL,"records":rows,"clusters":clusters,"coverage":{"n":len(rows),"advertiser_firsthand_share":sum(r['advertiser_firsthand'] for r in rows)/len(rows),"time_series":"insufficient historical snapshots"}}

if __name__=='__main__': print(json.dumps(analyze([json.loads(x) for x in RAW.read_text().splitlines()]),indent=2))
