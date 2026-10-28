"""In SCSS a comment is whitespace (Sass compiles .a/**/.b to .a .b), so deleting one
between two tokens leaves a space, and verify flags .a/**/.b -> .a.b. The exception is
a comment right before a negative number ($x/**/-1 is a subtraction, $x -1 a list):
it is protected. CSS keeps treating a comment as no token at all; Less has rules of
its own (test_less.py).

With RC_SASS_ROOT (or RC_TS_ROOT) naming a directory whose node_modules has sass, every
case is also compiled before and after and must give the same CSS.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import SCRIPTS, commentscan, listing, decide, new_repo, commit_all, run, scratch, tmpdir

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line

GOLDEN = {
    ".a/**/.b { x: y }\n": ".a .b { x: y }\n",
    ".d{ &/**/.e{ x: y } }\n": ".d{ & .e{ x: y } }\n",
    "f/**/:hover { x: y }\n": "f :hover { x: y }\n",
    "a/**/>/**/.b { x: y }\n": "a > .b { x: y }\n",
    "a { m: 1px/**/2px }\n": "a { m: 1px 2px }\n",
    "a { m: 1px+/**/2px }\n": "a { m: 1px+ 2px }\n",
    "a { m: 1/**/.5px }\n": "a { m: 1 .5px }\n",
    ".a /* c */.b { x: y }\n": ".a .b { x: y }\n",
    ".a/* x\n */.b { x: y }\n": ".a .b { x: y }\n",
    "a { font: 12px/**//**/30px }\n": "a { font: 12px 30px }\n",
    "$x: 3;\na { m: $x/**/-1 }\n": "$x: 3;\na { m: $x/**/-1 }\n",
    "a { m: 1px /**/-.5 }\n": "a { m: 1px /**/-.5 }\n",
    "a { m: 1px/**/-a }\n": "a { m: 1px -a }\n",
}
NODE_SASS = r"""
const sass = require(process.argv[1]);
const css = s => { try { return sass.compileString(s, {style: 'compressed', logger: sass.Logger.silent}).css; }
                   catch (e) { return 'ERROR ' + e.message.split('\n')[0]; } };
process.stdout.write(JSON.stringify(JSON.parse(require('fs').readFileSync(0, 'utf8')).map(([b, a]) => [css(b), css(a)])));
"""


def strip_all(text, lang):
    sc = commentscan.scan(text, lang)
    return comments.edit(text, lang, [(c, None) for c in sc.comments if not c.protected])


def find_sass():
    """Path of sass's entry point from RC_SASS_ROOT or RC_TS_ROOT, or None."""
    for var in ("RC_SASS_ROOT", "RC_TS_ROOT"):
        root = os.environ.get(var)
        if root and shutil.which("node"):
            with scratch() as d:
                r = subprocess.run(["node", "-e", "process.stdout.write(require.resolve('sass', {paths: [process.argv[1]]}))",
                                    root], capture_output=True, text=True, timeout=30, cwd=d)
            if r.returncode == 0 and r.stdout:
                return r.stdout
    return None


class Scss(unittest.TestCase):
    def test_golden_deletes(self):
        for src, want in GOLDEN.items():
            with self.subTest(src=src):
                new = strip_all(src, "scss")
                self.assertEqual(new, want)
                self.assertTrue(comments.compare(src, new, "scss")["ok"])

    def test_comment_before_a_negative_number_is_protected(self):
        for src in ("a { m: $x/**/-1 }\n", "a { m: 1px /**/-.5 }\n"):
            with self.subTest(src=src):
                [c] = commentscan.scan(src, "scss").comments
                self.assertIn("minus", c.protected)
        [c] = commentscan.scan("a { m: $x/**/-1 }\n", "css").comments
        self.assertIsNone(c.protected)

    def test_verify_treats_the_comment_as_whitespace(self):
        self.assertFalse(comments.compare(".a/**/.b { x: y }\n", ".a.b { x: y }\n", "scss")["ok"])
        self.assertTrue(comments.compare(".a/**/.b { x: y }\n", ".a .b { x: y }\n", "scss")["ok"])
        self.assertFalse(comments.compare(".d{ &/**/.e{} }\n", ".d{ &.e{} }\n", "scss")["ok"])
        self.assertTrue(comments.compare(".a/**/.b { x: y }\n", ".a.b { x: y }\n", "css")["ok"])
        self.assertFalse(comments.compare("a { m: $x/**/-1 }\n", "a { m: $x -1 }\n", "scss")["ok"])

    def test_css_unchanged(self):
        self.assertEqual(strip_all(".a/**/.b { x: y }\n", "css"), ".a.b { x: y }\n")

    def test_apply_through_the_cli(self):
        repo = new_repo(self)
        src = ".a/* c */.b { x: y }\n.d{ &/* c */.e { x: y } }\nf/* c */:hover { x: y }\n"
        (repo / "a.scss").write_text(src)
        commit_all(repo, "base")
        r, doc, out = listing(self, repo, "--paths", "a.scss")
        r = run("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((repo / "a.scss").read_text(), ".a .b { x: y }\n.d{ & .e { x: y } }\nf :hover { x: y }\n")
        self.assertEqual(run("verify", "--list", out, cwd=repo).returncode, 0)

    @unittest.skipUnless(find_sass(), "set RC_SASS_ROOT to a directory whose node_modules has sass")
    def test_sass_compiles_both_sides_the_same(self):
        pairs = [[b, a] for b, a in GOLDEN.items()]
        pairs.append(["$x: 3;\na { m: $x/**/-1; n: $x -1; o: 1px/**/-.5 }\n",
                      strip_all("$x: 3;\na { m: $x/**/-1; n: $x -1; o: 1px/**/-.5 }\n", "scss")])
        r = subprocess.run(["node", "-e", NODE_SASS, find_sass()], input=json.dumps(pairs), capture_output=True,
                           text=True, timeout=60, cwd=tmpdir(self))
        self.assertEqual(r.returncode, 0, r.stderr)
        for (src, new), (b, a) in zip(pairs, json.loads(r.stdout)):
            with self.subTest(src=src):
                self.assertNotIn("ERROR", b)
                self.assertEqual(a, b, new)


if __name__ == "__main__":
    unittest.main()
