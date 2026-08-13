from __future__ import annotations

import hashlib
import json
import runpy
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
            "--design-name",
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
                    "priority_order": ["P1", "P2", "P3"],
                    "items": [
                        {
                            "id": "VP-T001",
                            "kind": "TEST",
                            "priority": "P1",
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

        debugger = self.new_task(
            "malformed-debug-001",
            "debugger",
            "DIAGNOSE_FAILURE",
            lineage="malformed-debug",
            retry_kind="debug-evidence",
            phase="PLAN",
            input_revision=baseline,
            inputs=[{"kind": "spec", "path": "spec.md", "required": True}],
            read=["spec.md"],
            write=[],
        )
        debugger_result = {
            "schema_version": "dv-result/1.0",
            "task_id": debugger["task_id"],
            "run_id": debugger["run_id"],
            "role": "debugger",
            "action": "DIAGNOSE_FAILURE",
            "attempt": debugger["attempt"],
            "agent_status": "COMPLETED",
            "outcome": "DIAGNOSED",
            "input_revision": baseline,
            "summary": "Malformed classification type test.",
            "artifacts": [],
            "evidence": [
                {
                    "id": "DBG-EVID-001",
                    "path": "spec.md",
                    "line_or_time": "1",
                    "observation": "The bounded source was inspected.",
                }
            ],
            "issues": [],
            "payload": {
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
                    "instructions": [],
                    "candidate_files": [],
                    "must_preserve": [],
                },
                "rerun": {
                    "test": "smoke_test",
                    "seed": 1,
                    "extra_diagnostics": [],
                },
            },
            "recommended_next": None,
        }
        debugger_result_path = self.root / debugger["expected_result_path"]
        debugger_result_path.write_text(
            json.dumps(debugger_result) + "\n", encoding="utf-8"
        )
        failed = self.run_cli(
            "record-result",
            "--root",
            str(self.root),
            "--task-id",
            debugger["task_id"],
            expected=2,
        )
        self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(
            "READY", self.state()["tasks"][debugger["task_id"]]["status"]
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
            "p1-build-001",
            "builder",
            "IMPLEMENT_FEATURE_BATCH",
            lineage="p1-build",
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
        feature_revision = self.state()["tasks"]["p1-build-001"]["output_revision"]
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "BUILDING", "--last-task-id", "p1-build-001",
            "--reason", "P1 implementation started.",
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "AWAITING_REVIEW", "--last-task-id", "p1-build-001",
            "--reason", "P1 artifacts are ready for static review.",
        )
        feature_review = self.new_task(
            "p1-review-001",
            "reviewer",
            "REVIEW_TB",
            lineage="p1-review",
            retry_kind="review",
            phase="FEATURES",
            input_revision=feature_revision,
            parent="p1-build-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["spec.md", "rtl.f", "verif"],
            write=[],
            context={"work_item_ids": ["VP-T001"]},
        )
        self.record(feature_review, outcome="APPROVED")
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "READY_TO_RUN", "--last-task-id", "p1-review-001",
            "--reason", "P1 static review approved this revision.",
        )
        targeted = self.new_task(
            "p1-run-001",
            "runner",
            "RUN_CASE",
            lineage="p1-run",
            retry_kind="environment",
            phase="FEATURES",
            input_revision=feature_revision,
            parent="p1-review-001",
            inputs=[{"kind": "vplan", "path": "verif/vplan.md", "required": True}],
            read=["verif"],
            write=[(run_root / "p1-run-001").as_posix()],
            context={
                "work_item_ids": ["VP-T001"],
                "test_ids": ["VP-T001"],
                "seeds": [17],
            },
        )
        self.run_cli(
            "set-item", "--root", str(self.root), "--item-id", "VP-T001",
            "--status", "RUNNING", "--last-task-id", "p1-run-001",
            "--reason", "The exact targeted test is running.",
        )
        targeted_log = self.root / run_root / "p1-run-001" / "run.log"
        targeted_log.parent.mkdir(parents=True)
        targeted_log.write_text("P1 PASS\n", encoding="utf-8")
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
            "p1-run-001",
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
            parent="p1-run-001",
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


if __name__ == "__main__":
    unittest.main()
