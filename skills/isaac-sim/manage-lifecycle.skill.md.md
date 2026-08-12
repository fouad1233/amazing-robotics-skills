<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    ai/skills/manage-lifecycle.skill.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/ai/skills/manage-lifecycle.skill.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: manage-lifecycle
triggers: ["stop the workstation", "start the rig", "destroy the deployment", "repair isaac", "check deployment status", "how much is it costing"]
summary: Control a deployment's cost and health - status, stop/start, repair, and destroy.
---

# manage-lifecycle <!-- omit in toc -->

- [Cost model (why this matters)](#cost-model-why-this-matters)
- [Status](#status)
- [Stop (pause billing for compute)](#stop-pause-billing-for-compute)
- [Start (resume)](#start-resume)
- [Repair](#repair)
- [Destroy (stop all billing)](#destroy-stop-all-billing)

Every command takes the deployment name you chose at deploy.

## Cost model (why this matters)

- A **running** instance bills for compute (the expensive part) plus storage.
- A **stopped** instance bills only for storage (cheap, but not zero).
- **Destroying** removes the resources and stops all billing.

So: `./stop` when idle, `./start` to resume, and `./destroy` when you are truly done. The public IP is
preserved across stop/start, so connection URLs keep working after a resume.

## Status

Check whether the instance exists and its state (running / stopped / ...). Quick direct check on AWS:

```sh
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY isaac_automator \
  "unset AWS_PROFILE; AWS_DEFAULT_REGION=<region> aws ec2 describe-instances \
     --filters Name=tag:Deployment,Values=<name> \
     --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text"
```

You can also read `state/<name>/info.txt` for the saved connection details.

## Stop (pause billing for compute)

```sh
./stop <name>
```

Stops the instance but keeps it (and its disk + IP). Use whenever you are done for now but will come back.

## Start (resume)

```sh
./start <name>
```

Boots the stopped instance again; same IP. After boot the autorun launches the default app on the desktop.
If a `./start --quick` runs Ansible before sshd is ready you may see `unreachable=1` - just re-run `./start`.

## Repair

```sh
./repair <name>
```

Re-applies configuration without changing your chosen parameters. Use it when a deployment came up unhealthy
(e.g. a service did not start, or after an image driver/library mismatch + reboot).

## Destroy (stop all billing)

```sh
./destroy <name> --yes
```

Deletes the deployment's cloud resources. This is the only thing that stops storage cost. **Always destroy
what you created when you are finished.** Afterward, verify nothing tagged with your deployment name remains
(instances, VPC, static IPs); if `./destroy` failed partway (e.g. a dependency error on the VPC), clean up the
leftover resources by hand and re-run.
