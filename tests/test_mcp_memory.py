"""MCP smoke tests for plugins/infrastructure/tools/mcp-memory.py.

Drives the server over stdin with newline-delimited JSON-RPC and asserts the
responses on stdout, mirroring the MCP protocol shape of mcp-adapter.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import write_experiences

SERVER = (Path(__file__).resolve().parents[1]
          / "plugins/infrastructure/tools/mcp-memory.py")


def _drive(requests: list[dict], memory_root: str | None = None) -> list[dict]:
    args = [sys.executable, str(SERVER)]
    if memory_root:
        args += ["--memory-root", memory_root]
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(args, input=payload, capture_output=True, text=True)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _records():
    return [{
        "run_id": "synthesis_1", "timestamp": "2026-01-01T00:00:00Z",
        "design_name": "demo_cpu", "pdk": "sky130", "tool_used": "yosys",
        "key_metrics": {"wns_ns": -0.35},
        "issues_encountered": ["wns -0.35ns on critical path"],
        "fixes_applied": ["upsized drivers to close wns on sky130"],
        "notes": "sky130 timing closure",
    }]


def test_initialize_reports_protocol_version():
    resps = _drive([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    assert resps[0]["id"] == 1
    result = resps[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "memory-mcp"


def test_tools_list_exposes_query_experiences():
    resps = _drive([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = resps[0]["result"]["tools"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "query_experiences"
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    for key in ("domain", "query", "filters", "limit", "min_records_threshold"):
        assert key in schema["properties"]
    assert set(schema["required"]) == {"domain", "query"}


def test_tools_call_returns_results(tmp_path):
    mem = tmp_path / "memory"
    write_experiences(mem, "synthesis", _records())
    resps = _drive([{
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "query_experiences",
                   "arguments": {"domain": "synthesis",
                                 "query": "what fixed wns on sky130"}},
    }], memory_root=str(mem))
    result = resps[0]["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["backend"] == "keyword"
    assert payload["results"]
    assert payload["results"][0]["run_id"] == "synthesis_1"


def test_unknown_method_returns_minus_32601():
    resps = _drive([{"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"}])
    assert resps[0]["error"]["code"] == -32601


def test_unknown_tool_returns_minus_32602():
    resps = _drive([{
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "not_a_tool", "arguments": {}},
    }])
    assert resps[0]["error"]["code"] == -32602


def test_bad_arguments_type_returns_minus_32602():
    resps = _drive([{
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "query_experiences", "arguments": "not-an-object"},
    }])
    assert resps[0]["error"]["code"] == -32602
