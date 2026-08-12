<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-build/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-build/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-build
description: SpacemiT Robot SDK 构建基础 skill；用于统一处理 envsetup.sh、board/target 选择、lunch、m、mm，以及 local/remote/hybrid 模式下的构建位置决策。
---

# SpacemiT Robot Build

先按 `spacemit-robot-shared` 解析 mode、SDK root 和 `build_hint`。

## Rules

- `local`: 在板端本地 `SROBOTIS_ROOT` 构建。
- `remote`: 通过 `spacemit-robot-remote` 在 `SROBOTIS_REMOTE_ROOT` 构建。
- `hybrid`: 先按需用 `spacemit-robot-sync` 同步显式路径，再通过 `spacemit-robot-remote` 在 `SROBOTIS_REMOTE_ROOT` 构建。
- Hybrid 不交叉编译，不在 PC 侧构建目标产物。
- 不写死 target；需要 target 时按 shared 规则识别 board 并读取 `target/*.json`。

## Build Decision

1. 检查 SDK root 是否完整。
2. 需要 SDK 环境时执行 `source build/envsetup.sh`。
3. 读取组件 skill 的 `metadata.sdk.build_hint`。
4. `single_package_first`: 优先进入模块目录执行 `mm`。
5. `target_preferred`: 若模块依赖板型、驱动或产品配置，先明确 target；否则可先 `mm`。
6. `target_required`: 先明确 target，执行 `lunch <target>` 后再构建。
7. 只有用户要求全量构建、镜像构建或模块构建无法满足依赖时，才执行 `m` 或更重的构建入口。

## Output

构建完成后返回：

- mode 和构建所在机器。
- SDK root。
- target 是否已选择。
- 执行的构建命令。
- 退出码、关键日志路径和失败点。

日常开发验证不默认调用 `scripts/test/robot-test`；只有 CI、回归验证或用户明确要求时才使用。
