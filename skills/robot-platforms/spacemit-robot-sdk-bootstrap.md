<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-sdk-bootstrap/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-sdk-bootstrap/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-sdk-bootstrap
description: SpacemiT Robot SDK 首次获取与初始化入口；用于 local/remote 在板端准备 SDK，或仅在 hybrid 模式下准备 PC 本地 SDK，负责 repo 基础依赖、工作区、repo init/sync 和 SROBOTIS_ROOT 验证，不承担日常构建或测试。
---

# SpacemiT Robot SDK Bootstrap

先按 `spacemit-robot-shared` 确认 mode 和要初始化的 SDK 对象。本 skill 负责把需要的 SDK 从“没有”变成“完整可 source envsetup”，初始化完成后回到 shared、build、remote、sync 或具体组件 skill。

## 何时使用

适用场景：

- 用户还没有 SDK，需要首次下载。
- 板端没有 `/home/<user>/spacemit_robot` 或用户指定的 SDK root。
- `hybrid` 模式下，PC 需要本地 SDK 作为编辑工作区。
- 现有目录不满足完整 SDK root 检查：缺少 `build/`、`components/`、`application/` 或 `target/`。

不适用场景：

- 日常模块构建、测试、性能验证。
- 组件 API、示例和专项运行说明。
- SDK 已下载后的 ROS2 包、模块 apt 依赖、SpacemiT 运行包、Python/pip/uv 源配置；这些转到 `spacemit-robot-software-setup`。

## 初始化对象

先选择初始化对象，再执行后续流程：

| mode | 需要初始化的 SDK | 默认目录 | 执行位置 |
| --- | --- | --- | --- |
| `local` | 板端 `SROBOTIS_ROOT` | `/home/<user>/spacemit_robot` | 板端本机 |
| `remote` | 板端 `SROBOTIS_REMOTE_ROOT` | `/home/<user>/spacemit_robot` | PC 通过 `spacemit-robot-remote` 在板端执行 |
| `hybrid` | PC 本地 `SROBOTIS_ROOT` | `~/workspace/spacemit-robot` | PC 本机 |
| `hybrid` | 板端 `SROBOTIS_REMOTE_ROOT` | `/home/<user>/spacemit_robot` | PC 通过 `spacemit-robot-remote` 在板端执行 |

规则：

- `remote` 模式不需要 PC 本地 SDK。不要在 PC 上创建、下载或验证 `SROBOTIS_ROOT`，除非用户明确改用 `hybrid`。
- `hybrid` 才需要 PC 本地 SDK。PC 本地 SDK 只负责读代码和改代码；目标产物仍在板端构建和运行。
- `hybrid` 下 PC SDK 和板端 SDK 是两个独立对象；哪个缺失就初始化哪个，已经完整的对象不要重复下载。

## 执行前确认

以下动作属于初始化动作，执行前必须向用户确认目标机器、目录和影响范围：

- `sudo apt update`
- `sudo apt install ...`
- macOS 上的 `brew install ...`
- 创建 SDK 工作目录
- `repo init ...`
- `repo sync ...`
- `repo start ... --all`
- 大量下载、覆盖 manifest、切换分支或复用已有非空目录

不要在未确认的情况下删除目录、清空已有 SDK、重置分支或覆盖用户改动。

## 固定流程

1. 确认当前要初始化的是哪一个 SDK 对象、执行机器、操作系统和 SDK 目录。先做只读检查：

```bash
uname -s || true
test -f /etc/os-release && sed -n '1,8p' /etc/os-release || true
test -d "<sdk-root>/build" && test -d "<sdk-root>/components" && test -d "<sdk-root>/application" && test -d "<sdk-root>/target" || true
test -d "<sdk-root>/.repo" || true
command -v repo || true
command -v git || true
```

2. 如果缺少 `repo`、`git` 或基础工具，按当前初始化对象选择安装方式。

板端 `SROBOTIS_ROOT` 或板端 `SROBOTIS_REMOTE_ROOT`：

```bash
sudo apt update
sudo apt install -y repo git curl
```

如果用户希望首次初始化时顺手准备板端基础构建依赖，可按 SDK `build/package.xml` 继续安装：

```bash
sudo apt install -y build-essential cmake pkg-config wget jq pybind11-dev python3-build
```

`hybrid` 的 PC 本地 `SROBOTIS_ROOT`：

- 先检查 `git`、`repo` 是否存在。
- macOS 不要运行 `apt`；如用户使用 Homebrew 且确认安装，可执行 `brew install git repo`。
- Windows 原生 PowerShell/CMD 不要运行 `apt` 或假设 repo 可用；建议使用 WSL Ubuntu 作为 `hybrid` 的 PC 本地 SDK 工作区，然后按 Linux 流程初始化。
- PC 本地 SDK 默认只用于编辑，不默认安装板端构建依赖。

3. 经用户确认后创建并进入 SDK 工作目录：

```bash
mkdir -p "<sdk-root>"
cd "<sdk-root>"
```

4. 根据目录状态选择 repo 动作。默认分支和 manifest 为 `main` / `default.xml`；如果用户、项目配置或官方文档给出其他分支或 manifest，以明确输入为准。

空目录执行 `repo init`：

GitHub 可稳定访问时：

```bash
repo init -u https://github.com/spacemit-robotics/manifest.git -b main -m default.xml \
  --repo-url=https://gitee.com/spacemit-robotics/git-repo
repo sync -j4
```

国内网络访问 GitHub 不稳定时使用 Gitee manifest：

```bash
repo init -u https://gitee.com/spacemit-robotics/manifest.git -b main -m default.xml \
  --repo-url=https://gitee.com/spacemit-robotics/git-repo
repo sync -j4
```

目录非空且没有 `.repo` 时，不要直接 `repo init` 覆盖语义；先停下来说明目录已有内容，询问用户是否换目录、清空目录或继续复用。

目录已有 `.repo` 时，不要默认重新 `repo init`。先检查现有 manifest，再确认是否复用并续传：

```bash
git -C .repo/manifests remote -v
git -C .repo/manifests branch --show-current || true
repo manifest -r -o - | sed -n '1,80p' || true
repo sync -j4
```

`repo sync -j4` 可用于中断后的续传；失败时先保留工作区并报告失败仓库，不要删除 `.repo` 或重置用户改动。

5. 只有用户明确要进入开发状态时，才创建 repo 开发分支。分支名默认可用 `robot-dev`，但执行前要确认：

```bash
repo branches
repo start robot-dev --all
```

6. 导出 SDK root 并验证。

所有 SDK 对象都要验证目录完整性：

```bash
export SROBOTIS_ROOT="<sdk-root>"
cd "$SROBOTIS_ROOT"
test -d build
test -d components
test -d application
test -d target
```

板端 SDK 对象还要验证 `envsetup.sh`：

```bash
bash -lc 'cd "$SROBOTIS_ROOT" && source build/envsetup.sh'
```

`hybrid` 的 PC 本地 SDK 在 macOS 或 Windows 原生环境中只要求完整 checkout 和可编辑，不要求 `source build/envsetup.sh` 成功。PC 使用 Linux/WSL 时，可以按需验证 `envsetup.sh`。

7. 只有 target 已由用户或 shared 规则明确时，才执行：

```bash
lunch <target>
```

不要使用裸 `lunch` 让 agent 依赖默认项。

## Remote 和 Hybrid

- `remote` 下，目录创建、依赖安装、`repo init/sync` 和验证都通过 `spacemit-robot-remote` 在板端执行。
- `hybrid` 下，PC 本地 SDK 和板端 SDK 是两个初始化对象；PC 用于编辑，板端用于构建和运行。
- `hybrid` 不交叉编译，不用 PC Docker 构建目标产物。

## 完成后输出

初始化完成后输出：

- mode
- 初始化对象和执行机器
- 当前模式适用的 `SROBOTIS_ROOT`
- 当前模式适用的 `SROBOTIS_REMOTE`
- 当前模式适用的 `SROBOTIS_REMOTE_ROOT`
- manifest 源、branch、manifest 文件
- SDK root 检查结果
- 板端 `source build/envsetup.sh` 验证结果
- 是否已明确 target

`remote` 模式下不要要求或输出 PC 本地 `SROBOTIS_ROOT`。然后转回相应 skill 继续工作，不在 bootstrap 内继续日常构建或测试。
