"""Build the shared codebase the agents work on, and the bare repository that plays "the server".

Layout of the seed codebase (package `app`):
  app/mNN.py          modules of independent constants, one per line: K_SS = 0
                      (a blank line between slots, so only edits to the SAME line conflict)
  app/api.py          functions g_00 ... g_FF that other code calls
  app/callers/base.py calls every api function
  ci.py               the CI check: import everything, call every caller

A rename of an api function that misses a caller, or a new caller of a name that
was just renamed, merges cleanly in Git but fails ci.py: a semantic integration failure.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .gitops import git

CI_SCRIPT = '''"""Simulated CI: import every module and call every caller. Exit 1 on failure."""
import importlib
import pkgutil
import sys

import app


def main():
    for info in pkgutil.walk_packages(app.__path__, "app."):
        module = importlib.import_module(info.name)
        if info.name.startswith("app.callers.") and hasattr(module, "run"):
            module.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CI FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
'''


@dataclass(frozen=True)
class Shape:
    modules: int = 20
    slots: int = 40
    funcs: int = 10


def write_codebase(root: Path, shape: Shape) -> None:
    pkg = root / "app"
    (pkg / "callers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "callers" / "__init__.py").write_text("")
    for m in range(shape.modules):
        slots = "\n\n".join(f"K_{s:02d} = 0" for s in range(shape.slots))
        (pkg / f"m{m:02d}.py").write_text(slots + "\n")
    api = "\n\n".join(f"def g_{k:02d}():\n    return {k}\n" for k in range(shape.funcs))
    (pkg / "api.py").write_text(api)
    imports = "\n\n".join(f"from app.api import g_{k:02d}" for k in range(shape.funcs))
    calls = "\n\n".join(f"    out.append(g_{k:02d}())" for k in range(shape.funcs))
    (pkg / "callers" / "base.py").write_text(f"{imports}\n\n\ndef run():\n    out = []\n\n{calls}\n\n    return out\n")
    (root / "ci.py").write_text(CI_SCRIPT)


def make_seed(workdir: Path, shape: Shape) -> Path:
    """Create the seed codebase, commit it, and return the path of a bare 'server' repository."""
    src = workdir / "seed"
    src.mkdir(parents=True)
    git(src, "init", "-q", "-b", "main")
    write_codebase(src, shape)
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "seed codebase")
    bare = workdir / "server.git"
    git(workdir, "clone", "-q", "--bare", str(src), str(bare))
    return bare


def ci_passes(repo: Path) -> bool:
    """Run ci.py in a checkout, without bytecode caching (-B).

    Python trusts a cached .pyc when the source has the same size and mtime in whole seconds.
    A rename like g_03_a12_t0 -> g_03_a45_t1 keeps the size, and checkouts happen less than a
    second apart, so with caching CI would sometimes test the previous version of the code.
    """
    return subprocess.run([sys.executable, "-B", "ci.py"], cwd=repo, capture_output=True).returncode == 0


def clone(bare: Path, dest: Path) -> Path:
    git(bare.parent, "clone", "-q", str(bare), str(dest))
    return dest
