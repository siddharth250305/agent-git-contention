# agent-git-contention

What happens to Git when a lot of coding agents work on the same repository at the
same time? Is it better to let every agent push straight to `main`, or to send every
change through a merge queue?

I wanted actual numbers for this, so I wrote a small benchmark. It runs 1 to 100
simulated agents against one shared repository and compares the two approaches.

## How it works

Each agent is a thread with its own clone of a bare repository on my machine. The
agents are scripted, not LLMs. An agent waits 1-3 seconds (standing in for the time a
real agent spends writing code), makes one small change, and tries to get it onto
`main`. Every push, fetch, rebase and cherry-pick runs the real `git` command.

I compare two setups:

**Direct push.** The agent pushes to `main`. If the push is rejected because someone
else landed first, it pulls with rebase and tries again. If the rebase conflicts, it
throws the change away and makes it again on the new `main`. CI runs after a change
has landed, and a bot reverts the first commit that breaks the build.

**Merge queue (batch 1 or batch 8).** The agent pushes to its own branch and waits.
One worker is the only thing allowed to write to `main`. It cherry-picks each change
onto `main`, runs CI first, and only lands changes that pass. With batch 8 it tests up
to 8 changes in one CI run. If the batch fails, it goes back and tests them one at a
time to find the broken one.

## The test codebase

`gitbench/seed.py` creates a tiny Python package:

- 20 modules of constants, one per line with a blank line between them, so two edits
  only conflict when they touch the same line
- `api.py` with 10 functions, plus `callers/` that use them
- `ci.py`, which imports every module and runs every caller. This is the CI check.

Every agent does 3 tasks. Each task is one of:

- **edit** (80%): change one constant. Modules are picked with a Zipf distribution, so
  a few files are "hot" and get most of the edits, like in a real codebase.
- **rename** (10%): rename an api function and update every caller the agent can see.
- **use** (10%): add a new file that calls an api function.

The interesting case is a rename and a use happening at the same time. Git merges them
without any conflict, but the build is broken, because the new caller still imports
the old name. This is a semantic integration failure, and `tests/test_gitbench.py`
checks that it happens in both orders.

## What I measure

One row per run goes into `results/raw.csv`:

- throughput: changes landed per minute
- retries per landed change (push attempts after the first one)
- conflicts: rebase conflicts for direct push, cherry-pick conflicts for the queue
- time to land, from the first attempt until the change is on `main` (p50 and p95)
- CI runs per landed change
- red commits on `main`: after each run I check out every commit on `main` and run CI
  on it, the same way for both setups
- CPU time used by git and CI, peak memory, and the size of the server repository

Settings that stay fixed: 3 tasks per agent, 1-3 s think time, 0.5 s added to every CI
run as a stand-in for build time, 20% rename/use tasks, and at most 60 attempts per
task before the agent gives up. Task choices come from a seeded random generator, but
thread timing is not deterministic, so I run every setting 3 times.

## Results

Full table with min-max ranges: `results/summary.md`. These are the means of 3 runs
at 100 agents:

| setup | landed / min | retries / landed | tasks landed | red commits on main | CPU s / landed |
|---|---:|---:|---:|---:|---:|
| direct push | 58.3 | 50.3 | 639 / 900 | 152.7 | 8.12 |
| merge queue, batch 1 | 70.0 | 0.40 | 900 / 900 | 0 | 0.45 |
| merge queue, batch 8 | 136.5 | 0.42 | 900 / 900 | 0 | 0.30 |

What I took from it:

- With few agents, direct push is the fastest. At 10 agents it lands 212 changes per
  minute against 158 for the batch-8 queue.
- From about 25 agents it falls apart. Agents keep losing the race to push, so they
  rebase and retry again and again. At 100 agents that's around 50 retries per change,
  and 29% of the tasks are given up.
- The queue never landed a red commit in any of its 30 runs. Direct push left a lot of
  them, because the revert bot only catches the first break and more broken changes
  land on top before it's fixed.
- Batching matters. A queue that tests one change at a time is safe but slow (about 70
  per minute, whatever the number of agents). Batch 8 roughly doubles that.

## A bug I found along the way

My first full sweep showed the merge queue landing broken commits. That shouldn't be
possible, since it runs CI before merging, so something was wrong with the benchmark.

The cause was Python's bytecode cache. Python reuses a cached `.pyc` file if the
source file has the same size and the same modification time (in whole seconds) as
when it was cached. A rename like `g_03_a12_t0` to `g_03_a45_t1` doesn't change the
file size, and the checkouts happen less than a second apart. So CI was sometimes
running the previous version of the code.

CI now runs with `python -B`, so nothing is cached, and
`test_ci_sees_a_same_size_rewrite_in_the_same_second` makes sure this can't come back.
I kept the wrong results in `results/raw_before_pyc_fix.csv` for comparison.

## Running it

You need Python 3.10+ and git. There are no other dependencies.

```bash
python3 -m unittest discover -s tests          # 9 tests, about 15 s
python3 -m gitbench --strategy direct --agents 10
python3 -m gitbench --strategy queue --batch 8 --agents 50
scripts/sweep.sh 3 1 10 25 50 100              # full sweep (about 45 min), writes results/summary.md
```

## Limitations

- The agents are scripted, not LLMs, and every task is small (one line or one small file).
- Everything runs on one machine against a local bare repository, so there's no
  network delay and no real Git server.
- The CI check is tiny. Real build and test time is only modelled by the fixed 0.5 s.
- The revert bot doesn't revert a second break that lands while `main` is already red.
- I've tested up to 100 agents so far.

## Ideas for later

- Stacked changes and speculative merging, where the queue tests the likely future of
  `main` and not only the change at the front
- File- or symbol-level ownership, so agents working on different functions never
  wait for each other
- 200 and 500 agents, agents running in containers, and a hosted Git server
