#!/usr/bin/env python3
"""Zero-dependency structural static checks for SystemVerilog sources.

Designed to run on a machine with **no simulator or EDA tools installed**
(python3 only). It is a bounded, heuristic checker, not a parser: it strips
comments (and, for keyword checks, string literals), balances the unambiguous
structural keyword blocks, resolves ``include`` files, cross-references
``uvm_config_db`` set/get pairs, and flags ``virtual`` interface-type
references.

Findings are printed as ``path:line: LEVEL: message`` with LEVEL in
{ERROR, WARNING, INFO}. Exit codes: 0 clean, 1 has ERROR, 2 warnings only.

Known limitations (documented so the builder can weight the result):
- ``begin``/``end`` pairs are intentionally not tracked: in SystemVerilog a
  task/function may close with plain ``end`` and prototypes carry no body, so a
  naive stack would misreport. The runner's compile remains the authority.
- A block closed with plain ``end : label`` instead of its end-keyword (e.g.
  ``end : modname`` for ``endmodule``) is not recognized and is reported as
  unclosed. This is rare in verification sources.
- config_db matching is by field name only and scans the given sources; a
  ``get`` without a ``set`` is a WARNING because the ``set`` may live in an
  unscanned file or be built at runtime.

Usage:
  python3 static_check.py --dir <verification_root> [--dir ...]
      [--files a.sv] [--files b.svh] [--include-dir <rtl_or_pkg_root>]...
      [--interface-file <file declaring interfaces>]...
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterator

SV_SUFFIXES = {".sv", ".svh", ".v", ".vh", ".pkg"}

# Unambiguous structural keyword pairs (see module docstring for what is left out).
BLOCK_OPEN = {
    "module": "endmodule",
    "interface": "endinterface",
    "package": "endpackage",
    "class": "endclass",
    "program": "endprogram",
    "checker": "endchecker",
    "clocking": "endclocking",
    "property": "endproperty",
    "sequence": "endsequence",
    "specify": "endspecify",
    "table": "endtable",
    "config": "endconfig",
    "primitive": "endprimitive",
    "generate": "endgenerate",
    "case": "endcase",
    "casez": "endcase",
    "casex": "endcase",
    "randcase": "endcase",
    "fork": "join",
}
JOIN_CLOSES = {"join", "join_any", "join_none"}
# `assert/cover/assume/restrict property (…)` is a statement, not a property
# declaration; `property` there must not open a block.
STATEMENT_PROPERTY_PREFIXES = {"assert", "cover", "assume", "restrict"}
OPEN_WORDS = set(BLOCK_OPEN)
CLOSE_TO_OPEN: dict[str, str] = {}
for _open, _close in BLOCK_OPEN.items():
    CLOSE_TO_OPEN.setdefault(_close, _open)
CLOSE_TO_OPEN["join_any"] = "fork"
CLOSE_TO_OPEN["join_none"] = "fork"

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
CONFIG_RE = re.compile(
    r"\buvm_config_db\s*#\s*\([^)]*\)\s*::\s*(set|get)\s*\(\s*"
    r"[^,]*\s*,\s*[^,]*\s*,\s*\"([^\"]+)\"",
    re.DOTALL,
)
INCLUDE_RE = re.compile(r"^\s*`include\s+\"([^\"]+)\"")
INTERFACE_RE = re.compile(
    r"\binterface\s+(?!class\b)([A-Za-z_][A-Za-z0-9_$]*)"
)
VIRTUAL_RE = re.compile(
    r"\bvirtual\s+(?!class\b|function\b|task\b|interface\b|modport\b)"
    r"([A-Za-z_][A-Za-z0-9_$]*)"
)


def strip_comments_and_strings(text: str, keep_strings: bool = False) -> str:
    """Replace comments (and, unless ``keep_strings``, string literals) with
    spaces, keeping newlines.

    ``keep_strings=True`` preserves string-literal contents verbatim so that
    checks which need those contents (``include`` paths, config_db field
    names) can still see them while comments stay masked.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    state = "code"
    while i < n:
        char = text[i]
        if state == "code":
            if text.startswith("//", i):
                newline = text.find("\n", i)
                i = n if newline == -1 else newline
                continue
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end == -1:
                    out.extend(
                        "\n" if text[k] == "\n" else " " for k in range(i, n)
                    )
                    break
                out.extend(
                    "\n" if text[k] == "\n" else " " for k in range(i, end + 2)
                )
                i = end + 2
                continue
            if char == '"':
                state = "string"
                out.append('"' if keep_strings else " ")
                i += 1
                continue
            out.append(char)
            i += 1
        else:  # string
            if char == "\\":
                if keep_strings:
                    out.append("\\")
                    if i + 1 < n:
                        out.append(text[i + 1])
                        i += 2
                    else:
                        i += 1
                else:
                    i += 2
                continue
            if char == '"':
                state = "code"
                out.append('"' if keep_strings else " ")
                i += 1
                continue
            out.append(char if keep_strings else " ")
            i += 1
    return "".join(out)


def iter_words(stripped: str) -> Iterator[tuple[str, int]]:
    for line_no, line in enumerate(stripped.split("\n"), start=1):
        for match in WORD_RE.finditer(line):
            yield match.group(0), line_no


def _finding(
    findings: list[dict[str, Any]],
    path: Path,
    line: int,
    level: str,
    message: str,
) -> None:
    findings.append(
        {"file": str(path), "line": line, "level": level, "message": message}
    )


def check_blocks(path: Path, stripped: str, findings: list[dict[str, Any]]) -> None:
    tokens = list(iter_words(stripped))
    stack: list[tuple[str, int]] = []
    i = 0
    while i < len(tokens):
        word, line = tokens[i]
        if word == "interface" and i + 1 < len(tokens) and tokens[i + 1][0] == "class":
            i += 2
            continue
        if word in OPEN_WORDS:
            if (
                word == "property"
                and i > 0
                and tokens[i - 1][0] in STATEMENT_PROPERTY_PREFIXES
            ):
                i += 1
                continue
            stack.append((word, line))
        elif word in JOIN_CLOSES:
            if not stack or stack[-1][0] != "fork":
                _finding(findings, path, line, "ERROR", f"'{word}' without a matching 'fork'")
            else:
                stack.pop()
        elif word in CLOSE_TO_OPEN:
            expected = CLOSE_TO_OPEN[word]
            if not stack:
                _finding(
                    findings,
                    path,
                    line,
                    "ERROR",
                    f"'{word}' without a matching '{expected}'",
                )
            elif stack[-1][0] == expected:
                stack.pop()
            else:
                _finding(
                    findings,
                    path,
                    line,
                    "ERROR",
                    f"'{word}' closes '{expected}' but '{stack[-1][0]}' "
                    f"opened at line {stack[-1][1]}",
                )
        i += 1
    for word, line in stack:
        level = "WARNING" if word == "generate" else "ERROR"
        _finding(
            findings,
            path,
            line,
            level,
            f"unclosed '{word}' (expected {BLOCK_OPEN[word]})",
        )


def check_preprocessor(path: Path, stripped: str, findings: list[dict[str, Any]]) -> None:
    stack: list[tuple[str, int]] = []
    for line_no, line in enumerate(stripped.split("\n"), start=1):
        match = re.match(r"^\s*`(ifdef|ifndef)\b", line)
        if match:
            stack.append((match.group(1), line_no))
            continue
        if re.match(r"^\s*`endif\b", line):
            if stack:
                stack.pop()
            else:
                _finding(
                    findings,
                    path,
                    line_no,
                    "ERROR",
                    "`endif without a matching `ifdef/`ifndef",
                )
    for kind, line_no in stack:
        _finding(findings, path, line_no, "WARNING", f"unclosed `{kind} (missing `endif)")


def check_includes(
    path: Path,
    kept: str,
    include_dirs: list[Path],
    findings: list[dict[str, Any]],
) -> None:
    base = path.parent
    for line_no, line in enumerate(kept.split("\n"), start=1):
        match = INCLUDE_RE.match(line)
        if not match:
            continue
        target = match.group(1)
        if (base / target).is_file():
            continue
        if any((directory / target).is_file() for directory in include_dirs):
            continue
        _finding(
            findings,
            path,
            line_no,
            "ERROR",
            f"`include \"{target}\" not found",
        )


def collect_config(
    path: Path, kept: str
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    sets: list[tuple[str, int]] = []
    gets: list[tuple[str, int]] = []
    for match in CONFIG_RE.finditer(kept):
        kind, field = match.group(1), match.group(2)
        line = kept.count("\n", 0, match.start()) + 1
        (sets if kind == "set" else gets).append((field, line))
    return sets, gets


def check_config_pairs(
    path: Path,
    kept: str,
    set_fields: set[str],
    get_fields: set[str],
    findings: list[dict[str, Any]],
) -> None:
    for field, line in collect_config(path, kept)[1]:
        if field not in set_fields:
            _finding(
                findings,
                path,
                line,
                "WARNING",
                f"uvm_config_db::get(\"{field}\") has no matching ::set with that "
                "field in scanned sources (possible null at time 0)",
            )
    for field, line in collect_config(path, kept)[0]:
        if field not in get_fields:
            _finding(
                findings,
                path,
                line,
                "INFO",
                f"uvm_config_db::set(\"{field}\") is never read",
            )


def check_virtual_types(
    path: Path,
    stripped: str,
    interfaces: set[str],
    findings: list[dict[str, Any]],
) -> None:
    for match in VIRTUAL_RE.finditer(stripped):
        ident = match.group(1)
        line = stripped.count("\n", 0, match.start()) + 1
        if ident not in interfaces:
            _finding(
                findings,
                path,
                line,
                "WARNING",
                f"virtual type '{ident}' is not declared as an interface in the "
                "scanned sources (declare it or pass --interface-file)",
            )


def collect_files(dirs: list[Path], files: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in files:
        resolved = path.resolve()
        if resolved.is_file() and resolved.suffix in SV_SUFFIXES:
            result.append(resolved)
    for directory in dirs:
        root = directory.resolve()
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob("*")):
            if candidate.suffix not in SV_SUFFIXES:
                continue
            parts = candidate.parts
            if any(part.startswith(".") or part == ".dv" for part in parts):
                continue
            result.append(candidate.resolve())
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in result:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def scan_sources(
    sources: list[Path],
    include_dirs: list[Path] | None = None,
    interface_files: list[Path] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    include_dirs = include_dirs or []
    interface_files = interface_files or []

    all_set_fields: set[str] = set()
    all_get_fields: set[str] = set()
    interfaces: set[str] = set()

    stripped_by_path: dict[Path, str] = {}
    kept_by_path: dict[Path, str] = {}

    for path in interface_files:
        resolved = path.resolve()
        if not resolved.is_file():
            continue
        text = resolved.read_text(encoding="utf-8", errors="replace")
        stripped = strip_comments_and_strings(text)
        stripped_by_path[resolved] = stripped
        kept_by_path[resolved] = strip_comments_and_strings(text, keep_strings=True)
        interfaces.update(INTERFACE_RE.findall(stripped))

    for path in sources:
        text = path.read_text(encoding="utf-8", errors="replace")
        stripped = strip_comments_and_strings(text)
        kept = strip_comments_and_strings(text, keep_strings=True)
        stripped_by_path[path] = stripped
        kept_by_path[path] = kept
        check_blocks(path, stripped, findings)
        check_preprocessor(path, stripped, findings)
        check_includes(path, kept, include_dirs, findings)
        sets, gets = collect_config(path, kept)
        all_set_fields.update(field for field, _ in sets)
        all_get_fields.update(field for field, _ in gets)
        interfaces.update(INTERFACE_RE.findall(stripped))

    for path in sources:
        check_config_pairs(path, kept_by_path[path], all_set_fields, all_get_fields, findings)
        check_virtual_types(path, stripped_by_path[path], interfaces, findings)

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir", action="append", default=[], metavar="PATH",
        help="recursively scan this directory for SV sources (repeatable)",
    )
    parser.add_argument(
        "--files", action="append", default=[], metavar="PATH",
        help="explicit SV source file (repeatable)",
    )
    parser.add_argument(
        "--include-dir", action="append", default=[], metavar="PATH",
        help="extra directory searched for `include targets (repeatable)",
    )
    parser.add_argument(
        "--interface-file", action="append", default=[], metavar="PATH",
        help="file declaring interfaces, e.g. an RTL filelist entry (repeatable)",
    )
    args = parser.parse_args(argv)

    dirs = [Path(value) for value in args.dir]
    files = [Path(value) for value in args.files]
    if not dirs and not files:
        dirs = [Path(".")]
    include_dirs = [Path(value) for value in args.include_dir]
    interface_files = [Path(value) for value in args.interface_file]

    sources = collect_files(dirs, files)
    findings = scan_sources(sources, include_dirs, interface_files)

    for finding in findings:
        print(
            f"{finding['file']}:{finding['line']}: "
            f"{finding['level']}: {finding['message']}"
        )
    errors = sum(1 for finding in findings if finding["level"] == "ERROR")
    warnings = sum(1 for finding in findings if finding["level"] == "WARNING")
    print(
        f"static_check: {len(sources)} file(s), "
        f"{errors} error(s), {warnings} warning(s)",
        file=sys.stderr,
    )
    if errors:
        return 1
    if warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
