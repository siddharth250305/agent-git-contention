"""Run one benchmark configuration and return one row of metrics."""

from __future__ import annotations

import random
import resource
import shutil
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .gitops import git
from .seed import Shape, ci_passes, clone, make_seed
from .strategies import CI, MergeQueue, PostMergeCI, land_direct, land_queue
from .tasks import TaskMaker


@dataclass
class Config:
    strategy: str = "direct"      # direct | queue
    batch: int = 1                # merge queue batch size
    agents: int = 10
    tasks: int = 3                # tasks per agent
    seed: int = 0
    repeat: int = 0
    ci_seconds: float = 0.5       # stand-in build time added to every CI run
    think_min: float = 1.0        # simulated agent coding time before each task
    think_max: float = 3.0
    semantic_share: float = 0.2   # share of tasks that are rename or use
    zipf_s: float = 1.1
    modules: int = 20
    slots: int = 40
    funcs: int = 10
    max_attempts: int = 60
    workdir: str = ".work"


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def audit(repo: Path) -> dict:
    """After the run: test every commit on main once, the same way for both strategies."""
    git(repo, "fetch", "-q", "origin", "main")
    shas = git(repo, "rev-list", "--reverse", "--first-parent", "origin/main").stdout.split()[1:]  # skip the seed
    red, breaks, prev_red = 0, 0, False
    for sha in shas:
        git(repo, "checkout", "-q", "--detach", sha)
        is_red = not ci_passes(repo)
        red += is_red
        breaks += is_red and not prev_red
        prev_red = is_red
    return {"main_commits": len(shas), "red_main_commits": red, "red_main_share": red / len(shas) if shas else 0.0,
            "breaks_on_main": breaks, "final_main_green": not prev_red}


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def run(cfg: Config) -> dict:
    work = Path(cfg.workdir).resolve() / f"run-{uuid.uuid4().hex[:8]}"  # git runs in other folders, so paths must be absolute
    shape = Shape(cfg.modules, cfg.slots, cfg.funcs)
    try:
        bare = make_seed(work, shape)
        repos = [clone(bare, work / f"agent{i:03d}") for i in range(cfg.agents)]
        worker_repo = clone(bare, work / "worker")
        maker = TaskMaker(cfg.modules, cfg.slots, cfg.funcs, cfg.zipf_s, cfg.semantic_share)
        ci = CI(cfg.ci_seconds)
        landed_at: dict[str, float] = {}
        stop = threading.Event()
        records: list[dict] = []

        if cfg.strategy == "direct":
            worker = PostMergeCI(worker_repo, ci, landed_at, stop, cfg.max_attempts)
            land = lambda repo, task: land_direct(repo, task, landed_at, cfg.max_attempts)  # noqa: E731
        elif cfg.strategy == "queue":
            worker = MergeQueue(worker_repo, ci, cfg.batch, landed_at, stop)
            land = lambda repo, task: land_queue(repo, task, worker, cfg.max_attempts)  # noqa: E731
        else:
            raise ValueError(cfg.strategy)

        def agent(i: int) -> None:
            rng = random.Random(cfg.seed * 100_003 + i)
            for n in range(cfg.tasks):
                time.sleep(rng.uniform(cfg.think_min, cfg.think_max))
                task = maker.make(rng, i, n)
                rec = land(repos[i], task)
                rec.update(agent=i, tag=task.tag, kind=task.kind)
                records.append(rec)

        usage0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.monotonic()
        worker.start()
        threads = [threading.Thread(target=agent, args=(i,)) for i in range(cfg.agents)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        agents_done = time.monotonic() - t0
        stop.set()
        worker.join()
        wall = time.monotonic() - t0
        usage1 = resource.getrusage(resource.RUSAGE_CHILDREN)

        landed = [r for r in records if r["landed"]]
        n_landed = len(landed)
        rss_div = 1e6 if sys.platform == "darwin" else 1e3  # ru_maxrss is bytes on macOS, KiB on Linux
        row = {
            **asdict(cfg),
            "tasks_total": len(records),
            "landed": n_landed,
            "abandoned": len(records) - n_landed,
            "agents_done_s": round(agents_done, 2),
            "wall_s": round(wall, 2),
            "throughput_per_min": round(60 * n_landed / agents_done, 1) if agents_done else 0.0,
            "push_attempts": sum(r["attempts"] for r in records),
            "push_rejections": sum(r["rejections"] for r in records),
            "conflicts": sum(r["conflicts"] for r in records),
            "red_rejects": sum(r["red_rejects"] for r in records),
            "errors": sum(r["errors"] for r in records),
            "retries_per_landed": round((sum(r["attempts"] for r in records) - n_landed) / n_landed, 3) if n_landed else 0.0,
            "land_p50_s": round(pct([r["seconds"] for r in landed], 0.5), 2),
            "land_p95_s": round(pct([r["seconds"] for r in landed], 0.95), 2),
            "ci_runs": ci.runs,
            "ci_red_runs": ci.red,
            "ci_runs_per_landed": round(ci.runs / n_landed, 3) if n_landed else 0.0,
            "ci_lag_p50_s": round(pct(getattr(worker, "lags", []), 0.5), 2),
            "ci_lag_p95_s": round(pct(getattr(worker, "lags", []), 0.95), 2),
            "live_breaks": getattr(worker, "breaks", 0),
            "reverts": getattr(worker, "reverts", 0),
            "cpu_s": round((usage1.ru_utime - usage0.ru_utime) + (usage1.ru_stime - usage0.ru_stime), 2),
            "max_child_rss_mb": round(usage1.ru_maxrss / rss_div, 1),
            "server_repo_kb": round(dir_bytes(bare) / 1024, 1),
        }
        row.update(audit(worker_repo))
        return row
    finally:
        shutil.rmtree(work, ignore_errors=True)
