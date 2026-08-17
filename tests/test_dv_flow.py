from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
FLOW = REPO_ROOT / "plugins" / "verification" / "scripts" / "dv_flow.py"


class FlowCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "spec.md").write_text("# Test specification\n", encoding="utf-8")
        (self.root / "rtl").mkdir()
        (self.root / "rtl" / "top.sv").write_text(
            "module unit_top; endmodule\n", encoding="utf-8"
        )
        (self.root / "rtl.f").write_text("rtl/top.sv\n", encoding="utf-8")
        self.run_cli(
            "init",
            "--root",
            str(self.root),
            "--dut-name",
            "unit-dut",
            "--spec",
            "spec.md",
            "--rtl-filelist",
            "rtl.f",
            "--rtl-root",
            "rtl",
            "--top",
            "unit_top",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(FLOW), *arguments],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            expected,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def state(self) -> dict[str, Any]:
        return json.loads(
            (self.root / ".dv" / "workflow_state.json").read_text(encoding="utf-8")
        )

    def new_task(
        self,
        task_id: str,
        role: str,
        action: str,
        *,
        lineage: str,
        retry_kind: str,
        phase: str | None = None,
        input_revision: str | None = None,
        parent: str | None = None,
        inputs: list[dict[str, Any]] | None = None,
        read: list[str] | None = None,
        write: list[str] | None = None,
        context: dict[str, Any] | None = None,
        seal_expected: int = 0,
    ) -> dict[str, Any]:
        arguments = [
            "new-task",
            "--root",
            str(self.root),
            "--task-id",
            task_id,
            "--role",
            role,
            "--action",
            action,
            "--lineage",
            lineage,
            "--retry-kind",
            retry_kind,
        ]
        if phase:
            arguments.extend(["--phase", phase])
        if input_revision:
            arguments.extend(["--input-revision", input_revision])
        if parent:
            arguments.extend(["--parent-task-id", parent])
        self.run_cli(*arguments)

        request_path = self.root / ".dv" / "tasks" / task_id / "request.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["inputs"] = inputs or []
        request["scope"] = {"read": read or [], "write": write or []}
        if role == "builder":
            request["revision_paths"].extend(request["scope"]["write"])
        request["revision_paths"] = list(dict.fromkeys(request["revision_paths"]))
        request["acceptance"] = ["The bounded task returns contract-valid evidence."]
        request_context = dict(context or {})
        if role == "runner":
            request_context.setdefault("command", "unit-test-command")
            request_context.setdefault("cwd", str(self.root))
            request_context.setdefault("tool", "unit-sim")
            request_context.setdefault("timeout_s", 60)
        request["context"] = request_context
        request_path.write_text(
            json.dumps(request, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.run_cli(
            "seal-task",
            "--root",
            str(self.root),
            "--task-id",
            task_id,
            expected=seal_expected,
        )
        return request

    def record(
        self,
        request: dict[str, Any],
        *,
        outcome: str,
        artifacts: list[dict[str, str]] | None = None,
        agent_status: str = "COMPLETED",
        payload: dict[str, Any] | None = None,
        expected: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        result_artifacts = artifacts or []
        result_evidence: list[dict[str, Any]] = []
        result_payload: dict[str, Any] = payload or {}
        if request["role"] == "builder" and agent_status == "COMPLETED":
            paths = [artifact["path"] for artifact in result_artifacts]
            kind_by_action = {
                "WRITE_VPLAN": "vplan",
                "APPLY_PLAN_EDITS": "plan_edits",
                "BUILD_SMOKE_FOUNDATION": "smoke_foundation",
                "IMPLEMENT_FEATURE_BATCH": "feature_batch",
                "APPLY_REVIEW_FIX": "review_fix",
                "APPLY_DEBUG_FIX": "debug_fix",
                "COVERAGE_CLOSURE": "coverage_closure",
            }
            result_payload = {
                "change_set": {
                    "kind": kind_by_action[request["action"]],
                    "files_created": paths,
                    "files_modified": [],
                    "files_deleted": [],
                    "implemented_ids": [],
                    "resolved_issue_ids": [],
                    "unresolved_spec_gaps": [],
                    "self_checks": ["Stable IDs and references were checked."],
                }
            }
            if result_artifacts:
                result_evidence = [
                    {
                        "id": "BLD-EVID-001",
                        "path": result_artifacts[0]["path"],
                        "line_or_time": "artifact",
                        "observation": "The bounded artifact and its self-check were produced.",
                    }
                ]
        elif request["role"] == "reviewer" and agent_status == "COMPLETED":
            inventory = None
            audit = None
            if request["action"] == "REVIEW_VPLAN" and outcome == "APPROVED":
                inventory = {
                    "priority_order": ["P0", "P1", "P2"],
                    "items": [
                        {
                            "id": "VP-T001",
                            "kind": "TEST",
                            "priority": "P0",
                            "dependencies": [],
                            "mandatory": True,
                        }
                    ],
                    "random_campaigns": [],
                    "coverage_items": [],
                }
            if request["action"] == "SIGNOFF_AUDIT":
                audit = {
                    "revision_consistent": True,
                    "mandatory_items_total": 1,
                    "mandatory_items_passed": 1,
                    "random_seeds_planned": 0,
                    "random_seeds_completed": 0,
                    "coverage_targets_met": True,
                    "open_blockers": [],
                    "open_fix_requests": [],
                    "waivers": [],
                    "evidence_refs": ["RUN-EVID-001"],
                }
            result_payload = {
                "reviewed_revision": request["input_revision"],
                "gate": {
                    "blocking_count": 0,
                    "major_count": 0,
                    "minor_count": 0,
                    "note_count": 0,
                },
                "prior_findings": [],
                "plan_inventory": inventory,
                "signoff_audit": audit,
            }
            result_evidence = [
                {
                    "id": "REV-EVID-001",
                    "path": "verif/vplan.md",
                    "line_or_time": "1",
                    "observation": "The requested revision was reviewed.",
                }
            ]
        elif request["role"] == "runner" and agent_status == "COMPLETED":
            context = request["context"]
            test_ids = context.get("test_ids", [])
            run_test = (
                (test_ids[0] if test_ids else "smoke_test")
                if request["action"] == "RUN_CASE"
                else None
            )
            run_seed = (
                (context.get("seeds") or [1])[0]
                if request["action"] == "RUN_CASE"
                else None
            )
            phase_by_action = {
                "PREFLIGHT": "PREFLIGHT",
                "COMPILE_ELAB": "ELABORATION",
                "RUN_CASE": "SIMULATION",
                "RUN_REGRESSION": "REGRESSION",
                "MERGE_COVERAGE": "COVERAGE_MERGE",
            }
            result_payload = {
                "tested_revision": request["input_revision"],
                "run": {
                    "phase": phase_by_action[request["action"]],
                    "test": run_test,
                    "seed": run_seed,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 0,
                    "duration_s": 1,
                },
                "counts": {
                    "uvm_fatal": 0,
                    "uvm_error": 0,
                    "assertion_failures": 0,
                    "scoreboard_mismatches": 0,
                },
                "environment_actions": [],
                "failure": {
                    "signature": None,
                    "first_time": None,
                    "log_excerpt_ref": None,
                },
                "case_results": [
                    {**case, "outcome": "PASS"}
                    for case in context.get("case_manifest", [])
                ] if request["action"] == "RUN_REGRESSION" else [],
                "coverage_summary": None,
                "diagnosis": None,
            }
            result_evidence = [
                {
                    "id": "RUN-EVID-001",
                    "path": result_artifacts[0]["path"],
                    "line_or_time": "1",
                    "observation": "The command completed and acceptance markers passed.",
                }
            ]
        result = {
            "schema_version": "dv-result/1.0",
            "task_id": request["task_id"],
            "run_id": request["run_id"],
            "role": request["role"],
            "action": request["action"],
            "attempt": request["attempt"],
            "agent_status": agent_status,
            "outcome": outcome,
            "input_revision": request["input_revision"],
            "summary": f"{request['action']} produced {outcome}.",
            "artifacts": result_artifacts,
            "evidence": result_evidence,
            "issues": [],
            "payload": result_payload,
            "recommended_next": None,
        }
        result_path = self.root / request["expected_result_path"]
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            request["task_id"],
            expected=expected,
        )

    @staticmethod
    def artifact(path: Path, root: Path, kind: str) -> dict[str, str]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "kind": kind,
            "path": path.relative_to(root).as_posix(),
            "sha256": "sha256:" + digest,
        }

    def enter_plan(self) -> None:
        if self.state()["current_phase"] == "INIT":
            self.run_cli(
                "transition",
                "--root",
                str(self.root),
                "--to",
                "PLAN",
                "--reason",
                "begin V-plan-first verification",
            )

    def create_builder_revision(self) -> tuple[dict[str, Any], str]:
        self.enter_plan()
        request = self.new_task(
            "plan-build-001",
            "builder",
            "WRITE_VPLAN",
            lineage="plan-build",
            retry_kind="dispatch",
            phase="PLAN",
            input_revision=self.state()["baseline_revision"],
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
            ],
            read=["spec.md", "rtl.f"],
            write=["verif"],
        )
        artifact_path = self.root / "verif" / "vplan.md"
        artifact_path.parent.mkdir()
        artifact_path.write_text("# Verification plan\n", encoding="utf-8")
        self.record(
            request,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(artifact_path, self.root, "vplan")],
        )
        revision = self.state()["tasks"]["plan-build-001"]["output_revision"]
        self.assertTrue(revision.startswith("sha256:"))
        return request, revision

    def record_failure(
        self,
        request: dict[str, Any],
        *,
        diagnosis: dict[str, Any] | None,
        outcome: str = "SIMULATION_FAILURE",
        signature: str = "scoreboard mismatch on channel A",
        counts: dict[str, int] | None = None,
        expected: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        """Record a completed failing runner result with an embedded diagnosis."""
        run_dir = self.root / ".dv" / "runs" / request["run_id"] / request["task_id"]
        log_path = run_dir / "run.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("UVM_ERROR: scoreboard mismatch\n", encoding="utf-8")
        context = request["context"]
        phase_by_action = {
            "PREFLIGHT": "PREFLIGHT",
            "COMPILE_ELAB": "ELABORATION",
            "RUN_CASE": "SIMULATION",
            "RUN_REGRESSION": "REGRESSION",
            "MERGE_COVERAGE": "COVERAGE_MERGE",
        }
        test = (context.get("test_ids") or ["smoke_test"])[0]
        seed = (context.get("seeds") or [1])[0]
        result = {
            "schema_version": "dv-result/1.0",
            "task_id": request["task_id"],
            "run_id": request["run_id"],
            "role": "runner",
            "action": request["action"],
            "attempt": request["attempt"],
            "agent_status": "COMPLETED",
            "outcome": outcome,
            "input_revision": request["input_revision"],
            "summary": f"{request['action']} failed with {outcome}.",
            "artifacts": [self.artifact(log_path, self.root, "log")],
            "evidence": [
                {
                    "id": "RUN-EVID-001",
                    "path": log_path.relative_to(self.root).as_posix(),
                    "line_or_time": "1",
                    "observation": "The run log records the failure.",
                }
            ],
            "issues": [],
            "payload": {
                "tested_revision": request["input_revision"],
                "run": {
                    "phase": phase_by_action[request["action"]],
                    "test": test,
                    "seed": seed,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 1,
                    "duration_s": 1,
                },
                "counts": counts
                or {
                    "uvm_fatal": 0,
                    "uvm_error": 1,
                    "assertion_failures": 1,
                    "scoreboard_mismatches": 1,
                },
                "environment_actions": [],
                "failure": {
                    "signature": signature,
                    "first_time": "line 271",
                    "log_excerpt_ref": log_path.relative_to(self.root).as_posix(),
                },
                "case_results": [],
                "coverage_summary": None,
                "diagnosis": diagnosis,
            },
            "recommended_next": None,
        }
        result_path = self.root / request["expected_result_path"]
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            request["task_id"],
            expected=expected,
        )

    def test_builder_revision_is_preserved_by_read_only_review(self) -> None:
        _, revision = self.create_builder_revision()
        review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif/vplan.md"],
            write=[],
        )
        self.record(review, outcome="APPROVED")

        state = self.state()
        self.assertEqual(revision, state["tasks"]["plan-review-001"]["output_revision"])
        self.assertEqual(
            "plan-build-001",
            state["artifacts"][revision]["producer_task_id"],
        )
        self.run_cli("validate", "--root", str(self.root))

    def plan_reviewed(self, plan_revision: str, parent: str = "plan-build-001") -> None:
        """Approve the plan revision with a REVIEW_VPLAN (no inventory yet)."""
        review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent=parent,
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(review, outcome="APPROVED")

    def test_applied_plan_edits_fold_into_accepted_revision(self) -> None:
        """Human edits a table; APPLY_PLAN_EDITS folds it, re-review + re-approve
        bind the accepted plan to the edited revision before PREFLIGHT."""
        self.enter_plan()
        _, plan_revision = self.create_builder_revision()
        self.plan_reviewed(plan_revision)

        # The operator hands the delivered tables to the human; the human edits
        # them in Excel, then the builder folds the edits into a new revision.
        edits = self.new_task(
            "plan-edits-001",
            "builder",
            "APPLY_PLAN_EDITS",
            lineage="plan-edits",
            retry_kind="dispatch",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=["verif"],
        )
        tables = self.root / "verif" / "tables.json"
        tables.parent.mkdir(parents=True, exist_ok=True)
        tables.write_text(
            '{"dut": "unit-dut", "testpoint": {"template": '
            '"XXXX-UT-TestPoint.xlsx", "rows": []}, "testlist": {"template": '
            '"Bach_Testlist_template.xlsx", "rows": []}, "covergroups": '
            '{"template": "Bach_CoverGroups_XXXX.xlsx", "rows": []}}\n',
            encoding="utf-8",
        )
        self.record(
            edits,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(tables, self.root, "tables")],
        )
        edited_revision = self.state()["tasks"]["plan-edits-001"]["output_revision"]
        self.assertNotEqual(plan_revision, edited_revision)

        # Re-review the edited revision, then approve it with the human VPLAN gate.
        review2 = self.new_task(
            "plan-review-002",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=edited_revision,
            parent="plan-edits-001",
            inputs=[
                {"kind": "vplan", "path": "verif/vplan.md", "required": True},
                {"kind": "tables", "path": "verif/tables.json", "required": True},
            ],
            read=["spec.md", "verif"],
            write=[],
        )
        self.record(review2, outcome="APPROVED")
        self.run_cli(
            "approve", "--root", str(self.root), "--gate", "VPLAN",
            "--decision", "APPROVED", "--approved-by", "unit-test",
            "--note", "Plan with human table edits approved.", "--revision",
            edited_revision,
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "accept the edited plan inventory",
        )

        state = self.state()
        self.assertEqual("PREFLIGHT", state["current_phase"])
        self.assertEqual(edited_revision, state["accepted_revision"])
        self.assertEqual(
            edited_revision, state["work_items"]["VP-T001"]["accepted_revision"]
        )
        self.assertEqual(
            "plan-edits-001",
            state["artifacts"][edited_revision]["producer_task_id"],
        )
        self.assertEqual("plan-review-002", state["plan_inventory"]["review_task_id"])

    def test_direct_table_edit_without_builder_fold_is_drift(self) -> None:
        """A human editing a revision-tracked table file in place, without an
        APPLY_PLAN_EDITS fold, cannot advance the plan gate: the workspace no
        longer matches the accepted revision."""
        self.enter_plan()
        _, plan_revision = self.create_builder_revision()
        self.plan_reviewed(plan_revision)
        (self.root / "verif" / "vplan.md").write_text(
            "# Verification plan edited by the human\n", encoding="utf-8"
        )
        self.run_cli(
            "approve", "--root", str(self.root), "--gate", "VPLAN",
            "--decision", "APPROVED", "--approved-by", "unit-test",
            "--note", "Approved on the stale revision.", "--revision",
            plan_revision,
        )
        result = self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "attempt on a drifted revision",
            expected=2,
        )
        self.assertIn("no longer matches revision", result.stderr)

    def test_write_vplan_revision_includes_plan_tables(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        request = self.new_task(
            "plan-build-001",
            "builder",
            "WRITE_VPLAN",
            lineage="plan-build",
            retry_kind="dispatch",
            phase="PLAN",
            input_revision=baseline,
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
            ],
            read=["spec.md", "rtl.f"],
            write=["verif"],
        )
        table_paths = [
            "verif/vplan.md",
            "verif/tables/tables.json",
            "verif/tables/testpoint.xlsx",
            "verif/tables/testlist.xlsx",
            "verif/tables/covergroups.xlsx",
        ]
        artifacts = []
        for rel in table_paths:
            full = self.root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes((rel + "\n").encode("utf-8"))
            kind = "vplan" if rel == "verif/vplan.md" else "tables"
            artifacts.append(self.artifact(full, self.root, kind))
        self.record(request, outcome="READY_FOR_REVIEW", artifacts=artifacts)
        revision = self.state()["tasks"]["plan-build-001"]["output_revision"]
        recorded = set(self.state()["artifacts"][revision]["paths"])
        for rel in table_paths:
            self.assertIn(rel, recorded)
        self.run_cli("validate", "--root", str(self.root))

    def test_stale_child_revision_is_rejected(self) -> None:
        self.create_builder_revision()
        stale = "sha256:" + "0" * 64
        failed = self.run_cli(
            "new-task",
            "--root",
            str(self.root),
            "--task-id",
            "stale-review-001",
            "--role",
            "reviewer",
            "--action",
            "REVIEW_VPLAN",
            "--lineage",
            "stale-review",
            "--retry-kind",
            "review",
            "--input-revision",
            stale,
            expected=2,
        )
        self.assertIn("unknown input revision", failed.stderr)

    def test_retry_budget_is_persistent_by_lineage(self) -> None:
        self.enter_plan()
        for index in range(1, 4):
            self.run_cli(
                "new-task",
                "--root",
                str(self.root),
                "--task-id",
                f"dispatch-{index:03d}",
                "--role",
                "builder",
                "--action",
                "WRITE_VPLAN",
                "--lineage",
                "same-dispatch",
                "--retry-kind",
                "dispatch",
            )
        failed = self.run_cli(
            "new-task",
            "--root",
            str(self.root),
            "--task-id",
            "dispatch-004",
            "--role",
            "builder",
            "--action",
            "WRITE_VPLAN",
            "--lineage",
            "same-dispatch",
            "--retry-kind",
            "dispatch",
            expected=2,
        )
        self.assertIn("retry limit reached", failed.stderr)

    def test_forward_phase_gate_cannot_be_bypassed(self) -> None:
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "PLAN",
            "--reason", "begin verification planning",
        )
        failed = self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "attempt to bypass V-plan approval", expected=2,
        )
        self.assertIn("PREFLIGHT gate failed", failed.stderr)
        self.assertEqual("PLAN", self.state()["current_phase"])

    def test_builder_non_success_outcomes_cannot_hide_write_drift(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        outcomes = [
            ("COMPLETED", "NO_CHANGE"),
            ("BLOCKED", "BLOCKED"),
            ("FAILED", "INTERNAL_ERROR"),
        ]
        for index, (agent_status, outcome) in enumerate(outcomes, 1):
            request = self.new_task(
                f"drift-{index:03d}",
                "builder",
                "WRITE_VPLAN",
                lineage=f"drift-{index}",
                retry_kind="dispatch",
                phase="PLAN",
                input_revision=baseline,
                inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
                read=["spec.md", "rtl.f"],
                write=["verif"],
            )
            hidden = self.root / "verif" / f"hidden-{index}.sv"
            hidden.parent.mkdir(exist_ok=True)
            hidden.write_text("module hidden; endmodule\n", encoding="utf-8")
            artifact_revisions = set(self.state()["artifacts"])
            failed = self.record(
                request,
                outcome=outcome,
                agent_status=agent_status,
                expected=2,
            )
            self.assertIn("mutated its write scope", failed.stderr)
            self.assertNotIn("Traceback", failed.stderr)
            state = self.state()
            self.assertEqual("READY", state["tasks"][request["task_id"]]["status"])
            self.assertEqual(artifact_revisions, set(state["artifacts"]))
            hidden.unlink()

    def test_builder_rejects_undeclared_created_file(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        request = self.new_task(
            "undeclared-build-001",
            "builder",
            "WRITE_VPLAN",
            lineage="undeclared-build",
            retry_kind="dispatch",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md", "rtl.f"],
            write=["verif"],
        )
        vplan = self.root / "verif" / "vplan.md"
        vplan.parent.mkdir(exist_ok=True)
        vplan.write_text("# V-plan\n", encoding="utf-8")
        (self.root / "verif" / "surprise.sv").write_text(
            "module surprise; endmodule\n", encoding="utf-8"
        )
        artifact_revisions = set(self.state()["artifacts"])
        failed = self.record(
            request,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(vplan, self.root, "vplan")],
            expected=2,
        )
        self.assertIn("files_created does not match", failed.stderr)
        self.assertEqual(artifact_revisions, set(self.state()["artifacts"]))
        self.assertEqual("READY", self.state()["tasks"]["undeclared-build-001"]["status"])

    def test_snapshot_hash_has_unambiguous_file_framing(self) -> None:
        api = runpy.run_path(str(FLOW))
        digest = api["snapshot_path_digest"]
        directory = self.root / "frame"
        directory.mkdir()
        marker = b"F\0frame/b\0"
        (directory / "a").write_bytes(b"left" + marker + b"right")
        one_file = digest(self.root, directory)
        (directory / "a").write_bytes(b"left")
        (directory / "b").write_bytes(b"right")
        self.assertNotEqual(one_file, digest(self.root, directory))

    def test_malformed_payload_is_rejected_without_traceback(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "malformed-run-001"
        request = self.new_task(
            "malformed-run-001",
            "runner",
            "RUN_CASE",
            lineage="malformed-run",
            retry_kind="environment",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        malformed = {
            "schema_version": "dv-result/1.0",
            "task_id": request["task_id"],
            "run_id": request["run_id"],
            "role": "runner",
            "action": "RUN_CASE",
            "attempt": 1,
            "agent_status": "COMPLETED",
            "outcome": "PASS",
            "input_revision": baseline,
            "summary": "Malformed payload test.",
            "artifacts": [],
            "evidence": [],
            "issues": [],
            "payload": [],
            "recommended_next": None,
        }
        result_path = self.root / request["expected_result_path"]
        result_path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
        failed = self.run_cli(
            "record-result", "--root", str(self.root), "--task-id", request["task_id"],
            expected=2,
        )
        self.assertIn("payload must be an object", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual("READY", self.state()["tasks"][request["task_id"]]["status"])

        log_path = self.root / run_dir / "run.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("PASS\n", encoding="utf-8")
        valid_result = {
            **malformed,
            "artifacts": [self.artifact(log_path, self.root, "log")],
            "evidence": [
                {
                    "id": "RUN-EVID-001",
                    "path": log_path.relative_to(self.root).as_posix(),
                    "line_or_time": "1",
                    "observation": "The bounded command completed.",
                }
            ],
            "payload": {
                "tested_revision": baseline,
                "run": {
                    "phase": "SIMULATION",
                    "test": "smoke_test",
                    "seed": 1,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 0,
                    "duration_s": 1,
                },
                "counts": {
                    "uvm_fatal": 0,
                    "uvm_error": 0,
                    "assertion_failures": 0,
                    "scoreboard_mismatches": 0,
                },
                "environment_actions": [],
                "failure": {
                    "signature": None,
                    "first_time": None,
                    "log_excerpt_ref": None,
                },
                "case_results": [],
                "coverage_summary": None,
            },
        }
        malformed_variants = [
            ("case_results", {"payload.case_results": 1}),
            (
                "recommended_role",
                {
                    "recommended_next": {
                        "role": [],
                        "action": "RUN_CASE",
                        "reason": "Invalid role type must be rejected.",
                    }
                },
            ),
        ]
        for label, changes in malformed_variants:
            with self.subTest(label=label):
                candidate = json.loads(json.dumps(valid_result))
                if "payload.case_results" in changes:
                    candidate["payload"]["case_results"] = changes["payload.case_results"]
                if "recommended_next" in changes:
                    candidate["recommended_next"] = changes["recommended_next"]
                result_path.write_text(
                    json.dumps(candidate) + "\n", encoding="utf-8"
                )
                failed = self.run_cli(
                    "record-result",
                    "--root",
                    str(self.root),
                    "--task-id",
                    request["task_id"],
                    expected=2,
                )
                self.assertNotIn("Traceback", failed.stderr)
                self.assertEqual(
                    "READY", self.state()["tasks"][request["task_id"]]["status"]
                )

        failing_run_dir = (
            Path(".dv") / "runs" / self.state()["run_id"] / "malformed-debug-001"
        )
        diagnosis = self.new_task(
            "malformed-debug-001",
            "runner",
            "RUN_CASE",
            lineage="malformed-debug",
            retry_kind="debug-evidence",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[failing_run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        failing_log = self.root / failing_run_dir / "run.log"
        failing_log.parent.mkdir(parents=True)
        failing_log.write_text("UVM_ERROR: scoreboard mismatch\n", encoding="utf-8")
        diagnosis_result = {
            "schema_version": "dv-result/1.0",
            "task_id": diagnosis["task_id"],
            "run_id": diagnosis["run_id"],
            "role": "runner",
            "action": "RUN_CASE",
            "attempt": diagnosis["attempt"],
            "agent_status": "COMPLETED",
            "outcome": "SIMULATION_FAILURE",
            "input_revision": baseline,
            "summary": "Malformed classification type test.",
            "artifacts": [self.artifact(failing_log, self.root, "log")],
            "evidence": [
                {
                    "id": "RUN-EVID-001",
                    "path": failing_log.relative_to(self.root).as_posix(),
                    "line_or_time": "1",
                    "observation": "The run log records the failure.",
                }
            ],
            "issues": [],
            "payload": {
                "tested_revision": baseline,
                "run": {
                    "phase": "SIMULATION",
                    "test": "smoke_test",
                    "seed": 1,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 1,
                    "duration_s": 1,
                },
                "counts": {
                    "uvm_fatal": 0,
                    "uvm_error": 1,
                    "assertion_failures": 0,
                    "scoreboard_mismatches": 1,
                },
                "environment_actions": [],
                "failure": {
                    "signature": "scoreboard mismatch",
                    "first_time": "line 271",
                    "log_excerpt_ref": failing_log.relative_to(self.root).as_posix(),
                },
                "case_results": [],
                "coverage_summary": None,
                "diagnosis": {
                    "state": "DIAGNOSED",
                    "classification": [],
                    "subtype": "MISMATCH",
                    "confidence": "MEDIUM",
                    "expected": "Expected behavior.",
                    "observed": "Observed behavior.",
                    "root_cause": "The discriminator is intentionally malformed.",
                    "suspected_locations": [],
                    "affected_ids": [],
                    "route_to": "HUMAN",
                    "fix_request": {
                        "instructions": "Fix the discriminator.",
                        "candidate_files": [],
                        "must_preserve": [],
                    },
                    "rerun": {
                        "test": "smoke_test",
                        "seed": 1,
                        "extra_diagnostics": [],
                    },
                },
            },
            "recommended_next": None,
        }
        diagnosis_result_path = self.root / diagnosis["expected_result_path"]
        diagnosis_result_path.write_text(
            json.dumps(diagnosis_result) + "\n", encoding="utf-8"
        )
        failed = self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            diagnosis["task_id"],
            expected=2,
        )
        self.assertIn("invalid diagnosis classification", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(
            "READY", self.state()["tasks"][diagnosis["task_id"]]["status"]
        )

    def test_task_scopes_protect_project_and_isolate_runner(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        outside = self.new_task(
            "outside-build-001", "builder", "WRITE_VPLAN",
            lineage="outside-build", retry_kind="dispatch", phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md", "rtl.f"], write=["../outside"], seal_expected=2,
        )
        self.assertEqual("DRAFT", self.state()["tasks"][outside["task_id"]]["status"])
        protected = self.new_task(
            "protected-build-001", "builder", "WRITE_VPLAN",
            lineage="protected-build", retry_kind="dispatch", phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "rtl_filelist", "path": "rtl.f", "required": True}],
            read=["rtl.f"], write=["rtl.f"], seal_expected=2,
        )
        self.assertEqual("DRAFT", self.state()["tasks"][protected["task_id"]]["status"])
        wrong_run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "other-task"
        runner = self.new_task(
            "runner-scope-001", "runner", "RUN_CASE",
            lineage="runner-scope", retry_kind="environment", phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"], write=[wrong_run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]}, seal_expected=2,
        )
        self.assertEqual("DRAFT", self.state()["tasks"][runner["task_id"]]["status"])

    def test_runner_result_must_match_sealed_test_and_seed(self) -> None:
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "context-run-001"
        request = self.new_task(
            "context-run-001",
            "runner",
            "RUN_CASE",
            lineage="context-run",
            retry_kind="environment",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["expected_test"], "seeds": [17]},
        )
        log_path = self.root / run_dir / "run.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("PASS\n", encoding="utf-8")
        result = {
            "schema_version": "dv-result/1.0",
            "task_id": request["task_id"],
            "run_id": request["run_id"],
            "role": "runner",
            "action": "RUN_CASE",
            "attempt": request["attempt"],
            "agent_status": "COMPLETED",
            "outcome": "PASS",
            "input_revision": baseline,
            "summary": "The result intentionally disagrees with its sealed ticket.",
            "artifacts": [self.artifact(log_path, self.root, "log")],
            "evidence": [
                {
                    "id": "RUN-EVID-001",
                    "path": log_path.relative_to(self.root).as_posix(),
                    "line_or_time": "1",
                    "observation": "A run log was produced.",
                }
            ],
            "issues": [],
            "payload": {
                "tested_revision": baseline,
                "run": {
                    "phase": "SIMULATION",
                    "test": "different_test",
                    "seed": 18,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 0,
                    "duration_s": 1,
                },
                "counts": {
                    "uvm_fatal": 0,
                    "uvm_error": 0,
                    "assertion_failures": 0,
                    "scoreboard_mismatches": 0,
                },
                "environment_actions": [],
                "failure": {
                    "signature": None,
                    "first_time": None,
                    "log_excerpt_ref": None,
                },
                "case_results": [],
                "coverage_summary": None,
            },
            "recommended_next": None,
        }
        result_path = self.root / request["expected_result_path"]
        result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        revision_before = self.state()["state_revision"]
        failed = self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            request["task_id"],
            expected=2,
        )
        self.assertIn("payload.run.test must equal", failed.stderr)
        self.assertIn("payload.run.seed must equal", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        state = self.state()
        self.assertEqual(revision_before, state["state_revision"])
        self.assertEqual("READY", state["tasks"][request["task_id"]]["status"])

    def test_awaiting_review_requires_a_valid_builder_result(self) -> None:
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "VPLAN",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "Plan approved by the operator.",
            "--revision",
            plan_revision,
        )
        self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "PREFLIGHT",
            "--reason",
            "materialize the approved test inventory",
        )
        self.run_cli(
            "set-item",
            "--root",
            str(self.root),
            "--item-id",
            "VP-T001",
            "--status",
            "BUILDING",
            "--last-task-id",
            "plan-build-001",
            "--reason",
            "begin implementation",
        )
        revision_before = self.state()["state_revision"]
        failed = self.run_cli(
            "set-item",
            "--root",
            str(self.root),
            "--item-id",
            "VP-T001",
            "--status",
            "AWAITING_REVIEW",
            "--last-task-id",
            "plan-review-001",
            "--reason",
            "a reviewer result cannot stand in for a builder result",
            expected=2,
        )
        self.assertIn("requires a completed builder change", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        state = self.state()
        self.assertEqual(revision_before, state["state_revision"])
        self.assertEqual("BUILDING", state["work_items"]["VP-T001"]["status"])

    def test_dut_fix_request_requires_runner_diagnosis_lineage(self) -> None:
        """A DUT fix request reads the embedded diagnosis of the failing runner result."""
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "VPLAN",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "Plan approved by the operator.",
            "--revision",
            plan_revision,
        )
        self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "PREFLIGHT",
            "--reason",
            "materialize the approved test inventory",
        )
        self.assertIn("VP-T001", self.state()["work_items"])

        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "feature-run-fail-001"
        failure = self.new_task(
            "feature-run-fail-001",
            "runner",
            "RUN_CASE",
            lineage="feature-run-fail",
            retry_kind="dispatch",
            phase="PREFLIGHT",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
            ],
            read=["spec.md", "rtl.f"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["vp_t001_test"], "seeds": [7]},
        )
        self.record_failure(
            failure,
            diagnosis={
                "state": "DIAGNOSED",
                "classification": "DUT_BUG",
                "subtype": "address_hazard",
                "confidence": "HIGH",
                "expected": "Channel A holds its address through the full handshake.",
                "observed": "Channel A drops the address one cycle before ready.",
                "root_cause": "The DUT releases the address early.",
                "suspected_locations": [],
                "affected_ids": ["VP-T001"],
                "route_to": "RTL_OWNER",
                "fix_request": {
                    "instructions": "Hold the address until ready is sampled.",
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {"test": "vp_t001_test", "seed": 7, "extra_diagnostics": []},
            },
        )
        self.assertEqual(
            "SIMULATION_FAILURE", self.state()["tasks"][failure["task_id"]]["outcome"]
        )

        # The failing runner result's own embedded diagnosis is the only
        # diagnosis: a fix request cannot point at a runner whose embedded
        # diagnosis is not a high-confidence DUT_BUG.
        tb_run_dir = (
            Path(".dv") / "runs" / self.state()["run_id"] / "feature-run-fail-002"
        )
        tb_failure = self.new_task(
            "feature-run-fail-002",
            "runner",
            "RUN_CASE",
            lineage="feature-run-fail-tb",
            retry_kind="dispatch",
            phase="PREFLIGHT",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
            ],
            read=["spec.md", "rtl.f"],
            write=[tb_run_dir.as_posix()],
            context={"test_ids": ["vp_t001_test"], "seeds": [7]},
        )
        self.record_failure(
            tb_failure,
            diagnosis={
                "state": "DIAGNOSED",
                "classification": "TB_BUG",
                "subtype": "monitor_sampling",
                "confidence": "HIGH",
                "expected": "The monitor publishes a transfer after the handshake.",
                "observed": "The monitor publishes before ready.",
                "root_cause": "The monitor samples on valid alone.",
                "suspected_locations": [],
                "affected_ids": ["VP-T001"],
                "route_to": "BUILDER",
                "fix_request": {
                    "instructions": "Publish only on a completed handshake.",
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {"test": "vp_t001_test", "seed": 7, "extra_diagnostics": []},
            },
        )
        rejected = self.run_cli(
            "add-fix-request",
            "--root",
            str(self.root),
            "--failure-task-id",
            tb_failure["task_id"],
            "--summary",
            "not-a-dut-bug",
            "--affected-id",
            "VP-T001",
            expected=2,
        )
        self.assertIn("non-low-confidence DUT_BUG diagnosis", rejected.stderr)
        self.assertNotIn("Traceback", rejected.stderr)

        self.run_cli(
            "add-fix-request",
            "--root",
            str(self.root),
            "--failure-task-id",
            failure["task_id"],
            "--summary",
            "DUT address hazard confirmed",
            "--affected-id",
            "VP-T001",
        )
        state = self.state()
        self.assertEqual("OPEN", state["fix_requests"][0]["status"])
        self.assertEqual("DUT_BUG", state["fix_requests"][0]["classification"])
        self.assertEqual("BLOCKED_DUT", state["work_items"]["VP-T001"]["status"])
        self.assertEqual(
            failure["task_id"], state["work_items"]["VP-T001"]["last_task_id"]
        )

    def test_failing_runner_outcome_requires_embedded_diagnosis(self) -> None:
        """A failing runner outcome without the embedded diagnosis is rejected."""
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "no-diag-run-001"
        request = self.new_task(
            "no-diag-run-001",
            "runner",
            "RUN_CASE",
            lineage="no-diag-run",
            retry_kind="environment",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        failed = self.record_failure(request, diagnosis=None, expected=2)
        self.assertIn(
            "SIMULATION_FAILURE requires an embedded payload.diagnosis", failed.stderr
        )
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual("READY", self.state()["tasks"][request["task_id"]]["status"])

    def test_non_failure_outcome_forbids_embedded_diagnosis(self) -> None:
        """A passing runner outcome cannot smuggle a diagnosis object."""
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "pass-diag-run-001"
        request = self.new_task(
            "pass-diag-run-001",
            "runner",
            "RUN_CASE",
            lineage="pass-diag-run",
            retry_kind="environment",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        log_path = self.root / run_dir / "run.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("PASS\n", encoding="utf-8")
        result = {
            "schema_version": "dv-result/1.0",
            "task_id": request["task_id"],
            "run_id": request["run_id"],
            "role": "runner",
            "action": "RUN_CASE",
            "attempt": request["attempt"],
            "agent_status": "COMPLETED",
            "outcome": "PASS",
            "input_revision": baseline,
            "summary": "A pass that illegally embeds a diagnosis.",
            "artifacts": [self.artifact(log_path, self.root, "log")],
            "evidence": [
                {
                    "id": "RUN-EVID-001",
                    "path": log_path.relative_to(self.root).as_posix(),
                    "line_or_time": "1",
                    "observation": "The bounded command completed.",
                }
            ],
            "issues": [],
            "payload": {
                "tested_revision": baseline,
                "run": {
                    "phase": "SIMULATION",
                    "test": "smoke_test",
                    "seed": 1,
                    "command": "unit-test-command",
                    "cwd": str(self.root),
                    "tool": "unit-sim",
                    "tool_version": "1.0",
                    "exit_code": 0,
                    "duration_s": 1,
                },
                "counts": {
                    "uvm_fatal": 0,
                    "uvm_error": 0,
                    "assertion_failures": 0,
                    "scoreboard_mismatches": 0,
                },
                "environment_actions": [],
                "failure": {
                    "signature": None,
                    "first_time": None,
                    "log_excerpt_ref": None,
                },
                "case_results": [],
                "coverage_summary": None,
                "diagnosis": {
                    "state": "DIAGNOSED",
                    "classification": "TB_BUG",
                    "subtype": None,
                    "confidence": "LOW",
                    "expected": "The diagnosis must never ride on a passing outcome.",
                    "observed": "It does.",
                    "root_cause": None,
                    "suspected_locations": [],
                    "affected_ids": [],
                    "route_to": "BUILDER",
                    "fix_request": {
                        "instructions": "N/A",
                        "candidate_files": [],
                        "must_preserve": [],
                    },
                    "rerun": {"test": "smoke_test", "seed": 1, "extra_diagnostics": []},
                },
            },
            "recommended_next": None,
        }
        result_path = self.root / request["expected_result_path"]
        result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        failed = self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            request["task_id"],
            expected=2,
        )
        self.assertIn(
            "payload.diagnosis is only valid on a failing runner outcome", failed.stderr
        )
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual("READY", self.state()["tasks"][request["task_id"]]["status"])

    def test_needs_more_evidence_requires_bounded_extra_diagnostics(self) -> None:
        """NEEDS_MORE_EVIDENCE without bounded rerun diagnostics is rejected."""
        self.enter_plan()
        baseline = self.state()["baseline_revision"]
        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "needs-more-001"
        request = self.new_task(
            "needs-more-001",
            "runner",
            "RUN_CASE",
            lineage="needs-more",
            retry_kind="environment",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[run_dir.as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        failed = self.record_failure(
            request,
            diagnosis={
                "state": "NEEDS_MORE_EVIDENCE",
                "classification": "UNKNOWN",
                "subtype": None,
                "confidence": "LOW",
                "expected": "No single hypothesis is supported yet.",
                "observed": "The failure does not reproduce under the original seed.",
                "root_cause": None,
                "suspected_locations": [],
                "affected_ids": [],
                "route_to": "RUNNER",
                "fix_request": {
                    "instructions": "Collect bounded evidence first.",
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {"test": "smoke_test", "seed": 1, "extra_diagnostics": []},
            },
            expected=2,
        )
        self.assertIn(
            "NEEDS_MORE_EVIDENCE requires bounded extra diagnostics", failed.stderr
        )
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual("READY", self.state()["tasks"][request["task_id"]]["status"])

    def test_passed_item_lineage_walks_embedded_diagnosis(self) -> None:
        """A passing item lineage accepts a DIAGNOSED evidence rerun ancestor
        and forces the pass to rerun its exact test and seed."""
        run_root = Path(".dv") / "runs" / self.state()["run_id"]
        self.enter_plan()
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        self.run_cli(
            "approve", "--root", str(self.root), "--gate", "VPLAN",
            "--decision", "APPROVED", "--approved-by", "unit-test",
            "--note", "Plan approved by the operator.", "--revision", plan_revision,
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "materialize the approved test inventory",
        )

        feature_build = self.new_task(
            "vp-build-001",
            "builder",
            "IMPLEMENT_FEATURE_BATCH",
            lineage="vp-build",
            retry_kind="dispatch",
            phase="PREFLIGHT",
            input_revision=plan_revision,
            parent="plan-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=["verif"],
            context={"work_item_ids": ["VP-T001"]},
        )
        tb_path = self.root / "verif" / "vp_t001.sv"
        tb_path.write_text("module vp_t001; endmodule\n", encoding="utf-8")
        self.record(
            feature_build,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(tb_path, self.root, "test_source")],
        )
        feature_revision = self.state()["tasks"]["vp-build-001"]["output_revision"]
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "BUILDING", "--last-task-id", "vp-build-001",
            "--reason", "P0 implementation started.",
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "AWAITING_REVIEW", "--last-task-id", "vp-build-001",
            "--reason", "P0 artifacts are ready for static review.",
        )
        feature_review = self.new_task(
            "vp-review-001",
            "reviewer",
            "REVIEW_TB",
            lineage="vp-review",
            retry_kind="review",
            phase="PREFLIGHT",
            input_revision=feature_revision,
            parent="vp-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=[],
            context={"work_item_ids": ["VP-T001"]},
        )
        self.record(feature_review, outcome="APPROVED")
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "READY_TO_RUN", "--last-task-id", "vp-review-001",
            "--reason", "static review approved this revision.",
        )

        # The first run needs more evidence: it cannot own the failure yet.
        fail_run = self.new_task(
            "vp-fail-001",
            "runner",
            "RUN_CASE",
            lineage="vp-fail",
            retry_kind="dispatch",
            phase="PREFLIGHT",
            input_revision=feature_revision,
            parent="vp-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "vp-fail-001").as_posix()],
            context={
                "work_item_ids": ["VP-T001"], "test_ids": ["VP-T001"], "seeds": [17],
            },
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "RUNNING", "--last-task-id", "vp-fail-001",
            "--reason", "the targeted test is running.",
        )
        self.record_failure(
            fail_run,
            diagnosis={
                "state": "NEEDS_MORE_EVIDENCE",
                "classification": "UNKNOWN",
                "subtype": None,
                "confidence": "LOW",
                "expected": "No single hypothesis is supported by the first failure.",
                "observed": "The scoreboard mismatch does not reproduce under the original seed.",
                "root_cause": None,
                "suspected_locations": [],
                "affected_ids": ["VP-T001"],
                "route_to": "RUNNER",
                "fix_request": {
                    "instructions": "Collect the bounded diagnostic dump before ownership.",
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {
                    "test": "VP-T001",
                    "seed": 17,
                    "extra_diagnostics": ["--dump-mem", "--dump-interface"],
                },
            },
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "DEBUGGING", "--last-task-id", "vp-fail-001",
            "--reason", "the failure needs bounded evidence.",
        )

        # The evidence rerun collects the diagnostics and now owns the failure.
        evidence_run = self.new_task(
            "vp-evidence-001",
            "runner",
            "RUN_CASE",
            lineage="vp-evidence",
            retry_kind="debug-evidence",
            phase="PREFLIGHT",
            input_revision=feature_revision,
            parent="vp-fail-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif", (run_root / "vp-fail-001").as_posix()],
            write=[(run_root / "vp-evidence-001").as_posix()],
            context={
                "work_item_ids": ["VP-T001"], "test_ids": ["VP-T001"], "seeds": [17],
                "extra_diagnostics": ["--dump-mem", "--dump-interface"],
            },
        )
        self.record_failure(
            evidence_run,
            diagnosis={
                "state": "DIAGNOSED",
                "classification": "TEST_BUG",
                "subtype": "seed_starvation",
                "confidence": "HIGH",
                "expected": "The checker finds the transfer under the extra dump.",
                "observed": "The extra dump shows the driver under-constrained the address.",
                "root_cause": "The driver does not hold the address long enough for slow lanes.",
                "suspected_locations": [],
                "affected_ids": ["VP-T001"],
                "route_to": "BUILDER",
                "fix_request": {
                    "instructions": "Constrain the address hold to the full latency window.",
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {"test": "VP-T001", "seed": 17, "extra_diagnostics": []},
            },
        )

        # The debug fix builder routes to BUILDER and produces a new revision.
        fix_build = self.new_task(
            "vp-fix-001",
            "builder",
            "APPLY_DEBUG_FIX",
            lineage="vp-fix",
            retry_kind="tb-fix",
            phase="PREFLIGHT",
            input_revision=feature_revision,
            parent="vp-evidence-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=["verif"],
            context={"work_item_ids": ["VP-T001"]},
        )
        fixed_path = self.root / "verif" / "vp_t001_fix.sv"
        fixed_path.write_text(
            "module vp_t001_fix; // constrained address hold\nendmodule\n",
            encoding="utf-8",
        )
        self.record(
            fix_build,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(fixed_path, self.root, "test_source")],
        )
        fix_revision = self.state()["tasks"]["vp-fix-001"]["output_revision"]
        self.assertNotEqual(fix_revision, feature_revision)
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "FIXING", "--last-task-id", "vp-evidence-001",
            "--reason", "the diagnosed fix is being applied.",
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "AWAITING_REVIEW", "--last-task-id", "vp-fix-001",
            "--reason", "the debug fix is ready for static review.",
        )
        fix_review = self.new_task(
            "vp-fix-review-001",
            "reviewer",
            "REVIEW_FIX",
            lineage="vp-fix-review",
            retry_kind="review",
            phase="PREFLIGHT",
            input_revision=fix_revision,
            parent="vp-fix-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=[],
            context={"work_item_ids": ["VP-T001"]},
        )
        self.record(fix_review, outcome="APPROVED")
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "READY_TO_RUN", "--last-task-id", "vp-fix-review-001",
            "--reason", "the fix review approved this revision.",
        )

        # The matching pass must rerun the diagnosed failure's exact test and seed.
        pass_run = self.new_task(
            "vp-pass-001",
            "runner",
            "RUN_CASE",
            lineage="vp-pass",
            retry_kind="tb-fix",
            phase="PREFLIGHT",
            input_revision=fix_revision,
            parent="vp-fix-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "vp-pass-001").as_posix()],
            context={
                "work_item_ids": ["VP-T001"], "test_ids": ["VP-T001"], "seeds": [17],
            },
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "RUNNING", "--last-task-id", "vp-pass-001",
            "--reason", "the exact diagnosed rerun is running.",
        )
        pass_log = self.root / run_root / "vp-pass-001" / "run.log"
        pass_log.parent.mkdir(parents=True)
        pass_log.write_text("PASS\n", encoding="utf-8")
        self.record(
            pass_run,
            outcome="PASS",
            artifacts=[self.artifact(pass_log, self.root, "log")],
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "PASSED", "--last-task-id", "vp-pass-001",
            "--reason", "the exact diagnosed rerun passed.",
        )
        item = self.state()["work_items"]["VP-T001"]
        self.assertEqual("PASSED", item["status"])
        self.assertEqual("vp-pass-001", item["run_task_id"])
        self.assertEqual("vp-fix-001", item["builder_task_id"])

    def test_preflight_requires_human_vplan_approval(self) -> None:
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        failed = self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "PREFLIGHT",
            "--reason",
            "agent review approved but human approval is still absent",
            expected=2,
        )
        self.assertIn("human VPLAN approval", failed.stderr)
        self.assertEqual("PLAN", self.state()["current_phase"])
        self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "VPLAN",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "Plan approved by the operator.",
            "--revision",
            plan_revision,
        )
        self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "PREFLIGHT",
            "--reason",
            "human approval recorded",
        )
        self.assertEqual("PREFLIGHT", self.state()["current_phase"])

    def test_complete_requires_frozen_regression_audit_and_human_gate(self) -> None:
        run_root = Path(".dv") / "runs" / self.state()["run_id"]
        self.enter_plan()
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "VPLAN",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "Plan approved by the operator.",
            "--revision",
            plan_revision,
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "accept the reviewed plan inventory",
        )

        preflight = self.new_task(
            "preflight-001",
            "runner",
            "PREFLIGHT",
            lineage="preflight",
            retry_kind="environment",
            phase="PREFLIGHT",
            input_revision=plan_revision,
            parent="plan-review-001",
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
                {"kind": "vplan", "path": "verif/vplan.md", "required": True},
            ],
            read=["spec.md", "rtl.f", "verif/vplan.md"],
            write=[(run_root / "preflight-001").as_posix()],
        )
        preflight_log = self.root / run_root / "preflight-001" / "preflight.log"
        preflight_log.parent.mkdir(parents=True)
        preflight_log.write_text("PREFLIGHT PASS\n", encoding="utf-8")
        self.record(
            preflight,
            outcome="PASS",
            artifacts=[self.artifact(preflight_log, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "SMOKE",
            "--reason", "preflight passed on the accepted plan revision",
        )

        smoke_build = self.new_task(
            "smoke-build-001",
            "builder",
            "BUILD_SMOKE_FOUNDATION",
            lineage="smoke-build",
            retry_kind="dispatch",
            phase="SMOKE",
            input_revision=plan_revision,
            parent="preflight-001",
            inputs=[
                {"kind": "vplan", "path": "verif/vplan.md", "required": True},
                {"kind": "spec", "path": "spec.md", "required": True},
            ],
            read=["spec.md", "rtl.f", "verif"],
            write=["verif"],
        )
        tb_path = self.root / "verif" / "tb_smoke.sv"
        tb_path.write_text("module tb_smoke; endmodule\n", encoding="utf-8")
        self.record(
            smoke_build,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(tb_path, self.root, "tb_source")],
        )
        smoke_revision = self.state()["tasks"]["smoke-build-001"]["output_revision"]
        code_review = self.new_task(
            "smoke-review-001",
            "reviewer",
            "REVIEW_TB",
            lineage="smoke-review",
            retry_kind="review",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[],
            context={"test_ids": ["SMOKE"]},
        )
        self.record(code_review, outcome="APPROVED")

        compile_request = self.new_task(
            "smoke-compile-001",
            "runner",
            "COMPILE_ELAB",
            lineage="smoke-compile",
            retry_kind="environment",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "smoke-compile-001").as_posix()],
        )
        compile_log = self.root / run_root / "smoke-compile-001" / "compile.log"
        compile_log.parent.mkdir(parents=True)
        compile_log.write_text("COMPILE AND ELABORATION PASS\n", encoding="utf-8")
        self.record(
            compile_request,
            outcome="PASS",
            artifacts=[self.artifact(compile_log, self.root, "log")],
        )
        smoke_request = self.new_task(
            "smoke-run-001",
            "runner",
            "RUN_CASE",
            lineage="smoke-run",
            retry_kind="environment",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-compile-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "smoke-run-001").as_posix()],
            context={"test_ids": ["smoke_test"], "seeds": [1]},
        )
        smoke_log = self.root / run_root / "smoke-run-001" / "smoke.log"
        smoke_log.parent.mkdir(parents=True)
        smoke_log.write_text("SMOKE PASS\n", encoding="utf-8")
        self.record(
            smoke_request,
            outcome="PASS",
            artifacts=[self.artifact(smoke_log, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "FEATURES",
            "--reason", "review, compile, elaboration, and smoke passed on one revision",
        )

        feature_build = self.new_task(
            "p0-build-001",
            "builder",
            "IMPLEMENT_FEATURE_BATCH",
            lineage="p0-build",
            retry_kind="dispatch",
            phase="FEATURES",
            input_revision=smoke_revision,
            parent="smoke-run-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=["verif"],
            context={"work_item_ids": ["VP-T001"]},
        )
        feature_path = self.root / "verif" / "p1_test.sv"
        feature_path.write_text("module p1_test; endmodule\n", encoding="utf-8")
        self.record(
            feature_build,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(feature_path, self.root, "test_source")],
        )
        feature_revision = self.state()["tasks"]["p0-build-001"]["output_revision"]
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "BUILDING", "--last-task-id", "p0-build-001",
            "--reason", "P0 implementation started.",
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "AWAITING_REVIEW", "--last-task-id", "p0-build-001",
            "--reason", "P0 artifacts are ready for static review.",
        )
        feature_review = self.new_task(
            "p0-review-001",
            "reviewer",
            "REVIEW_TB",
            lineage="p0-review",
            retry_kind="review",
            phase="FEATURES",
            input_revision=feature_revision,
            parent="p0-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=[],
            context={"work_item_ids": ["VP-T001"]},
        )
        self.record(feature_review, outcome="APPROVED")
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "READY_TO_RUN", "--last-task-id", "p0-review-001",
            "--reason", "P0 static review approved this revision.",
        )
        targeted = self.new_task(
            "p0-run-001",
            "runner",
            "RUN_CASE",
            lineage="p0-run",
            retry_kind="environment",
            phase="FEATURES",
            input_revision=feature_revision,
            parent="p0-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "p0-run-001").as_posix()],
            context={
                "work_item_ids": ["VP-T001"],
                "test_ids": ["VP-T001"],
                "seeds": [17],
            },
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "RUNNING", "--last-task-id", "p0-run-001",
            "--reason", "The exact targeted test is running.",
        )
        targeted_log = self.root / run_root / "p0-run-001" / "run.log"
        targeted_log.parent.mkdir(parents=True)
        targeted_log.write_text("P0 PASS\n", encoding="utf-8")
        self.record(
            targeted,
            outcome="PASS",
            artifacts=[self.artifact(targeted_log, self.root, "log")],
        )
        self.run_cli(
            "set-item",
            "--root",
            str(self.root),
            "--item-id",
            "VP-T001",
            "--status",
            "PASSED",
            "--last-task-id",
            "p0-run-001",
            "--reason",
            "Mandatory unit-test work item passed.",
        )
        cumulative = self.new_task(
            "cumulative-001",
            "runner",
            "RUN_REGRESSION",
            lineage="cumulative",
            retry_kind="none",
            phase="FEATURES",
            input_revision=feature_revision,
            parent="p0-run-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "cumulative-001").as_posix()],
            context={
                "regression_scope": "CUMULATIVE",
                "work_item_ids": ["VP-T001"],
                "case_manifest": [{"test": "VP-T001", "seed": 17}],
            },
        )
        cumulative_log = self.root / run_root / "cumulative-001" / "regression.log"
        cumulative_log.parent.mkdir(parents=True)
        cumulative_log.write_text("CUMULATIVE PASS\n", encoding="utf-8")
        self.record(
            cumulative,
            outcome="PASS",
            artifacts=[self.artifact(cumulative_log, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "COVERAGE",
            "--reason", "all directed items and cumulative regression passed",
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "REGRESSION",
            "--reason", "the plan has no mandatory random or coverage campaign",
        )
        failed = self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "SIGNOFF",
            "--reason",
            "must fail",
            expected=2,
        )
        self.assertIn("FROZEN regression", failed.stderr)
        self.assertEqual("REGRESSION", self.state()["current_phase"])

        run_dir = Path(".dv") / "runs" / self.state()["run_id"] / "regression-001"
        regression = self.new_task(
            "regression-001",
            "runner",
            "RUN_REGRESSION",
            lineage="frozen-regression",
            retry_kind="none",
            phase="REGRESSION",
            input_revision=feature_revision,
            parent="cumulative-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif/vplan.md"],
            write=[run_dir.as_posix()],
            context={
                "regression_scope": "FROZEN",
                "work_item_ids": ["VP-T001"],
                "case_manifest": [{"test": "VP-T001", "seed": 17}],
            },
        )
        log_path = self.root / run_dir / "regression.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("PASS\n", encoding="utf-8")
        self.record(
            regression,
            outcome="PASS",
            artifacts=[self.artifact(log_path, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "SIGNOFF",
            "--reason", "the exact frozen regression passed",
        )

        audit = self.new_task(
            "signoff-audit-001",
            "reviewer",
            "SIGNOFF_AUDIT",
            lineage="signoff-audit",
            retry_kind="review",
            phase="SIGNOFF",
            input_revision=feature_revision,
            parent="regression-001",
            inputs=[
                {
                    "kind": "regression_result",
                    "path": regression["expected_result_path"],
                    "required": True,
                }
            ],
            read=["verif/vplan.md", regression["expected_result_path"]],
            write=[],
        )
        self.record(audit, outcome="APPROVED")
        failed = self.run_cli(
            "transition", "--root", str(self.root), "--to", "COMPLETE",
            "--reason", "human approval is still absent", expected=2,
        )
        self.assertIn("human approval", failed.stderr)
        self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "SIGNOFF",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "Approved after the audit.",
            "--revision",
            feature_revision,
        )
        audit_result = self.root / audit["expected_result_path"]
        original_result = audit_result.read_bytes()
        audit_result.write_bytes(original_result + b"\n")
        failed = self.run_cli(
            "transition", "--root", str(self.root), "--to", "COMPLETE",
            "--reason", "tampered result must fail", expected=2,
        )
        self.assertNotIn("Traceback", failed.stderr)
        audit_result.write_bytes(original_result)
        audit_request = self.root / ".dv" / "tasks" / "signoff-audit-001" / "request.json"
        original_request = audit_request.read_bytes()
        audit_request.write_bytes(original_request + b"\n")
        failed = self.run_cli(
            "transition", "--root", str(self.root), "--to", "COMPLETE",
            "--reason", "tampered request must fail", expected=2,
        )
        self.assertNotIn("Traceback", failed.stderr)
        audit_request.write_bytes(original_request)
        self.run_cli(
            "transition",
            "--root",
            str(self.root),
            "--to",
            "COMPLETE",
            "--reason",
            "all completion evidence is present",
        )
        self.assertEqual("COMPLETE", self.state()["current_phase"])
        self.run_cli("validate", "--root", str(self.root))
        terminal_revision = self.state()["state_revision"]
        failed = self.run_cli(
            "add-blocker",
            "--root",
            str(self.root),
            "--kind",
            "UNKNOWN",
            "--summary",
            "terminal state must reject new blockers",
            expected=2,
        )
        self.assertIn("completed workflow state is immutable", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(terminal_revision, self.state()["state_revision"])
        failed = self.run_cli(
            "approve",
            "--root",
            str(self.root),
            "--gate",
            "POST_SIGNOFF",
            "--decision",
            "APPROVED",
            "--approved-by",
            "unit-test",
            "--note",
            "terminal state must reject new approvals",
            "--revision",
            feature_revision,
            expected=2,
        )
        self.assertIn("completed workflow state is immutable", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(terminal_revision, self.state()["state_revision"])

    def enter_smoke_reviewed(self, run_root: Path) -> tuple[str, str]:
        """Plan + human approval + preflight + smoke build + smoke review.

        Returns ``(plan_revision, smoke_revision)``.
        """
        self.enter_plan()
        _, plan_revision = self.create_builder_revision()
        plan_review = self.new_task(
            "plan-review-001",
            "reviewer",
            "REVIEW_VPLAN",
            lineage="plan-review",
            retry_kind="review",
            phase="PLAN",
            input_revision=plan_revision,
            parent="plan-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "verif/vplan.md"],
            write=[],
        )
        self.record(plan_review, outcome="APPROVED")
        self.run_cli(
            "approve", "--root", str(self.root), "--gate", "VPLAN",
            "--decision", "APPROVED", "--approved-by", "unit-test",
            "--note", "Plan approved by the operator.", "--revision", plan_revision,
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "PREFLIGHT",
            "--reason", "accept the reviewed plan inventory",
        )
        preflight = self.new_task(
            "preflight-001",
            "runner",
            "PREFLIGHT",
            lineage="preflight",
            retry_kind="environment",
            phase="PREFLIGHT",
            input_revision=plan_revision,
            parent="plan-review-001",
            inputs=[
                {"kind": "spec", "path": "spec.md", "required": True},
                {"kind": "rtl_filelist", "path": "rtl.f", "required": True},
                {"kind": "vplan", "path": "verif/vplan.md", "required": True},
            ],
            read=["spec.md", "rtl.f", "verif/vplan.md"],
            write=[(run_root / "preflight-001").as_posix()],
        )
        preflight_log = self.root / run_root / "preflight-001" / "preflight.log"
        preflight_log.parent.mkdir(parents=True)
        preflight_log.write_text("PREFLIGHT PASS\n", encoding="utf-8")
        self.record(
            preflight, outcome="PASS",
            artifacts=[self.artifact(preflight_log, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "SMOKE",
            "--reason", "preflight passed on the accepted plan revision",
        )

        smoke_build = self.new_task(
            "smoke-build-001",
            "builder",
            "BUILD_SMOKE_FOUNDATION",
            lineage="smoke-build",
            retry_kind="dispatch",
            phase="SMOKE",
            input_revision=plan_revision,
            parent="preflight-001",
            inputs=[
                {"kind": "vplan", "path": "verif/vplan.md", "required": True},
                {"kind": "spec", "path": "spec.md", "required": True},
            ],
            read=["spec.md", "rtl.f", "verif"],
            write=["verif"],
        )
        tb_path = self.root / "verif" / "tb_smoke.sv"
        tb_path.write_text("module tb_smoke; endmodule\n", encoding="utf-8")
        self.record(
            smoke_build,
            outcome="READY_FOR_REVIEW",
            artifacts=[self.artifact(tb_path, self.root, "tb_source")],
        )
        smoke_revision = self.state()["tasks"]["smoke-build-001"]["output_revision"]
        code_review = self.new_task(
            "smoke-review-001",
            "reviewer",
            "REVIEW_TB",
            lineage="smoke-review",
            retry_kind="review",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[],
            context={"test_ids": ["SMOKE"]},
        )
        self.record(code_review, outcome="APPROVED")
        return plan_revision, smoke_revision

    def test_smoke_build_and_run_satisfies_features_gate(self) -> None:
        run_root = Path(".dv") / "runs" / self.state()["run_id"]
        _, smoke_revision = self.enter_smoke_reviewed(run_root)

        # Single build-and-run smoke: one RUN_CASE whose command rebuilds the
        # sealed filelist then runs the smoke sim. Its parent is the review, not
        # a separate COMPILE_ELAB task.
        smoke_request = self.new_task(
            "smoke-run-001",
            "runner",
            "RUN_CASE",
            lineage="smoke-run",
            retry_kind="environment",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "smoke-run-001").as_posix()],
            context={"build_and_run": True, "test_ids": ["smoke_test"], "seeds": [1]},
        )
        smoke_log = self.root / run_root / "smoke-run-001" / "smoke.log"
        smoke_log.parent.mkdir(parents=True)
        smoke_log.write_text("BUILD OK\nSMOKE PASS\n", encoding="utf-8")
        self.record(
            smoke_request,
            outcome="PASS",
            artifacts=[self.artifact(smoke_log, self.root, "log")],
        )
        self.run_cli(
            "transition", "--root", str(self.root), "--to", "FEATURES",
            "--reason", "single build-and-run smoke passed on one revision",
        )
        self.assertEqual("FEATURES", self.state()["current_phase"])

    def test_build_and_run_smoke_without_review_fails_gate(self) -> None:
        run_root = Path(".dv") / "runs" / self.state()["run_id"]
        _, smoke_revision = self.enter_smoke_reviewed(run_root)

        # Marked build-and-run but parented directly to the builder: no approved
        # static review, so the FEATURES gate must stay closed.
        smoke_request = self.new_task(
            "smoke-run-001",
            "runner",
            "RUN_CASE",
            lineage="smoke-run",
            retry_kind="environment",
            phase="SMOKE",
            input_revision=smoke_revision,
            parent="smoke-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "smoke-run-001").as_posix()],
            context={"build_and_run": True, "test_ids": ["smoke_test"], "seeds": [1]},
        )
        smoke_log = self.root / run_root / "smoke-run-001" / "smoke.log"
        smoke_log.parent.mkdir(parents=True)
        smoke_log.write_text("BUILD OK\nSMOKE PASS\n", encoding="utf-8")
        self.record(
            smoke_request,
            outcome="PASS",
            artifacts=[self.artifact(smoke_log, self.root, "log")],
        )
        failed = self.run_cli(
            "transition", "--root", str(self.root), "--to", "FEATURES",
            "--reason", "smoke ran but nothing reviewed it",
            expected=2,
        )
        self.assertIn("build-and-run", failed.stderr)

    def test_init_accepts_external_spec_and_rtl(self) -> None:
        api = runpy.run_path(str(FLOW))
        external = tempfile.TemporaryDirectory()
        external_root = Path(external.name)
        project = tempfile.TemporaryDirectory()
        project_root = Path(project.name)
        try:
            (external_root / "spec.md").write_text("# external spec\n", encoding="utf-8")
            (external_root / "rtl").mkdir()
            (external_root / "rtl" / "top.sv").write_text(
                "module ext_top; endmodule\n", encoding="utf-8"
            )
            (external_root / "rtl.f").write_text("rtl/top.sv\n", encoding="utf-8")
            self.run_cli(
                "init",
                "--root",
                str(project_root),
                "--dut-name",
                "unit-dut",
                "--spec",
                str(external_root / "spec.md"),
                "--rtl-filelist",
                str(external_root / "rtl.f"),
                "--rtl-root",
                str(external_root / "rtl"),
                "--top",
                "ext_top",
            )
            state = json.loads(
                (project_root / ".dv" / "workflow_state.json").read_text(encoding="utf-8")
            )
            baseline = state["baseline_revision"]
            self.assertEqual(api["EMPTY_SNAPSHOT_REVISION"], baseline)
            self.assertEqual([], state["artifacts"][baseline]["paths"])
            self.assertEqual({}, state["artifacts"][baseline]["digests"])
            self.assertEqual(
                str((external_root / "spec.md").resolve()), state["design"]["spec"]
            )
            self.assertEqual(
                str((external_root / "rtl").resolve()), state["design"]["rtl_roots"][0]
            )
        finally:
            external.cleanup()
            project.cleanup()

    def test_external_rtl_drift_does_not_invalidate_revision(self) -> None:
        external = tempfile.TemporaryDirectory()
        external_root = Path(external.name)
        try:
            # Reinitialize this project with external specification and RTL.
            shutil.rmtree(self.root / ".dv", ignore_errors=True)
            (external_root / "spec.md").write_text("# spec\n", encoding="utf-8")
            (external_root / "rtl").mkdir()
            (external_root / "rtl" / "top.sv").write_text(
                "module ext_top; endmodule\n", encoding="utf-8"
            )
            (external_root / "rtl.f").write_text("rtl/top.sv\n", encoding="utf-8")
            self.run_cli(
                "init",
                "--root",
                str(self.root),
                "--dut-name",
                "unit-dut",
                "--spec",
                str(external_root / "spec.md"),
                "--rtl-filelist",
                str(external_root / "rtl.f"),
                "--rtl-root",
                str(external_root / "rtl"),
                "--top",
                "ext_top",
            )
            self.enter_plan()
            request = self.new_task(
                "plan-build-001",
                "builder",
                "WRITE_VPLAN",
                lineage="plan-build",
                retry_kind="dispatch",
                phase="PLAN",
                input_revision=self.state()["baseline_revision"],
                inputs=[
                    {
                        "kind": "spec",
                        "path": str(external_root / "spec.md"),
                        "required": True,
                    },
                    {
                        "kind": "rtl_filelist",
                        "path": str(external_root / "rtl.f"),
                        "required": True,
                    },
                ],
                read=[str(external_root / "spec.md"), str(external_root / "rtl.f")],
                write=["verif"],
            )
            artifact_path = self.root / "verif" / "vplan.md"
            artifact_path.parent.mkdir()
            artifact_path.write_text("# plan\n", encoding="utf-8")
            self.record(
                request,
                outcome="READY_FOR_REVIEW",
                artifacts=[self.artifact(artifact_path, self.root, "vplan")],
            )
            revision = self.state()["tasks"]["plan-build-001"]["output_revision"]

            # External RTL drift is untracked: a review still records cleanly.
            (external_root / "rtl" / "top.sv").write_text(
                "module ext_top; wire x; endmodule\n", encoding="utf-8"
            )
            review = self.new_task(
                "drift-ok-001",
                "reviewer",
                "REVIEW_VPLAN",
                lineage="drift-ok",
                retry_kind="review",
                phase="PLAN",
                input_revision=revision,
                parent="plan-build-001",
                inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
                read=["verif/vplan.md"],
                write=[],
            )
            self.record(review, outcome="APPROVED")

            # Drift of the tracked verification asset must be rejected at seal.
            (self.root / "verif" / "vplan.md").write_text(
                "# drifted plan\n", encoding="utf-8"
            )
            drift_review = self.new_task(
                "drift-bad-001",
                "reviewer",
                "REVIEW_VPLAN",
                lineage="drift-bad",
                retry_kind="review",
                phase="PLAN",
                input_revision=revision,
                parent="plan-build-001",
                inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
                read=["verif/vplan.md"],
                write=[],
                seal_expected=2,
            )
            self.assertEqual(
                "DRAFT", self.state()["tasks"][drift_review["task_id"]]["status"]
            )
        finally:
            external.cleanup()

    def test_builder_write_scope_cannot_reach_external_spec(self) -> None:
        external = tempfile.TemporaryDirectory()
        external_root = Path(external.name)
        try:
            (external_root / "spec.md").write_text("# spec\n", encoding="utf-8")
            self.enter_plan()
            baseline = self.state()["baseline_revision"]
            request = self.new_task(
                "external-write-001",
                "builder",
                "WRITE_VPLAN",
                lineage="external-write",
                retry_kind="dispatch",
                phase="PLAN",
                input_revision=baseline,
                inputs=[
                    {
                        "kind": "spec",
                        "path": str(external_root / "spec.md"),
                        "required": True,
                    }
                ],
                read=[str(external_root / "spec.md")],
                write=[str(external_root / "spec.md")],
                seal_expected=2,
            )
            self.assertEqual("DRAFT", self.state()["tasks"][request["task_id"]]["status"])
        finally:
            external.cleanup()

    def test_read_scope_and_inputs_may_be_external(self) -> None:
        external = tempfile.TemporaryDirectory()
        external_root = Path(external.name)
        try:
            (external_root / "spec.md").write_text("# spec\n", encoding="utf-8")
            self.enter_plan()
            baseline = self.state()["baseline_revision"]
            # An external required input inside an external read scope seals.
            request = self.new_task(
                "external-read-001",
                "reviewer",
                "REVIEW_VPLAN",
                lineage="external-read",
                retry_kind="review",
                phase="PLAN",
                input_revision=baseline,
                inputs=[
                    {
                        "kind": "spec",
                        "path": str(external_root / "spec.md"),
                        "required": True,
                    }
                ],
                read=[str(external_root / "spec.md")],
                write=[],
            )
            self.assertEqual("READY", self.state()["tasks"][request["task_id"]]["status"])

            # A missing external required input is still rejected.
            missing = str(external_root / "missing.md")
            missing_request = self.new_task(
                "external-missing-001",
                "reviewer",
                "REVIEW_VPLAN",
                lineage="external-missing",
                retry_kind="review",
                phase="PLAN",
                input_revision=baseline,
                inputs=[{"kind": "spec", "path": missing, "required": True}],
                read=[missing],
                write=[],
                seal_expected=2,
            )
            self.assertEqual(
                "DRAFT", self.state()["tasks"][missing_request["task_id"]]["status"]
            )
        finally:
            external.cleanup()

    def test_empty_snapshot_manifest(self) -> None:
        api = runpy.run_path(str(FLOW))
        revision, paths, digests, files = api["snapshot_manifest"](self.root, [])
        self.assertEqual(api["EMPTY_SNAPSHOT_REVISION"], revision)
        self.assertEqual("sha256:" + hashlib.sha256().hexdigest(), revision)
        self.assertEqual([], paths)
        self.assertEqual({}, digests)
        self.assertEqual({}, files)

    def test_current_revision_short_circuits_unchanged_files(self) -> None:
        api = runpy.run_path(str(FLOW))
        snapshot_manifest = api["snapshot_manifest"]
        current_revision = api["current_revision"]

        verif = self.root / "verif"
        verif.mkdir(exist_ok=True)
        (verif / "a.sv").write_text("module a;\n", encoding="utf-8")
        (verif / "b.sv").write_text("module b;\n", encoding="utf-8")
        revision, paths, digests, files = snapshot_manifest(self.root, ["verif"])
        snapshot = {"paths": paths, "digests": digests, "files": files}

        # Unchanged files reuse their cached digests and reproduce the revision.
        self.assertEqual((revision, digests), current_revision(self.root, snapshot))

        # A normal content change (size differs) is re-read and detected.
        (verif / "a.sv").write_text("module a; // edited\n", encoding="utf-8")
        changed_revision, _ = current_revision(self.root, snapshot)
        self.assertNotEqual(revision, changed_revision)

        # Deleting a tracked file also changes the revision.
        (verif / "b.sv").unlink()
        deleted_revision, _ = current_revision(self.root, snapshot)
        self.assertNotEqual(changed_revision, deleted_revision)


if __name__ == "__main__":
    unittest.main()
