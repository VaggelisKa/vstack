import os
import stat
import unittest

from helpers import rmtree, tmpdir


class RmtreeTest(unittest.TestCase):
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_removes_unreadable_and_read_only_dirs(self):
        d = tmpdir(self)
        (d / "locked" / "inner").mkdir(parents=True)
        (d / "locked" / "inner" / "f").write_text("x")
        (d / "ro").mkdir()
        (d / "ro" / "g").write_text("y")
        os.chmod(d / "locked" / "inner", 0)
        os.chmod(d / "locked", 0)
        os.chmod(d / "ro", stat.S_IRUSR | stat.S_IXUSR)
        rmtree(d)
        self.assertFalse(os.path.lexists(d))

    def test_does_not_follow_symlinks(self):
        outside = tmpdir(self)
        (outside / "keep").write_text("k")
        os.chmod(outside / "keep", stat.S_IRUSR)
        d = tmpdir(self)
        os.symlink(outside, d / "link")
        rmtree(d)
        self.assertFalse(os.path.lexists(d))
        self.assertEqual(stat.S_IMODE(os.stat(outside / "keep").st_mode), stat.S_IRUSR)


if __name__ == "__main__":
    unittest.main()
