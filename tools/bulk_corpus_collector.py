"""bulk_corpus_collector.py — Multi-source bulk paper-corpus collector (v2.8).

Collects up to **10,000+ papers per domain** from multiple complementary
sources with pagination, deduplication, retry, and rate-limiting.

Sources
-------
1. **OpenAlex** (https://api.openalex.org) — 245M+ works, free, no auth.
   Cursor-based pagination; up to 200 per page; ~100k/day per IP polite.
2. **arXiv** (export.arxiv.org/api/query) — 2.4M+ papers, free, no auth.
   Offset pagination; max 2000 per request; 3-second cooldown.
3. **Semantic Scholar Graph API** (api.semanticscholar.org) — 200M+,
   free, optional API key for higher limits.
4. **CrossRef** (api.crossref.org) — 145M+ DOI metadata.

Deduplication
-------------
DOI + arXiv ID + S2 paper ID + title-hash as the merge key set.

Output
------
JSONL files per domain in ``docs/papers/<domain>/<source>_bulk.jsonl``
with the same schema as ``collect_image_papers.py``.

Usage
-----
::

    # Collect ~10k papers for one domain
    python tools/bulk_corpus_collector.py \\
        --domain industrial_iot --target 10000 \\
        --queries "predictive maintenance" "modbus" "opc-ua"

    # Run all 9 domains end-to-end (long-running; ~6h)
    python tools/bulk_corpus_collector.py --all

Security invariants
-------------------
- HTTPS GET only; no credentials uploaded.
- Per-source request size + rate caps.
- Each record JSON-serialized with strict size cap.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Endpoint URLs.
_OPENALEX_API   = "https://api.openalex.org/works"
_ARXIV_API      = "http://export.arxiv.org/api/query"
_S2_API         = "https://api.semanticscholar.org/graph/v1/paper/search"
_CROSSREF_API   = "https://api.crossref.org/works"

# Rate-limit cooldowns per source (seconds between requests).
_RATE_LIMIT_S = {
    "openalex": 0.1,
    "arxiv":    3.0,
    "s2":       1.0,
    "crossref": 0.5,
}

# HTTP timeout / retry policy.
_HTTP_TIMEOUT = 30.0
_HTTP_RETRIES = 3
_HTTP_BACKOFF = 2.0   # exponential

# OpenAlex per-page limit.
_OPENALEX_PAGE_SIZE = 200
# arXiv per-request hard cap.
_ARXIV_PAGE_SIZE = 1_000

# Per-record size cap (bytes).
_MAX_RECORD_BYTES = 16 * 1024

# 9 domains × default per-domain query bundles.
_DOMAIN_QUERIES: dict[str, list[str]] = {
    "image": [
        "automated optical inspection",
        "depth camera scene description",
        "event camera dvs",
        "industrial computer vision",
    ],
    "security": [
        "industrial control system security",
        "llm prompt injection",
        "differential privacy edge",
        "supply chain security sbom",
    ],
    "industrial_iot": [
        "predictive maintenance",
        "Mahalanobis Taguchi anomaly",
        "OPC-UA digital twin",
        "modbus iot",
    ],
    "mlops": [
        "edge llm inference",
        "onnx runtime quantization",
        "model compression distillation",
        "mlops drift detection",
    ],
    "game_dev": [
        "npc dialogue large language model",
        "procedural generation game",
        "anti cheat behavioural detection",
        "game telemetry analytics",
    ],
    "medical": [
        "medical imaging language model",
        "dicom federated learning",
        "ecg time series anomaly",
        "fhir clinical decision support",
    ],
    "automotive": [
        "controller area network anomaly",
        "autosar safety verification",
        "obd-ii diagnostic deep learning",
        "adas event camera",
    ],
    "infrastructure": [
        "dnp3 scada anomaly",
        "iec 61850 substation cybersecurity",
        "smart grid demand response",
        "bacnet building automation",
    ],
    "robotics": [
        "ros 2 large language model",
        "slam point cloud edge",
        "robot manipulation language",
        "humanoid embodied agent",
    ],
    # ----- Frontier AI / Quantum domains (v2.9) -----
    "deep_learning": [
        "deep learning optimization",
        "convolutional neural network",
        "self supervised learning",
        "contrastive learning",
        "scaling laws neural",
    ],
    "neural_network": [
        "spiking neural network",
        "graph neural network",
        "neural ode",
        "neural radiance field",
        "transformer architecture",
    ],
    "llm": [
        "large language model alignment",
        "instruction tuning rlhf",
        "long context attention",
        "mixture of experts moe",
        "tool use chain of thought",
    ],
    "vllm": [
        "vision language model clip",
        "multimodal foundation model",
        "visual instruction tuning",
        "paged attention kv cache",
        "speculative decoding draft",
    ],
    "quantum": [
        "quantum machine learning",
        "variational quantum algorithm",
        "quantum error correction",
        "quantum simulation chemistry",
        "noise intermediate scale quantum",
    ],
    "diffusion": [
        "denoising diffusion probabilistic model",
        "score based generative",
        "stable diffusion latent",
        "flow matching rectified flow",
        "consistency model distillation",
    ],
    "agents": [
        "autonomous llm agent",
        "tool calling function",
        "multi agent collaboration",
        "react planning",
        "computer use agent gui",
    ],
    # ----- Mathematics / Statistics domains (v2.10) -----
    "multivariate": [
        "Mahalanobis distance anomaly",
        "principal component analysis pca",
        "discriminant analysis classification",
        "multivariate statistical process control",
        "manifold learning umap tsne",
    ],
    "statistics": [
        "statistical process control spc",
        "bayesian inference posterior",
        "hypothesis testing power",
        "time series forecasting",
        "extreme value theory",
    ],
    "optimization": [
        "convex optimization machine learning",
        "stochastic gradient descent sgd",
        "mixed integer programming",
        "metaheuristic genetic algorithm",
        "bayesian optimization hyperparameter",
    ],
    "numerical": [
        "singular value decomposition svd",
        "matrix factorization low rank",
        "iterative linear solver krylov",
        "tensor decomposition cp tucker",
        "randomized linear algebra",
    ],
    "information_theory": [
        "shannon entropy mutual information",
        "channel capacity coding theorem",
        "data compression lossless",
        "differential privacy bounds",
        "quantum information entanglement",
    ],
}

_DEFAULT_TARGET_PER_DOMAIN = 10_000


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _http_get(url: str, *, ua: str = "llmesh-corpus/2.8") -> bytes:
    """GET *url* with retry + backoff."""
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            time.sleep(_HTTP_BACKOFF ** attempt)
    raise RuntimeError(f"HTTP failed after {_HTTP_RETRIES} attempts: {last_exc}")


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def _coerce_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Validate + size-cap a single record before persistence."""
    line = json.dumps(rec, ensure_ascii=False)
    if len(line.encode()) > _MAX_RECORD_BYTES:
        return None
    return rec


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

def fetch_openalex(query: str, target: int) -> Iterator[dict[str, Any]]:
    """Yield up to *target* OpenAlex records for *query* (cursor pagination)."""
    cursor = "*"
    fetched = 0
    while fetched < target and cursor:
        params = {
            "search": query,
            "per-page": _OPENALEX_PAGE_SIZE,
            "cursor": cursor,
            "select": "id,doi,title,abstract_inverted_index,publication_year,authorships,primary_location,concepts",
        }
        url = f"{_OPENALEX_API}?{urllib.parse.urlencode(params)}"
        try:
            body = _http_get(url)
            data = json.loads(body)
        except Exception as exc:
            logger.warning("openalex query=%r cursor=%s error=%s", query, cursor, exc)
            return

        results = data.get("results", [])
        if not results:
            return
        for r in results:
            rec = _openalex_to_common(r)
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return

        cursor = data.get("meta", {}).get("next_cursor")
        time.sleep(_RATE_LIMIT_S["openalex"])


def _openalex_to_common(o: dict[str, Any]) -> dict[str, Any] | None:
    """Convert OpenAlex Work to LLMesh corpus schema."""
    title = (o.get("title") or "").strip()
    if not title:
        return None
    abstract = _openalex_abstract(o.get("abstract_inverted_index"))
    authors = [
        (a.get("author", {}) or {}).get("display_name", "")
        for a in (o.get("authorships") or [])
    ]
    concepts = [c.get("display_name", "") for c in (o.get("concepts") or [])][:8]
    rec = {
        "id": f"openalex:{(o.get('id') or '').rsplit('/', 1)[-1]}",
        "title": title,
        "abstract": abstract,
        "authors": [a for a in authors if a],
        "year": int(o.get("publication_year") or 0),
        "categories": concepts,
        "url": o.get("id") or "",
        "doi": (o.get("doi") or "").replace("https://doi.org/", ""),
        "source": "openalex",
        "topics": [],
        "title_hash": _title_hash(title),
        "fetched_at": _utc_now_iso(),
    }
    return _coerce_record(rec)


def _openalex_abstract(idx: dict[str, list[int]] | None) -> str:
    """Reconstruct abstract from OpenAlex inverted index."""
    if not idx:
        return ""
    positions: list[tuple[int, str]] = []
    for word, locs in idx.items():
        for p in locs:
            positions.append((p, word))
    positions.sort()
    return " ".join(w for _, w in positions)


# ---------------------------------------------------------------------------
# arXiv (offset-based pagination)
# ---------------------------------------------------------------------------

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_arxiv_bulk(query: str, target: int) -> Iterator[dict[str, Any]]:
    """Paginate arXiv with offset until *target* records are returned."""
    fetched = 0
    while fetched < target:
        page = min(_ARXIV_PAGE_SIZE, target - fetched)
        params = {
            "search_query": f"all:{query}",
            "start": fetched,
            "max_results": page,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = f"{_ARXIV_API}?{urllib.parse.urlencode(params)}"
        try:
            body = _http_get(url)
            root = ET.fromstring(body)
        except Exception as exc:
            logger.warning("arxiv query=%r start=%d error=%s", query, fetched, exc)
            return

        entries = root.findall("atom:entry", _ARXIV_NS)
        if not entries:
            return
        for e in entries:
            rec = _arxiv_to_common(e)
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        time.sleep(_RATE_LIMIT_S["arxiv"])


def _arxiv_to_common(e: ET.Element) -> dict[str, Any] | None:
    title = (e.findtext("atom:title", default="", namespaces=_ARXIV_NS) or "").strip()
    if not title:
        return None
    abstract = (e.findtext("atom:summary", default="", namespaces=_ARXIV_NS) or "").strip()
    arxiv_url = e.findtext("atom:id", default="", namespaces=_ARXIV_NS) or ""
    arxiv_id = arxiv_url.rsplit("/", 1)[-1]
    published = e.findtext("atom:published", default="", namespaces=_ARXIV_NS) or ""
    year = int(published[:4]) if published[:4].isdigit() else 0
    authors = [
        a.findtext("atom:name", default="", namespaces=_ARXIV_NS) or ""
        for a in e.findall("atom:author", _ARXIV_NS)
    ]
    cats = [c.attrib.get("term", "") for c in e.findall("atom:category", _ARXIV_NS)]
    rec = {
        "id": f"arxiv:{arxiv_id}",
        "title": title,
        "abstract": abstract,
        "authors": [a for a in authors if a],
        "year": year,
        "categories": [c for c in cats if c],
        "url": arxiv_url,
        "doi": "",
        "source": "arxiv",
        "topics": [],
        "title_hash": _title_hash(title),
        "fetched_at": _utc_now_iso(),
    }
    return _coerce_record(rec)


# ---------------------------------------------------------------------------
# Semantic Scholar — pagination via offset, capped by API at 1k cumulative
# ---------------------------------------------------------------------------

def fetch_s2_bulk(query: str, target: int) -> Iterator[dict[str, Any]]:
    fetched = 0
    page = 100
    while fetched < target:
        params = {
            "query": query,
            "offset": fetched,
            "limit": min(page, target - fetched),
            "fields": "title,abstract,authors,year,externalIds,url,fieldsOfStudy",
        }
        url = f"{_S2_API}?{urllib.parse.urlencode(params)}"
        try:
            body = _http_get(url)
            data = json.loads(body)
        except Exception as exc:
            logger.warning("s2 query=%r offset=%d error=%s", query, fetched, exc)
            return

        items = data.get("data") or []
        if not items:
            return
        for item in items:
            rec = _s2_to_common(item)
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        time.sleep(_RATE_LIMIT_S["s2"])


def _s2_to_common(o: dict[str, Any]) -> dict[str, Any] | None:
    title = (o.get("title") or "").strip()
    if not title:
        return None
    rec = {
        "id": "s2:" + ((o.get("externalIds") or {}).get("DOI")
                       or str((o.get("externalIds") or {}).get("CorpusId") or "?")),
        "title": title,
        "abstract": o.get("abstract") or "",
        "authors": [a.get("name", "") for a in (o.get("authors") or [])],
        "year": int(o.get("year") or 0),
        "categories": o.get("fieldsOfStudy") or [],
        "url": o.get("url") or "",
        "doi": ((o.get("externalIds") or {}).get("DOI") or ""),
        "source": "semantic_scholar",
        "topics": [],
        "title_hash": _title_hash(title),
        "fetched_at": _utc_now_iso(),
    }
    return _coerce_record(rec)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicates by DOI / arxiv id / title_hash, preserving first-seen."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in records:
        keys = []
        if r.get("doi"):
            keys.append("doi:" + r["doi"].lower())
        if r.get("id"):
            keys.append(r["id"])
        keys.append(r.get("title_hash", ""))
        if any(k in seen for k in keys):
            continue
        seen.update(keys)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def collect_domain(domain: str, target: int, queries: list[str],
                    out_dir: Path) -> int:
    """Multi-source collection for one domain.  Returns total deduped records."""
    out_dir.mkdir(parents=True, exist_ok=True)

    per_query_target = max(target // (len(queries) * 3), 200)
    all_records: list[dict[str, Any]] = []

    for q in queries:
        logger.info("[%s] query=%r — OpenAlex", domain, q)
        for r in fetch_openalex(q, per_query_target):
            all_records.append(r)
        logger.info("[%s] query=%r — arXiv", domain, q)
        for r in fetch_arxiv_bulk(q, per_query_target):
            all_records.append(r)
        logger.info("[%s] query=%r — Semantic Scholar", domain, q)
        for r in fetch_s2_bulk(q, per_query_target):
            all_records.append(r)

    deduped = dedupe_records(all_records)

    out_path = out_dir / "bulk_combined.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in deduped:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("[%s] wrote %d unique records to %s", domain, len(deduped), out_path)
    return len(deduped)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--domain", default=None,
                   help=f"one of {sorted(_DOMAIN_QUERIES)}")
    p.add_argument("--all", action="store_true",
                   help="run all domains end-to-end")
    p.add_argument("--target", type=int, default=_DEFAULT_TARGET_PER_DOMAIN)
    p.add_argument("--queries", nargs="+", default=None)
    p.add_argument("--out-base", type=Path, default=Path("docs/papers"))
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.all:
        total = 0
        for dom, queries in _DOMAIN_QUERIES.items():
            n = collect_domain(dom, args.target, queries,
                               args.out_base / f"{dom}_corpus")
            total += n
        print(f"All domains: collected {total} unique records total")
    else:
        if args.domain not in _DOMAIN_QUERIES:
            raise SystemExit(f"--domain must be one of: {sorted(_DOMAIN_QUERIES)}")
        queries = args.queries or _DOMAIN_QUERIES[args.domain]
        n = collect_domain(args.domain, args.target, queries,
                           args.out_base / f"{args.domain}_corpus")
        print(f"{args.domain}: collected {n} unique records")


if __name__ == "__main__":
    main()
