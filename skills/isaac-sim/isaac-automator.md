<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    skills/isaac-automator/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/skills/isaac-automator/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: isaac-automator
description: >
  Deploy and operate a cloud Isaac Workstation with Isaac Automator: provision a
  GPU VM running Isaac Sim, Isaac Lab, and/or Isaac Lab Arena on AWS, GCP, Azure,
  or Alibaba Cloud, connect to it, move data in and out, control cost with
  stop/start, repair, import existing deployments, and destroy. Use when the user
  wants a ready-to-use remote Isaac workstation in the cloud via the Isaac
  Automator CLI. Do NOT use for installing Isaac Sim/Lab on local hardware, for
  developing Isaac Automator itself, or for writing simulation / robot-learning
  code.
license: Apache-2.0
metadata:
  author: NVIDIA <agent-skills@nvidia.com>
  tags:
    - isaac-automator
    - isaac-sim
    - isaac-lab
    - deployment
    - cloud
    - remote-desktop
    - aws
    - gcp
    - azure
---

# Isaac Automator: deploy and operate a cloud Isaac Workstation

Isaac Automator provisions a GPU virtual machine in a public cloud and configures it as a ready-to-use
**Isaac Workstation**: a remote-desktop VM with Isaac Sim, Isaac Lab, and/or Isaac Lab Arena installed, plus
lifecycle controls, data transfer, and import of existing deployments. This skill drives that CLI to stand up,
use, and tear down a workstation.

Source: https://github.com/isaac-sim/IsaacAutomator

## When to use this skill

Use it when the user wants to deploy, connect to, manage cost on, or tear down a cloud Isaac workstation via
Isaac Automator. Do NOT use it for installing Isaac Sim/Lab on local hardware, for changing Isaac Automator's
own code (Python/Terraform/Ansible), or for writing simulation or robot-learning code.

## Before you act

- **It provisions real, paid cloud infrastructure.** A running deployment bills by the hour; a stopped
  instance still bills for storage. Treat every deploy and start as live-fire.
- **Always clean up.** Stop when idle (`./stop`), and **destroy** (`./destroy <name> --yes`) when done. Only
  destroy stops all cost.
- **Run non-interactively.** The deploy commands prompt for any required option you omit; an unanswered
  prompt blocks an agent. Supply every required option and use `--existing replace` (never `ask`).
- **Never print or commit credentials.** Pass them via environment variables and reference them by name.

## Prerequisites

1. Docker installed and running.
2. Cloud credentials for the target cloud. For AWS the simplest path is `AWS_ACCESS_KEY_ID` +
   `AWS_SECRET_ACCESS_KEY` in the environment; otherwise the tool falls back to an interactive SSO login.
3. Build the container image once, from the repo root: `./build`.

Everything runs inside the `isaac_automator` Docker image, and the top-level scripts wrap it. The `./run`
helper uses an interactive TTY; for a headless agent, call the container directly instead:

```sh
docker run --rm --network host -v "$(pwd)":/app \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
  isaac_automator "<command>"
```

## Instructions

### 1. Gather requirements

Ask the user if not already clear:

1. **Cloud** - AWS, GCP, Azure, or Alibaba Cloud?
2. **Apps** - Isaac Sim, Isaac Lab, Isaac Lab Arena (any combination), and which versions (git ref) or `no`.
3. **Region and instance type** - or accept the cheapest viable GPU default.
4. **Budget/lifetime** - so you stop or destroy it promptly afterward.

### 2. Deploy

Each deployment has a unique name (lowercase letters, digits, `-`). Common options (all clouds):
`--deployment-name`/`--dn`, `--region`, `--instance-type`, `--ingress-cidrs` (`myip` = your current public
IP), `--isaacsim` / `--isaaclab` / `--isaaclab-arena` (git ref or `no`), `--from-image` / `--not-from-image`,
`--existing` (`replace` / `repair` / `modify` / `run_ansible`), `--upload` / `--no-upload`.

Per-cloud commands and extras:

- `./deploy-aws` - `--region`, `--instance-type`.
- `./deploy-gcp` - adds `--zone`, `--project`, `--isaac-workstation-gpu-count`.
- `./deploy-azure` - adds `--resource-group`, `--login` / `--no-login`.
- `./deploy-alicloud` - `--region`.

`--from-image` deploys from a pre-built image (~10-15 min, cheaper); `--not-from-image` does a full
build-from-source (~45-60 min). A successful deploy ends with an Ansible `PLAY RECAP` showing `failed=0` and
prints connection info (also saved to `state/<name>/info.txt`).

### 3. Connect

- **noVNC (browser, 2D desktop):** `./novnc <name>` prints a URL on port 6080. Good for the desktop, files,
  and launching apps.
- **NoMachine (live 3D viewport):** connect a NoMachine client to the VM's public IP, key-based auth with
  `state/<name>/key.pem`, user `ubuntu`. **Use this to watch the live Isaac Sim/Lab 3D viewport** - Omniverse
  Kit renders through a Vulkan surface that noVNC does not capture, so noVNC shows the desktop but usually not
  the rendered 3D.
- **SSH (shell):** `./ssh <name>` for logs, launching commands, or recording viewport video headlessly.

### 4. Move data and auto-launch

- **Upload inputs:** put files in the local `uploads/` folder, then `./upload <name>` (copies to `~/uploads`
  on the VM). You can also upload during deploy with `--upload` (default) instead of `--no-upload`.
- **Download results:** `./download <name>` pulls `~/results` from the VM into your local `results/` folder.
- **Autorun on boot:** place a script at `uploads/autorun.sh`; when present, the desktop runs it on boot and
  after each start instead of launching Isaac Sim by default.

### 5. Control cost, repair, import, tear down

- `./stop <name>` - stop the instance (keeps it and its public IP; pauses compute billing).
- `./start <name>` - resume; the public IP is preserved across stop/start.
- `./repair <name>` - re-apply configuration if the workstation came up unhealthy.
- `./import --cloud <aws|gcp|azure|alicloud> ...` - bring existing cloud resources under Isaac Automator
  management (creates local state for a deployment that already exists).
- `./destroy <name> --yes` - delete the deployment and stop all billing. **Always do this when finished**,
  then confirm no resources tagged with the deployment name remain.

### Faster, repeated deploys (prebuilt images)

For many deploys, bake a prebuilt image once with `./image-aws` / `./image-gcp` / `./image-azure`, then deploy
with `--from-image`. Use a full `--not-from-image` deploy when you need a from-scratch install or no prebuilt
image exists.

## Examples

**Deploy Isaac Sim + Isaac Lab on AWS, locked to your IP:**

```sh
./deploy-aws \
  --deployment-name my-rig \
  --region us-east-1 \
  --instance-type g5.2xlarge \
  --not-from-image \
  --ingress-cidrs myip \
  --isaacsim v6.0.0 \
  --isaaclab release/3.0.0-beta2 \
  --isaaclab-arena no \
  --existing replace \
  --no-upload
```

**Deploy Isaac Sim only on GCP with 1 GPU:**

```sh
./deploy-gcp --deployment-name gcp-sim --zone us-central1-a --project my-project \
  --isaac-workstation-gpu-count 1 --isaacsim v6.0.0 --isaaclab no --isaaclab-arena no \
  --ingress-cidrs myip --existing replace --no-upload
```

**Pause cost overnight, then resume next day (same IP):**

```sh
./stop my-rig
./start my-rig
```

**Finish and stop all cost:**

```sh
./destroy my-rig --yes
```

## Troubleshooting

- **Blank viewport over noVNC:** expected (Vulkan, see Connect) - use NoMachine, or record headlessly with the
  app's own video recorder.
- **A command hangs:** an unanswered interactive prompt; pass all required options and avoid `--existing ask`.
- **`cannot attach stdin to a TTY-enabled container`:** call the container directly (see Prerequisites)
  instead of `./run`.
- **Driver/library mismatch after a `--from-image` deploy:** reboot the instance, then `./repair <name>`.
- **Cannot reach noVNC/SSH:** your public IP changed since deploy; re-deploy with `--existing modify` and the
  correct `--ingress-cidrs`.
