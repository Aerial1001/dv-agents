#!/usr/bin/env python3
"""Durable state and task ledger for the dv-agents verification workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FLOW_SCHEMA = "dv-flow/1.0"
TASK_SCHEMA = "dv-task/1.0"
RESULT_SCHEMA = "dv-result/1.0"

PHASES = {
    "INIT",
    "PREFLIGHT",
    "PLAN",
    "SMOKE",
    "FEATURES",
    "RANDOM",
    "COVERAGE",
    "REGRESSION",
    "SIGNOFF",
    "WAITING_HUMAN",
    "BLOCKED",
    "COMPLETE",
}

TRANSITIONS = {
    "INIT": {"PLAN", "BLOCKED"},
    "PLAN": {"PLAN", "PREFLIGHT", "BLOCKED", "WAITING_HUMAN"},
    "PREFLIGHT": {"SMOKE", "BLOCKED", "WAITING_HUMAN"},
    "SMOKE": {"SMOKE", "FEATURES", "BLOCKED", "WAITING_HUMAN"},
    "FEATURES": {
        "FEATURES",
        "RANDOM",
        "COVERAGE",
        "BLOCKED",
        "WAITING_HUMAN",
    },
    "RANDOM": {"RANDOM", "COVERAGE", "FEATURES", "BLOCKED", "WAITING_HUMAN"},
    "COVERAGE": {
        "COVERAGE",
        "FEATURES",
        "REGRESSION",
        "BLOCKED",
        "WAITING_HUMAN",
    },
    "REGRESSION": {
        "REGRESSION",
        "FEATURES",
        "COVERAGE",
        "SIGNOFF",
        "BLOCKED",
        "WAITING_HUMAN",
    },
    "SIGNOFF": {"SIGNOFF", "COMPLETE", "BLOCKED", "WAITING_HUMAN"},
    "WAITING_HUMAN": set(PHASES - {"INIT", "COMPLETE"}),
    "BLOCKED": set(PHASES - {"INIT", "COMPLETE"}),
    "COMPLETE": set(),
}

ROLE_ACTIONS = {
    "builder": {
        "WRITE_VPLAN",
        "BUILD_SMOKE_FOUNDATION",
        "IMPLEMENT_FEATURE_BATCH",
        "APPLY_REVIEW_FIX",
        "APPLY_DEBUG_FIX",
        "COVERAGE_CLOSURE",
    },
    "reviewer": {"REVIEW_VPLAN", "REVIEW_TB", "REVIEW_FIX", "SIGNOFF_AUDIT"},
    "runner": {
        "PREFLIGHT",
        "COMPILE_ELAB",
        "RUN_CASE",
        "RUN_REGRESSION",
        "MERGE_COVERAGE",
    },
    "debugger": {"DIAGNOSE_FAILURE", "REDIAGNOSE_WITH_EVIDENCE"},
}

ROLE_OUTCOMES = {
    "builder": {"READY_FOR_REVIEW", "NO_CHANGE", "BLOCKED"},
    "reviewer": {"APPROVED", "CHANGES_REQUIRED", "BLOCKED"},
    "runner": {
        "PASS",
        "ENVIRONMENT_ERROR",
        "COMPILE_ERROR",
        "ELABORATION_ERROR",
        "SIMULATION_FAILURE",
        "TIMEOUT",
        "COVERAGE_GAP",
        "BLOCKED",
    },
    "debugger": {"DIAGNOSED", "NEEDS_MORE_EVIDENCE", "BLOCKED"},
}

RETRY_LIMITS = {
    "dispatch": 3,
    "review": 3,
    "environment": 3,
    "tb-fix": 3,
    "debug-evidence": 2,
    "none": 1,
}

TASK_STATUSES = {"DRAFT", "READY", "COMPLETED", "BLOCKED", "FAILED"}
ITEM_STATUSES = {
    "PENDING",
    "BUILDING",
    "AWAITING_REVIEW",
    "CHANGES_REQUIRED",
    "READY_TO_RUN",
    "RUNNING",
    "DEBUGGING",
    "FIXING",
    "PASSED",
    "BLOCKED_DUT",
    "BLOCKED_SPEC",
    "WAIVED",
    "TERMINAL_FAILURE",
}

ITEM_TRANSITIONS = {
    None: {"PENDING"},
    "PENDING": {"BUILDING", "BLOCKED_DUT", "BLOCKED_SPEC", "WAIVED"},
    "BUILDING": {"AWAITING_REVIEW", "BLOCKED_SPEC", "TERMINAL_FAILURE"},
    "AWAITING_REVIEW": {"CHANGES_REQUIRED", "READY_TO_RUN", "BLOCKED_SPEC"},
    "CHANGES_REQUIRED": {"FIXING", "TERMINAL_FAILURE"},
    "READY_TO_RUN": {"RUNNING", "BLOCKED_DUT", "BLOCKED_SPEC"},
    "RUNNING": {"PASSED", "DEBUGGING", "BLOCKED_DUT", "TERMINAL_FAILURE"},
    "DEBUGGING": {"FIXING", "BLOCKED_DUT", "BLOCKED_SPEC", "TERMINAL_FAILURE"},
    "FIXING": {"AWAITING_REVIEW", "TERMINAL_FAILURE"},
    "BLOCKED_DUT": {"FIXING", "READY_TO_RUN", "WAIVED", "TERMINAL_FAILURE"},
    "BLOCKED_SPEC": {"BUILDING", "WAIVED", "TERMINAL_FAILURE"},
    "PASSED": set(),
    "WAIVED": set(),
    "TERMINAL_FAILURE": set(),
}

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class FlowError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def project_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def flow_dir(root: Path) -> Path:
    return root / ".dv"


def state_path(root: Path) -> Path:
    return flow_dir(root) / "workflow_state.json"


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise FlowError(f"output path escapes project root: {path}") from exc


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FlowError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FlowError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FlowError(f"expected a JSON object in {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def append_event(root: Path, event: dict[str, Any]) -> None:
    path = flow_dir(root) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_state(root: Path) -> dict[str, Any]:
    state = read_json(state_path(root))
    errors = validate_state(root, state, check_files=False)
    if errors:
        raise FlowError("invalid workflow state: " + "; ".join(errors))
    return state


def save_state(root: Path, state: dict[str, Any], kind: str, details: dict[str, Any]) -> None:
    state["state_revision"] += 1
    state["updated_at"] = now()
    event = {
        "schema_version": FLOW_SCHEMA,
        "event_seq": state["state_revision"],
        "timestamp": state["updated_at"],
        "run_id": state["run_id"],
        "kind": kind,
        "details": details,
    }
    state.setdefault("history", []).append(event)
    write_json_atomic(state_path(root), state)
    append_event(root, event)


def require_mutable_state(state: dict[str, Any]) -> None:
    if state.get("current_phase") == "COMPLETE":
        raise FlowError("completed workflow state is immutable")


def validate_state(root: Path, state: dict[str, Any], check_files: bool = True) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "state_revision",
        "run_id",
        "created_at",
        "updated_at",
        "design",
        "workflow_status",
        "current_phase",
        "phase_status",
        "priority_order",
        "retry_limits",
        "tasks",
        "work_items",
        "artifacts",
        "baseline_revision",
        "accepted_revision",
        "frozen_revision",
        "plan_inventory",
        "blockers",
        "fix_requests",
        "approvals",
        "history",
    }
    missing = sorted(required - set(state))
    if missing:
        errors.append("missing state keys: " + ", ".join(missing))
        return errors
    if state["schema_version"] != FLOW_SCHEMA:
        errors.append(f"unsupported schema_version: {state['schema_version']!r}")
    if not isinstance(state["state_revision"], int) or state["state_revision"] < 0:
        errors.append("state_revision must be a non-negative integer")
    if state["current_phase"] not in PHASES:
        errors.append(f"invalid current_phase: {state['current_phase']!r}")
    if not isinstance(state["tasks"], dict):
        errors.append("tasks must be an object")
        return errors
    for task_id, task in state["tasks"].items():
        if task_id != task.get("task_id"):
            errors.append(f"task key/id mismatch: {task_id}")
        if task.get("run_id") != state["run_id"]:
            errors.append(f"task {task_id} has a mismatched run_id")
        if task.get("status") not in TASK_STATUSES:
            errors.append(f"task {task_id} has invalid status")
        if task.get("role") not in ROLE_ACTIONS:
            errors.append(f"task {task_id} has invalid role")
        elif task.get("action") not in ROLE_ACTIONS[task["role"]]:
            errors.append(f"task {task_id} has invalid action for {task['role']}")
        if task.get("status") == "DRAFT" and task.get("request_sha256") is not None:
            errors.append(f"draft task {task_id} must not have a request hash")
        if task.get("status") != "DRAFT" and not task.get("request_sha256"):
            errors.append(f"sealed task {task_id} is missing its request hash")
        if not isinstance(task.get("protected_paths"), list):
            errors.append(f"task {task_id} protected_paths must be an array")
        if not isinstance(task.get("write_scope_before"), dict):
            errors.append(f"task {task_id} write_scope_before must be an object")
        if check_files:
            request = root / task.get("request_path", "")
            if not request.is_file():
                errors.append(f"task {task_id} request is missing: {request}")
            elif task.get("request_sha256") and artifact_digest(root, request) != task["request_sha256"]:
                errors.append(f"task {task_id} request changed after sealing")
            if task.get("status") in {"COMPLETED", "BLOCKED", "FAILED"}:
                result = root / task.get("result_path", "")
                if not result.is_file():
                    errors.append(f"task {task_id} result is missing: {result}")
                elif not task.get("result_sha256"):
                    errors.append(f"task {task_id} is missing its result hash")
                elif artifact_digest(root, result) != task["result_sha256"]:
                    errors.append(f"task {task_id} result changed after recording")
    if not isinstance(state["work_items"], dict):
        errors.append("work_items must be an object")
    else:
        work_item_fields = {
            "id", "kind", "priority", "mandatory", "status", "dependencies",
            "accepted_revision", "builder_task_id", "review_task_id", "run_task_id",
            "evidence_task_ids", "last_task_id", "reason", "updated_at",
        }
        for item_id, item in state["work_items"].items():
            if not isinstance(item, dict) or set(item) != work_item_fields:
                errors.append(f"work item {item_id} has invalid fields")
                continue
            if item.get("id") != item_id:
                errors.append(f"work item key/id mismatch: {item_id}")
            if item.get("status") not in ITEM_STATUSES:
                errors.append(f"work item {item_id} has invalid status")
            if not isinstance(item.get("mandatory"), bool):
                errors.append(f"work item {item_id} mandatory must be boolean")
            for key in ("dependencies", "evidence_task_ids"):
                value = item.get(key)
                if not isinstance(value, list) or not all(
                    isinstance(entry, str) for entry in value
                ):
                    errors.append(f"work item {item_id} {key} must be an array of IDs")
    if not isinstance(state["artifacts"], dict):
        errors.append("artifacts must be an object")
    else:
        for field in ("baseline_revision", "accepted_revision", "frozen_revision"):
            revision = state.get(field)
            if revision is not None and revision not in state["artifacts"]:
                errors.append(f"{field} is absent from the artifact ledger")
    if state["plan_inventory"] is not None:
        if not isinstance(state["plan_inventory"], dict):
            errors.append("plan_inventory must be an object or null")
        else:
            core = {
                key: state["plan_inventory"].get(key)
                for key in ("priority_order", "items", "random_campaigns", "coverage_items")
            }
            errors.extend(validate_plan_inventory(core))
    if state["current_phase"] == "COMPLETE":
        errors.extend(evaluate_phase_gate(root, state, "SIGNOFF", "COMPLETE"))
    return errors


def validate_request(root: Path, request: dict[str, Any], task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "task_id",
        "run_id",
        "role",
        "action",
        "phase",
        "attempt",
        "lineage_id",
        "retry_kind",
        "requested_by",
        "reply_to",
        "project_root",
        "input_revision",
        "revision_paths",
        "inputs",
        "scope",
        "acceptance",
        "context",
        "prior_result_refs",
        "expected_result_path",
    }
    missing = sorted(required - set(request))
    if missing:
        errors.append("missing request keys: " + ", ".join(missing))
        return errors
    extra = sorted(set(request) - required)
    if extra:
        errors.append("unexpected request keys: " + ", ".join(extra))
    for key in (
        "task_id",
        "run_id",
        "role",
        "action",
        "phase",
        "attempt",
        "lineage_id",
        "retry_kind",
        "input_revision",
    ):
        if request.get(key) != task.get(key):
            errors.append(f"request {key} does not match task ledger")
    if request["schema_version"] != TASK_SCHEMA:
        errors.append("unsupported task request schema")
    if request["requested_by"] != "main" or request["reply_to"] != "main":
        errors.append("tasks must be requested by and reply to main")
    if request["project_root"] != str(root):
        errors.append("project_root does not match the initialized workflow root")
    if request["expected_result_path"] != task["result_path"]:
        errors.append("expected_result_path does not match task ledger")
    if not isinstance(request["prior_result_refs"], list):
        errors.append("prior_result_refs must be an array")
    elif (
        not all(isinstance(value, str) and value for value in request["prior_result_refs"])
        or len(set(request["prior_result_refs"])) != len(request["prior_result_refs"])
    ):
        errors.append("prior_result_refs must contain unique non-empty paths")
    else:
        for value in request["prior_result_refs"]:
            path = resolve_path(root, value)
            try:
                relative_to_root(root, path)
            except FlowError:
                errors.append(f"prior_result_ref escapes project root: {value}")
                continue
            if not path.is_file():
                errors.append(f"prior_result_ref does not exist: {value}")
    if not isinstance(request["inputs"], list):
        errors.append("inputs must be an array")
    else:
        serialized_inputs = [
            json.dumps(value, sort_keys=True) for value in request["inputs"]
        ]
        if len(serialized_inputs) != len(set(serialized_inputs)):
            errors.append("inputs must be unique")
        for index, item in enumerate(request["inputs"]):
            if not isinstance(item, dict) or set(item) != {"kind", "path", "required"}:
                errors.append(f"inputs[{index}] must contain only kind, path, and required")
                continue
            if not isinstance(item["kind"], str) or not item["kind"]:
                errors.append(f"inputs[{index}].kind must be a non-empty string")
            if not isinstance(item["path"], str) or not item["path"]:
                errors.append(f"inputs[{index}].path must be a non-empty string")
                continue
            input_path = resolve_path(root, item["path"])
            try:
                relative_to_root(root, input_path)
            except FlowError:
                errors.append(f"inputs[{index}].path escapes project root")
                continue
            if not isinstance(item["required"], bool):
                errors.append(f"inputs[{index}].required must be boolean")
                continue
            if item["required"] and not input_path.exists():
                errors.append(f"required input does not exist: {item['path']}")
    scope = request.get("scope")
    write_scope: list[str] = []
    read_scope: list[str] = []
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
    else:
        allowed_scope_keys = {"read", "write", "feature_ids", "test_ids", "seeds", "files"}
        extra_scope_keys = sorted(set(scope) - allowed_scope_keys)
        if extra_scope_keys:
            errors.append("unexpected scope keys: " + ", ".join(extra_scope_keys))
        read_value = scope.get("read")
        write_value = scope.get("write")
        if not isinstance(read_value, list) or not all(isinstance(v, str) for v in read_value):
            errors.append("scope.read must be an array of paths")
        else:
            read_scope = read_value
            if len(read_scope) != len(set(read_scope)):
                errors.append("scope.read must contain unique paths")
            for value in read_scope:
                try:
                    relative_to_root(root, resolve_path(root, value))
                except FlowError:
                    errors.append(f"read scope escapes project root: {value}")
        if not isinstance(write_value, list) or not all(isinstance(v, str) for v in write_value):
            errors.append("scope.write must be an array of paths")
        else:
            write_scope = write_value
            if len(write_scope) != len(set(write_scope)):
                errors.append("scope.write must contain unique paths")
        for key in ("feature_ids", "test_ids"):
            if key in scope and (
                not isinstance(scope[key], list)
                or not all(
                    isinstance(value, str) and ID_RE.fullmatch(value)
                    for value in scope[key]
                )
                or len(scope[key]) != len(set(scope[key]))
            ):
                errors.append(f"scope.{key} must contain unique IDs")
        if "seeds" in scope and (
            not isinstance(scope["seeds"], list)
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in scope["seeds"]
            )
            or len(scope["seeds"]) != len(set(scope["seeds"]))
        ):
            errors.append("scope.seeds must contain unique non-negative integers")
        if "files" in scope and (
            not isinstance(scope["files"], list)
            or not all(isinstance(value, str) and value for value in scope["files"])
            or len(scope["files"]) != len(set(scope["files"]))
        ):
            errors.append("scope.files must contain unique paths")
        if request["role"] in {"reviewer", "debugger"} and write_scope:
            errors.append(f"{request['role']} tasks must have an empty write scope")
        elif request["role"] == "builder" and not write_scope:
            errors.append("builder tasks require an explicit write scope")
        elif request["role"] == "builder":
            protected = [
                resolve_path(root, value)
                for value in task.get("protected_paths", [])
                if isinstance(value, str)
            ]
            for value in write_scope:
                resolved = resolve_path(root, value)
                try:
                    relative_to_root(root, resolved)
                except FlowError:
                    errors.append(f"builder write scope escapes project root: {value}")
                    continue
                if resolved == root:
                    errors.append("builder write scope must not be the project root")
                if paths_overlap(resolved, flow_dir(root)):
                    errors.append(f"builder write scope must not include .dv: {value}")
                if any(paths_overlap(resolved, path) for path in protected):
                    errors.append(f"builder write scope overlaps a protected input: {value}")
        elif request["role"] == "runner":
            assigned = (flow_dir(root) / "runs" / task["run_id"] / task["task_id"]).resolve()
            resolved_writes = [resolve_path(root, value) for value in write_scope]
            if resolved_writes != [assigned]:
                errors.append(
                    "runner write scope must be exactly its isolated run directory: "
                    + relative_to_root(root, assigned)
                )
    resolved_reads = [resolve_path(root, value) for value in read_scope]
    if isinstance(request.get("inputs"), list):
        for index, item in enumerate(request["inputs"]):
            if not isinstance(item, dict) or not item.get("required") or not isinstance(item.get("path"), str):
                continue
            input_path = resolve_path(root, item["path"])
            if not any(
                input_path == allowed or allowed in input_path.parents
                for allowed in resolved_reads
            ):
                errors.append(f"required input is outside scope.read: inputs[{index}]")
    revision_paths = request.get("revision_paths")
    if not isinstance(revision_paths, list) or not revision_paths:
        errors.append("revision_paths must be a non-empty array")
    elif not all(isinstance(value, str) and value for value in revision_paths):
        errors.append("revision_paths must contain non-empty paths")
    elif len(set(revision_paths)) != len(revision_paths):
        errors.append("revision_paths must be unique")
    else:
        resolved_writes = [resolve_path(root, value) for value in write_scope]
        resolved_revisions: list[Path] = []
        for value in revision_paths:
            resolved = resolve_path(root, value)
            resolved_revisions.append(resolved)
            try:
                relative_to_root(root, resolved)
            except FlowError:
                errors.append(f"revision path escapes project root: {value}")
                continue
            may_be_created = request["role"] == "builder" and any(
                resolved == allowed
                or allowed in resolved.parents
                or resolved in allowed.parents
                for allowed in resolved_writes
            )
            if not resolved.exists() and not may_be_created:
                errors.append(f"revision path does not exist: {value}")
        if request["role"] == "builder":
            for write_root in resolved_writes:
                if not any(
                    revision == write_root or revision in write_root.parents
                    for revision in resolved_revisions
                ):
                    errors.append(
                        "each builder write scope must be covered by revision_paths: "
                        + relative_to_root(root, write_root)
                    )
    if not isinstance(request["acceptance"], list) or not request["acceptance"]:
        errors.append("acceptance must be a non-empty array")
    elif not all(isinstance(value, str) and value.strip() for value in request["acceptance"]):
        errors.append("every acceptance item must be a non-empty string")
    elif len(request["acceptance"]) != len(set(request["acceptance"])):
        errors.append("acceptance items must be unique")
    if not isinstance(request["context"], dict):
        errors.append("context must be an object")
    else:
        context = request["context"]
        allowed_context_keys = {
            "feature_ids", "test_ids", "finding_ids", "affected_ids",
            "work_item_ids", "coverage_ids", "failure_id",
            "random_campaign_id", "campaign_id", "command", "cwd", "tool",
            "tool_version", "simulator", "timeout_s", "seeds",
            "regression_scope", "case_manifest", "acceptance_markers",
            "extra_diagnostics", "parameters", "environment",
        }
        unexpected = sorted(set(context) - allowed_context_keys)
        if unexpected:
            errors.append("unexpected context keys: " + ", ".join(unexpected))
        for key in (
            "work_item_ids", "feature_ids", "test_ids", "finding_ids",
            "affected_ids", "coverage_ids", "seeds",
        ):
            if key not in context:
                continue
            value = context[key]
            expected_type = int if key == "seeds" else str
            if (
                not isinstance(value, list)
                or not all(
                    isinstance(item, expected_type)
                    and not (expected_type is int and isinstance(item, bool))
                    and not (expected_type is int and item < 0)
                    and not (
                        expected_type is str
                        and not ID_RE.fullmatch(item)
                    )
                    for item in value
                )
                or len(value) != len(set(value))
            ):
                errors.append(f"context.{key} must be an array of unique values")
        for key in ("failure_id", "random_campaign_id", "campaign_id"):
            value = context.get(key)
            if value is not None and (
                not isinstance(value, str) or not ID_RE.fullmatch(value)
            ):
                errors.append(f"context.{key} must be an ID or null")
        for key in ("command", "cwd", "tool", "tool_version", "simulator"):
            value = context.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                errors.append(f"context.{key} must be a non-empty string or null")
        for key in ("acceptance_markers", "extra_diagnostics"):
            if key in context and (
                not isinstance(context[key], list)
                or not all(isinstance(value, str) and value for value in context[key])
                or len(context[key]) != len(set(context[key]))
            ):
                errors.append(f"context.{key} must contain unique non-empty strings")
        for key in ("parameters", "environment"):
            if key in context and not isinstance(context[key], dict):
                errors.append(f"context.{key} must be an object")
        action = request["action"]
        if action == "RUN_CASE":
            tests = context.get("test_ids")
            seeds = context.get("seeds")
            if not isinstance(tests, list) or len(tests) != 1 or not isinstance(tests[0], str):
                errors.append("RUN_CASE context.test_ids must name exactly one test")
            if (
                not isinstance(seeds, list)
                or len(seeds) != 1
                or isinstance(seeds[0], bool)
                or not isinstance(seeds[0], int)
            ):
                errors.append("RUN_CASE context.seeds must contain exactly one integer seed")
        elif action == "RUN_REGRESSION":
            if context.get("regression_scope") not in {"CUMULATIVE", "RANDOM", "FROZEN"}:
                errors.append("RUN_REGRESSION requires a valid context.regression_scope")
            try:
                manifest_cases(context.get("case_manifest"))
            except FlowError as exc:
                errors.append(str(exc))
            if context.get("regression_scope") == "RANDOM" and not isinstance(
                context.get("campaign_id"), str
            ):
                errors.append("RANDOM regression requires context.campaign_id")
        elif action == "MERGE_COVERAGE":
            coverage_ids = context.get("coverage_ids")
            if not isinstance(coverage_ids, list) or not coverage_ids:
                errors.append("MERGE_COVERAGE requires non-empty context.coverage_ids")
        elif action in {"REVIEW_TB", "REVIEW_FIX"}:
            if not context_ids(context, "work_item_ids", "feature_ids", "test_ids"):
                errors.append(f"{action} context must name at least one reviewed item")
        if request["role"] == "runner":
            for key in ("command", "cwd", "tool"):
                if not isinstance(context.get(key), str) or not context[key]:
                    errors.append(f"runner context.{key} must be a non-empty string")
            timeout_s = context.get("timeout_s")
            if (
                isinstance(timeout_s, bool)
                or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s)
                or timeout_s <= 0
            ):
                errors.append("runner context.timeout_s must be a positive finite number")
    revision = request.get("input_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", revision):
        errors.append("every dispatched task requires a valid input_revision")
    return errors


def require_object_fields(
    value: Any,
    fields: set[str],
    location: str,
    errors: list[str],
    *,
    exact: bool = True,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return None
    missing = sorted(fields - set(value))
    if missing:
        errors.append(f"{location} is missing: " + ", ".join(missing))
    if exact:
        extra = sorted(set(value) - fields)
        if extra:
            errors.append(f"{location} has unexpected keys: " + ", ".join(extra))
    if missing:
        return None
    return value


def validate_builder_result(result: dict[str, Any], errors: list[str]) -> None:
    if result["agent_status"] != "COMPLETED":
        return
    if result["outcome"] == "NO_CHANGE":
        if result["input_revision"] is None:
            errors.append("builder NO_CHANGE requires an input revision")
        if result["artifacts"]:
            errors.append("builder NO_CHANGE must not declare changed artifacts")
        return
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return
    change_set = require_object_fields(
        payload.get("change_set"),
        {
            "kind",
            "files_created",
            "files_modified",
            "files_deleted",
            "implemented_ids",
            "resolved_issue_ids",
            "unresolved_spec_gaps",
            "self_checks",
        },
        "payload.change_set",
        errors,
    )
    if change_set is None:
        return
    expected_kind = {
        "WRITE_VPLAN": "vplan",
        "BUILD_SMOKE_FOUNDATION": "smoke_foundation",
        "IMPLEMENT_FEATURE_BATCH": "feature_batch",
        "APPLY_REVIEW_FIX": "review_fix",
        "APPLY_DEBUG_FIX": "debug_fix",
        "COVERAGE_CLOSURE": "coverage_closure",
    }[result["action"]]
    if change_set["kind"] != expected_kind:
        errors.append(
            f"payload.change_set.kind must be {expected_kind!r} for {result['action']}"
        )
    arrays_valid = True
    for key in (
        "files_created",
        "files_modified",
        "files_deleted",
        "implemented_ids",
        "resolved_issue_ids",
        "unresolved_spec_gaps",
        "self_checks",
    ):
        if not isinstance(change_set[key], list) or not all(
            isinstance(value, str) and value for value in change_set[key]
        ):
            errors.append(f"payload.change_set.{key} must contain non-empty strings")
            arrays_valid = False
        elif len(change_set[key]) != len(set(change_set[key])):
            errors.append(f"payload.change_set.{key} must contain unique values")
            arrays_valid = False
    if not arrays_valid:
        return
    declared = {
        item["path"]
        for item in result["artifacts"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    changed = set(change_set["files_created"]) | set(change_set["files_modified"])
    if declared != changed:
        errors.append("builder artifacts must exactly match created and modified files")
    if not change_set["self_checks"]:
        errors.append("builder READY_FOR_REVIEW requires at least one self-check")


def validate_issue_shapes(result: dict[str, Any], errors: list[str]) -> None:
    role = result.get("role")
    issues = result.get("issues")
    if not isinstance(issues, list):
        return
    simple = {
        "builder": ({"id", "severity", "summary", "paths", "related_ids"}, {"BLOCKER", "WARNING"}),
        "runner": ({"id", "severity", "summary", "paths", "related_ids"}, {"BLOCKER", "ERROR", "WARNING"}),
    }
    if role == "debugger":
        if issues:
            errors.append("debugger issues must be empty; use diagnosis payload and evidence")
        return
    for index, issue in enumerate(issues):
        location = f"issues[{index}]"
        if not isinstance(issue, dict):
            continue
        if role in simple:
            fields, severities = simple[role]
            if set(issue) != fields:
                errors.append(f"{location} has invalid {role} issue fields")
                continue
            if not isinstance(issue.get("severity"), str) or issue["severity"] not in severities:
                errors.append(f"{location}.severity is invalid")
            if not isinstance(issue.get("id"), str) or not ID_RE.fullmatch(issue["id"]):
                errors.append(f"{location}.id is invalid")
            if not isinstance(issue.get("summary"), str) or not issue["summary"].strip():
                errors.append(f"{location}.summary must be non-empty")
            for key in ("paths", "related_ids"):
                if not isinstance(issue.get(key), list) or not all(
                    isinstance(value, str) for value in issue[key]
                ):
                    errors.append(f"{location}.{key} must be an array of strings")
        elif role == "reviewer":
            fields = {
                "id", "severity", "category", "path", "line", "related_ids",
                "evidence_ids", "impact", "required_change", "disposition",
            }
            if set(issue) != fields:
                errors.append(f"{location} has invalid reviewer issue fields")
                continue
            if not isinstance(issue.get("severity"), str) or issue["severity"] not in {
                "BLOCKER", "MAJOR", "MINOR", "NOTE"
            }:
                errors.append(f"{location}.severity is invalid")
            if not isinstance(issue.get("category"), str) or issue["category"] not in {
                "plan", "correctness", "uvm", "protocol", "checker",
                "assertion", "coverage", "build", "signoff",
            }:
                errors.append(f"{location}.category is invalid")
            if not isinstance(issue.get("disposition"), str) or issue["disposition"] not in {
                "OPEN", "RESOLVED", "STILL_OPEN"
            }:
                errors.append(f"{location}.disposition is invalid")
            if not isinstance(issue.get("line"), int) or isinstance(issue.get("line"), bool) or issue["line"] < 0:
                errors.append(f"{location}.line must be a non-negative integer")
            for key in ("id", "path", "impact", "required_change"):
                if not isinstance(issue.get(key), str) or not issue[key].strip():
                    errors.append(f"{location}.{key} must be non-empty")
            for key in ("related_ids", "evidence_ids"):
                if not isinstance(issue.get(key), list) or not all(
                    isinstance(value, str) for value in issue[key]
                ):
                    errors.append(f"{location}.{key} must be an array of strings")


def validate_reviewer_result(result: dict[str, Any], errors: list[str]) -> None:
    if result["artifacts"]:
        errors.append("reviewer must not declare writable artifacts")
    if result["agent_status"] != "COMPLETED":
        return
    if not result["evidence"]:
        errors.append("completed reviewer result requires evidence")
    payload = require_object_fields(
        result["payload"],
        {"reviewed_revision", "gate", "prior_findings", "plan_inventory", "signoff_audit"},
        "payload",
        errors,
    )
    if payload is None:
        return
    if payload["reviewed_revision"] != result["input_revision"]:
        errors.append("reviewed_revision must equal input_revision")
    gate = require_object_fields(
        payload["gate"],
        {"blocking_count", "major_count", "minor_count", "note_count"},
        "payload.gate",
        errors,
    )
    if gate is not None:
        for key, value in gate.items():
            if not isinstance(value, int) or value < 0:
                errors.append(f"payload.gate.{key} must be a non-negative integer")
        blocking = gate.get("blocking_count")
        if result["outcome"] == "APPROVED" and blocking != 0:
            errors.append("reviewer APPROVED requires blocking_count 0")
        if result["outcome"] == "CHANGES_REQUIRED" and (
            not isinstance(blocking, int) or blocking < 1
        ):
            errors.append("CHANGES_REQUIRED requires at least one blocking finding")
    if not isinstance(payload["prior_findings"], list):
        errors.append("payload.prior_findings must be an array")
    else:
        seen_prior: set[str] = set()
        for index, finding in enumerate(payload["prior_findings"]):
            location = f"payload.prior_findings[{index}]"
            shaped = require_object_fields(
                finding, {"id", "disposition"}, location, errors
            )
            if shaped is None:
                continue
            identifier = shaped["id"]
            if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
                errors.append(f"{location}.id is invalid")
            elif identifier in seen_prior:
                errors.append(f"{location}.id is duplicated")
            else:
                seen_prior.add(identifier)
            if not isinstance(shaped["disposition"], str) or shaped["disposition"] not in {
                "RESOLVED", "STILL_OPEN"
            }:
                errors.append(f"{location}.disposition is invalid")
    severities = [
        issue.get("severity")
        for issue in result["issues"]
        if isinstance(issue, dict)
    ]
    calculated_blocking = sum(
        isinstance(issue, dict)
        and issue.get("severity") in {"BLOCKER", "MAJOR"}
        and issue.get("disposition") in {"OPEN", "STILL_OPEN"}
        for issue in result["issues"]
    )
    if gate is not None and gate.get("blocking_count") != calculated_blocking:
        errors.append("payload.gate.blocking_count does not match blocking issues")
    if result["action"] == "REVIEW_VPLAN" and result["outcome"] == "APPROVED":
        inventory = require_object_fields(
            payload["plan_inventory"],
            {"priority_order", "items", "random_campaigns", "coverage_items"},
            "payload.plan_inventory",
            errors,
        )
        if inventory is not None:
            if not isinstance(inventory["priority_order"], list) or not inventory["priority_order"]:
                errors.append("plan inventory requires a non-empty priority_order")
            for key in ("items", "random_campaigns", "coverage_items"):
                if not isinstance(inventory[key], list):
                    errors.append(f"payload.plan_inventory.{key} must be an array")
    elif payload["plan_inventory"] is not None:
        errors.append("plan_inventory is only valid for approved REVIEW_VPLAN")
    if result["action"] == "SIGNOFF_AUDIT":
        audit = require_object_fields(
            payload["signoff_audit"],
            {
                "revision_consistent",
                "mandatory_items_total",
                "mandatory_items_passed",
                "random_seeds_planned",
                "random_seeds_completed",
                "coverage_targets_met",
                "open_blockers",
                "open_fix_requests",
                "waivers",
                "evidence_refs",
            },
            "payload.signoff_audit",
            errors,
        )
        if audit is not None and result["outcome"] == "APPROVED":
            if audit["revision_consistent"] is not True:
                errors.append("approved signoff audit requires revision consistency")
            if audit["mandatory_items_total"] != audit["mandatory_items_passed"]:
                errors.append("approved signoff audit requires all mandatory items passed")
            if audit["random_seeds_planned"] != audit["random_seeds_completed"]:
                errors.append("approved signoff audit requires planned random seeds completed")
            if audit["coverage_targets_met"] is not True:
                errors.append("approved signoff audit requires coverage targets met")
            if audit["open_blockers"] or audit["open_fix_requests"]:
                errors.append("approved signoff audit cannot have open blockers or fix requests")
    elif payload["signoff_audit"] is not None:
        errors.append("signoff_audit is only valid for SIGNOFF_AUDIT")


def validate_runner_result(result: dict[str, Any], errors: list[str]) -> None:
    if result["agent_status"] != "COMPLETED":
        return
    if not result["artifacts"]:
        errors.append("completed runner result requires run artifacts")
    if not result["evidence"]:
        errors.append("completed runner result requires evidence")
    payload = require_object_fields(
        result["payload"],
        {
            "tested_revision",
            "run",
            "counts",
            "environment_actions",
            "failure",
            "case_results",
            "coverage_summary",
        },
        "payload",
        errors,
    )
    if payload is None:
        return
    if payload["tested_revision"] != result["input_revision"]:
        errors.append("tested_revision must equal input_revision")
    run = require_object_fields(
        payload["run"],
        {
            "phase",
            "test",
            "seed",
            "command",
            "cwd",
            "tool",
            "tool_version",
            "exit_code",
            "duration_s",
        },
        "payload.run",
        errors,
    )
    counts = require_object_fields(
        payload["counts"],
        {"uvm_fatal", "uvm_error", "assertion_failures", "scoreboard_mismatches"},
        "payload.counts",
        errors,
    )
    failure = require_object_fields(
        payload["failure"],
        {"signature", "first_time", "log_excerpt_ref"},
        "payload.failure",
        errors,
    )
    if not isinstance(payload["environment_actions"], list):
        errors.append("payload.environment_actions must be an array")
    elif not all(
        isinstance(value, str) and value for value in payload["environment_actions"]
    ):
        errors.append("payload.environment_actions must contain non-empty strings")
    case_results = payload["case_results"]
    if not isinstance(case_results, list):
        errors.append("payload.case_results must be an array")
        case_results = []
    else:
        for index, case in enumerate(case_results):
            if not isinstance(case, dict):
                errors.append(f"payload.case_results[{index}] must be an object")
                continue
            if set(case) != {"test", "seed", "outcome"}:
                errors.append(
                    f"payload.case_results[{index}] must contain only test, seed, and outcome"
                )
                continue
            if not isinstance(case["test"], str) or not case["test"]:
                errors.append(f"payload.case_results[{index}].test must be non-empty")
            if (
                case["seed"] is not None
                and (
                    isinstance(case["seed"], bool)
                    or not isinstance(case["seed"], int)
                    or case["seed"] < 0
                )
            ):
                errors.append(
                    f"payload.case_results[{index}].seed must be a non-negative integer or null"
                )
            if not isinstance(case["outcome"], str) or case["outcome"] not in {
                "PASS", "ENVIRONMENT_ERROR", "SIMULATION_FAILURE", "TIMEOUT", "BLOCKED"
            }:
                errors.append(f"payload.case_results[{index}].outcome is invalid")
    if run is not None:
        if not isinstance(run["phase"], str) or run["phase"] not in {
            "PREFLIGHT", "COMPILE", "ELABORATION", "SIMULATION",
            "REGRESSION", "COVERAGE_MERGE",
        }:
            errors.append("payload.run.phase is invalid")
        for key in ("command", "cwd", "tool", "tool_version"):
            if not isinstance(run[key], str) or not run[key]:
                errors.append(f"payload.run.{key} must be non-empty")
        if run["test"] is not None and (
            not isinstance(run["test"], str) or not run["test"]
        ):
            errors.append("payload.run.test must be a non-empty string or null")
        if run["seed"] is not None and (
            isinstance(run["seed"], bool) or not isinstance(run["seed"], int)
            or run["seed"] < 0
        ):
            errors.append("payload.run.seed must be a non-negative integer or null")
        if run["exit_code"] is not None and (
            isinstance(run["exit_code"], bool) or not isinstance(run["exit_code"], int)
        ):
            errors.append("payload.run.exit_code must be an integer or null")
        if (
            isinstance(run["duration_s"], bool)
            or not isinstance(run["duration_s"], (int, float))
            or not math.isfinite(run["duration_s"])
            or run["duration_s"] < 0
        ):
            errors.append("payload.run.duration_s must be a finite non-negative number")
    if counts is not None:
        for key, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"payload.counts.{key} must be a non-negative integer")
    if failure is not None:
        for key, value in failure.items():
            if value is not None and (not isinstance(value, str) or not value):
                errors.append(f"payload.failure.{key} must be a non-empty string or null")
    expected_phases = {
        "PREFLIGHT": {"PREFLIGHT"},
        "COMPILE_ELAB": {"COMPILE", "ELABORATION"},
        "RUN_CASE": {"SIMULATION"},
        "RUN_REGRESSION": {"REGRESSION"},
        "MERGE_COVERAGE": {"COVERAGE_MERGE"},
    }
    if run is not None and run.get("phase") not in expected_phases[result["action"]]:
        errors.append(f"payload.run.phase is invalid for {result['action']}")

    def validate_coverage(value: Any) -> dict[str, Any] | None:
        coverage = require_object_fields(
            value,
            {"targets_met", "metrics", "waiver_ids"},
            "payload.coverage_summary",
            errors,
        )
        if coverage is None:
            return None
        if not isinstance(coverage["targets_met"], bool):
            errors.append("payload.coverage_summary.targets_met must be boolean")
        metrics = coverage["metrics"]
        if not isinstance(metrics, list):
            errors.append("payload.coverage_summary.metrics must be an array")
        else:
            identifiers: set[str] = set()
            for index, metric in enumerate(metrics):
                location = f"payload.coverage_summary.metrics[{index}]"
                shaped = require_object_fields(
                    metric, {"id", "metric", "value", "target", "met"},
                    location, errors,
                )
                if shaped is None:
                    continue
                identifier = shaped["id"]
                if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
                    errors.append(f"{location}.id is invalid")
                elif identifier in identifiers:
                    errors.append(f"{location}.id is duplicated")
                else:
                    identifiers.add(identifier)
                if not isinstance(shaped["metric"], str) or not shaped["metric"]:
                    errors.append(f"{location}.metric must be non-empty")
                for key in ("value", "target"):
                    number = shaped[key]
                    if (
                        isinstance(number, bool)
                        or not isinstance(number, (int, float))
                        or not math.isfinite(number)
                        or number < 0
                    ):
                        errors.append(f"{location}.{key} must be finite and non-negative")
                if not isinstance(shaped["met"], bool):
                    errors.append(f"{location}.met must be boolean")
                elif all(
                    isinstance(shaped[key], (int, float))
                    and not isinstance(shaped[key], bool)
                    and math.isfinite(shaped[key])
                    for key in ("value", "target")
                ) and shaped["met"] != (shaped["value"] >= shaped["target"]):
                    errors.append(f"{location}.met disagrees with value and target")
        waiver_ids = coverage["waiver_ids"]
        if (
            not isinstance(waiver_ids, list)
            or not all(isinstance(value, str) and ID_RE.fullmatch(value) for value in waiver_ids)
            or len(waiver_ids) != len(set(waiver_ids))
        ):
            errors.append("payload.coverage_summary.waiver_ids must contain unique IDs")
        return coverage
    if result["outcome"] == "PASS":
        if result["action"] != "PREFLIGHT" and result["input_revision"] is None:
            errors.append("runner PASS after preflight requires an input revision")
        if run is not None and run["exit_code"] != 0:
            errors.append("runner PASS requires exit_code 0")
        if failure is not None and any(value is not None for value in failure.values()):
            errors.append("runner PASS requires an empty failure record")
        if counts is not None and any(value != 0 for value in counts.values()):
            errors.append("runner PASS requires zero failure counts")
        if result["action"] == "COMPILE_ELAB" and run is not None:
            if run.get("phase") != "ELABORATION":
                errors.append("COMPILE_ELAB PASS requires phase ELABORATION")
        if result["action"] == "RUN_REGRESSION":
            if not case_results:
                errors.append("passing regression requires case_results")
            elif any(
                not isinstance(case, dict) or case.get("outcome") != "PASS"
                for case in case_results
            ):
                errors.append("passing regression requires every case result to PASS")
        if result["action"] == "MERGE_COVERAGE":
            coverage = validate_coverage(payload["coverage_summary"])
            if coverage is not None and coverage["targets_met"] is not True:
                errors.append("passing coverage merge requires targets_met true")
            if not any(
                isinstance(item, dict) and item.get("kind") == "coverage"
                for item in result["artifacts"]
            ):
                errors.append("passing coverage merge requires a coverage artifact")
    if result["outcome"] == "COVERAGE_GAP":
        if result["action"] != "MERGE_COVERAGE":
            errors.append("COVERAGE_GAP is only valid for MERGE_COVERAGE")
        coverage = validate_coverage(payload["coverage_summary"])
        if coverage is not None and coverage.get("targets_met") is not False:
            errors.append("COVERAGE_GAP requires targets_met false")
        if not any(
            isinstance(item, dict) and item.get("kind") == "coverage"
            for item in result["artifacts"]
        ):
            errors.append("COVERAGE_GAP requires a coverage artifact")
    if result["outcome"] in {
        "COMPILE_ERROR",
        "ELABORATION_ERROR",
        "SIMULATION_FAILURE",
        "TIMEOUT",
        "ENVIRONMENT_ERROR",
    } and failure is not None:
        if not isinstance(failure["signature"], str) or not failure["signature"]:
            errors.append(f"{result['outcome']} requires a failure signature")
    if result["action"] != "MERGE_COVERAGE" and payload["coverage_summary"] is not None:
        errors.append("coverage_summary is only valid for MERGE_COVERAGE")


def validate_debugger_result(result: dict[str, Any], errors: list[str]) -> None:
    if result["artifacts"]:
        errors.append("debugger must not declare writable artifacts")
    if result["agent_status"] != "COMPLETED":
        return
    if not result["evidence"]:
        errors.append("completed debugger result requires evidence")
    payload = require_object_fields(
        result["payload"],
        {
            "classification",
            "subtype",
            "confidence",
            "expected",
            "observed",
            "root_cause",
            "suspected_locations",
            "affected_ids",
            "route_to",
            "fix_request",
            "rerun",
        },
        "payload",
        errors,
    )
    if payload is None:
        return
    classifications = {
        "TB_BUG": "BUILDER",
        "TEST_BUG": "BUILDER",
        "DUT_BUG": "RTL_OWNER",
        "SPEC_GAP": "HUMAN",
        "ENVIRONMENT": "RUNNER",
        "TOOLCHAIN": "RUNNER",
        "UNKNOWN": None,
    }
    classification = payload["classification"]
    if not isinstance(classification, str) or classification not in classifications:
        errors.append("invalid debugger classification")
    elif classifications[classification] and payload["route_to"] != classifications[classification]:
        errors.append("debugger classification and route_to disagree")
    elif classification == "UNKNOWN" and payload["route_to"] not in {"RUNNER", "HUMAN"}:
        errors.append("UNKNOWN diagnosis must route to RUNNER or HUMAN")
    if not isinstance(payload["confidence"], str) or payload["confidence"] not in {
        "HIGH", "MEDIUM", "LOW"
    }:
        errors.append("invalid debugger confidence")
    if classification == "DUT_BUG" and payload["confidence"] == "LOW":
        errors.append("low-confidence evidence cannot confirm a DUT bug")
    for key in ("expected", "observed"):
        if not isinstance(payload[key], str) or not payload[key]:
            errors.append(f"payload.{key} must be a non-empty string")
    for key in ("subtype", "root_cause"):
        if payload[key] is not None and (
            not isinstance(payload[key], str) or not payload[key]
        ):
            errors.append(f"payload.{key} must be a non-empty string or null")
    if not isinstance(payload["route_to"], str) or payload["route_to"] not in {
        "BUILDER", "RUNNER", "RTL_OWNER", "HUMAN"
    }:
        errors.append("payload.route_to is invalid")
    locations = payload["suspected_locations"]
    if not isinstance(locations, list):
        errors.append("payload.suspected_locations must be an array")
    else:
        for index, location in enumerate(locations):
            shaped = require_object_fields(
                location, {"path", "line", "module", "signal"},
                f"payload.suspected_locations[{index}]", errors,
            )
            if shaped is None:
                continue
            if not isinstance(shaped["path"], str) or not shaped["path"]:
                errors.append(f"payload.suspected_locations[{index}].path must be non-empty")
            if (
                isinstance(shaped["line"], bool)
                or not isinstance(shaped["line"], int)
                or shaped["line"] < 0
            ):
                errors.append(f"payload.suspected_locations[{index}].line is invalid")
            for key in ("module", "signal"):
                if shaped[key] is not None and (
                    not isinstance(shaped[key], str) or not shaped[key]
                ):
                    errors.append(
                        f"payload.suspected_locations[{index}].{key} must be a string or null"
                    )
    affected_ids = payload["affected_ids"]
    if (
        not isinstance(affected_ids, list)
        or not all(isinstance(value, str) and ID_RE.fullmatch(value) for value in affected_ids)
        or len(affected_ids) != len(set(affected_ids))
    ):
        errors.append("payload.affected_ids must contain unique IDs")
    fix_request = require_object_fields(
        payload["fix_request"],
        {"instructions", "candidate_files", "must_preserve"},
        "payload.fix_request",
        errors,
    )
    if fix_request is not None:
        if not isinstance(fix_request["instructions"], str) or not fix_request["instructions"]:
            errors.append("payload.fix_request.instructions must be non-empty")
        for key in ("candidate_files", "must_preserve"):
            if not isinstance(fix_request[key], list) or not all(
                isinstance(value, str) and value for value in fix_request[key]
            ):
                errors.append(f"payload.fix_request.{key} must contain non-empty strings")
            elif len(fix_request[key]) != len(set(fix_request[key])):
                errors.append(f"payload.fix_request.{key} must contain unique values")
    rerun = require_object_fields(
        payload["rerun"],
        {"test", "seed", "extra_diagnostics"},
        "payload.rerun",
        errors,
    )
    if rerun is not None:
        if rerun["test"] is not None and (
            not isinstance(rerun["test"], str) or not rerun["test"]
        ):
            errors.append("payload.rerun.test must be a string or null")
        if rerun["seed"] is not None and (
            isinstance(rerun["seed"], bool)
            or not isinstance(rerun["seed"], int)
            or rerun["seed"] < 0
        ):
            errors.append("payload.rerun.seed must be a non-negative integer or null")
        if not isinstance(rerun["extra_diagnostics"], list) or not all(
            isinstance(value, str) and value for value in rerun["extra_diagnostics"]
        ):
            errors.append("payload.rerun.extra_diagnostics must contain non-empty strings")
    if result["outcome"] == "NEEDS_MORE_EVIDENCE":
        rerun = payload["rerun"]
        if not isinstance(rerun, dict) or not rerun.get("extra_diagnostics"):
            errors.append("NEEDS_MORE_EVIDENCE requires bounded extra diagnostics")


def validate_result(root: Path, result: dict[str, Any], task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "task_id",
        "run_id",
        "role",
        "action",
        "attempt",
        "agent_status",
        "outcome",
        "input_revision",
        "summary",
        "artifacts",
        "evidence",
        "issues",
        "payload",
        "recommended_next",
    }
    missing = sorted(required - set(result))
    if missing:
        errors.append("missing result keys: " + ", ".join(missing))
        return errors
    extra = sorted(set(result) - required)
    if extra:
        errors.append("unexpected result keys: " + ", ".join(extra))
    if result["schema_version"] != RESULT_SCHEMA:
        errors.append("unsupported task result schema")
    for key in ("task_id", "run_id", "role", "action", "attempt", "input_revision"):
        if result.get(key) != task.get(key):
            errors.append(f"result {key} does not match task ledger")
    agent_status = result.get("agent_status")
    outcome = result.get("outcome")
    if not isinstance(agent_status, str) or agent_status not in {"COMPLETED", "BLOCKED", "FAILED"}:
        errors.append("invalid agent_status")
    allowed = ROLE_OUTCOMES.get(task["role"], set())
    if agent_status == "FAILED":
        if outcome != "INTERNAL_ERROR":
            errors.append("FAILED agent_status requires INTERNAL_ERROR outcome")
    elif agent_status == "BLOCKED":
        if outcome != "BLOCKED":
            errors.append("BLOCKED agent_status requires BLOCKED outcome")
    elif agent_status == "COMPLETED" and (not isinstance(outcome, str) or outcome not in allowed):
        errors.append(f"invalid outcome for {task['role']}: {outcome!r}")
    elif outcome == "BLOCKED":
        errors.append("BLOCKED outcome requires BLOCKED agent_status")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        errors.append("summary must be a non-empty string")
    elif len(result["summary"]) > 1000:
        errors.append("summary must not exceed 1000 characters")
    for key in ("artifacts", "evidence", "issues"):
        if not isinstance(result[key], list):
            errors.append(f"{key} must be an array")
        elif not all(isinstance(value, dict) for value in result[key]):
            errors.append(f"{key} must contain objects")
    if isinstance(result.get("evidence"), list):
        evidence_ids: set[str] = set()
        for index, item in enumerate(result["evidence"]):
            if not isinstance(item, dict):
                continue
            if set(item) != {"id", "path", "line_or_time", "observation"}:
                errors.append(
                    f"evidence[{index}] must contain only id, path, line_or_time, and observation"
                )
                continue
            if not isinstance(item["id"], str) or not ID_RE.fullmatch(item["id"]):
                errors.append(f"evidence[{index}].id is invalid")
            elif item["id"] in evidence_ids:
                errors.append(f"duplicate evidence id: {item['id']}")
            else:
                evidence_ids.add(item["id"])
            if not all(
                isinstance(item[key], str) and item[key].strip()
                for key in ("path", "line_or_time", "observation")
            ):
                errors.append(f"evidence[{index}] text fields must be non-empty strings")
    if not isinstance(result["payload"], dict):
        errors.append("payload must be an object")
    if result["recommended_next"] is not None and not isinstance(result["recommended_next"], dict):
        errors.append("recommended_next must be an object or null")
    elif isinstance(result["recommended_next"], dict):
        recommendation = result["recommended_next"]
        if set(recommendation) != {"role", "action", "reason"}:
            errors.append("recommended_next must contain only role, action, and reason")
        else:
            role = recommendation["role"]
            action = recommendation["action"]
            if role is None or action is None:
                if role is not None or action is not None:
                    errors.append("recommended_next role and action must both be null")
            elif (
                not isinstance(role, str)
                or not isinstance(action, str)
                or role not in ROLE_ACTIONS
                or action not in ROLE_ACTIONS[role]
            ):
                errors.append("recommended_next has an invalid role/action pair")
            if not isinstance(recommendation["reason"], str):
                errors.append("recommended_next.reason must be a string")
    if isinstance(result.get("artifacts"), list):
        for index, artifact in enumerate(result["artifacts"]):
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
                errors.append(f"artifacts[{index}] must contain a path")
                continue
            if set(artifact) != {"kind", "path", "sha256"}:
                errors.append(f"artifacts[{index}] must contain only kind, path, and sha256")
            if not isinstance(artifact.get("kind"), str) or not artifact["kind"]:
                errors.append(f"artifacts[{index}].kind must be a non-empty string")
            path = resolve_path(root, artifact["path"])
            try:
                relative_to_root(root, path)
            except FlowError as exc:
                errors.append(str(exc))
                continue
            if not path.exists():
                errors.append(f"declared artifact does not exist: {artifact['path']}")
                continue
            if not path.is_file():
                errors.append(f"declared artifact must be a file: {artifact['path']}")
                continue
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                errors.append(f"artifacts[{index}] must contain a lowercase sha256 digest")
            elif digest != artifact_digest(root, path):
                errors.append(f"artifact digest mismatch: {artifact['path']}")
    structure_valid = (
        isinstance(agent_status, str)
        and isinstance(outcome, str)
        and isinstance(result.get("artifacts"), list)
        and isinstance(result.get("evidence"), list)
        and isinstance(result.get("issues"), list)
        and isinstance(result.get("payload"), dict)
    )
    if (
        task["role"] == "builder"
        and agent_status == "COMPLETED"
        and outcome == "READY_FOR_REVIEW"
        and not result["artifacts"]
    ):
        payload = result.get("payload", {})
        change_set = payload.get("change_set", {}) if isinstance(payload, dict) else {}
        if not isinstance(change_set, dict) or not change_set.get("files_deleted"):
            errors.append("READY_FOR_REVIEW requires at least one changed or deleted artifact")
    if (
        task["role"] == "builder"
        and agent_status == "COMPLETED"
        and outcome == "READY_FOR_REVIEW"
        and not result["evidence"]
    ):
        errors.append("READY_FOR_REVIEW requires evidence")
    if structure_valid and task["role"] == "builder":
        validate_builder_result(result, errors)
    elif structure_valid and task["role"] == "reviewer":
        validate_reviewer_result(result, errors)
    elif structure_valid and task["role"] == "runner":
        validate_runner_result(result, errors)
    elif structure_valid and task["role"] == "debugger":
        validate_debugger_result(result, errors)
    if structure_valid:
        validate_issue_shapes(result, errors)
    parent_id = task.get("parent_task_id")
    if parent_id:
        # Parent consistency is checked by record_result, where state is available.
        pass
    return errors


def hash_path(hasher: Any, root: Path, path: Path) -> None:
    relative = relative_to_root(root, path)
    if path.is_file():
        content = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                content.update(chunk)
        record = json.dumps(
            {
                "kind": "file",
                "path": relative,
                "sha256": content.hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        hasher.update(len(record).to_bytes(8, "big"))
        hasher.update(record)
    elif path.is_dir():
        record = json.dumps(
            {"kind": "directory", "path": relative},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        hasher.update(len(record).to_bytes(8, "big"))
        hasher.update(record)
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            hash_path(hasher, root, child)
    else:
        raise FlowError(f"cannot hash missing artifact: {path}")


def canonical_snapshot_paths(root: Path, values: list[str]) -> list[Path]:
    candidates = sorted(
        {resolve_path(root, value) for value in values},
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    selected: list[Path] = []
    for path in candidates:
        relative_to_root(root, path)
        if not path.exists():
            raise FlowError(f"snapshot path does not exist: {path}")
        if any(parent.is_dir() and parent in path.parents for parent in selected):
            continue
        selected.append(path)
    return sorted(selected, key=lambda path: relative_to_root(root, path))


def snapshot_path_digest(root: Path, path: Path) -> str:
    hasher = hashlib.sha256()
    hash_path(hasher, root, path)
    return "sha256:" + hasher.hexdigest()


def snapshot_manifest(
    root: Path, values: list[str]
) -> tuple[str, list[str], dict[str, str]]:
    if not values:
        raise FlowError("snapshot requires at least one revision path")
    paths = canonical_snapshot_paths(root, values)
    digests = {
        relative_to_root(root, path): snapshot_path_digest(root, path)
        for path in paths
    }
    hasher = hashlib.sha256()
    for relative, digest in sorted(digests.items()):
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("ascii"))
        hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest(), list(digests), digests


def paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def artifact_digest(root: Path, path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def scoped_file_manifest(root: Path, values: list[str]) -> dict[str, str]:
    """Return content digests for every regular file in the declared roots."""
    manifest: dict[str, str] = {}
    for value in values:
        scope_root = resolve_path(root, value)
        relative_to_root(root, scope_root)
        if not scope_root.exists():
            continue
        candidates = [scope_root] if scope_root.is_file() else sorted(scope_root.rglob("*"))
        for path in candidates:
            if path.is_file():
                manifest[relative_to_root(root, path)] = artifact_digest(root, path)
    return dict(sorted(manifest.items()))


def normalize_result_paths(root: Path, values: Any, location: str) -> set[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise FlowError(f"{location} must be an array of paths")
    normalized = {relative_to_root(root, resolve_path(root, value)) for value in values}
    if len(normalized) != len(values):
        raise FlowError(f"{location} contains duplicate paths")
    return normalized


def checked_task_request(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    path = root / task["request_path"]
    if not path.is_file() or artifact_digest(root, path) != task.get("request_sha256"):
        raise FlowError(f"task request changed after sealing: {task['task_id']}")
    return read_json(path)


def checked_task_result(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    if task.get("status") != "COMPLETED":
        raise FlowError(f"task is not completed: {task.get('task_id')}")
    checked_task_request(root, task)
    path = root / task["result_path"]
    if not path.is_file() or artifact_digest(root, path) != task.get("result_sha256"):
        raise FlowError(f"task result changed after recording: {task['task_id']}")
    result = read_json(path)
    if result.get("output_revision") != task.get("output_revision"):
        raise FlowError(f"task result revision disagrees with ledger: {task['task_id']}")
    return result


def find_task_ancestor(
    state: dict[str, Any],
    task: dict[str, Any] | None,
    predicate: Any,
) -> dict[str, Any] | None:
    seen: set[str] = set()
    current = task
    while isinstance(current, dict):
        task_id = current.get("task_id")
        if not isinstance(task_id, str) or task_id in seen:
            return None
        seen.add(task_id)
        if predicate(current):
            return current
        parent_id = current.get("parent_task_id")
        current = state["tasks"].get(parent_id) if isinstance(parent_id, str) else None
    return None


def context_ids(context: Any, *keys: str) -> set[str]:
    identifiers: set[str] = set()
    if not isinstance(context, dict):
        return identifiers
    for key in keys:
        value = context.get(key, [])
        if isinstance(value, list):
            identifiers.update(item for item in value if isinstance(item, str))
    return identifiers


def verify_current_revision(root: Path, state: dict[str, Any], revision: str) -> None:
    snapshot = state.get("artifacts", {}).get(revision)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("paths"), list):
        raise FlowError(f"revision is absent from artifact ledger: {revision}")
    observed, _, digests = snapshot_manifest(root, snapshot["paths"])
    if observed != revision or digests != snapshot.get("digests"):
        raise FlowError(f"workspace no longer matches revision {revision}")


def completed_tasks(
    state: dict[str, Any], *, role: str | None = None, action: str | None = None,
    phase: str | None = None, outcome: str | None = None, revision: str | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for task in state["tasks"].values():
        if task.get("status") != "COMPLETED":
            continue
        if role is not None and task.get("role") != role:
            continue
        if action is not None and task.get("action") != action:
            continue
        if phase is not None and task.get("phase") != phase:
            continue
        if outcome is not None and task.get("outcome") != outcome:
            continue
        if revision is not None and task.get("input_revision") != revision:
            continue
        matches.append(task)
    return sorted(matches, key=lambda item: item.get("updated_at", ""), reverse=True)


def validate_plan_inventory(inventory: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(inventory, dict):
        return ["plan_inventory must be an object"]
    fields = {"priority_order", "items", "random_campaigns", "coverage_items"}
    missing = sorted(fields - set(inventory))
    extra = sorted(set(inventory) - fields)
    if missing:
        errors.append("plan_inventory is missing: " + ", ".join(missing))
    if extra:
        errors.append("plan_inventory has unexpected keys: " + ", ".join(extra))
    if missing:
        return errors
    priorities = inventory["priority_order"]
    if (
        not isinstance(priorities, list)
        or not priorities
        or not all(isinstance(value, str) and value for value in priorities)
        or len(set(priorities)) != len(priorities)
    ):
        errors.append("plan_inventory.priority_order must contain unique non-empty strings")
        priorities = []
    definitions = {
        "items": {"id", "kind", "priority", "dependencies", "mandatory"},
        "random_campaigns": {"id", "test", "seed_budget", "dependencies", "mandatory"},
        "coverage_items": {"id", "metric", "target", "dependencies", "mandatory"},
    }
    all_ids: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    for collection, expected in definitions.items():
        entries = inventory[collection]
        if not isinstance(entries, list):
            errors.append(f"plan_inventory.{collection} must be an array")
            continue
        for index, entry in enumerate(entries):
            location = f"plan_inventory.{collection}[{index}]"
            if not isinstance(entry, dict) or set(entry) != expected:
                errors.append(f"{location} must contain exactly {', '.join(sorted(expected))}")
                continue
            identifier = entry.get("id")
            if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
                errors.append(f"{location}.id is invalid")
                continue
            if identifier in all_ids:
                errors.append(f"duplicate plan inventory id: {identifier}")
            all_ids.add(identifier)
            deps = entry.get("dependencies")
            if (
                not isinstance(deps, list)
                or not all(isinstance(value, str) for value in deps)
                or len(set(deps)) != len(deps)
            ):
                errors.append(f"{location}.dependencies must contain unique IDs")
                deps = []
            dependencies[identifier] = deps
            if not isinstance(entry.get("mandatory"), bool):
                errors.append(f"{location}.mandatory must be boolean")
            if collection == "items":
                if entry.get("kind") not in {"FEATURE", "TEST"}:
                    errors.append(f"{location}.kind must be FEATURE or TEST")
                if entry.get("priority") not in priorities:
                    errors.append(f"{location}.priority is absent from priority_order")
            elif collection == "random_campaigns":
                if not isinstance(entry.get("test"), str) or not entry["test"]:
                    errors.append(f"{location}.test must be non-empty")
                budget = entry.get("seed_budget")
                if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
                    errors.append(f"{location}.seed_budget must be a positive integer")
            else:
                if not isinstance(entry.get("metric"), str) or not entry["metric"]:
                    errors.append(f"{location}.metric must be non-empty")
                target = entry.get("target")
                if (
                    isinstance(target, bool)
                    or not isinstance(target, (int, float))
                    or not math.isfinite(target)
                ):
                    errors.append(f"{location}.target must be a finite number")
    for identifier, deps in dependencies.items():
        for dependency in deps:
            if dependency not in all_ids:
                errors.append(f"{identifier} has unknown dependency {dependency}")
            if dependency == identifier:
                errors.append(f"{identifier} depends on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            errors.append(f"plan inventory dependency cycle includes {identifier}")
            return
        visiting.add(identifier)
        for dependency in dependencies.get(identifier, []):
            if dependency in dependencies:
                visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in dependencies:
        visit(identifier)
    return errors


def manifest_cases(value: Any, *, result: bool = False) -> list[tuple[str, int]]:
    if not isinstance(value, list) or not value:
        raise FlowError("case_manifest/case_results must be a non-empty array")
    cases: list[tuple[str, int]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise FlowError(f"case entry {index} must be an object")
        required = {"test", "seed", "outcome"} if result else {"test", "seed"}
        if set(item) != required:
            raise FlowError(f"case entry {index} must contain exactly {', '.join(sorted(required))}")
        if not isinstance(item.get("test"), str) or not item["test"]:
            raise FlowError(f"case entry {index} has an invalid test")
        seed = item.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise FlowError(f"case entry {index} has an invalid seed")
        if result and item.get("outcome") != "PASS":
            raise FlowError(f"case entry {index} did not PASS")
        cases.append((item["test"], seed))
    if len(set(cases)) != len(cases):
        raise FlowError("case manifest contains duplicate test/seed pairs")
    return cases


def check_regression_task(
    root: Path,
    task: dict[str, Any],
    *,
    scope_name: str,
    required_work_items: set[str] | None = None,
) -> dict[str, Any]:
    result = checked_task_result(root, task)
    request = checked_task_request(root, task)
    context = request.get("context")
    if not isinstance(context, dict) or context.get("regression_scope") != scope_name:
        raise FlowError(f"{task['task_id']} is not a {scope_name} regression")
    requested_cases = manifest_cases(context.get("case_manifest"))
    result_cases = manifest_cases(result.get("payload", {}).get("case_results"), result=True)
    if requested_cases != result_cases:
        raise FlowError(f"{task['task_id']} result does not exactly match its case_manifest")
    if required_work_items is not None:
        work_item_ids = context.get("work_item_ids")
        if (
            not isinstance(work_item_ids, list)
            or not all(isinstance(value, str) for value in work_item_ids)
            or set(work_item_ids) != required_work_items
        ):
            raise FlowError(
                f"{task['task_id']} does not cover the exact mandatory work-item set"
            )
    return result


def materialize_plan(
    root: Path, state: dict[str, Any], review_task: dict[str, Any]
) -> list[str]:
    try:
        result = checked_task_result(root, review_task)
    except FlowError as exc:
        return [str(exc)]
    inventory = result.get("payload", {}).get("plan_inventory")
    errors = validate_plan_inventory(inventory)
    if errors:
        return errors
    revision = review_task.get("input_revision")
    if not revision:
        return ["approved V-plan review has no input revision"]
    if result.get("payload", {}).get("reviewed_revision") != revision:
        return ["approved V-plan review is not bound to its input revision"]
    stored = json.loads(json.dumps(inventory))
    stored["review_task_id"] = review_task["task_id"]
    stored["source_revision"] = revision
    existing_inventory = state.get("plan_inventory")
    if existing_inventory is not None and existing_inventory != stored:
        return ["an accepted V-plan inventory already exists; create a new workflow run"]
    state["plan_inventory"] = stored
    state["priority_order"] = list(inventory["priority_order"])
    state["accepted_revision"] = revision
    expected_items: dict[str, dict[str, Any]] = {}
    for entry in inventory["items"]:
        expected_items[entry["id"]] = {
            "id": entry["id"],
            "kind": entry["kind"],
            "priority": entry["priority"],
            "mandatory": entry["mandatory"],
            "status": "PENDING",
            "dependencies": list(entry["dependencies"]),
            "accepted_revision": revision,
            "builder_task_id": None,
            "review_task_id": None,
            "run_task_id": None,
            "evidence_task_ids": [],
            "last_task_id": None,
            "reason": "Accepted from the approved V-plan inventory.",
            "updated_at": now(),
        }
    if state["work_items"] and state["work_items"] != expected_items:
        return ["work_items disagree with the accepted V-plan inventory"]
    state["work_items"] = expected_items
    return []


def mandatory_work_item_ids(state: dict[str, Any]) -> set[str]:
    return {
        item_id
        for item_id, item in state.get("work_items", {}).items()
        if item.get("mandatory") is True
    }


def require_directed_completion(
    root: Path,
    state: dict[str, Any],
    revision: str | None = None,
    *,
    update_items: bool = True,
) -> list[str]:
    errors: list[str] = []
    mandatory = mandatory_work_item_ids(state)
    unresolved = sorted(
        item_id
        for item_id in mandatory
        if state["work_items"][item_id].get("status") not in {"PASSED", "WAIVED"}
    )
    if unresolved:
        return ["mandatory directed work items are unresolved: " + ", ".join(unresolved)]
    revision = revision or state.get("accepted_revision")
    if not revision:
        return ["directed completion has no accepted revision"]
    regressions = completed_tasks(
        state,
        role="runner",
        action="RUN_REGRESSION",
        phase="FEATURES",
        outcome="PASS",
        revision=revision,
    )
    for task in regressions:
        try:
            check_regression_task(
                root, task, scope_name="CUMULATIVE", required_work_items=mandatory
            )
        except FlowError:
            continue
        if update_items:
            for item_id in mandatory:
                item = state["work_items"][item_id]
                if item.get("status") == "PASSED":
                    item["accepted_revision"] = revision
                    item["run_task_id"] = task["task_id"]
                    item["evidence_task_ids"] = list(
                        dict.fromkeys(item.get("evidence_task_ids", []) + [task["task_id"]])
                    )
                    item["updated_at"] = now()
        return []
    errors.append("phase gate requires a complete CUMULATIVE regression on the accepted revision")
    return errors


def require_random_campaigns(
    root: Path, state: dict[str, Any], revision: str | None = None
) -> list[str]:
    inventory = state.get("plan_inventory") or {}
    campaigns = [
        item for item in inventory.get("random_campaigns", [])
        if item.get("mandatory") is True
    ]
    if not campaigns:
        return []
    revision = revision or state.get("accepted_revision")
    errors: list[str] = []
    for campaign in campaigns:
        candidates = completed_tasks(
            state,
            role="runner",
            action="RUN_REGRESSION",
            phase="RANDOM",
            outcome="PASS",
            revision=revision,
        )
        accepted = False
        for task in candidates:
            try:
                request = checked_task_request(root, task)
                result = checked_task_result(root, task)
                context = request.get("context", {})
                if context.get("campaign_id") != campaign["id"]:
                    continue
                if context.get("regression_scope") != "RANDOM":
                    continue
                requested = manifest_cases(context.get("case_manifest"))
                observed = manifest_cases(
                    result.get("payload", {}).get("case_results"), result=True
                )
                if requested != observed:
                    continue
                if len(requested) != campaign["seed_budget"]:
                    continue
                if any(test != campaign["test"] for test, _ in requested):
                    continue
            except FlowError:
                continue
            accepted = True
            break
        if not accepted:
            errors.append(
                f"random campaign {campaign['id']} lacks its exact {campaign['seed_budget']}-seed PASS manifest"
            )
    return errors


def evaluate_phase_gate(
    root: Path, state: dict[str, Any], current: str, target: str
) -> list[str]:
    if current == target or target in {"BLOCKED", "WAITING_HUMAN"}:
        return []
    if target == "PLAN":
        return []
    if target == "PREFLIGHT":
        reviews = completed_tasks(
            state, role="reviewer", action="REVIEW_VPLAN", phase="PLAN", outcome="APPROVED"
        )
        if not reviews:
            return ["PREFLIGHT gate requires an approved V-plan review"]
        for review in reviews:
            parent = state["tasks"].get(review.get("parent_task_id"))
            origin = find_task_ancestor(
                state,
                parent,
                lambda task: (
                    task.get("role") == "builder"
                    and task.get("action") == "WRITE_VPLAN"
                    and task.get("status") == "COMPLETED"
                    and task.get("outcome") == "READY_FOR_REVIEW"
                ),
            )
            if not (
                parent
                and parent.get("role") == "builder"
                and parent.get("action") in {"WRITE_VPLAN", "APPLY_REVIEW_FIX"}
                and parent.get("status") == "COMPLETED"
                and parent.get("outcome") == "READY_FOR_REVIEW"
                and parent.get("output_revision") == review.get("input_revision")
                and origin
            ):
                continue
            candidate = json.loads(json.dumps(state))
            errors = materialize_plan(root, candidate, review)
            if not errors:
                state.clear()
                state.update(candidate)
                verify_current_revision(root, state, review["input_revision"])
                return []
        return ["PREFLIGHT gate rejected every approved V-plan review as stale or invalid"]
    if target == "SMOKE":
        revision = state.get("accepted_revision")
        tasks = completed_tasks(
            state, role="runner", action="PREFLIGHT", phase="PREFLIGHT",
            outcome="PASS", revision=revision,
        )
        for task in tasks:
            try:
                checked_task_result(root, task)
            except FlowError:
                continue
            return []
        return ["SMOKE gate requires a passing immutable RTL/tool PREFLIGHT task on the accepted plan revision"]
    if target == "FEATURES":
        smoke_runs = completed_tasks(
            state, role="runner", action="RUN_CASE", phase="SMOKE", outcome="PASS"
        )
        for smoke in smoke_runs:
            revision = smoke.get("input_revision")
            compile_id = smoke.get("parent_task_id")
            compile_task = state["tasks"].get(compile_id) if compile_id else None
            review_id = compile_task.get("parent_task_id") if compile_task else None
            review = state["tasks"].get(review_id) if review_id else None
            if not compile_task or not review:
                continue
            build_id = review.get("parent_task_id")
            build = state["tasks"].get(build_id) if build_id else None
            origin = find_task_ancestor(
                state,
                build,
                lambda task: (
                    task.get("role") == "builder"
                    and task.get("action") == "BUILD_SMOKE_FOUNDATION"
                    and task.get("status") == "COMPLETED"
                    and task.get("outcome") == "READY_FOR_REVIEW"
                ),
            )
            if not (
                build
                and build.get("role") == "builder"
                and build.get("action") in {
                    "BUILD_SMOKE_FOUNDATION", "APPLY_REVIEW_FIX", "APPLY_DEBUG_FIX"
                }
                and build.get("status") == "COMPLETED"
                and build.get("outcome") == "READY_FOR_REVIEW"
                and build.get("output_revision") == revision
                and origin
                and review.get("parent_task_id") == build.get("task_id")
                and
                compile_task.get("role") == "runner"
                and compile_task.get("action") == "COMPILE_ELAB"
                and compile_task.get("phase") == "SMOKE"
                and compile_task.get("status") == "COMPLETED"
                and compile_task.get("outcome") == "PASS"
                and review.get("role") == "reviewer"
                and review.get("action") in {"REVIEW_TB", "REVIEW_FIX"}
                and review.get("status") == "COMPLETED"
                and review.get("outcome") == "APPROVED"
                and compile_task.get("input_revision") == revision
                and review.get("input_revision") == revision
            ):
                continue
            try:
                checked_task_result(root, smoke)
                checked_task_result(root, compile_task)
                checked_task_result(root, review)
                checked_task_result(root, build)
                verify_current_revision(root, state, revision)
            except FlowError:
                continue
            state["accepted_revision"] = revision
            return []
        return [
            "FEATURES gate requires chained static review -> compile/elaboration -> smoke PASS on one revision"
        ]
    if target in {"RANDOM", "COVERAGE"}:
        errors = require_directed_completion(root, state)
        if errors:
            return errors
        if target == "RANDOM":
            campaigns = (state.get("plan_inventory") or {}).get("random_campaigns", [])
            if not campaigns:
                return ["RANDOM gate is not applicable because the accepted plan has no campaigns"]
            return []
        return require_random_campaigns(root, state)
    if target == "REGRESSION":
        prerequisite_errors = require_directed_completion(root, state)
        prerequisite_errors.extend(require_random_campaigns(root, state))
        if prerequisite_errors:
            return prerequisite_errors
        inventory = state.get("plan_inventory") or {}
        coverage_ids = {
            item["id"] for item in inventory.get("coverage_items", [])
            if item.get("mandatory") is True
        }
        revision = state.get("accepted_revision")
        if coverage_ids:
            candidates = completed_tasks(
                state,
                role="runner",
                action="MERGE_COVERAGE",
                phase="COVERAGE",
                outcome="PASS",
            )
            selected: str | None = None
            for task in candidates:
                candidate_revision = task.get("input_revision")
                try:
                    result = checked_task_result(root, task)
                    request = checked_task_request(root, task)
                    context = request.get("context", {})
                    if set(context.get("coverage_ids", [])) != coverage_ids:
                        continue
                    summary = result.get("payload", {}).get("coverage_summary")
                    if not isinstance(summary, dict) or summary.get("targets_met") is not True:
                        continue
                    metrics = summary.get("metrics")
                    if not isinstance(metrics, list):
                        continue
                    observed = {
                        metric.get("id"): metric
                        for metric in metrics
                        if isinstance(metric, dict) and isinstance(metric.get("id"), str)
                    }
                    planned = {
                        item["id"]: item
                        for item in inventory.get("coverage_items", [])
                        if item.get("mandatory") is True
                    }
                    if set(observed) != coverage_ids:
                        continue
                    waiver_ids = summary.get("waiver_ids")
                    if (
                        not isinstance(waiver_ids, list)
                        or not all(isinstance(value, str) for value in waiver_ids)
                        or not set(waiver_ids).issubset(coverage_ids)
                    ):
                        continue
                    approved_waivers = {
                        waiver_id
                        for waiver_id in waiver_ids
                        if any(
                            approval.get("gate") == f"COVERAGE:{waiver_id}"
                            and approval.get("decision") in {"APPROVED", "WAIVED"}
                            and approval.get("revision") == candidate_revision
                            for approval in state["approvals"]
                        )
                    }
                    if approved_waivers != set(waiver_ids):
                        continue
                    valid_metrics = True
                    for identifier, target in planned.items():
                        metric = observed[identifier]
                        value = metric.get("value")
                        if (
                            metric.get("metric") != target["metric"]
                            or metric.get("target") != target["target"]
                            or isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(value)
                            or (
                                identifier not in approved_waivers
                                and (
                                    metric.get("met") is not True
                                    or value < target["target"]
                                )
                            )
                        ):
                            valid_metrics = False
                            break
                    if not valid_metrics:
                        continue
                    reviews = completed_tasks(
                        state, role="reviewer", outcome="APPROVED", revision=candidate_revision
                    )
                    eligible_reviews = [
                        review for review in reviews
                        if review.get("action") in {"REVIEW_TB", "REVIEW_FIX"}
                    ]
                    if not eligible_reviews:
                        continue
                    for review in eligible_reviews:
                        checked_task_result(root, review)
                    if candidate_revision != revision:
                        closure_review = next(
                            (
                                review for review in eligible_reviews
                                if find_task_ancestor(
                                    state,
                                    state["tasks"].get(review.get("parent_task_id")),
                                    lambda ancestor: (
                                        ancestor.get("role") == "builder"
                                        and ancestor.get("action") == "COVERAGE_CLOSURE"
                                        and ancestor.get("status") == "COMPLETED"
                                        and ancestor.get("outcome") == "READY_FOR_REVIEW"
                                    ),
                                )
                            ),
                            None,
                        )
                        if closure_review is None:
                            continue
                        if require_directed_completion(
                            root, state, candidate_revision, update_items=False
                        ):
                            continue
                        if require_random_campaigns(root, state, candidate_revision):
                            continue
                    verify_current_revision(root, state, candidate_revision)
                except (FlowError, TypeError):
                    continue
                selected = candidate_revision
                break
            if not selected:
                return [
                    "REGRESSION gate requires all mandatory coverage targets PASS on a statically approved revision"
                ]
            revision = selected
            state["accepted_revision"] = revision
            require_directed_completion(root, state, revision, update_items=True)
        if not revision:
            return ["REGRESSION gate has no accepted revision to freeze"]
        verify_current_revision(root, state, revision)
        state["frozen_revision"] = revision
        return []
    if target == "SIGNOFF":
        revision = state.get("frozen_revision")
        if not revision:
            return ["SIGNOFF gate requires a frozen revision from the coverage gate"]
        if any(item.get("status") == "OPEN" for item in state["blockers"]):
            return ["SIGNOFF gate has open blockers"]
        if any(item.get("status") != "RESOLVED" for item in state["fix_requests"]):
            return ["SIGNOFF gate has unresolved DUT fix requests"]
        mandatory = mandatory_work_item_ids(state)
        regressions = completed_tasks(
            state,
            role="runner",
            action="RUN_REGRESSION",
            phase="REGRESSION",
            outcome="PASS",
            revision=revision,
        )
        for task in regressions:
            try:
                check_regression_task(
                    root, task, scope_name="FROZEN", required_work_items=mandatory
                )
                verify_current_revision(root, state, revision)
            except FlowError:
                continue
            return []
        return ["SIGNOFF gate requires an exact FROZEN regression PASS on frozen_revision"]
    if target == "COMPLETE":
        revision = state.get("frozen_revision")
        if not revision:
            return ["COMPLETE gate has no frozen revision"]
        mandatory = mandatory_work_item_ids(state)
        unresolved_items = sorted(
            item_id for item_id in mandatory
            if state["work_items"][item_id].get("status") not in {"PASSED", "WAIVED"}
        )
        if unresolved_items:
            return ["COMPLETE gate has unresolved mandatory items: " + ", ".join(unresolved_items)]
        if any(item.get("status") == "OPEN" for item in state["blockers"]):
            return ["COMPLETE gate has open blockers"]
        if any(item.get("status") != "RESOLVED" for item in state["fix_requests"]):
            return ["COMPLETE gate has unresolved DUT fix requests"]
        regressions = completed_tasks(
            state,
            role="runner",
            action="RUN_REGRESSION",
            phase="REGRESSION",
            outcome="PASS",
            revision=revision,
        )
        regression: dict[str, Any] | None = None
        for task in regressions:
            try:
                check_regression_task(
                    root, task, scope_name="FROZEN", required_work_items=mandatory
                )
            except FlowError:
                continue
            regression = task
            break
        if regression is None:
            return ["COMPLETE gate cannot revalidate the frozen regression"]
        audits = completed_tasks(
            state,
            role="reviewer",
            action="SIGNOFF_AUDIT",
            phase="SIGNOFF",
            outcome="APPROVED",
            revision=revision,
        )
        for audit in audits:
            if audit.get("parent_task_id") != regression["task_id"]:
                continue
            try:
                result = checked_task_result(root, audit)
            except FlowError:
                continue
            payload = result.get("payload", {}).get("signoff_audit")
            if not isinstance(payload, dict):
                continue
            inventory = state.get("plan_inventory") or {}
            planned_seeds = sum(
                item["seed_budget"]
                for item in inventory.get("random_campaigns", [])
                if item.get("mandatory") is True
            )
            satisfied = sum(
                state["work_items"][item_id].get("status") in {"PASSED", "WAIVED"}
                for item_id in mandatory
            )
            if payload.get("mandatory_items_total") != len(mandatory):
                continue
            if payload.get("mandatory_items_passed") != satisfied:
                continue
            if payload.get("random_seeds_planned") != planned_seeds:
                continue
            if payload.get("random_seeds_completed") != planned_seeds:
                continue
            if payload.get("open_blockers") or payload.get("open_fix_requests"):
                continue
            approvals = [
                item for item in state["approvals"]
                if item.get("gate") == "SIGNOFF"
                and item.get("decision") == "APPROVED"
                and item.get("revision") == revision
                and item.get("timestamp", "") >= audit.get("updated_at", "")
            ]
            if not approvals:
                continue
            latest_by_lineage: dict[tuple[str, str], dict[str, Any]] = {}
            for task in state["tasks"].values():
                key = (task["lineage_id"], task["retry_kind"])
                if task["attempt"] > latest_by_lineage.get(key, {}).get("attempt", 0):
                    latest_by_lineage[key] = task
            unresolved = [
                task["task_id"] for task in latest_by_lineage.values()
                if task.get("status") in {"DRAFT", "READY", "BLOCKED", "FAILED"}
            ]
            if unresolved:
                return ["COMPLETE gate has unresolved latest task attempts: " + ", ".join(sorted(unresolved))]
            verify_current_revision(root, state, revision)
            return []
        return [
            "COMPLETE gate requires an immutable audit chained to the frozen regression and later human approval"
        ]
    return []


def cmd_init(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    path = state_path(root)
    if path.exists():
        raise FlowError(f"workflow already exists: {path}")
    root.mkdir(parents=True, exist_ok=True)
    flow_dir(root).mkdir(parents=True, exist_ok=True)
    (flow_dir(root) / "tasks").mkdir(parents=True, exist_ok=True)
    (flow_dir(root) / "runs").mkdir(parents=True, exist_ok=True)
    spec_path = resolve_path(root, args.spec)
    rtl_filelist_path = resolve_path(root, args.rtl_filelist)
    for label, input_path in (("spec", spec_path), ("rtl filelist", rtl_filelist_path)):
        relative_to_root(root, input_path)
        if not input_path.is_file():
            raise FlowError(f"{label} does not exist: {input_path}")
    rtl_roots = [resolve_path(root, value) for value in args.rtl_root]
    if len(set(rtl_roots)) != len(rtl_roots):
        raise FlowError("--rtl-root values must be unique")
    for rtl_root in rtl_roots:
        relative_to_root(root, rtl_root)
        if not rtl_root.exists():
            raise FlowError(f"RTL root does not exist: {rtl_root}")
        if rtl_root == root:
            raise FlowError("RTL root must be narrower than the project root")
        if paths_overlap(rtl_root, flow_dir(root)):
            raise FlowError(f"RTL root must not overlap .dv: {rtl_root}")
    baseline_revision, baseline_paths, baseline_digests = snapshot_manifest(
        root, [str(spec_path), str(rtl_filelist_path), *(str(path) for path in rtl_roots)]
    )
    timestamp = now()
    state: dict[str, Any] = {
        "schema_version": FLOW_SCHEMA,
        "state_revision": 0,
        "run_id": "dv-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8],
        "created_at": timestamp,
        "updated_at": timestamp,
        "design": {
            "name": args.design_name,
            "spec": str(spec_path),
            "rtl_filelist": str(rtl_filelist_path),
            "rtl_roots": [str(path) for path in rtl_roots],
            "top": args.top,
        },
        "workflow_status": "RUNNING",
        "current_phase": "INIT",
        "phase_status": "PENDING",
        "priority_order": [value.strip() for value in args.priority_order.split(",") if value.strip()],
        "retry_limits": dict(RETRY_LIMITS),
        "tasks": {},
        "work_items": {},
        "artifacts": {
            baseline_revision: {
                "producer_task_id": None,
                "producer_task_ids": [],
                "paths": baseline_paths,
                "digests": baseline_digests,
                "recorded_at": timestamp,
            }
        },
        "baseline_revision": baseline_revision,
        "accepted_revision": None,
        "frozen_revision": None,
        "plan_inventory": None,
        "blockers": [],
        "fix_requests": [],
        "approvals": [],
        "history": [],
    }
    save_state(
        root,
        state,
        "WORKFLOW_INITIALIZED",
        {"project_root": str(root), "baseline_revision": baseline_revision},
    )
    print(path)


def cmd_new_task(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    if state["current_phase"] == "COMPLETE":
        raise FlowError("cannot create tasks after workflow completion")
    if not ID_RE.fullmatch(args.task_id):
        raise FlowError("task-id must be 3-128 characters using letters, digits, dot, underscore, or hyphen")
    if args.task_id in state["tasks"]:
        raise FlowError(f"task already exists: {args.task_id}")
    if args.role not in ROLE_ACTIONS:
        raise FlowError(f"unknown role: {args.role}")
    if args.action not in ROLE_ACTIONS[args.role]:
        raise FlowError(f"invalid action {args.action!r} for role {args.role!r}")
    if args.retry_kind not in state["retry_limits"]:
        raise FlowError(f"unknown retry kind: {args.retry_kind}")
    if args.parent_task_id and args.parent_task_id not in state["tasks"]:
        raise FlowError(f"unknown parent task: {args.parent_task_id}")
    if args.input_revision and args.input_revision not in state["artifacts"]:
        raise FlowError(f"unknown input revision: {args.input_revision}")
    prior = [
        task
        for task in state["tasks"].values()
        if task["lineage_id"] == args.lineage and task["retry_kind"] == args.retry_kind
    ]
    attempt = len(prior) + 1
    limit = state["retry_limits"][args.retry_kind]
    if attempt > limit:
        raise FlowError(
            f"retry limit reached for lineage {args.lineage!r}: {attempt - 1}/{limit} attempts already exist"
        )
    phase = args.phase or state["current_phase"]
    if phase not in PHASES:
        raise FlowError(f"invalid phase: {phase}")
    current_phase = state["current_phase"]
    direct_forward = phase in TRANSITIONS[current_phase] and phase not in {
        "BLOCKED", "WAITING_HUMAN", "COMPLETE"
    }
    if phase != current_phase and not direct_forward:
        raise FlowError(
            f"task phase must be current or its direct forward phase: {current_phase}"
        )
    if args.parent_task_id:
        parent = state["tasks"][args.parent_task_id]
        if parent.get("status") != "COMPLETED":
            raise FlowError("parent task must be completed before creating a child")
        if args.input_revision != parent.get("output_revision"):
            raise FlowError("child input revision must equal the parent output revision")
    task_dir = flow_dir(root) / "tasks" / args.task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    request_path = task_dir / "request.json"
    result_path = task_dir / "result.json"
    request = {
        "schema_version": TASK_SCHEMA,
        "task_id": args.task_id,
        "run_id": state["run_id"],
        "role": args.role,
        "action": args.action,
        "phase": phase,
        "attempt": attempt,
        "lineage_id": args.lineage,
        "retry_kind": args.retry_kind,
        "requested_by": "main",
        "reply_to": "main",
        "project_root": str(root),
        "input_revision": args.input_revision,
        "revision_paths": (
            list(state["artifacts"][args.input_revision]["paths"])
            if args.input_revision
            else []
        ),
        "inputs": [],
        "scope": {"read": [], "write": []},
        "acceptance": [],
        "context": {},
        "prior_result_refs": [],
        "expected_result_path": relative_to_root(root, result_path),
    }
    write_json_atomic(request_path, request)
    task = {
        "task_id": args.task_id,
        "run_id": state["run_id"],
        "lineage_id": args.lineage,
        "retry_kind": args.retry_kind,
        "attempt": attempt,
        "role": args.role,
        "action": args.action,
        "phase": phase,
        "status": "DRAFT",
        "input_revision": args.input_revision,
        "output_revision": None,
        "parent_task_id": args.parent_task_id,
        "request_path": relative_to_root(root, request_path),
        "result_path": relative_to_root(root, result_path),
        "request_sha256": None,
        "result_sha256": None,
        "protected_paths": [
            relative_to_root(root, resolve_path(root, state["design"]["spec"])),
            relative_to_root(root, resolve_path(root, state["design"]["rtl_filelist"])),
            *[
                relative_to_root(root, resolve_path(root, value))
                for value in state["design"]["rtl_roots"]
            ],
        ],
        "write_scope_before": {},
        "outcome": None,
        "created_at": now(),
        "updated_at": now(),
    }
    state["tasks"][args.task_id] = task
    save_state(root, state, "TASK_CREATED", {"task_id": args.task_id, "attempt": attempt})
    print(request_path)


def cmd_seal_task(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    task = state["tasks"].get(args.task_id)
    if not task:
        raise FlowError(f"unknown task: {args.task_id}")
    if task["status"] != "DRAFT":
        raise FlowError(f"only DRAFT tasks can be sealed; current status is {task['status']}")
    request = read_json(root / task["request_path"])
    errors = validate_request(root, request, task)
    if errors:
        raise FlowError("invalid task request: " + "; ".join(errors))
    if task["input_revision"]:
        input_snapshot = state["artifacts"][task["input_revision"]]
        expected_paths = set(input_snapshot["paths"])
        if task["role"] == "builder":
            expected_paths.update(
                relative_to_root(root, resolve_path(root, value))
                for value in request["scope"]["write"]
            )
        actual_paths = {
            relative_to_root(root, resolve_path(root, value))
            for value in request["revision_paths"]
        }
        if actual_paths != expected_paths:
            raise FlowError(
                "revision_paths must exactly equal the input artifact paths"
                + (" plus builder write roots" if task["role"] == "builder" else "")
            )
        current_revision, _, _ = snapshot_manifest(root, input_snapshot["paths"])
        if current_revision != task["input_revision"]:
            raise FlowError(
                f"input revision drifted before dispatch: expected {task['input_revision']}, "
                f"observed {current_revision}"
            )
    write_scope = request["scope"]["write"]
    task["write_scope_before"] = (
        scoped_file_manifest(root, write_scope) if task["role"] == "builder" else {}
    )
    task["request_sha256"] = artifact_digest(root, root / task["request_path"])
    task["status"] = "READY"
    task["updated_at"] = now()
    save_state(root, state, "TASK_SEALED", {"task_id": args.task_id})
    print(root / task["request_path"])


def cmd_record_result(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    task = state["tasks"].get(args.task_id)
    if not task:
        raise FlowError(f"unknown task: {args.task_id}")
    if task["status"] != "READY":
        raise FlowError(f"only READY tasks accept results; current status is {task['status']}")
    result_path = root / task["result_path"]
    result = read_json(result_path)
    errors = validate_result(root, result, task)
    request = read_json(root / task["request_path"])
    errors.extend(validate_request(root, request, task))
    if task["role"] == "runner" and isinstance(result.get("payload"), dict):
        run = result["payload"].get("run")
        context = request.get("context", {})
        if isinstance(run, dict) and isinstance(context, dict):
            for key in ("command", "cwd", "tool"):
                if run.get(key) != context.get(key):
                    errors.append(f"runner payload.run.{key} must equal sealed context.{key}")
            if task["action"] == "RUN_CASE":
                tests = context.get("test_ids", [])
                seeds = context.get("seeds", [])
                if (
                    len(tests) == 1 and run.get("test") != tests[0]
                ):
                    errors.append("RUN_CASE payload.run.test must equal context.test_ids[0]")
                if (
                    len(seeds) == 1 and run.get("seed") != seeds[0]
                ):
                    errors.append("RUN_CASE payload.run.seed must equal context.seeds[0]")
    if task.get("request_sha256") != artifact_digest(root, root / task["request_path"]):
        errors.append("task request changed after sealing")
    scope = request.get("scope")
    write_values = scope.get("write", []) if isinstance(scope, dict) else []
    write_roots = [
        resolve_path(root, value) for value in write_values if isinstance(value, str)
    ]
    read_values = scope.get("read", []) if isinstance(scope, dict) else []
    read_roots = [
        resolve_path(root, value) for value in read_values if isinstance(value, str)
    ]
    for artifact in result.get("artifacts", []):
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            continue
        artifact_path = resolve_path(root, artifact["path"])
        if not any(
            artifact_path == allowed or allowed in artifact_path.parents
            for allowed in write_roots
        ):
            errors.append(f"artifact is outside the task write scope: {artifact['path']}")
    for evidence in result.get("evidence", []):
        if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str):
            continue
        evidence_path = resolve_path(root, evidence["path"])
        try:
            relative_to_root(root, evidence_path)
        except FlowError:
            errors.append(f"evidence path escapes project root: {evidence['path']}")
            continue
        if not evidence_path.exists():
            errors.append(f"evidence path does not exist: {evidence['path']}")
        if not any(
            evidence_path == allowed or allowed in evidence_path.parents
            for allowed in read_roots + write_roots
        ):
            errors.append(f"evidence is outside the task read/write scope: {evidence['path']}")
    deleted_files: list[str] = []
    if task["role"] == "builder" and isinstance(result.get("payload"), dict):
        change_set = result["payload"].get("change_set")
        if isinstance(change_set, dict):
            candidate = change_set.get("files_deleted", [])
            if isinstance(candidate, list) and all(isinstance(value, str) for value in candidate):
                deleted_files = candidate
            else:
                errors.append("builder change_set.files_deleted must be an array of paths")
    for value in deleted_files:
        deleted_path = resolve_path(root, value)
        if not any(
            deleted_path == allowed or allowed in deleted_path.parents
            for allowed in write_roots
        ):
            errors.append(f"deleted path is outside the task write scope: {value}")
        if deleted_path.exists():
            errors.append(f"builder declared a deleted path that still exists: {value}")
    write_scope_after: dict[str, str] = {}
    if task["role"] == "builder":
        write_scope_after = scoped_file_manifest(root, write_values)
        before = task.get("write_scope_before", {})
        if not isinstance(before, dict):
            errors.append("builder task is missing its sealed write-scope baseline")
            before = {}
        actual_created = set(write_scope_after) - set(before)
        actual_deleted = set(before) - set(write_scope_after)
        actual_modified = {
            path for path in set(before) & set(write_scope_after)
            if before[path] != write_scope_after[path]
        }
        is_recordable_change = (
            result.get("agent_status") == "COMPLETED"
            and result.get("outcome") == "READY_FOR_REVIEW"
        )
        if is_recordable_change:
            payload_value = result.get("payload")
            change_set = (
                payload_value.get("change_set", {})
                if isinstance(payload_value, dict)
                else {}
            )
            if isinstance(change_set, dict):
                try:
                    declared_created = normalize_result_paths(
                        root, change_set.get("files_created"), "files_created"
                    )
                    declared_modified = normalize_result_paths(
                        root, change_set.get("files_modified"), "files_modified"
                    )
                    declared_deleted = normalize_result_paths(
                        root, change_set.get("files_deleted"), "files_deleted"
                    )
                except FlowError as exc:
                    errors.append(str(exc))
                else:
                    if declared_created != actual_created:
                        errors.append("builder files_created does not match the sealed write-scope diff")
                    if declared_modified != actual_modified:
                        errors.append("builder files_modified does not match the sealed write-scope diff")
                    if declared_deleted != actual_deleted:
                        errors.append("builder files_deleted does not match the sealed write-scope diff")
        elif actual_created or actual_modified or actual_deleted:
            errors.append(
                "builder mutated its write scope without a READY_FOR_REVIEW result"
            )
    parent_id = task.get("parent_task_id")
    if parent_id:
        parent = state["tasks"][parent_id]
        if parent["status"] != "COMPLETED":
            errors.append(f"parent task is not completed: {parent_id}")
        if task["input_revision"] != parent.get("output_revision"):
            errors.append(
                f"stale input revision: task has {task['input_revision']!r}, parent has {parent.get('output_revision')!r}"
            )
        if parent.get("status") == "COMPLETED":
            try:
                checked_task_result(root, parent)
            except FlowError as exc:
                errors.append(str(exc))
    input_revision = task["input_revision"]
    if input_revision:
        snapshot = state["artifacts"].get(input_revision)
        if not snapshot:
            errors.append(f"input revision is absent from the artifact ledger: {input_revision}")
        elif task["role"] == "builder":
            for relative, expected_digest in snapshot["digests"].items():
                path = resolve_path(root, relative)
                if any(paths_overlap(path, allowed) for allowed in write_roots):
                    continue
                if not path.exists():
                    errors.append(f"read-only snapshot path disappeared: {relative}")
                elif snapshot_path_digest(root, path) != expected_digest:
                    errors.append(f"read-only snapshot path drifted: {relative}")
        else:
            try:
                observed_revision, _, _ = snapshot_manifest(root, snapshot["paths"])
            except FlowError as exc:
                errors.append(str(exc))
            else:
                if observed_revision != input_revision:
                    errors.append(
                        f"input revision drifted during task: expected {input_revision}, "
                        f"observed {observed_revision}"
                    )
    if errors:
        raise FlowError("invalid task result: " + "; ".join(errors))
    snapshot_paths: list[str] = []
    snapshot_digests: dict[str, str] = {}
    if (
        task["role"] == "builder"
        and result["agent_status"] == "COMPLETED"
        and result["outcome"] == "READY_FOR_REVIEW"
    ):
        prior_paths = (
            state["artifacts"][task["input_revision"]]["paths"]
            if task["input_revision"]
            else []
        )
        revision_values = [
            value for value in prior_paths
            if not any(paths_overlap(resolve_path(root, value), write_root) for write_root in write_roots)
        ]
        revision_values.extend(write_scope_after)
        output_revision, snapshot_paths, snapshot_digests = snapshot_manifest(
            root, revision_values
        )
    else:
        output_revision = task["input_revision"]
    result["output_revision"] = output_revision
    write_json_atomic(result_path, result)
    status_map = {"COMPLETED": "COMPLETED", "BLOCKED": "BLOCKED", "FAILED": "FAILED"}
    task["status"] = status_map[result["agent_status"]]
    task["outcome"] = result["outcome"]
    task["output_revision"] = output_revision
    task["result_sha256"] = artifact_digest(root, result_path)
    task["updated_at"] = now()
    if (
        output_revision
        and task["role"] == "builder"
        and result["agent_status"] == "COMPLETED"
        and result["outcome"] == "READY_FOR_REVIEW"
    ):
        artifact_entry = state["artifacts"].get(output_revision)
        if artifact_entry is None:
            state["artifacts"][output_revision] = {
                "producer_task_id": args.task_id,
                "producer_task_ids": [args.task_id],
                "paths": snapshot_paths,
                "digests": snapshot_digests,
                "recorded_at": now(),
            }
        else:
            producers = artifact_entry.setdefault("producer_task_ids", [])
            if args.task_id not in producers:
                producers.append(args.task_id)
        affected_ids = context_ids(
            request.get("context"), "work_item_ids", "test_ids", "feature_ids"
        )
        for item_id in affected_ids:
            item = state.get("work_items", {}).get(item_id)
            if item and item.get("status") == "PASSED":
                item["status"] = "BUILDING"
                item["builder_task_id"] = task["task_id"]
                item["review_task_id"] = None
                item["run_task_id"] = None
                item["reason"] = "A builder change invalidated prior gates for this item."
                item["updated_at"] = now()
    save_state(
        root,
        state,
        "TASK_RESULT_RECORDED",
        {
            "task_id": args.task_id,
            "agent_status": result["agent_status"],
            "outcome": result["outcome"],
            "output_revision": output_revision,
        },
    )
    print(json.dumps({"task_id": args.task_id, "outcome": result["outcome"], "output_revision": output_revision}))


def cmd_transition(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    current = state["current_phase"]
    target = args.to
    if target not in PHASES:
        raise FlowError(f"invalid target phase: {target}")
    if target not in TRANSITIONS[current]:
        raise FlowError(f"transition is not allowed: {current} -> {target}")
    gate_errors = evaluate_phase_gate(root, state, current, target)
    if gate_errors:
        raise FlowError(f"{target} gate failed: " + "; ".join(gate_errors))
    state["current_phase"] = target
    state["workflow_status"] = {
        "COMPLETE": "COMPLETE",
        "BLOCKED": "BLOCKED",
        "WAITING_HUMAN": "WAITING_HUMAN",
    }.get(target, "RUNNING")
    state["phase_status"] = "PASSED" if target == "COMPLETE" else "IN_PROGRESS"
    if target == "COMPLETE":
        errors = validate_state(root, state, check_files=True)
        if errors:
            raise FlowError("cannot complete: " + "; ".join(errors))
    save_state(root, state, "PHASE_TRANSITION", {"from": current, "to": target, "reason": args.reason})
    print(target)


def cmd_set_item(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    if not ID_RE.fullmatch(args.item_id):
        raise FlowError("invalid work item id")
    if args.status not in ITEM_STATUSES:
        raise FlowError(f"invalid work item status: {args.status}")
    if args.last_task_id and args.last_task_id not in state["tasks"]:
        raise FlowError(f"unknown last task: {args.last_task_id}")
    if state.get("plan_inventory") is None:
        raise FlowError("work items can only be updated after an approved plan is materialized")
    if args.item_id not in state["work_items"]:
        raise FlowError(f"work item is absent from the accepted plan inventory: {args.item_id}")
    if args.status == "WAIVED" and not any(
        approval.get("gate") == f"WORK_ITEM:{args.item_id}"
        and approval.get("decision") in {"APPROVED", "WAIVED"}
        for approval in state["approvals"]
    ):
        raise FlowError(f"waiving {args.item_id} requires a WORK_ITEM:{args.item_id} approval")
    if args.status == "WAIVED" and any(
        args.item_id in request.get("affected_ids", [])
        and request.get("status") != "RESOLVED"
        for request in state["fix_requests"]
    ):
        raise FlowError("a confirmed DUT fix request cannot be waived into signoff")
    existing = state["work_items"][args.item_id]
    if args.kind and args.kind != existing["kind"]:
        raise FlowError("work item kind is immutable after plan acceptance")
    if args.priority and args.priority != existing["priority"]:
        raise FlowError("work item priority is immutable after plan acceptance")
    if args.dependency is not None and args.dependency != existing["dependencies"]:
        raise FlowError("work item dependencies are immutable after plan acceptance")
    current_status = existing.get("status")
    if args.status != current_status and args.status not in ITEM_TRANSITIONS[current_status]:
        raise FlowError(
            f"work item transition is not allowed: {current_status or 'NEW'} -> {args.status}"
        )
    if current_status == "PENDING" and args.status != "PENDING":
        try:
            priority_index = state["priority_order"].index(existing["priority"])
        except ValueError as exc:
            raise FlowError("work item priority is absent from priority_order") from exc
        blocked_higher = [
            item_id
            for item_id, item in state["work_items"].items()
            if item.get("mandatory") is True
            and state["priority_order"].index(item["priority"]) < priority_index
            and item.get("status") not in {"PASSED", "WAIVED"}
        ]
        if blocked_higher:
            raise FlowError(
                "higher-priority mandatory items are unresolved: "
                + ", ".join(sorted(blocked_higher))
            )
    builder_task_id = existing.get("builder_task_id")
    review_task_id = existing.get("review_task_id")
    if args.status == "AWAITING_REVIEW":
        if not args.last_task_id:
            raise FlowError("AWAITING_REVIEW requires --last-task-id for the builder result")
        builder_task = state["tasks"][args.last_task_id]
        if (
            builder_task.get("role") != "builder"
            or builder_task.get("action") not in {
                "IMPLEMENT_FEATURE_BATCH", "APPLY_REVIEW_FIX",
                "APPLY_DEBUG_FIX", "COVERAGE_CLOSURE",
            }
            or builder_task.get("status") != "COMPLETED"
            or builder_task.get("outcome") != "READY_FOR_REVIEW"
            or not builder_task.get("output_revision")
        ):
            raise FlowError("AWAITING_REVIEW requires a completed builder change")
        builder_context = checked_task_request(root, builder_task).get("context", {})
        if args.item_id not in context_ids(
            builder_context, "work_item_ids", "test_ids", "feature_ids"
        ):
            raise FlowError("builder result does not name this work item in its context")
        checked_task_result(root, builder_task)
        builder_task_id = builder_task["task_id"]
    if args.status == "READY_TO_RUN":
        if not args.last_task_id:
            raise FlowError("READY_TO_RUN requires --last-task-id for the approved review")
        review_task = state["tasks"][args.last_task_id]
        if (
            review_task.get("role") != "reviewer"
            or review_task.get("action") not in {"REVIEW_TB", "REVIEW_FIX"}
            or review_task.get("status") != "COMPLETED"
            or review_task.get("outcome") != "APPROVED"
            or not review_task.get("input_revision")
        ):
            raise FlowError("READY_TO_RUN requires an approved static code review")
        if not builder_task_id or review_task.get("parent_task_id") != builder_task_id:
            raise FlowError("approved review must be the child of this item's builder task")
        builder_task = state["tasks"].get(builder_task_id)
        if (
            not builder_task
            or builder_task.get("output_revision") != review_task.get("input_revision")
        ):
            raise FlowError("review revision does not match this item's builder output")
        checked_task_result(root, review_task)
        review_context = checked_task_request(root, review_task).get("context", {})
        scoped_ids = context_ids(
            review_context, "work_item_ids", "test_ids", "feature_ids"
        )
        if args.item_id not in scoped_ids:
            raise FlowError("approved review does not name this work item in its context")
        review_task_id = review_task["task_id"]
    if args.status == "PASSED":
        if not args.last_task_id:
            raise FlowError("PASSED requires --last-task-id")
        task = state["tasks"][args.last_task_id]
        if (
            task["role"] != "runner"
            or task["action"] not in {"RUN_CASE", "RUN_REGRESSION"}
            or task["status"] != "COMPLETED"
            or task["outcome"] != "PASS"
            or not task["input_revision"]
        ):
            raise FlowError("PASSED requires a completed passing runner task with a revision")
        result = checked_task_result(root, task)
        case_results = result.get("payload", {}).get("case_results", [])
        if task["action"] == "RUN_REGRESSION":
            matched = any(
                isinstance(case, dict)
                and case.get("test") == args.item_id
                and case.get("outcome") == "PASS"
                for case in case_results
            )
        else:
            matched = result.get("payload", {}).get("run", {}).get("test") == args.item_id
        if not matched:
            raise FlowError(f"runner result does not prove {args.item_id} passed")
        dependencies = args.dependency or existing.get("dependencies", [])
        unresolved = [
            dependency
            for dependency in dependencies
            if state["work_items"].get(dependency, {}).get("status")
            not in {"PASSED", "WAIVED"}
        ]
        if unresolved:
            raise FlowError("PASSED item has unresolved dependencies: " + ", ".join(unresolved))
        rtl_fix = next(
            (
                request for request in state["fix_requests"]
                if args.item_id in request.get("affected_ids", [])
                and request.get("status") == "RTL_UPDATED_PENDING_VERIFY"
                and request.get("rtl_revision") == task["input_revision"]
            ),
            None,
        )
        review_candidates = completed_tasks(
            state, role="reviewer", outcome="APPROVED", revision=task["input_revision"]
        )
        matching_review: dict[str, Any] | None = None
        for review in review_candidates:
            if review.get("action") not in {"REVIEW_TB", "REVIEW_FIX"}:
                continue
            review_context = checked_task_request(root, review).get("context", {})
            scoped_ids = context_ids(
                review_context, "work_item_ids", "test_ids", "feature_ids"
            )
            if args.item_id in scoped_ids:
                checked_task_result(root, review)
                matching_review = review
                break
        if matching_review is None and rtl_fix is None:
            raise FlowError("PASSED requires an approved static review for this item and revision")
        matching_builder = (
            state["tasks"].get(matching_review.get("parent_task_id"))
            if matching_review is not None else None
        )
        if rtl_fix is None and not (
            matching_builder
            and matching_builder.get("role") == "builder"
            and matching_builder.get("status") == "COMPLETED"
            and matching_builder.get("outcome") == "READY_FOR_REVIEW"
            and matching_builder.get("output_revision") == task["input_revision"]
            and matching_builder.get("task_id") == builder_task_id
        ):
            raise FlowError("PASSED requires the item's exact builder -> reviewer lineage")
        if rtl_fix is not None:
            diagnosis = state["tasks"].get(rtl_fix.get("diagnosis_task_id"))
            diagnosis_result = checked_task_result(root, diagnosis) if diagnosis else {}
            rerun = diagnosis_result.get("payload", {}).get("rerun", {})
            actual_run = result.get("payload", {}).get("run", {})
            if task["action"] == "RUN_CASE":
                exact_rerun = (
                    actual_run.get("test") == rerun.get("test")
                    and actual_run.get("seed") == rerun.get("seed")
                )
            else:
                exact_rerun = any(
                    isinstance(case, dict)
                    and case.get("test") == rerun.get("test")
                    and case.get("seed") == rerun.get("seed")
                    and case.get("outcome") == "PASS"
                    for case in result.get("payload", {}).get("case_results", [])
                )
            if not exact_rerun:
                raise FlowError("RTL fix must rerun the debugger's exact test and seed")
        else:
            diagnosis = find_task_ancestor(
                state,
                matching_builder,
                lambda ancestor: (
                    ancestor.get("role") == "debugger"
                    and ancestor.get("status") == "COMPLETED"
                    and ancestor.get("outcome") == "DIAGNOSED"
                ),
            )
        if rtl_fix is None and diagnosis is not None:
            diagnosis_result = checked_task_result(root, diagnosis)
            rerun = diagnosis_result.get("payload", {}).get("rerun", {})
            actual_run = result.get("payload", {}).get("run", {})
            if task["action"] == "RUN_CASE":
                exact_rerun = (
                    actual_run.get("test") == rerun.get("test")
                    and actual_run.get("seed") == rerun.get("seed")
                )
            else:
                exact_rerun = any(
                    isinstance(case, dict)
                    and case.get("test") == rerun.get("test")
                    and case.get("seed") == rerun.get("seed")
                    and case.get("outcome") == "PASS"
                    for case in result.get("payload", {}).get("case_results", [])
                )
            if not exact_rerun:
                raise FlowError("debug fix must rerun the debugger's exact test and seed")
        if matching_review is not None:
            review_task_id = matching_review["task_id"]
        verify_current_revision(root, state, task["input_revision"])
        state["accepted_revision"] = task["input_revision"]
    state["work_items"][args.item_id] = {
        "id": args.item_id,
        "kind": existing["kind"],
        "priority": existing["priority"],
        "mandatory": existing["mandatory"],
        "status": args.status,
        "dependencies": existing["dependencies"],
        "accepted_revision": (
            state["tasks"][args.last_task_id]["input_revision"]
            if args.status == "PASSED" and args.last_task_id
            else existing.get("accepted_revision")
        ),
        "builder_task_id": builder_task_id,
        "review_task_id": review_task_id,
        "run_task_id": (
            args.last_task_id if args.status == "PASSED" else existing.get("run_task_id")
        ),
        "evidence_task_ids": list(
            dict.fromkeys(
                existing.get("evidence_task_ids", [])
                + ([args.last_task_id] if args.last_task_id else [])
            )
        ),
        "last_task_id": args.last_task_id or existing.get("last_task_id"),
        "reason": args.reason,
        "updated_at": now(),
    }
    save_state(root, state, "WORK_ITEM_UPDATED", {"item_id": args.item_id, "status": args.status})
    print(args.item_id)


def cmd_add_blocker(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    sequence = len(state["blockers"]) + 1
    identifier = f"BLK-{sequence:04d}"
    entry = {
        "id": identifier,
        "kind": args.kind,
        "status": "OPEN",
        "summary": args.summary,
        "related_ids": args.related_id or [],
        "created_at": now(),
        "resolved_at": None,
    }
    state["blockers"].append(entry)
    save_state(root, state, "BLOCKER_ADDED", {"blocker_id": identifier})
    print(identifier)


def cmd_resolve_blocker(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    entry = next((item for item in state["blockers"] if item["id"] == args.blocker_id), None)
    if not entry:
        raise FlowError(f"unknown blocker: {args.blocker_id}")
    if entry["status"] != "OPEN":
        raise FlowError(f"blocker is not open: {args.blocker_id}")
    if args.status == "WAIVED" and not any(
        approval.get("gate") == f"BLOCKER:{args.blocker_id}"
        and approval.get("decision") in {"APPROVED", "WAIVED"}
        for approval in state["approvals"]
    ):
        raise FlowError(
            f"waiving {args.blocker_id} requires a BLOCKER:{args.blocker_id} approval"
        )
    entry["status"] = args.status
    entry["resolution"] = args.resolution
    entry["resolved_at"] = now()
    save_state(
        root,
        state,
        "BLOCKER_RESOLVED",
        {"blocker_id": args.blocker_id, "status": args.status},
    )
    print(args.blocker_id)


def cmd_add_fix_request(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    if args.failure_task_id not in state["tasks"]:
        raise FlowError(f"unknown failure task: {args.failure_task_id}")
    if args.diagnosis_task_id not in state["tasks"]:
        raise FlowError(f"unknown diagnosis task: {args.diagnosis_task_id}")
    failure = state["tasks"][args.failure_task_id]
    diagnosis = state["tasks"][args.diagnosis_task_id]
    failure_outcomes = {
        "COMPILE_ERROR", "ELABORATION_ERROR", "SIMULATION_FAILURE", "TIMEOUT"
    }
    if not (
        failure.get("role") == "runner"
        and failure.get("status") == "COMPLETED"
        and failure.get("outcome") in failure_outcomes
    ):
        raise FlowError("fix request failure task must be a completed failing runner task")
    if not (
        diagnosis.get("role") == "debugger"
        and diagnosis.get("status") == "COMPLETED"
        and diagnosis.get("outcome") == "DIAGNOSED"
        and diagnosis.get("parent_task_id") == failure["task_id"]
        and diagnosis.get("input_revision") == failure.get("input_revision")
    ):
        raise FlowError("fix request diagnosis must be chained to the failure on the same revision")
    checked_task_result(root, failure)
    diagnosis_result = checked_task_result(root, diagnosis)
    diagnosis_payload = diagnosis_result.get("payload", {})
    if not (
        diagnosis_payload.get("classification") == "DUT_BUG"
        and diagnosis_payload.get("route_to") == "RTL_OWNER"
        and diagnosis_payload.get("confidence") in {"HIGH", "MEDIUM"}
    ):
        raise FlowError("fix request requires a non-low-confidence DUT_BUG diagnosis")
    affected_ids = args.affected_id or []
    if not affected_ids:
        raise FlowError("fix request requires at least one --affected-id")
    unknown = sorted(set(affected_ids) - set(state["work_items"]))
    if unknown:
        raise FlowError("fix request names unknown work items: " + ", ".join(unknown))
    sequence = len(state["fix_requests"]) + 1
    identifier = f"FR-{sequence:04d}"
    entry = {
        "id": identifier,
        "status": "OPEN",
        "classification": "DUT_BUG",
        "failure_task_id": args.failure_task_id,
        "diagnosis_task_id": args.diagnosis_task_id,
        "summary": args.summary,
        "affected_ids": affected_ids,
        "evidence": args.evidence or [],
        "previous_revision": failure["input_revision"],
        "rtl_revision": None,
        "external_ref": None,
        "verification_task_id": None,
        "created_at": now(),
        "updated_at": now(),
    }
    for item_id in affected_ids:
        item = state["work_items"][item_id]
        item["status"] = "BLOCKED_DUT"
        item["last_task_id"] = diagnosis["task_id"]
        item["reason"] = f"Blocked by confirmed DUT fix request {identifier}."
        item["updated_at"] = now()
    state["fix_requests"].append(entry)
    save_state(root, state, "FIX_REQUEST_ADDED", {"fix_request_id": identifier})
    print(identifier)


def cmd_accept_rtl_update(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    entry = next(
        (item for item in state["fix_requests"] if item["id"] == args.fix_request_id),
        None,
    )
    if not entry:
        raise FlowError(f"unknown fix request: {args.fix_request_id}")
    if entry["status"] != "OPEN":
        raise FlowError(f"fix request is not awaiting an RTL update: {args.fix_request_id}")
    if state.get("accepted_revision") != args.expected_revision:
        raise FlowError("accepted revision changed; refuse a stale RTL update")
    snapshot = state["artifacts"].get(args.expected_revision)
    if not snapshot:
        raise FlowError("expected revision is absent from the artifact ledger")
    new_revision, paths, digests = snapshot_manifest(root, snapshot["paths"])
    if new_revision == args.expected_revision:
        raise FlowError("RTL update did not change the accepted snapshot")
    rtl_roots = [
        resolve_path(root, state["design"]["rtl_filelist"]),
        *(resolve_path(root, value) for value in state["design"]["rtl_roots"]),
    ]
    changed_rtl = False
    for relative, previous_digest in snapshot["digests"].items():
        path = resolve_path(root, relative)
        is_rtl = any(paths_overlap(path, rtl_root) for rtl_root in rtl_roots)
        changed = digests.get(relative) != previous_digest
        if changed and not is_rtl:
            raise FlowError(f"non-RTL snapshot path changed during RTL update: {relative}")
        changed_rtl = changed_rtl or (changed and is_rtl)
    if not changed_rtl:
        raise FlowError("accepted snapshot changed, but no protected RTL root changed")
    state["artifacts"][new_revision] = {
        "producer_task_id": None,
        "producer_task_ids": [],
        "paths": paths,
        "digests": digests,
        "recorded_at": now(),
    }
    state["accepted_revision"] = new_revision
    state["frozen_revision"] = None
    entry["status"] = "RTL_UPDATED_PENDING_VERIFY"
    entry["previous_revision"] = args.expected_revision
    entry["rtl_revision"] = new_revision
    entry["external_ref"] = args.external_ref
    entry["updated_at"] = now()
    for item_id in entry["affected_ids"]:
        item = state["work_items"][item_id]
        if item.get("status") == "BLOCKED_DUT":
            item["status"] = "READY_TO_RUN"
            item["reason"] = (
                f"RTL update for {entry['id']} preserves the reviewed TB and awaits "
                "the debugger's exact rerun."
            )
            item["updated_at"] = now()
    save_state(
        root,
        state,
        "RTL_UPDATE_ACCEPTED",
        {"fix_request_id": entry["id"], "revision": new_revision},
    )
    print(new_revision)


def cmd_resolve_fix_request(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    entry = next((item for item in state["fix_requests"] if item["id"] == args.fix_request_id), None)
    if not entry:
        raise FlowError(f"unknown fix request: {args.fix_request_id}")
    if entry["status"] != "RTL_UPDATED_PENDING_VERIFY":
        raise FlowError(f"fix request has no accepted RTL update to verify: {args.fix_request_id}")
    task = state["tasks"].get(args.verification_task_id)
    if not task:
        raise FlowError(f"unknown verification task: {args.verification_task_id}")
    if not (
        task.get("role") == "runner"
        and task.get("action") == "RUN_REGRESSION"
        and task.get("status") == "COMPLETED"
        and task.get("outcome") == "PASS"
        and task.get("input_revision") == entry.get("rtl_revision")
    ):
        raise FlowError(
            "fix resolution requires an affected cumulative regression on the RTL revision"
        )
    result = checked_task_result(root, task)
    request = checked_task_request(root, task)
    check_regression_task(
        root,
        task,
        scope_name="CUMULATIVE",
        required_work_items=set(entry["affected_ids"]),
    )
    diagnosis = state["tasks"].get(entry["diagnosis_task_id"])
    diagnosis_result = checked_task_result(root, diagnosis) if diagnosis else {}
    rerun = diagnosis_result.get("payload", {}).get("rerun", {})
    if not any(
        isinstance(case, dict)
        and case.get("test") == rerun.get("test")
        and case.get("seed") == rerun.get("seed")
        and case.get("outcome") == "PASS"
        for case in result.get("payload", {}).get("case_results", [])
    ):
        raise FlowError("DUT fix regression does not include the debugger's exact reproducer")
    if any(
        state["work_items"][item_id].get("status") != "PASSED"
        for item_id in entry["affected_ids"]
    ):
        raise FlowError("affected work items must complete review and rerun before fix resolution")
    entry["status"] = "RESOLVED"
    entry["resolution"] = args.resolution
    entry["verification_task_id"] = task["task_id"]
    entry["updated_at"] = now()
    save_state(root, state, "FIX_REQUEST_RESOLVED", {"fix_request_id": args.fix_request_id})
    print(args.fix_request_id)


def cmd_approve(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    require_mutable_state(state)
    if args.gate == "SIGNOFF" and args.decision == "APPROVED" and not args.revision:
        raise FlowError("approved SIGNOFF requires --revision")
    entry = {
        "gate": args.gate,
        "decision": args.decision,
        "approved_by": args.approved_by,
        "note": args.note,
        "revision": args.revision,
        "timestamp": now(),
    }
    state["approvals"].append(entry)
    save_state(root, state, "APPROVAL_RECORDED", entry)
    print(args.gate)


def cmd_show(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = load_state(root)
    print(json.dumps(state, indent=2, sort_keys=True))


def cmd_validate(args: argparse.Namespace) -> None:
    root = project_root(args.root)
    state = read_json(state_path(root))
    errors = validate_state(root, state, check_files=True)
    if errors:
        raise FlowError("; ".join(errors))
    print(f"OK: {state_path(root)} (revision {state['state_revision']})")


def parser() -> argparse.ArgumentParser:
    root_parser = argparse.ArgumentParser(description=__doc__)
    commands = root_parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize a new .dv workflow")
    init.add_argument("--root", required=True)
    init.add_argument("--design-name", required=True)
    init.add_argument("--spec", required=True)
    init.add_argument("--rtl-filelist", required=True)
    init.add_argument(
        "--rtl-root",
        action="append",
        required=True,
        help="protected RTL source file or directory; repeat for multiple roots",
    )
    init.add_argument("--top", required=True)
    init.add_argument("--priority-order", default="P1,P2,P3")
    init.set_defaults(func=cmd_init)

    new_task = commands.add_parser("new-task", help="create an immutable task draft")
    new_task.add_argument("--root", required=True)
    new_task.add_argument("--task-id", required=True)
    new_task.add_argument("--role", choices=sorted(ROLE_ACTIONS), required=True)
    new_task.add_argument("--action", required=True)
    new_task.add_argument("--phase", choices=sorted(PHASES))
    new_task.add_argument("--lineage", required=True)
    new_task.add_argument("--retry-kind", choices=sorted(RETRY_LIMITS), required=True)
    new_task.add_argument("--input-revision")
    new_task.add_argument("--parent-task-id")
    new_task.set_defaults(func=cmd_new_task)

    seal = commands.add_parser("seal-task", help="validate and seal a task request")
    seal.add_argument("--root", required=True)
    seal.add_argument("--task-id", required=True)
    seal.set_defaults(func=cmd_seal_task)

    record = commands.add_parser("record-result", help="validate and record a worker result")
    record.add_argument("--root", required=True)
    record.add_argument("--task-id", required=True)
    record.set_defaults(func=cmd_record_result)

    transition = commands.add_parser("transition", help="move to an allowed workflow phase")
    transition.add_argument("--root", required=True)
    transition.add_argument("--to", required=True)
    transition.add_argument("--reason", required=True)
    transition.set_defaults(func=cmd_transition)

    item = commands.add_parser("set-item", help="create or update a V-plan work item")
    item.add_argument("--root", required=True)
    item.add_argument("--item-id", required=True)
    item.add_argument("--kind", choices=["FEATURE", "TEST", "COVERAGE"])
    item.add_argument("--priority")
    item.add_argument("--status", required=True)
    item.add_argument("--last-task-id")
    item.add_argument("--dependency", action="append")
    item.add_argument("--reason", required=True)
    item.set_defaults(func=cmd_set_item)

    add_blocker = commands.add_parser("add-blocker", help="record a workflow blocker")
    add_blocker.add_argument("--root", required=True)
    add_blocker.add_argument(
        "--kind",
        choices=["DUT", "SPEC", "ENVIRONMENT", "UNKNOWN", "RETRY_EXHAUSTED", "HUMAN_GATE"],
        required=True,
    )
    add_blocker.add_argument("--summary", required=True)
    add_blocker.add_argument("--related-id", action="append")
    add_blocker.set_defaults(func=cmd_add_blocker)

    resolve_blocker = commands.add_parser(
        "resolve-blocker", help="resolve or waive an open workflow blocker"
    )
    resolve_blocker.add_argument("--root", required=True)
    resolve_blocker.add_argument("--blocker-id", required=True)
    resolve_blocker.add_argument("--status", choices=["RESOLVED", "WAIVED"], required=True)
    resolve_blocker.add_argument("--resolution", required=True)
    resolve_blocker.set_defaults(func=cmd_resolve_blocker)

    add_fix = commands.add_parser("add-fix-request", help="record a confirmed DUT bug")
    add_fix.add_argument("--root", required=True)
    add_fix.add_argument("--failure-task-id", required=True)
    add_fix.add_argument("--diagnosis-task-id", required=True)
    add_fix.add_argument("--summary", required=True)
    add_fix.add_argument("--affected-id", action="append")
    add_fix.add_argument("--evidence", action="append")
    add_fix.set_defaults(func=cmd_add_fix_request)

    accept_rtl = commands.add_parser(
        "accept-rtl-update",
        help="record an external RTL update for an open DUT fix request",
    )
    accept_rtl.add_argument("--root", required=True)
    accept_rtl.add_argument("--fix-request-id", required=True)
    accept_rtl.add_argument("--expected-revision", required=True)
    accept_rtl.add_argument("--external-ref", required=True)
    accept_rtl.set_defaults(func=cmd_accept_rtl_update)

    resolve_fix = commands.add_parser("resolve-fix-request", help="resolve an open DUT fix request")
    resolve_fix.add_argument("--root", required=True)
    resolve_fix.add_argument("--fix-request-id", required=True)
    resolve_fix.add_argument("--verification-task-id", required=True)
    resolve_fix.add_argument("--resolution", required=True)
    resolve_fix.set_defaults(func=cmd_resolve_fix_request)

    approve = commands.add_parser("approve", help="record an explicit human gate decision")
    approve.add_argument("--root", required=True)
    approve.add_argument("--gate", required=True)
    approve.add_argument("--decision", choices=["APPROVED", "REJECTED", "WAIVED"], required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--note", required=True)
    approve.add_argument("--revision")
    approve.set_defaults(func=cmd_approve)

    show = commands.add_parser("show", help="print current workflow state")
    show.add_argument("--root", required=True)
    show.set_defaults(func=cmd_show)

    validate = commands.add_parser("validate", help="validate workflow state and task files")
    validate.add_argument("--root", required=True)
    validate.set_defaults(func=cmd_validate)

    return root_parser


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except FlowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
