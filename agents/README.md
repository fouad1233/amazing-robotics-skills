# roboagents

Robotics domain-expert agents, built on [NVIDIA OO Agents (`nooa`)](https://github.com/NVIDIA-NeMo/labs-OO-Agents) and powered by the skill catalogue in this repository.

The catalogue tells an agent *how* to do a robotics task. `roboagents` is the thing that actually does it: a roster of experts — Isaac Sim, Isaac Lab, Newton, USD, ROS 2, LeRobot, VLA, Jetson, perception, inference, toolchain — that plan, execute, verify each other, and commit their work through guarded git.

Runs against a **local model by default**. Point it at Ollama and nothing leaves the machine.

---

## Why this design

An agent is a Python object. Its methods are its capabilities — a method with a real body is a deterministic tool, a method whose body is `...` is implemented by the LLM at call time, and the return type annotation is the output contract. That is `nooa`'s model, and it is the right one for robotics: the parts that must be exact (does `cuInit` succeed, what does `git status` say, which Isaac Sim builds exist) stay in Python, and only judgement goes to the model.

Four decisions follow from that:

**Skills are selected, not dumped.** The catalogue has over a thousand skills. A 32B model served locally has neither the context window nor the attention to be handed all of them. Each expert declares the patterns it draws from, and the router activates the top few per task (`Policy.max_active_skills`, default 4).

**Parallelism is per-instance.** `nooa` holds a lock across an agentic method, so one agent object runs one LLM task at a time. Fanning out means constructing one expert instance per assignment and `asyncio.gather`-ing them — which is exactly what the orchestrator does.

**Git safety is enforced in code.** Commits to `main` are refused; force push, hard reset, branch deletion and `filter-branch` are refused; pushing is off until you turn it on. A guardrail written into a prompt is a suggestion, and a model under pressure will talk itself past it.

**Claims need evidence.** Every expert returns a `WorkResult` carrying the command output that proves it, and a reviewer agent adversarially checks whether the evidence actually supports the claim before the orchestrator marks anything done.

---

## Install

```bash
uv venv --python 3.12 ~/envs/roboagents
VIRTUAL_ENV=~/envs/roboagents uv pip install -e agents/
```

Then a local model:

```bash
ollama serve &
ollama pull qwen3-coder:30b   # 19 GB, fits one 5090
```

Check what it resolved to:

```bash
~/envs/roboagents/bin/roboagents doctor
```

---

## Use

```bash
roboagents agents                       # the roster, with each expert's charter
roboagents skills --search camera       # what the catalogue knows about
roboagents run "the DynaNav benchmark segfaults on startup — find out why"
roboagents run --experts IsaacSimAgent,SimOpsAgent "check the Isaac Sim 5.0 install"
roboagents watch                        # reactive loop: react to file and job events
```

`run` routes the request across the roster, executes the assignments (in parallel where they are independent), has the reviewer check each result, and reports what was proved versus what was merely asserted.

## Configure

`~/.config/roboagents/models.yaml`:

```yaml
planner: qwen3-coder:30b    # routing, review — the most reasoning
worker:  qwen3-coder:30b    # the experts doing the work
fast:    qwen3:8b           # classification and summarisation
```

A bare string is an Ollama tag. For a hosted endpoint use a mapping:

```yaml
planner:
  model: nvidia_nim/moonshotai/kimi-k2-instruct
  api_key_env: NVIDIA_API_KEY
```

Environment overrides everything: `ROBOAGENTS_PLANNER_MODEL`, `ROBOAGENTS_WORKER_MODEL`, `ROBOAGENTS_FAST_MODEL`.

---

## The roster

| Expert | Covers |
|---|---|
| `IsaacSimAgent` | Isaac Sim scenes, headless runs, sensors, the Kit launcher and its settings |
| `IsaacLabAgent` | Isaac Lab environments, RL training configs, the Arena suite |
| `NewtonPhysicsAgent` | Newton, PhysX, solver behaviour, articulation tuning |
| `WarpAgent` | Warp kernels and GPU-native data pipelines |
| `USDAgent` | USD authoring, composition, conversion, optimisation |
| `ROS2Agent` | ROS 2 packages, bringup, bridges, behaviour trees |
| `LeRobotAgent` | LeRobot and leLab — datasets, policies, SO-101 arms |
| `VLAAgent` | Vision-language-action training and evaluation |
| `JetsonAgent` | Jetson bring-up, BSP, deployment |
| `PerceptionAgent` | Cameras, calibration, sensor pipelines |
| `InferenceAgent` | TensorRT and edge deployment of policies |
| `DataAgent` | Datasets, Hugging Face Hub, format conversion |
| `SimOpsAgent` | Drivers, CUDA, venvs, the toolchain — the layer everything else breaks on |
| `RepoAgent` | Branches, commits, repository hygiene |
| `ReviewerAgent` | Adversarial verification of other experts' claims |
| `SkillScoutAgent` | Keeps the catalogue living — finds skills that should be added |

Every expert also carries the shared deterministic skills: `self.shell`, `self.todo`, `self.git`, `self.env`.

---

Apache-2.0. `nooa` is Apache-2.0 and is depended on, not vendored.
