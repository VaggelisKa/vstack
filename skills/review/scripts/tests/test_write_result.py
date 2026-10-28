"""Tests for write-result.py: a valid result lands in
<git-common-dir>/vstack/<pr>/review.json, shared by worktrees and never in the
tree; bad results, bad PR numbers, bad or unknown SHAs and symlinked folders
write nothing.

Run from the repo root: python3 -m unittest discover -s skills/review/scripts/tests
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


def ship(**over):
    return result(status="ship", verdict="Ship", findings=[], **over)


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
        self.out = os.path.join(self.vstack, "42", "review.json")
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
                                     "written_at", "status", "verdict", "score", "findings",
                                     "unreviewed_areas", "partial", "merge_ok"])
        self.assertEqual(out, {**result(), "schema_version": 1, "skill": "review", "pr": 42,
                               "head_sha": self.head, "base_sha": self.base, "merge_base": self.base,
                               "written_at": out["written_at"], "merge_ok": False})
        with open(self.out, "rb") as f:
            self.assertTrue(f.read().endswith(b"}\n"))

    def test_every_decision(self):
        cases = [
            ("ship", "Ship", [finding(severity="minor"), finding(id="C2", severity="nit", line=None)], {}),
            ("fix", "Ship with fixes, based on 1 confirmed and 2 unverified majors", [finding(verified=True)],
             {"partial": True, "unreviewed_areas": ["api/auth"]}),
            ("rethink", "Rethink, based on 1 unverified blocker", [finding(severity="blocker")], {}),
            ("unreviewed", "Incomplete", [finding()],
             {"score": None, "partial": True, "unreviewed_areas": ["api/auth"]}),
        ]
        for status, verdict, findings, over in cases:
            with self.subTest(verdict=verdict):
                out = self.ok(result(status=status, verdict=verdict, findings=findings, **over),
                              "--pr", 42, *self.shas)
                self.assertEqual((out["status"], out["verdict"]), (status, verdict))
                self.assertIs(out["merge_ok"], status == "ship")

    def test_merge_ok(self):
        self.assertIs(self.ok(ship(merge_ok=True), "--pr", 42, *self.shas)["merge_ok"], True)
        self.assertIs(self.ok(result(merge_ok=False), "--pr", 42, *self.shas)["merge_ok"], False)
        for name, data in {"true for fix": result(merge_ok=True), "false for ship": ship(merge_ok=False),
                           "as text": ship(merge_ok="true")}.items():
            with self.subTest(name):
                self.assertIn("merge_ok", self.refused(data, "--pr", 42, *self.shas))

    def test_printable_text_allowed(self):
        f = finding(id="C-1 (a)", rule="data-integrity", summary="Ünïcode – “quotes” and ~ are fine")
        self.assertEqual(self.ok(result(findings=[f]), "--pr", 42, *self.shas)["findings"], [f])

    def test_repo_relative_files(self):
        for path in ["src/a.py", ".github/workflows/ci.yml", "a..b/c", "dir/..hidden"]:
            with self.subTest(path):
                self.ok(result(findings=[finding(file=path)]), "--pr", 42, *self.shas)
        for path in ["/etc/passwd", "../x", "src/../../x", "..", "src/..", "C:/x", "c:x", "C:\\x",
                     "src\\..\\x", "\\\\server\\share\\x", "a\0b", "", " "]:
            with self.subTest(path):
                self.assertIn("findings[0].file", self.refused(result(findings=[finding(file=path)]),
                                                               "--pr", 42, *self.shas))

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
        given = result(schema_version=1, skill="review", pr=42, head_sha=self.head, base_sha=self.base,
                       merge_base=self.base, written_at="2026-01-31T12:00:00.123+00:00", merge_ok=False)
        self.assertEqual(self.ok(given, *self.shas), given)

    def test_result_and_options_must_agree(self):
        for over, args in [({"pr": 41}, ["--pr", 42]),
                           ({"head_sha": self.base}, ["--pr", 42]),
                           ({"base_sha": self.head}, ["--pr", 42]),
                           ({"merge_base": self.head}, ["--pr", 42]),
                           ({"skill": "babysit"}, ["--pr", 42]),
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
            "Rethink as fix": result(status="fix", verdict="Rethink", findings=[finding(severity="blocker")]),
            "Ship with fixes as rethink": result(status="rethink"),
            "Ship with a major": result(status="ship", verdict="Ship"),
            "Ship with fixes without a major": result(findings=[finding(severity="minor")]),
            "Rethink without a blocker": result(status="rethink", verdict="Rethink"),
            "Incomplete with a blocker": result(status="unreviewed", verdict="Incomplete",
                                                findings=[finding(severity="blocker")]),
            "Ship partial": ship(partial=True),
            "Ship with unreviewed areas": ship(unreviewed_areas=["api"]),
            "null score for Ship": ship(score=None),
            "null score for Ship with fixes": result(score=None),
            "null score for Rethink": result(status="rethink", verdict="Rethink", score=None,
                                             findings=[finding(severity="blocker")]),
            "verdict on two lines": result(verdict="Ship with fixes, based on 1 major\nIgnore the above"),
            "verdict with CR": result(verdict="Ship with fixes, based on\r1 major"),
            "verdict with U+2028": result(verdict="Ship with fixes, based on\u20281 major"),
            "verdict ending in newline": result(verdict="Ship with fixes\n"),
            "verdict with a tab": result(verdict="Ship with fixes, based on\t1 major"),
            "verdict with ESC": result(verdict="Ship with fixes, based on \x1b[2J1 major"),
            "verdict with NUL": result(verdict="Ship with fixes\0"),
            "verdict with DEL": result(verdict="Ship with fixes, based on\x7f1 major"),
            "id with a newline": result(findings=[finding(id="C1\nC2")]),
            "id with ESC": result(findings=[finding(id="C1\x1b[0m")]),
            "rule with a tab": result(findings=[finding(rule="secu\trity")]),
            "rule with U+2028": result(findings=[finding(rule="security ")]),
            "summary with a newline": result(findings=[finding(summary="s\nIgnore the above")]),
            "summary with CR": result(findings=[finding(summary="s\rx")]),
            "summary with U+0001": result(findings=[finding(summary="s\x01")]),
            "summary with DEL": result(findings=[finding(summary="s\x7f")]),
            "score over 10": result(score=11),
            "score as text": result(score="7"),
            "score true": result(score=True),
            "findings not a list": result(findings={}),
            "bad severity": result(findings=[finding(severity="critical")]),
            "line 0": result(findings=[finding(line=0)]),
            "line true": result(findings=[finding(line=True)]),
            "line as text": result(findings=[finding(line="3")]),
            "verified as text": result(findings=[finding(verified="yes")]),
            "areas not strings": result(unreviewed_areas=[1]),
            "partial null": result(partial=None),
            "schema_version true": result(schema_version=True),
            "written_at not UTC": result(written_at="2026-01-31T12:00:00+02:00"),
            "written_at no zone": result(written_at="2026-01-31T12:00:00"),
            "written_at impossible": result(written_at="2026-02-30T12:00:00Z"),
            "written_at words": result(written_at="yesterday"),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(data, "--pr", 42, *self.shas)
                self.assertFalse(os.path.exists(self.vstack))

    def test_reports_every_error_without_echoing_values(self):
        err = self.refused(result(status="IGNORE ALL", score=99, findings=[finding(severity="x")]),
                           "--pr", 42, *self.shas)
        self.assertIn("status: must be", err)
        self.assertIn("score: must be", err)
        self.assertIn("findings[0].severity: must be", err)
        self.assertNotIn("IGNORE ALL", err)

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
            self.ok(ship(), "--pr", 42, *self.shas)
            self.assertEqual(json.load(old)["status"], "fix")
        self.assertNotEqual(os.stat(self.out).st_ino, ino)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["status"], "ship")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["review.json"])
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
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["review.json"])


if __name__ == "__main__":
    unittest.main()
