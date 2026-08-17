from __future__ import annotations

import json
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER = (
    REPO_ROOT / "plugins" / "verification" / "scripts" / "render_tables.py"
)
TEMPLATE_DIR = REPO_ROOT / "plugins" / "verification" / "template"

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    """Return {sheet_target: [[cell, ...], ...]} for one generated workbook."""
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [
                "".join(t.text or "" for t in si.iter(f"{{{MAIN}}}t"))
                for si in root.findall(f"{{{MAIN}}}si")
            ]
        sheets: dict[str, list[list[str]]] = {}
        for name in zf.namelist():
            if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
                continue
            root = ET.fromstring(zf.read(name))
            sheet_data = root.find(f"{{{MAIN}}}sheetData")
            rows: list[list[str]] = []
            for row in sheet_data:
                cells: list[str] = []
                for c in row:
                    t = c.get("t")
                    v = c.find(f"{{{MAIN}}}v")
                    if t == "s" and v is not None:
                        cells.append(shared[int(v.text)])
                    else:
                        cells.append(v.text if v is not None else "")
                rows.append(cells)
            sheets[name] = rows
        return sheets


class RenderTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api = runpy.run_path(str(RENDER))

    def _render(self, spec: dict[str, object], out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        spec_path = out / "tables.json"
        spec_path.write_text(json.dumps(spec) + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(RENDER),
                "render",
                "--template-dir",
                str(TEMPLATE_DIR),
                "--spec",
                str(spec_path),
                "--out",
                str(out),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, msg=result.stderr)

    def test_dump_reports_all_three_template_schemas(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RENDER), "dump", "--template-dir", str(TEMPLATE_DIR)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, msg=result.stderr)
        self.assertIn("CoverGroupName", result.stdout)
        self.assertIn("Unique Case Name", result.stdout)
        self.assertIn("L1 Feature", result.stdout)
        # The testlist template's Column1..ColumnN placeholders are filtered.
        self.assertNotIn("Column247", result.stdout)

    def test_read_template_columns(self) -> None:
        columns = {
            "testpoint": self.api["read_template"](
                TEMPLATE_DIR / "XXXX-UT-TestPoint.xlsx"
            )["columns"],
            "testlist": self.api["read_template"](
                TEMPLATE_DIR / "Bach_Testlist_template.xlsx"
            )["columns"],
            "covergroups": self.api["read_template"](
                TEMPLATE_DIR / "Bach_CoverGroups_XXXX.xlsx"
            )["columns"],
        }
        self.assertEqual(
            ["ID", "L1 Feature", "L2 Feature", "description", "Priority",
             "Platform", "Verify Level", "DV Owner", "Note"],
            columns["testpoint"],
        )
        self.assertEqual(
            ["Module", "Unique Case Name", "Unique C name", "Function Point",
             "Build Cfg", "DV Level", "Platform", "Test Steps/Procedure",
             "Checking Mechanism", "Status", "Priority", "Comment"],
            columns["testlist"],
        )
        self.assertEqual(
            ["ID", "CoverGroupName", "CoverPointName", "SignalName", "BinsType",
             "BinsValue", "Cross", "Dependency"],
            columns["covergroups"],
        )

    def test_render_round_trips_header_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._render(
                {
                    "dut": "axi2apb",
                    "testpoint": {
                        "template": "XXXX-UT-TestPoint.xlsx",
                        "rows": [
                            {
                                "ID": "TP-001",
                                "L1 Feature": "APB write",
                                "L2 Feature": "single beat",
                                "description": "verify single-beat APB write",
                                "Priority": "P0",
                                "Platform": "simulator",
                                "Verify Level": "subsystem",
                                "DV Owner": "alice",
                                "Note": "smoke",
                            }
                        ],
                    },
                    "testlist": {
                        "template": "Bach_Testlist_template.xlsx",
                        "rows": [
                            {
                                "Module": "axi2apb",
                                "Unique Case Name": "apb_wr_single",
                                "Unique C name": "apb_wr_single",
                                "Function Point": "write",
                                "Build Cfg": "cfg1",
                                "DV Level": "subsystem",
                                "Platform": "simulator",
                                "Test Steps/Procedure": "reset then write",
                                "Checking Mechanism": "scoreboard",
                                "Status": "draft",
                                "Priority": "P0",
                                "Comment": "",
                            }
                        ],
                    },
                    "covergroups": {
                        "template": "Bach_CoverGroups_XXXX.xlsx",
                        "rows": [
                            {
                                "ID": "CG-001",
                                "CoverGroupName": "cg_apb",
                                "CoverPointName": "cp_addr",
                                "SignalName": "paddr",
                                "BinsType": "auto",
                                "BinsValue": "",
                                "Cross": "",
                                "Dependency": "",
                            }
                        ],
                    },
                },
                out,
            )
            sheets = _read_xlsx(out / "testpoint.xlsx")
            sheet1 = sheets["xl/worksheets/sheet1.xml"]
            self.assertEqual(
                ["ID", "L1 Feature", "L2 Feature", "description", "Priority",
                 "Platform", "Verify Level", "DV Owner", "Note"],
                sheet1[0],
            )
            self.assertEqual("TP-001", sheet1[1][0])
            self.assertEqual("APB write", sheet1[1][1])
            # The note sheet is preserved with the template's column descriptions.
            note = sheets["xl/worksheets/sheet2.xml"]
            self.assertTrue(any("Priority" in c for row in note for c in row))

            testlist = _read_xlsx(out / "testlist.xlsx")["xl/worksheets/sheet1.xml"]
            self.assertEqual("apb_wr_single", testlist[1][1])
            self.assertEqual("scoreboard", testlist[1][8])

            covergroups = _read_xlsx(out / "covergroups.xlsx")["xl/worksheets/sheet1.xml"]
            self.assertEqual("CG-001", covergroups[1][0])
            self.assertEqual("cg_apb", covergroups[1][1])

            # The package must declare every part under the OOXML content-types
            # namespace so a real spreadsheet application can open it.
            ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
            with zipfile.ZipFile(out / "testpoint.xlsx") as zf:
                names = set(zf.namelist())
                types = ET.fromstring(zf.read("[Content_Types].xml"))
                self.assertEqual(f"{{{ct_ns}}}Types", types.tag)
                declared = {
                    o.get("PartName") for o in types.iter(f"{{{ct_ns}}}Override")
                }
                self.assertIn("/xl/worksheets/sheet2.xml", declared)

    def test_render_is_deterministic(self) -> None:
        spec = {
            "dut": "axi2apb",
            "testpoint": {
                "template": "XXXX-UT-TestPoint.xlsx",
                "rows": [{"ID": "TP-001", "L1 Feature": "registers", "Priority": "P0"}],
            },
            "testlist": {
                "template": "Bach_Testlist_template.xlsx",
                "rows": [{"Module": "axi2apb", "Unique Case Name": "t1"}],
            },
            "covergroups": {
                "template": "Bach_CoverGroups_XXXX.xlsx",
                "rows": [{"ID": "CG-001", "CoverGroupName": "cg", "CoverPointName": "cp", "SignalName": "s"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a"
            second = base / "b"
            self._render(spec, first)
            self._render(spec, second)
            for key in ("testpoint", "testlist", "covergroups"):
                self.assertEqual(
                    (first / f"{key}.xlsx").read_bytes(),
                    (second / f"{key}.xlsx").read_bytes(),
                )

    def test_extract_round_trips_and_honors_human_edits(self) -> None:
        spec = {
            "dut": "axi2apb",
            "testpoint": {
                "template": "XXXX-UT-TestPoint.xlsx",
                "rows": [
                    {
                        "ID": "TP-001",
                        "L1 Feature": "APB write",
                        "Priority": "P0",
                        "Platform": "simulator",
                        "Verify Level": "subsystem",
                    }
                ],
            },
            "testlist": {
                "template": "Bach_Testlist_template.xlsx",
                "rows": [{"Module": "axi2apb", "Unique Case Name": "t1"}],
            },
            "covergroups": {
                "template": "Bach_CoverGroups_XXXX.xlsx",
                "rows": [
                    {
                        "ID": "CG-001",
                        "CoverGroupName": "cg_apb",
                        "CoverPointName": "cp_addr",
                        "SignalName": "paddr",
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / "orig"
            original.mkdir()
            self._render(spec, original)

            # A human edits testpoint.xlsx in Excel: reprioritizes TP-001 and
            # adds a new row. This overwrites the delivered table in place.
            edited_rows = [
                {
                    "ID": "TP-001",
                    "L1 Feature": "APB write",
                    "Priority": "P2",
                    "Platform": "simulator",
                    "Verify Level": "subsystem",
                },
                {
                    "ID": "TP-002",
                    "L1 Feature": "APB read",
                    "Priority": "P1",
                    "Platform": "emulator",
                    "Verify Level": "unit",
                },
            ]
            self.api["render_table"](
                TEMPLATE_DIR / "XXXX-UT-TestPoint.xlsx",
                edited_rows,
                "axi2apb",
                original / "testpoint.xlsx",
            )

            # Fold the human edits back into tables.json.
            folded = base / "folded.json"
            result = subprocess.run(
                [
                    sys.executable, str(RENDER), "extract",
                    "--template-dir", str(TEMPLATE_DIR),
                    "--spec", str(original / "tables.json"),
                    "--xlsx-dir", str(original),
                    "--out", str(folded),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, msg=result.stderr)
            updated = json.loads(folded.read_text(encoding="utf-8"))
            got = updated["testpoint"]["rows"]
            self.assertEqual(2, len(got))
            self.assertEqual("TP-001", got[0]["ID"])
            self.assertEqual("P2", got[0]["Priority"])
            self.assertEqual("TP-002", got[1]["ID"])
            self.assertEqual("P1", got[1]["Priority"])
            self.assertEqual("emulator", got[1]["Platform"])
            # Untouched tables keep their data; empty cells stay empty.
            self.assertEqual("t1", updated["testlist"]["rows"][0]["Unique Case Name"])
            self.assertEqual(
                "cg_apb", updated["covergroups"]["rows"][0]["CoverGroupName"]
            )
            self.assertEqual("", got[0]["Note"])

            # Re-rendering from the folded spec reproduces the human's edits.
            # Rendered rows are sparse (empty cells carry no XML node), so
            # rebuild rows keyed by the absolute column reference.
            rerender = base / "rr"
            self._render(updated, rerender)
            columns = self.api["read_template"](
                TEMPLATE_DIR / "XXXX-UT-TestPoint.xlsx"
            )["columns"]
            with zipfile.ZipFile(rerender / "testpoint.xlsx") as zf:
                data_target = self.api["_read_sheets"](zf)[0][1]
                cells = self.api["_cell_values"](zf, data_target)
            grid: dict[tuple[int, int], str] = {}
            for ref, value in cells.items():
                col, row = self.api["_col_index"](ref)
                grid[(row, col)] = value
            max_row = max((row for row, _ in grid), default=-1)
            rerendered = [
                {
                    columns[col]: grid.get((row_index, col), "")
                    for col in range(len(columns))
                }
                for row_index in range(1, max_row + 1)
            ]
            self.assertEqual("TP-001", rerendered[0]["ID"])
            self.assertEqual("P2", rerendered[0]["Priority"])
            self.assertEqual("TP-002", rerendered[1]["ID"])
            self.assertEqual("P1", rerendered[1]["Priority"])
            self.assertEqual("emulator", rerendered[1]["Platform"])

    def test_extract_rejects_sheet_without_template_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            spec_path = base / "tables.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "dut": "axi2apb",
                        "testpoint": {
                            "template": "XXXX-UT-TestPoint.xlsx",
                            "rows": [],
                        },
                        "testlist": {
                            "template": "Bach_Testlist_template.xlsx",
                            "rows": [],
                        },
                        "covergroups": {
                            "template": "Bach_CoverGroups_XXXX.xlsx",
                            "rows": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            # Overwrite the testpoint table with a sheet whose header does not
            # match the template columns (for example a stray title row).
            data = self.api["_minimal_xlsx"](
                "test", ["Foo", "Bar"], [["x", "y"]], []
            )
            (base / "testpoint.xlsx").write_bytes(data)
            result = subprocess.run(
                [
                    sys.executable, str(RENDER), "extract",
                    "--template-dir", str(TEMPLATE_DIR),
                    "--spec", str(spec_path),
                    "--xlsx-dir", str(base),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("no data sheet matching template columns", result.stderr)

    def test_render_rejects_unknown_row_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            spec_path = out / "tables.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "dut": "axi2apb",
                        "testpoint": {
                            "template": "XXXX-UT-TestPoint.xlsx",
                            "rows": [{"ID": "TP-001", "NotAColumn": "x"}],
                        },
                        "testlist": {
                            "template": "Bach_Testlist_template.xlsx",
                            "rows": [],
                        },
                        "covergroups": {
                            "template": "Bach_CoverGroups_XXXX.xlsx",
                            "rows": [],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(RENDER), "render",
                 "--template-dir", str(TEMPLATE_DIR),
                 "--spec", str(spec_path), "--out", str(out)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("NotAColumn", result.stderr)


if __name__ == "__main__":
    unittest.main()
