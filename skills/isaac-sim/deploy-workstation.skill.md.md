<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    ai/skills/deploy-workstation.skill.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/ai/skills/deploy-workstation.skill.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: deploy-workstation
triggers: ["deploy isaac", "deploy a workstation", "spin up isaac sim", "create an isaac lab machine", "get me a cloud GPU with isaac"]
summary: Deploy a new Isaac Workstation (Isaac Sim / Isaac Lab / Arena, optional demos) to a public cloud, non-interactively.
---

# deploy-workstation <!-- omit in toc -->

- [1. Pick the basics](#1-pick-the-basics)
- [2. Set up credentials](#2-set-up-credentials)
- [3. Build the image (once)](#3-build-the-image-once)
- [4. Deploy](#4-deploy)
- [5. Per-cloud specifics](#5-per-cloud-specifics)
- [6. After deploy](#6-after-deploy)
- [Faster, cheaper deploys (--from-image)](#faster-cheaper-deploys---from-image)

Deploy a fully configured Isaac Workstation. This provisions **real, paid** cloud resources - confirm the
target, cloud, and that cleanup will happen before you start. A full source deploy takes roughly 45-60 min;
`--from-image` is roughly 10-15 min.

## 1. Pick the basics

- **Cloud:** `aws` | `gcp` | `azure` | `alicloud`.
- **Deployment name** (`--deployment-name`): unique, lowercase letters / digits / `-`, <= 32 chars. e.g.
  `demo-rig-1`.
- **Region** and **instance type**: each cloud has a default; pick the cheapest viable GPU instance unless
  told otherwise. Cheap viable picks: AWS `g5.2xlarge`, Azure `Standard_NV6ads_A10_v5`, GCP `g2-standard-8`,
  Alibaba `ecs.gn7i-c16g1.4xlarge`.
- **What to install:** any of Isaac Sim (`--isaacsim`), Isaac Lab (`--isaaclab`), Isaac Lab Arena
  (`--isaaclab-arena`). Each takes a git ref (e.g. a release tag) or `no` to skip.
- **Demos** (`--demos`): a comma-separated list, or `no`. A demo auto-enables the apps it needs (see
  `run-demos.skill.md`). e.g. `--demos quadruped-locomotion` will turn on Isaac Sim + Isaac Lab for you.

## 2. Set up credentials

- **AWS (simplest):** export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` and forward them into the
  container with `-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY`. If those are not set, the tool falls back to
  the AWS IAM Identity Center (SSO) device-code login flow, which needs a human to open a URL and enter a
  code - avoid in headless automation.
- **GCP / Azure / Alibaba:** authenticate with that cloud's normal CLI/credentials before deploying; the
  container reads the standard credential locations (mounted via the working directory). Obtain credentials
  from the user if you do not have them.

Never echo or commit these values.

## 3. Build the image (once)

```sh
./build
```

Only needed once per machine (or after the tool itself changes).

## 4. Deploy

Pass **every** required option so nothing prompts. Headless-safe form (AWS shown):

```sh
docker run --rm --network host -v "$(pwd)":/app \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
  isaac_automator \
  "unset AWS_PROFILE; ./deploy-aws \
     --deployment-name demo-rig-1 \
     --region us-east-1 \
     --instance-type g5.2xlarge \
     --not-from-image \
     --ingress-cidrs myip \
     --isaacsim v6.0.0 \
     --isaaclab release/3.0.0-beta2 \
     --isaaclab-arena no \
     --demos quadruped-locomotion \
     --existing replace \
     --no-upload \
     --debug"
```

Key common options (all clouds):

- `--deployment-name` / `--dn` - the name used by every later command.
- `--ingress-cidrs` - who may reach the VM. `myip` = your current public IP only (recommended). Also accepts
  explicit CIDRs, or `0.0.0.0/0` for anywhere (not recommended).
- `--isaacsim` / `--isaaclab` / `--isaaclab-arena` - git ref to install, or `no`.
- `--demos` - demos to install, or `no` (auto-enables required apps).
- `--from-image` / `--not-from-image` - deploy from a pre-built image (fast) or from bare OS (full).
- `--existing` - what to do if the name already exists: `replace` (delete and redeploy), `repair`, `modify`,
  `run_ansible`, or `ask` (do not use `ask` in automation - it prompts).
- `--upload` / `--no-upload` - upload your local `uploads/` to the VM during deploy.
- `--vnc-password`, `--system-user-password` - set explicitly if you want known values (otherwise random).
- `--debug` - verbose progress; useful when streaming a long deploy.

Long deploys: run it detached and stream milestones (`terraform` actions, Ansible `PLAY [` / `TASK [`
headers, `fatal:` / `Error`, and the final `PLAY RECAP`) rather than waiting blind. Success looks like a
`PLAY RECAP` with `failed=0`.

## 5. Per-cloud specifics

- **AWS** (`./deploy-aws`): `--region`, `--instance-type`. Default instance is an L40S `g6e.2xlarge`.
- **GCP** (`./deploy-gcp`): adds `--zone`, `--project`, and `--isaac-workstation-gpu-count` (1/2/4/8).
- **Azure** (`./deploy-azure`): adds `--resource-group` and `--login` / `--no-login`.
- **Alibaba** (`./deploy-alicloud`): `--region` (default `us-east-1`).

## 6. After deploy

The command prints (and saves to `state/<name>/info.txt`) the public IP and how to connect via SSH, noVNC,
and NoMachine. Next:

- Connect: `connect-workstation.skill.md`.
- Run a demo: `run-demos.skill.md`.
- Control cost / tear down: `manage-lifecycle.skill.md`.

## Faster, cheaper deploys (--from-image)

A full deploy builds Isaac Sim from source (slow). To deploy many times quickly, first bake a pre-built image
once with `./image-aws` / `./image-gcp` / `./image-azure`, then deploy with `--from-image` (roughly 10-15 min
and far cheaper per deploy). Use a full `--not-from-image` deploy when you specifically need to validate a
from-scratch install or no pre-built image exists.
