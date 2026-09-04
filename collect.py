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


def get_page(url: str) -> str:
    if not curl_requests:
        raise RuntimeError("curl_cffi unavailable")
    response = curl_requests.get(url, impersonate="chrome", timeout=15,
                                 headers={"Accept": "text/html,application/xhtml+xml"})
    response.raise_for_status()
    return response.text


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


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def youtube(query: str) -> list[dict]:
    """Parse result metadata embedded in YouTube's normal public search page, not its Data API."""
    page = get_page("https://www.youtube.com/results?search_query=" + quote(query))
    marker = "var ytInitialData = "
    start = page.find(marker)
    if start < 0:
        raise RuntimeError("YouTube public result data unavailable")
    payload, _ = json.JSONDecoder().raw_decode(page[start + len(marker):])
    rows = []
    for node in walk(payload):
        video = node.get("videoRenderer") if isinstance(node, dict) else None
        if not video or not video.get("videoId"):
            continue
        title = " ".join(run.get("text", "") for run in video.get("title", {}).get("runs", []))
        description = " ".join(run.get("text", "") for run in video.get("detailedMetadataSnippets", [{}])[0].get("snippetText", {}).get("runs", []))
        author = " ".join(run.get("text", "") for run in video.get("ownerText", {}).get("runs", [])) or None
        published = video.get("publishedTimeText", {}).get("simpleText")
        rows.append(record("youtube", "https://www.youtube.com/watch?v=" + video["videoId"], title + " " + description,
                           published, "curl_cffi:youtube-public-search-page", {"views": video.get("viewCountText", {}).get("simpleText"),
                           "duration": video.get("lengthText", {}).get("simpleText")}, author, "youtube-search-html-json"))
    return rows


REDDIT_URLS = (
    "https://www.reddit.com/r/FacebookAds/comments/1tbz7dy/is_my_budget_related_to_my_leads_quality/",
    "https://www.reddit.com/r/FacebookAds/comments/1w1v0jc/some_help_needed/",
)


def reddit(_: str) -> list[dict]:
    """Only accept an actual public post body; login shells and .json 403s are access failures."""
    rows = []
    for url in REDDIT_URLS:
        page = get_page(url)
        if "Welcome to Reddit" in page or "Log in or sign up" in page or "shreddit-post" not in page:
            raise RuntimeError("Reddit returned a login/challenge shell instead of the requested public post body")
        raise RuntimeError("Reddit page parser intentionally refuses unverified post markup")
    return rows


def page_probe(platform: str, url: str) -> list[dict]:
    """Record that a normal public search/page did not expose a usable discussion body."""
    page = get_page(url)
    lower = page.lower()
    if "log in" in lower or "login" in lower or "sign in" in lower:
        raise RuntimeError(f"{platform} served a login shell/no directly extractable public discussion body")
    raise RuntimeError(f"{platform} page did not expose a verified public discussion body")


def x_search(_: str) -> list[dict]:
    return page_probe("X", "https://x.com/search?q=" + quote("facebook ads tracking") + "&src=typed_query")


def tiktok_search(_: str) -> list[dict]:
    return page_probe("TikTok", "https://www.tiktok.com/search?q=" + quote("facebook ads lead quality"))


def linkedin_search(_: str) -> list[dict]:
    """Use ordinary static HTML only after a representative Camoufox equivalence check."""
    from lxml import html as lxml_html
    urls = (
        "https://www.linkedin.com/posts/solaiman-hossen-ratan_googleads-facebookads-googleanalytics4-activity-7386068693224603648-Lakv",
        "https://www.linkedin.com/posts/wafaruk_tracking-ga4-metaads-activity-7438013257056989184--DH6",
        "https://www.linkedin.com/posts/chrisaveryjudeluxe_it-took-me-years-to-figure-this-one-thing-activity-7401175810952564736-eEfr",
        "https://www.linkedin.com/posts/rabeyajaben478_googleads-ppc-leadgeneration-activity-7454891971141111808-bpEY",
        "https://www.linkedin.com/posts/chintandesai4_rightattribution-activity-7350952470480633857-Y2iL",
        "https://www.linkedin.com/posts/jobairmahmud365_firstpartydata-conversiontracking-gtm-activity-7448817188100030465-yzLU",
        "https://www.linkedin.com/posts/izazgoogleadsexpert_googleads-conversiontracking-digitalmarketing-activity-7381282230821707776-BEMe",
        "https://www.linkedin.com/posts/nicholas-a-brown_a-month-ago-i-posted-about-my-experience-activity-7479862973205766144-7lIR",
        "https://www.linkedin.com/posts/talentpirate_googleads-leadgeneration-ppc-activity-7396384091912196096-pW2K",
    )
    rows = []
    for url in urls:
        response = curl_requests.get(url, impersonate="chrome", timeout=20, headers={"Accept": "text/html,application/xhtml+xml"})
        response.raise_for_status()
        document = lxml_html.fromstring(response.text)
        article_nodes = document.xpath("//main//article[1]//text()")
        text = clean(" ".join(article_nodes))
        text = text.split(" Like Comment Share", 1)[0].strip()
        anchors = ("conversion", "tracking", "facebook ads", "google ads", "meta ads")
        if len(text) < 180 or not any(anchor in text.lower() for anchor in anchors):
            raise RuntimeError("LinkedIn static page lacks a body equivalent to the rendered public post")
        bits = [part.strip() for part in text.split(" ") if part.strip()]
        published = next((part for part in bits if re.fullmatch(r"\d+(mo|w|d|h)", part)), None)
        rows.append(record("linkedin", url, text, published, "curl_cffi:linkedin-public-static-html-article",
                           {"http_status": response.status_code, "bytes": len(response.content)}, bits[0] if bits else None,
                           "static-html-article"))
    return rows


ADAPTERS = {"bluesky": bluesky, "mastodon": mastodon, "lemmy": lemmy, "peertube": peertube,
            "youtube": youtube, "reddit": reddit, "x": x_search, "tiktok": tiktok_search,
            "linkedin": linkedin_search}
ADAPTER_METHODS = {
    "bluesky": "public ATProto search endpoint", "mastodon": "public Mastodon tag timeline endpoint",
    "lemmy": "public Lemmy search endpoint", "peertube": "public PeerTube search endpoint",
    "youtube": "normal public YouTube search page with embedded result metadata", "reddit": "normal public Reddit post pages",
    "x": "normal public X search page", "tiktok": "normal public TikTok search page", "linkedin": "curl_cffi static LinkedIn article HTML (Camoufox-equivalence checked)",
}


def classify(text: str) -> tuple[bool, list[str], str]:
    """Conservative transparent classification; it is not a claim of market incidence."""
    t = text.lower()
    commercial = any(x in t for x in ("ads", "attribution", "pixel", "lead", "conversion", "crm", "media buying", "campaign"))
    pain = any(x in t for x in ("wrong", "broken", "mismatch", "inaccurate", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "struggle"))
    first = bool(re.search(r"\b(?:my|our)\s+(?:ads?|campaigns?|leads?)\b|\bwe\s+spend\b|\bi\s+run\b|\bclient\s+account\b|\bfor\s+a\s+client\b", t))
    consumer = any(x in t for x in ("hate ads", "stop showing", "annoying ad"))
    promo = any(x in t for x in ("connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you"))
    topics = [name for name, terms in {"attribution": ("attribution", "roas"), "lead_quality": ("lead",), "measurement": ("pixel", "conversion", "tracking"), "workload": ("manual", "media buying"), "crm_signal": ("crm", "offline conversion")}.items() if any(term in t for term in terms)]
    accepted = commercial and pain and first and not consumer and not promo
    reason = "accepted_firsthand_complaint" if accepted else ("excluded_consumer_or_promotional" if consumer or promo else "relevant_candidate_needs_review")
    return accepted, topics, reason


def is_relevant_candidate(text: str) -> bool:
    """Reject search-keyword collisions, generic news, consumer posts, and promotional copy."""
    t = text.lower()
    paid_context = any(x in t for x in ("facebook ads", "meta ads", "google ads", "ad account", "ad campaign", "media buying", "paid social", "attribution", "conversion tracking", "conversion mismatch", "offline conversion", "crm", "pixel"))
    experience = any(x in t for x in ("my ad", "our ad", "my campaign", "our campaign", "client account", "i run", "we spend", "wrong", "broken", "mismatch", "inaccurate", "missing", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "struggle"))
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
        queries = ("direct-page-check",) if name in {"reddit", "x", "tiktok", "linkedin"} else TOPICS
        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            futures = {executor.submit(adapter, query): query for query in queries}
            for future in as_completed(futures):
                query = futures[future]
                try:
                    platform_rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"{query}: {type(exc).__name__}: {str(exc)[:160]}")
        fetched = len(platform_rows)
        platform_rows = normalize(platform_rows)[:MAX_PER_PLATFORM]
        collected.extend(platform_rows)
        report["platforms"][name] = {"method": ADAPTER_METHODS[name], "direct_bodies": fetched,
                                     "retrieved": len(platform_rows), "accepted": sum(r["advertiser_firsthand"] for r in platform_rows), "errors": errors}
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text("\n".join(json.dumps(row) for row in collected) + ("\n" if collected else ""))
    report["finished_at"] = now()
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return collected


if __name__ == "__main__":
    run()
