<!-- Vendored from nebius/nebius-physical-ai @ main
     Path:    skills/atomic/physical-ai-context/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/nebius/nebius-physical-ai/blob/main/skills/atomic/physical-ai-context/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: physical-ai-context
description: Use for Claude Code reviews that need robotics, simulation, GPU-routing, sim-to-real, or BDD100K pipeline domain context.
---

# Physical AI Context

Sim-to-real pipeline:

1. RL teacher policy in Genesis.
2. Visual demos.
3. SimToLeRobot adapter.
4. Student policy in LeRobot.
5. Evaluation in Genesis.

Isaac Lab requires RT cores: L40S or RTX Pro 6000. H100 and H200 do not have RT cores.

Genesis serverless RL teacher training works. Visual demo generation is blocked on EGL/DRI device access in containers.

LeRobot is Tier 1 validated on B300. SONIC, GR00T, Isaac Lab, and Cosmos are vendor-paced on NVIDIA CUDA 13 alignment.

Route SONIC to H100; L40S on-demand capacity is effectively zero for the required preset.

The BDD100K demo pipeline is the reference end-to-end workflow. `npa/workflows/workbench/npa-workflows/bdd100k-pipeline.yaml` is the canonical YAML.

Key claim: one YAML file describes a full Physical AI pipeline; SkyPilot orchestrates it on Nebius; results complete in about 30 minutes on a single H100.
