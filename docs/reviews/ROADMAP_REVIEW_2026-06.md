# Roadmap & Progress Review — June 2026

A multi-perspective review of the `FUTURE_WORK.md` roadmap (14 items; 9 shipped, 5 open)
and the repository's current state, conducted by three independent reviewers:
an **Implementation Auditor** (do the "shipped" claims hold up against the code?),
a **QA & CI Reviewer** (is progress protected against regression?), and a
**Product & Roadmap Strategist** (is the roadmap process itself healthy?).

**Repository state reviewed:** commit `f6ba8dc` (merge of PR #61), package version 1.3.0,
design_state `format_version` 1.5.

---

## TL;DR

The roadmap is unusually honest and well-executed: every item marked shipped was
independently verified against real artifacts, prerequisites were respected in shipping
order, and the format_version ladder (1.1 → 1.5) is consistent across schema, CHANGELOG,
fixtures, and all 15 orchestrators. The consensus weakness, reached independently by all
three reviewers, is that **validation stops at structure and the system is unproven in
use**: CI checks shapes, not content or behavior; the install path is untested; and zero
experience records exist, so the entire memory subsystem has never run on real data.
The next-highest-value work is *exercising* the system, not adding features.

---

## Perspective 1 — Implementation Audit: are the "shipped" claims real?

**Verdict: VERIFIED, with one documentation discrepancy.**

| Item | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Memory-keeper skill | ✅ Verified | `plugins/infrastructure/skills/memory-keeper/SKILL.md` + `distill.py` (threshold guard, `--domain`/`--all`) |
| 3 | QoR trending | ✅ Verified | `tools/qor_trends.py`; filter-before-group caveats documented in both roadmap and tool |
| 4 | Infrastructure memory | ✅ Verified | `memory/infrastructure/`; opt-in + environment-keyed behavior in `memory/README.md` and orchestrator Behaviour Rule 8; registered in `distill.py` `VALID_DOMAINS` |
| 5 | Central design state | ⚠️ Verified w/ discrepancy | All 15 orchestrators read/write `design_state.json`; **but `FUTURE_WORK.md` item 5 says "all 14 orchestrators"** — actual count is 15 (14 domain + 1 meta), as README and items 7/11 correctly state. *(Fixed alongside this review.)* |
| 6 | Continuous verification loop | ✅ Verified | `plugins/meta/agents/pipeline-orchestrator.md`; `fix_request` schema at format_version 1.1 |
| 7 | Agent contract standardization | ✅ Verified | `confidence`/`failure_class` (10-value enum)/`suggested_next_step` in all 15 orchestrators; format_version 1.2 |
| 8 | Constraint awareness | ✅ Verified | 11 SKILL files reference `design_state.constraints.<key>`; stage-entry validation + `constraint_gap` halt; format_version 1.4 |
| 10 | Structured failure handling | ✅ Verified | `retry_strategy` in all 15 orchestrators; failure_class → retry_strategy mapping **schema-enforced via `allOf`** in `docs/design_state.schema.json`; format_version 1.5 |
| 11 | Checkpoints + observability | ✅ Verified | `pipeline_config.checkpoints`, `approved_checkpoints[]`, `pending_approval.type` discriminator; gate logic in all 15 orchestrators; format_version 1.3 |
| 14 | `route_to` (reserved) | ✅ Verified as described | Present in schema `$defs.fixRequest.route_to`, **not consumed** by `dispatch_to_producer` — exactly as the roadmap states |

**Minor finding:** `constraint_ref` is documented in prose (`memory/README.md`,
orchestrator files) but not formalized in the schema's `historyEntry` definition — it
passes only via `additionalProperties: true`. Functionally fine; spec is split between
schema and prose.

**What this perspective says is done right:** the repo does not over-claim. Every
✓-marked item maps to a real artifact, and the format_version story (1.1 fix_requests →
1.2 output contract → 1.3 checkpoints → 1.4 constraints → 1.5 retry strategy) is coherent
end-to-end. That level of doc/code agreement is rare.

---

## Perspective 2 — QA & CI: is progress protected against regression?

### Done right

- **Manifest & path validation** (`validate.yml`): plugin.json structure, path-traversal
  protection, file-existence checks, marketplace.json cross-referencing, count consistency.
- **Schema validation with positive AND negative fixtures**, auto-discovered under
  `plugins/meta/skills/pipeline-orchestration/examples/` — new fixtures join CI without
  code changes.
- **Structural checks** on all 16 SKILL files and 15 orchestrator files (required
  frontmatter fields and sections).
- **23 pytest cases** covering the core logic of `distill.py` and `qor_trends.py`
  (malformed-JSONL handling, regression detection, threshold gating, CLI exit codes).
- **Pinned CI dependencies** (jsonschema 4.23.0, pytest, Python 3.11) — reproducible runs.

### Gaps, ranked by risk

1. **CRITICAL — SKILL.md *content* is not validated.** CI asserts section headers exist
   (`## Domain Rules`, `## QoR Metrics`, …) but never inspects what's inside. The product
   is mostly markdown prompt files; an emptied rules section or a vague QoR metric ships
   with green CI. There is currently **no mechanism by which a prose regression in the
   core product can be caught automatically.**
2. **CRITICAL — the install path is completely untested.** `install.sh`, `install.ps1`,
   `bin/install.mjs`, `bin/detect.mjs` (~500+ LOC, including the recent agent
   auto-detection feature) have zero CI coverage on any platform. This is the first thing
   every user touches.
3. **HIGH — no cross-doc/reference validation** across ~71 markdown files. E.g.,
   `memory_root.py` is referenced from 16 files with no check it exists; internal links
   and anchors are never verified; FUTURE_WORK ✓-marks aren't cross-checked (the item-5
   "14 orchestrators" typo is exactly the class of drift this would catch).
4. **MEDIUM-HIGH — no linting or type checking.** No ruff/mypy/pylint config for Python
   (despite type hints throughout), no eslint/prettier for `bin/*.mjs`.
5. **MEDIUM — thin schema regression coverage:** only one negative fixture (a single bad
   enum); no fixtures exercising older format_versions 1.1–1.4 for backward-compat; no
   end-to-end memory cycle test (orchestrator-format write → distill → trend); the
   output-side functions of `qor_trends.py` (`print_table`, `plot_chart`,
   `find_memory_root`) are untested; IDE config validation checks counts/existence, not
   that mappings point at valid domains.

**Summary:** a high-quality CI setup for *data* validation, a low-quality one for the
actual product surface (prompt content + install experience).

---

## Perspective 3 — Product & Roadmap Strategy: is the process healthy?

### Done right

- **The prerequisite graph is explicit and was actually followed.** Item 5
  (design_state) landed first, then 6 → 7 → 8/10/11 in dependency order. Git history
  confirms the cadence matches the declared plan — the roadmap is a real execution
  artifact, not aspirational prose.
- **Status hygiene:** items carry explicit ✓/open tags, shipped items document *where*
  the artifact lives, and item 14 honestly distinguishes "reserved in schema" from
  "consumed by logic".
- **CHANGELOG.md functions as a progress ledger** tied to format_version bumps.

### Gaps, ranked

1. **P0 — no issue-tracker linkage.** FUTURE_WORK.md line 4 says "Track these as
   follow-up issues" — this never happened. No GitHub issues, no `#NN` commit references.
   Open items 2, 9, 12–14 are untracked prose: no velocity visibility, no place for users
   to weigh in.
2. **P0 — zero experience records.** No `experiences.jsonl` exists anywhere under
   `memory/` (only seeded `knowledge.md` files). The two-tier memory system, the
   memory-keeper skill (item 1), and qor_trends (item 3) are all shipped but have **never
   processed real data**. Item 2's ~50-records-per-domain threshold sits at 0/16 domains —
   semantic search is not approaching readiness, and the write→distill→trend cycle is
   untested in practice.
3. **P1 — item 9 is silently partial.** `architecture.candidates[]` exists in the schema
   and fixtures (empty), but no agent populates it; `refinement_needed` re-entry and the
   `candidates_evaluated`/`winning_candidate_profile` memory fields are unimplemented.
   The roadmap shows it as plainly "open" when it's really "in progress — schema ready,
   agent behavior pending."
4. **P1 — no worked end-to-end example.** Docs are thorough but purely procedural; there
   is no known-good reference run (e.g., a sky130 counter or small multiplier from spec →
   design_state at each stage) for users to validate an install against.
5. **P1 — no agent-output quality benchmarking, no real EDA-tool integration.** Nothing
   measures whether generated RTL synthesizes, whether timing closes, or whether quality
   regresses release-over-release. Schema-valid ≠ synthesizable. This is the biggest
   strategic blind spot *not on the roadmap at all*.
6. **P2 — assorted process debt:** item 14 isn't annotated as blocked on 12/13; the four
   `ides/` targets have no drift protection when SKILL files change; no
   version/compatibility migration guide (package 1.3.0 vs format_version 1.5 — what does
   an upgrade require?); no EDA tool licensing/cost matrix (skills reference Genus/
   Spectre/ZeBu without flagging paid-tool requirements); no plan for marketplace
   discoverability if items 12/13 push the catalog past 25 plugins.

---

## Cross-Perspective Synthesis

**Consensus strength — execution discipline.** What's marked shipped is genuinely
shipped, in dependency order, with the data contract guarded by real schema validation.
The audit found essentially no over-claiming across 9 items and 5 format versions.

**Consensus weakness — validation stops at structure; the system is unproven in use.**
Three reviewers reached the same conclusion from different directions:

- The auditor found documentation drift that only discipline (not CI) prevents.
- QA found that the actual product — prompt content and install behavior — has no
  automated safety net.
- Strategy found that no real run has ever exercised the memory system the last five
  roadmap items were built around.

The architecture is ahead of its evidence. **The recommended pivot: before building
items 12/13 (more domains, more agents), invest in proving and protecting what exists.**

## Prioritized Recommendations

| Priority | Action | Roadmap linkage |
|---|---|---|
| P0 | Create GitHub issues for open items 2, 9, 12, 13, 14 (label `roadmap`, link back to FUTURE_WORK sections) | Fulfills FUTURE_WORK's own stated process |
| P0 | Seed realistic `experiences.jsonl` records (5–10 per domain for synthesis/PD/verification, e.g. from OpenROAD sky130 runs) and run memory-keeper + qor_trends against them in CI | Unblocks items 1/3 in practice; starts the item-2 clock |
| P1 | Re-status item 9 as "in progress — schema ready, agent implementation pending" (or split it) | Roadmap accuracy |
| P1 | Add a worked end-to-end tutorial (`docs/TUTORIAL.md`) with expected `design_state.json` at each stage | Onboarding + install validation |
| P1 | Smoke-test the install path in CI (Linux + macOS at minimum; `npx` flow and `install.sh`) | Closes the largest untested surface |
| P1 | Add SKILL.md *content* validation (non-empty sections, parseable numbered rules, metrics with units) | Protects the core product |
| P2 | Add a docs link/reference checker; add ruff + mypy (Python) and a linter for `bin/*.mjs` | Catches drift like the item-5 count typo |
| P2 | Mark item 14 as blocked on 12/13; add an EDA tool licensing/cost matrix to `docs/INSTALL.md`; document format_version migration rules | Expectation-setting |
| P3 | Nightly open-source EDA integration job (Yosys/OpenROAD on sky130) measuring WNS/area/coverage against a baseline | Long-term quality benchmark |
