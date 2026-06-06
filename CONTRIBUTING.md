# Contributing to digital-chip-design-agents

## Skill File Standards

Every `SKILL.md` must have these sections in order:

```markdown
---                          ← YAML frontmatter (required)
name: domain-name
description: >
  One-sentence description for Claude Code's skill discovery.
version: x.y.z
author: chuanseng-ng
license: MIT
allowed-tools: Read, Write, Bash
---

# Skill: Domain Name

## Purpose
One paragraph — what this skill enables Claude to do.

## Stage: stage_name          ← Repeat per stage

### Domain Rules
Numbered rules. Be specific — vague rules are not useful.

### QoR Metrics to Evaluate
Measurable pass/fail criteria with units (ns, %, count).

### Common Issues & Fixes
Table: Issue | Fix

### Output Required
Bullet list of files/artifacts the stage must produce.
```

## Adding a New Skill

1. Create `skills/<new-domain>/SKILL.md` following the standard above.

2. Add an entry to `.claude-plugin/marketplace.json`:
```json
{
  "name": "chip-design-<new-domain>",
  "source": { "source": "github", "repo": "chuanseng-ng/digital-chip-design-agents" },
  "description": "One-line description",
  "category": "engineering",
  "keywords": ["keyword1", "keyword2"]
}
```

3. Create `agents/<new-domain>-orchestrator.md` with this minimum structure:
```markdown
---
name: <new-domain>-orchestrator
description: >
  When to invoke this orchestrator.
model: sonnet
effort: high
maxTurns: 50
skills:
  - digital-chip-design-agents:<new-domain>
---

## Stage Sequence
stage_1 → stage_2 → stage_3

## Loop-Back Rules
- stage_2 FAIL (condition)  → stage_1  (max N×)

## Sign-off Criteria
- metric_name: value

## Behaviour Rules
1. ...
```

4. Run validation locally:
```bash
python3 -c "
import json, os, sys
for d in os.listdir('skills'):
    p = f'skills/{d}/SKILL.md'
    c = open(p).read()
    assert c.startswith('---'), f'{p}: missing frontmatter'
    for s in ['## Purpose','## Domain Rules','## QoR Metrics','## Output Required']:
        assert s in c, f'{p}: missing {s}'
    print(f'OK: {p}')
"
```

5. Open a Pull Request — the CI `validate.yml` must pass before merge.

## Improving Existing Skills

- **Domain Rules**: Be more specific, add new tool-specific commands, update metrics
- **QoR Metrics**: Add units; tighten targets based on real project experience
- **Loop-back rules in orchestrators**: Add new transitions or tighten max iterations

## Pull Request Checklist

- [ ] SKILL.md has YAML frontmatter with `name`, `description`, `version`
- [ ] SKILL.md has all four required sections
- [ ] marketplace.json updated if new domain added
- [ ] Orchestrator .md has frontmatter with `model`, `effort`, `maxTurns`, `skills`
- [ ] Orchestrator .md has `## Stage Sequence`, `## Loop-Back Rules`, `## Sign-off Criteria`
- [ ] Local validation passes (see above)
- [ ] Count remains consistent: skills = agents = marketplace entries

## Versioning

- `PATCH` (x.x.1) — fixes or clarifications within existing skills
- `MINOR` (x.1.0) — new skill or orchestrator domain added
- `MAJOR` (2.0.0) — breaking change to frontmatter schema or stage interface

## Shared metadata in plugin.json

Each `plugins/<domain>/.claude-plugin/plugin.json` repeats the same `author`,
`homepage`, `repository`, and `license` fields. These are intentional — the
plugin installer reads each manifest in isolation and requires these fields to
be present. The canonical values are:

```json
"author":     { "name": "chuanseng-ng", "url": "https://github.com/chuanseng-ng" },
"homepage":   "https://github.com/chuanseng-ng/digital-chip-design-agents",
"repository": "https://github.com/chuanseng-ng/digital-chip-design-agents",
"license":    "MIT"
```

When updating these fields, change all 14 `plugin.json` files and
`.claude-plugin/marketplace.json` together.
