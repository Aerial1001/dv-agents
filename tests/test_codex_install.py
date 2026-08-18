from __future__ import annotations

import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL = REPO_ROOT / "install.sh"


class CodexInstallTests(unittest.TestCase):
    def test_codex_install_generates_custom_agents_and_thread_policy(self) -> None:
        expected = {
            "verification-builder": ("gpt-5.6-sol", "high", "workspace-write"),
            "verification-reviewer": ("gpt-5.6-terra", "high", "read-only"),
            "verification-runner": ("gpt-5.6-terra", "medium", "workspace-write"),
        }
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = subprocess.run(
                ["bash", str(INSTALL), "--ide", "codex", "--yes"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                result.returncode,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )

            instructions = (project / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("## Runner Thread Reuse", instructions)
            self.assertIn("same idle thread as follow-up turns", instructions)
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", instructions)

            agent_dir = project / ".codex" / "agents"
            self.assertEqual(
                {f"{name}.toml" for name in expected},
                {path.name for path in agent_dir.glob("*.toml")},
            )
            for name, (model, effort, sandbox) in expected.items():
                path = agent_dir / f"{name}.toml"
                config = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(name, config["name"])
                self.assertEqual(model, config["model"])
                self.assertEqual(effort, config["model_reasoning_effort"])
                self.assertEqual(sandbox, config["sandbox_mode"])
                self.assertIn("# Verification", config["developer_instructions"])
                self.assertNotIn(
                    "${CLAUDE_PLUGIN_ROOT}", config["developer_instructions"]
                )


if __name__ == "__main__":
    unittest.main()
