# Functional Verification — Copilot Workspace Instructions

This workspace contains ASIC/FPGA functional verification work.

## Behaviour

- Execute one verification stage at a time and report **PASS / FAIL / WARN** after each stage.
- Flag ambiguities before proceeding — chip design is safety-critical.

## Domain-Specific Rules

Per-domain rules and stage sequences are loaded from
`.github/instructions/<domain>.instructions.md` based on the files you are working with.
These files are generated from the plugin SKILL.md sources.
