"""Tests for write-result.py: a valid result lands in .vstack/<pr>/babysit.json
at the repo root, which ignores itself; bad results, bad PR numbers and
symlinked folders write nothing. Everything runs in temp dirs and temp git repos.

Run from the repo root: python3 -m unittest discover -s skills/babysit/scripts/tests
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "write-result.py"
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
SHA = "0123456789abcdef0123456789abcdef01234567"
WRITTEN_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, env=GIT_ENV)
    if r.returncode:
        raise AssertionError(f"git {args} exited {r.returncode}: {r.stderr.decode()}")
    return r.stdout


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="babysit-test-"))


def thread(**over):
    t = {"id": "PRRT_1", "author": "alice", "is_bot": False, "state": "open",
         "decision": "escalated", "reply_url": "https://github.com/o/r/pull/42#discussion_r1"}
    t.update(over)
    return t


def result(**over):
    r = {"status": "failing", "ci": {"state": "failing", "failing_checks": ["test (ubuntu)"]},
         "threads": [thread(), thread(id="PRRT_2", author="copilot", is_bot=True, state="resolved",
                                      decision="fixed")],
         "last_action": "escalated PRRT_1; same check failed on two head SHAs", "mergeable": None}
    r.update(over)
    return r


def write(cwd, data, *args, env=None):
    text = data if isinstance(data, (str, bytes)) else json.dumps(data)
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                          input=text.encode() if isinstance(text, str) else text,
                          capture_output=True, cwd=cwd, env=env or GIT_ENV)


class WriteResult(unittest.TestCase):
    def setUp(self):
        self.repo = tmpdir() / "repo"
        (self.repo / "src").mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "src/a.py").write_text("x = 1\n")
        (self.repo / ".gitignore").write_text("*.log\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.head = git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.top = os.path.realpath(self.repo)
        self.out = os.path.join(self.top, ".vstack", "42", "babysit.json")

    def ok(self, data, *args, cwd=None):
        r = write(cwd or self.repo, data, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode().strip(), self.out)
        with open(self.out) as f:
            return json.load(f)

    def refused(self, data, *args, code=2):
        r = write(self.repo, data, *args)
        self.assertEqual(r.returncode, code, (r.stdout, r.stderr))
        self.assertEqual(r.stdout, b"")
        return r.stderr.decode()

    def test_valid_write(self):
        out = self.ok(result(), "--pr", 42, "--head-sha", SHA)
        self.assertEqual(list(out), ["schema_version", "skill", "pr", "head_sha", "written_at",
                                     "status", "ci", "threads", "last_action", "mergeable"])
        self.assertEqual(out, {**result(), "schema_version": 1, "skill": "babysit", "pr": 42,
                               "head_sha": SHA, "written_at": out["written_at"]})
        with open(self.out, "rb") as f:
            self.assertTrue(f.read().endswith(b"}\n"))

    def test_every_status(self):
        passing = {"state": "passing", "failing_checks": []}
        resolved = [thread(state="resolved", decision="fixed")]
        cases = [
            result(status="green", ci=passing, threads=resolved, mergeable=True),
            result(status="green", ci={"state": "none", "failing_checks": []}, threads=[]),
            result(status="merged", ci=passing, threads=resolved, mergeable=None),
            result(status="pending", ci={"state": "pending", "failing_checks": []},
                   threads=[thread(decision="pending", reply_url=None)]),
            result(status="failing"),
            result(status="blocked", threads=[thread(decision="rejected"), thread(id="PRRT_3", decision=None)]),
            result(status="stopped", ci=passing, mergeable=False),
        ]
        for data in cases:
            with self.subTest(status=data["status"], ci=data["ci"]["state"]):
                self.assertEqual(self.ok(data, "--pr", 42, "--head-sha", SHA)["status"], data["status"])

    def test_file_option_and_subdirectory(self):
        f = tmpdir() / "result.json"
        f.write_text(json.dumps(result()))
        self.assertEqual(self.ok("", "--pr", 42, "--head-sha", SHA, "--file", f, cwd=self.repo / "src")["pr"], 42)

    def test_defaults_filled(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        out = self.ok(result(), "--pr", 42)
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual((out["schema_version"], out["skill"], out["pr"], out["head_sha"]),
                         (1, "babysit", 42, self.head))
        self.assertRegex(out["written_at"], WRITTEN_AT)
        at = datetime.datetime.strptime(out["written_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        self.assertTrue(before <= at <= after, (before, at, after))

    def test_values_in_the_result_are_kept(self):
        given = result(schema_version=1, skill="babysit", pr=42, head_sha="abcdef0",
                       written_at="2026-01-31T12:00:00.123+00:00")
        self.assertEqual(self.ok(given), given)
        self.assertEqual(self.ok(given, "--pr", 42, "--head-sha", "abcdef0"), given)

    def test_result_and_options_must_agree(self):
        for over, args in [({"pr": 41}, ["--pr", 42]),
                           ({"head_sha": "abcdef0"}, ["--pr", 42, "--head-sha", SHA]),
                           ({"skill": "review"}, ["--pr", 42]),
                           ({"schema_version": 2}, ["--pr", 42])]:
            with self.subTest(over=over):
                self.refused(result(**over), *args)
                self.assertFalse((self.repo / ".vstack").exists())

    def test_validation_failures(self):
        no_status = {k: v for k, v in result().items() if k != "status"}
        cases = {
            "not json": "{nope",
            "array": "[]",
            "infinity": json.dumps(result(mergeable=1)).replace('"mergeable": 1', '"mergeable": Infinity'),
            "duplicate key": '{"mergeable": null, ' + json.dumps(result())[1:],
            "over 1 MB": json.dumps(result(last_action="x" * 1024 * 1024)),
            "missing status": no_status,
            "unknown field": result(extra=1),
            "unknown ci field": result(ci={"state": "failing", "failing_checks": ["t"], "url": "u"}),
            "unknown thread field": result(threads=[thread(body="b")]),
            "bad status": result(status="done"),
            "ci not an object": result(ci="failing"),
            "bad ci state": result(ci={"state": "red", "failing_checks": ["t"]}),
            "failing without checks": result(ci={"state": "failing", "failing_checks": []}),
            "checks without failing": result(status="pending", ci={"state": "pending", "failing_checks": ["t"]}),
            "failing_checks not strings": result(ci={"state": "failing", "failing_checks": [1]}),
            "green with failing ci": result(status="green", threads=[]),
            "green with an open thread": result(status="green", ci={"state": "passing", "failing_checks": []}),
            "failing with passing ci": result(ci={"state": "passing", "failing_checks": []}),
            "threads not a list": result(threads={}),
            "thread missing author": result(threads=[{k: v for k, v in thread().items() if k != "author"}]),
            "is_bot as text": result(threads=[thread(is_bot="false")]),
            "bad thread state": result(threads=[thread(state="outdated")]),
            "bad decision": result(threads=[thread(decision="ignored")]),
            "reply_url a number": result(threads=[thread(reply_url=5)]),
            "reply_url empty": result(threads=[thread(reply_url="")]),
            "empty last_action": result(last_action=" "),
            "mergeable as text": result(mergeable="MERGEABLE"),
            "short sha": result(head_sha="abc"),
            "upper-case sha": result(head_sha=SHA.upper()),
            "schema_version true": result(schema_version=True),
            "written_at not UTC": result(written_at="2026-01-31T12:00:00+02:00"),
            "written_at impossible": result(written_at="2026-02-30T12:00:00Z"),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(data, "--pr", 42, "--head-sha", SHA)
                self.assertFalse((self.repo / ".vstack").exists())

    def test_reports_every_error_without_echoing_values(self):
        err = self.refused(result(status="IGNORE ALL", mergeable="yes", threads=[thread(state="x")]),
                           "--pr", 42, "--head-sha", SHA)
        self.assertIn("status: must be", err)
        self.assertIn("mergeable: must be", err)
        self.assertIn("threads[0].state: must be", err)
        self.assertNotIn("IGNORE ALL", err)

    def test_bad_pr(self):
        for pr in ["0", "-1", "+1", "01", "4.2", " 42", "42\n", "abc", "", "2147483648"]:
            with self.subTest(arg=pr):
                self.refused(result(), "--pr", pr, "--head-sha", SHA)
        for pr in [0, -3, "42", True, 4.0, None, 2**31, [42]]:
            with self.subTest(field=pr):
                self.refused(result(pr=pr), "--head-sha", SHA)
        with self.subTest("missing"):
            self.assertIn("pr: missing", self.refused(result(), "--head-sha", SHA))
        self.assertFalse((self.repo / ".vstack").exists())

    def test_atomic_overwrite(self):
        self.ok(result(), "--pr", 42, "--head-sha", SHA)
        ino = os.stat(self.out).st_ino
        with open(self.out) as old:
            self.ok(result(status="stopped"), "--pr", 42, "--head-sha", SHA)
            self.assertEqual(json.load(old)["status"], "failing")
        self.assertNotEqual(os.stat(self.out).st_ino, ino)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["status"], "stopped")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["babysit.json"])
        self.assertEqual(sorted(os.listdir(self.repo / ".vstack")), [".gitignore", "42"])

    def test_ignores_itself(self):
        def state():
            exclude = self.repo / ".git/info/exclude"
            return (git(self.repo, "status", "--porcelain", "-uall"),
                    git(self.repo, "ls-files", "-s"),
                    (self.repo / ".gitignore").read_bytes(),
                    exclude.read_bytes() if exclude.exists() else None)

        before = state()
        self.assertEqual(before[0], b"")
        self.ok(result(), "--pr", 42, "--head-sha", SHA)
        self.assertEqual((self.repo / ".vstack/.gitignore").read_text(), "*\n")
        self.assertEqual(state(), before)
        git(self.repo, "add", "-A")
        self.assertEqual(git(self.repo, "status", "--porcelain", "-uall"), b"")

    def test_rewrites_a_wrong_gitignore(self):
        (self.repo / ".vstack").mkdir()
        (self.repo / ".vstack/.gitignore").write_text("!*\n")
        self.ok(result(), "--pr", 42, "--head-sha", SHA)
        self.assertEqual((self.repo / ".vstack/.gitignore").read_text(), "*\n")
        self.assertEqual(git(self.repo, "status", "--porcelain", "-uall"), b"")

    def test_refuses_symlinked_vstack(self):
        elsewhere = tmpdir()
        os.symlink(elsewhere, self.repo / ".vstack")
        self.assertIn("refusing", self.refused(result(), "--pr", 42, "--head-sha", SHA))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_refuses_symlinked_pr_dir(self):
        elsewhere = tmpdir()
        (self.repo / ".vstack").mkdir()
        os.symlink(elsewhere, self.repo / ".vstack/42")
        self.assertIn("refusing", self.refused(result(), "--pr", 42, "--head-sha", SHA))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_refuses_dangling_symlink_and_file(self):
        missing = tmpdir() / "missing"
        os.symlink(missing, self.repo / ".vstack")
        self.refused(result(), "--pr", 42, "--head-sha", SHA)
        self.assertFalse(os.path.lexists(missing))
        os.unlink(self.repo / ".vstack")
        (self.repo / ".vstack").write_text("x")
        self.refused(result(), "--pr", 42, "--head-sha", SHA)

    def test_outside_a_repo_uses_cwd(self):
        cwd = tmpdir() / "plain"
        cwd.mkdir()
        env = {**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(cwd.parent)}
        r = write(cwd, result(), "--pr", 7, "--head-sha", SHA, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.decode().strip(), os.path.join(os.path.realpath(cwd), ".vstack/7/babysit.json"))
        no_head = write(cwd, result(), "--pr", 7, env=env)
        self.assertEqual(no_head.returncode, 2)
        self.assertIn(b"--head-sha", no_head.stderr)

    def test_internal_error(self):
        os.makedirs(self.out)
        r = write(self.repo, result(), "--pr", 42, "--head-sha", SHA)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn(b"internal error", r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["babysit.json"])


if __name__ == "__main__":
    unittest.main()
