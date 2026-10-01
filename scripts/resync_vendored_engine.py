"""Re-syncs src/agent_compiler/ (the vendored copy, see its own git history --
`git log --follow` won't show it, `git blame` will) after `agent_compiler`
changes, without repeating the manual dance done to vendor it the first
time (2026-09-29): a fresh export, a commit in agent_runtime, a filtered
clone, and a `git subtree pull`.

Two stages, matching the existing pipeline (agent_compiler -> agent_runtime
-> agent_executor):

  1. agent_compiler -> agent_runtime, via agent_compiler/scripts/export_runtime.py
     (unchanged -- still the one place that decides what belongs in the
     runtime-only export). If that leaves agent_runtime's working tree
     dirty, this script commits it there.

  2. agent_runtime -> agent_executor/src/agent_compiler, via `git subtree
     pull`. Requires the *same* history shape used when it was first
     vendored -- just the src/agent_compiler/ subtree, at repo root -- so
     this script clones agent_runtime into a throwaway temp dir and
     `git filter-branch --subdirectory-filter`s that clone before pulling
     from it. This is deterministic (same tree+parents+message+dates in,
     same commit hashes out), so `git subtree pull` correctly recognizes
     commits already vendored (via the `git-subtree-split:` trailer on the
     original `git subtree add` merge commit) and only merges what's new.

Safe to run with nothing new to sync (no-ops cleanly). Stashes/restores
any uncommitted changes in agent_executor around the subtree pull, since
that command requires a clean working tree -- same as the original manual
vendoring needed.

Usage:
    python scripts/resync_vendored_agent_compiler.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENT_EXECUTOR_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = AGENT_EXECUTOR_ROOT.parent
AGENT_COMPILER_ROOT = WORKSPACE_ROOT / "agent_compiler"
AGENT_RUNTIME_ROOT = WORKSPACE_ROOT / "agent_runtime"
EXPORT_SCRIPT = AGENT_COMPILER_ROOT / "scripts" / "export_runtime.py"
VENDOR_PREFIX = "src/engine"
TEMP_REMOTE_NAME = "_resync_engine_tmp"


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if check and result.returncode != 0:
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def _step1_export_and_commit() -> None:
    print("\n== Step 1: agent_compiler -> agent_runtime ==")
    if not AGENT_COMPILER_ROOT.exists() or not AGENT_RUNTIME_ROOT.exists():
        raise SystemExit(
            f"Both sibling repos must exist locally for this script "
            f"(this is a dev-time maintenance tool, not something the deployed "
            f"server needs): {AGENT_COMPILER_ROOT}, {AGENT_RUNTIME_ROOT}"
        )
    _run([sys.executable, str(EXPORT_SCRIPT), str(AGENT_RUNTIME_ROOT)], cwd=AGENT_COMPILER_ROOT)

    status = _run(["git", "status", "--porcelain"], cwd=AGENT_RUNTIME_ROOT)
    if not status.stdout.strip():
        print("agent_runtime: nothing changed by the export.")
        return

    _run(["git", "add", "-A"], cwd=AGENT_RUNTIME_ROOT)
    _run(
        ["git", "commit", "-m", "Re-export from agent_compiler (scripts/export_runtime.py)"],
        cwd=AGENT_RUNTIME_ROOT,
    )
    print("agent_runtime: committed the refreshed export.")


def _step2_subtree_pull() -> None:
    print("\n== Step 2: agent_runtime -> agent_executor/src/agent_compiler (git subtree) ==")

    with tempfile.TemporaryDirectory(prefix="agent_runtime_filtered_") as tmp:
        filtered = Path(tmp) / "agent_runtime_filtered"
        _run(["git", "clone", str(AGENT_RUNTIME_ROOT), str(filtered)], cwd=WORKSPACE_ROOT)
        _run(
            ["git", "filter-branch", "--prune-empty", "--subdirectory-filter", "src/agent_compiler", "--", "--all"],
            cwd=filtered,
        )

        stashed = False
        status = _run(["git", "status", "--porcelain"], cwd=AGENT_EXECUTOR_ROOT)
        if status.stdout.strip():
            _run(["git", "stash", "push", "-u", "-m", "resync_vendored_agent_compiler: temp stash"], cwd=AGENT_EXECUTOR_ROOT)
            stashed = True

        try:
            existing_remotes = _run(["git", "remote"], cwd=AGENT_EXECUTOR_ROOT).stdout.split()
            if TEMP_REMOTE_NAME in existing_remotes:
                _run(["git", "remote", "remove", TEMP_REMOTE_NAME], cwd=AGENT_EXECUTOR_ROOT)
            _run(["git", "remote", "add", TEMP_REMOTE_NAME, str(filtered)], cwd=AGENT_EXECUTOR_ROOT)
            _run(["git", "fetch", TEMP_REMOTE_NAME], cwd=AGENT_EXECUTOR_ROOT)

            pull = _run(
                ["git", "subtree", "pull", f"--prefix={VENDOR_PREFIX}", TEMP_REMOTE_NAME, "master",
                 "-m", "Re-sync vendored agent_compiler from agent_runtime"],
                cwd=AGENT_EXECUTOR_ROOT,
                check=False,
            )
            if pull.returncode != 0:
                raise SystemExit(
                    "git subtree pull failed -- resolve manually (likely a real "
                    "merge conflict if agent_compiler/'s source and this vendored "
                    "copy diverged independently, which shouldn't normally happen "
                    "since this copy is never hand-edited)."
                )
            if "up to date" in (pull.stdout + pull.stderr).lower():
                print("Already up to date -- nothing new to vendor.")
            else:
                print("Vendored copy updated.")
        finally:
            _run(["git", "remote", "remove", TEMP_REMOTE_NAME], cwd=AGENT_EXECUTOR_ROOT, check=False)
            if stashed:
                _run(["git", "stash", "pop"], cwd=AGENT_EXECUTOR_ROOT)


def main() -> None:
    _step1_export_and_commit()
    _step2_subtree_pull()
    print("\nDone. Sanity-check with: git log --oneline -5  /  git blame src/agent_compiler/runtime/graph_builder.py")


if __name__ == "__main__":
    main()
