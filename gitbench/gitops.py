"""Thin wrapper around the git CLI.

Every call runs with the user's global and system git config switched off, so a
personal setting (commit signing, hooks, pull.rebase, ...) cannot change the
benchmark. Author and committer come from environment variables.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "agent",
    "GIT_AUTHOR_EMAIL": "agent@bench.local",
    "GIT_COMMITTER_NAME": "agent",
    "GIT_COMMITTER_EMAIL": "agent@bench.local",
}


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}")
    return proc


def head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD").stdout.strip()


def rebase_in_progress(cwd: Path) -> bool:
    gitdir = cwd / ".git"
    return (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists()


def fresh_base(cwd: Path) -> None:
    """Throw away local work and stand on the latest origin/main."""
    git(cwd, "fetch", "-q", "origin", "main")
    git(cwd, "reset", "-q", "--hard", "origin/main")
    git(cwd, "clean", "-qfd")
