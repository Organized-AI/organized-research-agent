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

# Synter's documented footprint, captured 2026-09-04 from
# https://docs.syntermedia.ai/integrations. This is an inventory, not a claim
# that this local pilot can read an account or collect public evidence from it.
SYNTER_INTEGRATIONS = (
    ("Google Ads", "paid search", "live", "authorized account required"),
    ("Microsoft Ads", "paid search", "live", "authorized account required"),
    ("Search Ads 360", "paid search", "marketing/MCP listed; not detailed in integrations guide", "authorized account required"),
    ("Apple Search Ads", "paid search", "marketing/MCP listed; not detailed in integrations guide", "authorized account required"),
    ("Meta Ads (Facebook, Instagram, Messenger, Audience Network)", "paid social", "live", "authorized account required; public discussion adapter not enabled"),
    ("LinkedIn Ads", "paid social", "live", "authorized account required; public-post pilot coverage"),
    ("Reddit Ads", "paid social", "live", "authorized account required; normal public-page coverage is partial"),
    ("TikTok Ads", "paid social", "live", "authorized account required; public search currently login-limited"),
    ("X Ads", "paid social", "live", "authorized account required; public search currently login-limited"),
    ("Pinterest Ads", "paid social", "live", "authorized account required"),
    ("Snapchat Ads", "paid social", "live", "authorized account required"),
    ("Samsung Ads", "CTV / smart TV", "marketing/MCP listed; not detailed in integrations guide", "authorized account required"),
    ("Google Analytics 4", "analytics", "live", "authorized account required"),
    ("Google Tag Manager", "analytics", "live", "authorized account required"),
    ("YouTube", "analytics / video", "live", "authorized account required; public search adapter has no retained firsthand evidence"),
    ("PostHog", "analytics", "API-key auth", "authorized account required"),
    ("Mixpanel", "analytics", "API-key auth", "authorized account required"),
    ("Segment", "customer data", "API-key auth", "authorized account required"),
    ("HubSpot", "CRM", "live", "authorized account required"),
    ("Attio", "CRM", "live", "authorized account required"),
    ("Salesforce", "CRM", "coming soon", "not enabled"),
    ("Shopify", "commerce", "live", "authorized account required"),
    ("The Trade Desk", "programmatic / DSP", "API-key auth", "authorized account required"),
    ("Amazon DSP", "programmatic / DSP", "live", "authorized account required"),
    ("Display & Video 360", "programmatic / DSP", "live", "authorized account required"),
    ("StackAdapt", "programmatic / DSP", "live", "authorized account required"),
    ("FreeWheel", "programmatic / CTV", "live", "authorized account required"),
    ("Campaign Manager 360", "programmatic / ad serving", "live", "authorized account required"),
    ("Taboola", "native / content", "marketing/MCP listed; not detailed in integrations guide", "authorized account required"),
    ("Outbrain", "native / content", "marketing/MCP listed; not detailed in integrations guide", "authorized account required"),
    ("Amazon Ads", "retail media", "live", "authorized account required"),
    ("Walmart Connect", "retail media", "live", "authorized account required"),
    ("Instacart Ads", "retail media", "live", "authorized account required"),
    ("Target Roundel", "retail media", "live", "authorized account required"),
    ("Criteo Commerce Media", "retail media", "live", "authorized account required"),
    ("OpenAI Ads & LLM Placements", "AI / LLM", "live", "authorized account required"),
    ("Spotify Ads", "audio / podcast", "live", "authorized account required"),
    ("Nextdoor Ads", "local advertising", "live", "authorized account required"),
    ("Stripe", "revenue / payments", "API-key auth", "authorized account required"),
    ("Loops", "email marketing", "API-key auth", "authorized account required"),
    ("Klaviyo", "email marketing", "live", "authorized account required"),
    ("Slack", "notifications", "live", "authorized account required"),
    ("Google Drive", "storage / export", "live", "authorized account required"),
)


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


@app.get("/api/integrations")
def integrations(x_demo_tenant: str | None = Header(None)):
    tenant(x_demo_tenant)
    return {"source": "https://docs.syntermedia.ai/integrations", "captured_at": "2026-09-04",
            "items": [{"name": name, "category": category, "documented_status": status, "local_access": access}
                      for name, category, status, access in SYNTER_INTEGRATIONS]}


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
