"""What one agent task changes in the codebase.

Three kinds of task, mixed by `semantic_share`:
  edit    set one constant in one module; modules are picked with a Zipf skew,
          so a few "hot" files get most of the edits (as in real codebases)
  rename  rename one api function and update every caller the agent can see
  use     add a new caller module that imports one api function

`edit` can only conflict textually. `rename` and `use` can also merge cleanly
and still break the build: that is the semantic integration failure we count.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

from .gitops import git


@dataclass(frozen=True)
class Task:
    kind: str
    tag: str
    module: int = 0
    slot: int = 0
    func: int = 0


class TaskMaker:
    def __init__(self, modules: int, slots: int, funcs: int, zipf_s: float, semantic_share: float):
        self.modules, self.slots, self.funcs = modules, slots, funcs
        self.semantic_share = semantic_share
        self.weights = [1.0 / (i + 1) ** zipf_s for i in range(modules)]

    def make(self, rng: random.Random, agent: int, n: int) -> Task:
        tag = f"a{agent}_t{n}"
        r = rng.random()
        if r < self.semantic_share / 2:
            return Task("rename", tag, func=rng.randrange(self.funcs))
        if r < self.semantic_share:
            return Task("use", tag, func=rng.randrange(self.funcs))
        module = rng.choices(range(self.modules), weights=self.weights)[0]
        return Task("edit", tag, module=module, slot=rng.randrange(self.slots))


def current_name(repo: Path, func: int) -> str:
    """The name api function `func` has in this checkout (it may have been renamed already)."""
    api = (repo / "app" / "api.py").read_text()
    return re.search(rf"^def (g_{func:02d}\w*)\(", api, re.M).group(1)


def apply_task(repo: Path, task: Task) -> None:
    """Make the change on the current checkout and commit it."""
    if task.kind == "edit":
        path = repo / "app" / f"m{task.module:02d}.py"
        value = sum(ord(c) for c in task.tag) * 1000 + len(task.tag)
        text = re.sub(rf"^K_{task.slot:02d} = .*$", f"K_{task.slot:02d} = {value}  # {task.tag}", path.read_text(), flags=re.M)
        path.write_text(text)
    elif task.kind == "rename":
        old = current_name(repo, task.func)
        new = f"g_{task.func:02d}_{task.tag}"
        word = re.compile(rf"\b{re.escape(old)}\b")
        for path in [repo / "app" / "api.py", *sorted((repo / "app" / "callers").glob("*.py"))]:
            text = path.read_text()
            if word.search(text):
                path.write_text(word.sub(new, text))
    elif task.kind == "use":
        name = current_name(repo, task.func)
        (repo / "app" / "callers" / f"c_{task.tag}.py").write_text(f"from app.api import {name}\n\n\ndef run():\n    return {name}()\n")
    else:
        raise ValueError(task.kind)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{task.kind} {task.tag}")
