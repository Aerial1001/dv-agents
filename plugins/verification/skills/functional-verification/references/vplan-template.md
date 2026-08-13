# <Design> Verification Plan

## Document Control

| Field | Value |
|---|---|
| Plan revision | `VP-REV-001` |
| Design | `<name>` |
| Specification baseline | `<path and revision/hash>` |
| RTL baseline | `<filelist and revision/hash>` |
| DUT top | `<module>` |
| Verification top | `<module>` |
| Owner | `<name>` |
| Status | `PROPOSED` |

`PROPOSED` is the only status written in this builder-owned document. The
immutable reviewer result, workflow ledger, and explicit human approvals are
authoritative for plan acceptance and freezing; do not signal approval by
editing this field.

## Priority Semantics

Define the explicit execution order. Do not rely on lexical sorting.

| Priority | Meaning | Exit rule |
|---|---|---|
| `P1` | Must-have behavior | All mapped mandatory tests pass or have approved waivers |
| `P2` | Important behavior | All mapped mandatory tests pass or have approved waivers |
| `P3` | Remaining planned behavior | All mandatory tests pass or have approved waivers |

Smoke is a separate bring-up gate and is not a feature priority.

## Inputs and Assumptions

- Clocks and frequencies:
- Reset polarity, synchrony, and sequencing:
- Interfaces and protocol versions:
- Parameters/configurations:
- Expected simulator and UVM version:
- External models or packages:
- Explicit assumptions:

## Specification Gaps

| Gap ID | Requirement/source | Ambiguity or conflict | Affected IDs | Owner | Decision/status |
|---|---|---|---|---|---|
| `SPEC-GAP-001` | `<section>` | `<unknown behavior>` | `REQ-...` | Human | OPEN |

Do not invent expected behavior for an open gap.

## TB Architecture

Describe the component hierarchy and transaction flow. Include:

- DUT and interface binding
- active/passive agents, sequencers, drivers, and monitors
- monitor transaction reconstruction
- reference model/predictor and scoreboard independence
- assertion binding and clock/reset domains
- coverage collectors and sampling events
- configuration/factory ownership
- watchdog, objections, and deterministic shutdown
- filelist, compile, elaboration, simulation, and coverage entry points

### Component Map

| Component ID | Type | Interface/domain | Responsibility | Inputs | Outputs |
|---|---|---|---|---|---|
| `TB-COMP-001` | `monitor` | `<interface>` | `<responsibility>` | `<signals>` | `<transaction>` |

## Smoke Gate

| Smoke ID | Required evidence | Acceptance |
|---|---|---|
| `SMOKE-001` | Compile/elaboration log | No compile or elaboration error |
| `SMOKE-002` | Reset trace/assertion | Reset reaches the documented idle state |
| `SMOKE-003` | Driver and monitor evidence | One minimum legal transaction is observed end to end |
| `SMOKE-004` | Scoreboard/reference-model evidence | At least one real comparison completes and passes |
| `SMOKE-005` | Assertion/coverage evidence | Bindings and collectors are instantiated and active |
| `SMOKE-006` | Completion log | Watchdog remains quiet and objections end cleanly |

Where practical, include one bounded checker self-test or fault injection that
proves a critical checker can detect an error.

## Traceability Matrix

Use one row per independently executable test objective. IDs never change after
plan approval; superseded rows remain recorded.

| Requirement ID | Feature ID | Test ID | Priority | Mandatory | Dependencies | Stimulus/constraints | Independent checker/oracle | Assertions | Coverage IDs | Acceptance criteria | Implementation refs | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `REQ-001` | `FEAT-001` | `VP-T001` | `P1` | yes | none (after smoke gate) | `<legal and boundary stimulus>` | `<prediction and compare>` | `ASRT-001` | `COV-001` | `<observable pass condition>` | TBD | PLANNED |

Each in-scope requirement maps to at least one test/checker and coverage or a
documented reason why coverage does not apply.

## Feature Batches

Keep batches small enough for build, review, targeted execution, and cumulative
regression in one loop.

| Batch ID | Priority | Feature/test IDs | Dependencies | Targeted set | Cumulative set |
|---|---|---|---|---|---|
| `BATCH-P1-001` | `P1` | `FEAT-001, VP-T001` | smoke | `VP-T001` | smoke + accepted P1 tests |

## Constrained Random Plan

| Campaign ID | Test/config | Seed budget | Mandatory | Dependencies | Stop conditions | Coverage contribution |
|---|---|---:|---|---|---|---|
| `RAND-001` | `<test>` | `20` | yes | `VP-T001` | Any failure is recorded and routed; budget completion | `COV-...` |

Record every seed, command, revision, result, and coverage database. A failing
seed is preserved for exact rerun.

## Coverage Model and Closure

| Coverage ID | Metric | Requirement IDs | Target percent | Mandatory | Dependencies | Sampling/bins | Exclusions/waiver owner |
|---|---|---|---:|---|---|---|---|
| `COV-001` | functional | `REQ-001` | 100 | yes | `RAND-001` | `<definition>` | none |

Define targets for functional, assertion, and applicable code coverage. Closure
actions may add legal stimulus, constraints, tests, assertions, or coverpoints;
they may not weaken checking or hide reachable bins.

## Approved Plan Inventory

The approving reviewer returns a machine-readable inventory derived from these
tables. Keep every referenced ID unique and every dependency explicit so the
inventory can be validated without interpreting prose:

- directed items: `id`, `kind`, `priority`, `dependencies`, `mandatory`
- random campaigns: `id`, `test`, `seed_budget`, `mandatory`, `dependencies`
- coverage items: `id`, `metric`, numeric `target`, `mandatory`, `dependencies`
- priority order: the exact top-to-bottom order in Priority Semantics

## Regression and Signoff

The frozen signoff set records:

- specification, RTL, V-plan, and TB revisions
- simulator/UVM versions and exact commands
- mandatory test list and seed manifest
- merged coverage reports and thresholds
- open/closed bug and fix-request ledger
- approved exclusions and waivers
- full regression result paths

Final acceptance requires a passing frozen regression, approved signoff audit,
no unresolved mandatory work, and explicit human approval.
