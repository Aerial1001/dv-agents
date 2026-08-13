from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_repo import (  # noqa: E402
    REQUIRED_WORKERS,
    validate_agents,
    validate_repo,
    validate_schemas,
)


class RepositoryValidationTests(unittest.TestCase):
    def test_checked_in_repository_is_valid(self) -> None:
        self.assertEqual([], validate_repo(REPO_ROOT))

    def test_agents_reject_legacy_fields_and_agent_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_dir = root / "plugins" / "verification" / "agents"
            agent_dir.mkdir(parents=True)
            for name in REQUIRED_WORKERS:
                extra = "allowed-tools: Read\n" if name == "verification-builder" else ""
                tools = "Read, Agent" if name == "verification-runner" else "Read, Grep"
                (agent_dir / f"{name}.md").write_text(
                    "---\n"
                    f"name: {name}\n"
                    "description: Test worker\n"
                    "model: sonnet\n"
                    "color: blue\n"
                    f"tools: {tools}\n"
                    f"{extra}"
                    "---\n\n# Worker\n",
                    encoding="utf-8",
                )

            errors = validate_agents(root)
            self.assertTrue(any("allowed-tools" in error for error in errors))
            self.assertTrue(any("Agent tool" in error for error in errors))

    def test_schema_validation_reports_a_dangling_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema_dir = (
                root
                / "plugins"
                / "verification"
                / "skills"
                / "functional-verification"
                / "references"
            )
            schema_dir.mkdir(parents=True)
            roles = ["builder", "reviewer", "runner", "debugger"]
            base = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["role"],
                "properties": {"role": {"enum": roles}},
                "$defs": {"action": {"enum": ["TEST"]}},
            }
            request = dict(base, **{"$id": "urn:test:request"})
            request["properties"] = {
                "role": {"enum": roles},
                "broken": {"$ref": "#/$defs/missing"},
            }
            result = dict(base, **{"$id": "urn:test:result"})
            workflow = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:test:workflow",
                "type": "object",
                "required": ["status"],
                "properties": {"status": {"type": "string"}},
            }
            for name, schema in (
                ("task-request.schema.json", request),
                ("task-result.schema.json", result),
                ("workflow-state.schema.json", workflow),
            ):
                (schema_dir / name).write_text(json.dumps(schema), encoding="utf-8")

            errors = validate_schemas(root)
            self.assertTrue(any("unresolved '$ref'" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
