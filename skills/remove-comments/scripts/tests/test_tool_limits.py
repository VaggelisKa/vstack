"""Checkers run with capped memory, CPU time and output, in a session of their own, and
a timeout kills everything they started: a C file that includes /dev/zero ends the gcc
cross-check in about a second as skipped, and a tool's background child doesn't
outlive it. The caps make a hostile file cheap to check, not safe to run: they are no
sandbox. POSIX only.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import os
import re
import sys
import time
import unittest
from unittest import mock

from helpers import SCRIPTS, run, scratch, tmpdir

try:
    import resource
except ImportError:
    resource = None

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line


def has_c_preprocessor():
    with scratch() as d:
        return comments.c_preprocessor(str(d)) is not None


def alive(pid):
    """Whether pid is a process that hasn't exited (a zombie has)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


@unittest.skipIf(resource is None, "resource limits are POSIX only")
class ToolLimits(unittest.TestCase):
    def setUp(self):
        self.scratch = str(tmpdir(self))
        self.env = comments.tool_env()

    @unittest.skipUnless(has_c_preprocessor(), "no working gcc, cc or clang")
    def test_include_of_dev_zero_ends_quickly_as_skipped(self):
        d = tmpdir(self)
        (d / "before.c").write_text('#include "/dev/zero"\nint x; // c\n')
        (d / "a.c").write_text('#include "/dev/zero"\nint x;\n')
        start = time.monotonic()
        r = run("verify", "--before", d / "before.c", "--after", d / "a.c")
        took = time.monotonic() - start
        out = r.stdout.decode()
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        self.assertLess(took, comments.TOOL_TIMEOUT / 2, out)
        self.assertRegex(out, r"cross-check \S+: skipped \(the before file doesn't preprocess")
        self.assertNotRegex(out, r"cross-check \S+: ok")
        if sys.platform.startswith("linux"):
            self.assertLess(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024, comments.TOOL_MEMORY)

    def test_timeout_kills_the_whole_process_group(self):
        d = tmpdir(self)
        tool, pidfile = d / "tool", d / "pid"
        tool.write_text('#!/bin/sh\nsleep 300 &\necho $! > "$1"\nexec sleep 300\n')
        tool.chmod(0o755)
        start = time.monotonic()
        with mock.patch.object(comments, "TOOL_TIMEOUT", 1):
            self.assertIsNone(comments.run_tool([str(tool), str(pidfile)], self.scratch, self.env))
        self.assertLess(time.monotonic() - start, 10)
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid), f"background child {pid} survived the timeout")

    def test_memory_cpu_and_output_are_capped(self):
        r = comments.run_tool([sys.executable, "-I", "-c", "b = bytearray(%d)" % (comments.TOOL_MEMORY + (1 << 30))],
                              self.scratch, self.env)
        self.assertIsNotNone(r)
        self.assertNotEqual(r[0], 0)
        self.assertIn(b"MemoryError", r[2])
        cpu = "import resource; print(resource.getrlimit(resource.RLIMIT_CPU))"
        r = comments.run_tool([sys.executable, "-I", "-c", cpu], self.scratch, self.env)
        self.assertEqual(r[0], 0, r)
        self.assertLessEqual(int(re.match(rb"\((\d+)", r[1]).group(1)), comments.TOOL_TIMEOUT)
        with mock.patch.object(comments, "TOOL_OUTPUT", 1000):
            self.assertIsNone(comments.run_tool([sys.executable, "-I", "-c", "print('x' * 5000)"], self.scratch,
                                                self.env))
            self.assertEqual(comments.run_tool([sys.executable, "-I", "-c", "print('x' * 10)"], self.scratch, self.env),
                             (0, b"x" * 10 + b"\n", b""))

    def test_a_cap_the_system_refuses_is_left_out(self):
        real = resource.setrlimit

        def refuse_as(res, limits):
            if res == resource.RLIMIT_AS:
                raise ValueError("not allowed")
            real(res, limits)

        with mock.patch.object(resource, "setrlimit", refuse_as):
            r = comments.run_tool([sys.executable, "-I", "-c", "print(1)"], self.scratch, self.env)
        self.assertEqual(r, (0, b"1\n", b""))

    def test_stdin_reaches_the_tool(self):
        r = comments.run_tool([sys.executable, "-I", "-c", "import sys; sys.stdout.write(sys.stdin.read()[::-1])"],
                              self.scratch, self.env, b"abc")
        self.assertEqual(r, (0, b"cba", b""))


if __name__ == "__main__":
    unittest.main()
