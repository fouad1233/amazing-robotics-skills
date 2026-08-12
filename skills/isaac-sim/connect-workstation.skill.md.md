<!-- Vendored from isaac-sim/IsaacAutomator @ main
     Path:    ai/skills/connect-workstation.skill.md
     Licence: Apache-2.0
     Source:  https://github.com/isaac-sim/IsaacAutomator/blob/main/ai/skills/connect-workstation.skill.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: connect-workstation
triggers: ["connect to the workstation", "open novnc", "ssh into the rig", "use nomachine", "how do I see the desktop"]
summary: Connect to a deployed Isaac Workstation via noVNC (browser), NoMachine (3D), or SSH (shell).
---

# connect-workstation <!-- omit in toc -->

- [Pick the right method](#pick-the-right-method)
- [noVNC (browser desktop)](#novnc-browser-desktop)
- [NoMachine (live 3D viewport)](#nomachine-live-3d-viewport)
- [SSH (shell)](#ssh-shell)

You need a deployed, running workstation (see `deploy-workstation.skill.md`; resume a stopped one with
`./start <name>`). All connection info is also saved in `state/<name>/info.txt`.

## Pick the right method

| Want to... | Use |
|---|---|
| Click around the desktop, launch apps, open a terminal | noVNC |
| Watch the live Isaac Sim / Isaac Lab 3D viewport (rendered robots) | NoMachine |
| Run commands, read logs, launch a demo headlessly | SSH |

The 3D viewport is the key distinction: Omniverse Kit renders via a Vulkan surface that noVNC does **not**
capture, so the live 3D shows under NoMachine, not noVNC.

## noVNC (browser desktop)

```sh
./novnc <name>
```

This prints a URL of the form
`http://<ip>:6080/vnc.html?host=<ip>&port=6080&password=<vnc_password>&resize=scale`. Open it in a browser
(add `&autoconnect=true` to connect immediately). The VNC password is the one set at deploy
(`--vnc-password`, or the random value saved in `state/<name>/meta.json`). noVNC requires port 6080 to be
reachable from your IP - deploy with `--ingress-cidrs myip` so it is.

## NoMachine (live 3D viewport)

1. Install the NoMachine client from https://downloads.nomachine.com/ and launch it.
2. Add a connection to **Host** = the workstation public IP.
3. Use key-based auth with the private key at `state/<name>/key.pem`.
4. Connect and log in as the SSH user (default `ubuntu`).

NoMachine is GPU/Vulkan-aware, so this is what to use when you actually need to see rendered output.

## SSH (shell)

```sh
./ssh <name>
```

Or directly with the saved key:

```sh
ssh -i state/<name>/key.pem -o StrictHostKeyChecking=no ubuntu@<ip>
```

Use SSH to read logs, launch a demo's `~/.local/share/isaac-automator-demos/<demo>.sh` script, or record
viewport video headlessly. Set `DISPLAY=:0` when launching GUI apps so they render on the workstation desktop.
