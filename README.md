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

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The test suite covers conservative candidate classification/deduplication, 384-dimensional L2-normalized vector validation, tenant/API contracts, and an owned loopback-only relay fixture (response cap, denied host/CONNECT, bounded queue, shutdown).

## Interpretation

Each run writes ignored `data/collection-report.json` with exact per-platform retrieval/accepted/error counts. “Accepted” means the transparent classifier detected a first-person advertiser/operator complaint; “candidate” means directly retrieved material that needs review. Shares use all de-duplicated direct candidates as their denominator. One snapshot is explicitly insufficient for a trend or representative market-incidence claim.

Current adapters cover public Bluesky, Mastodon, Lemmy, and PeerTube endpoints. Availability/relevance is expected to vary. A 403/challenge or an unauthenticated search limitation is recorded as a limitation, never circumvented or replaced by search snippets/fixtures.
