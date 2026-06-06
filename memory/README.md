# Agent Memory System

This directory holds persistent, file-based memory for the digital chip design orchestrators.
Agents read it at session start, write a run-state file before the first stage, and upsert
an experience record after each stage completes — no new infrastructure required.

## Two-Tier Design

### Tier 1 — `experiences.jsonl`
JSONL file with per-stage upsert/overwrite by run_id. One record per orchestrator run,
updated as stages complete. Machine-parseable; grows over time; never edited manually.

### Tier 2 — `knowledge.md`
Human- and agent-readable distilled summary. Seeded with known failure patterns, successful
tool flags, and PDK/tool quirks. Intended to be periodically updated by a memory-keeper skill
(see `FUTURE_WORK.md`) as experience records accumulate.

## Experience Record Schema

```json
{
  "run_id": "<domain>_<YYYYMMDD>_<HHMMSS>",
  "timestamp": "<ISO-8601>",
  "domain": "<domain>",
  "design_name": "<from state>",
  "pdk": "<from state if known, else null>",
  "tool_used": "<primary tool>",
  "stages_completed": ["<stage>", "..."],
  "loop_backs": {"<stage>": "<count>", "..."},
  "key_metrics": { "<domain-specific fields — see table below>" },
  "issues_encountered": ["<description>", "..."],
  "fixes_applied": ["<description>", "..."],
  "signoff_achieved": true,
  "notes": "<free-text observations>"
}
```

## Domain key_metrics Fields

| Domain       | key_metrics fields                                                    |
|--------------|-----------------------------------------------------------------------|
| architecture | `selected_arch`, `estimated_mhz`, `estimated_area_um2`              |
| compiler     | `isa_tests_passed`, `abi_compliant`, `regression_pass_rate`          |
| dft          | `scan_coverage_pct`, `atpg_fault_coverage_pct`                       |
| firmware     | `build_pass`, `flash_size_kb`, `bsp_tests_passed`                    |
| formal       | `proved`, `failed`, `unknown`                                         |
| fpga         | `lut_count`, `fmax_mhz`, `timing_met`                                |
| hls          | `latency_cycles`, `dsp_count`, `ii_achieved`                         |
| infrastructure | `tools_detected`, `tools_missing`, `wrappers_deployed`, `mcp_servers_configured`, `module_system`, `tool_versions` |
| pd           | `wns_ns`, `drc_violations`, `lvs_errors`, `gds_area_um2`            |
| rtl-design   | `lint_errors`, `cdc_violations`, `synth_check_pass`                  |
| soc          | `ip_blocks_integrated`, `simulation_pass`, `memory_map_conflicts`    |
| sta          | `setup_wns_ns`, `hold_wns_ns`, `tns_ns`, `failing_paths`            |
| synthesis    | `wns_ns`, `cells`, `area_um2`, `lec_unmatched`                       |
| verification | `functional_coverage_pct`, `regression_failures`, `assertions_triggered` |

## Directory Layout

```
memory/
├── README.md                    ← this file
├── designs/                     ← per-design metric history (future use)
│   └── .gitkeep
├── architecture/
│   ├── knowledge.md             ← Tier 2: seeded domain knowledge
│   ├── experiences.jsonl        ← Tier 1: created on first run
│   └── run_state.md             ← active run identity (created at session start)
├── compiler/
├── dft/
├── firmware/
├── formal/
├── fpga/
├── hls/
├── infrastructure/             ← opt-in, environment-keyed (see note below)
├── pd/
├── rtl-design/
├── soc/
├── sta/
├── synthesis/
└── verification/
```

### Infrastructure memory (opt-in, environment-keyed)

The `infrastructure` domain is **opt-in**: the infrastructure-orchestrator writes an
`experiences.jsonl` record only when `design_state.pipeline_config.track_infrastructure` is
`true` (or it is invoked with `--track-memory`). Default is off — infrastructure state is
environment-specific and lockfiles remain the primary version source of truth, so memory is
written only when tool-version mismatches have caused repeated cross-session debugging.

When enabled, each record carries an `environment` fingerprint (`host`, `os`, `os_version`,
`arch`) and a `key_metrics.tool_versions` map. Records are **environment-keyed**: prefer entries
whose `environment.host`/`os` matches the current machine, since versions and quirks do not
transfer across hosts.

## Design State

`design_state.json` is a cross-orchestrator shared file written to the working directory.
It persists spec, interfaces, constraints, and per-domain outputs across all 15 orchestrator
boundaries. Every orchestrator reads it at session start (after `knowledge.md`) and performs
an atomic read-modify-write at session end (alongside `experiences.jsonl`).

Key top-level fields:
- `spec` — raw and structured product specification (written by architecture)
- `interfaces` — AXI/protocol interface list (written by architecture)
- `constraints` — shared timing, area, and power targets (written by architecture)
- `architecture`, `rtl`, `synthesis`, `sta`, `pd`, ... — per-domain signoff state
- `history[]` — append-only execution trace; one entry per **stage** (not per run — as of format_version 1.3), each with: `timestamp`, `agent`, `stage`, `decision` (`proceed|escalate|abandoned|await_approval`), `confidence` (`high|medium|low`), `failure_class` (see taxonomy below), `retry_strategy` (`none|regenerate|refine|escalate`, mapped from `failure_class`; format_version 1.5+), `suggested_next_step` (`proceed|loop_back_to:<stage>|retry_stage|escalate|abandon`), `reason`, `constraint_ref`
- `fix_requests[]` — structured RTL fix requests written by verification/formal on DUT bug; consumed by RTL orchestrator and dispatched by pipeline-orchestrator (format_version 1.2+)
- `cross_domain_iteration_count` — integer count of verification↔RTL feedback cycles driven by pipeline-orchestrator; capped at 3 before escalation
- `pipeline_config.checkpoints` — list of stage names requiring human approval before the orchestrator may declare signoff (format_version 1.3+). Empty/absent ⇒ fully autonomous. Example: `["arch_signoff", "rtl_signoff", "signoff"]`. Written by user; never overwritten by orchestrators.
- `approved_checkpoints[]` — checkpoints cleared by the user. Each entry: `{ "stage": "<name>", "approved_at": "<ISO-8601>", "approved_by": "user" }` (format_version 1.3+).
- `pending_approval` — non-null when a human decision is required. Field `type` distinguishes: `"checkpoint"` (proactive gate set by a domain orchestrator at its sign-off boundary) vs `"escalation"` (failure-driven, set by pipeline-orchestrator). Additional fields: `stage` (checkpoint stage name or null), `agent` (orchestrator that set it), `reason`, `fix_request_id`, `last_summary`, `requires_user`.

`failure_class` values in `history[]` entries: `none` (PASS), `functional`, `timing`, `power_area`, `drc_lvs`, `coverage_gap`, `connectivity`, `tool_error`, `spec_gap`, `resource_limit`. Distinct from `fix_request.failure_class` which is scoped to verification/formal root causes.

`format_version` tiers:
- `"1.1"` — `fix_requests[]` and `cross_domain_iteration_count` present
- `"1.2"` — history entries carry standardized `confidence`/`failure_class`/`suggested_next_step`
- `"1.3"` — `pipeline_config.checkpoints`, `approved_checkpoints[]`, `pending_approval.type/stage/agent`; per-stage history entries (one per stage, not one per run)
- `"1.4"` — authoritative `constraints` object, stage-entry constraint validation, `pending_approval.type: "constraint_gap"`
- `"1.5"` — every history entry carries `retry_strategy` (`none|regenerate|refine|escalate`), deterministically mapped from `failure_class`; escalations include `failure_class` + actionable guidance

When `fix_requests[]` contains entries with `status=open`, the chip-design-meta `pipeline-orchestrator` is responsible for routing them to the RTL orchestrator for fixing, then re-running verification.

Atomic write protocol with multi-writer protection: acquire an exclusive lock (e.g., flock or application-level mutex) around the entire read-modify-write sequence → read `design_state.json` (or {}) and record a version/checksum → modify → write to a unique temp file (e.g., `design_state.<pid>.<uuid>.tmp`) → re-check that the version/checksum of `design_state.json` is unchanged (or retry on mismatch) → rename temp to `design_state.json` while still holding the lock → release the lock. This prevents both partial writes and lost updates from concurrent orchestrators. Apply the same pattern to `experiences.jsonl` upsert operations if multiple writers can touch it.

## How Orchestrators Use This

**Session start**: Read `memory/<domain>/knowledge.md` and `memory/<domain>/run_state.md`
before the first stage. `knowledge.md` provides known failure patterns and tool flags.
`run_state.md` (if present) identifies an interrupted run to resume.

**Before first stage**: Write `memory/<domain>/run_state.md` with `run_id`, `design_name`,
`tool`, `start_time`, and `last_stage`. Update `last_stage` after each stage completes.

**Per stage**: Upsert (create-or-replace by `run_id`) one JSON line in
`memory/<domain>/experiences.jsonl` with `signoff_achieved: false` and the metrics
available so far. On final sign-off, set `signoff_achieved: true`. Do not append a second
line for the same `run_id` — overwrite the existing line.

**Optional — claude-mem index**: If `mcp__plugin_ecc_memory__add_observations` is available
in the session, also emit each applied fix as an observation to entity
`chip-design-<domain>-fixes`. Skip silently if the tool is absent — JSONL is the canonical
record; claude-mem is a supplemental cross-session search index only.

## Distilling New Knowledge

After enough runs accumulate (default threshold: 5 records), merge new learnings back
into `knowledge.md`:

```text
/chip-design-infrastructure:memory-keeper --domain synthesis
/chip-design-infrastructure:memory-keeper --all --min-records 10
```

The `memory-keeper` skill reads the JSONL records, identifies new issue/fix patterns and
tool flags not already captured, and updates the relevant sections of `knowledge.md`
without discarding still-valid content.

## QoR Trend Analysis

Track how key metrics evolve across runs for a named design with `tools/qor_trends.py`:

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

Regression alerts fire automatically when a metric moves in the wrong direction between
runs (e.g. WNS degrades, coverage drops).
