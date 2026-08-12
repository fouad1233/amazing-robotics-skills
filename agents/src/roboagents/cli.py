# SPDX-License-Identifier: Apache-2.0
"""Command line entry point.

Three of these subcommands — ``doctor``, ``agents``, ``skills`` — have to work on
a *broken* machine: no Ollama, no model pulled, a half-synced catalogue, an
expert module that fails to import. They are what you reach for when nothing
else runs. So this module imports nothing heavier than the standard library at
module scope; the orchestrator (which pulls in litellm and wants a live model)
and the three views are imported inside the handler that needs them.

The views are readers of ``events.EventBus``. Nothing here fabricates agent
activity: ``--dry-run`` asks the router and prints what it said, ``replay``
re-emits a recorded transcript, and a view with no events shows an empty world.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import inspect
import json
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

#: Where `pets` looks for the GTK overlay script, relative to this package and
#: to the `agents/` project directory. Reported verbatim when none is found.
_PETS_CANDIDATES: tuple[str, ...] = (
    "pets/pets.py",
    "pets/__main__.py",
    "pets/main.py",
    "pets.py",
)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080


# --------------------------------------------------------------------------
# Terminal output
# --------------------------------------------------------------------------


class _Term:
    """Printing that degrades to plain ``print`` when rich is not installed.

    ``markup=False`` on purpose: skill descriptions and shell output are full of
    square brackets, and rich would eat them as style tags. Colour is applied
    through ``style=``, never by hand-writing escape codes.
    """

    def __init__(self) -> None:
        self._out: Any = None
        self._err: Any = None
        try:
            from rich.console import Console
        except ImportError:  # pragma: no cover - rich is a soft dependency
            return
        self._out = Console(highlight=False, markup=False, soft_wrap=False)
        self._err = Console(stderr=True, highlight=False, markup=False)

    def print(self, text: str = "", style: str = "") -> None:
        if self._out is not None:
            self._out.print(text, style=style or None)
        else:
            print(text)

    def error(self, text: str) -> None:
        if self._err is not None:
            self._err.print(text, style="bold red")
        else:
            print(text, file=sys.stderr)

    def warn(self, text: str) -> None:
        if self._err is not None:
            self._err.print(text, style="yellow")
        else:
            print(text, file=sys.stderr)

    @property
    def width(self) -> int:
        if self._out is not None:
            return max(40, self._out.width)
        return max(40, shutil.get_terminal_size((100, 24)).columns)

    def wrapped(self, text: str, indent: int = 0) -> None:
        """Word-wrap to the terminal, keeping the hanging indent on every line."""
        pad = " " * indent
        self.print(
            textwrap.fill(
                text,
                width=max(20, self.width - indent),
                initial_indent=pad,
                subsequent_indent=pad,
                # Skill ids and shell flags are hyphenated; splitting them
                # across lines makes them uncopyable.
                break_on_hyphens=False,
            )
        )

    def heading(self, text: str) -> None:
        if self._out is not None:
            self._out.rule(text, style="cyan")
        else:
            print(f"\n== {text} " + "=" * max(0, 60 - len(text)))

    def table(self, columns: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
        if self._out is not None:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
            for column in columns:
                table.add_column(column, overflow="fold")
            for row in rows:
                table.add_row(*[str(cell) for cell in row])
            self._out.print(table)
            return

        widths = [len(c) for c in columns]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(columns)))
        for row in rows:
            print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


# --------------------------------------------------------------------------
# Lazy loading of the heavy half of the package
# --------------------------------------------------------------------------


class _NotBuiltYet(RuntimeError):
    """A module this subcommand needs is not importable.

    Raised instead of letting an ImportError traceback out, so the user is told
    which symbol was expected rather than being handed a stack trace.
    """


def _load(module: str, symbol: str) -> Any:
    """Import ``roboagents.<module>.<symbol>`` on demand."""
    import importlib

    try:
        loaded = importlib.import_module(f".{module}", __package__)
    except ImportError as exc:
        raise _NotBuiltYet(
            f"roboagents.{module} could not be imported ({exc}). "
            f"`{_command_for(module)}` needs it."
        ) from exc
    try:
        return getattr(loaded, symbol)
    except AttributeError as exc:
        raise _NotBuiltYet(
            f"roboagents.{module} does not define {symbol!r}; "
            f"`{_command_for(module)}` expects it."
        ) from exc


def _command_for(module: str) -> str:
    return {"orchestrator": "roboagents run", "tui": "roboagents tui", "web": "roboagents web"}.get(
        module, f"roboagents {module}"
    )


def _supported(target: Callable[..., Any], kwargs: dict[str, Any], term: _Term) -> dict[str, Any]:
    """Drop keyword arguments ``target`` does not declare, loudly.

    The CLI flags are the stable surface; the orchestrator and the views are
    free to grow or drop constructor parameters. Silently swallowing a dropped
    ``--repo`` would be worse than either failing or complaining, so anything
    discarded is named on stderr.
    """
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return dict(kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    kept = {k: v for k, v in kwargs.items() if k in params}
    dropped = [k for k, v in kwargs.items() if k not in params and v not in (None, False)]
    if dropped:
        name = getattr(target, "__qualname__", repr(target))
        term.warn(f"warning: {name} does not accept {', '.join(dropped)}; ignored")
    return kept


def _jsonable(obj: Any) -> Any:
    """Best-effort JSON view of whatever an orchestrator handed back."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        with contextlib.suppress(Exception):
            return dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return str(obj)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report the environment. Must never raise, however broken the machine is."""
    term = _Term()
    usable = _doctor_models(term, start=args.start)
    _doctor_catalogue(term)
    _doctor_roster(term)
    _doctor_gpu(term)
    _doctor_gtk(term)

    term.heading("verdict")
    if usable:
        term.print("A model resolves. You can run `roboagents run ...`.", style="green")
        return 0
    term.print("No model resolves — `roboagents run` will fail until one does.", style="bold red")
    return 1


def _doctor_models(term: _Term, *, start: bool) -> bool:
    term.heading("models")
    try:
        from . import llm
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        term.error(f"roboagents.llm failed to import: {type(exc).__name__}: {exc}")
        return False

    if start:
        term.print("starting Ollama ...")
        started = llm.start_ollama()
        term.print(f"start_ollama() -> {started}")
        # resolve() is lru_cached; a server that just came up would otherwise be
        # invisible to the status call below.
        llm.resolve.cache_clear()

    try:
        status = llm.status()
    except Exception as exc:  # noqa: BLE001
        term.error(f"llm.status() failed: {type(exc).__name__}: {exc}")
        return False

    term.print(f"ollama host   {status['ollama_host']}")
    term.print(f"ollama up     {status['ollama_up']}")
    models = [m for m in status.get("ollama_models") or [] if m]
    term.print(f"pulled models {', '.join(models) if models else '(none)'}")
    term.print()

    tiers = status.get("tiers", {})
    rows = [[tier, str(value)] for tier, value in tiers.items()]
    term.table(["tier", "resolves to"], rows)

    resolved = [t for t, v in tiers.items() if not str(v).startswith("unresolved")]
    if not status["ollama_up"]:
        term.print()
        term.print("Ollama is not answering. Start it and pull a model:", style="yellow")
        term.print("    systemctl start ollama        # packaged install")
        term.print("    ollama serve &                # or run it in the foreground")
        term.print("    ollama pull qwen3-coder:30b")
        term.print("    roboagents doctor --start     # or let doctor try the first two")
    elif not models:
        term.print()
        term.print("Ollama is up but has no models pulled:", style="yellow")
        term.print("    ollama pull qwen3-coder:30b")
    return bool(resolved)


def _doctor_catalogue(term: _Term) -> None:
    term.heading("skill catalogue")
    try:
        from .config import catalogue_root
        from .skillbridge import index
    except Exception as exc:  # noqa: BLE001
        term.error(f"catalogue modules failed to import: {type(exc).__name__}: {exc}")
        return

    try:
        root = catalogue_root()
    except FileNotFoundError as exc:
        term.error(str(exc))
        return

    term.print(f"catalogue     {root}")
    try:
        catalogue = index()
    except Exception as exc:  # noqa: BLE001
        term.error(f"indexing failed: {type(exc).__name__}: {exc}")
        return

    term.print(f"skills        {len(catalogue)}")
    term.print()
    term.table(
        ["category", "skills"],
        [[name, str(count)] for name, count in catalogue.categories.items()],
    )

    missing = catalogue.missing_vendored
    if missing:
        term.print()
        term.warn(f"{len(missing)} skill(s) marked vendored in sources.json are missing on disk:")
        for name in missing[:20]:
            term.print(f"    {name}")
        if len(missing) > 20:
            term.print(f"    ... and {len(missing) - 20} more")
        term.print("Re-run the catalogue sync to fetch them:")
        term.print(f"    cd {root} && python3 scripts/sync.py")


def _doctor_roster(term: _Term) -> None:
    term.heading("roster")
    try:
        from . import roster
    except Exception as exc:  # noqa: BLE001
        term.error(f"roboagents.roster failed to import: {type(exc).__name__}: {exc}")
        return

    term.print(f"experts loaded {len(roster.REGISTRY)}")
    if not roster.IMPORT_ERRORS:
        return
    term.print()
    term.error(f"{len(roster.IMPORT_ERRORS)} expert module(s) failed to import:")
    for name, error in roster.IMPORT_ERRORS.items():
        term.print(f"    {name}: {error}")


def _doctor_gpu(term: _Term) -> None:
    term.heading("gpu / cuda")
    try:
        from .skills import WorkspaceSkill
    except Exception as exc:  # noqa: BLE001
        term.error(f"WorkspaceSkill unavailable: {type(exc).__name__}: {exc}")
        return

    async def probe(env: Any) -> tuple[str, str]:
        return await env.gpu(), await env.cuda_health()

    try:
        gpu, cuda = asyncio.run(probe(WorkspaceSkill()))
    except Exception as exc:  # noqa: BLE001
        term.error(f"GPU probe failed: {type(exc).__name__}: {exc}")
        return

    for line in gpu.strip().splitlines():
        term.print(f"gpu   {line}")
    term.print(f"cuda  {cuda.strip()}")

    if "803" in cuda:
        term.print()
        term.error("CUDA error 803: the driver is only half-loaded.")
        term.print(
            "The NVIDIA kernel module currently in memory and the userspace CUDA\n"
            "libraries on disk are different versions — that happens after a driver\n"
            "package is upgraded while the old module is still running. Nothing you\n"
            "can do from userspace fixes it, and every GPU workload will fail (often\n"
            "as a segfault deep inside an unrelated library). Reboot the machine."
        )


def _doctor_gtk(term: _Term) -> None:
    term.heading("desktop pets (GTK)")
    # Deliberately a subprocess: GTK/PyGObject lives on the system python, and
    # importing gi in *this* interpreter would only ever prove the venv lacks it.
    python3 = _system_python()
    if not python3:
        term.warn("no system python3 found; `roboagents pets` cannot run")
        return
    term.print(f"system python {python3}")
    try:
        proc = subprocess.run(
            [python3, "-c", "import gi; gi.require_version('Gtk', '3.0'); print(gi.__file__)"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        term.warn(f"could not probe GTK: {exc}")
        return

    if proc.returncode == 0:
        term.print(f"gi (GTK 3)    ok — {proc.stdout.strip()}", style="green")
    else:
        term.warn("gi (GTK 3) not importable on the system python:")
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        term.print(f"    {detail[-1][:200] if detail else f'exit {proc.returncode}'}")
        term.print("    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0")


def _system_python() -> str | None:
    """A python3 outside this virtualenv — the one that can import gi."""
    if sys.prefix != sys.base_prefix:
        # `which python3` inside an active venv resolves to the venv's own
        # interpreter, which is exactly the one that cannot import gi.
        return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else None
    return shutil.which("python3")


# --------------------------------------------------------------------------
# agents / skills
# --------------------------------------------------------------------------


def cmd_agents(args: argparse.Namespace) -> int:
    term = _Term()
    from . import roster

    specs = roster.specs()
    if args.json:
        print(json.dumps({"experts": specs, "import_errors": roster.IMPORT_ERRORS}, indent=2))
        return 0

    if not specs:
        term.error("No experts loaded.")
    # A block per expert rather than a table: charters are full sentences and a
    # five-column table folds them into unreadable confetti.
    for spec in specs:
        term.print(spec["name"], style="bold cyan")
        term.print(f"  domain {spec['domain']}    tier {spec['tier']}")
        term.wrapped(spec["charter"], indent=2)
        term.wrapped(f"skills: {', '.join(spec['skills']) or '(none declared)'}", indent=2)
        term.print()
    term.print(f"{len(specs)} expert(s).")

    if roster.IMPORT_ERRORS:
        term.print()
        term.error("failed to import:")
        for name, error in roster.IMPORT_ERRORS.items():
            term.print(f"    {name}: {error}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    term = _Term()
    from .skillbridge import index

    try:
        catalogue = index()
    except FileNotFoundError as exc:
        term.error(str(exc))
        return 1

    if args.search or args.category:
        entries = catalogue.select(
            [args.search] if args.search else [],
            categories=[args.category] if args.category else [],
            limit=args.limit,
        )
    else:
        entries = sorted(catalogue, key=lambda e: (e.category, e.id))[: args.limit]

    if not entries:
        term.print("No matching skills.")
        return 0

    term.table(
        ["id", "category", "summary"],
        [[e.id, e.category, e.summary] for e in entries],
    )
    term.print()
    term.print(f"{len(entries)} shown of {len(catalogue)} in the catalogue.")

    if args.materialize:
        written = catalogue.materialize(entries)
        term.print(f"materialised {len(written)} SKILL.md director(ies):")
        for skill_id, path in list(written.items())[:20]:
            term.print(f"    {skill_id} -> {path}")
        if len(written) > 20:
            term.print(f"    ... and {len(written) - 20} more")
    return 0


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    term = _Term()
    try:
        experts = _pinned_experts(args.experts)
    except KeyError as exc:
        # roster.UnknownExpert subclasses KeyError, whose str() adds quotes.
        term.error(str(exc.args[0]) if exc.args else str(exc))
        return 2
    try:
        return asyncio.run(_run_async(args, experts, term))
    except _NotBuiltYet as exc:
        term.error(str(exc))
        return 2


async def _run_async(args: argparse.Namespace, experts: list[str], term: _Term) -> int:
    if args.dry_run:
        # Deliberately before the bus is touched: constructing an EventBus opens
        # a transcript file, and a dry run that emits nothing should not leave an
        # empty one behind for `latest_run()` to find.
        return await _dry_run(args, experts, term)

    from .events import bus

    bus().bind_loop()
    orchestrator = _make_orchestrator(args, experts, term)

    # Resolve the views before the run is scheduled. A missing tui/web module
    # must fail while there is nothing in flight, not leave an orphaned run.
    views = [asyncio.create_task(start()) for start in _view_starters(args, term)]
    # `experts` is pinned on the constructor, not on run(): the orchestrator
    # skips routing entirely when it has a pinned roster subset.
    run_task = asyncio.create_task(orchestrator.run(args.request))

    if not views:
        return _report(await run_task, orchestrator, args, term)

    done, pending = await asyncio.wait({run_task, *views}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        # The views run forever by design; the run finishing is what ends them.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    for task in done:
        if task is not run_task:
            # A view that died first took the run down with it; surface why.
            with contextlib.suppress(asyncio.CancelledError):
                task.result()
    return _report(await run_task, orchestrator, args, term)


async def _dry_run(args: argparse.Namespace, experts: list[str], term: _Term) -> int:
    """Print the route without executing anything."""
    from .llm import OllamaUnavailable
    from .types import Assignment, RoutePlan

    orchestrator: Any = None
    try:
        orchestrator = _make_orchestrator(args, experts, term)
    except OllamaUnavailable as exc:
        # Routing is an LLM call and cannot be faked. Pinned experts are not:
        # checking what `--experts A,B` means is exactly the thing you want to
        # be able to do on a machine with no model running.
        if not experts:
            term.error(f"routing needs a model. {exc}")
            return 1
        term.warn(f"no model resolved ({exc}); pinned routing needs none, continuing")

    if experts and orchestrator is not None:
        plan: Any = orchestrator.pinned_plan(args.request)
    elif experts:
        # Same shape Orchestrator.pinned_plan produces, built without one.
        plan = RoutePlan(
            rationale=f"experts pinned on the command line: {', '.join(experts)}",
            assignments=[Assignment(expert=name, task=args.request) for name in experts],
            parallel=True,
        )
    else:
        plan = await orchestrator.route(args.request)

    if args.json:
        print(json.dumps(_jsonable(plan), indent=2))
        return 0

    if isinstance(plan, RoutePlan):
        term.print(f"rationale  {plan.rationale}")
        term.print(f"parallel   {plan.parallel}")
        term.print()
        term.table(
            ["#", "expert", "task", "skills", "depends on"],
            [
                [
                    str(i),
                    a.expert,
                    a.task,
                    ", ".join(a.skills),
                    ", ".join(str(d) for d in a.depends_on),
                ]
                for i, a in enumerate(plan.assignments)
            ],
        )
    else:
        term.print(json.dumps(_jsonable(plan), indent=2))
    return 0


def _report(report: Any, orchestrator: Any, args: argparse.Namespace, term: _Term) -> int:
    """Print a RunReport and turn it into an exit code.

    Non-zero unless every assignment came back verified. "The model said it
    worked" is not success here — a reviewer has to have checked the evidence,
    which is the same rule ``report_text`` prints under.
    """
    if args.json:
        print(json.dumps(_jsonable(report), indent=2))
    else:
        term.print(orchestrator.report_text(report))
        # report_text already names the transcript; this is the thing to do
        # with it, which it has no reason to know about.
        transcript = getattr(report, "transcript", "")
        if transcript:
            term.print(f"replay it:  roboagents tui --run {transcript}")

    results = getattr(report, "results", None)
    if not results:
        return 1
    return 0 if not getattr(report, "unverified", 0) else 1


def _pinned_experts(raw: str | None) -> list[str]:
    """Resolve --experts to canonical class names, failing fast on a typo."""
    if not raw:
        return []
    from . import roster

    return [roster.get(name.strip()).__name__ for name in raw.split(",") if name.strip()]


def _make_orchestrator(args: argparse.Namespace, experts: list[str], term: _Term) -> Any:
    """Build the orchestrator with the policy the command line asked for."""
    from .config import Policy

    orchestrator_cls = _load("orchestrator", "Orchestrator")
    policy = dataclasses.replace(
        Policy.default(),
        allow_push=bool(args.allow_push),
        max_parallel_agents=max(1, int(args.parallel)),
    )
    kwargs = _supported(
        orchestrator_cls,
        {
            "policy": policy,
            "workdir": Path(args.workdir).expanduser() if args.workdir else None,
            "repo": Path(args.repo).expanduser() if args.repo else None,
            "experts": experts or None,
        },
        term,
    )
    return orchestrator_cls(**kwargs)


def _view_starters(args: argparse.Namespace, term: _Term) -> list[Callable[[], Any]]:
    """Entry points for the views ``run`` was asked to show alongside the run.

    Every one is imported and its arguments checked here, so ``--tui`` on a
    build without a TUI fails immediately rather than half-way through a run.
    """
    starters: list[Callable[[], Any]] = []

    if args.tui:
        run_tui = _load("tui", "run_tui")
        tui_kwargs = _supported(run_tui, {"path": None, "follow": True}, term)
        starters.append(lambda: run_tui(**tui_kwargs))

    if args.web:
        serve = _load("web", "serve")
        web_kwargs = _supported(
            serve, {"host": _DEFAULT_HOST, "port": _DEFAULT_PORT, "path": None}, term
        )
        term.print(f"web world on http://{_DEFAULT_HOST}:{_DEFAULT_PORT}", style="cyan")
        starters.append(lambda: serve(**web_kwargs))

    return starters


# --------------------------------------------------------------------------
# watch / tui / web / pets / replay
# --------------------------------------------------------------------------


def cmd_watch(args: argparse.Namespace) -> int:
    term = _Term()
    sources = list(args.tail or [])
    if not sources:
        term.error("nothing to watch. Give at least one --tail.")
        term.print("A source is a file to follow if it exists, otherwise a shell command:")
        term.print("    roboagents watch --tail ~/setup-progress.log --tail 'dmesg -w'")
        return 2

    async def go() -> Any:
        from .events import bus

        bus().bind_loop()
        # Sources stay strings: watch() treats one as a file if it exists on
        # disk and as a shell command to monitor otherwise.
        orchestrator = _make_orchestrator(args, [], term)
        return await orchestrator.watch(sources, interval=args.interval)

    return _drive(go(), term)


def cmd_tui(args: argparse.Namespace) -> int:
    term = _Term()
    try:
        run_tui = _load("tui", "run_tui")
    except _NotBuiltYet as exc:
        term.error(str(exc))
        return 2

    async def go() -> Any:
        from .events import bus

        bus().bind_loop()
        kwargs = _supported(
            run_tui,
            {"path": Path(args.run).expanduser() if args.run else None, "follow": args.follow},
            term,
        )
        return await run_tui(**kwargs)

    return _drive(go(), term)


def cmd_web(args: argparse.Namespace) -> int:
    term = _Term()
    try:
        serve = _load("web", "serve")
    except _NotBuiltYet as exc:
        term.error(str(exc))
        return 2

    url = f"http://{args.host}:{args.port}"
    term.print(f"web world on {url}", style="cyan")
    if args.open:
        # webbrowser is stdlib and never raises on a headless box; it just fails.
        import webbrowser

        with contextlib.suppress(Exception):
            webbrowser.open(url)

    async def go() -> Any:
        from .events import bus

        bus().bind_loop()
        kwargs = _supported(
            serve,
            {
                "host": args.host,
                "port": args.port,
                "path": Path(args.run).expanduser() if args.run else None,
            },
            term,
        )
        return await serve(**kwargs)

    return _drive(go(), term)


def cmd_pets(args: argparse.Namespace) -> int:
    """Hand the desktop overlay to the system python — GTK is not in this venv."""
    term = _Term()

    if not _server_reachable(args.url):
        term.error(f"nothing is serving {args.url}")
        term.print("The pets are a view over the event stream, and they read it from the")
        term.print("web server's WebSocket. Start it first, in another terminal:")
        term.print("    roboagents web")
        return 2

    script = _pets_script()
    if script is None:
        term.error("could not find the pets script. Looked for:")
        for candidate in _pets_search_paths():
            term.print(f"    {candidate}")
        return 2

    python3 = _system_python()
    if not python3:
        term.error("no system python3 found outside this virtualenv.")
        term.print("PyGObject/GTK3 is installed for the system interpreter, not for the venv,")
        term.print("so the overlay has to run under /usr/bin/python3.")
        return 2

    argv = [python3, str(script), "--url", args.url]
    if args.scale is not None:
        argv += ["--scale", str(args.scale)]
    term.print(f"exec {' '.join(argv)}")
    try:
        # execv, not Popen: the overlay should own the terminal and the signals,
        # and there is nothing left for this process to do afterwards.
        os.execv(python3, argv)
    except OSError as exc:
        term.error(f"could not exec {python3}: {exc}")
        return 2
    return 0  # pragma: no cover - execv does not return


def _pets_search_paths() -> list[Path]:
    package = Path(__file__).resolve().parent
    project = package.parents[1]  # agents/src/roboagents -> agents/
    return [base / name for base in (package, project) for name in _PETS_CANDIDATES]


def _pets_script() -> Path | None:
    return next((path for path in _pets_search_paths() if path.is_file()), None)


def _server_reachable(url: str, timeout: float = 2.0) -> bool:
    """Is something answering at that URL? ws:// is probed over http://."""
    probe = url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    try:
        with urllib.request.urlopen(probe, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        # A 404 still means a server is listening, which is all we asked.
        return True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def cmd_replay(args: argparse.Namespace) -> int:
    term = _Term()
    path = Path(args.path).expanduser()
    if not path.is_file():
        term.error(f"no such transcript: {path}")
        return 2

    from .events import bus, read_jsonl

    events = read_jsonl(path)
    if not events:
        term.error(f"{path} contains no readable events")
        return 1

    async def go() -> int:
        stream = bus()
        stream.bind_loop()
        speed = max(args.speed, 0.01)
        previous = events[0].ts
        for event in events:
            # Preserve the original pacing, scaled. A long idle gap in the
            # recording is capped so a viewer is not left staring at nothing.
            gap = min(max(event.ts - previous, 0.0) / speed, 2.0)
            previous = event.ts
            if gap:
                await asyncio.sleep(gap)
            stream.emit(
                event.kind,
                event.actor,
                target=event.target,
                text=event.text,
                **event.data,
            )
        term.print(f"replayed {len(events)} event(s) from {path}")
        term.print(f"new transcript: {stream.sink_path}")
        return 0

    return _drive(go(), term)


def _drive(coro: Any, term: _Term) -> int:
    """Run a coroutine, turning Ctrl-C into a clean exit instead of a traceback."""
    try:
        result = asyncio.run(coro)
    except KeyboardInterrupt:
        term.print("interrupted")
        return 130
    except _NotBuiltYet as exc:
        term.error(str(exc))
        return 2
    return result if isinstance(result, int) else 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def _add_policy_flags(parser: argparse.ArgumentParser) -> None:
    """Flags every subcommand that builds an Orchestrator shares."""
    parser.add_argument("--workdir", metavar="P", help="where the agents work (default ~/robotics)")
    parser.add_argument("--repo", metavar="P", help="git repository the agents may commit to")
    parser.add_argument(
        "--allow-push",
        action="store_true",
        help="permit `git push`. Off by default; commits to a protected branch stay refused.",
    )
    parser.add_argument(
        "--parallel", type=int, default=4, metavar="N", help="max concurrent experts"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roboagents",
        description="Robotics domain-expert agents, built on NVIDIA OO Agents.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    doctor = subparsers.add_parser("doctor", help="check models, catalogue, roster, GPU, GTK")
    doctor.add_argument("--start", action="store_true", help="try to start Ollama if it is down")
    doctor.set_defaults(handler=cmd_doctor)

    agents = subparsers.add_parser("agents", help="list the expert roster")
    agents.add_argument("--json", action="store_true")
    agents.set_defaults(handler=cmd_agents)

    skills = subparsers.add_parser("skills", help="search the skill catalogue")
    skills.add_argument("--search", metavar="Q", default="", help="substring to match")
    skills.add_argument("--category", metavar="C", default="", help="restrict to one category")
    skills.add_argument("--limit", type=int, default=20, metavar="N")
    skills.add_argument(
        "--materialize",
        action="store_true",
        help="write the matched skills out as NOOA-loadable SKILL.md directories",
    )
    skills.set_defaults(handler=cmd_skills)

    run = subparsers.add_parser("run", help="route a request across the roster and execute it")
    run.add_argument("request", help="what you want done, in plain language")
    run.add_argument("--experts", metavar="A,B", help="pin these experts and skip routing")
    _add_policy_flags(run)
    run.add_argument("--json", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="print the route and stop")
    run.add_argument("--tui", action="store_true", help="show the terminal view during the run")
    run.add_argument("--web", action="store_true", help="serve the browser world during the run")
    run.set_defaults(handler=cmd_run)

    watch = subparsers.add_parser("watch", help="reactive loop over files and job output")
    watch.add_argument(
        "--tail",
        action="append",
        metavar="PATH",
        help="file to follow, or a shell command to monitor. Repeatable.",
    )
    watch.add_argument(
        "--interval",
        type=float,
        default=300.0,
        metavar="S",
        help="seconds between heartbeats; output is batched, not acted on per line",
    )
    _add_policy_flags(watch)
    watch.set_defaults(handler=cmd_watch)

    tui = subparsers.add_parser("tui", help="terminal view of the event stream")
    tui.add_argument("--run", metavar="PATH", help="replay a recorded JSONL transcript")
    tui.add_argument("--follow", action="store_true", help="keep following as events arrive")
    tui.set_defaults(handler=cmd_tui)

    web = subparsers.add_parser("web", help="browser game world over the event stream")
    web.add_argument("--host", default=_DEFAULT_HOST)
    web.add_argument("--port", type=int, default=_DEFAULT_PORT)
    web.add_argument("--run", metavar="PATH", help="serve a recorded JSONL transcript")
    web.add_argument("--open", action="store_true", help="open a browser at the URL")
    web.set_defaults(handler=cmd_web)

    pets = subparsers.add_parser("pets", help="desktop overlay (runs on the system python)")
    pets.add_argument("--url", default=f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}")
    pets.add_argument("--scale", type=float, default=None, metavar="F", help="sprite scale")
    pets.set_defaults(handler=cmd_pets)

    replay = subparsers.add_parser("replay", help="re-emit a recorded run onto the event bus")
    replay.add_argument("path", metavar="PATH")
    replay.add_argument("--speed", type=float, default=1.0, metavar="F")
    replay.set_defaults(handler=cmd_replay)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except _NotBuiltYet as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
