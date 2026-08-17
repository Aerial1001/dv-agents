#!/usr/bin/env python3
"""Static, dependency-free validation for the dv-agents repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urldefrag, urlparse


REQUIRED_WORKERS = {
    "verification-builder",
    "verification-reviewer",
    "verification-runner",
}
REQUIRED_AGENT_FIELDS = {"name", "description", "model", "color", "tools"}
FORBIDDEN_AGENT_FIELDS = {"allowed-tools", "subagents"}
REQUIRED_SCHEMAS = (
    "task-request.schema.json",
    "task-result.schema.json",
    "workflow-state.schema.json",
)
SCHEMA_DIR = Path(
    "plugins/verification/skills/functional-verification/references"
)
SKILL_PATH = Path(
    "plugins/verification/skills/functional-verification/SKILL.md"
)


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_json(path: Path, root: Path, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{_display(path, root)}: file is missing")
    except UnicodeDecodeError as exc:
        errors.append(f"{_display(path, root)}: is not UTF-8: {exc}")
    except json.JSONDecodeError as exc:
        errors.append(
            f"{_display(path, root)}:{exc.lineno}:{exc.colno}: invalid JSON: "
            f"{exc.msg}"
        )
    return None


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list_of_strings(value: Any) -> bool:
    return isinstance(value, list) and all(_nonempty_string(item) for item in value)


def _resolve_repo_path(
    root: Path,
    base: Path,
    raw: str,
    owner: str,
    errors: list[str],
) -> Path | None:
    candidate = (base / raw).resolve()
    if not _inside(candidate, root):
        errors.append(f"{owner}: path escapes repository: {raw!r}")
        return None
    return candidate


def _validate_registration_list(
    *,
    root: Path,
    base: Path,
    owner: str,
    field: str,
    value: Any,
    errors: list[str],
) -> set[Path]:
    registered: set[Path] = set()
    if not _list_of_strings(value):
        errors.append(f"{owner}: {field!r} must be an array of non-empty strings")
        return registered

    for raw in value:
        target = _resolve_repo_path(root, base, raw, owner, errors)
        if target is None:
            continue
        if field == "skills":
            if target.is_dir() and (target / "SKILL.md").is_file():
                candidates = [target / "SKILL.md"]
            elif target.is_dir():
                candidates = sorted(target.glob("*/SKILL.md"))
            else:
                candidates = [target]
            if not candidates or any(
                not candidate.is_file() or candidate.name != "SKILL.md"
                for candidate in candidates
            ):
                errors.append(f"{owner}: skill not found for {raw!r}")
                continue
            registered.update(candidate.resolve() for candidate in candidates)
        else:
            candidates = sorted(target.glob("*.md")) if target.is_dir() else [target]
            if not candidates or any(
                not candidate.is_file() or candidate.suffix != ".md"
                for candidate in candidates
            ):
                errors.append(f"{owner}: agent file not found for {raw!r}")
                continue
            registered.update(candidate.resolve() for candidate in candidates)
    return registered


def validate_manifests(root: Path) -> list[str]:
    """Validate marketplace and every dynamically discovered plugin manifest."""

    root = root.resolve()
    errors: list[str] = []
    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    marketplace = _load_json(marketplace_path, root, errors)

    manifest_paths = sorted(root.glob("plugins/*/.claude-plugin/plugin.json"))
    if not manifest_paths:
        errors.append("plugins/*/.claude-plugin/plugin.json: no plugin manifests found")

    manifests_by_source: dict[Path, dict[str, Any]] = {}
    registered_agents: set[Path] = set()
    registered_skills: set[Path] = set()
    plugin_names: set[str] = set()

    for manifest_path in manifest_paths:
        owner = _display(manifest_path, root)
        data = _load_json(manifest_path, root, errors)
        if not isinstance(data, dict):
            if data is not None:
                errors.append(f"{owner}: top level must be an object")
            continue

        for field in ("name", "version", "description"):
            if not _nonempty_string(data.get(field)):
                errors.append(f"{owner}: missing non-empty string field {field!r}")
        name = data.get("name")
        if _nonempty_string(name):
            if name in plugin_names:
                errors.append(f"{owner}: duplicate plugin name {name!r}")
            plugin_names.add(name)

        plugin_root = manifest_path.parent.parent.resolve()
        if "skills" in data:
            registered_skills.update(
                _validate_registration_list(
                    root=root,
                    base=plugin_root,
                    owner=owner,
                    field="skills",
                    value=data["skills"],
                    errors=errors,
                )
            )
        else:
            registered_skills.update(
                path.resolve() for path in plugin_root.glob("skills/*/SKILL.md")
            )
        if "agents" in data:
            registered_agents.update(
                _validate_registration_list(
                    root=root,
                    base=plugin_root,
                    owner=owner,
                    field="agents",
                    value=data["agents"],
                    errors=errors,
                )
            )
        else:
            registered_agents.update(
                path.resolve() for path in plugin_root.glob("agents/*.md")
            )
        manifests_by_source[plugin_root] = data

    marketplace_sources: set[Path] = set()
    if isinstance(marketplace, dict):
        if not _nonempty_string(marketplace.get("name")):
            errors.append(
                f"{_display(marketplace_path, root)}: missing non-empty string "
                "field 'name'"
            )
        entries = marketplace.get("plugins")
        if not isinstance(entries, list) or not entries:
            errors.append(
                f"{_display(marketplace_path, root)}: 'plugins' must be a "
                "non-empty array"
            )
            entries = []

        marketplace_names: set[str] = set()
        for index, entry in enumerate(entries):
            owner = f"{_display(marketplace_path, root)}:plugins[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{owner}: entry must be an object")
                continue
            name = entry.get("name")
            source = entry.get("source")
            if not _nonempty_string(name):
                errors.append(f"{owner}: missing non-empty string field 'name'")
            elif name in marketplace_names:
                errors.append(f"{owner}: duplicate marketplace plugin name {name!r}")
            else:
                marketplace_names.add(name)
            if not _nonempty_string(source):
                errors.append(f"{owner}: missing non-empty string field 'source'")
                continue

            source_path = _resolve_repo_path(root, root, source, owner, errors)
            if source_path is None:
                continue
            if not source_path.is_dir():
                errors.append(f"{owner}: plugin source directory not found: {source!r}")
                continue
            marketplace_sources.add(source_path)
            manifest = manifests_by_source.get(source_path)
            if manifest is None:
                errors.append(f"{owner}: source has no .claude-plugin/plugin.json")
                continue
            if _nonempty_string(name) and manifest.get("name") != name:
                errors.append(
                    f"{owner}: name {name!r} does not match plugin manifest "
                    f"name {manifest.get('name')!r}"
                )

            # Marketplace registrations are optional when the plugin manifest owns
            # them, but validate them when present.
            for field in ("skills", "agents"):
                if field in entry:
                    _validate_registration_list(
                        root=root,
                        base=source_path,
                        owner=owner,
                        field=field,
                        value=entry[field],
                        errors=errors,
                    )
    elif marketplace is not None:
        errors.append(f"{_display(marketplace_path, root)}: top level must be an object")

    for source in sorted(set(manifests_by_source) - marketplace_sources):
        errors.append(
            f"{_display(source, root)}: plugin manifest is not registered in "
            ".claude-plugin/marketplace.json"
        )

    discovered_agents = {
        path.resolve() for path in root.glob("plugins/*/agents/*.md")
    }
    for path in sorted(discovered_agents - registered_agents):
        errors.append(f"{_display(path, root)}: agent is not registered by a plugin")

    discovered_skills = {
        path.resolve() for path in root.glob("plugins/*/skills/*/SKILL.md")
    }
    for path in sorted(discovered_skills - registered_skills):
        errors.append(f"{_display(path, root)}: skill is not registered by a plugin")

    return errors


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Parse the small YAML subset used by agent and skill frontmatter."""

    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, [f"{path}: cannot read frontmatter: {exc}"]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, [f"{path}: missing opening frontmatter delimiter"]
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        return {}, [f"{path}: missing closing frontmatter delimiter"]

    data: dict[str, Any] = {}
    current_key: str | None = None
    for index, raw_line in enumerate(lines[1:end], start=2):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[:1].isspace():
            item_match = re.match(r"^\s+-\s+(.+?)\s*$", raw_line)
            if item_match and current_key:
                prior = data.get(current_key)
                if prior in (None, ""):
                    data[current_key] = []
                if not isinstance(data.get(current_key), list):
                    errors.append(
                        f"{path}:{index}: list item follows scalar field "
                        f"{current_key!r}"
                    )
                    continue
                data[current_key].append(_yaml_scalar(item_match.group(1)))
                continue
            if current_key and data.get(current_key) in (">", ">-", "|", "|-"):
                data[current_key] = raw_line.strip()
                continue
            if current_key and isinstance(data.get(current_key), str):
                data[current_key] = f"{data[current_key]} {raw_line.strip()}".strip()
                continue
            errors.append(f"{path}:{index}: unsupported indented frontmatter line")
            continue

        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$", raw_line)
        if not match:
            errors.append(f"{path}:{index}: invalid frontmatter field")
            current_key = None
            continue
        key, raw_value = match.groups()
        if key in data:
            errors.append(f"{path}:{index}: duplicate frontmatter field {key!r}")
        data[key] = _yaml_scalar(raw_value or "")
        current_key = key
    return data, errors


def _yaml_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _tool_names(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_tools = [str(item) for item in value]
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            stripped = stripped[1:-1]
        raw_tools = stripped.split(",")
    else:
        return []
    return [_yaml_scalar(item.strip()) for item in raw_tools if item.strip()]


def validate_agents(root: Path) -> list[str]:
    """Validate every discovered agent while requiring the three DV workers."""

    root = root.resolve()
    errors: list[str] = []
    paths = sorted(root.glob("plugins/*/agents/*.md"))
    if not paths:
        return ["plugins/*/agents/*.md: no agent definitions found"]

    names: dict[str, Path] = {}
    for path in paths:
        rel = _display(path, root)
        frontmatter, parse_errors = parse_frontmatter(path)
        errors.extend(error.replace(str(path), rel, 1) for error in parse_errors)
        for field in sorted(REQUIRED_AGENT_FIELDS - set(frontmatter)):
            errors.append(f"{rel}: missing frontmatter field {field!r}")
        for field in sorted(FORBIDDEN_AGENT_FIELDS & set(frontmatter)):
            errors.append(f"{rel}: forbidden legacy frontmatter field {field!r}")
        for field in REQUIRED_AGENT_FIELDS - {"tools"}:
            if field in frontmatter and not _nonempty_string(frontmatter[field]):
                errors.append(f"{rel}: frontmatter field {field!r} must be non-empty")

        name = frontmatter.get("name")
        if _nonempty_string(name):
            if name in names:
                errors.append(
                    f"{rel}: duplicate agent name {name!r}; first declared in "
                    f"{_display(names[name], root)}"
                )
            else:
                names[name] = path
            if path.stem != name:
                errors.append(
                    f"{rel}: filename stem {path.stem!r} must match agent name {name!r}"
                )

        tools = _tool_names(frontmatter.get("tools"))
        if "tools" in frontmatter and not tools:
            errors.append(f"{rel}: frontmatter field 'tools' must not be empty")
        for tool in tools:
            if re.match(r"(?i)^agent(?:\s*\(|$)", tool):
                errors.append(
                    f"{rel}: worker agents must not declare the Agent tool: {tool!r}"
                )

    missing = sorted(REQUIRED_WORKERS - set(names))
    for name in missing:
        errors.append(f"required worker agent is missing: {name}")
    return errors


def validate_skills(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    required_skill = root / SKILL_PATH
    if not required_skill.is_file():
        return [f"{SKILL_PATH.as_posix()}: required skill is missing"]

    paths = sorted(root.glob("plugins/*/skills/*/SKILL.md"))
    for path in paths:
        rel = _display(path, root)
        frontmatter, parse_errors = parse_frontmatter(path)
        errors.extend(error.replace(str(path), rel, 1) for error in parse_errors)
        for field in ("name", "description"):
            if not _nonempty_string(frontmatter.get(field)):
                errors.append(f"{rel}: missing non-empty frontmatter field {field!r}")
    return errors


def _walk_json(value: Any, location: str = "$") -> Iterable[tuple[str, Any]]:
    yield location, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{location}/{index}")


def _resolve_pointer(document: Any, fragment: str) -> tuple[bool, str]:
    fragment = unquote(fragment)
    if not fragment:
        return True, ""
    if not fragment.startswith("/"):
        for _, candidate in _walk_json(document):
            if isinstance(candidate, dict) and candidate.get("$anchor") == fragment:
                return True, ""
        return False, f"anchor #{fragment} does not exist"

    current = document
    for raw_token in fragment[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, f"JSON Pointer #{fragment} does not exist"
    return True, ""


def _enum_at(schema: dict[str, Any], *path: str) -> set[str] | None:
    current: Any = schema
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if not isinstance(current, list) or not all(isinstance(item, str) for item in current):
        return None
    return set(current)


def validate_schemas(root: Path) -> list[str]:
    """Check schema JSON, local references, and shared task/result vocabulary."""

    root = root.resolve()
    errors: list[str] = []
    schema_dir = root / SCHEMA_DIR
    required_paths = [schema_dir / name for name in REQUIRED_SCHEMAS]
    documents: dict[Path, dict[str, Any]] = {}

    for path in sorted(schema_dir.glob("*.schema.json")):
        data = _load_json(path, root, errors)
        if isinstance(data, dict):
            documents[path.resolve()] = data
        elif data is not None:
            errors.append(f"{_display(path, root)}: schema top level must be an object")
    for path in required_paths:
        if path.resolve() not in documents and path.exists():
            # A present but malformed file was already reported by _load_json.
            continue
        if not path.exists():
            errors.append(f"{_display(path, root)}: required schema is missing")

    id_index: dict[str, Path] = {}
    for path, schema in documents.items():
        rel = _display(path, root)
        if not _nonempty_string(schema.get("$schema")):
            errors.append(f"{rel}: missing non-empty '$schema'")
        schema_id = schema.get("$id")
        if not _nonempty_string(schema_id):
            errors.append(f"{rel}: missing non-empty '$id'")
        elif schema_id in id_index:
            errors.append(
                f"{rel}: duplicate '$id' {schema_id!r} also used by "
                f"{_display(id_index[schema_id], root)}"
            )
        else:
            id_index[schema_id] = path
        if schema.get("type") != "object":
            errors.append(f"{rel}: root schema type must be 'object'")
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict):
            errors.append(f"{rel}: root 'properties' must be an object")
            properties = {}
        if not _list_of_strings(required):
            errors.append(f"{rel}: root 'required' must be a non-empty string array")
        else:
            for field in required:
                if field not in properties:
                    errors.append(
                        f"{rel}: required field {field!r} has no property definition"
                    )
        if "$defs" in schema and not isinstance(schema["$defs"], dict):
            errors.append(f"{rel}: '$defs' must be an object")

    for path, schema in documents.items():
        rel = _display(path, root)
        for location, value in _walk_json(schema):
            if not isinstance(value, dict) or "$ref" not in value:
                continue
            reference = value["$ref"]
            if not _nonempty_string(reference):
                errors.append(f"{rel}:{location}: '$ref' must be a non-empty string")
                continue
            target_text, fragment = urldefrag(reference)
            if not target_text:
                target_path = path
            elif target_text in id_index:
                target_path = id_index[target_text]
            elif urlparse(target_text).scheme:
                errors.append(f"{rel}:{location}: unresolved schema id {target_text!r}")
                continue
            else:
                target_path = (path.parent / unquote(target_text)).resolve()
                if not _inside(target_path, root):
                    errors.append(f"{rel}:{location}: '$ref' escapes repository")
                    continue
            target = documents.get(target_path)
            if target is None:
                errors.append(
                    f"{rel}:{location}: unresolved '$ref' document {target_text!r}"
                )
                continue
            ok, reason = _resolve_pointer(target, fragment)
            if not ok:
                errors.append(f"{rel}:{location}: unresolved '$ref' {reference!r}: {reason}")

    request = documents.get((schema_dir / "task-request.schema.json").resolve())
    result = documents.get((schema_dir / "task-result.schema.json").resolve())
    if request is not None and result is not None:
        request_roles = _enum_at(request, "properties", "role", "enum") or _enum_at(
            request, "$defs", "role", "enum"
        )
        result_roles = _enum_at(result, "properties", "role", "enum") or _enum_at(
            result, "$defs", "role", "enum"
        )
        expected_roles = {"builder", "reviewer", "runner"}
        if request_roles != expected_roles:
            errors.append(
                f"{_display(schema_dir / 'task-request.schema.json', root)}: "
                f"role enum must be {sorted(expected_roles)}"
            )
        if result_roles != expected_roles:
            errors.append(
                f"{_display(schema_dir / 'task-result.schema.json', root)}: "
                f"role enum must be {sorted(expected_roles)}"
            )
        request_actions = _enum_at(request, "$defs", "action", "enum")
        result_actions = _enum_at(result, "$defs", "action", "enum")
        if request_actions is None or result_actions is None:
            errors.append("task request/result schemas must define $defs.action.enum")
        elif request_actions != result_actions:
            errors.append(
                "task request/result schemas have inconsistent $defs.action enums"
            )

    return errors


def validate_repo(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    errors.extend(validate_manifests(root))
    errors.extend(validate_agents(root))
    errors.extend(validate_skills(root))
    errors.extend(validate_schemas(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    args = parser.parse_args(argv)
    errors = validate_repo(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Repository validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Repository validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
