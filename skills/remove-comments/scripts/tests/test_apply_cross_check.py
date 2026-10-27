"""apply runs the independent cross-checks on each edited file before writing any,
so an edit the scanner was fooled into passing is refused and every file is left as
it was. The scanner is fooled here by patching edit and compare in-process. Right
before writing, apply reads each file again and skips one that changed since it was
planned, and its summary counts the edited files by cross-check status.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import contextlib
import io
import shutil
import sys
import unittest
from unittest import mock

from helpers import SCRIPTS, commit_all, decide, listing, new_repo, run

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line


class FooledScanner(unittest.TestCase):
    def apply_fooled(self, files, bad):
        """List files, then apply 'delete all' with edit() returning bad[name] for those
        files and compare() saying ok; returns (exit code, stderr, files' text after)."""
        repo = new_repo()
        for name, text in files.items():
            (repo / name).write_text(text)
        commit_all(repo, "base")
        r, doc, out = listing(repo, "--paths", *files)
        self.assertEqual(r.returncode, 0, r.stderr)
        dec = decide(out, {c: {"action": "delete"} for c in doc["pending"]})
        real_edit = comments.edit

        def fooled_edit(text, lang, actions):
            for name, src in files.items():
                if text == src and name in bad:
                    return bad[name]
            return real_edit(text, lang, actions)

        err = io.StringIO()
        with mock.patch.object(comments, "edit", fooled_edit), \
                mock.patch.object(comments, "compare", lambda *a, **k: {"ok": True, "diff": None, "docstrings": []}), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as exit_:
            comments.main(["apply", str(dec), "--list", str(out)])
        return exit_.exception.code, err.getvalue(), {n: (repo / n).read_text() for n in files}

    def test_python_ast_difference_stops_apply(self):
        files = {"a.py": "x = 1  # one\ny = 2\n", "b.py": "z = 3  # three\n"}
        code, err, after = self.apply_fooled(files, {"a.py": "x = 1\n"})
        self.assertEqual(code, 2, err)
        self.assertIn('"a.py": cross-check python-ast differs (AST differs); file left untouched', err)
        self.assertIn("no file was changed", err)
        self.assertEqual(after, files)

    @unittest.skipUnless(shutil.which("node"), "node isn't on PATH")
    def test_js_syntax_error_stops_apply(self):
        files = {"a.js": "f(a); // one\n"}
        code, err, after = self.apply_fooled(files, {"a.js": "f(a;\n"})
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'"a\.js": cross-check (node --check|typescript) differs \(.*\); file left untouched')
        self.assertEqual(after, files)

    def test_clean_edit_still_applies(self):
        files = {"a.py": "x = 1  # one\n"}
        code, err, after = self.apply_fooled(files, {})
        self.assertEqual(code, 0, err)
        self.assertEqual(after, {"a.py": "x = 1\n"})


class AtWriteTime(unittest.TestCase):
    def listed(self, files):
        repo = new_repo()
        for name, text in files.items():
            (repo / name).write_text(text)
        commit_all(repo, "base")
        r, doc, out = listing(repo, "--paths", *files)
        self.assertEqual(r.returncode, 0, r.stderr)
        return repo, decide(out, {c: {"action": "delete"} for c in doc["pending"]}), out

    def test_file_changed_after_planning_is_not_written(self):
        repo, dec, out = self.listed({"a.py": "x = 1  # one\n", "b.py": "y = 2  # two\n"})
        real = comments.cross_checks

        def meanwhile(files, roots):
            (repo / "a.py").write_text("x = 1  # edited by someone else\n")
            return real(files, roots)

        stdout = io.StringIO()
        with mock.patch.object(comments, "cross_checks", meanwhile), contextlib.redirect_stdout(stdout), \
                self.assertRaises(SystemExit) as exit_:
            comments.main(["apply", str(dec), "--list", str(out)])
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn('skipped "a.py": file changed while apply was checking it; not written', stdout.getvalue())
        self.assertIn("edited 1 file(s): 1 deleted", stdout.getvalue())
        self.assertEqual((repo / "a.py").read_text(), "x = 1  # edited by someone else\n")
        self.assertEqual((repo / "b.py").read_text(), "y = 2\n")

    def test_summary_counts_cross_checks(self):
        repo, dec, out = self.listed({"a.py": "x = 1  # one\n", "b.rs": "fn f() {} // two\n"})
        r = run("apply", dec, "--list", out, "--dry-run", cwd=repo)
        self.assertIn(b"would edit 2 file(s)", r.stdout)
        self.assertIn(b"2 edited file(s): 1 cross-checked ok, 0 syntax-only (weak), 1 no independent check", r.stdout)
        r = run("apply", dec, "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"2 edited file(s): 1 cross-checked ok, 0 syntax-only (weak), 1 no independent check", r.stdout)


if __name__ == "__main__":
    unittest.main()
