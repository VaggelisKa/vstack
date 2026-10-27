"""Property-style checks over a corpus with one or more files per language:
strip is stable (stripping twice changes nothing more), and deleting every
comment the tool allows, through list and apply, always passes verify.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import shutil
import unittest

from helpers import SKILL, commentscan, commit_all, decide, listing, new_repo, run, tmpdir

CORPUS = {
    "a.js": "#!/usr/bin/env node\n/* a */ const re = /\\/\\*x/g; // b\nconst t = `${a /* in hole */} // text`;\n"
            "x = a / b /* c */ / d;\nexport default /*#__PURE__*/ f(); // d\n",
    "b.jsx": "const A = () => <div title=\"// no\">{/* unit */}<b>don't</b>{x /* expr */}</div>; // c\n",
    "c.c": "#include <stdio.h> // io\n#if 0\nit's disabled\n#endif\nint main(void) { /* a */ int n = 1'0; // b\n"
           "  char *s = \"/* no */\"; switch (n) { case 1: f(); /* fallthrough */ case 2: break; } }\n",
    "d.rs": "/// Docs.\nfn f<'a>(s: &'a str) -> char { /* a /* b */ c */ let r = r#\"// no\"#; 'x' } // e\n",
    "e.java": "/** Doc. */\nclass A { String t = \"\"\"\n  // no\n  \"\"\"; char c = '\\''; /* a */ } // b\n",
    "f.kt": "// a\nval s = \"${m[\"k\"]} // no\" /* b /* c */ */\nval r = \"\"\"raw // ${x}\"\"\" // d\n",
    "g.swift": "// a\nlet s = \"\\(f(\"x\")) // no\" /* b /* c */ */\nlet r = #\"raw // \"# // d\n",
    "h.cs": "// a\nvar s = $\"{d[\"k\"]} // no\"; var v = @\"c:\\x // \"\"no\"\"\"; /* b */\n",
    "i.scala": "// a\nval s = s\"${m(\"k\")} // no\" /* b /* c */ */\nval c = '\"' // d\n",
    "j.dart": "// a\nvar s = '${m['k']} // no'; /* b /* c */ */\nvar r = r'// no'; // d\n",
    "k.go": "//go:build linux\n\npackage x // a\n\nvar s = `// no` /* b */\nvar r = '/' // c\n",
    "l.css": "/*! Copyright X, MIT License */\na { b: url(//cdn/x); /* c */ content: \"/* no */\"; }\n",
    "m.scss": "// a\n.x { background: url(//cdn/x.png); // b\n  &:hover { c: d; } /* e */ }\n",
    "n.less": "// a\n@c: ~\"// no\"; /* b */\n",
    "o.sh": "#!/bin/sh\n# a\necho $# ${#x} a#b \\# 'q # no' \"$(echo x)\" # b\ncat <<-EOF\n\t# no\n\tEOF\n",
    "p.zsh": "# a\nprint -l *(#qN) # b\n",
    "q.yaml": "# a\nk: \"x # no\" # b\nb: |\n  # no\nu: http://x#y # c\n",
    "r.toml": "# a\nk = \"\"\"\n# no\n\"\"\" # b\nl = '#' # c\n",
    "Dockerfile": "# syntax=docker/dockerfile:1\nFROM x\n# a\nRUN <<EOF\n# no\nEOF\nRUN echo # no\n",
    "s.html": "<!-- a -->\n<p>x<!-- b -->y</p>\n<!--[if IE]>x<![endif]-->\n<style>/* no */</style>\n",
    "t.svg": "<svg><!-- a --><![CDATA[ <!-- no --> ]]><title>t</title></svg>\n",
    "u.py": "\"\"\"Mod.\"\"\"\n# a\nx = f\"{d['#']}\"  # b\ndef f():\n    \"\"\"Only.\"\"\"\nclass A:\n    \"\"\"Doc.\"\"\"\n    y = 1  # c\n",
    "v.ts": "\ufeff// bom\r\nconst a = 1; // crlf\r\n/* x */ const b = 2;",
}


def corpus():
    files = dict(CORPUS)
    for d in sorted((SKILL / "evals" / "fixtures").iterdir()):
        for f in d.glob("*.*"):
            if f.name.startswith(("before.", "after.")):
                files[f"{d.name}-{f.name}"] = f.read_text()
    return files


class Property(unittest.TestCase):
    def test_strip_is_stable_and_verifies(self):
        d = tmpdir()
        for name, text in corpus().items():
            with self.subTest(file=name):
                src = d / name
                src.write_bytes(text.encode())
                r = run("strip", src)
                self.assertEqual(r.returncode, 0, r.stderr)
                once = d / ("once." + name)
                once.write_bytes(r.stdout)
                again = run("strip", once)
                self.assertEqual(again.stdout, r.stdout)
                v = run("verify", "--before", src, "--after", once, "--lang", commentscan.detect(name))
                self.assertEqual(v.returncode, 0, v.stdout.decode())

    def test_delete_everything_allowed_then_verify(self):
        repo = new_repo()
        for name, text in corpus().items():
            (repo / name).write_bytes(text.encode())
        commit_all(repo, "corpus")
        r, doc, out = listing(repo, "--paths", ".")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([f["path"] for f in doc["files"] if f["status"] != "ok"], [])
        by_id = {c["id"]: c for c in doc["comments"]}
        dec = {cid: {"action": "keep", "reason": "only statement"} if by_id[cid].get("only_statement")
               else {"action": "delete"} for cid in doc["pending"]}
        r = run("apply", decide(out, dec), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        r = run("verify", "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        r, doc2, _ = listing(repo, "--paths", ".")
        left = [c for c in doc2["comments"] if not c["protected"] and not c.get("only_statement")]
        self.assertEqual([(c["file"], c["text"]) for c in left], [])


if __name__ == "__main__":
    unittest.main()
