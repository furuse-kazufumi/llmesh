"""collect_image_papers.py — Image-processing paper corpus collector.

Inspired by RAD-style hacker-corpus collection (Phrack / GHSA / CAPEC),
this tool harvests metadata for image-processing / computer-vision /
industrial-inspection papers from public APIs and stores them as a
JSONL corpus suitable for LLMesh ``corpus2skill`` ingestion.

Sources
-------
* **arXiv API** (default; no auth required)
  ``http://export.arxiv.org/api/query``
* **Semantic Scholar API** (optional; rate-limited, no auth)
  ``https://api.semanticscholar.org/graph/v1/paper/search``
* **OpenReview API** (optional)

Usage
-----
::

    python tools/collect_image_papers.py \\
        --source arxiv \\
        --query "industrial inspection" \\
        --max-results 100 \\
        --out docs/papers/image_corpus/arxiv_inspection.jsonl

Output schema (one JSON object per line)::

    {
        "id": "arxiv:2401.12345",
        "title": "...",
        "abstract": "...",
        "authors": ["...", ...],
        "year": 2024,
        "categories": ["cs.CV", "cs.LG"],
        "url": "https://arxiv.org/abs/2401.12345",
        "source": "arxiv",
        "topics": ["AOI", "anomaly_detection"],
        "fetched_at": "2026-05-07T12:34:56Z"
    }

Security invariants
-------------------
- Read-only HTTP GET; no credentials uploaded.
- All input parameters validated; no shell evaluation.
- Output is JSONL with strict size cap per record (max 16 KiB).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_ARXIV_API = "http://export.arxiv.org/api/query"
_SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# Time-out for HTTP fetches (seconds) — defends against API hangs.
_HTTP_TIMEOUT = 30.0

# Maximum size of one record (bytes) — defends against memory abuse.
_MAX_RECORD_BYTES = 16 * 1024

# arXiv subject categories of interest for image-processing topics.
_DEFAULT_ARXIV_CATEGORIES = (
    "cs.CV",        # Computer Vision and Pattern Recognition
    "cs.GR",        # Graphics
    "cs.MM",        # Multimedia
    "eess.IV",      # Image and Video Processing
)

# Topic keyword classifier — auto-tags abstract text.  Order matters:
# earlier rules win.  Keep narrow / domain-specific terms first.
_TOPIC_RULES: tuple[tuple[str, str], ...] = (
    ("aoi",                            "AOI"),
    ("automated optical inspection",   "AOI"),
    ("event camera",                   "DVS"),
    ("dvs ",                           "DVS"),
    ("dynamic vision sensor",          "DVS"),
    ("depth camera",                   "depth"),
    ("rgb-d",                          "depth"),
    ("point cloud",                    "depth"),
    ("anomaly detection",              "anomaly_detection"),
    ("defect detection",               "anomaly_detection"),
    ("manufacturing",                  "manufacturing"),
    ("industrial",                     "manufacturing"),
    ("privacy",                        "privacy"),
    ("differential privacy",           "privacy"),
    ("face anonym",                    "privacy"),
    ("license plate",                  "privacy"),
    ("ocr",                            "ocr"),
    ("optical character recognition",  "ocr"),
    ("barcode",                        "ocr"),
    ("medical imaging",                "medical"),
    ("dicom",                          "medical"),
    ("ct scan",                        "medical"),
    ("mri",                            "medical"),
    ("yolo",                           "object_detection"),
    ("object detection",               "object_detection"),
    ("segment anything",               "segmentation"),
    ("segmentation",                   "segmentation"),
    ("transformer",                    "transformer"),
    ("vision transformer",             "transformer"),
    ("clip",                           "multimodal"),
    ("multimodal",                     "multimodal"),
    ("vlm",                            "multimodal"),
    ("vision-language",                "multimodal"),
    ("llm",                            "llm_integration"),
    ("language model",                 "llm_integration"),
    ("on-device",                      "edge"),
    ("edge ai",                        "edge"),
    ("embedded",                       "edge"),
)

# arXiv ID format — used to validate parsed records.
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _classify_topics(text: str) -> list[str]:
    """Auto-tag *text* using the keyword rules.  Returns deduplicated topic list."""
    lower = text.lower()
    seen: list[str] = []
    for keyword, topic in _TOPIC_RULES:
        if keyword in lower and topic not in seen:
            seen.append(topic)
    return seen


# ---------------------------------------------------------------------------
# arXiv collector
# ---------------------------------------------------------------------------

def _build_arxiv_query(query: str, categories: Iterable[str]) -> str:
    """Compose an arXiv API search_query expression."""
    cat_or = " OR ".join(f"cat:{c}" for c in categories)
    # Keyword OR full-text search across title + abstract
    kw = f'(ti:"{query}" OR abs:"{query}")'
    return f"{kw} AND ({cat_or})"


def _fetch_arxiv(query: str, categories: Iterable[str], max_results: int) -> list[dict]:
    params = {
        "search_query": _build_arxiv_query(query, categories),
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{_ARXIV_API}?{urllib.parse.urlencode(params)}"
    logger.info("fetching arxiv: %s", url)
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
        body = resp.read()

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(body)
    out: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        arxiv_url = entry.findtext("atom:id", default="", namespaces=ns)
        # arxiv id from URL: http://arxiv.org/abs/2401.12345v1 → 2401.12345v1
        arxiv_id_raw = arxiv_url.rsplit("/", 1)[-1]
        if not _ARXIV_ID_RE.match(arxiv_id_raw):
            continue

        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        abstract = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else 0

        authors: list[str] = []
        for author in entry.findall("atom:author", ns):
            n = author.findtext("atom:name", default="", namespaces=ns)
            if n:
                authors.append(n.strip())

        cats: list[str] = []
        for cat in entry.findall("atom:category", ns):
            term = cat.attrib.get("term", "")
            if term:
                cats.append(term)

        topics = _classify_topics(f"{title}\n{abstract}")

        record = {
            "id": f"arxiv:{arxiv_id_raw}",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "year": year,
            "categories": cats,
            "url": arxiv_url,
            "source": "arxiv",
            "topics": topics,
            "fetched_at": _utc_now_iso(),
        }

        # Size guard
        as_json = json.dumps(record, ensure_ascii=False)
        if len(as_json.encode("utf-8")) > _MAX_RECORD_BYTES:
            logger.warning("skipping oversized record: %s", arxiv_id_raw)
            continue

        out.append(record)

    return out


# ---------------------------------------------------------------------------
# Semantic Scholar collector
# ---------------------------------------------------------------------------

def _fetch_semantic_scholar(query: str, max_results: int) -> list[dict]:
    params = {
        "query": query,
        "limit": min(max_results, 100),     # API caps at 100
        "fields": "title,abstract,authors,year,externalIds,url,fieldsOfStudy",
    }
    url = f"{_SEMANTIC_SCHOLAR_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "llmesh-corpus/2.2"})
    logger.info("fetching semantic-scholar: %s", url)
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read())

    out: list[dict] = []
    for paper in data.get("data", []):
        title = paper.get("title", "") or ""
        abstract = paper.get("abstract", "") or ""
        authors = [a.get("name", "") for a in (paper.get("authors") or [])]
        year = int(paper.get("year") or 0)
        ext = paper.get("externalIds") or {}
        s2_id = ext.get("DOI") or ext.get("CorpusId") or ""
        topics = _classify_topics(f"{title}\n{abstract}")

        record = {
            "id": f"s2:{s2_id}",
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "year": year,
            "categories": paper.get("fieldsOfStudy") or [],
            "url": paper.get("url") or "",
            "source": "semantic_scholar",
            "topics": topics,
            "fetched_at": _utc_now_iso(),
        }

        if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > _MAX_RECORD_BYTES:
            continue
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _write_jsonl(records: list[dict], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=("arxiv", "semantic_scholar"), default="arxiv")
    p.add_argument("--query", required=True, help="search query")
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--categories", nargs="*", default=list(_DEFAULT_ARXIV_CATEGORIES),
                   help="arXiv categories (arxiv source only)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.source == "arxiv":
        records = _fetch_arxiv(args.query, args.categories, args.max_results)
    else:
        records = _fetch_semantic_scholar(args.query, args.max_results)

    n = _write_jsonl(records, args.out)
    print(f"wrote {n} records → {args.out}")


if __name__ == "__main__":
    main()
