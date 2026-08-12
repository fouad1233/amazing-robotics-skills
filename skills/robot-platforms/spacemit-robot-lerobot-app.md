<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-lerobot-app/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-lerobot-app/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-lerobot-app
description: >-
  application/native/lerobot_app 的专项入口；聚焦 ACT dummy inference 功能
  与性能测试，默认通过 robot-test 或测试脚本产出日志到 SDK output 目录。
metadata:
  requires:
    bins: ["bash", "python3"]
  sdk:
    module_paths:
      - application/native/lerobot_app
    primary_docs:
      - application/native/lerobot_app/README.md
      - application/native/lerobot_app/test.yaml
      - application/native/lerobot_app/package.xml
    build_hint: single_package_first
---

# SpacemiT lerobot_app

先按 [`spacemit-robot-shared`](../spacemit-robot-shared/SKILL.md) 确认 SDK 根路径与通用构建规则。
无 SDK 就转 [`spacemit-robot-sdk-bootstrap`](../spacemit-robot-sdk-bootstrap/SKILL.md)，不要在 `robot-skills` 仓库里直接运行测试。

## 何时使用

- 用户要测试 `application/native/lerobot_app`。
- 用户要跑 ACT dummy inference、采集 avg/p50/p95 latency，或检查 `lerobot_app` 的 scheduled/release 性能用例。
- 用户要定位 `lerobot_app` 的 Python 环境、模型下载、benchmark 日志或性能阈值问题。

## 默认规则

- `build_hint`: `single_package_first`
- `robot-skills` 仓库与 SDK 仓库独立；所有命令都必须在 `$SROBOTIS_ROOT` 下执行，不要假设当前目录与 SDK 相邻。
- 不默认先读总览文档或 README；只有命令细节、参数语义或失败点不清，才回读 `primary_docs`、测试脚本或 benchmark 源码。
- `pr` 用例只做轻量验证；ACT 模型下载和性能测试默认走 `scheduled`、`release` 或显式手动执行。

## 固定流程

1. 先确认 SDK 根可用；若当前机器没有完整 SDK，就转 bootstrap，不在 `robot-skills` 仓库里直接运行测试。
2. 在 `$SROBOTIS_ROOT` 下执行 `source build/envsetup.sh`。
3. 测试前先确认 `scripts/test/robot-test`、
  `application/native/lerobot_app/test.yaml`、
  `application/native/lerobot_app/tests/test_act_dummy_performance.sh` 存在。
4. 若 `robot-test` 缺 `PyYAML` 或 Python 依赖，按报错补装；不要先做脱离上下文的大段环境排查。
5. 对“帮我跑一下 / 测一下性能 / 给我性能数据”这类请求，必须真正执行测试并返回指标、日志路径、阈值和失败点，而不只是给命令。

## ACT 性能测试

当用户说“测试 lerobot_app 的 ACT 性能”、“跑一下 ACT benchmark”时，直接按下面顺序执行：

1. 进入 SDK 根目录：

```bash
cd "$SROBOTIS_ROOT"
source build/envsetup.sh
```

2. 先列出测试定义，确认模块和 scope：

```bash
./scripts/test/robot-test list application/native/lerobot_app
```

3. 若只是快速验证功能，跑 `scheduled` 的 functional：

```bash
./scripts/test/robot-test run application/native/lerobot_app --scope scheduled --category functional
```

4. 若目标是性能数据，优先跑 performance 用例：

```bash
./scripts/test/robot-test run application/native/lerobot_app --scope scheduled --category performance
```

5. 如果需要发布前口径，则跑 `release` scope：

```bash
./scripts/test/robot-test run application/native/lerobot_app --scope release --category performance
```

6. 测试执行中，实时查看 runner 日志和测试脚本日志：

```bash
tail -f output/test/<scope>/application__native__lerobot_app/modules/application__native__lerobot_app/lerobot-app-act-dummy-performance.log
tail -f output/test/<scope>/application__native__lerobot_app/modules/application__native__lerobot_app/logs/act_dummy_performance.log
```

7. 测试完成后，返回以下结果：
   - `avg` / `p50` / `p95` / `min` / `max`
   - 使用的 `LEROBOT_ACT_WARMUP`、`LEROBOT_ACT_ITERS`、`LEROBOT_ACT_MAX_AVG_MS`
   - 模型缓存目录和日志路径
   - 若失败，指出是下载失败、模型文件缺失、Python 依赖失败还是阈值超限

8. 若用户要求更严格阈值或更多统计轮次，直接带环境变量重跑，例如：

```bash
LEROBOT_ACT_WARMUP=3 LEROBOT_ACT_ITERS=10 LEROBOT_ACT_MAX_AVG_MS=5000 \
./scripts/test/robot-test run application/native/lerobot_app --scope scheduled --category performance
```

## 禁止事项

- 不要在 `robot-skills` 仓库目录里直接执行 SDK 测试命令。
- 不要把 ACT 性能测试放到 `pr` scope。
- 不要只返回“命令已给出”；用户要求测试时必须真正执行，并回传性能指标或失败点。
- 不要写死具体 target 名；需要 target 时按 shared 规则先确认 board 和 target。

## 常见任务与命令

| 意图 | 动作 |
| ---- | ---- |
| 列出测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test list application/native/lerobot_app` |
| PR 轻量验证 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run application/native/lerobot_app --scope pr` |
| ACT 功能测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run application/native/lerobot_app --scope scheduled --category functional` |
| ACT 性能测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run application/native/lerobot_app --scope scheduled --category performance` |
| 发布前性能测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run application/native/lerobot_app --scope release --category performance` |
| 直接看性能日志 | `tail -f "$SROBOTIS_ROOT"/output/test/<scope>/application__native__lerobot_app/modules/application__native__lerobot_app/logs/act_dummy_performance.log` |
