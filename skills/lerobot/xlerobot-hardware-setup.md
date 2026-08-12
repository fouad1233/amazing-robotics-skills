<!-- Vendored from ambient-robots/xlerobot_pinc @ main
     Path:    .codex/skills/xlerobot-hardware-setup/SKILL.md
     Licence: MIT
     Source:  https://github.com/ambient-robots/xlerobot_pinc/blob/main/.codex/skills/xlerobot-hardware-setup/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: xlerobot-hardware-setup
description: Configure and verify the physical XLerobot runtime setup, including udev serial symlinks, motor board ports, camera IDs, user JSON runtime config, environment loading, and calibration/setup flows. Use when Codex needs to help with first-time hardware bring-up, diagnose missing ports or cameras, update the user config schema, or reason about calibration and setup procedures.
---

# Xlerobot Hardware Setup

## Overview

Use this skill for hardware-facing setup and debugging tasks. Focus on operator workflow, required device identifiers, runtime env loading, and the robot's calibration and motor-setup procedures.

## Workflow

1. Read the current operator-facing workflow in `README.md`.
2. Read `xlerobot_user_config.example.json` and `load_xlerobot_env.sh` together when the task touches runtime config or environment variables.
3. Read `src/lerobot/robots/xlerobot_pinc/config_xlerobot_pinc.py` when the task touches required env vars, camera defaults, or config validation.
4. Read `src/lerobot/robots/xlerobot_pinc/xlerobot_pinc.py` when the task touches `connect()`, `calibrate()`, `configure()`, `setup_motors()`, or base/no-base behavior.
5. Keep the user workflow coherent across docs, JSON template, env loader, and config consumer.

## Operating Rules

- Treat `load_xlerobot_env.sh` as the single JSON-to-env translation layer.
- Keep required runtime keys explicit and fail fast with actionable errors.
- Preserve the default hardware assumptions unless the task explicitly changes them:
  - motor ports default to `/dev/xlerobot_right_head` and `/dev/xlerobot_left_base`
  - camera identifiers come from env-backed runtime config
- When changing hardware-facing config, update `README.md` in the same iteration.
- When touching sourced shell code, avoid changing caller shell options globally.

## Validation

- Run `bash -n load_xlerobot_env.sh`.
- If Python config code changed, run `python -m compileall src examples/so107_follower examples/xlerobot_pinc`.
- Grep for touched env vars to ensure docs and examples stay aligned.

## References

- Read `references/workflow.md` for the exact file map and common task breakdown.
