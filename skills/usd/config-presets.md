<!-- Vendored from NVIDIA-Omniverse/usd-optimize @ main
     Path:    .agents/skills/config-presets/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/NVIDIA-Omniverse/usd-optimize/blob/main/.agents/skills/config-presets/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: config-presets
description: Choose and run a ready-made preset operation stack from config_presets/. Use when you want a known-good starting point for a common optimization goal rather than assembling a custom config.
version: "1.0.0"
allowed-tools: Shell, Read
metadata:
  author: NVIDIA Corporation
  tags: [usd, optimization, presets, config]
---

<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# config-presets — Ready-made operation stacks

Each `config_presets/*.json` file is a ready-to-run operation stack: a JSON
array of operations applied to a stage in order. Use one to get a known-good
baseline before considering custom tuning.

## What this skill covers

- **Preset table** — the six presets, their effect, and whether they are lossless.
- **Step 1** — pick a preset for the user's goal.
- **Step 2** — run the preset.
- **Step 3** — customize if needed.

Companion skills: [`run-operations`](../run-operations/SKILL.md) (CLI usage,
flags, error handling), [`tune-parameters`](../tune-parameters/SKILL.md)
(iterate a single operation's parameters after running a preset).

---

## Preset table

| Preset | Effect | Lossless? |
|---|---|---|
| `safe-cleanup.json` | Extents, prune empty leaves, dedup geometry, dedup materials, drop redundant time samples. | Yes |
| `memory-reduction.json` | Dedup geometry into instances, dedup materials, prune leaves. | Yes |
| `load-time-reduction.json` | Author extents, prune leaves, drop redundant time samples, dedup materials. | Yes |
| `hierarchy-dedup.json` | Collapse duplicate hierarchies, then dedup remaining geometry. | Yes |
| `data-quality-baseline.json` | Generate normals, mesh cleanup (merge verts, manifold, coorient), extents. | Bounded loss |
| `mesh-count-reduction.json` | Mesh cleanup, dedup geometry, remove small geometry, conservative decimation (error-bounded by `maxMeanError`, not `reductionFactor`). | Bounded loss |

When the user is unsure, recommend `safe-cleanup` — it is conservative and
fully lossless.

## Step 1 — pick a preset

Match the user's goal to a preset:

- General cleanup with no risk: `safe-cleanup`
- Reduce runtime memory: `memory-reduction`
- Reduce load time: `load-time-reduction`
- Duplicate subtrees dominate the scene: `hierarchy-dedup`
- Fix mesh quality issues: `data-quality-baseline`
- Reduce draw-call or polygon count: `mesh-count-reduction`

If the goal spans multiple presets (e.g. memory AND load time), run them in
sequence or combine their operation lists into a custom config.

## Step 2 — run the preset

```bash
_build/<platform>/<config>/bin/usdOptimize \
    -i input.usd \
    -c config_presets/<preset>.json \
    -s -r \
    -w output.usd
```

`<platform>` is e.g. `linux-x86_64`; `<config>` is `release` or `debug`.
`-s` captures before/after stats; `-r` emits a per-operation report. See
[`docs/cli.rst`](../../../docs/cli.rst) for the full flag list.

## Step 3 — customize if needed

To adjust a preset, copy the JSON file and edit the operation list. Per-operation
arguments and defaults are in [`docs/operations/<key>.rst`](../../../docs/operations/);
guidance on which operations help which goal is in
[`docs/choosing-operations.rst`](../../../docs/choosing-operations.rst).

For interactive parameter tuning on a single operation, use
[`/tune-parameters`](../tune-parameters/SKILL.md).
