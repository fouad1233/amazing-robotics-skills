<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-sync/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-sync/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-sync
description: SpacemiT Robot SDK Hybrid 同步基础 skill；用于 PC 本地 SDK 到板端 SDK 的显式单向同步、默认 dry-run、路径保护和 repo preflight。
---

# SpacemiT Robot Sync

仅用于 `hybrid` 模式。先按 `spacemit-robot-shared` 确认 PC 的 `SROBOTIS_ROOT`、板端 `SROBOTIS_REMOTE` 和 `SROBOTIS_REMOTE_ROOT`。如果没有指定 PC 本地 SDK root，默认使用 `~/workspace/spacemit-robot`。

## Rules

- 只做 PC 到板端的单向同步。
- 默认 dry-run；真正同步必须显式传 `--apply`。
- 必须显式传 `--path`，不默认同步整个 SDK。
- 不同步 `.repo/`、`.git/`、`output/`、缓存、日志和构建产物。
- 同步后仍通过 `spacemit-robot-remote` 在板端构建和运行。

## Helper

```bash
node skills/spacemit-robot-sync/scripts/srobotis_sync.mjs \
  --local-root "<pc-sdk-root>" \
  --remote "<user@host>" \
  --remote-root "<board-sdk-root>" \
  --path components/model_zoo/llm \
  --dry-run
```

执行真实同步时改用 `--apply`。

该 helper 使用 Node.js，因为安装 skills 已经依赖 `npx`/npm；不要要求 Windows 用户额外安装 Python。若当前机器没有 `rsync`，helper 仍可执行 dry-run 和路径检查；真实同步应改用可用的 SSH/SFTP/WSL/Git Bash 方案，并继续遵守本 skill 的路径保护规则。

## Preflight

同步前检查：

- `--path` 不能是绝对路径，不能包含 `..`，不能指向 `.repo`、`.git`、`output` 等被保护目录。
- 如果本地存在 `.repo/project.list`，尽量确认 path 能映射到 repo project。
- 执行真实同步前，确认远端 SDK root 存在并检查远端对应 project 是否有未提交改动。

日常开发验证不默认调用 `scripts/test/robot-test`；只有 CI、回归验证或用户明确要求时才使用。
