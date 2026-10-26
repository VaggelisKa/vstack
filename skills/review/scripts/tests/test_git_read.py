"""Tests for git-read.sh: legitimate reads work, everything that could write a
file, run a program or read outside the commit is rejected with exit 2.

Run from the repo root: python3 -m unittest discover -s skills/review/scripts/tests
"""
import os
import pathlib
import shlex
import stat
import subprocess
import unittest

from helpers import GIT_ENV, SCRIPTS, commit_all, git, new_repo, tmpdir

SCRIPT = SCRIPTS / "git-read.sh"


class GitRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = new_repo()
        r = cls.repo
        (r / "src").mkdir()
        (r / "a.txt").write_text("one\n")
        (r / "src/app.py").write_text("x = 1\n")
        cls.base = commit_all(r, "base")
        git(r, "checkout", "-qb", "feature")
        (r / "a.txt").write_text("two\n")
        (r / "src/app.py").write_text("x = 2\n")
        cls.head = commit_all(r, "change")
        git(r, "checkout", "-q", "main")
        # Anything that runs a program leaves this marker behind.
        cls.marker = tmpdir() / "ran"
        cls.prog = tmpdir() / "prog.sh"
        cls.prog.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(cls.marker))}\ncat \"$1\" 2>/dev/null\nexit 0\n")
        cls.prog.chmod(cls.prog.stat().st_mode | stat.S_IXUSR)

    def setUp(self):
        self.out = tmpdir()            # where rejected --output attempts would write
        if self.marker.exists():
            self.marker.unlink()

    def tearDown(self):
        self.assertEqual(list(self.out.iterdir()), [], "a file was written")
        self.assertFalse(self.marker.exists(), "an external program ran")
        self.assertEqual(git(self.repo, "status", "--porcelain"), b"")

    def read(self, *args, env=None):
        return subprocess.run([str(SCRIPT), *map(str, args)], cwd=self.repo,
                              capture_output=True, env=env or GIT_ENV)

    def shell(self, cmd):
        """Run cmd through bash, so its quoting is undone by a real shell first."""
        return subprocess.run(["bash", "-c", f"{shlex.quote(str(SCRIPT))} {cmd}"],
                              cwd=self.repo, capture_output=True, env=GIT_ENV)

    def ok(self, *args, **kw):
        r = self.read(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        return r.stdout.decode()

    def rejected(self, r):
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertEqual(r.stdout, b"")
        self.assertIn(b"git-read: ", r.stderr)

    # --- legitimate reads -------------------------------------------------

    def test_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_show_blob(self):
        self.assertEqual(self.ok("show", f"{self.head}:a.txt"), "two\n")
        self.assertEqual(self.ok("show", "feature:src/app.py"), "x = 2\n")   # names resolve
        self.assertIn("app.py", self.ok("show", f"{self.head}:src"))        # tree listing

    def test_diff_three_dot(self):
        out = self.ok("diff", f"{self.base}...{self.head}")
        self.assertIn("--- a/a.txt\n+++ b/a.txt\n", out)
        self.assertIn("+++ b/src/app.py\n", out)
        out = self.ok("diff", f"{self.base}...feature", "--", "src/app.py")
        self.assertIn("+x = 2", out)
        self.assertNotIn("a.txt", out)

    def test_diff_safe_options(self):
        self.assertIn("2 files changed", self.ok("diff", "--stat", f"{self.base}...{self.head}"))
        self.assertEqual(self.ok("diff", "--name-only", f"{self.base}...{self.head}"),
                         "a.txt\nsrc/app.py\n")
        self.assertEqual(self.ok("diff", "--name-status", f"{self.base}...{self.head}"),
                         "M\ta.txt\nM\tsrc/app.py\n")
        self.assertIn("@@ -1 +1 @@", self.ok("diff", "-U0", f"{self.base}...{self.head}"))
        self.assertIn("@@ -1 +1 @@", self.ok("diff", "--unified=5", f"{self.base}...{self.head}"))

    def test_diff_prefixes_ignore_repo_config(self):
        for key in ("diff.noprefix", "diff.mnemonicPrefix"):
            git(self.repo, "config", key, "true")
            try:
                out = self.ok("diff", f"{self.base}...{self.head}")
                self.assertIn("--- a/a.txt\n+++ b/a.txt\n", out, key)
            finally:
                git(self.repo, "config", "--unset", key)

    def test_log(self):
        self.assertEqual(self.ok("log", "--oneline", f"{self.base}..{self.head}").count("\n"), 1)
        self.assertIn("change", self.ok("log", "-n", "1", "feature"))
        self.assertIn("change", self.ok("log", "-n1", "--stat", self.head))
        self.assertIn("a.txt", self.ok("log", "--max-count=1", "--name-only", self.head))
        self.assertIn("base", self.ok("log", "--oneline"))             # defaults to HEAD

    # --- writes a file, runs a program, or reads outside the commit -------

    def test_output_rejected_in_every_quoting(self):
        o = self.out
        for cmd in (f'show "--output={o}/q1" HEAD',            # the live bypasses
                    f"show '--output'={o}/q3 HEAD",
                    f'diff --out"put"={o}/q4 {self.base}...{self.head}',
                    f"log --output={o}/q5",
                    f"show {self.head}:a.txt --output={o}/q6"):
            with self.subTest(cmd=cmd):
                self.rejected(self.shell(cmd))
        # and the already-unquoted argument passed directly
        self.rejected(self.read("diff", f"--output={o}/q2", f"{self.base}...{self.head}"))
        self.rejected(self.read("diff", f"{self.base}...{self.head}", "--output", f"{o}/q7"))

    def test_program_running_options_rejected(self):
        rng = f"{self.base}...{self.head}"
        for args in (("diff", "--ext-diff", rng),
                     ("diff", "--textconv", rng),
                     ("diff", "--no-index", "/etc/hostname", "a.txt"),
                     ("diff", "-c", "diff.external=x", rng),
                     ("log", "-p", "--ext-diff"),
                     ("log", "--format=%H"),
                     ("-c", "x=y", "show", f"{self.head}:a.txt"),
                     ("--exec-path=/tmp", "show", f"{self.head}:a.txt"),
                     ("show", f"--textconv={self.head}:a.txt")):
            with self.subTest(args=args):
                self.rejected(self.read(*args))
        for cmd in (f'diff --ext"-diff" {rng}', f'diff --no"-index" /etc/hostname a.txt',
                    f"diff '--text'conv {rng}"):
            with self.subTest(cmd=cmd):
                self.rejected(self.shell(cmd))

    def test_bad_paths_rejected(self):
        h = self.head
        for args in (("show", f"{h}:/etc/passwd"),
                     ("show", f"{h}:../x"),
                     ("show", f"{h}:src/../../x"),
                     ("show", f"{h}:-x"),
                     ("show", f"{h}::(top)a.txt"),
                     ("diff", f"{self.base}...{h}", "--", "/etc/passwd"),
                     ("diff", f"{self.base}...{h}", "--", "src/../.."),
                     ("diff", f"{self.base}...{h}", "--", "-x"),
                     ("diff", f"{self.base}...{h}", "--", ":(exclude)a.txt"),
                     ("diff", f"{self.base}...{h}", "--"),
                     ("log", h, "--", "a.txt")):              # paths are for diff only
            with self.subTest(args=args):
                self.rejected(self.read(*args))

    def test_bad_revisions_rejected(self):
        h, b = self.head, self.base
        for args in (("show", "deadbeef:a.txt"),
                     ("show", "nope:a.txt"),
                     ("show", f"{h}"),                        # needs <sha>:<path>
                     ("show", f"{h}:a.txt", f"{b}:a.txt"),
                     ("diff", f"{b}..{h}"),                    # two dots
                     ("diff", h),
                     ("diff", f"{b}...{h}...{h}"),
                     ("diff", f"nope...{h}"),
                     ("diff", f"{b}..."),
                     ("diff", f"{b}...{h}", f"{b}...{h}"),
                     ("diff", "-Ux", f"{b}...{h}"),
                     ("diff", "--unified=1;touch", f"{b}...{h}"),
                     ("log", f"{b}...{h}"),
                     ("log", "nope"),
                     ("log", b, h),
                     ("log", "-n", "x"),
                     ("log", "-n"),
                     ("blame", "a.txt"),
                     ()):
            with self.subTest(args=args):
                self.rejected(self.read(*args))

    def test_env_and_repo_config_cannot_run_programs(self):
        rng = f"{self.base}...{self.head}"
        evil = {**GIT_ENV,
                "GIT_EXTERNAL_DIFF": str(self.prog),
                "GIT_PAGER": str(self.prog), "PAGER": str(self.prog),
                "GIT_CONFIG_PARAMETERS": f"'diff.external'='{self.prog}'",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pager",
                "GIT_CONFIG_VALUE_0": str(self.prog),
                "GIT_TRACE": str(self.out / "trace")}
        self.assertIn("+++ b/a.txt", self.ok("diff", rng, env=evil))
        self.ok("log", "-n1", env=evil)
        self.ok("show", f"{self.head}:a.txt", env=evil)
        # repo config and .gitattributes driving an external diff and a textconv
        git(self.repo, "config", "diff.external", str(self.prog))
        git(self.repo, "config", "diff.evil.textconv", str(self.prog))
        git(self.repo, "config", "diff.evil.command", str(self.prog))
        info = self.repo / ".git/info/attributes"
        info.write_text("* diff=evil\n")
        try:
            self.assertIn("+two", self.ok("diff", rng))
            self.assertEqual(self.ok("show", f"{self.head}:a.txt"), "two\n")
            self.ok("log", "-n1", "--stat")
        finally:
            info.unlink()
            for k in ("diff.external", "diff.evil.textconv", "diff.evil.command"):
                git(self.repo, "config", "--unset", k)

    def test_sanity_marker_program_does_run_without_guard(self):
        # Proves the marker check above can fail: plain git with the same
        # repo config does run the program.
        git(self.repo, "config", "diff.external", str(self.prog))
        try:
            git(self.repo, "diff", "--ext-diff", f"{self.base}...{self.head}")
            self.assertTrue(self.marker.exists())
        finally:
            git(self.repo, "config", "--unset", "diff.external")
            self.marker.unlink()


if __name__ == "__main__":
    unittest.main()
