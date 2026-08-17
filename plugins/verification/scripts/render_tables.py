#!/usr/bin/env python3
"""Render DV plan tables (testpoint / testlist / covergroups) from xlsx templates.

Dependency-free: uses only the standard library (zipfile + xml.etree.ElementTree).

Three subcommands:

  dump    - print the sheet names, data-sheet header columns, and note-sheet text
            of every .xlsx template in a directory (plain text, for the builder
            to learn the column schema before filling tables.json).

  render  - write three populated .xlsx files from a JSON spec plus the templates.
            Output is deterministic: given the same templates and spec, the bytes
            are identical (no timestamps or random identifiers are embedded), so
            the produced files can be revision-tracked reproducibly.

  extract - read human-edited .xlsx files back into tables.json. This is the
            reverse direction: after a human reviews and edits the delivered
            tables in Excel, the edits are folded back into the JSON text source
            (which reviewers and the workflow consume) instead of being lost.
            The three files are matched by their template header columns; every
            data row becomes one object keyed by those columns.

The JSON spec shape (tables.json):

  {
    "dut": "axi2apb",
    "testpoint":   {"template": "XXXX-UT-TestPoint.xlsx",   "rows": [ {...} ]},
    "testlist":    {"template": "Bach_Testlist_template.xlsx", "rows": [ {...} ]},
    "covergroups": {"template": "Bach_CoverGroups_XXXX.xlsx",  "rows": [ {...} ]}
  }

Each row is an object keyed by the template's header column name. Missing keys
render as empty cells; unknown keys are an error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

TABLE_KEYS = ("testpoint", "testlist", "covergroups")

# Default template directory: <plugin-root>/template, i.e. the sibling of the
# scripts/ directory this file lives in. The builder can rely on this when the
# plugin is installed at the standard location and does not need to spell the
# path; pass --template-dir explicitly to override.
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"


class RenderError(RuntimeError):
    pass


# ── Reading ──────────────────────────────────────────────────────────────────

def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in si.iter(f"{{{MAIN}}}t"))
        for si in root.findall(f"{{{MAIN}}}si")
    ]


def _cell_values(zf: zipfile.ZipFile, sheet_target: str) -> dict[str, str]:
    """Return {cell_ref: text} for one worksheet, resolving shared strings."""
    shared = _read_shared_strings(zf)
    root = ET.fromstring(zf.read(sheet_target))
    cells: dict[str, str] = {}
    for c in root.iter(f"{{{MAIN}}}c"):
        ref = c.get("r")
        if ref is None:
            continue
        t = c.get("t")
        v = c.find(f"{{{MAIN}}}v")
        is_el = c.find(f"{{{MAIN}}}is")
        if t == "s" and v is not None:
            value = shared[int(v.text)]
        elif t == "inlineStr" and is_el is not None:
            value = "".join(x.text or "" for x in is_el.iter(f"{{{MAIN}}}t"))
        elif v is not None:
            value = v.text or ""
        else:
            value = ""
        if value.strip():
            cells[ref] = value
    return cells


def _col_index(ref: str) -> tuple[int, int]:
    letters = "".join(ch for ch in ref if ch.isalpha())
    digits = "".join(ch for ch in ref if ch.isdigit())
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch.upper()) - ord("A") + 1)
    return col - 1, int(digits) - 1


def _read_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets = wb.find(f"{{{MAIN}}}sheets")
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    result = []
    for s in sheets:
        name = s.get("name")
        rid = s.get(f"{{{DOC_REL}}}id")
        target = relmap[rid]
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        result.append((name, target))
    return result


def read_template(path: Path) -> dict[str, object]:
    """Extract the data-sheet header and any note-sheet text from a template.

    The data sheet is the sheet whose first row has the most non-empty header
    cells; every other sheet with content is treated as a note sheet.
    """
    with zipfile.ZipFile(path) as zf:
        sheets = _read_sheets(zf)
        scored: list[tuple[int, str, str, dict[str, str]]] = []
        for name, target in sheets:
            cells = _cell_values(zf, target)
            header_count = sum(1 for ref, v in cells.items() if _col_index(ref)[1] == 0)
            scored.append((header_count, name, target, cells))

        if not scored:
            raise RenderError(f"template has no sheets: {path.name}")

        scored.sort(key=lambda item: item[0], reverse=True)
        _, data_name, data_target, data_cells = scored[0]

        header: list[str] = []
        for ref, value in data_cells.items():
            col, row = _col_index(ref)
            if row == 0:
                stripped = value.strip()
                # Drop the template's trailing "Column1..ColumnN" placeholder
                # cells that mark an unused, pre-styled grid area.
                if stripped and not re.fullmatch(r"Column\d+", stripped):
                    header.append((col, stripped))
        header.sort(key=lambda item: item[0])
        columns = [value for _, value in header]

        notes: list[tuple[str, list[str]]] = []
        for _, name, _, cells in scored[1:]:
            lines = [value for _, value in sorted(cells.items()) if value.strip()]
            if lines:
                notes.append((name, lines))

    return {
        "data_sheet": data_name,
        "columns": columns,
        "notes": notes,
    }


def _dump_template(path: Path) -> str:
    info = read_template(path)
    lines = [f"===== {path.name} ====="]
    lines.append(f"data sheet: {info['data_sheet']}")
    lines.append("columns: " + " | ".join(info["columns"]))
    for name, text in info["notes"]:
        lines.append(f"note sheet '{name}':")
        lines.extend(f"  {line}" for line in text)
    return "\n".join(lines)


# ── Writing ──────────────────────────────────────────────────────────────────

def _col_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _minimal_xlsx(
    data_sheet_name: str,
    columns: list[str],
    rows: list[list[str]],
    note_lines: list[str],
) -> bytes:
    """Build a minimal, deterministic .xlsx (inline strings, no docProps)."""
    shared: list[str] = []
    index: dict[str, int] = {}

    def sid(value: str) -> int:
        if value not in index:
            index[value] = len(shared)
            shared.append(value)
        return index[value]

    def sheet_xml(header: list[str], body: list[list[str]]) -> str:
        rows: list[str] = []
        header_cells = [
            f'<c r="{_col_letter(col)}1" t="s"><v>{sid(text)}</v></c>'
            for col, text in enumerate(header)
            if text
        ]
        rows.append('<row r="1">' + "".join(header_cells) + "</row>")
        for row_idx, row in enumerate(body, start=2):
            data_cells = [
                f'<c r="{_col_letter(col)}{row_idx}" t="s"><v>{sid(text)}</v></c>'
                for col, text in enumerate(row)
                if text
            ]
            rows.append(f'<row r="{row_idx}">' + "".join(data_cells) + "</row>")
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="' + MAIN + '"><sheetData>'
            + "".join(rows)
            + "</sheetData></worksheet>"
        )

    def note_sheet_xml(lines: list[str]) -> str:
        rows = [
            f'<row r="{idx}"><c r="A{idx}" t="s"><v>{sid(line)}</v></c></row>'
            for idx, line in enumerate(lines, start=1)
        ]
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="' + MAIN + '"><sheetData>'
            + "".join(rows)
            + "</sheetData></worksheet>"
        )

    # Build the sheet XML first so the shared-string table is fully populated.
    sheet1 = sheet_xml(columns, rows)
    sheet2 = note_sheet_xml(note_lines) if note_lines else None

    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="' + MAIN + '" count="' + str(len(shared)) + '" '
        'uniqueCount="' + str(len(shared)) + '">'
        + "".join(f"<si><t>{_escape(s)}</t></si>" for s in shared)
        + "</sst>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="' + MAIN + '">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        "</styleSheet>"
    )

    workbook_xml_sheets = (
        f'<sheet name="{_escape(data_sheet_name)}" sheetId="1" r:id="rId1"/>'
    )
    rels_entries = [
        '<Relationship Id="rId1" Type="' + DOC_REL
        + '/worksheet" Target="worksheets/sheet1.xml"/>',
        '<Relationship Id="rId2" Type="' + DOC_REL
        + '/sharedStrings" Target="sharedStrings.xml"/>',
        '<Relationship Id="rId3" Type="' + DOC_REL + '/styles" Target="styles.xml"/>',
    ]
    content_types_overrides = [
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>',
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    members: list[tuple[str, str]] = [
        ("xl/worksheets/sheet1.xml", sheet1),
        ("xl/sharedStrings.xml", shared_xml),
        ("xl/styles.xml", styles_xml),
    ]
    if sheet2 is not None:
        workbook_xml_sheets += (
            '<sheet name="Note" sheetId="2" r:id="rId4"/>'
        )
        rels_entries.append(
            '<Relationship Id="rId4" Type="' + DOC_REL
            + '/worksheet" Target="worksheets/sheet2.xml"/>'
        )
        content_types_overrides.append(
            '<Override PartName="/xl/worksheets/sheet2.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        members.append(("xl/worksheets/sheet2.xml", sheet2))

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="' + MAIN + '" xmlns:r="' + DOC_REL + '"><sheets>'
        + workbook_xml_sheets
        + "</sheets></workbook>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="' + PKG_REL + '">'
        + "".join(rels_entries)
        + "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="' + CONTENT_TYPES + '">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(content_types_overrides)
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="' + PKG_REL + '">'
        '<Relationship Id="rId1" Type="' + DOC_REL
        + '/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )

    members += [
        ("xl/workbook.xml", workbook_xml),
        ("xl/_rels/workbook.xml.rels", rels_xml),
        ("[Content_Types].xml", content_types_xml),
        ("_rels/.rels", root_rels),
    ]
    return _zip_bytes(members)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _zip_bytes(members: list[tuple[str, str]]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members:
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buffer.getvalue()


# ── Extract (reverse render) ────────────────────────────────────────────────

def extract_rows_from_xlsx(
    xlsx_path: Path, template_path: Path
) -> list[dict[str, str]]:
    """Read one rendered xlsx back into rows keyed by the template's columns.

    The data sheet is the sheet whose header row equals the template columns;
    every later row becomes one object keyed by those column names. Rows that
    are entirely empty are dropped, so a human deleting rows or leaving blank
    trailing rows round-trips cleanly. A sheet whose header does not match the
    template columns (for example a stray title row inserted above the header)
    is rejected rather than mis-parsed.
    """
    columns = read_template(template_path)["columns"]
    if not columns:
        raise RenderError(f"template has no header columns: {template_path.name}")
    with zipfile.ZipFile(xlsx_path) as zf:
        sheets = _read_sheets(zf)
        candidates: list[tuple[bool, int, str, dict[str, str]]] = []
        for _name, target in sheets:
            cells = _cell_values(zf, target)
            header: dict[int, str] = {}
            for ref, value in cells.items():
                col, row = _col_index(ref)
                if row == 0 and value.strip():
                    header[col] = value.strip()
            matched = all(
                header.get(col) == columns[col] for col in range(len(columns))
            )
            candidates.append((matched, len(header), target, cells))
        if not candidates:
            raise RenderError(f"{xlsx_path.name} has no sheets")
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        matched, _, target, cells = candidates[0]
        if not matched:
            raise RenderError(
                f"{xlsx_path.name} has no data sheet matching template columns "
                + ", ".join(columns)
            )
        grid: dict[tuple[int, int], str] = {}
        for ref, value in cells.items():
            col, row = _col_index(ref)
            grid[(row, col)] = value
        max_row = max((row for row, _ in grid), default=-1)
        rows: list[dict[str, str]] = []
        for row_index in range(1, max_row + 1):
            row = {
                columns[col]: grid.get((row_index, col), "")
                for col in range(len(columns))
            }
            if any(value.strip() for value in row.values()):
                rows.append(row)
        return rows


def cmd_extract(args: argparse.Namespace) -> None:
    template_dir = Path(args.template_dir)
    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise RenderError("spec must be a JSON object")
    xlsx_dir = Path(args.xlsx_dir)
    for key in TABLE_KEYS:
        table = spec.get(key)
        if not isinstance(table, dict):
            raise RenderError(f"spec.{key} must be an object")
        template = table.get("template")
        if not isinstance(template, str) or not template:
            raise RenderError(f"spec.{key}.template must name a template file")
        template_path = template_dir / template
        if not template_path.is_file():
            raise RenderError(f"template not found: {template_path}")
        xlsx_path = xlsx_dir / f"{key}.xlsx"
        if not xlsx_path.is_file():
            raise RenderError(f"edited table not found: {xlsx_path}")
        table["rows"] = extract_rows_from_xlsx(xlsx_path, template_path)
    serialized = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    out_path = Path(args.out) if args.out else spec_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(serialized, encoding="utf-8")
    print(out_path)


# ── Render ───────────────────────────────────────────────────────────────────

def render_table(
    template_path: Path,
    rows: list[dict[str, str]],
    dut: str,
    out_path: Path,
) -> None:
    info = read_template(template_path)
    columns = info["columns"]
    if not columns:
        raise RenderError(f"template has no header columns: {template_path.name}")
    for row in rows:
        unknown = sorted(set(row) - set(columns))
        if unknown:
            raise RenderError(
                f"row key(s) not in template columns {columns}: {unknown}"
            )
    body = [[row.get(col, "") for col in columns] for row in rows]
    note_lines: list[str] = []
    for _, lines in info["notes"]:
        note_lines.extend(lines)
    data_sheet_name = dut or info["data_sheet"].replace("XXXX", "")
    data = _minimal_xlsx(data_sheet_name, columns, body, note_lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)


def cmd_dump(args: argparse.Namespace) -> None:
    template_dir = Path(args.template_dir)
    templates = sorted(template_dir.glob("*.xlsx"))
    if not templates:
        raise RenderError(f"no .xlsx templates in {template_dir}")
    for path in templates:
        print(_dump_template(path))
        print()


def cmd_render(args: argparse.Namespace) -> None:
    template_dir = Path(args.template_dir)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise RenderError("spec must be a JSON object")
    dut = str(spec.get("dut") or "").strip()
    out_dir = Path(args.out)
    for key in TABLE_KEYS:
        table = spec.get(key)
        if table is None:
            raise RenderError(f"spec is missing required table: {key}")
        if not isinstance(table, dict):
            raise RenderError(f"spec.{key} must be an object")
        template = table.get("template")
        rows = table.get("rows")
        if not isinstance(template, str) or not template:
            raise RenderError(f"spec.{key}.template must name a template file")
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise RenderError(f"spec.{key}.rows must be an array of objects")
        template_path = template_dir / template
        if not template_path.is_file():
            raise RenderError(f"template not found: {template_path}")
        render_table(template_path, rows, dut, out_dir / f"{key}.xlsx")
    print(out_dir)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="render_tables.py")
    sub = p.add_subparsers(dest="command", required=True)
    dump = sub.add_parser("dump", help="print template sheet/column schema")
    dump.add_argument(
        "--template-dir", default=str(DEFAULT_TEMPLATE_DIR),
        help="directory holding the xlsx templates (default: plugin template dir)",
    )
    dump.set_defaults(func=cmd_dump)
    render = sub.add_parser("render", help="write populated xlsx from a JSON spec")
    render.add_argument(
        "--template-dir", default=str(DEFAULT_TEMPLATE_DIR),
        help="directory holding the xlsx templates (default: plugin template dir)",
    )
    render.add_argument("--spec", required=True)
    render.add_argument("--out", required=True)
    render.set_defaults(func=cmd_render)
    extract = sub.add_parser(
        "extract",
        help="fold human-edited xlsx back into the JSON tables spec",
    )
    extract.add_argument(
        "--template-dir", default=str(DEFAULT_TEMPLATE_DIR),
        help="directory holding the xlsx templates (default: plugin template dir)",
    )
    extract.add_argument("--spec", required=True)
    extract.add_argument("--xlsx-dir", required=True)
    extract.add_argument(
        "--out",
        default=None,
        help="write the updated spec here (default: overwrite --spec in place)",
    )
    extract.set_defaults(func=cmd_extract)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except (RenderError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
