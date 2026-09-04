"""Loopback-only FastAPI boundary for the local evidence pilot."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from analysis import MODEL, nearest, summarize
from collect import classify

ROOT, RAW, VECTORS = Path(__file__).parent, Path(__file__).parent / "data/evidence.jsonl", Path(__file__).parent / "data/vectors.jsonl"
TENANT = "demo-tenant"
app = FastAPI(title="Organized Research Agent", version="0.3.0")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=8)
    platform: str = Field(min_length=2)
    source_url: AnyHttpUrl
    text: str = Field(min_length=1, max_length=8000)
    published_at: str | None = None
    captured_at: str
    extraction_method: str
    format: str
    metrics: dict
    author: str | None = None
    provenance: str
    live: bool
    candidate_relevant: bool
    advertiser_firsthand: bool
    topics: list[str]
    classification_reason: str
    cluster: int | None = None
    similarity: float | None = None


def tenant(header: str | None) -> None:
    if header != TENANT:
        raise HTTPException(404, "tenant not found")


def rows() -> list[dict]:
    path = VECTORS if VECTORS.exists() else RAW
    result = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    # Reclassification maintains behavior for pre-existing local material from prior collector versions.
    for item in result:
        accepted, topics, reason = classify(item["text"])
        item.update(advertiser_firsthand=accepted, topics=topics, classification_reason=reason, candidate_relevant=True)
    return result


def public_evidence(item: dict) -> dict:
    """Never return locally persisted embeddings through the evidence ledger."""
    fields = set(Evidence.model_fields)
    return Evidence.model_validate({key: value for key, value in item.items() if key in fields}).model_dump(mode="json")


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "local-public-pilot", "embedding": MODEL["model"], "dimension": MODEL["dimension"]}


@app.get("/api/evidence")
def evidence(x_demo_tenant: str | None = Header(None)):
    tenant(x_demo_tenant)
    data = rows()
    return {"tenant": TENANT, "records": [public_evidence(item) for item in data], "counts": summarize(data)}


@app.get("/api/summary")
def summary(x_demo_tenant: str | None = Header(None)):
    tenant(x_demo_tenant)
    data = rows()
    by_platform = {platform: sum(row["platform"] == platform for row in data) for platform in sorted({row["platform"] for row in data})}
    return {"coverage": {**summarize(data), "by_platform": by_platform}, "model": MODEL,
            "limits": ["Public posts do not establish spend, ROAS, bot traffic, causality, or representative market incidence.", "Raw evidence and vectors are local-only."]}


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=500), limit: int = Query(10, ge=1, le=20), x_demo_tenant: str | None = Header(None)):
    tenant(x_demo_tenant)
    data = rows()
    try:
        matches = nearest(q, data, limit=limit)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"tenant": TENANT, "query": q, "records": [public_evidence(item) for item in matches]}


@app.get("/")
def page():
    return FileResponse(ROOT / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4173)
