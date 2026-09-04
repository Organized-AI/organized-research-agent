"""Bounded, public-only collection. Fetched bodies remain in ignored ``data/``."""
from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # Unit tests inject fixtures and need no network package.
    curl_requests = None

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "evidence.jsonl"
REPORT = ROOT / "data" / "collection-report.json"
TOPICS = ("facebook ads", "conversion tracking", "lead quality", "media buying")
MAX_PER_PLATFORM = 20


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def record(platform: str, url: str, text: str, published_at: str | None, method: str,
           metrics: dict[str, Any], author: str | None = None, fmt: str = "json") -> dict[str, Any]:
    """Normalize a directly retrieved public body, retaining provenance and missing values."""
    text = clean(text)
    return {"id": hashlib.sha256(url.encode()).hexdigest()[:20], "platform": platform,
            "source_url": url, "text": text[:8000], "published_at": published_at,
            "captured_at": now(), "extraction_method": method, "format": fmt,
            "metrics": metrics, "author": author, "provenance": "public", "live": True}


def get_json(url: str) -> Any:
    if not curl_requests:
        raise RuntimeError("curl_cffi unavailable")
    response = curl_requests.get(url, impersonate="chrome", timeout=15, headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()


def bluesky(query: str) -> list[dict]:
    data = get_json("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=" + quote(query) + "&limit=25")
    return [record("bluesky", f"https://bsky.app/profile/{p['author']['did']}/post/{p['uri'].rsplit('/', 1)[-1]}", p.get("record", {}).get("text", ""), p.get("record", {}).get("createdAt"), "curl_cffi:atproto-search", {"like_count": p.get("likeCount"), "reply_count": p.get("replyCount"), "repost_count": p.get("repostCount")}, p.get("author", {}).get("handle"), "atproto-json") for p in data.get("posts", [])]


def mastodon(query: str) -> list[dict]:
    # This unauthenticated endpoint is a tag timeline, not full-text search.
    data = get_json("https://mas.to/api/v1/timelines/tag/" + quote(query.replace(" ", "")) + "?limit=40")
    return [record("mastodon", s["url"], s.get("content", ""), s.get("created_at"), "curl_cffi:mastodon-public-tag", {"favourites": s.get("favourites_count"), "reblogs": s.get("reblogs_count"), "replies": s.get("replies_count")}, s.get("account", {}).get("acct"), "mastodon-json") for s in data]


def lemmy(query: str) -> list[dict]:
    data = get_json("https://lemmy.world/api/v3/post/list?search_term=" + quote(query) + "&limit=30")
    out = []
    for item in data.get("posts", []):
        post = item["post"]
        out.append(record("lemmy", post.get("ap_id") or f"https://lemmy.world/post/{post['id']}", post.get("body") or post.get("name", ""), post.get("published"), "curl_cffi:lemmy-public-search", {"score": item.get("counts", {}).get("score"), "comments": item.get("counts", {}).get("comments")}, item.get("creator", {}).get("name"), "lemmy-json"))
    return out


def peertube(query: str) -> list[dict]:
    data = get_json("https://tube.tchncs.de/api/v1/search/videos?search=" + quote(query) + "&count=30")
    return [record("peertube", video.get("url"), video.get("name", "") + " " + (video.get("description") or ""), video.get("publishedAt"), "curl_cffi:peertube-public-search", {"views": video.get("views"), "likes": video.get("likes"), "comments": video.get("comments")}, video.get("channel", {}).get("displayName"), "peertube-json") for video in data.get("data", []) if video.get("url")]


ADAPTERS = {"bluesky": bluesky, "mastodon": mastodon, "lemmy": lemmy, "peertube": peertube}


def classify(text: str) -> tuple[bool, list[str], str]:
    """Conservative transparent classification; it is not a claim of market incidence."""
    t = text.lower()
    commercial = any(x in t for x in ("ads", "attribution", "pixel", "lead", "conversion", "crm", "media buying", "campaign"))
    pain = any(x in t for x in ("wrong", "broken", "mismatch", "inaccurate", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "struggle"))
    first = any(x in t for x in ("my ad", "my campaign", "my lead", "our ad", "our campaign", "we spend", "i run", "client account", "for a client"))
    consumer = any(x in t for x in ("hate ads", "stop showing", "annoying ad"))
    promo = any(x in t for x in ("connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you"))
    topics = [name for name, terms in {"attribution": ("attribution", "roas"), "lead_quality": ("lead",), "measurement": ("pixel", "conversion", "tracking"), "workload": ("manual", "media buying"), "crm_signal": ("crm", "offline conversion")}.items() if any(term in t for term in terms)]
    accepted = commercial and pain and first and not consumer and not promo
    reason = "accepted_firsthand_complaint" if accepted else ("excluded_consumer_or_promotional" if consumer or promo else "relevant_candidate_needs_review")
    return accepted, topics, reason


def is_relevant_candidate(text: str) -> bool:
    """Reject search-keyword collisions, generic news, consumer posts, and promotional copy."""
    t = text.lower()
    paid_context = any(x in t for x in ("facebook ads", "meta ads", "google ads", "ad account", "ad campaign", "media buying", "paid social", "attribution", "conversion tracking", "offline conversion", "crm", "pixel"))
    experience = any(x in t for x in ("my ad", "our ad", "my campaign", "our campaign", "client account", "i run", "we spend", "wrong", "broken", "mismatch", "inaccurate", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "struggle"))
    excluded = any(x in t for x in ("hate ads", "stop showing", "annoying ad", "connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you", "i create and manage", "i will set up", "boost your business", "with froggyads", "ppc ads expert", "benchmarks reveal", "which is suitable for your campaign"))
    return paid_context and experience and not excluded


def normalize(items: list[dict]) -> list[dict]:
    """Deduplicate direct bodies by canonical source URL and retain candidate/accepted distinction."""
    out, seen = [], set()
    for item in items:
        if not item.get("source_url") or item["id"] in seen or not item.get("text"):
            continue
        seen.add(item["id"])
        item["advertiser_firsthand"], item["topics"], item["classification_reason"] = classify(item["text"])
        item["candidate_relevant"] = is_relevant_candidate(item["text"])
        if item["candidate_relevant"]:
            out.append(item)
    return out


def run() -> list[dict]:
    collected: list[dict] = []
    report: dict[str, Any] = {"started_at": now(), "platforms": {}, "scope": "direct public endpoint bodies only"}
    for name, adapter in ADAPTERS.items():
        platform_rows: list[dict] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=len(TOPICS)) as executor:
            futures = {executor.submit(adapter, query): query for query in TOPICS}
            for future in as_completed(futures):
                query = futures[future]
                try:
                    platform_rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"{query}: {type(exc).__name__}: {str(exc)[:160]}")
        fetched = len(platform_rows)
        platform_rows = normalize(platform_rows)[:MAX_PER_PLATFORM]
        collected.extend(platform_rows)
        report["platforms"][name] = {"direct_bodies": fetched, "retrieved": len(platform_rows), "accepted": sum(r["advertiser_firsthand"] for r in platform_rows), "errors": errors}
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text("\n".join(json.dumps(row) for row in collected) + ("\n" if collected else ""))
    report["finished_at"] = now()
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return collected


if __name__ == "__main__":
    run()
