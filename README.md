# amazing-robotics-skills

A living catalogue of **agent skills for robotics** — Isaac Sim, Isaac Lab, Newton, PhysX, Warp, USD, Omniverse and Jetson — collected from NVIDIA's public repositories and kept in sync automatically.

**293 skills across 27 repositories.** Install the ones you need with one command.

> A *skill* is a `SKILL.md` workflow guide that an AI coding agent (Claude Code, Cursor, Codex, …) loads to learn a specific task — how to drive Isaac Sim headless over its Python socket, how to convert URDF to USD, how to flash a Jetson. They turn "the agent guesses" into "the agent follows the documented procedure."

---

## Quick start

```bash
git clone https://github.com/fouad1233/amazing-robotics-skills.git
cd amazing-robotics-skills
python3 scripts/install.py --list
```

Install a category:

```bash
python3 scripts/install.py --category isaac-sim
```

Install specific skills:

```bash
python3 scripts/install.py --name isaac-sim-remote --name urdf-mjcf-to-usd-conversion
```

Everything:

```bash
python3 scripts/install.py --all
```

Skills land in `~/.claude/skills/<name>/SKILL.md` by default. Point somewhere else with `--dest`.

---

## What's in here

### Robotics

| Category | What it covers |
|---|---|
| **`isaac-sim`** | Driving Isaac Sim programmatically — headless deployment, the remote Python socket (port 8226), sensors, cameras, rendering, navigation, ROS 2 bridge, profiling, troubleshooting |
| **`isaac-lab`** | Isaac Lab environments and the Arena benchmark suite |
| **`newton`** | The Newton physics engine — the backend behind Isaac Lab 3.0's multi-backend rewrite |
| **`physx`** | PhysX simulation, solver behaviour, articulation tuning |
| **`warp`** | NVIDIA Warp kernels — the GPU-native data pipeline Isaac Lab 3.0 is built on |
| **`usd`** | USD scene authoring, composition, optimisation and interchange |
| **`omniverse`** | Omniverse Kit runtime and RTX rendering |
| **`jetson`** | Jetson bring-up, BSP, device configuration, DeepStream — the deployment target for most robot fleets |
| **`perception`** | Camera calibration |

### Secondary

| Category | What it covers |
|---|---|
| **`inference`** | TensorRT, TensorRT-LLM, DALI — relevant if you deploy VLA/VLM policies at the edge |
| **`ml-infra`** | Megatron-LM, NeMo, cuDF — large-model training infrastructure |

Full machine-readable index with provenance for every skill: [`sources.json`](sources.json).

---

## Keeping it living

```bash
python3 scripts/sync.py            # rescan NVIDIA's orgs, pull in anything new
python3 scripts/sync.py --dry-run  # show what would change
```

`sync.py` sweeps every org in its `ORGS` list for `SKILL.md` files, resolves each repository's licence, vendors what may be redistributed and catalogues the rest. New NVIDIA repos and new skills appear on the next run without any code change. To widen the sweep, add an org to `ORGS` in `scripts/sync.py`.

Requires the [`gh` CLI](https://cli.github.com/), authenticated.

---

## Licensing — read this before you redistribute

Skills here come from many repositories under **different licences**. This matters if you fork or republish.

**Vendored skills** (copied into `skills/`) are all under licences that permit redistribution — Apache-2.0, BSD-3-Clause, MIT or CC-BY-4.0. Each file carries a header recording its source repository, ref, path and licence. None have been modified beyond that header.

**Link-only skills** are *catalogued but not copied*, because their upstream licence does not permit redistribution. `install.py` fetches these straight from their source repository at install time, so the copy reaches you from NVIDIA under your own licence to use that software.

Currently link-only:

- **`NVIDIA-Omniverse/ovrtx`** — proprietary NVIDIA Software License Agreement. §2.5 forbids distribution *"in any publicly accessible software repositories"*, and §8.1 grants no licence by implication or estoppel.
- **`newton-physics/newton-asv`** — no `LICENSE` file, so all rights reserved by default.
- Several repositories whose licence GitHub cannot classify and which have not yet been reviewed by hand.

> **Attribution does not substitute for a licence.** Crediting an author satisfies the *conditions* of a licence that already grants redistribution — it cannot create permission that was never granted. `sync.py` will only vendor a repository whose SPDX identifier is on an explicit allow-list, and unreviewed `NOASSERTION` repositories are never vendored by accident.

See [`NOTICE`](NOTICE) for full attribution. The packaging, scripts and documentation in this repository are Apache-2.0 ([`LICENSE`](LICENSE)).

---

## Contributing

Found a skill repository worth adding? Open an issue or a PR adding the org to `ORGS` in `scripts/sync.py`. If its licence needs a hand review, add the verified SPDX identifier to `MANUAL_LICENCE_REVIEW` with a note on where you read it.

---

NVIDIA, Isaac Sim, Isaac Lab, Omniverse, Jetson, TensorRT, Warp and Newton are trademarks of NVIDIA Corporation. This project is not affiliated with, sponsored by, or endorsed by NVIDIA Corporation.
