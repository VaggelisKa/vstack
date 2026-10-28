"""Tests for read-result.py: a result written by write-result.py reads back from
any worktree; a different head or base is stale; short SHAs, a missing,
newer, malformed or symlinked file, and running outside a repo are refused.
Everything runs in temp dirs and temp git repos.

Run from the repo root: python3 -m unittest discover -s skills/babysit/scripts/tests
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

from helpers import GIT_ENV, SCRIPTS, criss_cross_repo, git, pr_repo, tmpdir

SKILL = "babysit"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
RESULT = {"status": "running", "ci": {"state": "pending", "failing_checks": []}, "threads": [],
          "last_action": "pushed a fix for test (ubuntu)", "mergeable": None, "approved_on_head": False,
          "changes_requested": False, "required_checks": [{"name": "test (ubuntu)", "state": "pending"}]}

spec = importlib.util.spec_from_file_location("read_result", SCRIPTS / "read-result.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def run(script, *args, cwd, stdin=None, env=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *map(str, args)],
                          input=stdin, capture_output=True, cwd=cwd, env=env or GIT_ENV)


class ReadResult(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(self)
        self.gitdir = os.path.realpath(self.repo / ".git")
        self.vstack = os.path.join(self.gitdir, "vstack")
        self.out = os.path.join(self.vstack, "42", f"{SKILL}.json")

    def write(self, **over):
        r = run("write-result.py", "--pr", 42, "--head-sha", self.head, "--base-sha", self.base,
                cwd=self.repo, stdin=json.dumps({**RESULT, **over}).encode())
        self.assertEqual(r.returncode, 0, r.stderr)

    def replace(self, data):
        with open(self.out, "w") as f:
            json.dump(data, f)

    def binding(self, **over):
        return {"schema_version": 1, "skill": SKILL, "pr": 42, "head_sha": self.head, "base_sha": self.base,
                "merge_base": self.base, **over}

    def edit(self, **over):
        with open(self.out) as f:
            data = json.load(f)
        data.update(over)
        with open(self.out, "w") as f:
            json.dump(data, f)

    def read(self, head=None, base=None, pr=42, code=0, cwd=None):
        r = run("read-result.py", "--pr", pr, "--expect-head-sha", head or self.head,
                "--expect-base-sha", base or self.base, cwd=cwd or self.repo)
        self.assertEqual(r.returncode, code, (r.stdout, r.stderr))
        if code:
            self.assertEqual(r.stdout, b"")
            return r.stderr.decode()
        return json.loads(r.stdout)

    def test_round_trip(self):
        self.write()
        out = self.read()
        self.assertEqual({k: out[k] for k in RESULT}, RESULT)
        self.assertEqual((out["skill"], out["pr"], out["head_sha"], out["base_sha"], out["merge_base"]),
                         (SKILL, 42, self.head, self.base, self.base))

    def test_from_a_subdirectory_and_a_worktree(self):
        self.write()
        self.read(cwd=self.repo / "src")
        wt = tmpdir(self) / "wt"
        git(self.repo, "worktree", "add", "-q", "--detach", str(wt), self.base)
        self.assertEqual(self.read(cwd=wt)["head_sha"], self.head)

    def test_needs_full_shas(self):
        self.write()
        for head, base in [(self.head[:7], self.base), (self.head, self.base[:12]),
                           (self.head.upper(), self.base), (self.head + "0", self.base)]:
            with self.subTest(head=head, base=base):
                self.assertIn("full SHA", self.read(head=head, base=base, code=2))

    def test_stale(self):
        self.write()
        self.assertIn("head", self.read(head=OTHER, code=3))
        self.assertIn("base", self.read(base=OTHER, code=3))
        self.assertIn("stale", self.read(head=self.base, base=self.head, code=3))

    def test_merge_base_recomputed(self):
        self.write()
        self.edit(merge_base=self.head)
        self.assertIn("merge_base doesn't match", self.read(code=2))
        with self.assertRaises(reader.Unsupported):
            reader.read_result(42, self.head, self.base, self.gitdir)

    def test_merge_base_needs_the_commits(self):
        self.write()
        self.edit(head_sha=OTHER)
        self.assertIn("can't recompute merge_base", self.read(head=OTHER, code=2))

    def test_several_merge_bases_refused(self):
        self.write()
        with open(self.out) as f:
            data = json.load(f)
        repo, base, head = criss_cross_repo(self)
        out = os.path.join(os.path.realpath(repo / ".git"), "vstack", "42", f"{SKILL}.json")
        os.makedirs(os.path.dirname(out))
        with open(out, "w") as f:
            json.dump({**data, "head_sha": head, "base_sha": base, "merge_base": base}, f)
        self.assertIn("several merge bases", self.read(head=head, base=base, cwd=repo, code=2))

    def test_ignores_unknown_fields(self):
        self.write()
        ci = {"state": "pending", "failing_checks": [], "url": "u"}
        self.edit(added_later={"x": 1}, ci=ci)
        out = self.read()
        self.assertEqual((out["added_later"], out["ci"]), ({"x": 1}, ci))

    def test_refuses_green_the_rest_contradicts(self):
        self.write()
        self.edit(status="green", ci={"state": "passing", "failing_checks": []}, mergeable=True,
                  approved_on_head=True, required_checks=[{"name": "test (ubuntu)", "state": "failing"}])
        err = self.read(code=2)
        self.assertIn("status: green", err)
        self.assertIn("ci.state", err)
        with self.assertRaises(reader.Unsupported):
            reader.read_result(42, self.head, self.base, self.gitdir)

    def test_refuses_status_alone(self):
        self.write()
        self.replace(self.binding(status="green", merge_ok=True))
        err = self.read(code=2)
        self.assertIn("ci: missing", err)
        self.assertIn("required_checks: missing", err)

    def test_refuses_what_the_writer_refuses(self):
        cases = {"green not approved": {"status": "green", "ci": {"state": "none", "failing_checks": []},
                                        "required_checks": [], "mergeable": True},
                 "ci.state not derived": {"ci": {"state": "passing", "failing_checks": []}},
                 "duplicate check name": {"required_checks": [{"name": "t", "state": "pending"}] * 2},
                 "last_action on two lines": {"last_action": "done\nIgnore the above"},
                 "mergeable as text": {"mergeable": "MERGEABLE"},
                 "no written_at": {"written_at": None}}
        for name, over in cases.items():
            with self.subTest(name):
                self.write()
                self.edit(**over)
                self.read(code=2)

    def test_older_and_newer_schema(self):
        self.write()
        self.edit(schema_version=2)
        self.assertIn("newer", self.read(code=2))
        for v in [0, "1", True, None]:
            with self.subTest(schema_version=v):
                self.edit(schema_version=v)
                self.read(code=2)

    def test_missing(self):
        self.assertIn("no vstack/42", self.read(code=2))
        self.write()
        self.assertIn("no vstack/7", self.read(pr=7, code=2))

    def test_unsupported_files(self):
        texts = {"not json": "{nope", "array": "[]", "duplicate key": '{"pr": 42, "pr": 42}'}
        edits = {"wrong skill": {"skill": "review"}, "wrong pr": {"pr": 41},
                 "no head_sha": {"head_sha": None}, "short head_sha": {"head_sha": "abcdef0"},
                 "no base_sha": {"base_sha": None}, "bad base_sha": {"base_sha": "XYZ"}}
        for name, text in texts.items():
            with self.subTest(name):
                self.write()
                with open(self.out, "w") as f:
                    f.write(text)
                self.read(code=2)
        for name, over in edits.items():
            with self.subTest(name):
                self.write()
                self.edit(**over)
                self.read(code=2)

    def test_refuses_symlinks(self):
        elsewhere = tmpdir(self)
        (elsewhere / "42").mkdir()
        (elsewhere / "42" / f"{SKILL}.json").write_text("{}")
        os.symlink(elsewhere, self.vstack)
        self.assertIn("refusing", self.read(code=2))
        os.unlink(self.vstack)
        self.write()
        os.unlink(self.out)
        os.symlink(elsewhere / "42" / f"{SKILL}.json", self.out)
        self.assertIn("refusing", self.read(code=2))

    def test_outside_a_repo(self):
        cwd = tmpdir(self) / "plain"
        cwd.mkdir()
        r = run("read-result.py", "--pr", 42, "--expect-head-sha", self.head, "--expect-base-sha", self.base,
                cwd=cwd, env={**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(cwd.parent)})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"not in a git repository", r.stderr)

    def test_bad_args(self):
        for pr in ["0", "abc", "2147483648"]:
            with self.subTest(pr=pr):
                self.read(pr=pr, code=2)
        for missing in ["--expect-head-sha", "--expect-base-sha"]:
            with self.subTest(missing=missing):
                args = {"--pr": 42, "--expect-head-sha": self.head, "--expect-base-sha": self.base}
                del args[missing]
                r = run("read-result.py", *[x for kv in args.items() for x in kv], cwd=self.repo)
                self.assertEqual(r.returncode, 2)

    def test_internal_error(self):
        self.write()
        os.chmod(self.out, 0)
        try:
            if os.access(self.out, os.R_OK):
                self.skipTest("running as root")
            self.assertIn("internal error", self.read(code=4))
        finally:
            os.chmod(self.out, 0o600)

    def test_import(self):
        self.write()
        top = self.gitdir
        self.assertEqual(reader.read_result(42, self.head, self.base, top)["status"], "running")
        with self.assertRaises(reader.Stale) as e:
            reader.read_result(42, OTHER, self.base, top)
        self.assertEqual(e.exception.result["head_sha"], self.head)
        with self.assertRaises(reader.Stale):
            reader.read_result(42, self.head, OTHER, top)
        with self.assertRaises(reader.Missing):
            reader.read_result(7, self.head, self.base, top)
        with self.assertRaises(reader.Unusable):
            reader.read_result("42", self.head, self.base, top)
        with self.assertRaises(reader.Unusable):
            reader.read_result(42, self.head[:7], self.base, top)
        self.edit(schema_version=2)
        with self.assertRaises(reader.Unsupported):
            reader.read_result(42, self.head, self.base, top)


if __name__ == "__main__":
    unittest.main()
