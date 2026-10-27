"""apply runs the independent cross-checks on each edited file before writing any,
so an edit the scanner was fooled into passing is refused and every file is left as
it was. The scanner is fooled here by patching edit and compare in-process.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import contextlib
import io
import shutil
import sys
import unittest
from unittest import mock

from helpers import SCRIPTS, commit_all, decide, listing, new_repo

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


if __name__ == "__main__":
    unittest.main()
