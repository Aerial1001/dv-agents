#!/usr/bin/env bash
# install.sh — installs digital-chip-design-agents plugins
#
# Usage:
#   bash install.sh                         # Claude Code (default)
#   bash install.sh --ide claude            # Claude Code (explicit)
#   bash install.sh --ide copilot           # GitHub Copilot (.github/ in cwd)
#   bash install.sh --ide gemini            # Gemini Code Assist (GEMINI.md in cwd)
#   bash install.sh --ide gemini --global   # Gemini global (~/GEMINI.md)
#   bash install.sh --ide opencode          # OpenCode (opencode.json in cwd)
#   bash install.sh --ide opencode --global # OpenCode global (~/.config/opencode/)
#   bash install.sh --ide codex          # OpenAI Codex CLI (AGENTS.md in cwd)
#   bash install.sh --ide codex --global  # OpenAI Codex CLI global (~/.codex/instructions.md)
#   bash install.sh --ide all               # Claude Code + all five IDEs (copilot, gemini, opencode, codex)
#
# Works on macOS, Linux, and Git Bash / MSYS2 on Windows.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKETPLACE="digital-chip-design-agents"
# Each plugin's cache version is read from its own .claude-plugin/plugin.json
# below, so plugins at different versions land in the correct path.

# ── Parse flags ───────────────────────────────────────────────────────────────
IDE="claude"
GLOBAL="false"
while [[ $# -gt 0 ]]; do
  case $1 in
    --ide)
      IDE="$2"; shift 2
      ;;
    --global)
      GLOBAL="true"; shift
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: bash install.sh [--ide claude|copilot|gemini|opencode|codex|all] [--global]"
      exit 1
      ;;
  esac
done

case "$IDE" in
  claude|copilot|gemini|opencode|codex|all) ;;
  *)
    echo "ERROR: --ide must be one of: claude, copilot, gemini, opencode, codex, all"
    exit 1
    ;;
esac

# ── Shared sanity checks ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required but not found in PATH."
  exit 1
fi

if [[ ! -f "$REPO_DIR/.claude-plugin/marketplace.json" ]]; then
  echo "ERROR: Cannot locate repo root. Ensure install.sh is inside the cloned repo."
  exit 1
fi

# ── Plugin list ───────────────────────────────────────────────────────────────
PLUGINS=(
  "chip-design-architecture"
  "chip-design-rtl"
  "chip-design-verification"
  "chip-design-formal"
  "chip-design-synthesis"
  "chip-design-dft"
  "chip-design-sta"
  "chip-design-hls"
  "chip-design-pd"
  "chip-design-soc"
  "chip-design-compiler"
  "chip-design-firmware"
  "chip-design-fpga"
  "chip-design-infrastructure"
  "chip-design-meta"
)

# ── Plugin → source directory mapping ────────────────────────────────────────
declare -A PLUGIN_DIRS=(
  ["chip-design-architecture"]="architecture"
  ["chip-design-rtl"]="rtl-design"
  ["chip-design-verification"]="verification"
  ["chip-design-formal"]="formal"
  ["chip-design-synthesis"]="synthesis"
  ["chip-design-dft"]="dft"
  ["chip-design-sta"]="sta"
  ["chip-design-hls"]="hls"
  ["chip-design-pd"]="pd"
  ["chip-design-soc"]="soc"
  ["chip-design-compiler"]="compiler"
  ["chip-design-firmware"]="firmware"
  ["chip-design-fpga"]="fpga"
  ["chip-design-infrastructure"]="infrastructure"
  ["chip-design-meta"]="meta"
)

# ═══════════════════════════════════════════════════════════════════════════════
# Claude Code install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$IDE" == "claude" || "$IDE" == "all" ]]; then

  # Locate Claude config dir
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    CLAUDE_DIR="$CLAUDE_CONFIG_DIR"
  elif [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* || "$OSTYPE" == win32* ]]; then
    CLAUDE_DIR="${USERPROFILE}/.claude"
  else
    CLAUDE_DIR="${HOME}/.claude"
  fi

  CACHE_DIR="$CLAUDE_DIR/plugins/cache/$MARKETPLACE"
  SETTINGS="$CLAUDE_DIR/settings.json"

  echo "Claude config : $CLAUDE_DIR"
  echo "Plugin cache  : $CACHE_DIR"
  echo ""

  if [[ ! -d "$CLAUDE_DIR" ]]; then
    echo "ERROR: Claude config directory not found at $CLAUDE_DIR"
    echo "  Make sure Claude Code is installed and has been run at least once."
    exit 1
  fi

  echo "Installing Claude Code plugin cache..."
  for plugin in "${PLUGINS[@]}"; do
    subdir="${PLUGIN_DIRS[$plugin]}"
    src="$REPO_DIR/plugins/$subdir"
    version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$src/.claude-plugin/plugin.json")"
    dest="$CACHE_DIR/$plugin/$version"
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -r "$src/agents"         "$dest/"
    cp -r "$src/skills"         "$dest/"
    cp -r "$src/.claude-plugin" "$dest/"
    [[ -f "$REPO_DIR/README.md" ]] && cp "$REPO_DIR/README.md" "$dest/"
    [[ -f "$REPO_DIR/LICENSE" ]]   && cp "$REPO_DIR/LICENSE"   "$dest/"
    echo "  [OK] $plugin"
  done

  echo ""
  echo "Updating $SETTINGS ..."

  python3 - "$SETTINGS" "$MARKETPLACE" "$REPO_DIR" <<PYEOF
import json, sys, os

settings_path = sys.argv[1]
marketplace   = sys.argv[2]

plugins = [
  "chip-design-architecture", "chip-design-rtl", "chip-design-verification",
  "chip-design-formal",       "chip-design-synthesis", "chip-design-dft",
  "chip-design-sta",          "chip-design-hls",       "chip-design-pd",
  "chip-design-soc",          "chip-design-compiler",  "chip-design-firmware",
  "chip-design-fpga",         "chip-design-infrastructure",
  "chip-design-meta",
]

cfg = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        cfg = json.load(f)

enabled = cfg.setdefault("enabledPlugins", {})
for p in plugins:
    enabled[f"{p}@{marketplace}"] = True

mp = cfg.setdefault("extraKnownMarketplaces", {})
mp[marketplace] = {
    "source": {"source": "directory", "path": sys.argv[3]}
}

with open(settings_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

print(f"  [OK] {len(plugins)} plugins enabled in settings.json")
PYEOF

  echo ""
  echo "Done! Restart Claude Code to activate all 15 plugins."

fi  # end Claude Code block

# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Copilot install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$IDE" == "copilot" || "$IDE" == "all" ]]; then

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

print(f'\nCopilot: {len(skill_files)} instruction files installed.')
print('Commit .github/ to share domain rules with your team.')
PYEOF

fi  # end Copilot block

# ═══════════════════════════════════════════════════════════════════════════════
# Gemini Code Assist install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$IDE" == "gemini" || "$IDE" == "all" ]]; then

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

skill_files  = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'skills', '*', 'SKILL.md')))
agent_files  = {
    os.path.basename(os.path.dirname(os.path.dirname(p))): p
    for p in glob.glob(os.path.join(repo_dir, 'plugins', '*', 'agents', '*.md'))
}

for skill_path in skill_files:
    parts = os.path.normpath(skill_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    lines.append(f'### {domain}')
    lines.append('')
    lines.append(f'@{skill_path}')
    if domain in agent_files:
        lines.append(f'@{agent_files[domain]}')
    lines.append('')

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print(f'  [OK] {out_path}')
print(f'  ({len(skill_files)} domains, {len(skill_files) + len(agent_files)} @-imports)')
PYEOF

fi  # end Gemini block

# ═══════════════════════════════════════════════════════════════════════════════
# OpenCode install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$IDE" == "opencode" || "$IDE" == "all" ]]; then

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
    'architecture': ('chip-architecture', 'Chip Architecture Evaluation'),
    'rtl-design':   ('chip-rtl',          'RTL Design (SystemVerilog)'),
    'verification': ('chip-verification', 'Functional Verification (UVM)'),
    'formal':       ('chip-formal',       'Formal Verification (FPV/LEC)'),
    'synthesis':    ('chip-synthesis',    'Logic Synthesis'),
    'dft':          ('chip-dft',          'Design for Test'),
    'sta':          ('chip-sta',          'Static Timing Analysis'),
    'hls':          ('chip-hls',          'High-Level Synthesis'),
    'pd':           ('chip-pd',           'Physical Design'),
    'soc':          ('chip-soc',          'SoC IP Integration'),
    'compiler':     ('chip-compiler',     'Compiler Toolchain'),
    'firmware':     ('chip-firmware',     'Embedded Firmware'),
    'fpga':         ('chip-fpga',         'FPGA Emulation'),
}

base = json.load(open(os.path.join(repo_dir, 'ides', 'opencode', 'opencode-base.json')))
modes = {}

agent_files = sorted(glob.glob(os.path.join(repo_dir, 'plugins', '*', 'agents', '*.md')))
for agent_path in agent_files:
    parts = os.path.normpath(agent_path).split(os.sep)
    domain = parts[parts.index('plugins') + 1]

    # Extract description from YAML frontmatter
    content = open(agent_path, encoding='utf-8').read()
    m = re.search(r'^description:\s*>?\s*\n((?:  .+\n)+)', content, re.MULTILINE)
    desc = ' '.join(l.strip() for l in m.group(1).strip().splitlines()) if m else domain
    desc = desc[:120]

    mode_key, mode_name = mode_display.get(domain, (f'chip-{domain}', domain.replace('-', ' ').title()))
    prompt_path = agent_path if os.path.isabs(agent_path) else os.path.relpath(agent_path, os.path.dirname(target))
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

print(f'  [OK] {target} — {len(modes)} modes')
print('  Use /mode chip-<domain> in OpenCode to activate a domain.')
PYEOF

fi  # end OpenCode block

# ═══════════════════════════════════════════════════════════════════════════════
# OpenAI Codex CLI install
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$IDE" == "codex" || "$IDE" == "all" ]]; then

  echo ""
  echo "Installing OpenAI Codex CLI context file..."

  if [[ "$GLOBAL" == "true" ]]; then
    CODEX_TARGET="${HOME}/.codex/instructions.md"
  else
    CODEX_TARGET="$PWD/AGENTS.md"
  fi

  python3 - "$REPO_DIR" "$CODEX_TARGET" <<'PYEOF'
import os, glob, re, sys

repo_dir = sys.argv[1]
out_path = sys.argv[2]

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

    lines.append(f'### {domain}')
    lines.append('')
    lines.append(body)
    lines.append('')

# Ensure parent directory exists (needed for global ~/.codex/ path)
os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print(f'  [OK] {out_path}')
print(f'  ({len(skill_files)} domains inlined)')
PYEOF

fi  # end Codex block
