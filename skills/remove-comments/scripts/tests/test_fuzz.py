"""Seeded fuzz against independent oracles: random C and JS/TS lines with
comments inserted, every comment deleted through edit(); whenever verify's
token comparison says nothing changed, the C preprocessor (gcc/cc/clang -E) or
TypeScript's emitter must agree. Skipped when the oracle isn't available
(TypeScript: set RC_TS_ROOT to a directory whose node_modules has it).

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import unittest

from helpers import SCRIPTS, commentscan, tmpdir

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line

C_BANK = ['int a = 1;', 'char *s = "a // b";', 'char *t = "/* x */";', "char c = '\"';", "char d = '/';",
          '#define X "/*"', '#define Y(a) ((a) + 1)', 'int z = Y(2);', '#define L 1 \\\n  + 2', 'int w = L;',
          '#if 0\n// x\n#endif', '#ifdef FOO\nint q;\n#endif', 'x = a / b / c;', 'x = a /*c*/ / b;',
          "int e = 1'000;", '#define Z(a,b) a ## b', '#define STR(x) #x', 'char *p = STR(a // b);',
          'int f(void) { return 1; }', 'int g = 0x1e+1;', 'int h = 1.5e-3;', '#define E 1 +\\\n 2',
          '#define Q(a) \\\n  ((a) * 2)', 'a = b ? c : d;', 'a = - -b;', 'a = b- -c;', 'a = b+ +c;']
C_COMMENTS = ["// c", "/* c */", "/* c\n */", "/*c*/", "// c \\\n   still", "/* a */ /* b */", "// c /* d"]
JS_BANK = ["a = b / c;", "if (p) /x/.test(s);", "if (p) /\\//.test(s) && go();", "while (x) /a\\/*b/.test(y);",
           "x = /[/*]/.test(y);", "x = a / b / c;", "x = (a) / 2 / (b);", "const s = 'a // b';",
           "const t = `x ${a /* c */} // y`;", "const u = \"/* not */\";", "return /* \n */ x;",
           "y = a /* \n */ ++b;", "z = a+ +b;", "z = a- -b;", "z = a++ + ++b;", "k = a / /re/.source.length;",
           "f(/ab+c/gi, 1);", "x = y ? /a/ : /b/;", "let r = a\n/b/g;", "x = a</*c*/b;", "q = a /**/ / b;",
           "x = a ?? b;", "x = a?.[0];", "x = [1,2,3].map(n => n / 2);", "do x++; while (x < 3)",
           "x = typeof /a/;", "x = a\n++b;", "if (a) {\n}\n/re/.test(b);", "x = `a${`b${c}`}d`;",
           "var re = /\\/\\/|\\/\\*/g;", "x = a /* */ / 2 /* */ / 3;", "foo(); // note bar();"]
JS_COMMENTS = ["// c", "/* c */", "/* c\n */", "/*c*/", "// c /* d", "/* c // d */", "/* c */ /* d */"]
NODE_EMIT = r"""
const ts = require(process.argv[1]);
const emit = (src, fileName) => {
  const r = ts.transpileModule(src, {fileName, reportDiagnostics: true, compilerOptions: {removeComments: true,
    target: ts.ScriptTarget.ESNext, jsx: ts.JsxEmit.Preserve, module: ts.ModuleKind.ESNext}});
  return [r.outputText.replace(/\s+/g, ' '), r.diagnostics.length];
};
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(([f, a, b]) => {
  const [x, dx] = emit(a, f), [y, dy] = emit(b, f);
  return dx === 0 && dy === 0 ? x === y : null;
})));
"""


def generate(rnd, bank, marks, n):
    out = []
    for _ in range(n):
        lines = []
        for _ in range(rnd.randint(1, 4)):
            parts = rnd.choice(bank).split("\n")
            k = rnd.randrange(len(parts))
            p = parts[k]
            spots = [i for i, ch in enumerate(p) if ch == " "] + [len(p)]
            i = rnd.choice(spots)
            if rnd.random() < .7 and not p.endswith("\\"):
                parts[k] = p[:i] + " " + rnd.choice(marks) + (" " if i < len(p) else "") + p[i:]
            if rnd.random() < .2:
                parts.insert(0, rnd.choice(marks[:2]))
            lines.append("\n".join(parts))
        out.append("\n".join(lines) + "\n")
    return out


def strip_all(text, lang):
    """The text with every unprotected comment deleted, or None when the scanner refuses
    it or verify's own comparison already reports a change."""
    try:
        sc = commentscan.scan(text, lang)
        new = comments.edit(text, lang, [(c, None) for c in sc.comments if not c.protected])
        ok = comments.compare(text, new, lang)["ok"]
    except commentscan.ScanError:
        return None
    return new if ok and new != text else None


class Fuzz(unittest.TestCase):
    def test_c_against_the_preprocessor(self):
        argv = comments.c_preprocessor(str(tmpdir()))
        if not argv:
            self.skipTest("no working gcc, cc or clang")

        def pre(text):
            r = subprocess.run(argv + ["-"], input=text, capture_output=True, text=True, timeout=30)
            items = []
            for line in r.stdout.split("\n"):
                items += [line.split()] if line.lstrip().startswith("#") else line.split()
            return r.returncode, items

        checked = 0
        for text in generate(random.Random(11), C_BANK, C_COMMENTS, 60):
            new = strip_all(text, "c")
            if new is None:
                continue
            rc, before = pre(text)
            if rc:
                continue
            checked += 1
            rc2, after = pre(new)
            with self.subTest(text=text, new=new):
                self.assertEqual(rc2, 0)
                self.assertEqual([re.sub(r"\s+", "", "".join(x)) for x in after],
                                 [re.sub(r"\s+", "", "".join(x)) for x in before])
        self.assertGreater(checked, 30)

    @unittest.skipUnless(os.environ.get("RC_TS_ROOT") and shutil.which("node"),
                         "set RC_TS_ROOT to a directory whose node_modules has typescript")
    def test_js_against_typescript(self):
        rnd = random.Random(5)
        cases = []
        for lang in ("js", "ts"):
            for text in generate(rnd, JS_BANK, JS_COMMENTS, 150):
                new = strip_all(text, lang)
                if new is not None:
                    cases.append([f"x.{lang}", text, new])
        ts = subprocess.run(["node", "-e", "process.stdout.write(require('module').createRequire("
                             "process.argv[1]).resolve('typescript'))", os.path.join(os.environ["RC_TS_ROOT"], "x.js")],
                            capture_output=True, text=True, timeout=30, cwd=tmpdir()).stdout
        r = subprocess.run(["node", "-e", NODE_EMIT, ts], input=json.dumps(cases), capture_output=True, text=True,
                           timeout=60, cwd=tmpdir())
        same = json.loads(r.stdout)
        self.assertGreater(sum(s is not None for s in same), 150)
        self.assertEqual([c[1:] for c, s in zip(cases, same) if s is False], [])


if __name__ == "__main__":
    unittest.main()
