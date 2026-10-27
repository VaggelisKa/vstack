"""lessc skips a comment between tokens like whitespace, then tests the one character
next to a token: before it for a descendant combinator or a negative number
(.a /**/.b is .a.b, 1 /**/-2 is -1), after it for an operator (@x -/**/ 1 is a list,
@x - 1 is 2). Some constructs are a single regex a comment breaks (@x /**/ : 3 is an
at-rule, :extend(.b all /**/ ) extends nothing), and a selector's (...) is copied as
it is, // included. So a Less comment is deletable only with whitespace on both sides
or at a statement start, outside parentheses and brackets, and not between an @name
and its ':' or '('; every other one is protected, and verify flags its removal.

With RC_LESS_ROOT (or RC_TS_ROOT) naming a directory whose node_modules has less,
every case is also compiled before and after, the protected ones are shown to compile
differently without their comment, and a fuzz inserts comments at every position of
a few stylesheets: each edit must compile to the same CSS.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from helpers import SCRIPTS, commentscan, listing, decide, new_repo, commit_all, run, tmpdir

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line

GOLDEN = {
    "a {\n  x: y;\n  /* c */\n  z: w;\n}\n": "a {\n  x: y;\n  z: w;\n}\n",
    "a {\n  x: y; /* c */\n  z: w; // d\n}\n": "a {\n  x: y;\n  z: w;\n}\n",
    ".a /* c */ { x: y }\n": ".a { x: y }\n",
    "a { x: y; /* c */ z: w; }\n": "a { x: y; z: w; }\n",
    "a{x:y;/* c */z:w}\n": "a{x:y;z:w}\n",
    ".a{/* c */.b{x:y}}\n": ".a{.b{x:y}}\n",
    ".a{x:y;/* c */&-b{x:y}}\n": ".a{x:y;&-b{x:y}}\n",
    "a { m: 1px /* c */ 2px }\n": "a { m: 1px 2px }\n",
    ".a-b /* c */ .c { x: y }\n": ".a-b .c { x: y }\n",
    "@x: 3;\na { m: @x /* c */ -1 }\n": "@x: 3;\na { m: @x -1 }\n",
    "@x: 3;\na { m: @x /* c */\n  -1 }\n": "@x: 3;\na { m: @x\n  -1 }\n",
    "@x: 3;\na { m: @x // c\n  -1 }\n": "@x: 3;\na { m: @x\n  -1 }\n",
    "@x: 3;\na { m: @x - /* c */ 1 }\n": "@x: 3;\na { m: @x - 1 }\n",
    "/* c */.a { x: y }\n": ".a { x: y }\n",
    ".a, // first\n.b { x: y }\n": ".a,\n.b { x: y }\n",
    "/* a\n   b */\n.a { x: y } /* c */\n": ".a { x: y }\n",
    "a {\n  box-shadow:\n    /* one */\n    0 0 1px red,\n    0 0 2px blue;\n}\n":
        "a {\n  box-shadow:\n    0 0 1px red,\n    0 0 2px blue;\n}\n",
}
# Each protected case, what deleting its comment would give, and the reason's first words.
PROTECTED = {
    ".a-b /**/.c { x: y }\n": (".a-b .c { x: y }\n", "Less: it touches code"),
    "a { p: 1 /**/-2 }\n": ("a { p: 1 -2 }\n", "Less: it touches code"),
    "a { p: 1 /**/+2 }\n": ("a { p: 1 +2 }\n", "Less: it touches code"),
    "@x: 3;\na { p: @x /**/-1 }\n": ("@x: 3;\na { p: @x -1 }\n", "Less: it touches code"),
    "@x: 3;\na { p: -/**/@x }\n": ("@x: 3;\na { p: -@x }\n", "Less: it touches code"),
    "@x: 3;\na { p: @x -/**/ 1 }\n": ("@x: 3;\na { p: @x - 1 }\n", "Less: it touches code"),
    "@x: 3;\na { p: @x -// c\n1 }\n": ("@x: 3;\na { p: @x -\n1 }\n", "Less: it touches code"),
    "a { p: 1\n/**/-2 }\n": ("a { p: 1\n-2 }\n", "Less: it touches code"),
    ".a-b\n/**/.c { x: y }\n": (".a-b\n.c { x: y }\n", "Less: it touches code"),
    ".a/* x\n */.b { x: y }\n": (".a .b { x: y }\n", "Less: it touches code"),
    "@n: q;\n.@{n} /**/.c { x: y }\n": ("@n: q;\n.@{n} .c { x: y }\n", "Less: it touches code"),
    "a:not( // c\n.b) { x: y }\n": ("a:not(\n.b) { x: y }\n", "Less: inside parentheses"),
    ".a:extend(.b all /**/ ) { x: y }\n.b { z: w }\n": (".a:extend(.b all ) { x: y }\n.b { z: w }\n",
                                                         "Less: inside parentheses"),
    "@x /**/ : 3;\na { b: 1 }\n": ("@x : 3;\na { b: 1 }\n", "Less: between an @name"),
}
FUZZ_BANK = [
    "@x: 3; a { m: @x -1; n: @x - 1; o: 1-2; p: 1 -2; q: (1)-2; r: 1+2; s: 1 +2; t: a-b; u: -@x; v: 1px-2px }",
    ".a-b .c > .d + .e ~ .f { x: y; &:hover, &.g { x: y } &-suf { x: y } }",
    ".m(@a; @b: 2) { x: @a @b } .a { .m(1; 2); }",
    "a { margin: -1px -2px; w: ~'calc(1px + 2px)'; h: (1/2); i: 10px/2 }",
    "a[href^='x'], a:not(.b):nth-child(2n+1)::after { content: 'a'; x: y !important }",
    "@x : 3; .m(@a) when (@a > 0) { k: @a } a { .m(1); b: @x; }",
    ".a:extend(.b all) { x: y } .b { z: w } .c { &:extend(.b); }",
    "@d: { a: b }; .c { @d(); } #ns { .m() { c: d } } e { #ns > .m(); }",
    "@media screen and (min-width: 1px) { .a { b: c } }",
]
FUZZ_COMMENTS = ["/**/", " /**/", "/**/ ", " /**/ ", "\n/**/", "/**/\n", "\n/**/\n", "// c\n", " // c\n"]
NODE_LESS = r"""
const less = require(process.argv[1]);
const css = s => less.render(s, {compress: true}).then(r => r.css, e => 'ERROR ' + e.message);
(async () => {
  const out = [];
  for (const [b, a] of JSON.parse(require('fs').readFileSync(0, 'utf8'))) out.push([await css(b), await css(a)]);
  process.stdout.write(JSON.stringify(out));
})();
"""


def strip_all(text):
    sc = commentscan.scan(text, "less")
    return comments.edit(text, "less", [(c, None) for c in sc.comments if not c.protected])


def find_less():
    """Path of less's entry point from RC_LESS_ROOT or RC_TS_ROOT, or None."""
    for var in ("RC_LESS_ROOT", "RC_TS_ROOT"):
        root = os.environ.get(var)
        if root and shutil.which("node"):
            r = subprocess.run(["node", "-e", "process.stdout.write(require.resolve('less', {paths: [process.argv[1]]}))",
                                root], capture_output=True, text=True, timeout=30, cwd=tmpdir())
            if r.returncode == 0 and r.stdout:
                return r.stdout
    return None


def lessc(pairs):
    """[(css before, css after)] for [(before, after)] sources; comments lessc copies
    through and whitespace that can't change the meaning are dropped from the CSS."""
    r = subprocess.run(["node", "-e", NODE_LESS, find_less()], input=json.dumps(pairs), capture_output=True,
                       text=True, timeout=120, cwd=tmpdir())
    if r.returncode:
        raise AssertionError(r.stderr)

    def norm(css):
        css = re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
        return re.sub(r"\s*([{};,])\s*", r"\1", css).strip()
    return [(b if b.startswith("ERROR") else norm(b), a if a.startswith("ERROR") else norm(a))
            for b, a in json.loads(r.stdout)]


class Less(unittest.TestCase):
    def test_golden_deletes(self):
        for src, want in GOLDEN.items():
            with self.subTest(src=src):
                self.assertEqual({c.protected for c in commentscan.scan(src, "less").comments}, {None})
                new = strip_all(src)
                self.assertEqual(new, want)
                self.assertTrue(comments.compare(src, new, "less")["ok"])

    def test_comments_lessc_could_read_differently_are_protected(self):
        for src, (_, why) in PROTECTED.items():
            with self.subTest(src=src):
                [c] = commentscan.scan(src, "less").comments
                self.assertTrue(c.protected and c.protected.startswith(why), c.protected)
                self.assertEqual(strip_all(src), src)

    def test_verify_flags_removing_a_protected_comment(self):
        for src, (naive, _) in PROTECTED.items():
            with self.subTest(src=src):
                self.assertFalse(comments.compare(src, naive, "less")["ok"])
        self.assertFalse(comments.compare(".a-b /**/.c { x: y }\n", ".a-b.c { x: y }\n", "less")["ok"])
        self.assertFalse(comments.compare("a { p:1 /**/-2 }\n", "a { p:1 -2 }\n", "less")["ok"])
        self.assertTrue(comments.compare("a { m: 1px /* c */ 2px }\n", "a { m: 1px 2px }\n", "less")["ok"])

    def test_comment_between_an_at_name_and_a_paren_is_protected(self):
        [c] = commentscan.scan(".c { @d /**/ (); }\n@d: { a: b };\n", "less").comments
        self.assertTrue(c.protected.startswith("Less: between an @name"))
        [c] = commentscan.scan("@x: 1;\na { m: @x /**/ -1 }\n", "less").comments
        self.assertIsNone(c.protected)

    def test_same_text_is_safe_in_css_and_scss(self):
        [c] = commentscan.scan(".a-b /**/.c { x: y }\n", "css").comments
        self.assertIsNone(c.protected)
        [c] = commentscan.scan("a:not( /**/ .b) { x: y }\n", "scss").comments
        self.assertIsNone(c.protected)

    def test_apply_through_the_cli(self):
        repo = new_repo()
        src = ".a /* c */ { x: y; /* d */ z: w; }\n.a-b /* e */.c { x: y }\n@x: 1; b { m: @x // f\n  -1 }\n"
        (repo / "a.less").write_text(src)
        commit_all(repo, "base")
        r, doc, out = listing(repo, "--paths", "a.less")
        self.assertEqual(r.returncode, 0, r.stderr)
        [e] = [c for c in doc["comments"] if c["protected"]]
        self.assertEqual(e["text"], "/* e */")
        dec = {c: {"action": "delete"} for c in doc["pending"]}
        r = run("apply", decide(out, {**dec, e["id"]: {"action": "delete"}}), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"{e['id']}: protected (Less: it touches code".encode(), r.stderr)
        r = run("apply", decide(out, {**dec, e["id"]: {"action": "rewrite", "text": "/* x */"}}), "--list", out,
                cwd=repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"{e['id']}: protected".encode(), r.stderr)
        self.assertEqual((repo / "a.less").read_text(), src)
        r = run("apply", decide(out, dec), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((repo / "a.less").read_text(),
                         ".a { x: y; z: w; }\n.a-b /* e */.c { x: y }\n@x: 1; b { m: @x\n  -1 }\n")
        self.assertEqual(run("verify", "--list", out, cwd=repo).returncode, 0)

    @unittest.skipUnless(find_less(), "set RC_LESS_ROOT to a directory whose node_modules has less")
    def test_lessc_compiles_deletes_the_same_and_protected_cases_differently(self):
        pairs = [[b, a] for b, a in GOLDEN.items()] + [[b, a] for b, (a, _) in PROTECTED.items()]
        for (src, new), (b, a) in zip(pairs, lessc(pairs)):
            with self.subTest(src=src):
                self.assertFalse(b.startswith("ERROR"), b)
                if src in GOLDEN:
                    self.assertEqual(a, b, new)
                else:
                    self.assertNotEqual(a, b, new)

    @unittest.skipUnless(find_less(), "set RC_LESS_ROOT to a directory whose node_modules has less")
    def test_lessc_fuzz(self):
        pairs = []
        for base in FUZZ_BANK:
            quote = None
            for i, ch in enumerate(base):
                if quote:
                    quote = None if ch == quote else quote
                    continue
                if ch in "'\"":
                    quote = ch
                    continue
                for comment in FUZZ_COMMENTS:
                    src = base[:i] + comment + base[i:]
                    try:
                        new = strip_all(src)
                    except commentscan.ScanError:
                        continue
                    if new != src:
                        self.assertTrue(comments.compare(src, new, "less")["ok"], src)
                        pairs.append([src, new])
        self.assertGreater(len(pairs), 2000)
        compiled = 0
        for (src, new), (b, a) in zip(pairs, lessc(pairs)):
            if not b.startswith("ERROR"):
                compiled += 1
                self.assertEqual(a, b, f"{src!r} -> {new!r}")
        self.assertGreater(compiled, 2000, compiled)


if __name__ == "__main__":
    unittest.main()
