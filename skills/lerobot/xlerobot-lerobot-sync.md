<!-- Vendored from ambient-robots/xlerobot_pinc @ main
     Path:    .codex/skills/xlerobot-lerobot-sync/SKILL.md
     Licence: MIT
     Source:  https://github.com/ambient-robots/xlerobot_pinc/blob/main/.codex/skills/xlerobot-lerobot-sync/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: xlerobot-lerobot-sync
description: Link this repository into a separate `lerobot` checkout using the symlink workflow, verify linked paths, and maintain the sync/bootstrap documentation. Use when Codex needs to update or debug `setup_lerobot_symlinks.sh`, reason about which files should be linked into `lerobot`, or help a user stop manually copying this repo into another checkout.
---

# Xlerobot Lerobot Sync

## Overview

Use this skill when the task is about integrating this repo with an external `lerobot` checkout. Focus on symlink creation, linked-path scope, backup behavior, and verification of the resulting integration.

## Workflow

1. Read `setup_lerobot_symlinks.sh`.
2. Read the "Link xlerobot_pinc into lerobot" section in `README.md`.
3. Confirm which source paths are intentionally linked and which are intentionally left local.
4. If the task changes linked content, update both the script and the documentation together.
5. Prefer verifying the resulting symlinks over assuming the script is correct.

## Operating Rules

- Prefer symlink-based integration over copying files.
- Keep script defaults generic and relative; avoid hardcoded user home paths.
- Preserve backup-before-link behavior when the destination already exists as a normal file or directory.
- Do not expand the linked surface casually. Only link files that are part of the core integration with `lerobot`.
- Keep examples and `XLeVR/` local unless the task explicitly requires a different workflow.

## Validation

- Run `bash -n setup_lerobot_symlinks.sh`.
- If the script changes, verify the linked path list against `README.md`.
- Use `git diff` to confirm the task did not accidentally modify unrelated runtime code.

## References

- Read `references/linked-paths.md` for the current linked surface and verification checklist.
