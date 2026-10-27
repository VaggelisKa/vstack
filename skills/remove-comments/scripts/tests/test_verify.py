"""verify: passes when only comments and whitespace changed, fails (exit 1) on
any code change, reports docstring changes separately, exits 2 on scan errors.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import unittest

from helpers import commit_all, decide, listing, new_repo, run, tmpdir


def verify(name, before, after, *flags):
    d = tmpdir()
    (d / "before").write_text(before)
    (d / name).write_text(after)
    return run("verify", "--before", d / "before", "--after", d / name, *flags)


class Verify(unittest.TestCase):
    def same(self, name, before, after):
        r = verify(name, before, after)
        self.assertEqual(r.returncode, 0, r.stdout.decode() + r.stderr.decode())

    def changed(self, name, before, after, needle=None):
        r = verify(name, before, after)
        self.assertEqual(r.returncode, 1, r.stdout.decode() + r.stderr.decode())
        self.assertIn(b"CODE CHANGED", r.stdout)
        if needle:
            self.assertIn(needle.encode(), r.stdout)

    def test_comment_and_whitespace_changes_pass(self):
        self.same("a.ts", "const a = 1; // x\n\n\n/* y */\nfunction f(){return a}\n",
                  "const a = 1;\n\nfunction f() {\n  return a\n}\n")
        self.same("a.py", "x = 1  # a\n\n\n# b\ny = [1,\n     2]\n", "x = 1\ny = [1, 2]\n")
        self.same("a.yaml", "a: 1  # x\n# y\nb:\n  c: 2\n", "a: 1\nb:\n  c: 2\n")
        self.same("a.sh", "echo a  # x\n# y\nfoo   bar\n", "echo a\nfoo bar\n")

    def test_code_token_changed(self):
        self.changed("a.ts", "const a = 1; // x\n", "const a = 2;\n", "expected '1'")
        self.changed("a.go", "x := f(a, b) // c\n", "x := f(b, a)\n")
        self.changed("a.js", "x = a - -b /* c */\n", "x = a--b\n")

    def test_string_content_changed(self):
        self.changed("a.ts", 'const s = "a b"; // x\n', 'const s = "a  b";\n')
        self.changed("a.py", 's = """\nline\n"""\n', 's = """\nline \n"""\n')

    def test_hash_inside_string_removed(self):
        self.changed("a.py", 's = "a # b"\n', 's = "a "\n')
        self.changed("a.sh", 'echo "a # b"\n', 'echo "a "\n')
        self.changed("a.yaml", 'k: "a # b"\n', 'k: "a "\n')
        self.changed("a.toml", 'k = "a # b"\n', 'k = "a "\n')

    def test_code_uncommented(self):
        self.changed("a.ts", "// legacy();\nrun();\n", "legacy();\nrun();\n")
        self.changed("a.py", "# legacy()\nrun()\n", "legacy()\nrun()\n")
        self.changed("a.css", "a { /* color: red; */ }\n", "a { color: red; }\n")

    def test_protected_comment_removed_is_a_change(self):
        self.changed("a.ts", "// @ts-expect-error untyped\nf(x);\n", "f(x);\n")
        self.changed("a.go", "//go:embed x\nvar f string\n", "var f string\n")
        self.changed("a.py", "x = f()  # type: ignore[call-arg]\n", "x = f()\n")

    def test_whitespace_that_matters(self):
        self.changed("a.py", "if a:\n    b()\n    c()\n", "if a:\n    b()\nc()\n")
        self.changed("a.yaml", "a:\n  b: 1\n", "a:\nb: 1\n")
        self.changed("a.sh", "run a \\\n\nrun b\n", "run a \\\nrun b\n")
        self.changed("a.html", "<p>a<!-- x -->b</p>\n", "<p>a b</p>\n")

    def test_docstrings_reported_separately(self):
        before = 'def f():\n    """Return one.\n\n    Long text.\n    """\n    return 1\n'
        after = 'def f():\n    """Return one."""\n    return 1\n'
        r = verify("a.py", before, after)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"1 docstring change", r.stdout)
        self.assertIn(b"docstring of f changed", r.stdout)
        self.assertEqual(verify("a.py", before, after, "--strict").returncode, 1)
        r = verify("a.py", before, "def f():\n    return 1\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"docstring of f removed", r.stdout)

    def test_python_ast_backs_up_tokens(self):
        self.changed("a.py", "x = 1 if a else 2\n", "x = 1 if not a else 2\n")

    def test_scan_error_is_exit_2(self):
        r = verify("a.ts", "const s = 'a';\n", "const s = 'a;\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"error", r.stdout)

    def test_after_apply_with_snapshot_and_base(self):
        repo = new_repo()
        (repo / "a.ts").write_text("export const a = 1;\n")
        commit_all(repo, "base")
        (repo / "a.ts").write_text("export const a = 1;\n// Added b\nexport const b = 2; // two\n")
        r, doc, out = listing(repo)
        self.assertEqual(run("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}),
                             "--list", out, cwd=repo).returncode, 0)
        r = run("verify", "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout)
        # Against HEAD the pre-existing (uncommitted) code change shows up, which is why
        # verify defaults to the snapshot taken by list.
        self.assertEqual(run("verify", "--base", "HEAD", cwd=repo).returncode, 1)
        (repo / "a.ts").write_text("export const a = 1;\nexport const b = 3;\n")
        self.assertEqual(run("verify", "--list", out, cwd=repo).returncode, 1)


if __name__ == "__main__":
    unittest.main()
