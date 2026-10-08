"""Keeps agent_runtime/'s exported copy of agent_compiler's engine in sync,
before anything in this process imports `agent_compiler`.

agent_executor's venv has agent_runtime installed editable (`pip install
--no-deps -e ../agent_runtime`) -- a separate on-disk copy of
runtime/graph_builder.py, dsl/condition_grammar.py, etc. that
agent_compiler/scripts/export_runtime.py refreshes, but nothing runs that
script on its own. A source-only fix in agent_compiler's runtime/dsl code
(e.g. a regex) never reaches a running agent_executor process until this
export happens -- found live (2026-09-21): a compiled-and-redeployed
graph.json looked fixed, but the *engine* evaluating it was still running
the pre-fix copy, silently. This has to run before any
`from agent_engine...` import in the calling script, since Python binds
a module once, at import time, to whichever file is on disk then.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_ROOT.parent
_AGENT_COMPILER_ROOT = _PROJECT_ROOT.parent / "agent_compiler"
_AGENT_RUNTIME_ROOT = _PROJECT_ROOT.parent / "agent_runtime"
_EXPORT_SCRIPT = _AGENT_COMPILER_ROOT / "scripts" / "export_runtime.py"
_RESYNC_SCRIPT = _SCRIPTS_ROOT / "resync_vendored_engine.py"
_SYNCED_STAMP = _SCRIPTS_ROOT / ".vendored_engine_synced_from"


def sync_agent_runtime_engine() -> None:
    """Re-run agent_compiler's export_runtime.py against agent_runtime/.

    Silently a no-op if either sibling repo isn't present (e.g. a checkout
    that only has agent_executor) -- this is a convenience for local dev,
    not something that should ever block startup.
    """
    if not _EXPORT_SCRIPT.exists() or not _AGENT_RUNTIME_ROOT.exists():
        return
    subprocess.run(
        [sys.executable, str(_EXPORT_SCRIPT), str(_AGENT_RUNTIME_ROOT)],
        check=False,
        capture_output=True,
    )


def _git_output(args: list[str], cwd: Path) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def sync_vendored_engine() -> None:
    """Bring agent_executor/src/engine up to date with agent_compiler.

    The TUI and the server import the vendored copy, not agent_runtime, so
    refreshing agent_runtime alone changes nothing they run. This runs
    resync_vendored_engine.py -- which commits the export in agent_runtime and
    `git subtree pull`s it here -- but only when the export changed
    something or agent_runtime's HEAD moved since the last successful sync
    (the clone and filter-branch it does are too slow for every launch).

    A failed resync only prints a warning: the vendored copy that is already
    here still works, it is just stale.
    """
    if not (_EXPORT_SCRIPT.exists() and _RESYNC_SCRIPT.exists() and _AGENT_RUNTIME_ROOT.exists()):
        return

    sync_agent_runtime_engine()
    head = _git_output(["rev-parse", "HEAD"], _AGENT_RUNTIME_ROOT)
    dirty = _git_output(["status", "--porcelain"], _AGENT_RUNTIME_ROOT)
    synced = _SYNCED_STAMP.read_text(encoding="utf-8").strip() if _SYNCED_STAMP.exists() else None
    if head and not dirty and head == synced:
        return

    print("Actualizando el motor vendorizado (agent_executor/src/engine)...", flush=True)
    result = subprocess.run([sys.executable, str(_RESYNC_SCRIPT)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        print(f"Advertencia: el resync del motor vendorizado fallo ({' '.join(detail)}); se usa la copia actual.")
        return

    new_head = _git_output(["rev-parse", "HEAD"], _AGENT_RUNTIME_ROOT)
    if new_head:
        _SYNCED_STAMP.write_text(new_head + "\n", encoding="utf-8")
