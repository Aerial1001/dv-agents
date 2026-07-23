# dv-agents

> Claude Code marketplace plugin — DV verification pipeline.  
> 4 plugins · 5 skill files · functional verification + SoC integration + EDA infrastructure · fix_request protocol for RTL designer handoff.

Forked from [digital-chip-design-agents](https://github.com/chuanseng-ng/digital-chip-design-agents) v1.3.0 — focused exclusively on design verification.

---

## Quick Start

With Node.js (≥18), install everything with one command — no clone, no Python:

```bash
npx dv-agents      # detects your AI agents and installs after a confirm
```

Then just describe your verification task in natural language:

```
Build a UVM testbench for my FIFO block with full coverage
Run regression on my AXI DMA controller and report coverage gaps
Set up Verilator + cocotb for my RTL design
Integrate these IP blocks into a SoC and run chip-level simulation
```

Claude automatically loads the correct skill before executing.

For the install script, selective marketplace install, other AI assistants
(Copilot / Gemini / OpenCode / Codex), and all flags, see
**[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Available Plugins

| Plugin Name | Domain | Invoke When You Want To... |
|-------------|--------|---------------------------|
| `chip-design-verification` | Functional Verification (UVM) | Build testbench, write tests, close coverage, run regression, report bugs via fix_request |
| `chip-design-soc` | SoC IP Integration | Qualify IPs, configure bus fabric, run chip-level simulation |
| `chip-design-infrastructure` | Infrastructure & Memory | Detect EDA tools, deploy wrappers, configure MCP servers, distil domain memory |
| `chip-design-meta` | Schema Reference | fix_request protocol, failure classification, retry strategy mapping, constraint definitions — **no agent included** |

---

## How It Works

Each plugin installs:

1. **A Skill** (`plugins/<domain>/skills/<domain>/SKILL.md`) — domain knowledge Claude reads
   before executing. Contains stage-by-stage rules, QoR metrics, common fixes, and output
   requirements.

2. **An Orchestrator Agent** (`plugins/<domain>/agents/<domain>-orchestrator.md`) — a subagent
   that manages the full multi-stage flow. It sequences stages, enforces pass/fail criteria,
   applies loop-back rules when a stage fails, and escalates clearly when human input is needed.
   *(The meta plugin provides schema reference only — no orchestrator agent.)*

Skills are loaded autonomously by Claude when you describe a task. Orchestrators are
invoked explicitly when you want to run a complete flow end-to-end.

---

## Verification Pipeline

```
[Infrastructure Setup] → [Functional Verification] → [SoC IP Integration]
                                │
                    DUT bug found? → write fix_request → escalate to RTL designer
```

The verification orchestrator writes structured `fix_request` entries to `design_state.json`
when bugs are found, with module, signal, waveform path, and expected/observed behavior.
The RTL designer consumes these entries and applies fixes — no automated RTL dispatch loop
is included in dv-agents.

---

## Memory System

Each domain orchestrator reads from and writes to a two-tier persistent memory store:

- **`memory/<domain>/knowledge.md`** — distilled summaries (failure patterns, tool flags, PDK
  quirks) read at session start.
- **`memory/<domain>/experiences.jsonl`** — append-only run records written after every signoff
  or escalation.

Distil accumulated records back into `knowledge.md` with the `memory-keeper` skill, track
QoR metrics across runs with `tools/qor_trends.py`, and search past experiences with
`tools/experience_search.py`. See **[memory/README.md](memory/README.md)** for full details.

---

## Repo Structure

```
dv-agents/
├── .claude-plugin/marketplace.json   ← Marketplace registry (4 plugins)
├── plugins/                          ← One isolated directory per plugin (skill + orchestrator)
│   ├── verification/                 ← Functional verification (UVM)
│   ├── soc/                          ← SoC IP integration
│   ├── infrastructure/               ← EDA tool detection, wrappers, MCP, memory-keeper
│   └── meta/                         ← Pipeline orchestration schema reference (skill only)
├── ides/                             ← IDE-specific config files (Copilot / Gemini / OpenCode / Codex)
├── memory/                           ← Persistent two-tier per-domain memory
├── docs/                             ← Install guide, pipeline map, and flow docs
├── tools/                            ← QoR trends, experience search
└── .github/workflows/                ← CI (validate.yml) and release (release.yml)
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome for:
- Improved verification rules or QoR metrics in SKILL.md
- New loop-back rules in orchestrators
- New verification-focused domains (e.g., formal verification, performance verification)

CI validates all files on every PR — the validate workflow must pass before merge.

---

## License

MIT — see [LICENSE](LICENSE).
