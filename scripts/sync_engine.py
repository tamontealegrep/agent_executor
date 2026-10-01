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
