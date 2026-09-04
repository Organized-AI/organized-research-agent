"""Bounded, public-only collection adapters. Raw results stay under ignored data/."""
from __future__ import annotations
import asyncio, hashlib, html, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # tests can inject payloads without network dependencies
    curl_requests = None

DATA = Path(__file__).parent / "data" / "evidence.jsonl"
TOPICS = ("facebook ads attribution", "google ads lead quality", "media buying workload", "conversion tracking crm")
MAX_PER_PLATFORM = 20

def now() -> str: return datetime.now(timezone.utc).isoformat()
def clean(value: str) -> str: return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", value or ""))).strip()
def record(platform: str, url: str, text: str, published_at: str | None, method: str, metrics: dict[str, Any], author: str | None = None, fmt: str = "json") -> dict[str, Any]:
    text = clean(text)
    return {"id": hashlib.sha256(url.encode()).hexdigest()[:20], "platform": platform, "source_url": url, "text": text[:8000], "published_at": published_at, "captured_at": now(), "extraction_method": method, "format": fmt, "metrics": metrics, "author": author, "provenance": "public", "live": True}

def get_json(url: str) -> Any:
    if not curl_requests: raise RuntimeError("curl_cffi unavailable")
    response = curl_requests.get(url, impersonate="chrome", timeout=20, headers={"Accept": "application/json"})
    response.raise_for_status(); return response.json()

def bluesky(query: str) -> list[dict]:
    data = get_json("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=" + query.replace(" ", "%20") + "&limit=25")
    return [record("bluesky", f"https://bsky.app/profile/{p['author']['did']}/post/{p['uri'].rsplit('/',1)[-1]}", p.get("record",{}).get("text",""), p.get("record",{}).get("createdAt"), "curl_cffi:atproto-search", {"like_count":p.get("likeCount"),"reply_count":p.get("replyCount"),"repost_count":p.get("repostCount")}, p.get("author",{}).get("handle"), "atproto-json") for p in data.get("posts", [])]

def mastodon(query: str) -> list[dict]:
    data = get_json("https://mas.to/api/v1/timelines/tag/" + query.replace(" ", "") + "?limit=40")
    return [record("mastodon", s["url"], s.get("content",""), s.get("created_at"), "curl_cffi:mastodon-tag", {"favourites":s.get("favourites_count"),"reblogs":s.get("reblogs_count"),"replies":s.get("replies_count")}, s.get("account",{}).get("acct"), "mastodon-json") for s in data]

def lemmy(query: str) -> list[dict]:
    data = get_json("https://lemmy.world/api/v3/post/list?search_term=" + query.replace(" ", "%20") + "&limit=30")
    out=[]
    for item in data.get("posts",[]):
        p=item["post"]; out.append(record("lemmy", p.get("ap_id") or f"https://lemmy.world/post/{p['id']}", p.get("body") or p.get("name",""), p.get("published"), "curl_cffi:lemmy-search", {"score":item.get("counts",{}).get("score"),"comments":item.get("counts",{}).get("comments")}, item.get("creator",{}).get("name"), "lemmy-json"))
    return out

def peertube(query: str) -> list[dict]:
    data = get_json("https://tube.tchncs.de/api/v1/search/videos?search=" + query.replace(" ", "%20") + "&count=30")
    return [record("peertube", v.get("url"), v.get("name","") + " " + (v.get("description") or ""), v.get("publishedAt"), "curl_cffi:peertube-search", {"views":v.get("views"),"likes":v.get("likes"),"comments":v.get("comments")}, v.get("channel",{}).get("displayName"), "peertube-json") for v in data.get("data",[])]

ADAPTERS = {"bluesky": bluesky, "mastodon": mastodon, "lemmy": lemmy, "peertube": peertube}

def classify(text: str) -> tuple[bool, list[str]]:
    t=text.lower(); first=any(x in t for x in ("i run", "my ads", "our ads", "we spend", "my campaign", "client account")); commercial=any(x in t for x in ("ads", "attribution", "pixel", "lead", "conversion", "crm", "media buying")); pain=any(x in t for x in ("wrong", "broken", "mismatch", "inaccurate", "bad leads", "low quality", "waste", "manual", "problem", "issue", "doesn't match")); consumer=any(x in t for x in ("hate ads", "stop showing", "annoying ad")); promo=any(x in t for x in ("connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you")); topics=[x for x in ("attribution","lead_quality","measurement","workload","crm_signal") if x.split("_")[0] in t]
    return first and commercial and pain and not consumer and not promo, topics

def run() -> list[dict]:
    rows=[]
    for name, adapter in ADAPTERS.items():
        seen=set()
        for query in TOPICS:
            try: batch=adapter(query if name != "mastodon" else query.split()[0])
            except Exception as exc: print(json.dumps({"platform":name,"query":query,"error":str(exc)[:120]})); continue
            for item in batch:
                item["advertiser_firsthand"], item["topics"] = classify(item["text"])
                if item["id"] not in seen and item["advertiser_firsthand"]:
                    rows.append(item); seen.add(item["id"])
                if len(seen) >= MAX_PER_PLATFORM: break
            if len(seen) >= MAX_PER_PLATFORM: break
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text("\n".join(json.dumps(x) for x in rows) + ("\n" if rows else ""))
    print(json.dumps({"saved":len(rows),"by_platform":{p:sum(x['platform']==p for x in rows) for p in ADAPTERS}})); return rows

if __name__ == "__main__": run()
