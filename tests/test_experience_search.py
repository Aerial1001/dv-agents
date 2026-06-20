"""Unit + CLI tests for tools/experience_search.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import write_experiences

SEARCH = Path(__file__).resolve().parents[1] / "tools/experience_search.py"


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def test_tokenize_keeps_eda_tokens_and_strips_stopwords(experience_search):
    toks = experience_search.tokenize("What fixed the WNS_ns on sky130 with -flatten before?")
    assert "wns_ns" in toks
    assert "sky130" in toks
    assert "-flatten" in toks
    assert "fixed" in toks
    # stopwords gone
    for sw in ("what", "the", "on", "with", "before"):
        assert sw not in toks


def test_searchable_text_joins_freetext_fields(experience_search):
    rec = {
        "issues_encountered": ["wns -0.35ns on critical path"],
        "fixes_applied": ["upsized drivers; set_max_fanout 8"],
        "notes": "sky130 corner",
    }
    text = experience_search.searchable_text(rec)
    assert "wns" in text and "drivers" in text and "sky130" in text


# ---------------------------------------------------------------------------
# Keyword ranking
# ---------------------------------------------------------------------------

def _records():
    return [
        {
            "run_id": "synthesis_1", "timestamp": "2026-01-01T00:00:00Z",
            "design_name": "demo_cpu", "pdk": "sky130", "tool_used": "yosys",
            "key_metrics": {"wns_ns": -0.35},
            "issues_encountered": ["wns -0.35ns on critical path"],
            "fixes_applied": ["upsized drivers to close wns on sky130"],
            "notes": "sky130 timing closure",
        },
        {
            "run_id": "synthesis_2", "timestamp": "2026-01-02T00:00:00Z",
            "design_name": "demo_cpu", "pdk": "gf180mcu", "tool_used": "yosys",
            "key_metrics": {"area_um2": 45000},
            "issues_encountered": ["area over budget"],
            "fixes_applied": ["enabled area recovery pass"],
            "notes": "gf180mcu area work",
        },
    ]


def test_keyword_ranks_relevant_record_first(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.query_experiences(
        "synthesis", "what fixed wns on sky130",
        memory_root=mem, backend="keyword")
    assert out["backend"] == "keyword"
    assert out["fell_back"] is False
    assert out["results"], "expected at least one match"
    assert out["results"][0]["run_id"] == "synthesis_1"
    assert "wns" in out["results"][0]["matched_terms"]


def test_filters_pre_narrow_candidates(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.query_experiences(
        "synthesis", "wns area",
        filters={"pdk": "sky130"}, memory_root=mem, backend="keyword")
    assert out["candidates_after_filter"] == 1
    assert out["filters_applied"] == {"pdk": "sky130"}
    for r in out["results"]:
        assert r["pdk"] == "sky130"


def test_no_matching_records_returns_empty(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.query_experiences(
        "synthesis", "completely unrelated zzzqqq",
        memory_root=mem, backend="keyword")
    assert out["results"] == []


# ---------------------------------------------------------------------------
# Backend selection / threshold fallback
# ---------------------------------------------------------------------------

def test_select_backend_truth_table(experience_search):
    sb = experience_search._select_backend
    # explicit keyword: never falls back, no reason
    assert sb("keyword", 100, 50, True) == ("keyword", None)
    # auto, available + above threshold -> embedding
    assert sb("auto", 50, 50, True) == ("embedding", None)
    # auto, available but below threshold -> keyword + reason
    resolved, reason = sb("auto", 10, 50, True)
    assert resolved == "keyword" and "threshold" in reason
    # auto, unavailable -> keyword + reason
    resolved, reason = sb("auto", 100, 50, False)
    assert resolved == "keyword" and "unavailable" in reason
    # embedding requested, unavailable -> keyword + reason
    resolved, reason = sb("embedding", 100, 50, False)
    assert resolved == "keyword" and "unavailable" in reason


def test_auto_falls_back_to_keyword_below_threshold(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.query_experiences(
        "synthesis", "wns sky130", memory_root=mem,
        backend="auto", min_records_threshold=50)
    assert out["backend"] == "keyword"
    assert out["fell_back"] is True
    assert out["fallback_reason"]


def test_embedding_request_without_lib_falls_back(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.query_experiences(
        "synthesis", "wns sky130", memory_root=mem,
        backend="embedding", min_records_threshold=1)
    # No embedding backend wired in by default -> keyword fallback.
    assert out["backend"] == "keyword"
    assert out["fell_back"] is True
    assert "unavailable" in out["fallback_reason"]


# ---------------------------------------------------------------------------
# Embedding cache (with an injected stub backend)
# ---------------------------------------------------------------------------

def _make_stub(experience_search):
    class StubBackend(experience_search.EmbeddingBackend):
        name = "embedding"
        model = "stub-v1"
        dim = 4

        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += len(texts)
            # Deterministic toy embedding from token hashes.
            vecs = []
            for t in texts:
                toks = experience_search.tokenize(t)
                v = [0.0, 0.0, 0.0, 0.0]
                for tok in toks:
                    v[hash(tok) % 4] += 1.0
                vecs.append(v)
            return vecs
    return StubBackend()


def test_embedding_cache_is_incremental(experience_search, tmp_path, monkeypatch):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    stub = _make_stub(experience_search)
    monkeypatch.setattr(experience_search, "get_embedding_backend", lambda: stub)

    first = experience_search.reindex("synthesis", memory_root=mem)
    assert first["reindexed"] is True
    assert first["embedded"] == 2
    calls_after_first = stub.calls

    # Second reindex with unchanged records embeds nothing new.
    second = experience_search.reindex("synthesis", memory_root=mem)
    assert second["embedded"] == 0
    assert stub.calls == calls_after_first

    # The sqlite index file was created beside the JSONL.
    assert (mem / "synthesis" / experience_search.INDEX_FILENAME).exists()


def test_reindex_drops_stale_hashes(experience_search, tmp_path, monkeypatch):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    stub = _make_stub(experience_search)
    monkeypatch.setattr(experience_search, "get_embedding_backend", lambda: stub)

    experience_search.reindex("synthesis", memory_root=mem)
    # Rewrite with only one record -> the other becomes stale.
    write_experiences(mem, "synthesis", _records()[:1])
    out = experience_search.reindex("synthesis", memory_root=mem)
    assert out["removed"] == 1
    assert out["total"] == 1


def test_embedding_backend_used_when_eligible(experience_search, tmp_path, monkeypatch):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    stub = _make_stub(experience_search)
    monkeypatch.setattr(experience_search, "get_embedding_backend", lambda: stub)
    out = experience_search.query_experiences(
        "synthesis", "wns sky130", memory_root=mem,
        backend="embedding", min_records_threshold=1)
    assert out["backend"] == "embedding"
    assert out["fell_back"] is False


def test_reindex_no_backend_creates_no_index(experience_search, tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    out = experience_search.reindex("synthesis", memory_root=mem)
    assert out["reindexed"] is False
    assert not (mem / "synthesis" / experience_search.INDEX_FILENAME).exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run(*args):
    return subprocess.run(
        [sys.executable, str(SEARCH), *args], capture_output=True, text=True)


def test_cli_json_shape(tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    res = _run("--domain", "synthesis", "--query", "wns sky130",
               "--memory-root", str(mem), "--json")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["backend"] == "keyword"
    assert "results" in data and data["results"]


def test_cli_no_records_exits_1(tmp_path):
    mem = tmp_path / "memory"
    (mem / "synthesis").mkdir(parents=True)
    res = _run("--domain", "synthesis", "--query", "anything",
               "--memory-root", str(mem))
    assert res.returncode == 1


def test_cli_bad_memory_root_exits_2(tmp_path):
    res = _run("--domain", "synthesis", "--query", "x",
               "--memory-root", str(tmp_path / "nope"))
    assert res.returncode == 2


def test_cli_reindex_runs(tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    res = _run("--domain", "synthesis", "--reindex", "--memory-root", str(mem))
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    # No embedding backend by default -> reindex is a documented no-op.
    assert data["reindexed"] is False
