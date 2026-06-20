#!/usr/bin/env python3
"""
experience_search.py — Semantic / keyword search over agent experience records.

Ranks past experience records in ``memory/<domain>/experiences.jsonl`` by
relevance to a natural-language query (e.g. "what fixed WNS issues on sky130
before?") so orchestrators can retrieve prior fixes by similarity instead of
reading the whole file.

Two backends, one stable contract:

  * keyword  — a pure-stdlib TF-IDF + cosine ranker over the free-text fields
    (``issues_encountered``, ``fixes_applied``, ``notes``). Always available;
    adds value at any dataset size. This is the default and the permanent
    fallback.
  * embedding — an OPTIONAL semantic backend that activates only when (a) an
    embedding library is importable AND (b) the domain has at least
    ``--min-records`` records (default 50, per issue #28). Vectors are cached in
    a stdlib ``sqlite3`` index keyed by a content hash, so re-embedding is
    incremental. Dormant by default (stdlib-only repo): ``get_embedding_backend``
    returns ``None`` until a deployment overrides/monkeypatches it.

The JSON output shape is identical regardless of backend — only the ``backend``
and ``fell_back`` flags change — so the keyword path is a drop-in for the
semantic one as records accumulate.

Usage:
    python3 tools/experience_search.py --domain synthesis \\
        --query "what fixed WNS on sky130" [options]

Options:
    --domain DOMAIN          Domain to search (required)
    --query  TEXT            Natural-language query (required unless --reindex)
    --design NAME            Filter to records with matching design_name
    --pdk    VALUE           Filter to records with matching pdk
    --tool   VALUE           Filter to records with matching tool_used
    --limit  N               Max results to return (default: 5)
    --min-records N          Threshold below which embedding falls back to
                             keyword (default: 50)
    --backend {auto,keyword,embedding}   Backend selection (default: auto)
    --memory-root PATH       Path to the memory/ directory (default: auto-detect)
    --reindex                Rebuild the embedding cache for the domain, then exit
    --json                   Emit machine-readable JSON (always on for --reindex)

Exit codes:
    0  — results emitted (or reindex completed)
    1  — no matching records found
    2  — unexpected error / bad memory root
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from array import array
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relpath: str):
    """Load one of the repo's standalone tool scripts by path (they are run as
    scripts in production, not installed as a package)."""
    path = REPO_ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Reuse the canonical loaders/filters so this reader agrees with the writers.
_distill = _load_module(
    "distill", "plugins/infrastructure/skills/memory-keeper/distill.py")
_memory_root = _load_module(
    "memory_root", "plugins/infrastructure/skills/memory-keeper/memory_root.py")
_qor = _load_module("qor_trends", "tools/qor_trends.py")

VALID_DOMAINS = _distill.VALID_DOMAINS
load_records = _distill.load_records
resolve_memory_root = _memory_root.resolve_memory_root
filter_by_design = _qor.filter_by_design
filter_by_pdk = _qor.filter_by_pdk
filter_by_tool = _qor.filter_by_tool

# Free-text fields that carry the searchable signal of an experience record.
SEARCHABLE_FIELDS = ("issues_encountered", "fixes_applied")

# Small stopword set so natural-language queries ("what fixed WNS issues on
# sky130 before?") reduce to content terms. Intentionally tiny — over-filtering
# hurts recall at low record counts.
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "was", "were", "is", "are", "be", "been", "did", "do", "does", "what",
    "which", "how", "when", "where", "why", "before", "after", "this", "that",
    "these", "those", "from", "by", "at", "as", "it", "its", "we", "i",
})

_TOKEN_RE = re.compile(r"[a-z0-9_.\-]+")

# sqlite cache lives beside the JSONL it indexes.
INDEX_FILENAME = ".experience_index.sqlite3"


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Lowercase and split into EDA-friendly tokens.

    Keeps domain tokens like ``wns_ns``, ``sky130``, ``-flatten`` and numbers
    like ``0.35`` intact; drops stopwords and single characters.
    """
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= 2 and t not in STOPWORDS
    ]


def searchable_text(rec: dict) -> str:
    """Concatenate a record's free-text fields into one searchable string."""
    parts: list[str] = []
    for field in SEARCHABLE_FIELDS:
        value = rec.get(field) or []
        if isinstance(value, list):
            parts.extend(str(x) for x in value if x is not None)
        elif isinstance(value, str):
            parts.append(value)
    notes = rec.get("notes")
    if isinstance(notes, str):
        parts.append(notes)
    return " ".join(parts)


def record_hash(rec: dict) -> str:
    """Stable content hash over the fields that determine the embedding."""
    canonical = json.dumps(
        {"run_id": rec.get("run_id"), "text": searchable_text(rec)},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Keyword backend: TF-IDF + cosine
# ---------------------------------------------------------------------------

def _idf_map(doc_tokens: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency over the candidate corpus."""
    n = len(doc_tokens)
    df: Counter = Counter()
    for toks in doc_tokens:
        for term in set(toks):
            df[term] += 1
    return {term: math.log((n + 1) / (c + 1)) + 1.0 for term, c in df.items()}


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf: Counter = Counter(tokens)
    return {term: count * idf.get(term, 0.0) for term, count in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    # Iterate the smaller vector for the dot product.
    if len(a) > len(b):
        a, b = b, a
    dot = sum(weight * b.get(term, 0.0) for term, weight in a.items())
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _keyword_rank(query_tokens: list[str], candidates: list[dict]) -> list[tuple[dict, float, list[str]]]:
    """Return (record, score, matched_terms) for candidates with score > 0."""
    doc_tokens = [tokenize(searchable_text(rec)) for rec in candidates]
    idf = _idf_map(doc_tokens)
    q_vec = _tfidf_vector(query_tokens, idf)
    q_set = set(query_tokens)

    scored: list[tuple[dict, float, list[str]]] = []
    for rec, toks in zip(candidates, doc_tokens):
        score = _cosine(q_vec, _tfidf_vector(toks, idf))
        if score <= 0.0:
            continue
        matched = sorted(q_set & set(toks), key=lambda t: -idf.get(t, 0.0))[:6]
        scored.append((rec, score, matched))
    return scored


# ---------------------------------------------------------------------------
# Optional embedding backend (dormant by default — stdlib-only repo)
# ---------------------------------------------------------------------------

class EmbeddingBackend:
    """Pluggable semantic backend.

    The base class is intentionally never available: this repo is stdlib-only,
    so semantic ranking stays off until a deployment supplies a real backend by
    overriding :func:`get_embedding_backend` (or monkeypatching it in tests).
    Subclasses set ``model``/``dim`` and implement :meth:`embed`.
    """

    name = "embedding"
    model = "none"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


def get_embedding_backend() -> EmbeddingBackend | None:
    """Return an available embedding backend, or ``None``.

    Default: ``None`` (no embedding library wired in). Override this function or
    monkeypatch it to enable the semantic path once records accumulate.
    """
    return None


def _select_backend(backend: str, record_count: int, threshold: int,
                    available: bool) -> tuple[str, str | None]:
    """Resolve the effective backend and a fallback reason (or None).

    Returns one of ``("keyword"|"embedding", reason)``. ``reason`` is non-None
    only when an embedding request/auto-selection degraded to keyword.
    """
    if backend == "keyword":
        return "keyword", None

    eligible = available and record_count >= threshold
    if eligible:
        return "embedding", None

    if not available:
        reason = "embedding backend unavailable; using keyword"
    else:
        reason = f"record_count {record_count} < threshold {threshold}"
    return "keyword", reason


# ---------------------------------------------------------------------------
# Embedding cache (stdlib sqlite3, keyed by content hash)
# ---------------------------------------------------------------------------

def _connect_cache(cache_path: Path):
    import sqlite3
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cache_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embeddings ("
        "record_hash TEXT PRIMARY KEY, model TEXT, dim INTEGER, "
        "vector BLOB, created_at TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _vec_to_blob(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def _blob_to_vec(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def _build_embeddings(records: list[dict], backend: EmbeddingBackend,
                      cache_path: Path, cache: bool = True) -> dict[str, list[float]]:
    """Return ``{record_hash: vector}`` for ``records``, embedding only the
    records whose hash is not already cached. Writes new vectors back."""
    import datetime as _dt

    hashes = [record_hash(rec) for rec in records]
    result: dict[str, list[float]] = {}

    conn = None
    cached: dict[str, list[float]] = {}
    if cache:
        conn = _connect_cache(cache_path)
        rows = conn.execute(
            "SELECT record_hash, vector FROM embeddings WHERE model = ?",
            (backend.model,)).fetchall()
        cached = {h: _blob_to_vec(b) for h, b in rows}

    missing = [(h, rec) for h, rec in zip(hashes, records) if h not in cached]
    if missing:
        vectors = backend.embed([searchable_text(rec) for _, rec in missing])
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        for (h, _rec), vec in zip(missing, vectors):
            cached[h] = list(vec)
            if conn is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings "
                    "(record_hash, model, dim, vector, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (h, backend.model, len(vec), _vec_to_blob(vec), now))
        if conn is not None:
            conn.commit()

    for h in hashes:
        if h in cached:
            result[h] = cached[h]
    if conn is not None:
        conn.close()
    return result


def _embedding_rank(query_text: str, candidates: list[dict],
                    backend: EmbeddingBackend, cache_path: Path,
                    cache: bool) -> list[tuple[dict, float, list[str]]]:
    vecs = _build_embeddings(candidates, backend, cache_path, cache=cache)
    q_vec = backend.embed([query_text])[0]
    q_tokens = set(tokenize(query_text))

    def _cos(u: list[float], v: list[float]) -> float:
        dot = sum(x * y for x, y in zip(u, v))
        nu = math.sqrt(sum(x * x for x in u))
        nv = math.sqrt(sum(y * y for y in v))
        if nu == 0.0 or nv == 0.0:
            return 0.0
        return dot / (nu * nv)

    scored: list[tuple[dict, float, list[str]]] = []
    for rec in candidates:
        vec = vecs.get(record_hash(rec))
        if vec is None:
            continue
        score = _cos(q_vec, vec)
        # matched_terms stays lexical for explainability even on the semantic path
        matched = sorted(q_tokens & set(tokenize(searchable_text(rec))))[:6]
        scored.append((rec, score, matched))
    return scored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _apply_filters(records: list[dict], filters: dict | None) -> tuple[list[dict], dict]:
    applied: dict = {}
    out = records
    if not filters:
        return out, applied
    if filters.get("design_name"):
        out = filter_by_design(out, filters["design_name"])
        applied["design_name"] = filters["design_name"]
    if filters.get("pdk"):
        out = filter_by_pdk(out, filters["pdk"])
        applied["pdk"] = filters["pdk"]
    if filters.get("tool_used"):
        out = filter_by_tool(out, filters["tool_used"])
        applied["tool_used"] = filters["tool_used"]
    return out, applied


def _result_record(rec: dict, score: float, matched: list[str]) -> dict:
    return {
        "score": round(score, 4),
        "matched_terms": matched,
        "run_id": rec.get("run_id"),
        "timestamp": rec.get("timestamp"),
        "design_name": rec.get("design_name"),
        "pdk": rec.get("pdk"),
        "tool_used": rec.get("tool_used"),
        "key_metrics": rec.get("key_metrics") or {},
        "issues_encountered": rec.get("issues_encountered") or [],
        "fixes_applied": rec.get("fixes_applied") or [],
        "notes": rec.get("notes"),
    }


def query_experiences(
    domain: str,
    query_text: str,
    *,
    filters: dict | None = None,
    limit: int = 5,
    min_records_threshold: int = 50,
    memory_root: Path | None = None,
    backend: str = "auto",
    cache: bool = True,
) -> dict:
    """Rank experience records for ``domain`` by relevance to ``query_text``.

    See module docstring for the JSON shape returned.
    """
    if memory_root is None:
        root = resolve_memory_root(None)
    else:
        root = Path(memory_root).expanduser()

    jsonl = root / domain / "experiences.jsonl"
    records = load_records(jsonl)
    record_count = len(records)

    candidates, filters_applied = _apply_filters(records, filters)

    emb = get_embedding_backend()
    resolved, fallback_reason = _select_backend(
        backend, record_count, min_records_threshold, emb is not None)

    query_tokens = tokenize(query_text)

    if resolved == "embedding" and emb is not None:
        cache_path = root / domain / INDEX_FILENAME
        scored = _embedding_rank(query_text, candidates, emb, cache_path, cache)
    else:
        scored = _keyword_rank(query_tokens, candidates)

    # Sort by score desc, tie-break newest timestamp first.
    scored.sort(key=lambda t: (t[1], t[0].get("timestamp") or ""), reverse=True)
    results = [_result_record(rec, score, matched)
               for rec, score, matched in scored[:limit]]

    return {
        "domain": domain,
        "query": query_text,
        "record_count": record_count,
        "threshold": min_records_threshold,
        "backend": resolved,
        "fell_back": resolved == "keyword" and backend != "keyword",
        "fallback_reason": fallback_reason,
        "filters_applied": filters_applied,
        "candidates_after_filter": len(candidates),
        "results": results,
    }


def reindex(domain: str, *, memory_root: Path | None = None,
            cache: bool = True) -> dict:
    """Rebuild the embedding cache for ``domain``.

    Embeds any records whose hash is not cached and drops cache rows whose hash
    no longer corresponds to a current record. No-op (no cache file created)
    when no embedding backend is available.
    """
    if memory_root is None:
        root = resolve_memory_root(None)
    else:
        root = Path(memory_root).expanduser()

    jsonl = root / domain / "experiences.jsonl"
    records = load_records(jsonl)

    emb = get_embedding_backend()
    if emb is None:
        return {
            "domain": domain,
            "backend": "keyword",
            "reindexed": False,
            "reason": "no embedding backend available; keyword search needs no index",
            "total": len(records),
        }

    cache_path = root / domain / INDEX_FILENAME
    current_hashes = {record_hash(rec) for rec in records}

    conn = _connect_cache(cache_path)
    before = {h for (h,) in conn.execute(
        "SELECT record_hash FROM embeddings WHERE model = ?", (emb.model,))}
    stale = before - current_hashes
    for h in stale:
        conn.execute(
            "DELETE FROM embeddings WHERE record_hash = ? AND model = ?",
            (h, emb.model))
    conn.commit()
    conn.close()

    vecs = _build_embeddings(records, emb, cache_path, cache=cache)
    embedded = len(current_hashes - before)
    return {
        "domain": domain,
        "backend": "embedding",
        "model": emb.model,
        "reindexed": True,
        "embedded": embedded,
        "reused": len(vecs) - embedded,
        "removed": len(stale),
        "total": len(records),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_table(result: dict) -> None:
    backend = result["backend"]
    tag = backend.upper()
    if result["fell_back"]:
        tag += f" (fell back: {result['fallback_reason']})"
    print(f"# {result['domain']} — query: {result['query']!r}")
    print(f"# backend={tag}  records={result['record_count']} "
          f"candidates={result['candidates_after_filter']} "
          f"threshold={result['threshold']}")
    if result["filters_applied"]:
        print(f"# filters: {result['filters_applied']}")
    if not result["results"]:
        print("# no matching records")
        return
    for i, r in enumerate(result["results"], 1):
        print(f"\n[{i}] score={r['score']}  {r.get('design_name')}"
              f"  pdk={r.get('pdk')}  tool={r.get('tool_used')}"
              f"  ({r.get('timestamp')})")
        if r["matched_terms"]:
            print(f"    matched: {', '.join(r['matched_terms'])}")
        for fix in r["fixes_applied"]:
            print(f"    fix: {fix}")
        if r.get("notes"):
            print(f"    notes: {r['notes']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Semantic / keyword search over experience records")
    parser.add_argument("--domain", required=True, choices=VALID_DOMAINS,
                        help="Domain to search")
    parser.add_argument("--query", default=None,
                        help="Natural-language query (required unless --reindex)")
    parser.add_argument("--design", default=None, help="Filter by design_name")
    parser.add_argument("--pdk", default=None, help="Filter by pdk")
    parser.add_argument("--tool", default=None, help="Filter by tool_used")
    parser.add_argument("--limit", type=int, default=5, help="Max results")
    parser.add_argument("--min-records", type=int, default=50,
                        help="Embedding threshold (default: 50)")
    parser.add_argument("--backend", choices=["auto", "keyword", "embedding"],
                        default="auto", help="Backend selection")
    parser.add_argument("--memory-root", default=None,
                        help="Path to the memory/ directory")
    parser.add_argument("--reindex", action="store_true",
                        help="Rebuild the embedding cache, then exit")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    # Bad explicit memory root → exit 2 (mirrors qor_trends.py).
    if args.memory_root and not Path(args.memory_root).expanduser().is_dir():
        print(f"error: memory root not found: {args.memory_root}", file=sys.stderr)
        return 2

    try:
        if args.reindex:
            summary = reindex(args.domain, memory_root=args.memory_root)
            print(json.dumps(summary, indent=2))
            return 0

        if not args.query:
            print("error: --query is required (or use --reindex)", file=sys.stderr)
            return 2

        filters = {
            "design_name": args.design,
            "pdk": args.pdk,
            "tool_used": args.tool,
        }
        result = query_experiences(
            args.domain, args.query,
            filters=filters, limit=args.limit,
            min_records_threshold=args.min_records,
            memory_root=args.memory_root, backend=args.backend,
        )
    except Exception as exc:  # noqa: BLE001 — surface as exit-2 per CLI contract
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_table(result)

    return 0 if result["results"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
