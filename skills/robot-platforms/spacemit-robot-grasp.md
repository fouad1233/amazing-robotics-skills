<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-grasp/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-grasp/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-grasp
description: >-
  components/control/grasp 的构建、功能测试与性能测试入口；默认优先跑 dummy
  驱动测试，硬件验证走手工 smoke。
metadata:
  requires:
    bins: ["bash", "cmake"]
  sdk:
    module_paths:
      - components/control/grasp
    primary_docs:
      - components/control/grasp/README.md
      - components/control/grasp/test.yaml
      - components/control/grasp/package.xml
    build_hint: target_preferred
---

# SpacemiT grasp

先按 [`spacemit-robot-shared`](../spacemit-robot-shared/SKILL.md) 确认 SDK 根路径与通用构建规则。
无 SDK 就转 [`spacemit-robot-sdk-bootstrap`](../spacemit-robot-sdk-bootstrap/SKILL.md)，不要在 `robot-skills` 仓库里直接运行测试。

## 何时使用

- 用户要构建、测试或调试 `components/control/grasp`。
- 用户要验证 dummy 夹爪驱动、查看抓取状态机行为，或采集 dummy grasp 延迟。
- 用户要检查 SO-101 夹爪硬件 smoke 测试入口、串口设备或测试日志。

## 默认规则

- `build_hint`: `target_preferred`
- 所有命令都必须在 `$SROBOTIS_ROOT` 下执行，不要假设当前目录与 SDK 相邻。
- 默认先跑 dummy functional / performance，用于快速回归；真实夹爪验证只走 `manual` scope。
- 不默认先读 README；只有命令细节不清、参数语义不清或执行失败时，才回读 `primary_docs`、`CMakeLists.txt` 或 `test/` 源码。

## 固定流程

1. 先确认 SDK 根可用；无 SDK 就转 bootstrap。
2. 在 `$SROBOTIS_ROOT` 下执行 `source build/envsetup.sh`。
3. 先确认 `components/control/grasp/test.yaml` 和
  `components/control/grasp/test/` 下脚本存在。
4. 若只是验证功能，优先跑 `pr` scope 的 dummy functional。
5. 若用户要求性能数据，必须真正执行 performance 用例并返回阈值、平均耗时和日志路径。
6. 若用户要求真实夹爪测试，只走 manual smoke，并明确需要串口设备与人工监看。

## grasp 测试

当用户说“测一下 grasp”“跑一下夹爪性能”时，直接按下面顺序执行：

1. `cd "$SROBOTIS_ROOT" && source build/envsetup.sh`
2. `./scripts/test/robot-test list components/control/grasp`
3. 功能验证：`./scripts/test/robot-test run components/control/grasp --scope pr --category functional`
4. 性能测试：`./scripts/test/robot-test run components/control/grasp --scope scheduled --category performance`
5. 若要发布前口径：`./scripts/test/robot-test run components/control/grasp --scope release --category performance`
6. 若要手工硬件验证：`./scripts/test/robot-test run components/control/grasp --scope manual`
7. 返回测试是否通过、`GRASP_DUMMY_ITERS`、`GRASP_DUMMY_MAX_AVG_US`、平均耗时与日志路径

## 禁止事项

- 不要在 `robot-skills` 仓库目录里直接执行 SDK 测试命令。
- 不要把真实硬件 smoke 放到 `pr`、`scheduled` 或 `release` scope。
- 不要只给命令不执行；用户要求测试时必须真正执行并回传结果或失败点。
- 不要在无硬件时伪造 SO-101 测试结果。

## 常见任务与命令

| 意图 | 动作 |
| ---- | ---- |
| 列出测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test list components/control/grasp` |
| dummy 功能回归 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run components/control/grasp --scope pr --category functional` |
| dummy 性能测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run components/control/grasp --scope scheduled --category performance` |
| 发布前性能测试 | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run components/control/grasp --scope release --category performance` |
| 手工硬件 smoke | `cd "$SROBOTIS_ROOT" && ./scripts/test/robot-test run components/control/grasp --scope manual` |
| 看性能日志 | `tail -f "$SROBOTIS_ROOT"/output/test/<scope>/components__control__grasp/modules/components__control__grasp/logs/grasp_dummy_performance.log` |
