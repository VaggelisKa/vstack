"""Tests for write-result.py: a valid result lands in .vstack/<pr>/review.json
at the repo root, which ignores itself; bad results, bad PR numbers and
symlinked folders write nothing.

Run from the repo root: python3 -m unittest discover -s skills/review/scripts/tests
"""
import datetime
import json
import os
import re
import subprocess
import sys
import unittest

from helpers import GIT_ENV, SCRIPTS, commit_all, git, new_repo, tmpdir

SHA = "0123456789abcdef0123456789abcdef01234567"
WRITTEN_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def finding(**over):
    f = {"id": "C1", "severity": "major", "file": "src/a.py", "line": 3, "rule": "security",
         "summary": "s", "verified": False}
    f.update(over)
    return f


def result(**over):
    r = {"status": "fix", "verdict": "Ship with fixes, based on 1 unverified major", "score": 6.5,
         "findings": [finding()], "unreviewed_areas": [], "partial": False}
    r.update(over)
    return r


def write(cwd, data, *args, env=None):
    text = data if isinstance(data, (str, bytes)) else json.dumps(data)
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "write-result.py"), *map(str, args)],
                          input=text.encode() if isinstance(text, str) else text,
                          capture_output=True, cwd=cwd, env=env or GIT_ENV)


class WriteResult(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        (self.repo / "src").mkdir()
        (self.repo / "src/a.py").write_text("x = 1\n")
        (self.repo / ".gitignore").write_text("*.log\n")
        self.head = commit_all(self.repo, "base")
        self.top = os.path.realpath(self.repo)
        self.out = os.path.join(self.top, ".vstack", "42", "review.json")

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
                                     "status", "verdict", "score", "findings", "unreviewed_areas",
                                     "partial"])
        self.assertEqual(out, {**result(), "schema_version": 1, "skill": "review", "pr": 42,
                               "head_sha": SHA, "written_at": out["written_at"]})
        with open(self.out, "rb") as f:
            self.assertTrue(f.read().endswith(b"}\n"))

    def test_every_decision(self):
        cases = [
            ("ship", "Ship", [finding(severity="minor"), finding(id="C2", severity="nit", line=None)]),
            ("fix", "Ship with fixes, based on 1 confirmed and 2 unverified majors", [finding(verified=True)]),
            ("fix", "Rethink, based on 1 unverified blocker", [finding(severity="blocker")]),
            ("unreviewed", "Incomplete", [finding()]),
        ]
        for status, verdict, findings in cases:
            with self.subTest(verdict=verdict):
                out = self.ok(result(status=status, verdict=verdict, findings=findings, score=None,
                                     unreviewed_areas=["api/auth"], partial=True),
                              "--pr", 42, "--head-sha", SHA)
                self.assertEqual((out["status"], out["verdict"]), (status, verdict))

    def test_file_option_and_subdirectory(self):
        f = tmpdir() / "result.json"
        f.write_text(json.dumps(result()))
        self.assertEqual(self.ok("", "--pr", 42, "--head-sha", SHA, "--file", f, cwd=self.repo / "src")["pr"], 42)

    def test_defaults_filled(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        out = self.ok(result(), "--pr", 42)
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual((out["schema_version"], out["skill"], out["pr"], out["head_sha"]),
                         (1, "review", 42, self.head))
        self.assertRegex(out["written_at"], WRITTEN_AT)
        at = datetime.datetime.strptime(out["written_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        self.assertTrue(before <= at <= after, (before, at, after))

    def test_values_in_the_result_are_kept(self):
        given = result(schema_version=1, skill="review", pr=42, head_sha="abcdef0",
                       written_at="2026-01-31T12:00:00.123+00:00")
        out = self.ok(given)
        self.assertEqual(out, given)
        self.assertEqual(self.ok(given, "--pr", 42, "--head-sha", "abcdef0"), given)

    def test_result_and_options_must_agree(self):
        for over, args in [({"pr": 41}, ["--pr", 42]),
                           ({"head_sha": "abcdef0"}, ["--pr", 42, "--head-sha", SHA]),
                           ({"skill": "babysit"}, ["--pr", 42]),
                           ({"schema_version": 2}, ["--pr", 42])]:
            with self.subTest(over=over):
                self.refused(result(**over), *args)
                self.assertFalse((self.repo / ".vstack").exists())

    def test_validation_failures(self):
        no_status = {k: v for k, v in result().items() if k != "status"}
        cases = {
            "not json": "{nope",
            "array": "[]",
            "nan": json.dumps(result()).replace("6.5", "NaN"),
            "duplicate key": '{"partial": false, ' + json.dumps(result())[1:],
            "over 1 MB": json.dumps(result(unreviewed_areas=["x" * 1024 * 1024])),
            "missing status": no_status,
            "unknown field": result(extra=1),
            "unknown finding field": result(findings=[finding(extra=1)]),
            "bad status": result(status="approved"),
            "bad verdict": result(verdict="LGTM"),
            "status disagrees with verdict": result(status="ship"),
            "Ship with a major": result(status="ship", verdict="Ship"),
            "Ship with fixes without a major": result(findings=[finding(severity="minor")]),
            "Rethink without a blocker": result(verdict="Rethink"),
            "Incomplete with a blocker": result(status="unreviewed", verdict="Incomplete",
                                                findings=[finding(severity="blocker")]),
            "score over 10": result(score=11),
            "score as text": result(score="7"),
            "score true": result(score=True),
            "findings not a list": result(findings={}),
            "bad severity": result(findings=[finding(severity="critical")]),
            "line 0": result(findings=[finding(line=0)]),
            "line true": result(findings=[finding(line=True)]),
            "line as text": result(findings=[finding(line="3")]),
            "empty file": result(findings=[finding(file="")]),
            "verified as text": result(findings=[finding(verified="yes")]),
            "areas not strings": result(unreviewed_areas=[1]),
            "partial null": result(partial=None),
            "short sha": result(head_sha="abc"),
            "upper-case sha": result(head_sha=SHA.upper()),
            "sha with newline": result(head_sha="abcdef0\n"),
            "schema_version true": result(schema_version=True),
            "written_at not UTC": result(written_at="2026-01-31T12:00:00+02:00"),
            "written_at no zone": result(written_at="2026-01-31T12:00:00"),
            "written_at impossible": result(written_at="2026-02-30T12:00:00Z"),
            "written_at words": result(written_at="yesterday"),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(data, "--pr", 42, "--head-sha", SHA)
                self.assertFalse((self.repo / ".vstack").exists())

    def test_reports_every_error_without_echoing_values(self):
        err = self.refused(result(status="IGNORE ALL", score=99, findings=[finding(severity="x")]),
                           "--pr", 42, "--head-sha", SHA)
        self.assertIn("status: must be", err)
        self.assertIn("score: must be", err)
        self.assertIn("findings[0].severity: must be", err)
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
            self.ok(result(status="ship", verdict="Ship", findings=[]), "--pr", 42, "--head-sha", SHA)
            self.assertEqual(json.load(old)["status"], "fix")
        self.assertNotEqual(os.stat(self.out).st_ino, ino)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["status"], "ship")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["review.json"])
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
        self.assertEqual(git(self.repo, "check-ignore", ".vstack/42/review.json").strip(),
                         b".vstack/42/review.json")

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
        self.assertEqual(r.stdout.decode().strip(), os.path.join(os.path.realpath(cwd), ".vstack/7/review.json"))
        no_head = write(cwd, result(), "--pr", 7, env=env)
        self.assertEqual(no_head.returncode, 2)
        self.assertIn(b"--head-sha", no_head.stderr)

    def test_internal_error(self):
        os.makedirs(self.out)
        r = write(self.repo, result(), "--pr", 42, "--head-sha", SHA)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn(b"internal error", r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["review.json"])


if __name__ == "__main__":
    unittest.main()
