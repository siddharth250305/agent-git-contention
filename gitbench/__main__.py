"""python -m gitbench --strategy direct --agents 10 --out results/raw.csv"""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields
from pathlib import Path

from .run import Config, run


def main() -> None:
    parser = argparse.ArgumentParser(description="One run of the Git contention benchmark.")
    for f in fields(Config):
        parser.add_argument(f"--{f.name.replace('_', '-')}", type=type(f.default), default=f.default)
    parser.add_argument("--out", default="results/raw.csv")
    args = vars(parser.parse_args())
    out = Path(args.pop("out"))
    row = run(Config(**args))

    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with out.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)
    print(f"{row['strategy']} batch={row['batch']} agents={row['agents']} repeat={row['repeat']}: "
          f"landed {row['landed']}/{row['tasks_total']} in {row['agents_done_s']} s, "
          f"{row['throughput_per_min']}/min, retries/landed {row['retries_per_landed']}, "
          f"conflicts {row['conflicts']}, red main commits {row['red_main_commits']}, CI runs {row['ci_runs']}")


if __name__ == "__main__":
    main()
