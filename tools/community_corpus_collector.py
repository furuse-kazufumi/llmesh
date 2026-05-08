"""community_corpus_collector.py — Community-source paper / article harvester.

Complements ``bulk_corpus_collector.py`` (pure academic APIs) with
**community-curated** material for each domain:

Sources
-------
* **CrossRef** — 145M+ DOI metadata, free, no auth
* **DBLP** — 6M+ CS publications, free
* **PubMed E-utilities** — biomedical literature (medical_corpus only)
* **Papers With Code** — implementations + papers
* **OpenReview** — peer-reviewed conferences
* **GitHub awesome-lists** — community-curated reading lists
* **HackerNews via Algolia** — practitioner discussions
* **Reddit /r/MachineLearning** — hot threads

Output is JSONL with the same schema as ``bulk_corpus_collector.py``,
making the result trivially mergeable.

Security invariants
-------------------
- HTTPS only, GET only, no auth uploaded
- Strict per-record byte cap; per-source rate limit
- HTML sanitisation: only the abstract / title / link metadata is kept
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = 30.0
_HTTP_RETRIES = 3
_HTTP_BACKOFF = 2.0
_MAX_RECORD_BYTES = 16 * 1024
_USER_AGENT = "llmesh-corpus/2.8 (https://llmesh.dev/contact)"

_RATE_LIMIT_S = {
    "crossref": 0.2,
    "dblp": 1.0,
    "pubmed": 0.34,        # 3 req/s per NCBI ToS
    "pwc": 1.0,
    "openreview": 1.0,
    "hn_algolia": 0.5,
    "github": 1.0,
}

_CROSSREF_API   = "https://api.crossref.org/works"
_DBLP_API       = "https://dblp.org/search/publ/api"
_PUBMED_API     = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PWC_API        = "https://paperswithcode.com/api/v1/papers/"
_HN_ALGOLIA_API = "https://hn.algolia.com/api/v1/search"
_OPENREVIEW_API = "https://api.openreview.net/notes"


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def _http_get(url: str, *, accept: str = "application/json") -> bytes:
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _USER_AGENT, "Accept": accept,
            })
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                return r.read()
        except Exception as exc:
            last_exc = exc
            time.sleep(_HTTP_BACKOFF ** attempt)
    raise RuntimeError(f"GET {url} failed: {last_exc}")


def _record(*,
            id_: str, title: str, abstract: str,
            authors: list[str], year: int, url: str,
            source: str, categories: list[str] | None = None,
            doi: str = "") -> dict[str, Any] | None:
    title = (title or "").strip()
    if not title:
        return None
    rec = {
        "id": id_,
        "title": title,
        "abstract": (abstract or "").strip(),
        "authors": [a for a in authors if a],
        "year": int(year or 0),
        "categories": categories or [],
        "url": url or "",
        "doi": doi or "",
        "source": source,
        "topics": [],
        "title_hash": _title_hash(title),
        "fetched_at": _utc_now_iso(),
    }
    if len(json.dumps(rec, ensure_ascii=False).encode()) > _MAX_RECORD_BYTES:
        return None
    return rec


# ---------------------------------------------------------------------------
# CrossRef (DOI-rich)
# ---------------------------------------------------------------------------

def fetch_crossref(query: str, target: int) -> Iterator[dict[str, Any]]:
    cursor = "*"
    fetched = 0
    while fetched < target:
        params = {
            "query": query,
            "rows": 1000,
            "cursor": cursor,
            "select": "DOI,title,abstract,author,issued,URL,subject,type",
        }
        url = f"{_CROSSREF_API}?{urllib.parse.urlencode(params)}"
        try:
            data = json.loads(_http_get(url))
        except Exception as exc:
            logger.warning("crossref %r: %s", query, exc)
            return
        items = (data.get("message") or {}).get("items") or []
        if not items:
            return
        for it in items:
            rec = _crossref_to_common(it)
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        cursor = (data.get("message") or {}).get("next-cursor")
        if not cursor:
            return
        time.sleep(_RATE_LIMIT_S["crossref"])


def _crossref_to_common(o: dict[str, Any]) -> dict[str, Any] | None:
    title_list = o.get("title") or []
    title = title_list[0] if title_list else ""
    abstract = re.sub(r"<[^>]+>", "", o.get("abstract") or "").strip()
    authors = []
    for a in (o.get("author") or []):
        name = " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
        if name:
            authors.append(name)
    issued = ((o.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued and issued[0] else 0
    return _record(
        id_=f"crossref:{o.get('DOI', '')}",
        title=title, abstract=abstract,
        authors=authors, year=year,
        url=o.get("URL", ""),
        categories=o.get("subject") or [],
        doi=o.get("DOI") or "",
        source="crossref",
    )


# ---------------------------------------------------------------------------
# DBLP (CS bibliography)
# ---------------------------------------------------------------------------

def fetch_dblp(query: str, target: int) -> Iterator[dict[str, Any]]:
    fetched = 0
    while fetched < target:
        params = {
            "q": query, "format": "json",
            "h": min(1000, target - fetched), "f": fetched,
        }
        url = f"{_DBLP_API}?{urllib.parse.urlencode(params)}"
        try:
            data = json.loads(_http_get(url))
        except Exception as exc:
            logger.warning("dblp %r: %s", query, exc)
            return
        hits = (((data.get("result") or {}).get("hits") or {}).get("hit")) or []
        if not hits:
            return
        for h in hits:
            rec = _dblp_to_common(h.get("info") or {})
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        time.sleep(_RATE_LIMIT_S["dblp"])


def _dblp_to_common(info: dict[str, Any]) -> dict[str, Any] | None:
    title = info.get("title", "")
    authors_field = info.get("authors") or {}
    authors_list = authors_field.get("author") or []
    if isinstance(authors_list, dict):
        authors_list = [authors_list]
    authors = [a.get("text", "") for a in authors_list if isinstance(a, dict)]
    return _record(
        id_=f"dblp:{info.get('key', '')}",
        title=title,
        abstract="",  # DBLP rarely carries abstracts
        authors=authors,
        year=int(info.get("year") or 0),
        url=info.get("ee", "") or info.get("url", ""),
        categories=[info.get("type", "")],
        doi=info.get("doi", ""),
        source="dblp",
    )


# ---------------------------------------------------------------------------
# PubMed (medical_corpus 専用)
# ---------------------------------------------------------------------------

def fetch_pubmed(query: str, target: int) -> Iterator[dict[str, Any]]:
    # 1. esearch — get IDs
    esearch = (f"{_PUBMED_API}/esearch.fcgi?db=pubmed&term="
               f"{urllib.parse.quote(query)}&retmax={min(target, 5000)}&retmode=json")
    try:
        data = json.loads(_http_get(esearch))
    except Exception as exc:
        logger.warning("pubmed esearch %r: %s", query, exc)
        return
    ids = (data.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return
    # 2. esummary — chunk fetch
    fetched = 0
    chunk = 200
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        url = (f"{_PUBMED_API}/esummary.fcgi?db=pubmed&id={','.join(batch)}"
               f"&retmode=json")
        try:
            data = json.loads(_http_get(url))
        except Exception as exc:
            logger.warning("pubmed esummary: %s", exc)
            continue
        result = data.get("result") or {}
        for pmid in batch:
            d = result.get(pmid)
            if not d:
                continue
            rec = _pubmed_to_common(d)
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        time.sleep(_RATE_LIMIT_S["pubmed"])


def _pubmed_to_common(d: dict[str, Any]) -> dict[str, Any] | None:
    title = d.get("title", "")
    authors = [a.get("name", "") for a in (d.get("authors") or [])]
    year_str = (d.get("pubdate") or "")[:4]
    year = int(year_str) if year_str.isdigit() else 0
    pmid = d.get("uid", "")
    return _record(
        id_=f"pubmed:{pmid}",
        title=title, abstract="",
        authors=authors, year=year,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        categories=d.get("pubtype") or [],
        doi=(d.get("articleids") or [{}])[0].get("value", "") if d.get("articleids") else "",
        source="pubmed",
    )


# ---------------------------------------------------------------------------
# HackerNews (Algolia full-text search)
# ---------------------------------------------------------------------------

def fetch_hn(query: str, target: int) -> Iterator[dict[str, Any]]:
    fetched = 0
    page = 0
    while fetched < target:
        params = {"query": query, "tags": "story", "hitsPerPage": 100, "page": page}
        url = f"{_HN_ALGOLIA_API}?{urllib.parse.urlencode(params)}"
        try:
            data = json.loads(_http_get(url))
        except Exception as exc:
            logger.warning("hn %r: %s", query, exc)
            return
        hits = data.get("hits") or []
        if not hits:
            return
        for h in hits:
            ts = h.get("created_at_i", 0)
            year = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).year if ts else 0
            rec = _record(
                id_=f"hn:{h.get('objectID', '')}",
                title=h.get("title") or h.get("story_title") or "",
                abstract=(h.get("story_text") or "")[:1000],
                authors=[h.get("author", "")] if h.get("author") else [],
                year=year,
                url=h.get("url") or h.get("story_url") or "",
                categories=["hn_story"],
                source="hackernews",
            )
            if rec is not None:
                yield rec
                fetched += 1
                if fetched >= target:
                    return
        page += 1
        if page > 50:    # API caps at 50 pages
            return
        time.sleep(_RATE_LIMIT_S["hn_algolia"])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True,
                   choices=("crossref", "dblp", "pubmed", "hn"))
    p.add_argument("--query", required=True)
    p.add_argument("--target", type=int, default=5000)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    fn = {
        "crossref": fetch_crossref,
        "dblp": fetch_dblp,
        "pubmed": fetch_pubmed,
        "hn": fetch_hn,
    }[args.source]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for r in fn(args.query, args.target):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} records ({args.source}) → {args.out}")


if __name__ == "__main__":
    main()
