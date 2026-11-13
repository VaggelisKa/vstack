"""Tests for budget.py: the reviewer budget is a hard cap on launches, retries included.

Run from the repo root: python3 -m unittest discover -s skills/review/scripts/tests
"""
import json
import subprocess
import sys
import tempfile
import unittest

from helpers import SCRIPTS


def plan(*ids):
    return json.dumps({"reviewers": [{"id": i, "area": "a", "lens": "bug-hunt"} for i in ids]})


class Budget(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="review-test-")
        self.session = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def run_budget(self, *args, stdin=""):
        return subprocess.run([sys.executable, "-I", str(SCRIPTS / "budget.py"), *args],
                              input=stdin, capture_output=True, text=True)

    def plan(self, n, *ids):
        return self.run_budget("plan", self.session, "--budget", str(n), stdin=plan(*ids))

    def start(self, rid, retry=False):
        return self.run_budget("start", self.session, rid, *(["--retry"] if retry else []))

    def test_plan_over_budget_is_refused(self):
        r = self.plan(2, "R1", "R2", "R3")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("drop 1", r.stdout)

    def test_launches_within_the_plan(self):
        self.assertEqual(self.plan(3, "R1", "R2").returncode, 0)
        r = self.start("R1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("launch 1 of 3", r.stdout)
        self.assertEqual(self.start("R2").returncode, 0)
        status = json.loads(self.run_budget("status", self.session).stdout)
        self.assertEqual(status["launched"], 2)
        self.assertEqual(status["left"], 1)

    def test_reviewer_not_in_plan_is_refused(self):
        self.plan(8, "R1")
        r = self.start("R9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't in the plan", r.stdout)

    def test_second_start_needs_retry(self):
        self.plan(3, "R1")
        self.start("R1")
        self.assertEqual(self.start("R1").returncode, 1)
        self.assertEqual(self.start("R1", retry=True).returncode, 0)
        r = self.start("R1", retry=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("one re-run", r.stdout)

    def test_retries_count_toward_the_cap(self):
        self.plan(2, "R1", "R2")
        self.start("R1")
        self.start("R2")
        r = self.start("R1", retry=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("all 2 reviewer launches are used", r.stdout)

    def test_retry_before_start_is_refused(self):
        self.plan(2, "R1")
        self.assertEqual(self.start("R1", retry=True).returncode, 1)

    def test_no_replan_after_launch(self):
        self.plan(2, "R1")
        self.assertEqual(self.plan(2, "R1", "R2").returncode, 0)
        self.start("R1")
        r = self.plan(4, "R1", "R2", "R3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already started", r.stdout)

    def test_start_without_plan_is_refused(self):
        self.assertEqual(self.start("R1").returncode, 1)

    def test_bad_input(self):
        self.assertEqual(self.run_budget("plan", self.session, "--budget", "3", stdin="nope").returncode, 2)
        self.assertEqual(self.plan(3, "R1", "R1").returncode, 2)
        self.assertEqual(self.plan(3, "-x").returncode, 2)
        self.assertEqual(self.plan(0, "R1").returncode, 2)
        self.assertEqual(self.run_budget("plan", self.session + "/missing", "--budget", "3",
                                         stdin=plan("R1")).returncode, 2)
        self.assertEqual(self.run_budget("bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
