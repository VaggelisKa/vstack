"""Tests for session.py: the session folder lives in the repo (or cwd), ignores
itself, and clean removes only a folder that new could have made.

Run from the repo root: python3 -m unittest discover -s skills/review/scripts/tests
"""
import os
import pathlib
import re
import stat
import subprocess
import sys
import unittest

from helpers import GIT_ENV, SCRIPTS, commit_all, git, new_repo, tmpdir

NAME = re.compile(r"\.review-session-[0-9a-f]{16}")


def session(*args, cwd, path=None):
    temp_root = next(p for p in pathlib.Path(os.path.realpath(cwd)).parents
                     if p.name.startswith("review-test-"))
    env = {**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(temp_root)}
    if path is not None:
        env["PATH"] = str(path)
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "session.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=env)


class Session(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo(self)
        (self.repo / "src").mkdir()
        (self.repo / "src/app.py").write_text("x = 1\n")
        commit_all(self.repo, "base")
        self.top = os.path.realpath(self.repo)

    def state(self):
        exclude = self.repo / ".git/info/exclude"
        return (git(self.repo, "status", "--porcelain", "-uall"),
                git(self.repo, "ls-files", "-s"),
                exclude.read_bytes() if exclude.exists() else None)

    def new(self, cwd=None):
        r = session("new", cwd=cwd or self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_new_is_inside_the_repo_and_ignores_itself(self):
        before = self.state()
        self.assertEqual(before[0], b"")
        path = self.new(cwd=self.repo / "src")
        self.assertEqual(os.path.dirname(path), self.top)
        self.assertTrue(path.startswith(self.top + os.sep))
        self.assertTrue(NAME.fullmatch(os.path.basename(path)))
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o700)
        with open(os.path.join(path, ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")
        with open(os.path.join(path, "R1.prompt"), "w") as f:
            f.write("prompt\n")
        self.assertEqual(self.state(), before)
        r = session("clean", path, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.state(), before)

    def test_two_sessions_differ(self):
        self.assertNotEqual(self.new(), self.new())

    def test_outside_a_repo_uses_cwd(self):
        cwd = tmpdir(self) / "plain"
        cwd.mkdir()
        path = self.new(cwd=cwd)
        self.assertEqual(os.path.dirname(path), os.path.realpath(cwd))
        self.assertEqual(session("clean", path, cwd=cwd).returncode, 0)
        self.assertFalse(os.path.exists(path))

    def fake_git(self, script):
        bin_dir = tmpdir(self) / "bin"
        bin_dir.mkdir()
        if script is not None:
            (bin_dir / "git").write_text("#!/bin/sh\n" + script + "\n")
            (bin_dir / "git").chmod(0o755)
        return bin_dir

    def test_git_failure_is_an_error_not_cwd(self):
        for script in (None, "echo 'fatal: boom' >&2; exit 1",
                       "echo \"fatal: detected dubious ownership in repository at '/x'\" >&2; exit 128",
                       "exit 0"):
            with self.subTest(script=script):
                r = session("new", cwd=self.repo / "src", path=self.fake_git(script))
                self.assertEqual((r.returncode, r.stdout), (2, ""))
                self.assertIn("session: can't determine the repo root", r.stderr)
                self.assertEqual(os.listdir(self.repo / "src"), ["app.py"])
        path = self.new()
        r = session("clean", path, cwd=self.repo, path=self.fake_git("exit 1"))
        self.assertEqual(r.returncode, 2)
        self.assertTrue(os.path.isdir(path))

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_clean_removes_read_only_and_unreadable_dirs(self):
        path = self.new()
        for name, mode in (("ro", 0o500), ("none", 0o000)):
            os.makedirs(os.path.join(path, name, "deep"))
            with open(os.path.join(path, name, "deep", "f.png"), "w") as f:
                f.write("x")
            with open(os.path.join(path, name, "f.log"), "w") as f:
                f.write("x")
            os.chmod(os.path.join(path, name, "deep"), 0o500)
            os.chmod(os.path.join(path, name), mode)
        r = session("clean", path, cwd=self.repo)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertFalse(os.path.lexists(path))

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_clean_warns_and_fails_on_what_it_cant_remove(self):
        path = self.new()
        with open(os.path.join(path, "R1.log"), "w") as f:
            f.write("x")
        os.chmod(self.repo, 0o555)
        try:
            r = session("clean", path, cwd=self.repo)
            self.assertEqual(stat.S_IMODE(os.stat(self.repo).st_mode), 0o555)
        finally:
            os.chmod(self.repo, 0o755)
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"session: warning: couldn't remove {path}\n", r.stderr)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.listdir(path), [])

    def assertRefused(self, arg, survivor):
        r = session("clean", arg, cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing", r.stderr)
        self.assertTrue(os.path.lexists(survivor))

    def test_clean_refuses_a_symlink(self):
        target = tmpdir(self) / "victim"
        target.mkdir()
        (target / "keep.txt").write_text("keep\n")
        link = self.repo / ".review-session-0123456789abcdef"
        link.symlink_to(target, target_is_directory=True)
        self.assertRefused(link, link)
        self.assertTrue((target / "keep.txt").exists())

    def test_clean_refuses_a_wrong_name(self):
        for name in (".review-session-xyz", ".review-session-0123", "review-session-0123456789abcdef",
                     ".review-session-0123456789ABCDEF", "src"):
            d = self.repo / name
            d.mkdir(exist_ok=True)
            self.assertRefused(d, d)

    def test_clean_refuses_a_file(self):
        f = self.repo / ".review-session-0123456789abcdef"
        f.write_text("x\n")
        self.assertRefused(f, f)

    def test_clean_refuses_outside_the_toplevel(self):
        elsewhere = tmpdir(self) / ".review-session-0123456789abcdef"
        elsewhere.mkdir()
        self.assertRefused(elsewhere, elsewhere)
        nested = self.repo / "src/.review-session-0123456789abcdef"
        nested.mkdir()
        self.assertRefused(nested, nested)

    def test_clean_refuses_dotdot(self):
        path = self.new()
        self.assertRefused(str(self.repo / "src/..") + "/" + os.path.basename(path), path)

    def test_usage(self):
        for args in ((), ("clean",), ("new", "x"), ("rm", "x")):
            r = session(*args, cwd=self.repo)
            self.assertEqual(r.returncode, 2)
            self.assertIn("usage", r.stderr)
        self.assertEqual(sorted(os.listdir(self.repo)), [".git", "src"])


if __name__ == "__main__":
    unittest.main()
