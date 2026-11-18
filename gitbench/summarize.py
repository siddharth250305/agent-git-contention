"""python -m gitbench.summarize results/raw.csv > results/summary.md

One row per (strategy, agents): the mean over repeats, with the min-max range,
because 3 repeats are too few for a meaningful confidence interval.
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict

METRICS = [
    ("throughput_per_min", "landed / min", 1),
    ("retries_per_landed", "retries / landed", 2),
    ("conflicts_per_landed", "conflicts / landed", 3),
    ("land_p95_s", "time to land p95 (s)", 1),
    ("ci_runs_per_landed", "CI runs / landed", 2),
    ("red_main_commits", "red commits on main", 1),
    ("cpu_s_per_landed", "CPU s / landed", 2),
]


def label(row: dict) -> str:
    return "direct push" if row["strategy"] == "direct" else f"merge queue, batch {row['batch']}"


def main(path: str) -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in csv.DictReader(open(path)):
        landed = int(row["landed"]) or 1
        row["conflicts_per_landed"] = int(row["conflicts"]) / landed
        row["cpu_s_per_landed"] = float(row["cpu_s"]) / landed
        groups[(label(row), int(row["agents"]))].append(row)

    print("| setup | agents | runs | " + " | ".join(name for _, name, _ in METRICS) + " | landed / tasks |")
    print("|---|---:|---:|" + "---:|" * (len(METRICS) + 1))
    for (setup, agents), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        cells = []
        for key, _, digits in METRICS:
            vals = [float(r[key]) for r in rows]
            mean = statistics.mean(vals)
            cells.append(f"{mean:.{digits}f}" if len(vals) == 1 else f"{mean:.{digits}f} ({min(vals):.{digits}f}-{max(vals):.{digits}f})")
        done = f"{sum(int(r['landed']) for r in rows)}/{sum(int(r['tasks_total']) for r in rows)}"
        print(f"| {setup} | {agents} | {len(rows)} | " + " | ".join(cells) + f" | {done} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/raw.csv")
