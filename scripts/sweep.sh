#!/usr/bin/env bash
# Run every setup at every agent count, REPEATS times, then write the summary table.
# Usage: scripts/sweep.sh [repeats] [agent counts...]
#   scripts/sweep.sh 3 1 10 25 50 100
set -euo pipefail
cd "$(dirname "$0")/.."

REPEATS="${1:-3}"
shift || true
if [ "$#" -gt 0 ]; then AGENTS=("$@"); else AGENTS=(1 10 25 50 100); fi
OUT="results/raw.csv"
SETUPS=("--strategy direct" "--strategy queue --batch 1" "--strategy queue --batch 8")

mkdir -p results
for repeat in $(seq 0 $((REPEATS - 1))); do
  for n in "${AGENTS[@]}"; do
    for setup in "${SETUPS[@]}"; do
      # shellcheck disable=SC2086  # $setup is meant to split into flags
      python3 -m gitbench $setup --agents "$n" --repeat "$repeat" --seed "$repeat" --out "$OUT"
    done
  done
done
python3 -m gitbench.summarize "$OUT" > results/summary.md
echo "wrote results/summary.md"
