from __future__ import annotations

import runpy
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "plugins" / "verification" / "scripts" / "static_check.py"


class StaticCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.api = runpy.run_path(str(STATIC))
        self.scan = self.api["scan_sources"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def errors(self, findings: list[dict]) -> list[dict]:
        return [finding for finding in findings if finding["level"] == "ERROR"]

    def warnings(self, findings: list[dict]) -> list[dict]:
        return [finding for finding in findings if finding["level"] == "WARNING"]

    def test_clean_module_produces_no_errors(self) -> None:
        a = self.write(
            "env.sv",
            (
                "`include \"pkg.svh\"\n"
                "module env;\n"
                "  interface my_if; logic clk; endinterface\n"
                "  virtual my_if vif;\n"
                "  initial begin\n"
                "    uvm_config_db#(virtual my_if)::set(null, \"*\", \"vif\", vif);\n"
                "  end\n"
                "endmodule\n"
            ),
        )
        pkg = self.write("pkg.svh", "package pkg; endpackage\n")
        findings = self.scan([a, pkg])
        self.assertEqual([], self.errors(findings))
        self.assertEqual([], self.warnings(findings))

    def test_get_without_set_is_warned(self) -> None:
        agent = self.write(
            "agent.sv",
            (
                "module agent;\n"
                "  virtual my_if vif;\n"
                "  function void build_phase();\n"
                "    uvm_config_db#(virtual my_if)::get(this, \"\", \"vif\", vif);\n"
                "  endfunction\n"
                "endmodule\n"
            ),
        )
        env = self.write(
            "my_if.sv",
            "interface my_if; logic clk; endinterface\n",
        )
        findings = self.scan([agent, env])
        self.assertEqual([], self.errors(findings))
        self.assertEqual(1, len(self.warnings(findings)))
        self.assertIn("no matching ::set", self.warnings(findings)[0]["message"])

    def test_missing_include_is_an_error(self) -> None:
        top = self.write("top.sv", "`include \"missing.svh\"\nmodule top; endmodule\n")
        findings = self.scan([top])
        self.assertEqual(1, len(self.errors(findings)))
        self.assertIn("missing.svh", self.errors(findings)[0]["message"])

    def test_unbalanced_module_is_an_error(self) -> None:
        broken = self.write("broken.sv", "module broken;\n  logic x;\n")
        findings = self.scan([broken])
        self.assertEqual(1, len(self.errors(findings)))
        self.assertIn("unclosed 'module'", self.errors(findings)[0]["message"])

    def test_keywords_inside_comments_are_ignored(self) -> None:
        source = self.write(
            "commented.sv",
            (
                "// module fake; unclosed by design\n"
                "/* interface fake; endinterface\n"
                "   \"module still inside comment\"\n"
                "*/\n"
                "module real;\n"
                "  string s = \"module not_a_block; endmodule\";\n"
                "endmodule\n"
            ),
        )
        findings = self.scan([source])
        self.assertEqual([], self.errors(findings))

    def test_unbalanced_case_is_an_error(self) -> None:
        source = self.write(
            "case.sv",
            "module m;\n  always @* begin\n    case (x)\n      1: y = 1;\n    endcase\n  end\nendmodule\n",
        )
        findings = self.scan([source])
        self.assertEqual([], self.errors(findings))

    def test_assert_cover_property_statements_do_not_open_blocks(self) -> None:
        source = self.write(
            "props.sv",
            (
                "module props;\n"
                "  property p_req;\n"
                "    @(posedge clk) req |-> ack;\n"
                "  endproperty\n"
                "  a_req: assert property (p_req);\n"
                "  a_req2: assert property (@(posedge clk) req |-> ack);\n"
                "  c_req: cover property (p_req);\n"
                "  assume property (@(posedge clk) !rst_n |-> !req);\n"
                "  restrict property (@(posedge clk) req |-> !busy);\n"
                "endmodule\n"
            ),
        )
        findings = self.scan([source])
        self.assertEqual([], self.errors(findings))


if __name__ == "__main__":
    unittest.main()
