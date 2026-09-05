"""Bounded, public-only collection. Fetched bodies remain in ignored ``data/``."""
from __future__ import annotations

import hashlib
import html
import json
import re
import codecs
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
DISCOVERY_QUEUE = ROOT / "data" / "discovery-queue.jsonl"
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


def publication_metadata(published_at: str | None, captured_at: str | None) -> dict[str, Any]:
    """Expose date precision without inventing a timestamp from a relative label."""
    if not published_at:
        return {"publication_date_status": "missing", "publication_age_days": None}
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        captured = datetime.fromisoformat((captured_at or now()).replace("Z", "+00:00"))
        return {"publication_date_status": "exact", "publication_age_days": max(0, (captured - published).days)}
    except ValueError:
        return {"publication_date_status": "relative_source_label", "publication_age_days": None}


def load_discovery_queue(platform: str) -> list[dict[str, str]]:
    """Read bounded public URL leads; URLs are not evidence until a body is captured."""
    if not DISCOVERY_QUEUE.exists():
        return []
    rows, seen = [], set()
    for line in DISCOVERY_QUEUE.read_text().splitlines()[:100]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = item.get("source_url")
        if item.get("attempted_at") or item.get("platform") != platform or not isinstance(url, str) or not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        rows.append({"source_url": url, "discovery_query": str(item.get("discovery_query") or "unspecified"),
                     "discovered_at": str(item.get("discovered_at") or "unknown")})
    return rows


def mark_discovery_attempt(url: str, outcome: str) -> None:
    """Consume a queued URL after one normal public attempt; never hammer a block."""
    if not DISCOVERY_QUEUE.exists():
        return
    changed, lines = False, []
    for line in DISCOVERY_QUEUE.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("source_url") == url and not item.get("attempted_at"):
            item.update(attempted_at=now(), outcome=outcome[:160])
            changed = True
        lines.append(json.dumps(item))
    if changed:
        DISCOVERY_QUEUE.write_text("\n".join(lines) + "\n")


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


def reddit(_: str) -> list[dict]:
    """Normal anonymous browser rendering only; never follow a challenge redirect."""
    try:
        from camoufox.sync_api import Camoufox
    except ImportError as exc:
        raise RuntimeError("optional Camoufox browser runtime is not installed") from exc
    rows = []
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        for queued in load_discovery_queue("reddit")[:5]:
            url = queued["source_url"]
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(4_000)
                if "js_challenge" in page.url or "challenge" in page.url:
                    raise RuntimeError("Reddit redirected to a JavaScript challenge")
                post = page.locator("shreddit-post")
                if post.count() != 1:
                    raise RuntimeError("Reddit did not render one public post body")
                text = post.inner_text(timeout=15_000)
                if len(text) < 180:
                    raise RuntimeError("Reddit public post body was too short to validate")
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                author_match = re.search(r"\b\d+(?:y|mo|w|d|h) ago\s+([A-Za-z0-9_-]+)\b", text)
                rows.append(record("reddit", url, text, next((line for line in lines if re.fullmatch(r"\d+(?:y|mo|w|d|h) ago", line)), None),
                                   "camoufox:normal-anonymous-rendered-shreddit-post", {"http_status": response.status if response else None,
                                   "discovery_query": queued["discovery_query"], "discovered_at": queued["discovered_at"]},
                                   author_match.group(1) if author_match else None, "rendered-html-dom"))
                persist_evidence([rows[-1]])
                mark_discovery_attempt(url, "captured")
            except Exception as exc:
                mark_discovery_attempt(url, f"blocked_or_unverified: {type(exc).__name__}: {exc}")
    return rows


def google_ads_community(_: str) -> list[dict]:
    """Extract the original question from a normal public Help Community page.

    A normal anonymous browser render was compared with the static hydration
    payload for title, author, and distinctive body anchors before enabling this
    smaller bounded request path.
    """
    rows = []
    for queued in load_discovery_queue("google_ads_community")[:5]:
        url = queued["source_url"]
        try:
            page = get_page(url)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            title = clean(title_match.group(1)) if title_match else ""
            title_at = page.find(title, 100) if title else -1
            start = page.find(r"\x22\\u003cdiv", title_at)
            end = page.find(r"\x22,", start + 5)
            if start < 0 or end < 0:
                raise RuntimeError("Google Ads Community original-body hydration was unavailable")
            payload = page[start + 4:end]
            for _ in range(2):
                payload = codecs.decode(payload, "unicode_escape")
            text = clean(payload)
            author_match = re.search(r"\[\[\\x22([^\\]+)\\x22\],\[null,", page[end:end + 3000])
            anchors = ("google ads", "click", "campaign", "conversion", "shopify")
            if len(text) < 180 or not title or not author_match or not any(anchor in text.lower() for anchor in anchors):
                raise RuntimeError("Google Ads Community page lacked a verified original advertiser body")
            rows.append(record("google_ads_community", url, title + "\n" + text, None,
                               "curl_cffi:google-ads-community-hydration", {"bytes": len(page),
                               "discovery_query": queued["discovery_query"], "discovered_at": queued["discovered_at"]},
                               author_match.group(1), "community-hydration"))
            persist_evidence([rows[-1]])
            mark_discovery_attempt(url, "captured")
        except Exception as exc:
            mark_discovery_attempt(url, f"blocked_or_unverified: {type(exc).__name__}: {exc}")
    return rows


def microsoft_ads_community(_: str) -> list[dict]:
    """Extract author-bound original questions from normal public Microsoft Q&A HTML."""
    from lxml import html as lxml_html
    rows = []
    for queued in load_discovery_queue("microsoft_ads_community")[:5]:
        url = queued["source_url"]
        try:
            page = get_page(url)
            document = lxml_html.fromstring(page)
            question = document.xpath('//*[@id="question-details"]')
            if len(question) != 1:
                raise RuntimeError("Microsoft Q&A did not expose one original question")
            question = question[0]
            title = clean(" ".join(question.xpath(".//h1[1]//text()")))
            author_nodes = (question.xpath('.//a[contains(@class, "profile-url")][1]//text()')
                            or question.xpath('.//span[contains(@class, "has-text-subtle")][1]//text()'))
            author = clean(" ".join(author_nodes))
            published = question.xpath(".//local-time[1]/@datetime")
            body = clean(" ".join(question.xpath(".//p[not(@class)][1]//text()")))
            body = body.split("Locked Question.", 1)[0].strip()
            anchors = ("ad", "campaign", "impression", "click", "budget")
            if len(body) < 80 or not title or not author or not any(anchor in body.lower() for anchor in anchors):
                raise RuntimeError("Microsoft Q&A page lacked an author-bound original advertiser body")
            rows.append(record("microsoft_ads_community", url, title + "\n" + body, published[0] if published else None,
                               "curl_cffi:microsoft-qna-original-question", {"bytes": len(page),
                               "discovery_query": queued["discovery_query"], "discovered_at": queued["discovered_at"]},
                               author, "community-html"))
            persisted = persist_evidence([rows[-1]])
            outcome = "captured" if any(item["id"] == rows[-1]["id"] for item in persisted) else "excluded_nonrelevant"
            mark_discovery_attempt(url, outcome)
        except Exception as exc:
            mark_discovery_attempt(url, f"blocked_or_unverified: {type(exc).__name__}: {exc}")
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
        "https://www.linkedin.com/posts/travi0074_metaads-googleads-digitaladvertising-activity-7465407715914452992-vVgN",
        "https://www.linkedin.com/posts/muhammad-mijanur_facebookads-metaads-digitalmarketing-activity-7469100408767143936-OsYg",
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
            "linkedin": linkedin_search, "google_ads_community": google_ads_community,
            "microsoft_ads_community": microsoft_ads_community}
ADAPTER_METHODS = {
    "bluesky": "public ATProto search endpoint", "mastodon": "public Mastodon tag timeline endpoint",
    "lemmy": "public Lemmy search endpoint", "peertube": "public PeerTube search endpoint",
    "youtube": "normal public YouTube search page with embedded result metadata", "reddit": "normal anonymous Camoufox rendered Reddit post",
    "x": "normal public X search page", "tiktok": "normal public TikTok search page", "linkedin": "curl_cffi static LinkedIn article HTML (Camoufox-equivalence checked)",
    "google_ads_community": "curl_cffi Google Ads Community hydration (Camoufox-equivalence checked)",
    "microsoft_ads_community": "curl_cffi Microsoft Q&A original-question HTML (Camoufox-equivalence checked)",
}


def classify(text: str) -> tuple[bool, list[str], str]:
    """Conservative transparent classification; it is not a claim of market incidence."""
    t = text.lower()
    commercial = any(x in t for x in ("ads", "attribution", "pixel", "lead", "conversion", "crm", "media buying", "campaign", "microsoft advertising", "microsoft ads", "bing ads"))
    pain = any(x in t for x in ("wrong", "broken", "mismatch", "inaccurate", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "not running", "pausing", "disappeared", "struggle", "died", "failed", "fraud"))
    # Possessives alone are too narrow: operators often report a live observation
    # as "I am seeing" or "we are getting" without writing "my campaign".
    # These verbs are only accepted alongside the independent commercial + pain
    # gates below, so an advisory post does not become firsthand merely by using I/we.
    first = bool(re.search(
        r"\b(?:my|our)\s+(?:ads?|campaigns?|leads?|accounts?|shopify|store)\b|\bwe\s+spend\b|"
        r"\bi(?:\s+am|'m)\s+(?:running|trying)\b|\bi(?:\s+have|'ve)\s+(?:(?:the\s+)?same\s+)?(?:run|ran|campaigns?)\b|\bi\s+run\b|"
        r"\bclient\s+account\b|\bfor\s+a\s+client\b|"
        r"\b(?:i(?:\s+am|'m)|we(?:\s+are|'re))\s+(?:seeing|experiencing|getting)\b",
        t,
    ))
    consumer = any(x in t for x in ("hate ads", "stop showing", "annoying ad"))
    promo = any(x in t for x in ("connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you", "dm me", "free audit", "i've helped", "i help", "here's how", "i'll manage your"))
    topics = [name for name, terms in {"attribution": ("attribution", "roas"), "lead_quality": ("lead",), "measurement": ("pixel", "conversion", "tracking"), "workload": ("manual", "media buying"), "crm_signal": ("crm", "offline conversion")}.items() if any(term in t for term in terms)]
    accepted = commercial and pain and first and not consumer and not promo
    reason = "accepted_firsthand_complaint" if accepted else ("promotional_or_advisory_candidate" if promo else ("excluded_consumer" if consumer else "relevant_candidate_needs_review"))
    return accepted, topics, reason


def is_relevant_candidate(text: str) -> bool:
    """Reject search-keyword collisions, generic news, consumer posts, and promotional copy."""
    t = text.lower()
    paid_context = any(x in t for x in ("facebook ads", "meta ads", "google ads", "microsoft ads", "microsoft advertising", "bing ads", "ad account", "ad campaign", "media buying", "paid social", "attribution", "conversion tracking", "conversion mismatch", "offline conversion", "crm", "pixel")) or ("microsoft" in t and "ads" in t) or ("ads" in t and "campaign" in t)
    experience = any(x in t for x in ("my ad", "our ad", "my campaign", "our campaign", "client account", "i run", "we spend", "no ads", "wrong", "broken", "mismatch", "inaccurate", "missing", "bad lead", "low quality", "waste", "manual", "problem", "issue", "doesn't", "not working", "not running", "pausing", "disappeared", "struggle", "died", "failed", "fraud"))
    excluded = any(x in t for x in ("hate ads", "stop showing", "annoying ad", "connect your", "integrates with", "sign up", "book a demo", "free trial", "we help you", "i'll manage your", "i create and manage", "i will set up", "boost your business", "with froggyads", "ppc ads expert", "benchmarks reveal", "which is suitable for your campaign"))
    return paid_context and experience and not excluded


def normalize(items: list[dict]) -> list[dict]:
    """Deduplicate direct bodies by canonical source URL and retain candidate/accepted distinction."""
    out, seen = [], set()
    for item in items:
        if not item.get("source_url") or item["id"] in seen or not item.get("text"):
            continue
        if item.get("platform") == "google_ads_community" and not item.get("author"):
            continue
        seen.add(item["id"])
        if item.get("platform") == "reddit" and not item.get("published_at"):
            relative = re.search(r"\b\d+(?:y|mo|w|d|h) ago\b", item["text"])
            if relative:
                item["published_at"] = relative.group(0)
        if item.get("platform") == "reddit":
            author = re.search(r"\b\d+(?:y|mo|w|d|h) ago\s+([A-Za-z0-9_-]+)\b", item["text"])
            if author:
                item["author"] = author.group(1)
        item["advertiser_firsthand"], item["topics"], item["classification_reason"] = classify(item["text"])
        item["candidate_relevant"] = is_relevant_candidate(item["text"])
        item.update(publication_metadata(item.get("published_at"), item.get("captured_at")))
        if item["candidate_relevant"]:
            out.append(item)
    return out


def merge_with_existing(items: list[dict]) -> list[dict]:
    """Keep the local evidence ledger append-only by source identity across runs."""
    existing = []
    if DATA.exists():
        for line in DATA.read_text().splitlines():
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # A newly fetched record refreshes mutable capture metadata for the same URL;
    # an earlier direct capture is retained when a later public request is blocked.
    merged = {item["id"]: item for item in existing if item.get("id")}
    merged.update({item["id"]: item for item in items if item.get("id")})
    return normalize(list(merged.values()))


def persist_evidence(items: list[dict]) -> list[dict]:
    """Atomically merge directly captured bodies before their queue outcome is final."""
    DATA.parent.mkdir(exist_ok=True)
    merged = merge_with_existing(items)
    DATA.write_text("\n".join(json.dumps(row) for row in merged) + ("\n" if merged else ""))
    return merged


def run() -> list[dict]:
    collected: list[dict] = []
    report: dict[str, Any] = {"started_at": now(), "platforms": {}, "scope": "direct public endpoint bodies only"}
    for name, adapter in ADAPTERS.items():
        platform_rows: list[dict] = []
        errors: list[str] = []
        queries = ("direct-page-check",) if name in {"reddit", "x", "tiktok", "linkedin", "google_ads_community", "microsoft_ads_community"} else TOPICS
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
    collected = persist_evidence(collected)
    report["finished_at"] = now()
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return collected


if __name__ == "__main__":
    run()
