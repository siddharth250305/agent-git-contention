"""python3 -m unittest discover -s tests"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from gitbench.gitops import git, rebase_in_progress
from gitbench.run import Config, run
from gitbench.seed import Shape, ci_passes as ci_green, clone, make_seed
from gitbench.tasks import Task, apply_task


class GitBehaviour(unittest.TestCase):
    """The benchmark relies on these Git facts; if one changes, the results mean something else."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.bare = make_seed(self.work, Shape())
        self.a = clone(self.bare, self.work / "a")
        self.b = clone(self.bare, self.work / "b")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def race(self, first: Task, second: Task) -> bool:
        """a lands `first`; b, still on the old main, rebases `second` onto it. True if the rebase is clean."""
        apply_task(self.a, first)
        apply_task(self.b, second)
        git(self.a, "push", "-q", "origin", "HEAD:main")
        self.assertNotEqual(git(self.b, "push", "-q", "origin", "HEAD:main", check=False).returncode, 0)
        clean = git(self.b, "pull", "-q", "--rebase", "origin", "main", check=False).returncode == 0
        self.assertEqual(clean, not rebase_in_progress(self.b))
        return clean

    def test_seed_passes_ci(self):
        self.assertTrue(ci_green(self.a))

    def test_ci_sees_a_same_size_rewrite_in_the_same_second(self):
        # Python trusts a cached .pyc when the source has the same size and mtime (whole seconds).
        # Checkouts less than a second apart produce exactly that, so CI must not use cached bytecode.
        self.assertTrue(ci_green(self.a))
        api = self.a / "app" / "api.py"
        stat = api.stat()
        api.write_text(api.read_text().replace("def g_00():", "def h_00():"))  # same size, breaks base.py
        os.utime(api, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertFalse(ci_green(self.a))

    def test_edits_to_neighbouring_lines_merge_cleanly(self):
        self.assertTrue(self.race(Task("edit", "a1_t0", module=0, slot=3), Task("edit", "a2_t0", module=0, slot=4)))
        self.assertTrue(ci_green(self.b))

    def test_edits_to_the_same_line_conflict(self):
        self.assertFalse(self.race(Task("edit", "a1_t0", module=0, slot=3), Task("edit", "a2_t0", module=0, slot=3)))

    def test_rename_then_new_caller_merges_cleanly_but_breaks_ci(self):
        self.assertTrue(self.race(Task("rename", "a1_t0", func=2), Task("use", "a2_t0", func=2)))
        self.assertFalse(ci_green(self.b))

    def test_new_caller_then_rename_merges_cleanly_but_breaks_ci(self):
        self.assertTrue(self.race(Task("use", "a1_t0", func=2), Task("rename", "a2_t0", func=2)))
        self.assertFalse(ci_green(self.b))


class Runs(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def small(self, **kw) -> dict:
        cfg = dict(agents=4, tasks=3, think_min=0.0, think_max=0.1, ci_seconds=0.0, semantic_share=0.6, workdir=self.work)
        return run(Config(**{**cfg, **kw}))

    def test_direct_lands_every_task(self):
        row = self.small(strategy="direct")
        self.assertEqual(row["landed"], row["tasks_total"])
        self.assertEqual(row["main_commits"], row["landed"] + row["reverts"])

    def test_queue_never_lands_a_red_commit(self):
        for batch in (1, 4):
            row = self.small(strategy="queue", batch=batch, semantic_share=1.0, seed=batch)
            self.assertEqual(row["landed"], row["tasks_total"])
            self.assertEqual(row["red_main_commits"], 0)
            self.assertEqual(row["push_rejections"], 0)

    def test_work_directory_is_removed(self):
        self.small(strategy="direct", agents=2, tasks=1)
        self.assertEqual(list(Path(self.work).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
