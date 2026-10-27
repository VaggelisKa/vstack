"""precheck: the clean-tree gate before checking out a PR. Anything git status
reports (untracked, staged, modified) and an unfinished merge, rebase or
cherry-pick fail with exit 2; ignored files don't count.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import unittest

from helpers import commit_all, git, new_repo, run, tmpdir


class Precheck(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        (self.repo / ".gitignore").write_text("*.log\nbuild/\n")
        (self.repo / "a.py").write_text("x = 1\n")
        commit_all(self.repo, "base")

    def check(self, *args):
        return run("precheck", *args, cwd=self.repo)

    def dirty(self, needle=b"uncommitted changes"):
        r = self.check("--pr", "7")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(needle, r.stderr)
        return r

    def test_clean_tree(self):
        r = self.check("--pr", "7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"ok to check out PR 7", r.stdout)

    def test_ignored_files_only(self):
        (self.repo / "debug.log").write_text("x\n")
        (self.repo / "build").mkdir()
        (self.repo / "build" / "out.o").write_text("x\n")
        self.assertEqual(self.check().returncode, 0)

    def test_untracked_file(self):
        (self.repo / "new.py").write_text("y = 2\n")
        self.assertIn(b'"new.py"', self.dirty().stderr)

    def test_untracked_file_in_a_new_directory(self):
        (self.repo / "pkg" / "sub").mkdir(parents=True)
        (self.repo / "pkg" / "sub" / "m.py").write_text("y = 2\n")
        self.assertIn(b'"pkg/sub/m.py"', self.dirty().stderr)

    def test_staged_change(self):
        (self.repo / "a.py").write_text("x = 2\n")
        git(self.repo, "add", "a.py")
        self.dirty()

    def test_modified_tracked_file(self):
        (self.repo / "a.py").write_text("x = 3\n")
        self.dirty()

    def test_hostile_file_name_is_escaped(self):
        (self.repo / "x\n\x1b[2Jy.py").write_text("")
        r = self.dirty()
        self.assertIn(b'"x\\n\\u001b[2Jy.py"', r.stderr)
        self.assertNotIn(b"\x1b", r.stderr)

    def test_merge_in_progress(self):
        head = git(self.repo, "rev-parse", "HEAD").decode().strip()
        (self.repo / ".git" / "MERGE_HEAD").write_text(head + "\n")
        self.dirty(b"a merge is in progress")

    def test_conflicted_merge(self):
        git(self.repo, "checkout", "-qb", "other")
        (self.repo / "a.py").write_text("x = 'other'\n")
        commit_all(self.repo, "other")
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "a.py").write_text("x = 'main'\n")
        commit_all(self.repo, "main")
        self.assertNotEqual(git(self.repo, "merge", "other", check=False).returncode, 0)
        self.dirty(b"a merge is in progress")

    def test_rebase_and_cherry_pick_in_progress(self):
        for marker, needle in (("rebase-merge", b"a rebase"), ("CHERRY_PICK_HEAD", b"a cherry-pick")):
            with self.subTest(marker=marker):
                path = self.repo / ".git" / marker
                path.mkdir() if marker == "rebase-merge" else path.write_text("0" * 40 + "\n")
                self.dirty(needle)
                path.rmdir() if marker == "rebase-merge" else path.unlink()

    def test_outside_a_repository(self):
        r = run("precheck", cwd=tmpdir())
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"not inside a git repository", r.stderr)
        r = run("precheck", "--repo", self.repo, cwd=tmpdir())
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
