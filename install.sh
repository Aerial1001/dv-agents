#!/usr/bin/env bash
# install.sh — installs the chip-design-verification plugin from this repo
#
# Usage:
#   bash install.sh                         # auto-detect installed agents + confirm
#   bash install.sh --yes                   # auto-detect, no confirmation prompt
#   bash install.sh --check                 # validate repo without installing
#   bash install.sh --ide claude            # Claude Code (explicit)
#   bash install.sh --ide copilot           # GitHub Copilot (.github/ in cwd)
#   bash install.sh --ide gemini            # Gemini Code Assist (GEMINI.md in cwd)
#   bash install.sh --ide gemini --global   # Gemini global (~/GEMINI.md)
#   bash install.sh --ide opencode          # OpenCode (opencode.json in cwd)
#   bash install.sh --ide opencode --global # OpenCode global (~/.config/opencode/)
#   bash install.sh --ide codex             # OpenAI Codex CLI (AGENTS.md in cwd)
#   bash install.sh --ide codex --global    # OpenAI Codex CLI global (~/.codex/instructions.md)
#   bash install.sh --ide all               # all five agents
#
# With no --ide flag the script detects which of the five supported agents
# (claude, codex, opencode, gemini, copilot) are present and installs to them
# after a confirmation prompt. Passing --ide bypasses detection.
#
# Works on macOS, Linux, and Git Bash / MSYS2 on Windows.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKETPLACE="dv-agents"

# ── Parse flags ───────────────────────────────────────────────────────────────
IDE=""
GLOBAL="false"
YES="false"
CHECK_ONLY="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ide)
      # Guard against a trailing `--ide` so `set -u` doesn't abort on $2 before
      # the user sees a usage message.
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --ide requires a value: claude|copilot|gemini|opencode|codex|all|auto"
        exit 1
      fi
      IDE="$2"; shift 2
      ;;
    --global)
      GLOBAL="true"; shift
      ;;
    --yes|-y)
      YES="true"; shift
      ;;
    --check)
      CHECK_ONLY="true"; shift
      ;;
    -h|--help)
      echo "Usage: bash install.sh [--ide claude|copilot|gemini|opencode|codex|all] [--global] [--yes] [--check]"
      echo "  With no --ide, detects installed agents and installs to them after confirmation."
      echo "  --yes     Skip confirmation prompts."
      echo "  --check   Validate the repo without installing."
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: bash install.sh [--ide claude|copilot|gemini|opencode|codex|all] [--global] [--yes] [--check]"
      exit 1
      ;;
  esac
done

if [[ -n "$IDE" && "$IDE" != "auto" ]]; then
  case "$IDE" in
    claude|copilot|gemini|opencode|codex|all) ;;
    *)
      echo "ERROR: --ide must be one of: claude, copilot, gemini, opencode, codex, all, auto"
      exit 1
      ;;
  esac
fi

# ── Shared sanity check ───────────────────────────────────────────────────────
if [[ ! -f "$REPO_DIR/.claude-plugin/marketplace.json" ]]; then
  echo "ERROR: Cannot locate repo root. Run this script from inside the cloned repo."
  exit 1
fi

PLUGIN_SRC="$REPO_DIR/plugins/verification"
if [[ ! -f "$PLUGIN_SRC/.claude-plugin/plugin.json" ]]; then
  echo "ERROR: plugin source not found at $PLUGIN_SRC"
  exit 1
fi

# ── Check mode (idempotent, no side effects) ─────────────────────────────────
check_repo() {
  echo "=== Repository validation ==="
  if command -v python3 &>/dev/null; then
    python3 "$REPO_DIR/scripts/validate_repo.py" || {
      echo "WARNING: Repository validation found issues (see above)."
      echo "  The plugin may still work; fix the reported problems if any are fatal."
    }
  else
    echo "WARNING: python3 not found — skipping structural validation."
  fi

  VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "$PLUGIN_SRC/.claude-plugin/plugin.json" 2>/dev/null || echo "unknown")"
  echo "Plugin  : chip-design-verification"
  echo "Version : $VERSION"
  echo "Source  : $PLUGIN_SRC"
  echo ""

  # List included agents and skills
  echo "Agents:"
  for f in "$PLUGIN_SRC"/agents/*.md; do
    [[ -f "$f" ]] && echo "  - $(basename "$f" .md)"
  done
  echo "Skills:"
  for f in "$PLUGIN_SRC"/skills/*/SKILL.md; do
    [[ -f "$f" ]] && echo "  - $(basename "$(dirname "$f")")"
  done
}

check_repo

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo ""
  echo "Check complete. No changes made."
  exit 0
fi

# ── Detection (read-only) ────────────────────────────────────────────────────
# A target counts as installed if its CLI is on PATH or its config dir exists.
is_installed() {
  case "$1" in
    claude)   command -v claude   >/dev/null 2>&1 || [[ -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" ]] ;;
    codex)    command -v codex    >/dev/null 2>&1 || [[ -d "$HOME/.codex" ]] ;;
    opencode) command -v opencode >/dev/null 2>&1 || [[ -d "$HOME/.config/opencode" ]] ;;
    gemini)   command -v gemini   >/dev/null 2>&1 || [[ -d "$HOME/.gemini" ]] ;;
    copilot)  command -v copilot  >/dev/null 2>&1 || command -v gh >/dev/null 2>&1 ;;
  esac
}

# Where each target writes, so the confirmation shows repo vs $HOME vs config dir.
destination_for() {
  case "$1" in
    claude)   echo "${CLAUDE_CONFIG_DIR:-$HOME/.claude} (global plugin cache)" ;;
    codex)    [[ "$GLOBAL" == "true" ]] && echo "$HOME/.codex/instructions.md" || echo "$PWD/AGENTS.md" ;;
    opencode) [[ "$GLOBAL" == "true" ]] && echo "$HOME/.config/opencode/config.json" || echo "$PWD/opencode.json" ;;
    gemini)   [[ "$GLOBAL" == "true" ]] && echo "$HOME/GEMINI.md" || echo "$PWD/GEMINI.md" ;;
    copilot)  echo "$PWD/.github" ;;
  esac
}

# ── Plugin list (single plugin in this repo) ────────────────────────────────
PLUGINS=("chip-design-verification")
declare -A PLUGIN_DIRS=(
  ["chip-design-verification"]="verification"
)

# ── Build the selection set ──────────────────────────────────────────────────
declare -A SEL=()
ALL_TARGETS=(claude codex opencode gemini copilot)

if [[ -z "$IDE" || "$IDE" == "auto" ]]; then
  echo "Detecting installed AI coding agents..."
  echo ""
  detected=()
  for t in "${ALL_TARGETS[@]}"; do
    if is_installed "$t"; then
      detected+=("$t"); echo "  [found] $t -> $(destination_for "$t")"
    else
      echo "  [  -  ] $t"
    fi
  done
  if [[ ${#detected[@]} -eq 0 ]]; then
    echo ""
    echo "No supported agents detected. Install one explicitly with:"
    echo "  bash install.sh --ide claude   (or copilot|gemini|opencode|codex|all)"
    exit 0
  fi
  if [[ "$YES" != "true" && -t 0 ]]; then
    echo ""
    read -r -p 'Install to all detected targets? [Y/n] (or list a subset, e.g. "claude,codex"): ' ans
    ans="$(echo "$ans" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$ans" in
      ""|y|yes) for t in "${detected[@]}"; do SEL[$t]=1; done ;;
      n|no)     echo "Aborted."; exit 0 ;;
      *)
        IFS=',' read -ra picks <<< "$ans"
        for p in "${picks[@]}"; do
          for t in "${detected[@]}"; do [[ "$p" == "$t" ]] && SEL[$t]=1; done
        done
        ;;
    esac
  else
    for t in "${detected[@]}"; do SEL[$t]=1; done
    echo ""
    echo "Installing to all detected targets."
  fi
elif [[ "$IDE" == "all" ]]; then
  for t in "${ALL_TARGETS[@]}"; do SEL[$t]=1; done
else
  SEL[$IDE]=1
fi

if [[ ${#SEL[@]} -eq 0 ]]; then
  echo "Nothing selected. Aborted."
  exit 0
fi

# ── python3 is required for every target in the shell installer ──────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required by install.sh but was not found in PATH."
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Claude Code install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ -n "${SEL[claude]:-}" ]]; then

  # Locate Claude config dir
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    CLAUDE_DIR="$CLAUDE_CONFIG_DIR"
  elif [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* || "$OSTYPE" == win32* ]]; then
    CLAUDE_DIR="${USERPROFILE}/.claude"
  else
    CLAUDE_DIR="${HOME}/.claude"
  fi

  if [[ ! -d "$CLAUDE_DIR" ]]; then
    echo ""
    echo "Claude Code config directory not found at $CLAUDE_DIR"
    echo "  Make sure Claude Code is installed and has been run at least once."
    echo ""
    echo "You can still prepare the plugin and load it with --plugin-dir later:"
    echo "  claude --plugin-dir $PLUGIN_SRC"
    exit 1
  fi

  echo ""
  echo "Installing Claude Code plugin cache..."
  for plugin in "${PLUGINS[@]}"; do
    subdir="${PLUGIN_DIRS[$plugin]}"
    src="$REPO_DIR/plugins/$subdir"
    version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$src/.claude-plugin/plugin.json")"
    dest="$CLAUDE_DIR/plugins/cache/$MARKETPLACE/$plugin/$version"
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -r "$src/agents"          "$dest/"
    cp -r "$src/skills"          "$dest/"
    cp -r "$src/.claude-plugin"  "$dest/"
    mkdir -p "$dest/scripts"
    cp -r "$REPO_DIR/scripts"/*  "$dest/scripts/" 2>/dev/null || true
    rm -rf "$dest/scripts/__pycache__"
    [[ -f "$REPO_DIR/README.md" ]] && cp "$REPO_DIR/README.md" "$dest/"
    [[ -f "$REPO_DIR/LICENSE" ]]   && cp "$REPO_DIR/LICENSE"   "$dest/"
    echo "  [OK] $plugin v$version cached"
  done

  SETTINGS="$CLAUDE_DIR/settings.json"
  echo ""
  echo "Updating $SETTINGS ..."

  python3 - "$SETTINGS" "$MARKETPLACE" "$REPO_DIR" "$PLUGIN_SRC" <<'PYEOF'
import json, os, sys
from datetime import datetime, timezone

settings_path = sys.argv[1]
marketplace   = sys.argv[2]
repo_dir      = sys.argv[3]
plugin_src    = sys.argv[4]

plugin = "chip-design-verification"

with open(os.path.join(plugin_src, ".claude-plugin", "plugin.json")) as f:
    version = json.load(f)["version"]

cfg = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        cfg = json.load(f)

# Enable the plugin
enabled = cfg.setdefault("enabledPlugins", {})
enabled[f"{plugin}@{marketplace}"] = True

# Register the local-directory marketplace so 'claude plugin update' works
mp = cfg.setdefault("extraKnownMarketplaces", {})
mp[marketplace] = {
    "source": {"source": "directory", "path": repo_dir}
}

with open(settings_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

# Seed the plugins ledger so the core tools pick it up immediately
plugins_json = os.path.join(os.path.dirname(settings_path), "plugins", "installed_plugins.json")
os.makedirs(os.path.dirname(plugins_json), exist_ok=True)

ledger = {}
if os.path.exists(plugins_json):
    with open(plugins_json) as f:
        ledger = json.load(f)

ledger.setdefault("version", 2)
ledger.setdefault("plugins", {})
plugin_key = f"{plugin}@{marketplace}"
now = datetime.now(timezone.utc).isoformat()
ledger["plugins"][plugin_key] = [{
    "scope": "user",
    "installPath": os.path.join(
        os.path.dirname(settings_path), "plugins", "cache",
        marketplace, plugin, version
    ),
    "version": version,
    "installedAt": now,
    "lastUpdated": now,
}]

with open(plugins_json, "w") as f:
    json.dump(ledger, f, indent=2)
    f.write("\n")

# Also register in known_marketplaces
kmp_path = os.path.join(os.path.dirname(settings_path), "plugins", "known_marketplaces.json")
os.makedirs(os.path.dirname(kmp_path), exist_ok=True)

known = {}
if os.path.exists(kmp_path):
    with open(kmp_path) as f:
        known = json.load(f)

known[marketplace] = {
    "source": {"source": "directory", "path": repo_dir},
    "installLocation": repo_dir,
    "lastUpdated": now,
}

with open(kmp_path, "w") as f:
    json.dump(known, f, indent=2)
    f.write("\n")

print(f"  [OK] {plugin}@{marketplace} v{version} enabled")
print(f"  [OK] marketplace '{marketplace}' registered -> {repo_dir}")
PYEOF

  echo ""
  echo "Done! Restart Claude Code and invoke:"
  echo "  /chip-design-verification:functional-verification"

fi  # end Claude Code block

# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Copilot install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ -n "${SEL[copilot]:-}" ]]; then

  echo ""
  echo "Installing GitHub Copilot instructions..."

  python3 - "$REPO_DIR" "$PWD" <<'PYEOF'
import json, os, re, glob, sys, shutil

repo_dir   = sys.argv[1]
target_dir = sys.argv[2]

# Load applyTo glob map
applyto_map = json.load(open(os.path.join(repo_dir, 'ides', 'copilot', 'applyto-map.json')))

# Copy global instructions file
gh_dir = os.path.join(target_dir, '.github', 'instructions')
os.makedirs(gh_dir, exist_ok=True)
shutil.copy(
    os.path.join(repo_dir, 'ides', 'copilot', '.github', 'copilot-instructions.md'),
    os.path.join(target_dir, '.github', 'copilot-instructions.md'),
)

# Generate per-domain instruction files from SKILL.md
skill_files = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'skills', '*', 'SKILL.md')))
for skill_path in skill_files:
    parts = os.path.normpath(skill_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    applyto = applyto_map.get(domain, '**/*')

    # Strip YAML frontmatter (--- ... ---) from SKILL.md body
    content = open(skill_path, encoding='utf-8').read()
    body = re.sub(r'^---\n.*?\n---\n', '', content, count=1, flags=re.DOTALL).strip()

    out_path = os.path.join(gh_dir, f'{domain}.instructions.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'---\napplyTo: "{applyto}"\n---\n\n{body}\n')
    print(f'  [OK] .github/instructions/{domain}.instructions.md')

print(f'\nCopilot: {len(skill_files)} instruction file(s) installed.')
print('Commit .github/ to share domain rules with your team.')
PYEOF

fi  # end Copilot block

# ═══════════════════════════════════════════════════════════════════════════════
# Gemini Code Assist install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ -n "${SEL[gemini]:-}" ]]; then

  echo ""
  echo "Installing Gemini Code Assist context file..."

  if [[ "$GLOBAL" == "true" ]]; then
    GEMINI_TARGET="${HOME}/GEMINI.md"
  else
    GEMINI_TARGET="$PWD/GEMINI.md"
  fi

  python3 - "$REPO_DIR" "$GEMINI_TARGET" <<'PYEOF'
import os, glob, sys

repo_dir = sys.argv[1]
out_path = sys.argv[2]

# Read preamble header
header = open(os.path.join(repo_dir, 'ides', 'gemini', 'gemini-header.md'), encoding='utf-8').read().strip()

lines = [
    '# Digital Chip Design Agents — Gemini Context',
    f'<!-- Generated by install.sh --ide gemini -->',
    f'<!-- Source: {repo_dir} -->',
    '',
    header,
    '',
    '## Domain Knowledge',
    '',
]

skill_files = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'skills', '*', 'SKILL.md')))

agent_files = {}
for p in sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'agents', '*.md'))):
    parts = os.path.normpath(p).split(os.sep)
    domain = parts[parts.index('plugins') + 1]
    agent_files.setdefault(domain, []).append(p)

for skill_path in skill_files:
    parts = os.path.normpath(skill_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    lines.append(f'### {domain}')
    lines.append('')
    lines.append(f'@{skill_path}')
    for agent_path in agent_files.get(domain, []):
        lines.append(f'@{agent_path}')
    lines.append('')

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

n_agents = sum(len(v) for v in agent_files.values())
print(f'  [OK] {out_path}')
print(f'  ({len(skill_files)} domains, {len(skill_files) + n_agents} @-imports)')
PYEOF

fi  # end Gemini block

# ═══════════════════════════════════════════════════════════════════════════════
# OpenCode install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ -n "${SEL[opencode]:-}" ]]; then

  echo ""
  echo "Installing OpenCode config..."

  if [[ "$GLOBAL" == "true" ]]; then
    OPENCODE_TARGET="${HOME}/.config/opencode/config.json"
  else
    OPENCODE_TARGET="$PWD/opencode.json"
  fi

  python3 - "$REPO_DIR" "$OPENCODE_TARGET" "$GLOBAL" <<'PYEOF'
import json, os, glob, re, sys

repo_dir   = sys.argv[1]
target     = sys.argv[2]
is_global  = sys.argv[3] == 'true'

# Mode key / display-name mapping
mode_display = {
    'verification': ('chip-verification', 'Functional Verification (UVM)'),
}

base = json.load(open(os.path.join(repo_dir, 'ides', 'opencode', 'opencode-base.json')))
modes = {}

skill_files = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'skills', '*', 'SKILL.md')))
for skill_path in skill_files:
    parts = os.path.normpath(skill_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    # Extract single-line description from the SKILL.md YAML frontmatter
    content = open(skill_path, encoding='utf-8').read()
    m = re.search(r'^description:\s*(.+)$', content, re.MULTILINE)
    desc = m.group(1).strip() if m else domain
    desc = desc[:120]

    mode_key, mode_name = mode_display.get(domain, (f'chip-{domain}', domain.replace('-', ' ').title()))
    prompt_path = skill_path if os.path.isabs(skill_path) else os.path.relpath(skill_path, os.path.dirname(target))
    modes[mode_key] = {
        'name':        mode_name,
        'description': desc,
        'model':       base.get('model', 'anthropic/claude-sonnet-4-5'),
        'prompt':     '{file:' + prompt_path + '}',
    }

if is_global and os.path.exists(target):
    # Merge modes into existing global config
    existing = json.load(open(target))
    existing.setdefault('mode', {}).update(modes)
    out = existing
else:
    base['mode'] = modes
    out = base
    if is_global:
        os.makedirs(os.path.dirname(target), exist_ok=True)

with open(target, 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2)
    f.write('\n')

print(f'  [OK] {target} — {len(modes)} mode(s)')
print('  Use /mode chip-<domain> in OpenCode to activate a domain.')
PYEOF

fi  # end OpenCode block

# ═══════════════════════════════════════════════════════════════════════════════
# OpenAI Codex CLI install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ -n "${SEL[codex]:-}" ]]; then

  echo ""
  echo "Installing OpenAI Codex CLI context file..."

  if [[ "$GLOBAL" == "true" ]]; then
    CODEX_TARGET="${HOME}/.codex/instructions.md"
    CODEX_AGENT_DIR="${HOME}/.codex/agents"
  else
    CODEX_TARGET="$PWD/AGENTS.md"
    CODEX_AGENT_DIR="$PWD/.codex/agents"
  fi

  python3 - "$REPO_DIR" "$CODEX_TARGET" "$CODEX_AGENT_DIR" <<'PYEOF'
import glob, json, os, re, sys

repo_dir = sys.argv[1]
out_path = sys.argv[2]
agent_dir = sys.argv[3]
plugin_root = os.path.join(repo_dir, 'plugins', 'verification')

# Read preamble header
header = open(os.path.join(repo_dir, 'ides', 'codex', 'AGENTS.md'), encoding='utf-8').read().strip()

lines = [
    '# Digital Chip Design Agents — Codex CLI Context',
    f'<!-- Generated by install.sh --ide codex -->',
    f'<!-- Source: {repo_dir} -->',
    '',
    header,
    '',
    '## Domain Knowledge',
    '',
]

skill_files = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'skills', '*', 'SKILL.md')))

for skill_path in skill_files:
    parts = os.path.normpath(skill_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    # Strip YAML frontmatter (--- ... ---) from SKILL.md body
    content = open(skill_path, encoding='utf-8').read()
    body = re.sub(r'^---\n.*?\n---\n', '', content, count=1, flags=re.DOTALL).strip()
    body = body.replace('${CLAUDE_PLUGIN_ROOT}', plugin_root)

    lines.append(f'### {domain}')
    lines.append('')
    lines.append(body)
    lines.append('')

# Ensure parent directories exist (needed for global ~/.codex/ paths).
os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
os.makedirs(agent_dir, exist_ok=True)

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

# Convert the canonical worker prompts into Codex custom-agent configuration
# layers. Keeping the Markdown prompts authoritative avoids maintaining a
# second copy of the DV contracts.
models_path = os.path.join(repo_dir, 'ides', 'codex', 'agent-models.json')
with open(models_path, encoding='utf-8') as f:
    agent_models = json.load(f)

for name, settings in sorted(agent_models.items()):
    source = os.path.join(plugin_root, 'agents', f'{name}.md')
    content = open(source, encoding='utf-8').read()
    body = re.sub(r'^---\n.*?\n---\n', '', content, count=1, flags=re.DOTALL).strip()
    body = body.replace('${CLAUDE_PLUGIN_ROOT}', plugin_root)
    values = {
        'name': name,
        'description': settings['description'],
        'model': settings['model'],
        'model_reasoning_effort': settings['model_reasoning_effort'],
        'sandbox_mode': settings['sandbox_mode'],
        'developer_instructions': body,
    }
    target = os.path.join(agent_dir, f'{name}.toml')
    with open(target, 'w', encoding='utf-8') as f:
        for key, value in values.items():
            f.write(f'{key} = {json.dumps(value, ensure_ascii=False)}\n')

print(f'  [OK] {out_path}')
print(f'  ({len(skill_files)} domains inlined)')
print(f'  [OK] {agent_dir}')
print(f'  ({len(agent_models)} Codex custom agents generated)')
PYEOF

fi  # end Codex block

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "Installation complete."
echo ""
echo "Claude Code:  restart and invoke /chip-design-verification:functional-verification"
echo "Copilot:      commit .github/ to share domain rules"
echo "Gemini:       project or global GEMINI.md written"
echo "OpenCode:     activate a mode with /mode chip-<domain>"
echo "Codex:        AGENTS.md or ~/.codex/instructions.md written"
echo "═══════════════════════════════════════════════════════════════════════"
