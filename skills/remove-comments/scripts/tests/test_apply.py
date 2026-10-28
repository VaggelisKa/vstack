"""apply: deletes and rewrites only inside comments, collapses the blank lines
it leaves, preserves encoding details, and refuses anything it can't do safely.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import unittest

from helpers import commit_all, decide, listing, new_repo, run


class Base(unittest.TestCase):
    def prepare(self, name, content):
        repo = new_repo(self)
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        commit_all(repo, "base")
        r, doc, out = listing(self, repo, "--paths", name)
        self.assertEqual(r.returncode, 0, r.stderr)
        return repo, path, doc, out

    def apply(self, name, content, choose=None, *flags):
        """Apply a decision per pending comment (default: delete all); returns (result, new text)."""
        repo, path, doc, out = self.prepare(name, content)
        by_id = {c["id"]: c for c in doc["comments"]}
        dec = {cid: (choose(by_id[cid]) if choose else {"action": "delete"}) for cid in doc["pending"]}
        r = run("apply", decide(out, dec), "--list", out, *flags, cwd=repo)
        return r, path.read_bytes().decode()

    def deleted(self, name, content):
        r, text = self.apply(name, content)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        return text


class Deletes(Base):
    def test_own_line_trailing_and_inline_block(self):
        src = ("function f(a, b) {\n  // own line\n  const x = a + b; // trailing\n"
               "  return g(/* inline */ x, a/*tight*/b, c /* spaced */ + d, e /* last */);\n}\n")
        self.assertEqual(self.deleted("a.js", src),
                         "function f(a, b) {\n  const x = a + b;\n  return g(x, a b, c + d, e);\n}\n")

    def test_tokens_never_merge(self):
        src = "x = a/**/-/**/-b; y = f(/**/); z = 1/**/;\n"
        self.assertEqual(self.deleted("a.js", src), "x = a- -b; y = f(); z = 1;\n")

    def test_multi_line_block(self):
        src = "int x;\n/*\n * Initialize the counter\n * to zero.\n */\nint y = 0;\nint z = f(a, /* one\n two */ b);\n"
        self.assertEqual(self.deleted("a.c", src), "int x;\nint y = 0;\nint z = f(a,\n b);\n")

    def test_multi_line_comment_between_code_keeps_a_line_break(self):
        src = "let a = 1 /* x\n*/ let b = 2\n"
        self.assertEqual(self.deleted("a.js", src), "let a = 1\n let b = 2\n")

    def test_multi_line_comment_in_a_directive_becomes_one_space(self):
        cases = {
            "#define SUM(a,b) ((a) /* first\n second */ + (b))\nint x = SUM(1,2);\n":
                "#define SUM(a,b) ((a) + (b))\nint x = SUM(1,2);\n",
            "#define A \\\n  1 /* c\n */ + 2\nint y = A;\n": "#define A \\\n  1 + 2\nint y = A;\n",
            "#define A /* x\n */ 1 /* y\n z */ + 2\nint q = A;\n": "#define A 1 + 2\nint q = A;\n",
            "#if X /* a\n b */\nint z;\n#endif\n": "#if X\nint z;\n#endif\n",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.deleted("a.c", src), want)

    def test_two_comments_on_one_line(self):
        self.assertEqual(self.deleted("a.c", "int a; /* x */ /* y */\n/* p */ /* q */\nint b;\n"),
                         "int a;\nint b;\n")

    def test_jsx_comment_child(self):
        src = "const A = () => (\n  <div>\n    {/* header */}\n    <b>x</b>{/* tail */}\n  </div>\n);\n"
        self.assertEqual(self.deleted("a.tsx", src),
                         "const A = () => (\n  <div>\n    <b>x</b>\n  </div>\n);\n")

    def test_html_inline_comment_leaves_no_space(self):
        self.assertEqual(self.deleted("a.html", "<p>a<!-- x -->b</p>\n<!-- own -->\n<p>c</p>\n"),
                         "<p>ab</p>\n<p>c</p>\n")

    def test_css_comment_is_not_whitespace(self):
        cases = {
            ("a.css", ".a/* c */.b { color: red }\n"): ".a.b { color: red }\n",
            ("a.css", "a/**/.b { x: y }\n#id/**/.c { x: y }\na/**/:hover { x: y }\n"):
                "a.b { x: y }\n#id.c { x: y }\na:hover { x: y }\n",
            ("a.css", ".a/* x\n */.b { x: y }\n"): ".a.b { x: y }\n",
            ("a.css", ".a /* c */ .b { x: f(/* c */ y) }\n"): ".a .b { x: f( y) }\n",
        }
        for (name, src), want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.deleted(name, src), want)

    def test_css_comment_between_tokens_that_would_merge_is_refused(self):
        src = "a { margin: 1/**/.5px }\n"
        r, text = self.apply("a.css", src)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(b"would change code", r.stderr)
        self.assertEqual(text, src)

    def test_escaped_space_before_a_trailing_comment_stays(self):
        self.assertEqual(self.deleted("a.sh", "echo a\\  # c\necho b\n"), "echo a\\ \necho b\n")

    def test_js_line_separator_ends_the_comment(self):
        self.assertEqual(self.deleted("a.js", "foo(); // note\u2028bar();\n"), "foo(); \u2028bar();\n")

    def test_shell_comment_after_continuation_keeps_its_line(self):
        src = "run a \\\n# ends the command\nrun b\n"
        self.assertEqual(self.deleted("a.sh", src), "run a \\\n\nrun b\n")

    def test_python_comments_and_docstrings(self):
        src = ('"""Module docstring."""\n\nimport os  # import os\n\n\ndef f():\n    """Return one."""\n'
               "    # the answer\n    return 1\n")
        self.assertEqual(self.deleted("a.py", src), "import os\n\n\ndef f():\n    return 1\n")

    def test_docstring_followed_by_a_semicolon(self):
        r, text = self.apply("a.py", 'def f():\n    """d"""; return 1\n\n\ndef g(): """e"""; return 2\n')
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(text, "def f():\n    return 1\n\n\ndef g(): return 2\n")


class BlankLines(Base):
    def test_collapse(self):
        src = ("const a = 1;\n\n// ---- Helpers ----\n\nconst b = 2;\n"
               "function f() {\n  // first line of the body\n\n  return b;\n}\n"
               "function g() {\n  return 1;\n\n  // last line\n}\n")
        self.assertEqual(self.deleted("a.js", src),
                         "const a = 1;\n\nconst b = 2;\nfunction f() {\n  return b;\n}\n"
                         "function g() {\n  return 1;\n}\n")

    def test_file_start_and_end(self):
        self.assertEqual(self.deleted("a.js", "// header\n\nconst a = 1;\n\n// trailer\n"), "const a = 1;\n")

    def test_python_block_opener(self):
        self.assertEqual(self.deleted("a.py", "def f():\n    # why\n\n    return 1\n"),
                         "def f():\n    return 1\n")

    def test_existing_blank_lines_untouched(self):
        src = "a = 1\n\n\nb = 2  # x\n"
        self.assertEqual(self.deleted("a.py", src), "a = 1\n\n\nb = 2\n")


class Encoding(Base):
    def test_crlf_bom_and_missing_final_newline(self):
        src = "﻿// head\r\nconst a = 1; // a\r\n// gone\r\nconst b = 2;\r\n// last"
        self.assertEqual(self.deleted("a.ts", src), "﻿const a = 1;\r\nconst b = 2;")

    def test_crlf_multi_line_rewrite(self):
        src = "// one\r\n// two\r\nconst a = 1;\r\n"
        r, text = self.apply("a.ts", src, lambda c: {"action": "rewrite", "text": "// A\n// B"}
                             if c["line"] == 1 else {"action": "delete"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(text, "// A\r\n// B\r\nconst a = 1;\r\n")

    def test_non_utf8_file_is_an_error_and_untouched(self):
        repo, path, doc, out = self.prepare("a.js", b"// caf\xe9\nx = 1;\n")
        self.assertEqual(doc["files"][0]["status"], "error")
        self.assertIn("not UTF-8", doc["files"][0]["reason"])
        self.assertEqual(doc["comments"], [])


class Rewrites(Base):
    def test_rewrite_keeps_indentation(self):
        src = "function f() {\n  // Note that we need to sleep here because the API rate-limits us (see #123)\n  sleep(1);\n}\n"
        r, text = self.apply("a.ts", src, lambda c: {"action": "rewrite", "text": "// API rate limit (#123)"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(text, "function f() {\n  // API rate limit (#123)\n  sleep(1);\n}\n")

    def test_multi_line_rewrite_of_a_run(self):
        src = "  // a\n  // b\n  // c\n  x();\n"
        r, text = self.apply("a.js", src, lambda c: {"action": "rewrite", "text": "// A\n// B"}
                             if c["line"] == 1 else {"action": "delete"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(text, "  // A\n  // B\n  x();\n")

    def test_docstring_rewrite(self):
        src = 'def f(x):\n    """Get the thing.\n\n    Args:\n        x: the x\n\n    Raises:\n        KeyError: missing x.\n    """\n    return d[x]\n'
        r, text = self.apply("a.py", src, lambda c: {"action": "rewrite", "text": '"""Raises KeyError if x is missing."""'})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(text, 'def f(x):\n    """Raises KeyError if x is missing."""\n    return d[x]\n')

    def test_invalid_rewrites_are_refused(self):
        bad = {
            ("a.ts", "x(); // c\n"): ["/* c */", "// a\n// b", "x(); // c", "not a comment", "// a\u2028b()",
                                     "// a\u2029"],
            ("a.ts", "f(a /* c */, b);\n"): ["// c", "/* a */ b /* c */"],
            ("a.c", "int x; // c\n"): ["// ends in a backslash \\"],
            ("a.py", "def f():\n    \"\"\"Doc.\"\"\"\n"): ["# comment", "'''a''' 'b'", 'f"x"'],
            ("a.tsx", "const A = <div>{/* c */}</div>;\n"): ["/* c */", "{x}"],
            ("a.ts", "// c\nx();\n"): ["// eslint-disable-next-line"],
        }
        for (name, src), texts in bad.items():
            for t in texts:
                with self.subTest(src=src, rewrite=t):
                    r, text = self.apply(name, src, lambda c: {"action": "rewrite", "text": t})
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertIn(b"no file was changed", r.stderr)
                    self.assertEqual(text, src)


class Refusals(Base):
    def test_missing_decisions(self):
        repo, path, doc, out = self.prepare("a.js", "// a\n// b\nx();\n")
        r = run("apply", decide(out, {"c1": {"action": "delete"}}), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"missing decisions (1): c2", r.stderr)
        self.assertEqual(path.read_text(), "// a\n// b\nx();\n")

    def test_bad_decisions(self):
        repo, path, doc, out = self.prepare("a.js", "#!/usr/bin/env node\n// a\nx();\n")
        cases = {"c1": {"action": "delete"}, "c9": {"action": "delete"}, "c2": {"action": "keep"}}
        for cid, d in cases.items():
            with self.subTest(cid=cid):
                dec = {"c2": {"action": "delete"}, cid: d}
                r = run("apply", decide(out, dec), "--list", out, cwd=repo)
                self.assertEqual(r.returncode, 2)
        self.assertEqual(path.read_text(), "#!/usr/bin/env node\n// a\nx();\n")

    def test_protected_stays_byte_identical(self):
        src = "// eslint-disable-next-line no-console\nconsole.log(1); // log\n"
        self.assertEqual(self.deleted("a.js", src), "// eslint-disable-next-line no-console\nconsole.log(1);\n")

    def test_only_statement_docstring_cannot_be_deleted(self):
        r, text = self.apply("a.py", 'def f():\n    """Stub."""\n')
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"only statement", r.stderr)

    def test_stale_file_and_second_apply(self):
        repo, path, doc, out = self.prepare("a.js", "// a\nx();\n")
        dec = decide(out, {"c1": {"action": "delete"}})
        path.write_text("// a\nx(); y();\n")
        r = run("apply", dec, "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"file changed since list", r.stdout)
        self.assertEqual(path.read_text(), "// a\nx(); y();\n")
        path.write_text("// a\nx();\n")
        self.assertEqual(run("apply", dec, "--list", out, cwd=repo).returncode, 0)
        self.assertEqual(path.read_text(), "x();\n")
        again = run("apply", dec, "--list", out, cwd=repo)
        self.assertEqual(again.returncode, 1)
        self.assertIn(b"file changed since list", again.stdout)
        self.assertEqual(path.read_text(), "x();\n")

    def test_dry_run_edits_nothing(self):
        r, text = self.apply("a.js", "// a\nx();\n", None, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"would edit 1 file", r.stdout)
        self.assertEqual(text, "// a\nx();\n")

    def test_restore_snapshot(self):
        repo, path, doc, out = self.prepare("a.js", "// a\nx();\n")
        self.assertEqual(run("apply", decide(out, {"c1": {"action": "delete"}}), "--list", out, cwd=repo).returncode, 0)
        self.assertEqual(path.read_text(), "x();\n")
        r = run("restore", "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(path.read_text(), "// a\nx();\n")
        run("restore", "--list", out, "--applied", cwd=repo)
        self.assertEqual(path.read_text(), "x();\n")

    def test_report(self):
        repo, path, doc, out = self.prepare(
            "a.ts", "// Added retry\n// eslint-disable-next-line no-console\nconsole.log(1);\n"
                    "// Safari drops the event: https://bugs.webkit.org/1\nf();\n// AI: keep this comment\n")
        dec = {"c1": {"action": "delete"}, "c3": {"action": "rewrite", "text": "// Safari drops it: https://bugs.webkit.org/1"},
               "c4": {"action": "delete"}}
        dpath = decide(out, dec)
        self.assertEqual(run("apply", dpath, "--list", out, cwd=repo).returncode, 0)
        r = run("report", "--list", out, "--decisions", dpath, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = r.stdout.decode()
        self.assertIn("4 in scope: 2 deleted, 1 rewritten, 0 kept, 1 protected", rep)
        self.assertIn("| a.ts:4 | shortened | `// Safari drops it: https://bugs.webkit.org/1` |", rep)
        self.assertIn("eslint-disable without a reason", rep)
        self.assertIn("addresses the agent (deleted)", rep)
        self.assertIn("- a.ts:1 `// Added retry`", rep)


if __name__ == "__main__":
    unittest.main()
