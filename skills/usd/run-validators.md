<!-- Vendored from NVIDIA-Omniverse/usd-optimize @ main
     Path:    .agents/skills/run-validators/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/NVIDIA-Omniverse/usd-optimize/blob/main/.agents/skills/run-validators/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: run-validators
description: Validate a USD asset with Usd Optimize's performance validators and auto-apply fixes with --fix. Use when checking or repairing a USD.
version: "4.0.0"
allowed-tools: Bash
metadata:
  author: NVIDIA Corporation
  tags: [usd, validation, performance]
---

<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# run-validators — Validate and auto-fix a USD asset

Runs the `usd-validation-nvidia` engine over a USD stage through
`tools/validators/run.sh` (POSIX) or `run.bat` (Windows). The wrapper
registers the `UsdOptimize*` performance rules and then delegates to the
official `usd_validation_nvidia` CLI entry point, so one run covers both
the Usd Optimize rules and the base `usd_validation_nvidia` rules.

The validators are reference-documented in
[`docs/performance-validators.rst`](../../../docs/performance-validators.rst) —
that is the source of truth for the rule list and what each rule checks. Many
rules ship a fix that `usd-validation-nvidia`'s `IssueFixer` can apply
automatically; pass `--fix` to enable it.

## What this skill covers

- **Usage** — flags and positional args.
- **Step 1** — validate the input path.
- **Step 2** — invoke the driver.
- **Step 3** — summarize results and hand off.
- **Errors to handle** — failure modes.
- **Auto-fix model** — how `IssueFixer` / `--fix` applies fixes and what it cannot.
- **Programmatic invocation** — `register_all()` + `ValidationEngine`.
- **CLI invocation** — `nvidia_usd_validate` setup, the wheel/entry-point requirement, and the `libusd` alignment gotcha.
- **Adding a new validator** — pointer to the `new-validator` skill.

Companion skills: `interpret-validators` (read the report and decide what to do
about issues that could **not** be auto-fixed), `run-operations` (run
operations / preset configs when a manual fix is needed).

---

## Usage

One positional argument (the asset) plus optional flags:

| Flag | Meaning |
|---|---|
| `ASSET` | Required. `.usd` / `.usda` / `.usdc` / `.usdz`. |
| `-f` / `--fix` | **Opt-in.** Run `IssueFixer` on every fixable issue, applied **in place** to the input. To keep the source, copy it first and pass the copy (see below). |
| `--csv-output <CSV>` | Write a per-issue CSV (all rules). |
| `--json-output <JSON>` | Write the full validation result as JSON. |
| `-r RULE` | Enable only a specific rule (repeatable). |
| `-D RULE` | Disable a specific rule (repeatable). |
| `-c CATEGORY` | Enable only a specific category (repeatable). |
| `--parameter NAME=VALUE` | Override a rule parameter (repeatable). |
| `--verbose` | Also emit one issue per failing prim for count-only rules (see below). |
| `-p PREDICATE` | Filter output by severity: `IsError`, `IsFailure`, `IsWarning`. |
| `--group-by requirement\|rule_name` | Group output by requirement or rule. |

Run `tools/validators/run.sh --help` for the full flag list (`--verbose` is
added by the wrapper, so it won't appear in the upstream `--help`).

### Verbose per-prim reporting

By default several rules report a single aggregate issue with a count (e.g.
"Found 2 nonManifold meshes to fix") anchored at the stage root, so the per-prim
paths never reach the CSV `Location` column. Verbose mode makes those rules also
emit one issue per failing prim (full path in `Location`), which is what you want
for CLI/batch debugging. The aggregate summary is still emitted.

Three equivalent ways to enable it, all read at validation time so they work no
matter how the rules are driven (our wrapper, the upstream CLI, or a direct
`ValidationEngine`):

- `tools/validators/run.{sh,bat} asset.usd --verbose`
- `USD_OPTIMIZE_VALIDATOR_VERBOSE=1` in the environment
- `--parameter VERBOSE=true` (or `--parameter <RuleName>.VERBOSE=true`),
  advertised through the standard usd-validation-nvidia parameter system; or
  programmatically `usd_optimize.validators.set_verbose(True)`.

Rules that already emit per-prim issues (e.g. `SmallMeshChecker`,
`InvisiblePrimsChecker`, `WindingsChecker`) are unaffected by the flag.

If no path is provided, ask:
> "Which USD file should I validate? Provide the full path."

## Step 1 — Validate the input

Reject a path that does not exist or is not a `.usd*` file before doing
anything else.

## Step 2 — Invoke the driver

Use the wrapper for the host OS. It sets `LD_LIBRARY_PATH` / `PATH` and
`PYTHONPATH` for the build's bundled Python, registers the Usd Optimize
rules, and delegates to the `usd_validation_nvidia` CLI. **Always go through
the wrapper** — invoking the bundled interpreter directly inherits the user's
`PYTHONPATH` and fails with USD-version mismatches.

**Validation is read-only by default — add `--fix` only when the user asks for
repairs.** `--fix` modifies the asset you pass **in place**. Pass `--csv-output`
to save a per-issue CSV for later analysis with `interpret-validators`.

POSIX (validate read-only, then opt into `--fix` to repair in place):

```bash
ASSET="<path/to/asset.usd>"
CSV="<artifact_dir>/issues.csv"
mkdir -p "<artifact_dir>"

# validate (read-only)
tools/validators/run.sh "$ASSET" --csv-output "$CSV" > "<artifact_dir>/run.log" 2>&1

# to repair: --fix modifies $ASSET in place
tools/validators/run.sh "$ASSET" --fix --csv-output "$CSV" >> "<artifact_dir>/run.log" 2>&1
```

Windows (PowerShell):

```powershell
$Asset = "<path\to\asset.usd>"
$Csv   = "<artifact_dir>\issues.csv"
New-Item -ItemType Directory -Force -Path "<artifact_dir>" | Out-Null

# validate (read-only)
& tools\validators\run.bat $Asset --csv-output $Csv *> "<artifact_dir>\run.log"

# to repair: --fix modifies $Asset in place
& tools\validators\run.bat $Asset --fix --csv-output $Csv *>> "<artifact_dir>\run.log"
```

Redirect to a log file rather than piping through `tail` (pipes buffer until
the process exits).

**Keeping the original (optional):** `--fix` overwrites the file you pass. If the
user wants to preserve the source, `--fix` a copy instead — e.g.
`cp "$ASSET" "$FIXED"` then `tools/validators/run.sh "$FIXED" --fix`. If it isn't
clear whether the original matters, ask before fixing in place.

### Long-running execution

Validation on real assets takes minutes (occlusion and overlap checks are the
slowest). Launch the driver as a long-running command and return control:

- **Claude Code**: set `run_in_background: true` on the Bash call.
- **Generic shells**: `nohup ... &` (POSIX) or `Start-Process ... -NoNewWindow`
  (PowerShell), then read bounded snapshots of `run.log`.

Tell the user it is running and that you will report results when done.
For a status snapshot: `tail -n 80 "<artifact_dir>/run.log"`.

## Step 3 — Summarize and hand off

When the run finishes, show the last ~40 lines of `run.log` (per-rule issue
counts and the fix summary) and append:

```
Validation complete.
  Artifacts: <artifact_dir>/
    issues.csv  — every issue, one row per rule hit
    run.log     — full driver output

<N> issues found. <M> were auto-fixed (--fix).
```

**Most issues are fixed automatically.** What remains needs a decision
(which prims, how aggressive, an accepted trade-off). For those, hand off:

> For the remaining issues, run `/interpret-validators <asset>` — they
> typically need a decision before an operation or preset config can address
> them.

If `--fix` was used, re-validate the file that was fixed — `$ASSET` if you fixed
in place, or the copy (`$FIXED`) if you fixed a copy — to confirm the targeted
rules dropped:

```bash
tools/validators/run.sh "<the file you fixed>" --csv-output "$CSV"
```

Don't interpret the remaining issues here — that's `interpret-validators`.

## Errors to handle

| Symptom | Cause | What to tell the user |
|---|---|---|
| `Build not found at _build/...` | The repo isn't built | Point at the `build` skill; build first. |
| Driver exits non-zero | USD open error or plugin import error | Surface the last lines of `run.log`; don't parse a partial CSV. |
| `usd-validation-nvidia` install fails in the wrapper | First-run pip install behind a proxy | Set `HTTPS_PROXY` / `HTTP_PROXY` and re-run. |
| 0 Usd Optimize issues but base-rule issues present | The asset has no `UsdGeomMesh` content (references-only / materials-library / layout stage), so mesh rules find nothing | Expected — not a registration failure. Confirm with `inspect-asset` if unsure. |

If the repo isn't built, **don't build it yourself** — point at the `build`
skill and stop.

---

## Auto-fix model

Each rule reports issues; many also attach a **suggestion** describing the
operation that would fix the issue. `usd-validation-nvidia`'s `IssueFixer`
applies those suggestions when the user opts into `--fix`. When opted in, most
issues are resolved without any manual step.

What `IssueFixer` **cannot** resolve automatically is any issue whose fix needs
a decision — which prims to act on, how aggressive to be, or a trade-off the
user must accept (e.g. decimation amount, whether to merge instanced meshes).
Those remain after `--fix` and are the input to `interpret-validators`.

## Programmatic invocation

```python
from usd_optimize.validators import register_all
from usd_validation_nvidia import ValidationEngine, IssueFixer
from pxr import Usd

register_all()                         # register every Usd Optimize rule

stage = Usd.Stage.Open("asset.usd")
results = ValidationEngine().validate(stage)

# example of printing the issues
for issue in results.issues():
    print(issue.severity, issue.rule.__name__, issue.message)

# Apply the auto-fixable suggestions
fix_results = IssueFixer(stage).fix(results.issues())
stage.Export("asset.fixed.usdc")
```

`register_all()` is idempotent.

## CLI invocation (`nvidia_usd_validate`)

The upstream `nvidia_usd_validate` CLI can also drive the rules, but it
discovers plugins via `importlib.metadata` entry points, so **it only sees Usd
Optimize once the `usd-optimize` wheel is pip-installed** (a source-tree
`PYTHONPATH` registers no entry-point metadata). There is no allow-list env
var — once the wheel is installed the `registrant` entry point auto-loads.

```bash
./repo.sh build && ./repo.sh py_package        # produces _build/packages/usd_optimize-*.whl
python3.12 -m pip install _build/packages/usd_optimize-*.whl
nvidia_usd_validate asset.usd
nvidia_usd_validate --help | grep UsdOptimize   # verify rules are discovered
```

**`libusd` alignment gotcha.** `pxr` (often from `usd-exchange`) and Usd
Optimize's C++ core must bind the *same* `libusd` image, or `UsdUtilsStageCache`
splits and the validator errors with missing stage IDs. The `run.sh` wrapper
aligns `PYTHONPATH` / `LD_LIBRARY_PATH` automatically; the raw CLI does not. If
you hit stage-id / cache errors on a dev-tree CLI run, export the build's USD
first:

```bash
export PYTHONPATH=_build/$platform/release/python:_build/target-deps/usd/release/lib/python:$PYTHONPATH
export LD_LIBRARY_PATH=_build/$platform/release/lib:_build/$platform/release/extraLibs:$LD_LIBRARY_PATH
```

Note: the entry-point plugin registers the default rule set; to run a rule that
is not in the default set, register it programmatically with `register_all()`
first.

## Adding a new validator

See [`new-validator`](../new-validator/SKILL.md) for the full recipe.

## Prerequisites

- A built repo (`./repo.sh build` / `repo.bat build`) so the wrappers and
  bundled Python exist.
- A USD asset (`.usd` / `.usda` / `.usdc` / `.usdz`).
- Network access on first run (the wrapper pip-installs `usd-validation-nvidia`
  into the bundled Python).

## Limitations

- Runs and auto-fixes in-place; it does not decide how to resolve issues that
  have no automatic fix — that's `interpret-validators`.
- Occlusion / overlap rules can take tens of minutes to potential hours on
  large stages.
- The raw `nvidia_usd_validate` CLI needs the wheel installed and may need the
  `libusd` alignment exports above.
