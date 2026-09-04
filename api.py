"""FastAPI boundary for the local evidence-backed pilot."""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT=Path(__file__).parent; RAW=ROOT/'data/evidence.jsonl'; VECTORS=ROOT/'data/vectors.jsonl'; TENANT='demo-tenant'
app=FastAPI(title='Organized Research Agent', version='0.2.0')
class Evidence(BaseModel): id:str; platform:str; source_url:str; text:str; captured_at:str; extraction_method:str; format:str; advertiser_firsthand:bool; topics:list[str]=[]
def tenant(header:str|None):
    if header!=TENANT: raise HTTPException(404,'tenant not found')
def rows():
    path=VECTORS if VECTORS.exists() else RAW
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
@app.get('/api/health')
def health(): return {'status':'ok','mode':'local-public-pilot','embedding':'sentence-transformers/all-MiniLM-L6-v2'}
@app.get('/api/evidence')
def evidence(x_demo_tenant:str|None=Header(None)):
    tenant(x_demo_tenant); data=rows(); return {'tenant':TENANT,'records':[Evidence.model_validate(x).model_dump() for x in data],'counts':{'retrieved':len(data),'advertiser_firsthand':sum(x.get('advertiser_firsthand',False) for x in data)}}
@app.get('/api/summary')
def summary(x_demo_tenant:str|None=Header(None)):
    tenant(x_demo_tenant); data=rows(); return {'coverage':{'records':len(data),'by_platform':{p:sum(x['platform']==p for x in data) for p in sorted({x['platform'] for x in data})},'historical':'insufficient historical snapshots'},'limits':['Public posts do not establish spend, ROAS, bot traffic, or causality.']}
@app.get('/')
def page(): return FileResponse(ROOT/'index.html')
app.mount('/assets', StaticFiles(directory=ROOT), name='assets')
if __name__=='__main__':
    import uvicorn; uvicorn.run(app,host='127.0.0.1',port=4173)
