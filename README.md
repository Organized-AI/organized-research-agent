# Organized Research Agent — bounded local pilot

This is an evidence-led customer-discovery pilot for Synter Media. It collects only directly retrieved public post/video bodies through unauthenticated, documented public endpoints. It does not collect private data, contact people, infer spend, or bypass sign-in/challenges. `data/` is ignored and must not be committed.

## Run

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python collect.py
.venv/bin/python analysis.py
.venv/bin/python api.py
```

Open `http://127.0.0.1:4173`. The page only exposes the local corpus and calls the tenant-protected evidence API. Search becomes available after `analysis.py` persists vectors.

The LinkedIn adapter uses Camoufox’s normal anonymous rendering for two concrete public post URLs. Its browser asset is fetched separately with `.venv/bin/python -m camoufox fetch`; it remains in the user cache, not this repository. The collector never logs in, handles a challenge, or uses a proxy.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The test suite covers conservative candidate classification/deduplication, 384-dimensional L2-normalized vector validation, tenant/API contracts, and an owned loopback-only relay fixture (response cap, denied host/CONNECT, bounded queue, shutdown).

## Interpretation

Each run writes ignored `data/collection-report.json` with exact per-platform retrieval/accepted/error counts. “Accepted” means the transparent classifier detected a first-person advertiser/operator complaint; “candidate” means directly retrieved material that needs review. Shares use all de-duplicated direct candidates as their denominator. One snapshot is explicitly insufficient for a trend or representative market-incidence claim.

Current adapters cover public Bluesky, Mastodon, Lemmy, and PeerTube endpoints plus normal public YouTube search pages. The run also probes Reddit, X, TikTok, and LinkedIn pages, recording a login shell, challenge, 403, or absent public post body as an access limitation—not as evidence. Availability/relevance is expected to vary; no search snippets or fixtures replace unavailable bodies.
