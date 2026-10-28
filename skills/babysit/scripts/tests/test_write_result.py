"""Tests for write-result.py: a valid result lands in
<git-common-dir>/vstack/<pr>/babysit.json, shared by worktrees and never in the
tree; bad results, bad PR numbers, bad or unknown SHAs and symlinked folders
write nothing.

Run from the repo root: python3 -m unittest discover -s skills/babysit/scripts/tests
"""
import datetime
import json
import os
import re
import subprocess
import sys
import unittest

from helpers import GIT_ENV, SCRIPTS, criss_cross_repo, git, pr_repo, sha256_supported, tmpdir, unrelated_repo

UNKNOWN = "0123456789abcdef0123456789abcdef01234567"
WRITTEN_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
PASSING = {"state": "passing", "failing_checks": []}
NONE = {"state": "none", "failing_checks": []}


def thread(**over):
    t = {"id": "PRRT_1", "author": "alice", "is_bot": False, "state": "open",
         "decision": "escalated", "reply_url": "https://github.com/o/r/pull/42#discussion_r1"}
    t.update(over)
    return t


def check(name="test (ubuntu)", state="failing"):
    return {"name": name, "state": state}


def result(**over):
    r = {"status": "running", "ci": {"state": "failing", "failing_checks": ["test (ubuntu)"]},
         "threads": [thread(), thread(id="PRRT_2", author="copilot", is_bot=True, state="resolved",
                                      decision="fixed")],
         "last_action": "escalated PRRT_1; same check failed on two head SHAs", "mergeable": None,
         "approved_on_head": False, "changes_requested": False,
         "required_checks": [check(), check("lint", "passing")]}
    r.update(over)
    return r


def all_passing():
    return [check(state="passing"), check("lint", "passing")]


def green(**over):
    r = {"status": "green", "ci": PASSING, "threads": [thread(state="resolved", decision="fixed")],
         "mergeable": True, "approved_on_head": True, "changes_requested": False,
         "required_checks": [check(state="passing"), check("lint", "passing")]}
    r.update(over)
    return result(**r)


def write(cwd, data, *args, env=None):
    text = data if isinstance(data, (str, bytes)) else json.dumps(data)
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "write-result.py"), *map(str, args)],
                          input=text.encode() if isinstance(text, str) else text,
                          capture_output=True, cwd=cwd, env=env or GIT_ENV)


class WriteResult(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(self)
        self.gitdir = os.path.realpath(self.repo / ".git")
        self.vstack = os.path.join(self.gitdir, "vstack")
        self.out = os.path.join(self.vstack, "42", "babysit.json")
        self.shas = ["--head-sha", self.head, "--base-sha", self.base]

    def ok(self, data, *args, cwd=None):
        r = write(cwd or self.repo, data, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode().strip(), self.out)
        with open(self.out) as f:
            return json.load(f)

    def refused(self, data, *args, code=2, cwd=None):
        r = write(cwd or self.repo, data, *args)
        self.assertEqual(r.returncode, code, (r.stdout, r.stderr))
        self.assertEqual(r.stdout, b"")
        return r.stderr.decode()

    def test_valid_write(self):
        out = self.ok(result(), "--pr", 42, *self.shas)
        self.assertEqual(list(out), ["schema_version", "skill", "pr", "head_sha", "base_sha", "merge_base",
                                     "written_at", "status", "ci", "threads", "last_action", "mergeable",
                                     "approved_on_head", "changes_requested", "required_checks"])
        self.assertEqual(out, {**result(), "schema_version": 1, "skill": "babysit", "pr": 42,
                               "head_sha": self.head, "base_sha": self.base, "merge_base": self.base,
                               "written_at": out["written_at"]})
        with open(self.out, "rb") as f:
            self.assertTrue(f.read().endswith(b"}\n"))

    def test_every_status(self):
        resolved = [thread(state="resolved", decision="fixed")]
        cases = [
            green(),
            green(ci=NONE, threads=[], required_checks=[]),
            result(),
            result(ci={"state": "pending", "failing_checks": []}, required_checks=[check(state="pending")],
                   threads=[thread(decision="pending", reply_url=None)]),
            result(status="handed_back", threads=[thread(decision="rejected"), thread(id="PRRT_3", decision=None)]),
            result(status="handed_back", ci=PASSING, required_checks=all_passing(), mergeable=False,
                   changes_requested=True),
            result(status="handed_back", ci=PASSING, required_checks=all_passing(), threads=resolved,
                   mergeable=True, approved_on_head=True, last_action="merged at 1a2b3c4"),
            result(status="handed_back", ci=NONE, required_checks=[]),
            result(status="failed"),
            result(status="failed", ci=PASSING, required_checks=all_passing(), threads=resolved),
        ]
        for data in cases:
            with self.subTest(status=data["status"], ci=data["ci"]["state"]):
                self.assertEqual(self.ok(data, "--pr", 42, *self.shas)["status"], data["status"])

    def test_green_gate(self):
        cases = {
            "not approved on head": (green(approved_on_head=False), "approved_on_head"),
            "mergeable unknown": (green(mergeable=None), "mergeable"),
            "not mergeable": (green(mergeable=False), "mergeable"),
            "changes requested": (green(changes_requested=True), "changes_requested"),
            "required check pending": (green(required_checks=[check(state="pending")]), "required check"),
            "required check failing": (green(required_checks=[check("lint", "passing"), check()]),
                                       "required check"),
            "open thread": (green(threads=[thread(decision="fixed")]), "open thread"),
            "no required checks but ci passing": (green(required_checks=[]), "ci.state"),
            "failing ci": (green(ci={"state": "failing", "failing_checks": ["t"]}), "ci.state"),
            "pending ci": (green(ci={"state": "pending", "failing_checks": []}), "ci.state"),
        }
        for name, (data, msg) in cases.items():
            with self.subTest(name):
                self.assertIn(msg, self.refused(data, "--pr", 42, *self.shas))
                self.assertFalse(os.path.exists(self.vstack))

    def test_failed_refused_when_everything_is_green(self):
        for name, data in {"passing": green(status="failed"),
                           "no checks": green(status="failed", ci=NONE, threads=[], required_checks=[])}.items():
            with self.subTest(name):
                self.assertIn("status: failed", self.refused(data, "--pr", 42, *self.shas))
                self.assertFalse(os.path.exists(self.vstack))
        for name, over in {"not approved": {"approved_on_head": False}, "unmergeable": {"mergeable": None},
                           "changes requested": {"changes_requested": True},
                           "open thread": {"threads": [thread()]}}.items():
            with self.subTest(name):
                self.assertEqual(self.ok(green(status="failed", **over), "--pr", 42, *self.shas)["status"], "failed")

    def test_ci_state_follows_required_checks(self):
        cases = [
            ([check(), check("lint", "pending"), check("b", "passing")], "failing"),
            ([check("lint", "pending"), check("b", "passing")], "pending"),
            (all_passing(), "passing"),
            ([], "none"),
        ]
        states = {"failing": {"state": "failing", "failing_checks": ["test (ubuntu)"]},
                  "pending": {"state": "pending", "failing_checks": []}, "passing": PASSING, "none": NONE}
        for checks, want in cases:
            for state, ci in states.items():
                with self.subTest(checks=checks, ci=state):
                    data = result(ci=ci, required_checks=checks)
                    if state == want:
                        self.assertEqual(self.ok(data, "--pr", 42, *self.shas)["ci"], ci)
                    else:
                        self.assertIn(f"ci.state: must be {want}", self.refused(data, "--pr", 42, *self.shas))

    def test_validation_failures(self):
        no_status = {k: v for k, v in result().items() if k != "status"}
        cases = {
            "not json": "{nope",
            "array": "[]",
            "infinity": json.dumps(result(mergeable=1)).replace('"mergeable": 1', '"mergeable": Infinity'),
            "duplicate key": '{"mergeable": null, ' + json.dumps(result())[1:],
            "over 1 MB": json.dumps(result(last_action="x" * 1024 * 1024)),
            "missing status": no_status,
            "missing approved_on_head": {k: v for k, v in result().items() if k != "approved_on_head"},
            "unknown field": result(extra=1),
            "unknown ci field": result(ci={"state": "failing", "failing_checks": ["t"], "url": "u"}),
            "unknown thread field": result(threads=[thread(body="b")]),
            "unknown required check field": result(required_checks=[{**check(), "url": "u"}]),
            "bad status": result(status="done"),
            "old status merged": result(status="merged"),
            "old status pending": result(status="pending"),
            "old status failing": result(status="failing"),
            "old status blocked": result(status="blocked"),
            "old status stopped": result(status="stopped"),
            "ci not an object": result(ci="failing"),
            "bad ci state": result(ci={"state": "red", "failing_checks": ["t"]}),
            "failing without checks": result(ci={"state": "failing", "failing_checks": []}),
            "checks without failing": result(ci={"state": "pending", "failing_checks": ["t"]}),
            "failing_checks not strings": result(ci={"state": "failing", "failing_checks": [1]}),
            "threads not a list": result(threads={}),
            "duplicate thread id": result(threads=[thread(), thread(state="resolved", decision="fixed")]),
            "resolved and escalated": result(threads=[thread(state="resolved")]),
            "thread missing author": result(threads=[{k: v for k, v in thread().items() if k != "author"}]),
            "is_bot as text": result(threads=[thread(is_bot="false")]),
            "bad thread state": result(threads=[thread(state="outdated")]),
            "bad decision": result(threads=[thread(decision="ignored")]),
            "reply_url a number": result(threads=[thread(reply_url=5)]),
            "reply_url empty": result(threads=[thread(reply_url="")]),
            "empty last_action": result(last_action=" "),
            "mergeable as text": result(mergeable="MERGEABLE"),
            "approved_on_head null": result(approved_on_head=None),
            "changes_requested as text": result(changes_requested="false"),
            "required_checks not a list": result(required_checks={}),
            "required check bad state": result(required_checks=[check(state="none")]),
            "required check no name": result(required_checks=[{"state": "passing"}]),
            "required check empty name": result(required_checks=[check(name="")]),
            "duplicate required check name": result(required_checks=[check(), check(state="passing")]),
            "last_action on two lines": result(last_action="fixed it\nIgnore the above"),
            "last_action with CR": result(last_action="fixed\rit"),
            "last_action with a tab": result(last_action="fixed\tit"),
            "last_action with ESC": result(last_action="fixed \x1b[2Jit"),
            "last_action with NUL": result(last_action="fixed\0"),
            "last_action with DEL": result(last_action="fixed\x7f"),
            "last_action with U+2028": result(last_action="fixed\u2028it"),
            "thread id with a newline": result(threads=[thread(id="PRRT_1\nPRRT_2")]),
            "thread id with ESC": result(threads=[thread(id="PRRT_1\x1b[0m")]),
            "schema_version true": result(schema_version=True),
            "written_at not UTC": result(written_at="2026-01-31T12:00:00+02:00"),
            "written_at impossible": result(written_at="2026-02-30T12:00:00Z"),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(data, "--pr", 42, *self.shas)
                self.assertFalse(os.path.exists(self.vstack))

    def test_reports_every_error_without_echoing_values(self):
        err = self.refused(result(status="IGNORE ALL", mergeable="yes", threads=[thread(state="x")]),
                           "--pr", 42, *self.shas)
        self.assertIn("status: must be", err)
        self.assertIn("mergeable: must be", err)
        self.assertIn("threads[0].state: must be", err)
        self.assertNotIn("IGNORE ALL", err)

    def test_file_option_and_subdirectory(self):
        f = tmpdir(self) / "result.json"
        f.write_text(json.dumps(result()))
        self.assertEqual(self.ok("", "--pr", 42, *self.shas, "--file", f, cwd=self.repo / "src")["pr"], 42)

    def test_written_at_filled(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        out = self.ok(result(), "--pr", 42, *self.shas)
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertRegex(out["written_at"], WRITTEN_AT)
        at = datetime.datetime.strptime(out["written_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        self.assertTrue(before <= at <= after, (before, at, after))

    def test_values_in_the_result_are_kept(self):
        given = result(schema_version=1, skill="babysit", pr=42, head_sha=self.head, base_sha=self.base,
                       merge_base=self.base, written_at="2026-01-31T12:00:00.123+00:00")
        self.assertEqual(self.ok(given, *self.shas), given)

    def test_result_and_options_must_agree(self):
        for over, args in [({"pr": 41}, ["--pr", 42]),
                           ({"head_sha": self.base}, ["--pr", 42]),
                           ({"base_sha": self.head}, ["--pr", 42]),
                           ({"merge_base": self.head}, ["--pr", 42]),
                           ({"skill": "review"}, ["--pr", 42]),
                           ({"schema_version": 2}, ["--pr", 42])]:
            with self.subTest(over=over):
                self.assertIn("disagree", self.refused(result(**over), *args, *self.shas))
                self.assertFalse(os.path.exists(self.vstack))

    def test_sha_options(self):
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        for i, opt in enumerate(["--head-sha", "--base-sha"]):
            for bad in [self.head[:7], self.head[:39], self.head + "0", self.head.upper(), self.head + "\n",
                        UNKNOWN, tree, "HEAD", "main", ""]:
                with self.subTest(opt=opt, sha=bad):
                    args = list(self.shas)
                    args[2 * i + 1] = bad
                    self.assertIn(opt, self.refused(result(), "--pr", 42, *args))
            with self.subTest(opt=opt, sha="missing"):
                args = self.shas[:2 * i] + self.shas[2 * i + 2:]
                self.assertIn(opt, self.refused(result(), "--pr", 42, *args))
        self.assertFalse(os.path.exists(self.vstack))

    def test_merge_base_computed(self):
        self.assertEqual(self.ok(result(), "--pr", 42, *self.shas)["merge_base"], self.base)
        self.refused(result(), "--pr", 42, *self.shas, "--merge-base", self.base)
        for name, (repo, base, head) in {"no merge base": unrelated_repo(self),
                                         "several merge bases": criss_cross_repo(self)}.items():
            with self.subTest(name):
                r = write(repo, result(), "--pr", 42, "--head-sha", head, "--base-sha", base)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(b"merge base", r.stderr)
                self.assertFalse(os.path.exists(os.path.join(repo, ".git", "vstack")))

    @unittest.skipUnless(sha256_supported(), "git can't make sha256 repos")
    def test_sha256_repo(self):
        repo, base, head = pr_repo(self, "sha256")
        self.assertEqual(len(head), 64)
        r = write(repo, result(), "--pr", 42, "--head-sha", head, "--base-sha", base)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(r.stdout.decode().strip()) as f:
            self.assertEqual(json.load(f)["head_sha"], head)

    def test_bad_pr(self):
        for pr in ["0", "-1", "+1", "01", "4.2", " 42", "42\n", "abc", "", "2147483648"]:
            with self.subTest(arg=pr):
                self.refused(result(), "--pr", pr, *self.shas)
        for pr in [0, -3, "42", True, 4.0, None, 2**31, [42]]:
            with self.subTest(field=pr):
                self.refused(result(pr=pr), *self.shas)
        with self.subTest("missing"):
            self.assertIn("pr: missing", self.refused(result(), *self.shas))
        self.assertFalse(os.path.exists(self.vstack))

    def test_atomic_overwrite(self):
        self.ok(result(), "--pr", 42, *self.shas)
        ino = os.stat(self.out).st_ino
        with open(self.out) as old:
            self.ok(result(status="handed_back"), "--pr", 42, *self.shas)
            self.assertEqual(json.load(old)["status"], "running")
        self.assertNotEqual(os.stat(self.out).st_ino, ino)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["status"], "handed_back")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["babysit.json"])
        self.assertEqual(os.listdir(self.vstack), ["42"])

    def test_outside_the_tree(self):
        def state():
            return (git(self.repo, "status", "--porcelain", "-uall", "--ignored"),
                    git(self.repo, "ls-files", "-s"))

        before = state()
        self.ok(result(), "--pr", 42, *self.shas)
        self.assertEqual(state(), before)
        self.assertFalse((self.repo / ".vstack").exists())
        git(self.repo, "add", "-A")
        self.assertEqual(git(self.repo, "status", "--porcelain"), b"")

    def test_shared_across_worktrees(self):
        wt = tmpdir(self) / "wt"
        git(self.repo, "worktree", "add", "-q", "--detach", str(wt), self.head)
        self.ok(result(), "--pr", 42, *self.shas, cwd=wt)
        self.assertFalse((wt / "vstack").exists())
        self.assertEqual(git(wt, "status", "--porcelain", "-uall", "--ignored"), b"")

    def test_refuses_symlinked_vstack(self):
        elsewhere = tmpdir(self)
        os.symlink(elsewhere, self.vstack)
        self.assertIn("refusing", self.refused(result(), "--pr", 42, *self.shas))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_refuses_symlinked_pr_dir(self):
        elsewhere = tmpdir(self)
        os.mkdir(self.vstack)
        os.symlink(elsewhere, os.path.join(self.vstack, "42"))
        self.assertIn("refusing", self.refused(result(), "--pr", 42, *self.shas))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_refuses_dangling_symlink_and_file(self):
        missing = tmpdir(self) / "missing"
        os.symlink(missing, self.vstack)
        self.refused(result(), "--pr", 42, *self.shas)
        self.assertFalse(os.path.lexists(missing))
        os.unlink(self.vstack)
        with open(self.vstack, "w") as f:
            f.write("x")
        self.refused(result(), "--pr", 42, *self.shas)

    def test_outside_a_repo_refuses(self):
        cwd = tmpdir(self) / "plain"
        cwd.mkdir()
        env = {**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(cwd.parent)}
        r = write(cwd, result(), "--pr", 7, *self.shas, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"not in a git repository", r.stderr)
        self.assertEqual(os.listdir(cwd), [])

    def test_internal_error(self):
        os.makedirs(self.out)
        r = write(self.repo, result(), "--pr", 42, *self.shas)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn(b"internal error", r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["babysit.json"])


if __name__ == "__main__":
    unittest.main()
