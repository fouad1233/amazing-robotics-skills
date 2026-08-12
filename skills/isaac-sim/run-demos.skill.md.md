<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    ai/skills/run-demos.skill.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/ai/skills/run-demos.skill.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: run-demos
triggers: ["run a demo", "list demos", "launch quadruped demo", "show me isaac lab training", "what demos are available"]
summary: Install and launch the out-of-the-box Isaac demos that ship as desktop shortcuts on the workstation.
---

# run-demos <!-- omit in toc -->

- [What demos are](#what-demos-are)
- [Available demos](#available-demos)
- [1. Enable a demo at deploy time](#1-enable-a-demo-at-deploy-time)
- [2. Launch it](#2-launch-it)
- [3. Watch it](#3-watch-it)
- [Tuning a demo](#tuning-a-demo)

## What demos are

Demos are curated, ready-to-run examples installed on the workstation as **double-click desktop shortcuts**,
chosen at deploy time with `--demos`. Selecting a demo **auto-enables the apps it depends on** (e.g. Isaac Sim
and Isaac Lab), so one flag is enough to get a working setup. Each demo also installs a launcher script under
`~/.local/share/isaac-automator-demos/<demo>.sh` that you can run directly over SSH.

## Available demos

- **`quadruped-locomotion`** - trains an ANYmal-D quadruped to walk with RSL-RL in Isaac Lab, rendered in the
  Isaac Sim viewport. Depends on Isaac Sim + Isaac Lab (auto-enabled).

To see the current list for a given build, check the `--demos` help on a deploy command, or look under
`src/ansible/roles/demos/tasks/` in the source.

## 1. Enable a demo at deploy time

Add `--demos <name>[,<name>...]` to the deploy command (see `deploy-workstation.skill.md`). Example:

```sh
./deploy-aws --deployment-name demo-rig-1 --region us-east-1 --instance-type g5.2xlarge \
  --not-from-image --ingress-cidrs myip --isaaclab-arena no \
  --demos quadruped-locomotion --existing replace --no-upload --debug
```

You do not need to pass `--isaacsim` / `--isaaclab` - the demo turns them on with their default versions if
you left them off. (You can still pass explicit versions to pin them.)

## 2. Launch it

Two ways:

- **Interactive:** connect via the desktop (NoMachine or noVNC) and double-click the demo shortcut on the
  desktop (e.g. "Quadruped Locomotion RL Demo"). It opens a terminal and starts the run.
- **Over SSH (headless-friendly):** run the launcher script on the workstation's display:

  ```sh
  ./ssh <name>
  # then on the VM:
  DISPLAY=:0 ~/.local/share/isaac-automator-demos/quadruped-locomotion.sh
  ```

The quadruped demo prints live RSL-RL training output (per-iteration reward and metrics) as it runs.

## 3. Watch it

The 3D viewport renders through Omniverse Kit's Vulkan surface, which **noVNC does not capture** - over noVNC
you will see the desktop but usually a blank viewport. To watch the live robots, connect with **NoMachine**
(see `connect-workstation.skill.md`).

To get a rendered file without a display, run the underlying Isaac Lab script with video recording instead of
relying on a screen capture, e.g. add `--headless --enable_cameras --video --video_length 600` to a
`train.py` / `play.py` invocation; the videos are written under the run's `logs/.../videos/` directory.

## Tuning a demo

The quadruped launcher honors environment variables, so you can adjust it without editing anything:

```sh
HEADLESS=1 NUM_ENVS=4096 MAX_ITERATIONS=1500 \
  ~/.local/share/isaac-automator-demos/quadruped-locomotion.sh
```

- `HEADLESS=1` - no GUI window (fastest training; nothing to watch).
- `NUM_ENVS` - parallel environments (more = faster learning, heavier GPU; lower for a lighter live view).
- `MAX_ITERATIONS` - how long to train.
