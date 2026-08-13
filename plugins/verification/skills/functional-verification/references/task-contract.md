# Worker Task Contract

## Topology

The user's main Claude session is the only orchestrator. It may dispatch one of
four first-level workers:

- `verification-builder`
- `verification-reviewer`
- `verification-runner`
- `verification-debugger`

Workers never dispatch or message one another. A worker recommendation is
advisory; every result returns to main, and only main may create the next task,
route a result, mutate state, or advance a gate.

## Durable files

```text
.dv/
  workflow_state.json
  events.jsonl
  tasks/<task-id>/request.json
  tasks/<task-id>/result.json
  runs/<run-id>/<task-id>/
```

`dv_flow.py` is the only writer of `workflow_state.json` and
`events.jsonl`. Task requests are editable only while `DRAFT`. Once
`seal-task` changes a task to `READY`, never change its request. Each retry
gets a new task ID and immutable result.

## Task lifecycle

```text
new-task -> DRAFT -> fill request -> seal-task -> READY
READY -> dispatch exactly one worker -> write result -> record-result
record-result -> COMPLETED | BLOCKED | FAILED
```

`agent_status` describes whether the worker fulfilled its contract.
`outcome` describes what the DV work found. A correctly executed command that
finds a simulation failure therefore uses `agent_status` `COMPLETED` and outcome
`SIMULATION_FAILURE`; it is not an agent failure.

`BLOCKED` is reserved for missing input or authority. `FAILED` with
`INTERNAL_ERROR` means the worker itself could not produce a valid result.

## Request rules

Every request uses `dv-task/1.0` and contains:

- exact `task_id`, `run_id`, `role`, `action`, phase, lineage, attempt
- the absolute initialized `project_root`; main and every worker reject a ticket
  whose resolved working project differs
- a non-null exact `input_revision`; the first task uses initialization's
  `baseline_revision`
- the complete `revision_paths` inventory covered by that composite revision
- required input paths with kinds and `required` flags
- explicit read and write roots
- observable acceptance conditions
- bounded feature, test, seed, finding, or failure context; runner execution
  tickets include one exact `command`, `cwd`, `tool`, and positive `timeout_s`
- `prior_result_refs`, containing immutable result paths when this task is a
  retry, review, run, diagnosis, or fix, and an empty array otherwise
- `expected_result_path`, matching the task ledger path where main records the
  returned object

Every required input must fall inside `scope.read`. Builder write roots are
inside the project but cannot overlap the specification, DUT RTL/filelist,
`.dv/`, or a runner directory. Reviewer and debugger write scopes are empty.
Runner write scope is exactly `.dv/runs/<run-id>/<task-id>/`.

Use stable lineage IDs for one bounded loop, for example
`VP-T014-review`, `VP-T014-run`, and `FAIL-0021-tb-fix`. The state tool
counts attempts by lineage and retry kind; respawning does not reset a budget.

## Revision rules

- `dv_flow.py init` creates the first content-addressed composite revision over
  the specification, RTL filelist, and every protected `--rtl-root`. No task is
  revision-less.
- A successful builder `READY_FOR_REVIEW` task is the normal producer of a new
  composite revision. It preserves every unchanged path from its input revision,
  removes declared deletions, and incorporates all declared write-scope changes.
- The sole exception is `accept-rtl-update`: after a confirmed `DUT_BUG`, main
  may register an external RTL-owner change under protected RTL roots. The
  command rejects a stale expected revision and any simultaneous non-RTL drift;
  this external revision has no builder producer task and remains pending
  verification until all affected items pass.
- Builder artifacts are exactly its created and modified files; deleted files
  appear only in `payload.change_set.files_deleted`.
- Every builder artifact is inside the assigned write roots and carries a
  `sha256:<64 lowercase hexadecimal characters>` file digest.
- `dv_flow.py` compares the builder's created, modified, and deleted lists with
  pre/post write-scope snapshots before deriving the output revision.
- Reviewer, runner, and debugger results preserve their input revision.
- A child task must name the completed parent task and use its exact output
  revision. Stale reviews, runs, and diagnoses are rejected.
- Any builder change invalidates affected prior static and dynamic gates.

## Result rules

Every worker returns one complete `dv-result/1.0` object to main, without a
Markdown fence or surrounding prose. Main records it at `expected_result_path`
and calls `record-result`; workers never write task/result or state files.

- Artifact entries contain exactly `kind`, `path`, and the standard `sha256`
  digest. Only builder and runner may return artifacts.
- Evidence entries use one shared shape: `id`, `path`, `line_or_time`, and a
  specific factual `observation`.
- `recommended_next` is either `null` or one valid role/action/reason object. It
  never authorizes a worker to contact or dispatch another worker.
- `COMPLETED` pairs with a role's substantive outcome, `BLOCKED` pairs only with
  `BLOCKED`, and `FAILED` pairs only with `INTERNAL_ERROR`.

Role payloads are exact, not extensible scratch space:

| Role | Required payload fields |
|---|---|
| builder | `change_set`, containing `kind`, `files_created`, `files_modified`, `files_deleted`, `implemented_ids`, `resolved_issue_ids`, `unresolved_spec_gaps`, and `self_checks` |
| reviewer | `reviewed_revision`, `gate`, `prior_findings`, `plan_inventory`, and `signoff_audit` |
| runner | `tested_revision`, `run`, `counts`, `environment_actions`, `failure`, `case_results`, and `coverage_summary` |
| debugger | `classification`, `subtype`, `confidence`, `expected`, `observed`, `root_cause`, `suspected_locations`, `affected_ids`, `route_to`, `fix_request`, and `rerun` |

Role issue arrays are exact as well:

| Role | Exact `issues[]` fields |
|---|---|
| builder | `id`, `severity`, `summary`, `paths`, `related_ids`; severity is `BLOCKER` or `WARNING` |
| reviewer | `id`, `severity`, `category`, `path`, `line`, `related_ids`, `evidence_ids`, `impact`, `required_change`, `disposition`; severity is `BLOCKER`, `MAJOR`, `MINOR`, or `NOTE` |
| runner | `id`, `severity`, `summary`, `paths`, `related_ids`; severity is `BLOCKER`, `ERROR`, or `WARNING` |
| debugger | always empty; diagnosis belongs in the typed debugger payload |

Reviewer category is one of `plan`, `correctness`, `uvm`, `protocol`,
`checker`, `assertion`, `coverage`, `build`, or `signoff`; disposition is
`OPEN`, `RESOLVED`, or `STILL_OPEN`. Reviewer `line` is a non-negative integer,
and `gate.blocking_count` exactly counts open `BLOCKER` and `MAJOR` issues.

An approved `REVIEW_VPLAN` must provide `plan_inventory` with
`priority_order`, `items`, `random_campaigns`, and `coverage_items`. Main uses
that validated inventory for gates rather than reparsing plan prose. An
approved `SIGNOFF_AUDIT` must provide revision consistency, mandatory item and
random-seed totals, coverage status, open blocker/fix lists, waivers, and
evidence references.

Runner outcome `COVERAGE_GAP` means a valid coverage merge completed but an
unwaived mandatory target remains below threshold. It is distinct from an
environment or merge failure. A completed coverage merge reports
`coverage_summary` with `targets_met`, `metrics`, and `waiver_ids`. Every metric
has exactly `id`, `metric`, non-negative numeric `value`, non-negative numeric
`target`, and boolean `met`. Every waiver ID must name explicit evidence and a
human approval bound to the reviewed revision.

## Ownership

| Role | Persistent project files | Run directory | Workflow state |
|---|---:|---:|---:|
| main | no DV content edits | no execution | sole owner through CLI |
| builder | assigned verification assets only | no | no |
| reviewer | read-only | read-only | no |
| runner | read-only | assigned task directory only | no |
| debugger | read-only | read-only | no |

The builder never modifies DUT RTL. Confirmed DUT defects become fix requests
for an external RTL owner. A debugger diagnoses and proposes a fix; it does not
become a second source writer. Main may register the external owner's protected
RTL change only through `accept-rtl-update` as described above.

Each runner task permits exactly one invocation of its requested execution
command. Bounded environment preparation is recorded before that invocation.
Any execution retry, changed diagnostic, or evidence rerun requires a new
immutable task and consumes the corresponding retry budget.

## Gate rules

1. Plan: builder revision, then reviewer approval of that exact revision and a
   valid machine-readable plan inventory.
2. Preflight: runner `PASS` on the approved-plan revision, checking only
   RTL/tool readiness available before TB creation; no TB filelist or compile
   pass is required.
3. Smoke: builder, static approval, compile/elaboration, then smoke pass.
4. Feature batch: builder, review, targeted pass, then cumulative pass on one
   accepted revision.
5. Priority: no lower priority while a higher one has an unwaived blocker.
6. Failure fix: rerun the original test and seed, then the affected cumulative
   set.
7. Random/coverage: complete mandatory seed budgets and meet or explicitly
   waive every mandatory coverage target.
8. Signoff: frozen regression pass, signoff audit of the same revision, no open
   work/fix request, then human approval.

A `BLOCKED_DUT` item is neither skipped, passed, nor waived into clean signoff.
Independent work may continue, but its priority and signoff gates stay closed
until an external RTL update is accepted, rerun at the debugger's exact
test/seed, covered by affected cumulative regression, and resolved. The prior
TB review remains valid because no verification asset changed.

The builder-owned V-plan always says `PROPOSED`. Its status text is informative,
not a gate: immutable reviewer results, the materialized inventory, and the main
ledger are authoritative for approval and freezing.

## Work-item lifecycle

The approved plan inventory materializes work items in `PENDING`. Main mutates
them only through `dv_flow.py set-item` and follows these routes:

```text
PENDING -> BUILDING -> AWAITING_REVIEW -> READY_TO_RUN -> RUNNING -> PASSED
AWAITING_REVIEW -> CHANGES_REQUIRED -> FIXING -> AWAITING_REVIEW
RUNNING -> DEBUGGING -> FIXING -> AWAITING_REVIEW
RUNNING or DEBUGGING -> BLOCKED_DUT
```

`READY_TO_RUN` requires `--last-task-id` naming an approved `REVIEW_TB` or
`REVIEW_FIX` task whose context names the item. `PASSED` requires
`--last-task-id` naming a passing `RUN_CASE` or `RUN_REGRESSION` task on a
revision with an approved static review for that item. Dependencies and higher
priorities must already be resolved. `WAIVED` requires a recorded human
`WORK_ITEM:<item-id>` approval and cannot resolve an open DUT fix request.

`AWAITING_REVIEW` requires `--last-task-id` naming the completed builder task
that produced the current item revision; state records it as `builder_task_id`.
The reviewer used for `READY_TO_RUN` must have that exact builder task as its
parent and must review the same revision. Each work-item ledger entry always
contains `mandatory`, `accepted_revision`, nullable `builder_task_id`, nullable
`review_task_id`, nullable `run_task_id`, and `evidence_task_ids`, so incomplete
lineage is explicit rather than represented by missing keys.

## DUT fix lifecycle

Main accepts only a completed failing runner task followed by a same-revision,
non-low-confidence debugger result classified `DUT_BUG` and routed to
`RTL_OWNER`:

```text
add-fix-request --failure-task-id <runner> --diagnosis-task-id <debugger>
  -> affected work items become BLOCKED_DUT
  -> external RTL owner updates a protected RTL root
accept-rtl-update --fix-request-id <id> \
  --expected-revision <old-revision> --external-ref <change-id>
  -> fix status RTL_UPDATED_PENDING_VERIFY; frozen revision is cleared
  -> rerun the debugger's exact test/seed on the new revision
  -> run the affected cumulative regression
set-item ... --status PASSED --last-task-id <passing-runner-task>
resolve-fix-request --fix-request-id <id> \
  --verification-task-id <passing-runner-task> --resolution <text>
```

`resolve-fix-request` succeeds only when the cited rerun passes on the accepted
new RTL revision, proves every affected ID, and all affected work items are
`PASSED`. An affected cumulative regression must pass before phase advancement.

## Retry budgets

| Retry kind | Total tasks in one lineage |
|---|---:|
| `dispatch` | 3 |
| `review` | 3 |
| `environment` | 3 |
| `tb-fix` | 3 |
| `debug-evidence` | 2 |
| `none` | 1 |

When a cap is reached, main records a blocker and transitions to `BLOCKED` or
`WAITING_HUMAN`. It never silently widens the cap.

## Concurrency

Use one builder at a time in a shared worktree. Runner tasks may be parallel
only when they use different immutable run directories and the same frozen
revision. Reviews and diagnoses must name the revision they inspected.
