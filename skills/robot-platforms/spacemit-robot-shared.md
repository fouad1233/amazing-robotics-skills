<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-shared/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-shared/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-shared
description: SpacemiT Robot SDK 共享基线；用于选择 local、remote、hybrid 开发模式，解析 SROBOTIS_ROOT、SDK root、target、配置优先级和 skill 路由。
---

# SpacemiT Robot Shared

## Mode

先确定开发模式，不确定时根据用户上下文选择最小可行模式：

| Mode | 含义 | 适用场景 |
| --- | --- | --- |
| `local` | Board local | agent 直接运行在板子上，SDK 也在板子上 |
| `remote` | PC remote | agent 在 PC 上，通过 SSH 使用板端 SDK |
| `hybrid` | PC local edit + board build | PC 有本地 SDK 用于编辑，板端有 SDK 用于构建和运行 |

`remote` 和 `hybrid` 的构建、运行、测试都在板端执行。`hybrid` 不交叉编译，不用 PC Docker 构建目标产物，只做 PC 到板端的显式单向同步。

## Config

配置来源优先级如下，前者覆盖后者：

1. 用户本次请求中明确给出的 mode、root、host、target、path。
2. 命令行参数或脚本参数。
3. 环境变量，如 `SROBOTIS_MODE`、`SROBOTIS_ROOT`、`SROBOTIS_REMOTE`、`SROBOTIS_REMOTE_ROOT`、`SROBOTIS_TARGET`。
4. 项目配置，例如 SDK 根或仓库根下的 `.srobotis.yaml`、`.srobotis/config.yaml`。
5. 全局配置，例如 `~/.config/srobotis/config.yaml`。
6. 自动发现。
7. 仍不确定时询问用户。

`SROBOTIS_ROOT` 是唯一 SDK root 环境变量。不要使用其他 SDK root 变量名作为主路径、兼容路径或命令示例。

## SDK Root

完整 SDK root 至少同时包含：

- `build/`
- `components/`
- `application/`
- `target/`

路径语义：

- `local`: `SROBOTIS_ROOT` 指向板端 SDK root。
- `remote`: `SROBOTIS_REMOTE_ROOT` 指向板端 SDK root；PC 可以没有本地 SDK。
- `hybrid`: `SROBOTIS_ROOT` 指向 PC 本地 SDK root，`SROBOTIS_REMOTE_ROOT` 指向板端 SDK root。

`hybrid` 模式下，如果用户没有指定 PC 本地 SDK root，默认使用 `~/workspace/spacemit-robot`。
创建目录、下载 SDK 或执行 repo 初始化前仍必须按 bootstrap 规则向用户确认。

找不到完整 SDK root 时，转到 `spacemit-robot-sdk-bootstrap`。不要在不完整目录里执行 `envsetup.sh`、`lunch`、`m`、`mm` 或组件命令。

## Target

只有需要产品配置、板型驱动或整机镜像上下文时才选择 target。

1. 先识别 board：用户明确给出优先；否则 remote/local 板端可读取 `/sys/firmware/devicetree/base/compatible`。
2. 再读取 SDK 的 `target/*.json`，按 JSON 内容中的 board/product 信息匹配候选。
3. 同一 board 只有一个 product 候选时，可显式使用该 target。
4. 同一 board 有多个 product 候选时，必须询问用户，不自行猜测。
5. 不写死 host、board、target，不从文件名前缀臆造 target。

## Build Hint

- `single_package_first`: 先 `source build/envsetup.sh`，进入模块目录执行 `mm`；遇到产品配置缺口再升级为 target 构建。
- `target_preferred`: 可以先尝试 `mm`，但模块明显依赖板型、驱动或产品配置时，先确定 target。
- `target_required`: 先确定 board 和 target，再执行 `lunch <target>`，然后构建或运行。

默认推导：

- `components/model_zoo/*`: `single_package_first`
- `components/multimedia/*`: `single_package_first`
- `components/control/*`: `target_preferred`
- `application/native/*`: `target_required`
- `application/ros2/*`: `target_required`

专项 skill 的 `metadata.sdk.build_hint` 优先于默认推导。

## Routing

- 首次拉取或初始化 SDK：`spacemit-robot-sdk-bootstrap`
- 软件包安装、ROS2 依赖、SpacemiT 运行包、Python/pip/uv 源配置：`spacemit-robot-software-setup`
- SSH 与远程命令：`spacemit-robot-remote`
- 构建决策：`spacemit-robot-build`
- Hybrid 同步：`spacemit-robot-sync`
- 模块开发：路由到对应组件或应用 skill

执行型请求需要真实执行命令并返回命令、结果、关键日志和失败点。日常开发验证不默认调用 `scripts/test/robot-test`；它只用于 CI、回归验证或用户明确要求的场景。
