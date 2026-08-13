# dv-agents

`dv-agents` is a Claude Code plugin for evidence-driven SystemVerilog/UVM
functional verification. Version 2 is a rewrite around a one-level worker
topology and a durable workflow state.

## Architecture

```text
                      verification-builder
                               ^
                               |
verification-reviewer <-- main session --> verification-runner
                               |
                               v
                      verification-debugger
```

The functional-verification skill runs in the user's main session. There is no
orchestrator subagent: this keeps builder, reviewer, runner, and debugger at
subagent depth one. Workers never invoke one another.

- Main owns `.dv/workflow_state.json`, task dispatch, revisions, gates, retry
  budgets, routing, and human approvals.
- Builder is the only writer of persistent verification assets.
- Reviewer performs read-only static plan/code review and signoff audit.
- Runner executes in isolated `.dv/runs/` directories and reports mechanical
  outcomes without root-cause guesses.
- Debugger performs read-only diagnosis and returns a routed fix request.

Every worker receives a sealed task from main and returns one structured result
to main. Worker recommendations are advisory; workers never invoke or message
one another. `dv_flow.py init` creates a content-addressed baseline revision,
and all later reviews and runs name an exact composite revision and complete
path inventory.

## Workflow

```text
V-plan + TB architecture -> plan review
  -> tool/RTL preflight
  -> TB foundation + smoke -> code review -> compile/elab/smoke
  -> P1 small batches: build -> review -> targeted run -> cumulative run
  -> remaining priority batches
  -> constrained random -> coverage merge/closure
  -> frozen full regression -> signoff audit -> human signoff
```

A test is complete only after static approval and dynamic pass on the same
artifact revision. A DUT defect becomes a visible `BLOCKED_DUT` item and fix
request; it is never silently skipped or counted as pass.

The approving plan reviewer returns a machine-readable inventory of priorities,
directed items, random campaigns, and coverage targets. Main uses that inventory
for gates. Each runner task invokes its requested command once; environment or
diagnostic retries are separate immutable tasks. A successful coverage merge
with an unmet mandatory target is reported as `COVERAGE_GAP`, then routed by
main to bounded coverage closure.

## Local use

Start Claude Code from the design project root with the plugin directory:

```bash
cd <design-project-root>
claude --plugin-dir /home/test/work/dv-agents/plugins/verification
```

Then invoke:

```text
/chip-design-verification:functional-verification
```

Provide the design root, specification, RTL filelist, every protected RTL source
root, top module, simulator entry point, and clock/reset facts. The skill
initializes `.dv/` in the design project and resumes it on later sessions. The
main session verifies that its working directory equals the sealed
`project_root` before dispatching workers.

The initialization command protects the specification, filelist, and every RTL
source root in the baseline revision:

```bash
python3 /home/test/work/dv-agents/plugins/verification/scripts/dv_flow.py init \
  --root "$PWD" \
  --design-name <name> \
  --spec <spec-path> \
  --rtl-filelist <filelist-path> \
  --rtl-root <rtl-source-file-or-directory> \
  --top <top-module>
```

Repeat `--rtl-root` when RTL lives under multiple protected roots.

The plugin relies on Claude Code standard discovery:

- `plugins/verification/agents/*.md`
- `plugins/verification/skills/*/SKILL.md`

The manifest intentionally does not enumerate individual agent files.

## Durable protocol

`plugins/verification/scripts/dv_flow.py` creates immutable task requests,
validates worker results, hashes artifacts, rejects stale revisions, enforces
retry limits, records DUT fix requests, and guards final completion.

Requests carry `input_revision`, complete `revision_paths`, prior result
references, explicit scopes, and an expected result path. Builder changes report
created, modified, and deleted files; all file digests use standard lowercase
`sha256:<hex>` notation.

```bash
python3 plugins/verification/scripts/dv_flow.py --help
```

Schemas and reference material live beside the skill in `references/`.

## Validation

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
claude plugin validate --strict plugins/verification
```

The repository validator checks manifests, standard agent/skill discovery,
worker frontmatter, the no-nested-agent rule, and cross-schema action
vocabulary.

## License

MIT
