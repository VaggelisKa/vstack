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


def session(*args, cwd):
    temp_root = next(p for p in pathlib.Path(os.path.realpath(cwd)).parents
                     if p.name.startswith("review-test-"))
    env = {**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(temp_root)}
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "session.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=env)


class Session(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
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
        cwd = tmpdir() / "plain"
        cwd.mkdir()
        path = self.new(cwd=cwd)
        self.assertEqual(os.path.dirname(path), os.path.realpath(cwd))
        self.assertEqual(session("clean", path, cwd=cwd).returncode, 0)
        self.assertFalse(os.path.exists(path))

    def assertRefused(self, arg, survivor):
        r = session("clean", arg, cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing", r.stderr)
        self.assertTrue(os.path.lexists(survivor))

    def test_clean_refuses_a_symlink(self):
        target = tmpdir() / "victim"
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
        elsewhere = tmpdir() / ".review-session-0123456789abcdef"
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
