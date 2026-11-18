"""The two ways a change reaches main.

direct  every agent pushes to main itself. A rejected push means someone landed
        first: fetch, rebase, push again. A rebase conflict means the agent throws
        its work away and redoes the task on the new main. CI runs AFTER landing
        (PostMergeCI); the first red commit is reverted by a bot.

queue   agents push to their own ref and wait. One MergeQueue worker is the only
        writer of main: it applies each change on top of main, runs CI BEFORE
        landing, and lands only green changes. With batch > 1 it tests several
        changes with one CI run and falls back to one-by-one when the batch is red.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .gitops import GitError, fresh_base, git, head, rebase_in_progress
from .seed import ci_passes
from .tasks import Task, apply_task


class CI:
    """Runs ci.py in a checkout. `seconds` adds a fixed delay to stand in for a real build."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.runs = 0
        self.red = 0
        self._lock = threading.Lock()

    def check(self, repo: Path) -> bool:
        if self.seconds:
            time.sleep(self.seconds)
        ok = ci_passes(repo)
        with self._lock:
            self.runs += 1
            self.red += not ok
        return ok


def new_record() -> dict:
    return {"landed": False, "attempts": 0, "rejections": 0, "conflicts": 0, "red_rejects": 0, "errors": 0, "seconds": 0.0}


# ---------- direct push ----------

def land_direct(repo: Path, task: Task, landed_at: dict, max_attempts: int) -> dict:
    rec = new_record()
    t0 = time.monotonic()
    redo = True
    while rec["attempts"] + rec["errors"] < max_attempts:
        try:
            if redo:
                fresh_base(repo)
                apply_task(repo, task)
                redo = False
            rec["attempts"] += 1
            if git(repo, "push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
                landed_at[head(repo)] = time.monotonic()
                rec.update(landed=True, seconds=time.monotonic() - t0)
                return rec
            rec["rejections"] += 1
            if git(repo, "pull", "-q", "--rebase", "origin", "main", check=False).returncode != 0:
                if rebase_in_progress(repo):
                    rec["conflicts"] += 1
                    git(repo, "rebase", "--abort", check=False)
                redo = True
        except GitError:
            rec["errors"] += 1
            redo = True
    rec["seconds"] = time.monotonic() - t0
    return rec


class PostMergeCI(threading.Thread):
    """Tests every commit that lands on main, in order, and reverts the first red one."""

    def __init__(self, repo: Path, ci: CI, landed_at: dict, stop: threading.Event, max_attempts: int):
        super().__init__(daemon=True)
        self.repo, self.ci, self.landed_at, self.stop, self.max_attempts = repo, ci, landed_at, stop, max_attempts
        self.lags: list[float] = []
        self.breaks = 0
        self.reverts = 0

    def run(self) -> None:
        last = head(self.repo)
        red = False
        while True:
            finished = self.stop.is_set()
            git(self.repo, "fetch", "-q", "origin", "main")
            shas = git(self.repo, "rev-list", "--reverse", "--first-parent", f"{last}..origin/main").stdout.split()
            for sha in shas:
                git(self.repo, "checkout", "-q", "--detach", sha)
                ok = self.ci.check(self.repo)
                self.lags.append(time.monotonic() - self.landed_at.get(sha, time.monotonic()))
                if not ok and not red:
                    self.breaks += 1
                    self.reverts += self.revert(sha)
                red = not ok
                last = sha
            if finished and not shas:
                return
            if not shas:
                time.sleep(0.05)

    def revert(self, sha: str) -> bool:
        for _ in range(self.max_attempts):
            try:
                fresh_base(self.repo)
                if git(self.repo, "revert", "--no-edit", sha, check=False).returncode != 0:
                    git(self.repo, "revert", "--abort", check=False)
                    return False
                if git(self.repo, "push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
                    self.landed_at[head(self.repo)] = time.monotonic()
                    return True
            except GitError:
                pass
        return False


# ---------- merge queue ----------

@dataclass
class Request:
    sha: str
    ref: str
    result: str = ""
    done: threading.Event = field(default_factory=threading.Event)


def land_queue(repo: Path, task: Task, mq: "MergeQueue", max_attempts: int) -> dict:
    rec = new_record()
    t0 = time.monotonic()
    while rec["attempts"] + rec["errors"] < max_attempts:
        try:
            fresh_base(repo)
            apply_task(repo, task)
            ref = f"refs/heads/q/{task.tag}-{rec['attempts']}"
            git(repo, "push", "-q", "origin", f"HEAD:{ref}")
        except GitError:
            rec["errors"] += 1
            continue
        rec["attempts"] += 1
        req = Request(head(repo), ref)
        mq.requests.put(req)
        req.done.wait()
        if req.result == "landed":
            rec.update(landed=True, seconds=time.monotonic() - t0)
            return rec
        key = {"conflict": "conflicts", "red": "red_rejects"}.get(req.result, "errors")
        rec[key] += 1
    rec["seconds"] = time.monotonic() - t0
    return rec


class MergeQueue(threading.Thread):
    def __init__(self, repo: Path, ci: CI, batch: int, landed_at: dict, stop: threading.Event):
        super().__init__(daemon=True)
        self.repo, self.ci, self.batch, self.landed_at, self.stop = repo, ci, batch, landed_at, stop
        self.requests: queue.Queue[Request] = queue.Queue()
        self.waits: list[float] = []

    def run(self) -> None:
        while not (self.stop.is_set() and self.requests.empty()):
            try:
                items = [self.requests.get(timeout=0.05)]
            except queue.Empty:
                continue
            while len(items) < self.batch:
                try:
                    items.append(self.requests.get_nowait())
                except queue.Empty:
                    break
            try:
                self.process(items)
            except GitError:
                for it in items:
                    self.reply(it, "error")
            finally:
                git(self.repo, "push", "-q", "origin", "--delete", *[it.ref for it in items], check=False)

    def process(self, items: list[Request]) -> None:
        git(self.repo, "fetch", "-q", "origin", "main", *[it.ref for it in items])
        self.reset()
        picked = [it for it in items if self.pick(it)]
        if not picked:
            return
        if self.ci.check(self.repo):
            self.land(picked)
            return
        if len(picked) == 1:
            self.reply(picked[0], "red")
            return
        # The batch is red: find the culprit(s) by testing one change at a time, in order.
        self.reset()
        good = []
        for it in picked:
            if not self.pick(it):
                continue
            if self.ci.check(self.repo):
                good.append(it)
            else:
                git(self.repo, "reset", "-q", "--hard", "HEAD~1")
                self.reply(it, "red")
        if good:
            self.land(good)

    def reset(self) -> None:
        git(self.repo, "reset", "-q", "--hard", "origin/main")
        git(self.repo, "clean", "-qfd")

    def pick(self, it: Request) -> bool:
        if git(self.repo, "cherry-pick", it.sha, check=False).returncode == 0:
            return True
        git(self.repo, "cherry-pick", "--abort", check=False)
        self.reply(it, "conflict")
        return False

    def land(self, items: list[Request]) -> None:
        git(self.repo, "push", "-q", "origin", "HEAD:main")  # the queue is the only writer of main
        git(self.repo, "fetch", "-q", "origin", "main")
        now = time.monotonic()
        for sha in git(self.repo, "rev-list", f"HEAD~{len(items)}..HEAD").stdout.split():
            self.landed_at[sha] = now
        for it in items:
            self.reply(it, "landed")

    def reply(self, it: Request, result: str) -> None:
        if not it.done.is_set():
            it.result = result
            it.done.set()
