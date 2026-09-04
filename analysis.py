"""Reproducible local semantic embeddings and descriptive, non-incidence math."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering

ROOT = Path(__file__).parent
RAW, VECTORS = ROOT / "data/evidence.jsonl", ROOT / "data/vectors.jsonl"
MODEL = {"provider": "sentence-transformers", "model": "sentence-transformers/all-MiniLM-L6-v2", "dimension": 384,
         "normalization": "L2", "note": "local pretrained semantic embedding model; source text and vectors stay local"}


def cosine(a: Any, b: Any) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denominator) if denominator else 0.0


def embed_texts(texts: list[str], model: Any | None = None) -> np.ndarray:
    encoder = model or SentenceTransformer(MODEL["model"])
    matrix = np.asarray(encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False), dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != MODEL["dimension"] or not np.isfinite(matrix).all():
        raise ValueError("embedding output must be finite 384-dimensional vectors")
    if not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-4):
        raise ValueError("embedding output must be L2-normalized")
    return matrix


def summarize(rows: list[dict]) -> dict:
    denominator = len(rows)
    accepted = sum(bool(row.get("advertiser_firsthand")) for row in rows)
    topic_counts = {topic: sum(topic in row.get("topics", []) for row in rows) for topic in sorted({t for r in rows for t in r.get("topics", [])})}
    return {"n": denominator, "accepted": accepted, "candidate": denominator - accepted,
            "advertiser_firsthand_share": accepted / denominator if denominator else 0.0,
            "topic_counts": topic_counts, "denominator": "all directly retrieved, de-duplicated candidate records",
            "time_series": "insufficient historical snapshots; no trend or market-incidence claim"}


def analyze(rows: list[dict], model: Any | None = None, persist: bool = True) -> dict:
    if not rows:
        return {"model": MODEL, "records": [], "clusters": [], "coverage": summarize([])}
    if len({row.get("id") for row in rows}) != len(rows):
        raise ValueError("analysis requires unique record IDs")
    matrix = embed_texts([row["text"] for row in rows], model)
    labels = np.zeros(len(rows), dtype=int) if len(rows) < 3 else AgglomerativeClustering(
        n_clusters=min(3, len(rows)), metric="cosine", linkage="average").fit_predict(matrix)
    enriched = []
    for row, vector, label in zip(rows, matrix, labels):
        item = dict(row)
        item["vector"] = vector.tolist()
        item["cluster"] = int(label)
        enriched.append(item)
    clusters = []
    for label in sorted(set(labels)):
        group = [row for row in enriched if row["cluster"] == int(label)]
        clusters.append({"id": int(label), "count": len(group), "unique_contributors": len({r.get("author") for r in group if r.get("author")}),
                         "topics": sorted({t for r in group for t in r.get("topics", [])})})
    if sum(cluster["count"] for cluster in clusters) != len(enriched) or {row["id"] for row in enriched} != {row["id"] for row in rows}:
        raise AssertionError("cluster/vector records must align exactly with the input corpus")
    if persist:
        VECTORS.parent.mkdir(exist_ok=True)
        VECTORS.write_text("\n".join(json.dumps(row) for row in enriched) + "\n")
    return {"model": MODEL, "records": enriched, "clusters": clusters, "coverage": summarize(enriched)}


def nearest(query: str, rows: list[dict], model: Any | None = None, limit: int = 10) -> list[dict]:
    if not query.strip() or not rows:
        return []
    if any("vector" not in row for row in rows):
        raise ValueError("search requires persisted embeddings")
    query_vector = embed_texts([query], model)[0]
    scored = [(cosine(query_vector, row["vector"]), row) for row in rows]
    return [{**row, "similarity": score} for score, row in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]


if __name__ == "__main__":
    source = [json.loads(line) for line in RAW.read_text().splitlines()] if RAW.exists() else []
    print(json.dumps(analyze(source), indent=2))
