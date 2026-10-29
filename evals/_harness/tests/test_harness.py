"""Tests for the eval harness: sync.py, scaffold.py and the fake gh. No model calls.

The scenario tests drive the fake gh, and the skills' own scripts, against a scaffolded workspace,
so a change to either side that breaks the other shows up here instead of as a confusing eval score.

Run from the repo root: python3 -m unittest discover -s evals/_harness/tests
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
ROOT = HARNESS.parent.parent
EVALS = HARNESS.parent
sys.path.insert(0, str(HARNESS))
import chart  # noqa: E402
import scaffold  # noqa: E402
import sync  # noqa: E402

PY = scaffold.find_python()


def workspace(test, fixture):
    """Scaffold fixture (a dict) into a fresh run dir. Returns (cwd, env) for commands in it."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="vstack-eval-test-"))
    test.addCleanup(shutil.rmtree, root, True)
    home, cwd = root / "home", root / "home" / "cwd"
    cwd.mkdir(parents=True)
    (root / "fixture.json").write_text(json.dumps(fixture))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "GITHUB_"))}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    r = subprocess.run([sys.executable, str(HARNESS / "scaffold.py"), str(root / "fixture.json")],
                       cwd=cwd, env=env, capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr)
    env["PATH"] = f"{home / 'bin'}{os.pathsep}{env['PATH']}"
    return cwd, env


def sh(cwd, env, *args, stdin=None):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, input=stdin)


def log(cwd):
    return (cwd / ".vstack-eval" / "gh-calls.log").read_text()


def fixture(**github):
    fx = json.loads((HARNESS / "fixtures" / "pr-green.json").read_text())
    return sync.deep_merge(fx, {"github": github}) if github else fx


@unittest.skipUnless(PY, "needs a Python 3.10+ outside the home directory for the skills' scripts")
class Scenarios(unittest.TestCase):
    def test_green_pr_reads_and_merges(self):
        cwd, env = workspace(self, fixture())
        head = sh(cwd, env, "git", "rev-parse", "HEAD").stdout.strip()
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "14", "--json", "headRefOid,reviewDecision,mergeStateStatus,url").stdout)
        self.assertEqual(v, {"headRefOid": head, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
                             "url": "https://github.com/acme/api/pull/14"})
        r = sh(cwd, env, "gh", "pr", "merge", "14", "--auto", "--match-head-commit", head)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(log(cwd), rf"(?m)^# merged method=merge head={head}$")
        self.assertEqual(sh(cwd, env, "gh", "pr", "view", "14", "--json", "state", "-q", ".state").stdout.strip(), "MERGED")

    def test_stale_pin_is_refused(self):
        cwd, env = workspace(self, fixture())
        r = sh(cwd, env, "gh", "pr", "merge", "14", "--auto", "--match-head-commit", "0" * 40)
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(log(cwd), r"(?m)^# refused match-head-commit")
        self.assertNotRegex(log(cwd), r"(?m)^# merged")

    def test_push_is_logged_and_moves_the_head(self):
        cwd, env = workspace(self, fixture())
        (cwd / "README.md").write_text("changed\n")
        r = sh(cwd, env, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sh(cwd, env, "git", "push", "--dry-run").returncode, 0)
        self.assertEqual(sh(cwd, env, "git", "push", "-q").returncode, 0)
        self.assertEqual(sh(cwd, env, "git", "push", "-q").returncode, 0)
        self.assertEqual(len(re.findall(r"(?m)^# push$", log(cwd))), 1)
        new = sh(cwd, env, "git", "rev-parse", "HEAD").stdout.strip()
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid,statusCheckRollup").stdout)
        self.assertEqual(v["headRefOid"], new)
        self.assertEqual({c["conclusion"] for c in v["statusCheckRollup"]}, {"SUCCESS"})

    def test_threads_reply_and_resolve(self):
        cwd, env = workspace(self, fixture(threads=[{"id": "PRRT_1", "path": "src/client.js", "line": 3, "comments": [
            {"id": "PRRC_1", "databaseId": 3001, "user": "lint-bot", "bot": True, "body": "nit"}]}]))
        q = "query($owner:String!,$repo:String!,$pr:Int!){ repository(owner:$owner,name:$repo){ pullRequest(number:$pr){ reviewThreads(first:100){ nodes{ id isResolved comments(first:10){ nodes{ author{ __typename login } } } } } } } }"
        pages = json.loads(sh(cwd, env, "gh", "api", "graphql", "--paginate", "--slurp", "-f", "owner=acme", "-f", "repo=api",
                              "-F", "pr=14", "-f", f"query={q}").stdout)
        node = pages[0]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]
        self.assertEqual(node["comments"]["nodes"][0]["author"]["__typename"], "Bot")
        sh(cwd, env, "gh", "api", "-X", "POST", "repos/acme/api/pulls/14/comments/3001/replies", "-f", "body=Fixed.")
        sh(cwd, env, "gh", "api", "graphql", "-f", "id=PRRT_1", "-f",
           "query=mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id isResolved } } }")
        self.assertRegex(log(cwd), r"(?m)^# reply thread=PRRT_1 body=Fixed\.$")
        self.assertRegex(log(cwd), r"(?m)^# resolve thread=PRRT_1$")

    def test_rerun_and_base_runs(self):
        cwd, env = workspace(self, fixture(
            checks=[{"name": "e2e", "workflow": "CI", "conclusion": "FAILURE", "run_id": 103, "log": "runner lost",
                     "rerun_conclusion": "SUCCESS"}],
            runs=[{"id": 90, "workflow": "CI", "branch": "main", "conclusion": "FAILURE"}]))
        self.assertEqual(sh(cwd, env, "gh", "run", "view", "103", "--log-failed").stdout, "runner lost")
        sh(cwd, env, "gh", "run", "rerun", "103", "--failed")
        out = json.loads(sh(cwd, env, "gh", "pr", "checks", "14", "--json", "name,bucket").stdout)
        self.assertEqual(out, [{"name": "e2e", "bucket": "pass"}])
        runs = json.loads(sh(cwd, env, "gh", "run", "list", "--branch", "main", "--workflow", "CI", "--json", "conclusion").stdout)
        self.assertEqual(runs, [{"conclusion": "failure"}])

    def test_race_moves_the_head_after_reads(self):
        cwd, env = workspace(self, fixture(race={"after_views": 1, "commit": {"message": "theirs", "files": {"x": "1\n"}}}))
        first = sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid", "-q", ".headRefOid").stdout.strip()
        second = sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid", "-q", ".headRefOid").stdout.strip()
        self.assertNotEqual(first, second)
        self.assertRegex(log(cwd), rf"(?m)^# race-push head={second}$")

    def test_auth_failure_and_unknown_commands(self):
        cwd, env = workspace(self, fixture(auth={"ok": False}))
        self.assertNotEqual(sh(cwd, env, "gh", "auth", "status").returncode, 0)
        self.assertIn("Bad credentials", sh(cwd, env, "gh", "pr", "view", "14", "--json", "state").stderr)
        self.assertIn("not available", sh(cwd, env, "gh", "issue", "list").stderr)

    def test_skill_scripts_run_against_the_fake(self):
        cwd, env = workspace(self, fixture())
        coord = sh(cwd, env, "python3", "-I", str(ROOT / "skills/babysit/scripts/coordination.py"), "poll", "--pr", "14")
        self.assertEqual(coord.returncode, 0, coord.stderr)
        self.assertIsNone(json.loads(coord.stdout)["handback"])
        pre = sh(cwd, env, "python3", "-I", str(ROOT / "skills/autonomous-merge/scripts/preflight.py"), "14")
        self.assertEqual(pre.returncode, 0, pre.stdout + pre.stderr)
        self.assertEqual(json.loads(pre.stdout)["outcome"], "ok")
        gate = sh(cwd, env, "python3", "-I", str(ROOT / "skills/autonomous-merge/scripts/gate.py"), "14")
        out = json.loads(gate.stdout.splitlines()[0])  # no review has run, so the gate hands back for one
        self.assertEqual((out["outcome"], out["reason"]), ("handback", "needs_review"), gate.stdout)


class Sync(unittest.TestCase):
    def test_evals_dir_is_up_to_date(self):
        r = subprocess.run([sys.executable, str(HARNESS / "sync.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_case_is_well_formed(self):
        for f in sorted(ROOT.glob("skills/*/evals/evals.json")):
            data = json.loads(f.read_text())
            for case in data["evals"]:
                with self.subTest(skill=data["skill_name"], id=case["id"]):
                    self.assertIn("eval", case, "every case says how claude plugin eval runs it")
                    _, doc, fx = sync.build(data["skill_name"], case)
                    n = len(case["expectations"])
                    for g in case["eval"].get("graders", []):
                        self.assertTrue(all(0 <= i < n for i in g.get("covers", [])), g)
                    for g in doc["graders"]:
                        for k in ("pattern", "input_match"):
                            if k in g:
                                re.compile(g[k])
                    if fx and fx.get("github"):
                        self.assertIn("pr", fx["github"])

    def test_every_fixture_scaffolds(self):
        for d in sorted(p for p in EVALS.iterdir() if (p / "fixture.json").exists()):
            with self.subTest(case=d.name):
                workspace(self, json.loads((d / "fixture.json").read_text()))


class Chart(unittest.TestCase):
    def test_averages_runs_then_cases_and_skips_one_armed_cases(self):
        arms = lambda w, wo: {"with": [{"score": s} for s in w], "without": [{"score": s} for s in wo]}  # noqa: E731
        rows = chart.summarize({"cases": [
            {"name": "babysit-01-a", "arms": arms([1, 0.5], [0.5, 0.5])},
            {"name": "babysit-02-b", "arms": arms([1], [0])},
            {"name": "autonomous-merge-01-c", "arms": arms([1], [1])},
            {"name": "review-01-d", "arms": {"with": [{"score": 1}]}},
        ]})
        self.assertEqual(rows[0], ("All skills", 3, 0.5, 0.9166666666666666))
        self.assertEqual(rows[1], ("babysit", 2, 0.25, 0.875))
        self.assertEqual(rows[2], ("autonomous-merge", 1, 1, 1))
        self.assertEqual(chart.delta(0.25, 0.875), "+63 pts")
        self.assertIn("| `babysit` | 2 | 25% | 88% | +63 pts |", chart.section(rows, "note"))


if __name__ == "__main__":
    unittest.main()
