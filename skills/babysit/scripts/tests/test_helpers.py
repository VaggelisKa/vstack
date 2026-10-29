import os
import pathlib
import signal
import stat
import subprocess
import sys
import textwrap
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
        os.chmod(outside, 0o750)
        d = tmpdir(self)
        os.symlink(outside, d / "link")
        rmtree(d)
        self.assertFalse(os.path.lexists(d))
        self.assertEqual(stat.S_IMODE(os.stat(outside / "keep").st_mode), stat.S_IRUSR)
        self.assertEqual(stat.S_IMODE(os.stat(outside).st_mode), 0o750)

    def test_sigterm_removes_temp_dirs(self):
        child = textwrap.dedent("""
            import os, sys, time, unittest
            sys.path.insert(0, %r)
            import helpers
            class T(unittest.TestCase):
                def test(self):
                    d = helpers.tmpdir(self)
                    (d / "locked").mkdir()
                    os.chmod(d / "locked", 0)
                    print(d, flush=True)
                    time.sleep(60)
            unittest.main()
        """) % str(pathlib.Path(__file__).resolve().parent)
        p = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            d = p.stdout.readline().strip()
            self.assertTrue(d and os.path.isdir(d))
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(30), -signal.SIGTERM)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.assertFalse(os.path.lexists(d))


if __name__ == "__main__":
    unittest.main()
