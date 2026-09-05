import http.client
import json
import math
import sys
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parents[1]))
from analysis import MODEL, analyze, cosine, nearest
from api import app
from collect import classify, google_ads_community, instagram, load_discovery_queue, mark_discovery_attempt, merge_with_existing, microsoft_ads_community, normalize, persist_evidence, publication_metadata, record
from fastapi.testclient import TestClient


class FakeEncoder:
    def encode(self, texts, **_):
        rows = []
        for i, _ in enumerate(texts):
            row = [0.0] * MODEL["dimension"]
            row[i % MODEL["dimension"]] = 1.0
            rows.append(row)
        return rows


class OwnedFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"x" * (2048 if self.path == "/large" else 5)
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass


class LocalRelay(BaseHTTPRequestHandler):
    limit, active, max_queue = 512, 0, 1
    lock = threading.Lock()
    upstream_port = 0
    def do_CONNECT(self): self.send_error(405, "CONNECT disabled")
    def do_GET(self):
        host = self.headers.get("X-Owned-Upstream", "")
        if host != f"127.0.0.1:{self.upstream_port}": self.send_error(403, "upstream denied"); return
        with self.lock:
            if self.active >= self.max_queue: self.send_error(429, "bounded queue full"); return
            type(self).active += 1
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=2)
            conn.request("GET", self.path); response = conn.getresponse(); body = response.read(self.limit + 1); conn.close()
            if len(body) > self.limit: self.send_error(413, "response cap exceeded"); return
            self.send_response(response.status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        finally:
            with self.lock: type(self).active -= 1
    def log_message(self, *_): pass


class PipelineTests(unittest.TestCase):
    def test_math_and_normalized_semantic_vectors(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1); self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0); self.assertFalse(math.isnan(cosine([0], [0])))
        rows = [record("test", f"https://example.test/{i}", f"my ads tracking is broken {i}", None, "fixture", {}, "a") for i in range(3)]
        for row in rows: row.update(advertiser_firsthand=True, topics=["measurement"], classification_reason="accepted_firsthand_complaint")
        result = analyze(rows, FakeEncoder(), persist=False)
        self.assertEqual(len(result["records"]), 3); self.assertTrue(all(len(r["vector"]) == 384 and abs(sum(x*x for x in r["vector"]) - 1) < 1e-8 for r in result["records"]))
        self.assertEqual(sum(cluster["count"] for cluster in result["clusters"]), result["coverage"]["n"])
        self.assertEqual({row["id"] for row in result["records"]}, {row["id"] for row in rows})
        self.assertEqual(result["coverage"]["accepted"], 3)
        self.assertEqual(len(nearest("tracking", result["records"], FakeEncoder(), 2)), 2)

    def test_filter_and_dedupe(self):
        self.assertFalse(classify("Connect your ads to CRM for full attribution")[0]); self.assertTrue(classify("My ads attribution is wrong and my leads are low quality")[0])
        self.assertFalse(classify("Your ad platforms are mismatched, but this is an agency explainer")[0])
        reddit_like = ("I am seeing Meta giving us 50% more purchase events than we actually have. "
                       "We never had this issue before and our Pixel has operated without changes.")
        accepted, _, reason = classify(reddit_like)
        self.assertTrue(accepted); self.assertEqual(reason, "accepted_firsthand_complaint")
        self.assertFalse(classify("I am seeing agencies struggle; here is how I help them fix conversion tracking")[0])
        self.assertTrue(classify("I'm running LinkedIn ads and can't do conversion tracking; it is a problem")[0])
        self.assertTrue(classify("I have ran all types of Facebook ads for my branded store, but my ads died")[0])
        self.assertTrue(classify("My Shopify analytics shows Google Ads clicks missing; its invalid-click system failed")[0])
        self.assertTrue(classify("My Microsoft Advertising ads keep pausing and my campaigns are not running")[0])
        self.assertTrue(classify("I have the same campaigns running since 2019, but my ads suddenly disappeared")[0])
        short_original = record("instagram", "https://example.test/short", "Idk why but Meta ads are not delivering at all!", "2026-04-17", "fixture", {}, "merchant")
        self.assertEqual(normalize([short_original])[0]["classification_reason"], "relevant_candidate_needs_review")
        hashtag_only = record("instagram", "https://example.test/tags", "#metaads #indianbrand #clothingbrand", "2026-04-17", "fixture", {}, "merchant")
        short_promo = record("instagram", "https://example.test/promo", "Comment ADS for my Meta ads setup guide #metaads", "2026-04-17", "fixture", {}, "merchant")
        self.assertEqual(normalize([hashtag_only, short_promo]), [])
        one = record("test", "https://example.test/a", "My Facebook ads tracking is broken", None, "fixture", {})
        self.assertEqual(len(normalize([one, dict(one)])), 1)
        rendered = record("reddit", "https://example.test/reddit", "r/FacebookAds • 2y ago author My Facebook ads are broken", None, "fixture", {}, fmt="rendered-html-dom")
        self.assertEqual(normalize([rendered])[0]["published_at"], "2y ago")
        self.assertEqual(normalize([rendered])[0]["author"], "author")
        missing_author = record("google_ads_community", "https://example.test/community", "My Google Ads campaign is not working and conversions are missing", None, "fixture", {})
        self.assertEqual(normalize([missing_author]), [])

    def test_merge_prefers_new_capture_for_same_source(self):
        old = record("test", "https://example.test/a", "My Facebook ads tracking is broken", None, "fixture", {})
        fresh = dict(old, text="My Facebook ads tracking is still broken", captured_at="later")
        from unittest.mock import patch
        with patch("collect.DATA") as path:
            path.exists.return_value = True
            path.read_text.return_value = json.dumps(old) + "\n"
            merged = merge_with_existing([fresh])
        self.assertEqual(len(merged), 1); self.assertEqual(merged[0]["text"], fresh["text"])

    def test_persist_merges_without_dropping_existing(self):
        old = record("test", "https://example.test/a", "My Facebook ads tracking is broken", None, "fixture", {})
        fresh = record("test", "https://example.test/b", "My Facebook ads tracking is broken", None, "fixture", {})
        from unittest.mock import patch
        with patch("collect.DATA") as path:
            path.exists.return_value = True; path.read_text.return_value = json.dumps(old) + "\n"
            merged = persist_evidence([fresh])
        self.assertEqual({item["id"] for item in merged}, {old["id"], fresh["id"]})

    def test_queue_and_publication_precision(self):
        self.assertEqual(publication_metadata("2025-01-01T00:00:00Z", "2025-01-03T00:00:00Z"), {"publication_date_status": "exact", "publication_age_days": 2})
        self.assertEqual(publication_metadata("2025-01-01", "2025-01-03T00:00:00Z"), {"publication_date_status": "exact", "publication_age_days": 2})
        self.assertEqual(publication_metadata("5mo ago", "2025-01-03T00:00:00Z")["publication_date_status"], "relative_source_label")
        from unittest.mock import patch
        with patch("collect.DISCOVERY_QUEUE") as path:
            path.exists.return_value = True
            path.read_text.return_value = '{"platform":"reddit","source_url":"https://example.test/a","discovery_query":"q"}\n{"platform":"reddit","source_url":"https://example.test/a"}\ninvalid\n'
            self.assertEqual(load_discovery_queue("reddit"), [{"source_url": "https://example.test/a", "discovery_query": "q", "discovered_at": "unknown"}])
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / "queue.jsonl"
            path.write_text('{"platform":"reddit","source_url":"https://example.test/a"}\n')
            with patch("collect.DISCOVERY_QUEUE", path):
                mark_discovery_attempt("https://example.test/a", "challenge")
                self.assertEqual(load_discovery_queue("reddit"), [])
            self.assertIn('"outcome": "challenge"', path.read_text())

    def test_google_ads_community_requires_original_hydration_body(self):
        with patch("collect.load_discovery_queue", return_value=[]):
            self.assertEqual(google_ads_community("ignored"), [])

    def test_microsoft_ads_community_requires_original_html_body(self):
        with patch("collect.load_discovery_queue", return_value=[]):
            self.assertEqual(microsoft_ads_community("ignored"), [])

    def test_instagram_requires_queued_public_post(self):
        with patch("collect.load_discovery_queue", return_value=[]):
            self.assertEqual(instagram("ignored"), [])

    def test_api_tenant_and_contract(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/evidence", headers={"X-Demo-Tenant": "other"}).status_code, 404)
        response = client.get("/api/evidence", headers={"X-Demo-Tenant": "demo-tenant"})
        self.assertEqual(response.status_code, 200); self.assertIn("counts", response.json())
        integrations = client.get("/api/integrations", headers={"X-Demo-Tenant": "demo-tenant"})
        self.assertEqual(integrations.status_code, 200); self.assertGreaterEqual(len(integrations.json()["items"]), 38)
        self.assertIn("public_research", integrations.json()["items"][0])
        self.assertEqual(client.get("/api/search", headers={"X-Demo-Tenant": "demo-tenant"}, params={"q": "x"}).status_code, 422)

    def test_bounded_local_relay(self):
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), OwnedFixture); fixture_thread = threading.Thread(target=fixture.serve_forever); fixture_thread.start()
        LocalRelay.upstream_port = fixture.server_port
        relay = ThreadingHTTPServer(("127.0.0.1", 0), LocalRelay); relay_thread = threading.Thread(target=relay.serve_forever); relay_thread.start()
        try:
            base = f"http://127.0.0.1:{relay.server_port}"
            with urlopen(__import__("urllib.request").request.Request(base + "/ok", headers={"X-Owned-Upstream": f"127.0.0.1:{fixture.server_port}"}), timeout=2) as response: self.assertEqual(response.read(), b"xxxxx")
            with self.assertRaises(HTTPError) as too_large: urlopen(__import__("urllib.request").request.Request(base + "/large", headers={"X-Owned-Upstream": f"127.0.0.1:{fixture.server_port}"}), timeout=2)
            too_large.exception.close()
            with self.assertRaises(HTTPError) as denied: urlopen(base + "/ok", timeout=2)
            denied.exception.close()
            conn = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=2); conn.request("CONNECT", "example.com:443"); self.assertEqual(conn.getresponse().status, 405); conn.close()
            LocalRelay.active = 1
            with self.assertRaises(HTTPError) as saturated: urlopen(__import__("urllib.request").request.Request(base + "/ok", headers={"X-Owned-Upstream": f"127.0.0.1:{fixture.server_port}"}), timeout=2)
            saturated.exception.close()
            LocalRelay.active = 0
        finally:
            relay.shutdown(); relay.server_close(); relay_thread.join(); fixture.shutdown(); fixture.server_close(); fixture_thread.join()


if __name__ == "__main__": unittest.main()
