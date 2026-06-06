# digital-chip-design-agents

> Claude Code marketplace plugin — full digital chip design pipeline.  
> 15 plugins · 16 skill files · 13 chip-design domains + infrastructure + pipeline orchestrator · closed-loop verification↔RTL feedback.

[![Validate](https://github.com/chuanseng-ng/digital-chip-design-agents/actions/workflows/validate.yml/badge.svg)](https://github.com/chuanseng-ng/digital-chip-design-agents/actions/workflows/validate.yml)

---

## Install

### Option A — npm (recommended, no clone)

If you have Node.js (≥18), run a single command — no `git clone` and no Python
required. With no flags the installer **detects which AI coding agents you have
installed** (Claude Code, OpenAI Codex, OpenCode, Gemini, GitHub Copilot), shows
what it found and where each would write, and installs to them after a
confirmation:

```bash
npx digital-chip-design-agents            # detect installed agents + confirm
npx digital-chip-design-agents --yes      # detect + install, no prompt (CI-friendly)
```

Detection treats an agent as installed if its CLI is on `PATH` **or** its config
directory exists (e.g. `~/.claude`, `~/.codex`, `~/.config/opencode`,
`~/.gemini`). For Claude Code it copies every plugin into your plugin cache and
enables them in `settings.json`; for the others it generates the matching context
files (see Option D). All five targets are handled natively in Node — no Python.

To target a specific agent (or all of them) explicitly and skip detection:

```bash
npx digital-chip-design-agents --ide claude     # or codex | opencode | gemini | copilot | all
npx digital-chip-design-agents --ide gemini --global
```

Re-run any of these to pick up future updates. Works identically on macOS, Linux,
and Windows (a single Node process copies plugins sequentially, so there is no
concurrent-write contention on the cache directory).

### Option B — Install script

Clone the repo and run one script. Like the npm installer, running it with no
flags **auto-detects your installed agents** and installs to them after a
confirmation (add `--yes` / `-Yes` to skip the prompt). The shell scripts require
`python3`; for a Python-free install use the npm path (Option A).

**macOS / Linux / Git Bash:**
```bash
git clone https://github.com/chuanseng-ng/digital-chip-design-agents.git
cd digital-chip-design-agents
bash install.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/chuanseng-ng/digital-chip-design-agents.git
cd digital-chip-design-agents
.\install.ps1
```

Restart Claude Code after running — all 16 skills and 15 agents will be active.

### Option C — Marketplace (selective install)

If you only need specific domains, install them individually via the Claude Code
marketplace. First register the marketplace, then install the domains you need:

```text
/plugin marketplace add github:chuanseng-ng/digital-chip-design-agents
```

<details>
<summary>Individual plugin install commands (click to expand)</summary>

```text
/plugin install chip-design-architecture@digital-chip-design-agents
/plugin install chip-design-rtl@digital-chip-design-agents
/plugin install chip-design-verification@digital-chip-design-agents
/plugin install chip-design-formal@digital-chip-design-agents
/plugin install chip-design-synthesis@digital-chip-design-agents
/plugin install chip-design-dft@digital-chip-design-agents
/plugin install chip-design-sta@digital-chip-design-agents
/plugin install chip-design-hls@digital-chip-design-agents
/plugin install chip-design-pd@digital-chip-design-agents
/plugin install chip-design-soc@digital-chip-design-agents
/plugin install chip-design-compiler@digital-chip-design-agents
/plugin install chip-design-firmware@digital-chip-design-agents
/plugin install chip-design-fpga@digital-chip-design-agents
```

</details>

### Option D — Other AI assistants (Copilot / Gemini / OpenCode / Codex CLI)

These targets are auto-detected by Options A and B, but you can also install one
explicitly. The npm installer (`npx digital-chip-design-agents --ide <target>`)
and the shell scripts both support every target natively; run from your chip
design project directory with `--ide`:

```bash
# GitHub Copilot — creates .github/instructions/ in your project
bash /path/to/digital-chip-design-agents/install.sh --ide copilot
# Commit the generated .github/ files to share rules with your team.

# Gemini Code Assist — creates GEMINI.md in your project (or ~/GEMINI.md with --global)
bash /path/to/digital-chip-design-agents/install.sh --ide gemini

# OpenCode — creates opencode.json in your project; use /mode chip-<domain> to activate
bash /path/to/digital-chip-design-agents/install.sh --ide opencode

# OpenAI Codex CLI — creates AGENTS.md in your project (or ~/.codex/instructions.md with --global)
bash /path/to/digital-chip-design-agents/install.sh --ide codex

# All IDEs at once (also installs Claude Code)
bash /path/to/digital-chip-design-agents/install.sh --ide all
```

**Windows (PowerShell):** replace `bash install.sh` with `.\install.ps1` and `--ide` with `-IDE`.

Domain knowledge is loaded directly from the plugin source files — no duplicate content.
Re-run the install command to pick up any future updates.

---

### Usage — describe your task in natural language

```
Run the RTL design flow for my AXI DMA controller block
Analyse timing violations on this routed DEF and suggest ECOs
Generate ATPG patterns for this DFT-inserted netlist
Build a UVM testbench for my FIFO block
```

Claude automatically loads the correct skill before executing.

---

## Available Plugins

| Plugin Name | Domain | Invoke When You Want To... |
|-------------|--------|---------------------------|
| `chip-design-architecture` | Architecture Evaluation | Explore microarch candidates, estimate PPA, assess risk |
| `chip-design-rtl` | RTL Design (SystemVerilog) | Write, lint, CDC-check, or synthesis-check RTL |
| `chip-design-verification` | Functional Verification (UVM) | Build testbench, write tests, close coverage, run regression |
| `chip-design-formal` | Formal Verification (FPV/LEC) | Prove properties, check equivalence, close formal gaps |
| `chip-design-synthesis` | Logic Synthesis | Set up SDC, run synthesis, verify netlist with LEC |
| `chip-design-dft` | Design for Test | Plan DFT, insert scan, run ATPG, set up JTAG |
| `chip-design-sta` | Static Timing Analysis | Analyse timing, guide ECO closure, sign off timing |
| `chip-design-hls` | High-Level Synthesis | Convert C/C++ to RTL, optimise directives, co-simulate |
| `chip-design-pd` | Physical Design | Full PD flow: floorplan → placement → CTS → routing → sign-off |
| `chip-design-soc` | SoC IP Integration | Qualify IPs, configure bus fabric, run chip-level sim |
| `chip-design-compiler` | Compiler Toolchain | Build LLVM/GCC backend, assembler, linker, runtime for custom ISA |
| `chip-design-firmware` | Embedded Firmware | BSP, HAL drivers, RTOS integration, firmware validation |
| `chip-design-fpga` | FPGA Emulation | Port ASIC to FPGA, bring up hardware, validate SW on prototype |
| `chip-design-infrastructure` | Infrastructure & Memory | Detect EDA tools, deploy wrappers, configure MCP servers, distil domain memory |
| `chip-design-meta` | Pipeline Orchestration | Drive closed-loop verification↔RTL feedback, manage fix_requests, enforce iteration cap |

---

## How It Works

Each plugin installs two things:

1. **A Skill** (`plugins/<domain>/skills/<domain>/SKILL.md`) — domain knowledge Claude reads
   before executing. Contains stage-by-stage rules, QoR metrics, common fixes, and output
   requirements.

2. **An Orchestrator Agent** (`plugins/<domain>/agents/<domain>-orchestrator.md`) — a subagent
   that manages the full multi-stage flow. It sequences stages, enforces pass/fail criteria,
   applies loop-back rules when a stage fails, and escalates clearly when human input is needed.

Skills are loaded autonomously by Claude when you describe a task. Orchestrators are
invoked explicitly when you want to run a complete flow end-to-end.

---

## Orchestrator Flows

Each orchestrator enforces a strict stage sequence with loop-back rules:

**Physical Design** (example):
```
floorplan → placement → CTS → routing →
timing_opt → power_opt → area_opt → signoff
```
If routing DRC fails → retry routing (max 3×).  
If signoff timing fails → loop back to timing_opt (max 2×).  
If any loop exceeds its limit → escalate to you with full state + recommendations.

All 13 domain orchestrators follow the same pattern with domain-specific stages and criteria.

---

## Repo Structure

```
digital-chip-design-agents/
│
├── .claude-plugin/
│   └── marketplace.json         ← Marketplace registry (all 15 plugins)
│
├── plugins/                     ← One isolated directory per plugin
│   ├── architecture/
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json      ← Per-plugin manifest
│   │   ├── agents/
│   │   │   └── architecture-orchestrator.md
│   │   └── skills/
│   │       └── architecture/
│   │           └── SKILL.md
│   ├── rtl-design/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/rtl-design-orchestrator.md
│   │   └── skills/rtl-design/SKILL.md
│   ├── ... (13 domain plugins, same layout each)
│   ├── infrastructure/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/infrastructure-orchestrator.md
│   │   ├── skills/infrastructure/SKILL.md
│   │   ├── skills/memory-keeper/   ← distils experiences.jsonl → knowledge.md
│   │   │   ├── SKILL.md
│   │   │   └── distill.py
│   │   └── tools/                  ← EDA wrapper scripts and MCP adapters
│   └── meta/                       ← Cross-domain pipeline orchestrator
│       ├── .claude-plugin/plugin.json
│       ├── agents/pipeline-orchestrator.md
│       └── skills/pipeline-orchestration/SKILL.md
│
├── ides/                        ← IDE-specific config files (non-Claude)
│   ├── copilot/
│   │   ├── .github/
│   │   │   └── copilot-instructions.md   ← global Copilot workspace instructions
│   │   └── applyto-map.json     ← domain → file-glob mapping for per-domain rules
│   ├── gemini/
│   │   └── gemini-header.md     ← preamble injected into generated GEMINI.md
│   ├── opencode/
│   │   └── opencode-base.json   ← base OpenCode config template
│   └── codex/
│       └── AGENTS.md            ← preamble injected into generated AGENTS.md (Codex CLI)
│
├── memory/                      ← Persistent two-tier memory (per domain)
│   ├── <domain>/knowledge.md   ← Tier 2: distilled summaries (read at session start)
│   └── <domain>/experiences.jsonl ← Tier 1: append-only run records
│
├── tools/
│   └── qor_trends.py           ← QoR metric trending and regression detection
│
└── .github/
    └── workflows/
        ├── validate.yml         ← CI: validates all files on every PR
        └── release.yml          ← CD: tags and publishes releases
```

---

## End-to-End Pipeline

The 13 design domains (+ the meta pipeline orchestrator) map to a complete chip design pipeline:

```
[Specification]
      │
      ▼
[1. Architecture Evaluation] ──► microarch doc
      │
      ├──► [2. RTL Design]  ──► [3. HLS] (algorithm blocks)
      │           │
      │           │           ├──► [4. Functional Verification] ◄──┐
      │           └──► [5. Formal Verification]    ◄──┤
      │                       │ (bug found)           │ fix_request loop
      │                       │                    [Meta / Pipeline Orch.]
      │                       ▼                       │
      │              [6. Logic Synthesis]          ────┘
      │                       │
      │           ┌───────────┼───────────┐
      │           ▼           ▼           ▼
      │      [7. DFT]  [8. Physical  [9. STA]
      │                   Design]
      │                       │
      │                   [Tape-out]
      │
      ├──► [10. SoC IP Integration]  (if SoC-level work)
      ├──► [11. Compiler Toolchain]  (if custom CPU)
      ├──► [12. Embedded Firmware]
      └──► [13. FPGA Emulation]      (pre-silicon SW dev)
```

---

## Memory System

Each domain orchestrator reads from and writes to a two-tier persistent memory store under `memory/`:

- **`memory/<domain>/knowledge.md`** — distilled summaries: known failure patterns, successful tool flags, PDK quirks. Read by every orchestrator at session start.
- **`memory/<domain>/experiences.jsonl`** — append-only run records written after every signoff or escalation.

### Distilling new knowledge

After enough runs accumulate (default threshold: 5 records), merge new learnings back into `knowledge.md`:

```text
/chip-design-infrastructure:memory-keeper --domain synthesis
/chip-design-infrastructure:memory-keeper --all --min-records 10
```

The `memory-keeper` skill reads the JSONL records, identifies new issue/fix patterns and tool flags not already captured, and updates the relevant sections of `knowledge.md` without discarding still-valid content.

### QoR trend analysis

Track how key metrics evolve across runs for a named design:

```bash
# Text table for all domains where design "aes_core" appears
python3 tools/qor_trends.py --design aes_core

# WNS trend for synthesis only, with regression alerts
python3 tools/qor_trends.py --design aes_core --domain synthesis --metric wns_ns

# Save a matplotlib chart
python3 tools/qor_trends.py --design aes_core --plot --output aes_core_qor.png

# Compare area/timing across sky130 vs gf180mcu
python3 tools/qor_trends.py --design aes_core --domain synthesis --group-by pdk

# Compare Yosys vs DC for the same design on sky130, with a grouped chart
python3 tools/qor_trends.py --design aes_core --pdk sky130 --group-by tool --plot
```

Regression alerts fire automatically when a metric moves in the wrong direction between runs (e.g. WNS degrades, coverage drops).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome for:
- Improved domain rules or QoR metrics in any SKILL.md
- New loop-back rules in orchestrators
- New skill domains (e.g., package/assembly, analog integration)

CI validates all files on every PR — the validate workflow must pass before merge.

### Shared metadata in plugin.json

Each `plugins/<domain>/.claude-plugin/plugin.json` repeats the same `author`,
`homepage`, `repository`, and `license` fields. These are intentional — the
plugin installer reads each manifest in isolation and requires these fields to
be present. The canonical values are:

```json
"author":     { "name": "chuanseng-ng", "url": "https://github.com/chuanseng-ng" },
"homepage":   "https://github.com/chuanseng-ng/digital-chip-design-agents",
"repository": "https://github.com/chuanseng-ng/digital-chip-design-agents",
"license":    "MIT"
```

When updating these fields, change all 14 `plugin.json` files and
`.claude-plugin/marketplace.json` together.

---

## License

MIT — see [LICENSE](LICENSE).
