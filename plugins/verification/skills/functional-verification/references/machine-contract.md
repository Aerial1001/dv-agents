# Machine Contract (dv_flow.py)

This is the **queryable authority** for the workflow state machine. The
orchestrator (main session) and every worker read this document instead of the
`dv_flow.py` source. It is a faithful mirror of the machine's enums, schemas,
and gates; if it ever disagrees with the tool, the tool wins — fix this document
to match, do not reason from the source at dispatch time.

The companion narrative is `task-contract.md` (who may do what, revision and
lineage rules, ownership). This file holds the exact machine shapes.

## 1. Command surface

Every command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dv_flow.py" <cmd> ...`
and takes `--root <project-root>` (resolved, absolute). `*` = repeatable.

| Command | Required args | Optional args |
|---|---|---|
| `init` | `--dut-name --spec --rtl-filelist --rtl-root* --top` | `--priority-order` (default `P0,P1,P2`) |
| `new-task` | `--task-id --role --action --lineage --retry-kind` | `--phase --input-revision --parent-task-id` |
| `seal-task` | `--task-id` | — |
| `record-result` | `--task-id` | — |
| `transition` | `--to --reason` | — |
| `set-item` | `--item-id --status --reason` | `--kind --priority --last-task-id --dependency*` |
| `add-blocker` | `--kind --summary` | `--related-id*` |
| `resolve-blocker` | `--blocker-id --status --resolution` | — |
| `add-fix-request` | `--failure-task-id --summary` | `--affected-id* --evidence*` |
| `approve` | `--gate --decision --approved-by --note` | `--revision` |
| `show` | — | — |
| `validate` | — | — |

- `--role` ∈ {`builder`, `reviewer`, `runner`}; `--action` must be in
  that role's `ROLE_ACTIONS` (below).
- `--retry-kind` ∈ `RETRY_LIMITS` keys; attempts are counted per
  `(lineage_id, retry_kind)`.
- `--phase` defaults to `current_phase`; otherwise it must be the current phase
  or its direct forward transition (never into `BLOCKED`/`WAITING_HUMAN`/
  `COMPLETE` via a task).
- A child task's `--input-revision` must equal its `--parent-task-id`'s
  `output_revision`; the parent must already be `COMPLETED`.

## 2. Enums

### Role → actions (`ROLE_ACTIONS`)

| Role | Actions |
|---|---|
| builder | `WRITE_VPLAN`, `APPLY_PLAN_EDITS`, `BUILD_SMOKE_FOUNDATION`, `IMPLEMENT_FEATURE_BATCH`, `APPLY_REVIEW_FIX`, `APPLY_DEBUG_FIX`, `COVERAGE_CLOSURE` |
| reviewer | `REVIEW_VPLAN`, `REVIEW_TB`, `REVIEW_FIX`, `SIGNOFF_AUDIT` |
| runner | `PREFLIGHT`, `COMPILE_ELAB`, `RUN_CASE`, `RUN_REGRESSION`, `MERGE_COVERAGE` |

### Role → outcomes (`ROLE_OUTCOMES`)

| Role | Outcomes |
|---|---|
| builder | `READY_FOR_REVIEW`, `NO_CHANGE`, `BLOCKED` |
| reviewer | `APPROVED`, `CHANGES_REQUIRED`, `BLOCKED` |
| runner | `PASS`, `ENVIRONMENT_ERROR`, `COMPILE_ERROR`, `ELABORATION_ERROR`, `SIMULATION_FAILURE`, `TIMEOUT`, `COVERAGE_GAP`, `BLOCKED` |

`DIAGNOSED` / `NEEDS_MORE_EVIDENCE` are not runner outcomes — they are the
`state` of the embedded `payload.diagnosis` carried by a failing execution
result (`COMPILE_ERROR` / `ELABORATION_ERROR` / `SIMULATION_FAILURE` / `TIMEOUT`).

`agent_status` pairs: `FAILED`→`INTERNAL_ERROR`, `BLOCKED`→`BLOCKED`,
`COMPLETED`→one of the role outcomes above.

### Phases (`PHASES`)

`INIT`, `PLAN`, `PREFLIGHT`, `SMOKE`, `FEATURES`, `RANDOM`, `COVERAGE`,
`REGRESSION`, `SIGNOFF`, `WAITING_HUMAN`, `BLOCKED`, `COMPLETE`.

### Phase transitions (`TRANSITIONS`)

| From | To |
|---|---|
| `INIT` | `PLAN`, `BLOCKED` |
| `PLAN` | `PLAN`, `PREFLIGHT`, `BLOCKED`, `WAITING_HUMAN` |
| `PREFLIGHT` | `SMOKE`, `BLOCKED`, `WAITING_HUMAN` |
| `SMOKE` | `SMOKE`, `FEATURES`, `BLOCKED`, `WAITING_HUMAN` |
| `FEATURES` | `FEATURES`, `RANDOM`, `COVERAGE`, `BLOCKED`, `WAITING_HUMAN` |
| `RANDOM` | `RANDOM`, `COVERAGE`, `FEATURES`, `BLOCKED`, `WAITING_HUMAN` |
| `COVERAGE` | `COVERAGE`, `FEATURES`, `REGRESSION`, `BLOCKED`, `WAITING_HUMAN` |
| `REGRESSION` | `REGRESSION`, `FEATURES`, `COVERAGE`, `SIGNOFF`, `BLOCKED`, `WAITING_HUMAN` |
| `SIGNOFF` | `SIGNOFF`, `COMPLETE`, `BLOCKED`, `WAITING_HUMAN` |
| `WAITING_HUMAN` | any phase except `INIT`/`COMPLETE` |
| `BLOCKED` | any phase except `INIT`/`COMPLETE` |
| `COMPLETE` | (terminal) |

### Work-item statuses (`ITEM_STATUSES`)

`PENDING`, `BUILDING`, `AWAITING_REVIEW`, `CHANGES_REQUIRED`, `READY_TO_RUN`,
`RUNNING`, `DEBUGGING`, `FIXING`, `PASSED`, `BLOCKED_DUT`, `BLOCKED_SPEC`,
`WAIVED`, `TERMINAL_FAILURE`.

### Work-item transitions (`ITEM_TRANSITIONS`)

| From | To |
|---|---|
| (new) | `PENDING` |
| `PENDING` | `BUILDING`, `BLOCKED_DUT`, `BLOCKED_SPEC`, `WAIVED` |
| `BUILDING` | `AWAITING_REVIEW`, `BLOCKED_SPEC`, `TERMINAL_FAILURE` |
| `AWAITING_REVIEW` | `CHANGES_REQUIRED`, `READY_TO_RUN`, `BLOCKED_SPEC` |
| `CHANGES_REQUIRED` | `FIXING`, `TERMINAL_FAILURE` |
| `READY_TO_RUN` | `RUNNING`, `BLOCKED_DUT`, `BLOCKED_SPEC` |
| `RUNNING` | `PASSED`, `DEBUGGING`, `BLOCKED_DUT`, `TERMINAL_FAILURE` |
| `DEBUGGING` | `FIXING`, `BLOCKED_DUT`, `BLOCKED_SPEC`, `TERMINAL_FAILURE` |
| `FIXING` | `AWAITING_REVIEW`, `TERMINAL_FAILURE` |
| `BLOCKED_DUT` | `FIXING`, `READY_TO_RUN`, `WAIVED`, `TERMINAL_FAILURE` |
| `BLOCKED_SPEC` | `BUILDING`, `WAIVED`, `TERMINAL_FAILURE` |
| `PASSED`, `WAIVED`, `TERMINAL_FAILURE` | (terminal) |

### Retry budgets (`RETRY_LIMITS`)

| retry_kind | attempts per lineage |
|---|---:|
| `dispatch` | 3 |
| `review` | 3 |
| `environment` | 3 |
| `tb-fix` | 3 |
| `debug-evidence` | 2 |
| `none` | 1 |

### Other enums

| Field | Legal values |
|---|---|
| ID (`ID_RE`) | `^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$` |
| task status | `DRAFT`, `READY`, `COMPLETED`, `BLOCKED`, `FAILED` |
| builder issue `severity` | `BLOCKER`, `WARNING` |
| runner issue `severity` | `BLOCKER`, `ERROR`, `WARNING` |
| reviewer issue `severity` | `BLOCKER`, `MAJOR`, `MINOR`, `NOTE` |
| reviewer issue `category` | `plan`, `correctness`, `uvm`, `protocol`, `checker`, `assertion`, `coverage`, `build`, `signoff` |
| reviewer issue `disposition` | `OPEN`, `RESOLVED`, `STILL_OPEN` |
| runner `run.phase` | `PREFLIGHT`, `COMPILE`, `ELABORATION`, `SIMULATION`, `REGRESSION`, `COVERAGE_MERGE` |
| runner `case_results[].outcome` | `PASS`, `ENVIRONMENT_ERROR`, `SIMULATION_FAILURE`, `TIMEOUT`, `BLOCKED` |
| diagnosis `state` | `DIAGNOSED`, `NEEDS_MORE_EVIDENCE` |
| diagnosis `classification` | `TB_BUG`, `TEST_BUG`, `DUT_BUG`, `SPEC_GAP`, `ENVIRONMENT`, `TOOLCHAIN`, `UNKNOWN` |
| diagnosis `route_to` | `BUILDER`, `RUNNER`, `RTL_OWNER`, `HUMAN` |
| diagnosis `confidence` | `HIGH`, `MEDIUM`, `LOW` |
| blocker `--kind` | `DUT`, `SPEC`, `ENVIRONMENT`, `UNKNOWN`, `RETRY_EXHAUSTED`, `HUMAN_GATE` |
| `resolve-blocker --status` | `RESOLVED`, `WAIVED` |
| `approve --decision` | `APPROVED`, `REJECTED`, `WAIVED` |

Diagnosis `classification → route_to` mapping: `TB_BUG`/`TEST_BUG`→`BUILDER`,
`DUT_BUG`→`RTL_OWNER`, `SPEC_GAP`→`HUMAN`, `ENVIRONMENT`/`TOOLCHAIN`→`RUNNER`,
`UNKNOWN`→`RUNNER` or `HUMAN`. `DUT_BUG` may not be `LOW` confidence. The
diagnosis is the embedded `payload.diagnosis` of a failing runner execution
result — there is no separate diagnosis task/action.

Runner `action → run.phase`: `PREFLIGHT`→`PREFLIGHT`, `COMPILE_ELAB`→`COMPILE`
or `ELABORATION`, `RUN_CASE`→`SIMULATION`, `RUN_REGRESSION`→`REGRESSION`,
`MERGE_COVERAGE`→`COVERAGE_MERGE`.

Builder `change_set.kind → action`: `WRITE_VPLAN`→`vplan`,
`APPLY_PLAN_EDITS`→`plan_edits`, `BUILD_SMOKE_FOUNDATION`→`smoke_foundation`,
`IMPLEMENT_FEATURE_BATCH`→`feature_batch`, `APPLY_REVIEW_FIX`→`review_fix`,
`APPLY_DEBUG_FIX`→`debug_fix`, `COVERAGE_CLOSURE`→`coverage_closure`.

## 3. Request schema (`request.json`, `dv-task/1.0`)

Top-level keys (exact set, no extras):

`schema_version`, `task_id`, `run_id`, `role`, `action`, `phase`, `attempt`,
`lineage_id`, `retry_kind`, `requested_by` (`"main"`), `reply_to` (`"main"`),
`project_root`, `input_revision`, `revision_paths`, `inputs`, `scope`,
`acceptance`, `context`, `prior_result_refs`, `expected_result_path`.

- `input_revision` is `sha256:<64 hex>`; every dispatched task has one.
- `inputs[]` = `{kind, path, required}`; a required input must exist and fall
  inside `scope.read`.
- `scope` keys: `read`, `write`, `feature_ids`, `test_ids`, `seeds`, `files`.
  `read`/`write` are unique path arrays; `feature_ids`/`test_ids` unique IDs;
  `seeds` unique non-negative ints; `files` unique paths.
- `scope.write`: reviewer empty; builder non-empty and inside project
  root, not the root itself, not `.dv/`, not a protected input; runner exactly
  `.dv/runs/<run-id>/<task-id>/` for every action (there are no read-only
  diagnosis tasks).
- `acceptance[]` non-empty unique strings.
- `prior_result_refs[]` unique paths to immutable prior results, else `[]`.
- `revision_paths` = the exact `input_revision` artifact paths (plus, for
  builder, the write roots being created).

`context` keys (exact set, no extras): `feature_ids`, `test_ids`, `finding_ids`,
`affected_ids`, `work_item_ids`, `coverage_ids`,
`random_campaign_id`, `campaign_id`, `command`, `cwd`, `tool`, `tool_version`,
`simulator`, `timeout_s`, `seeds`, `regression_scope`, `case_manifest`,
`acceptance_markers`, `extra_diagnostics`, `parameters`, `environment`,
`build_and_run`.

Per-action context requirements:

| Action | Context requirement |
|---|---|
| `RUN_CASE` | `test_ids` exactly one, `seeds` exactly one integer; smoke tickets may set `build_and_run` (bool) to prove compile+elaboration inside the same single command; an evidence rerun additionally sets `extra_diagnostics` (non-empty unique strings) |
| `RUN_REGRESSION` | `regression_scope` ∈ `CUMULATIVE`/`RANDOM`/`FROZEN`; valid `case_manifest`; `RANDOM` also needs `campaign_id`, and one task's manifest must contain that campaign's complete seed budget (separate task results are not aggregated) |
| `MERGE_COVERAGE` | non-empty `coverage_ids` |
| `REVIEW_TB`, `REVIEW_FIX` | at least one of `work_item_ids`/`feature_ids`/`test_ids` |
| every runner action | `command`, `cwd`, `tool` non-empty; `timeout_s` positive finite |

## 4. Result schema (`result.json`, `dv-result/1.0`)

Top-level keys (exact set, no extras):

`schema_version`, `task_id`, `run_id`, `role`, `action`, `attempt`,
`agent_status`, `outcome`, `input_revision`, `summary` (non-empty, ≤1000),
`artifacts`, `evidence`, `issues`, `payload`, `recommended_next`.

- `artifacts[]` = `{kind, path, sha256}`; only builder and runner return
  artifacts; each path exists, is a file, is inside `scope.write`, and its
  digest matches.
- `evidence[]` = `{id, path, line_or_time, observation}`; ids unique; text
  fields non-empty; paths inside read/write scope.
- `issues[]` shape per role (see §2 and `task-contract.md`):
  builder = `{id, severity, summary, paths, related_ids}`;
  reviewer = `{id, severity, category, path, line, related_ids, evidence_ids,
  impact, required_change, disposition}` (`line` non-negative int);
  runner = `{id, severity, summary, paths, related_ids}` (mechanical execution
  issues only — root-cause ownership lives in `payload.diagnosis`, never in
  `issues`).
- `recommended_next` = `null` or `{role, action, reason}` where the pair is a
  valid `ROLE_ACTIONS` entry.

Payloads (exact, not extensible):

| Role | `payload` fields |
|---|---|
| builder | `change_set` = `{kind, files_created, files_modified, files_deleted, implemented_ids, resolved_issue_ids, unresolved_spec_gaps, self_checks}`; `files_created ∪ files_modified` must exactly equal the declared artifacts; `self_checks` non-empty for `READY_FOR_REVIEW`; code-modifying actions must include a lint-only static syntax check in `self_checks` (`APPLY_PLAN_EDITS` is table-only, so no static lint is required — instead its `self_checks` record the `extract` → `render` round-trip check). `NO_CHANGE` returns no artifacts/change_set. |
| reviewer | `reviewed_revision`, `gate` = `{blocking_count, major_count, minor_count, note_count}`, `prior_findings[]` = `{id, disposition}`, `plan_inventory`, `signoff_audit`. `APPROVED` ⇒ `blocking_count == 0`; `CHANGES_REQUIRED` ⇒ `blocking_count ≥ 1`; `blocking_count` must equal the open `BLOCKER`+`MAJOR` count. `plan_inventory` only on approved `REVIEW_VPLAN`; `signoff_audit` only on `SIGNOFF_AUDIT`. |
| runner | `tested_revision`, `run` = `{phase, test, seed, command, cwd, tool, tool_version, exit_code, duration_s}`, `counts` = `{uvm_fatal, uvm_error, assertion_failures, scoreboard_mismatches}`, `environment_actions[]`, `failure` = `{signature, first_time, log_excerpt_ref}`, `case_results[]` = `{test, seed, outcome}`, `coverage_summary`, `diagnosis`. `PASS` ⇒ `exit_code 0`, empty failure, zero counts; `COMPILE_ELAB` PASS ⇒ `phase ELABORATION`; `RUN_REGRESSION` PASS ⇒ every case `PASS`; `MERGE_COVERAGE` PASS ⇒ `targets_met` true + a coverage artifact. `coverage_summary` only on `MERGE_COVERAGE`. `diagnosis` is `null` except on the four failing outcomes (`COMPILE_ERROR`, `ELABORATION_ERROR`, `SIMULATION_FAILURE`, `TIMEOUT`), where it is `{state, classification, subtype, confidence, expected, observed, root_cause, suspected_locations[]` = `{path, line, module, signal}`, `affected_ids[]`, `route_to`, `fix_request` = `{instructions, candidate_files, must_preserve}`, `rerun` = `{test, seed, extra_diagnostics}}`. `state` = `DIAGNOSED` or `NEEDS_MORE_EVIDENCE`; `NEEDS_MORE_EVIDENCE` ⇒ `rerun.extra_diagnostics` non-empty. |

## 5. `plan_inventory` schema (approved `REVIEW_VPLAN` only)

`{priority_order, items, random_campaigns, coverage_items}`.

- `priority_order[]`: non-empty, unique, non-empty strings.
- `items[]` = `{id, kind, priority, dependencies, mandatory}`; `kind` ∈
  `FEATURE`/`TEST`; `priority` must be in `priority_order`.
- `random_campaigns[]` = `{id, test, seed_budget, mandatory, dependencies}`;
  `seed_budget` positive int.
- `coverage_items[]` = `{id, metric, target, mandatory, dependencies}`;
  `target` finite number.
- IDs unique across all three arrays; every dependency names an existing ID; no
  self-dependency; no dependency cycles.

## 6. Gate conditions (per phase target)

`transition --to <target>` passes when the target gate is satisfied (the same
checks run in `validate`).

| Target | Gate |
|---|---|
| `PLAN` | always open |
| `PREFLIGHT` | an approved `REVIEW_VPLAN` on a valid, non-stale revision whose builder lineage traces to a completed `WRITE_VPLAN`/`APPLY_PLAN_EDITS`/`APPLY_REVIEW_FIX`, materializes a valid plan inventory, **and a recorded human `VPLAN` approval bound to that accepted revision** |
| `SMOKE` | a passing immutable `PREFLIGHT` runner task on the accepted plan revision |
| `FEATURES` | a reviewed smoke pass on one revision, in either form: (a) chained `COMPILE_ELAB` pass → smoke `RUN_CASE` pass, or (b) a single build-and-run smoke `RUN_CASE` (`context.build_and_run`) whose one command rebuilt the sealed filelist then ran the smoke sim |
| `RANDOM` | all mandatory directed items completed (no open higher-priority blockers), and at least one campaign |
| `COVERAGE` | all mandatory directed items + random campaigns completed |
| `REGRESSION` | all mandatory coverage targets `PASS` on a statically approved revision |
| `SIGNOFF` | frozen revision; no open blockers or fix requests; an exact `FROZEN` `RUN_REGRESSION` pass on the frozen revision |
| `COMPLETE` | no unresolved mandatory items, no open blockers/fix requests, frozen regression revalidated, and an approved `SIGNOFF_AUDIT` chained to that regression plus later human `SIGNOFF` approval |

A `BLOCKED_DUT` item closes priority/signoff/complete gates until its fix
request is resolved (by re-running `init` after the external RTL fix).

Two gates require an explicit **human** approval recorded with `approve`, both
bound to a revision via `--revision` (omitted for other gates like `WORK_ITEM:`
waivers): `VPLAN` (accept the plan before `PREFLIGHT`) and `SIGNOFF` (accept the
frozen regression before `COMPLETE`). Only `--decision APPROVED` satisfies them;
`REJECTED`/`WAIVED` do not.

## 7. Core invariants (why a task/result is rejected)

- Task phase must be current or a direct forward transition; no tasks after
  `COMPLETE`.
- `seal-task` only from `DRAFT`; `record-result` only from `READY`; request hash
  must be unchanged since sealing.
- Builder write scope cannot touch the spec, RTL filelist, protected RTL roots,
  `.dv/`, or a runner directory; reviewer has an empty write scope, and every
  runner action uses exactly its isolated run dir
  (`.dv/runs/<run-id>/<task-id>/`).
- `revision_paths` must exactly equal the input artifact paths (plus builder
  write roots); the input revision must not drift between dispatch and record.
- Builder's declared created/modified/deleted must match the sealed write-scope
  diff, and a non-`READY_FOR_REVIEW` result must not mutate the write scope.
- A child's `input_revision` must equal its completed parent's `output_revision`.
- Reviewer and runner preserve their input revision; only a successful
  builder `READY_FOR_REVIEW` produces a new revision.
- A builder change invalidates affected items' prior gates (resets `PASSED` to
  `BUILDING`).
- Revision drift is checked against a per-file content manifest keyed by
  `(mtime_ns, size)` (see `workflow-state.schema.json` `fileManifest`): files
  whose stat key is unchanged reuse their cached digest instead of being
  re-read. This is accidental-drift detection, not a tamper-proof boundary — a
  rewrite that preserves both `mtime` and `size` is not observed.
