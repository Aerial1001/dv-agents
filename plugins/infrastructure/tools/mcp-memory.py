#!/usr/bin/env python3
"""
mcp-memory.py — MCP stdio server exposing semantic/keyword experience search.

Implements MCP protocol version 2024-11-05 over stdio (JSON-RPC 2.0,
newline-delimited), mirroring the protocol scaffolding of ``mcp-adapter.py``.
Unlike the tool adapter, this server does its work in-process by importing
``tools/experience_search.py`` rather than spawning a shell wrapper.

Exposes a single tool, ``query_experiences``, that ranks past experience
records in ``memory/<domain>/experiences.jsonl`` by relevance to a query and
returns a compact JSON result (ranked records + score + matched terms + which
backend ran + whether it fell back to keyword search).

Usage:
    python3 mcp-memory.py [--memory-root PATH] [--version 1.0.0]

All debug/status output goes to stderr so it never corrupts the MCP protocol
stream on stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# tools/experience_search.py is the source of truth for ranking + the memory
# root resolver. plugins/infrastructure/tools/mcp-memory.py -> repo root is parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]
_SEARCH_PATH = REPO_ROOT / "tools" / "experience_search.py"

_spec = importlib.util.spec_from_file_location("experience_search", _SEARCH_PATH)
experience_search = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(experience_search)


# ---------------------------------------------------------------------------
# MCP protocol helpers (same shape as mcp-adapter.py)
# ---------------------------------------------------------------------------

def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


TOOL_NAME = "query_experiences"


def _input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "enum": list(experience_search.VALID_DOMAINS),
                "description": "Domain to search (e.g. synthesis, pd, sta)",
            },
            "query": {
                "type": "string",
                "description": "Natural-language query, e.g. 'what fixed WNS on sky130'",
            },
            "filters": {
                "type": "object",
                "description": "Optional exact-match pre-filters",
                "properties": {
                    "design_name": {"type": "string"},
                    "pdk": {"type": "string"},
                    "tool_used": {"type": "string"},
                },
            },
            "limit": {"type": "integer", "default": 5,
                      "description": "Max results to return"},
            "min_records_threshold": {
                "type": "integer", "default": 50,
                "description": "Below this domain record count, embedding falls back to keyword",
            },
            "backend": {
                "type": "string",
                "enum": ["auto", "keyword", "embedding"],
                "default": "auto",
            },
        },
        "required": ["domain", "query"],
    }


def _handle_call(arguments: dict, memory_root: str | None) -> dict:
    domain = arguments.get("domain", "")
    query = arguments.get("query", "")
    if domain not in experience_search.VALID_DOMAINS:
        return {"error": f"unknown domain {domain!r}",
                "valid_domains": list(experience_search.VALID_DOMAINS)}
    if not isinstance(query, str) or not query.strip():
        return {"error": "query must be a non-empty string"}

    raw_filters = arguments.get("filters") or {}
    filters = {k: raw_filters[k] for k in ("design_name", "pdk", "tool_used")
               if isinstance(raw_filters, dict) and raw_filters.get(k)}

    return experience_search.query_experiences(
        domain, query,
        filters=filters,
        limit=int(arguments.get("limit", 5)),
        min_records_threshold=int(arguments.get("min_records_threshold", 50)),
        memory_root=memory_root,
        backend=arguments.get("backend", "auto"),
    )


# ---------------------------------------------------------------------------
# Main server loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP stdio server for experience semantic/keyword search")
    parser.add_argument("--memory-root", default=None,
                        help="Explicit memory root (default: auto-detect)")
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()

    description = (
        "Search past chip-design experience records by similarity; returns "
        "ranked prior fixes with scores, matched terms, and the backend used")

    print(f"[mcp-memory] starting (memory_root={args.memory_root or 'auto'})",
          file=sys.stderr)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            print(f"[mcp-memory] malformed JSON ignored: {raw_line[:100]}",
                  file=sys.stderr)
            continue

        method: str = req.get("method", "")
        req_id = req.get("id")
        _raw_params = req.get("params")
        params: dict = _raw_params if isinstance(_raw_params, dict) else {}

        if req_id is None:
            print(f"[mcp-memory] notification: {method}", file=sys.stderr)
            continue

        print(f"[mcp-memory] request id={req_id} method={method}", file=sys.stderr)

        if method == "initialize":
            _send(_ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memory-mcp", "version": args.version},
            }))

        elif method == "ping":
            _send(_ok(req_id, {}))

        elif method == "tools/list":
            _send(_ok(req_id, {
                "tools": [{
                    "name": TOOL_NAME,
                    "description": description,
                    "inputSchema": _input_schema(),
                }]
            }))

        elif method == "tools/call":
            call_name: str = params.get("name", "")
            _raw_args = params.get("arguments")
            if _raw_args is not None and not isinstance(_raw_args, dict):
                _send(_err(req_id, -32602, "Invalid params: 'arguments' must be an object"))
                continue
            call_inputs: dict = _raw_args if isinstance(_raw_args, dict) else {}

            if call_name != TOOL_NAME:
                _send(_err(req_id, -32602,
                           f"Unknown tool '{call_name}'; this server exposes '{TOOL_NAME}'"))
                continue

            try:
                result = _handle_call(call_inputs, args.memory_root)
            except Exception as exc:  # noqa: BLE001
                _send(_ok(req_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                }))
                continue

            _send(_ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": "error" in result,
            }))

        else:
            _send(_err(req_id, -32601, f"Method not found: {method}"))


if __name__ == "__main__":
    main()
