<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    ai/skills/troubleshoot.skill.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/ai/skills/troubleshoot.skill.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: troubleshoot
triggers: ["isaac sim viewport is black", "deploy hangs", "command is stuck", "cannot connect", "driver mismatch", "demo wont show"]
summary: Diagnose and fix the common Isaac Automator failure modes from the operator side.
---

# troubleshoot <!-- omit in toc -->

- [Viewport is blank over noVNC](#viewport-is-blank-over-novnc)
- [A command hangs forever](#a-command-hangs-forever)
- [cannot attach stdin to a TTY-enabled container](#cannot-attach-stdin-to-a-tty-enabled-container)
- [Deploy or start finished but the machine is unhealthy](#deploy-or-start-finished-but-the-machine-is-unhealthy)
- [start ran Ansible before SSH was ready (unreachable=1)](#start-ran-ansible-before-ssh-was-ready-unreachable1)
- [Cannot reach noVNC / SSH](#cannot-reach-novnc--ssh)
- [AWS credentials rejected or SSO prompt appears](#aws-credentials-rejected-or-sso-prompt-appears)
- [Forgot what is still running / leftover cost](#forgot-what-is-still-running--leftover-cost)

## Viewport is blank over noVNC

Expected, not a bug. Omniverse Kit renders the 3D viewport through a Vulkan surface that the VNC server does
not capture, so noVNC shows the desktop but an empty viewport. To see rendered output:

- Connect with **NoMachine** (GPU/Vulkan-aware) - see `connect-workstation.skill.md`; or
- Record the viewport headlessly with the app's own recorder (Isaac Lab `--video --enable_cameras`), then
  download the file.

The app is still running; only the live display path is the issue.

## A command hangs forever

Almost always an unanswered interactive prompt. The deploy commands prompt for any required option you did
not supply, and `--existing ask` prompts too. Fix: pass **every** required option non-interactively and use
`--existing replace` (or `repair` / `modify` / `run_ansible`), never `ask`, in automation.

## cannot attach stdin to a TTY-enabled container

`./run` (and the auto-container path) uses `docker run -it`, which needs a TTY. In a headless/agent shell,
invoke the container directly instead:

```sh
docker run --rm --network host -v "$(pwd)":/app \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
  isaac_automator "<command>"
```

## Deploy or start finished but the machine is unhealthy

Run `./repair <name>` to re-apply configuration without changing your parameters. A common cause on
`--from-image` deploys is a stale NVIDIA driver/library version mismatch; reboot the instance, then
`./repair`.

## start ran Ansible before SSH was ready (unreachable=1)

A fast start can kick off configuration before sshd is up, giving `unreachable=1`. Just run `./start <name>`
again once the instance has finished booting.

## Cannot reach noVNC / SSH

The security group only allows the CIDRs you set at deploy. If you deployed with `--ingress-cidrs myip` and
your public IP changed (or you are connecting from a different network), the ports are no longer open to you.
Re-deploy with `--existing modify` and the correct `--ingress-cidrs`, or add your current IP. noVNC needs
port 6080 and SSH needs the SSH port (default 22) reachable from your address.

## AWS credentials rejected or SSO prompt appears

If `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are unset or invalid, the tool falls back to the AWS SSO
device-code login, which blocks waiting for a human. For headless runs, set valid env-var credentials and
forward them with `-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY`, and `unset AWS_PROFILE` in the command so
the env vars take precedence.

## Forgot what is still running / leftover cost

List your resources by deployment tag and destroy when done:

```sh
# what is still tagged to this deployment (AWS)
aws ec2 describe-instances --filters Name=tag:Deployment,Values=<name> \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text

./destroy <name> --yes   # removes resources, stops all billing
```

If `./destroy` fails partway (e.g. a VPC dependency error), remove the leftover resources by hand and re-run
until nothing tagged with your deployment name remains.
