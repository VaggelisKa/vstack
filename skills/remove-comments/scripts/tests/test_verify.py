"""verify: passes when only comments and whitespace changed, fails (exit 1) on
any code change, reports docstring changes separately, exits 2 on scan errors.
Its cross-checks (Python's ast, TypeScript, the C preprocessor, gofmt) fail it
on a difference; a missing tool is reported as skipped and never fails it.
TypeScript runs on TS_CHUNK files at a time, and a run that doesn't finish says so.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

from helpers import SCRIPTS, commit_all, decide, listing, new_repo, run, scratch, tmpdir

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line


def verify(test, name, before, after, *flags, env=None):
    d = tmpdir(test)
    (d / "before").write_text(before)
    (d / name).write_text(after)
    return run("verify", "--before", d / "before", "--after", d / name, *flags, env=env)


def has_c_preprocessor():
    with scratch() as d:
        return comments.c_preprocessor(str(d)) is not None


class Verify(unittest.TestCase):
    def same(self, name, before, after):
        r = verify(self, name, before, after)
        self.assertEqual(r.returncode, 0, r.stdout.decode() + r.stderr.decode())

    def changed(self, name, before, after, needle=None):
        r = verify(self, name, before, after)
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

    def test_css_whitespace_between_tokens(self):
        self.changed("a.css", ".a.b { x: y }\n", ".a .b { x: y }\n", "<whitespace>")
        self.changed("a.css", ".a .b { x: y }\n", ".a.b { x: y }\n", "<whitespace>")
        self.changed("a.scss", ".a/* c */.b { x: y }\n", ".a.b { x: y }\n", "<whitespace>")
        self.same("a.scss", ".a/* c */.b { x: y }\n", ".a .b { x: y }\n")
        self.changed("a.less", ".a/* c */.b { x: y }\n", ".a .b { x: y }\n", "'/* c */'")
        self.changed("a.less", ".a-b /**/.c { x: y }\n", ".a-b.c { x: y }\n")
        self.changed("a.css", "a { margin: 1/**/.5px }\n", "a { margin: 1.5px }\n")
        self.changed("a.css", "a { margin: 1/**/.5px }\n", "a { margin: 1 .5px }\n")
        self.changed("a.css", "a/**/b { x: y }\n", "ab { x: y }\n")
        self.same("a.css", ".a/* c */.b { x: y }\n#id/**/.c {}\n", ".a.b { x: y }\n#id.c {}\n")
        self.same("a.css", "a{color:red}\n", "a {\n  color: red\n}\n")

    def test_code_moved_out_of_a_directive(self):
        before = "#define SUM(a,b) ((a) /* first\n second */ + (b))\nint x = SUM(1,2);\n"
        r = verify(self, "a.c", before, "#define SUM(a,b) ((a)\n + (b))\nint x = SUM(1,2);\n", "--no-cross-check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(b"directive", r.stdout)
        self.same("a.c", before, "#define SUM(a,b) ((a) + (b))\nint x = SUM(1,2);\n")
        self.changed("a.c", "#define A 1 \\\n  + 2\n", "#define A 1\n  + 2\n")

    def test_docstrings_reported_separately(self):
        before = 'def f():\n    """Return one.\n\n    Long text.\n    """\n    return 1\n'
        after = 'def f():\n    """Return one."""\n    return 1\n'
        r = verify(self, "a.py", before, after)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"1 docstring change", r.stdout)
        self.assertIn(b"docstring of f changed", r.stdout)
        self.assertEqual(verify(self, "a.py", before, after, "--strict").returncode, 1)
        r = verify(self, "a.py", before, "def f():\n    return 1\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"docstring of f removed", r.stdout)

    def test_python_ast_backs_up_tokens(self):
        self.changed("a.py", "x = 1 if a else 2\n", "x = 1 if not a else 2\n")

    def test_scan_error_is_exit_2(self):
        r = verify(self, "a.ts", "const s = 'a';\n", "const s = 'a;\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"error", r.stdout)

    def test_after_apply_with_snapshot_and_base(self):
        repo = new_repo(self)
        (repo / "a.ts").write_text("export const a = 1;\n")
        commit_all(repo, "base")
        (repo / "a.ts").write_text("export const a = 1;\n// Added b\nexport const b = 2; // two\n")
        r, doc, out = listing(self, repo)
        self.assertEqual(run("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}),
                             "--list", out, cwd=repo).returncode, 0)
        r = run("verify", "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout)
        # Against HEAD the pre-existing (uncommitted) code change shows up, which is why
        # verify defaults to the snapshot taken by list.
        self.assertEqual(run("verify", "--base", "HEAD", cwd=repo).returncode, 1)
        (repo / "a.ts").write_text("export const a = 1;\nexport const b = 3;\n")
        self.assertEqual(run("verify", "--list", out, cwd=repo).returncode, 1)


class CrossCheck(unittest.TestCase):
    def line(self, r, tool, status):
        out = r.stdout.decode()
        self.assertIn(f"cross-check {tool}: {status}", out, out)

    def test_python_ast(self):
        r = verify(self, "a.py", "x = 1  # c\n", "x = 1\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.line(r, "python-ast", "ok")

    @unittest.skipUnless(has_c_preprocessor(), "no working gcc, cc or clang")
    def test_c_preprocessor(self):
        before = "#define SUM(a,b) ((a) /* first\n second */ + (b))\nint x = SUM(1,2); // c\n"
        r = verify(self, "a.c", before, "#define SUM(a,b) ((a) + (b))\nint x = SUM(1,2);\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout.decode(), r"cross-check \S+: ok")
        r = verify(self, "a.c", before, "#define SUM(a,b) ((a)\n + (b))\nint x = SUM(1,2);\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout.decode(), r"cross-check \S+: differs \(preprocessed output differs\)")
        r = verify(self, "a.c", '#include "missing.h"\nint x; // c\n', '#include "missing.h"\nint x;\n')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout.decode(), r"cross-check \S+: skipped \(the before file doesn't preprocess")

    @unittest.skipUnless(os.environ.get("RC_TS_ROOT") and shutil.which("node"),
                         "set RC_TS_ROOT to a directory whose node_modules has typescript")
    def test_typescript_emit(self):
        r = verify(self, "a.tsx", "const a = <b>{/* c */}x</b>; // d\n", "const a = <b>x</b>;\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.line(r, "typescript", "ok")
        r = verify(self, "a.ts", "foo(); // note\u2028bar();\n", "foo();\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.line(r, "typescript", "differs (emitted JavaScript differs)")
        r = verify(self, "a.ts", "x = 1; // c\n", "x = = 1;\n")
        self.line(r, "typescript", "differs (the edited file has syntax errors)")

    @unittest.skipUnless(shutil.which("node"), "node isn't on PATH")
    def test_node_check_without_typescript(self):
        r = verify(self, "a.js", "f(a); // c\n", "f(a);\n", env={"RC_TS_ROOT": str(tmpdir(self))})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.line(r, "node --check", "weak (syntax only)")
        self.assertIn(b"0 cross-checked ok, 1 syntax-only (weak)", r.stdout)

    @unittest.skipUnless(shutil.which("gofmt"), "gofmt isn't on PATH")
    def test_gofmt_syntax(self):
        r = verify(self, "a.go", "package p\n\nvar x = 1 // c\n", "package p\n\nvar x = 1\n")
        self.line(r, "gofmt", "weak (syntax only)")
        self.assertIn(b"1 edited file(s): 0 cross-checked ok, 1 syntax-only (weak), 0 no independent check", r.stdout)

    def test_missing_tools_never_fail(self):
        empty = tmpdir(self)
        for name, before, after in (("a.c", "int x; // c\n", "int x;\n"), ("a.ts", "f(); // c\n", "f();\n"),
                                    ("a.go", "package p // c\n", "package p\n"), ("a.rs", "fn f() {} // c\n", "fn f() {}\n")):
            with self.subTest(name=name):
                r = verify(self, name, before, after, env={"PATH": str(empty)})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(b": skipped (", r.stdout)

    def test_summary_counts_ok_weak_and_unchecked_apart(self):
        repo = new_repo(self)
        files = {"a.py": "x = 1\n", "b.rs": "fn f() {}\n", "c.js": "f(a);\n", "d.py": "y = 2\n"}
        for name, text in files.items():
            (repo / name).write_text(text)
        commit_all(repo, "base")
        for name, text in (("a.py", "x = 1  # c\n"), ("b.rs", "fn f() {} // c\n"), ("c.js", "f(a); // c\n")):
            (repo / name).write_text(text)
        r, doc, out = listing(self, repo, "--all")
        self.assertEqual(run("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}),
                             "--list", out, cwd=repo).returncode, 0)
        env = {"RC_TS_ROOT": str(tmpdir(self))}
        r = run("verify", "--list", out, cwd=repo, env=env)
        self.assertEqual(r.returncode, 0, r.stdout)
        weak = 1 if shutil.which("node") else 0
        self.assertIn(f"3 edited file(s): 1 cross-checked ok, {weak} syntax-only (weak), {2 - weak} no independent "
                      "check, 0 cross-check difference(s)".encode(), r.stdout)
        self.assertIn(b"cross-check rust: skipped (no independent tool)", r.stdout)
        r = run("verify", "--list", out, "--no-cross-check", cwd=repo, env=env)
        self.assertIn(b"3 edited file(s): 0 cross-checked ok, 0 syntax-only (weak), 3 no independent check", r.stdout)

    def ts_files(self, n):
        return [(f"f{k}.ts", "ts", f"f({k}); // c\n", f"f({k});\n", None) for k in range(n)]

    def fake_runs(self, results):
        """cross_checks with node and typescript found, and each TypeScript run's
        result taken from results in turn: None (didn't finish), a negative exit
        code (killed), or "ok" (every file emits the same). Returns its output and
        the number of files each run got."""
        sizes, results = [], iter(results)

        def run_tool(argv, scratch, env, data=b""):
            sizes.append(len(json.loads(data)))
            r = next(results)
            if r == "ok":
                return 0, json.dumps([{"same": True, "before": 0, "after": 0}] * sizes[-1]).encode(), b""
            return r

        with mock.patch.object(comments, "find_tool", lambda name, env, roots=(): "/node" if name == "node" else None), \
                mock.patch.object(comments, "find_typescript", lambda *a: ("/typescript.js", None)), \
                mock.patch.object(comments, "run_tool", run_tool), mock.patch.object(comments, "TS_CHUNK", 2):
            return comments.cross_checks(self.ts_files(5), [str(tmpdir(self))]), sizes

    def test_typescript_runs_in_chunks(self):
        out, sizes = self.fake_runs(["ok", "ok", "ok"])
        self.assertEqual(sizes, [2, 2, 1])
        self.assertEqual(out, [(f"f{k}.ts", "typescript", "ok", None) for k in range(5)])

    def test_a_typescript_run_that_does_not_finish_says_so(self):
        out, sizes = self.fake_runs(["ok", None, (-9, b"", b"")])
        self.assertEqual(sizes, [2, 2, 1])
        self.assertEqual([r[2:] for r in out], [("ok", None)] * 2 + [("skipped", comments.TS_FAILED)] * 3)
        self.assertEqual(comments.TS_FAILED, "typescript check didn't finish: timed out, was killed, or wrote too much")
        out, _ = self.fake_runs([(1, b"", b"boom")] * 3)
        self.assertEqual({r[2:] for r in out}, {("skipped", "the TypeScript run failed")})

    def test_finding_typescript_that_does_not_finish_says_so(self):
        for result, why in ((None, comments.TS_FAILED), ((-9, b"", b""), comments.TS_FAILED),
                            ((3, b"", b""), "RC_TS_ROOT has no typescript")):
            with self.subTest(result=result), mock.patch.object(comments, "run_tool", lambda *a, **k: result), \
                    mock.patch.dict(os.environ, RC_TS_ROOT=str(tmpdir(self))):
                self.assertEqual(comments.find_typescript("/node", [str(tmpdir(self))], str(tmpdir(self)), {}), (None, why))

    @unittest.skipUnless(os.environ.get("RC_TS_ROOT") and shutil.which("node"),
                         "set RC_TS_ROOT to a directory whose node_modules has typescript")
    def test_typescript_chunks_with_real_typescript(self):
        with mock.patch.object(comments, "TS_CHUNK", 2):
            out = comments.cross_checks(self.ts_files(5), [str(tmpdir(self))])
        self.assertEqual(out, [(f"f{k}.ts", "typescript", "ok", None) for k in range(5)])

    def test_no_cross_check(self):
        r = verify(self, "a.py", "x = 1  # c\n", "x = 1\n", "--no-cross-check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"cross-check: skipped (--no-cross-check)", r.stdout)
        self.assertNotIn(b"python-ast", r.stdout)


if __name__ == "__main__":
    unittest.main()
