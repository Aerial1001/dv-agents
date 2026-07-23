# DV Verification Pipeline — Master Index
## Agent + Skill Architecture for Design Verification

> **Purpose**: This document maps the verification flow documents and defines how orchestrators hand off to each other across the verification pipeline.

---

## Pipeline Overview

```
                     ┌─────────────────────────────────────────────────────┐
                     │          0. INFRASTRUCTURE SETUP                    │
                     │  Tool detection, wrappers, MCP config               │
                     └────────────────────┬────────────────────────────────┘
                                          │
                     ┌────────────────────▼────────────────────────────────┐
                     │  1. FUNCTIONAL VERIFICATION (UVM)                   │
                     │  TB arch, test plan, UVM build, coverage, regression│
                     │  DUT bug → fix_request → RTL designer               │
                     └────────────────────┬────────────────────────────────┘
                                          │ Verified RTL
                     ┌────────────────────▼────────────────────────────────┐
                     │  2. SoC IP INTEGRATION (if chip-level work)         │
                     │  IP procurement, bus fabric, chip-level simulation  │
                     └─────────────────────────────────────────────────────┘
```

---

## Document Index

| # | Document | Description | Input | Output |
|---|----------|-------------|-------|--------|
| 0 | `Infrastructure_Setup_Flow.md` | EDA tool detection, wrapper deployment, MCP config | Host environment | tool-manifest.json, wrappers, MCP snippets |
| 1 | `Functional_Verification_Flow.md` | UVM TB, coverage, regression | RTL + spec | Verified RTL + sign-off |
| 2 | `SoC_IP_Integration_Flow.md` | IP procurement, SoC assembly | IP list + arch | Integrated SoC RTL |

---

## Inter-Orchestrator Handoff Contracts

### RTL Design → Verification
```json
{
  "handoff": "rtl_to_verif",
  "from": "RTL Designer",
  "to":   "Verification Orchestrator",
  "package": {
    "rtl_filelist":  "filelist.f",
    "lint_report":   "lint_clean.rpt",
    "cdc_report":    "cdc_clean.rpt",
    "compile_order": "compile_order.f",
    "assertions":    "assertions.sva"
  }
}
```

### Verification → RTL Designer (via fix_request)
```json
{
  "handoff": "verif_to_rtl",
  "from": "Verification Orchestrator",
  "to":   "RTL Designer",
  "package": {
    "fix_request_id": "fr_<uuid>_<date>_<seq>",
    "test_name": "<directed test name>",
    "waveform_path": "<path>",
    "suspected_rtl": { "module": "<name>", "signal": "<name>", "file": "<path>" },
    "summary": "<one-line bug description>",
    "expected_behavior": "<spec excerpt>",
    "observed_behavior": "<observed>"
  }
}
```

### Verification → SoC
```json
{
  "handoff": "verif_to_soc",
  "from": "Verification Orchestrator",
  "to":   "SoC Integration Orchestrator",
  "package": {
    "rtl_filelist": "filelist.f",
    "verification_signoff": true,
    "coverage_pct": 100
  }
}
```
