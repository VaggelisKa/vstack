"""TypeScript's postfix non-null assertion (a!) ends a value, so a "/" after it is a
division; a prefix "!" (and every "!" in plain JS) still lets a regex follow. Type
arguments closed by ">" before a "/" are scanned or refused, never misread silently.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import unittest

from helpers import commentscan, texts

REGEX_LINE = "var re = /\\/\\/|\\/\\*/g;\n"


def literals(src, lang):
    return [src[a:b] for a, b in commentscan.scan(src, lang).literals]


class NonNull(unittest.TestCase):
    def test_non_null_then_division(self):
        src = "x = a! / b; /* c // d */\n" + REGEX_LINE
        for lang in ("ts", "tsx"):
            with self.subTest(lang=lang):
                self.assertEqual(texts(src, lang), ["/* c // d */"])
                self.assertEqual(literals(src, lang), ["/\\/\\/|\\/\\*/g"])

    def test_non_null_forms(self):
        cases = ["a!.b / c; // d\n", "a!! / b; // d\n", "f()! / 2; // d\n", "x[0]! / 2; // d\n",
                 "'s'! / 2; // d\n", "x = a!=b; // d\n", "a !== b / c // d\n", "a! / b // d\n"]
        for src in cases:
            for lang in ("ts", "tsx"):
                with self.subTest(src=src, lang=lang):
                    self.assertEqual(texts(src, lang), ["// d"])

    def test_prefix_not_still_starts_a_regex(self):
        for lang in ("js", "jsx", "ts", "tsx"):
            for src in ("if (!/\\/\\//.test(s)) {} // c\n", "x = !/\\/\\//.test(s); // c\n",
                        "return !/\\/\\//.test(s); // c\n"):
                with self.subTest(src=src, lang=lang):
                    self.assertEqual(texts(src, lang), ["// c"])
                    self.assertEqual(literals(src, lang), ["/\\/\\//"])

    def test_line_break_before_bang_makes_it_prefix(self):
        src = "x = y\n!/\\/\\//.test(s) // c\n"
        self.assertEqual(texts(src, "ts"), ["// c"])
        src = "x = y /* a\n */ !/\\/\\//.test(s) // c\n"
        self.assertEqual(texts(src, "ts"), ["/* a\n */", "// c"])

    def test_plain_js_bang_is_prefix_only(self):
        # "a! / b" isn't JS; there the "!" is read as prefix, so "/ b; /" is a regex.
        self.assertEqual(literals("x = a! / b; // c\n", "js"), ["/ b; /"])
        self.assertEqual(literals("x = a! / b; // c\n", "ts"), [])

    def test_bang_after_brace_before_slash_is_an_error(self):
        with self.assertRaises(commentscan.ScanError):
            commentscan.scan("x = {}! / a; // c\n", "ts")
        self.assertEqual(texts("function f() {}\n!g(); // c\n", "ts"), ["// c"])

    def test_type_arguments_before_a_slash_never_misscan(self):
        for src, lang in (("x = a as Array<number> / 2; // c\n", "ts"),
                          ("x = f<T>(y) / 2; // c\n", "ts"),
                          ("x = <T>y / z; // c\n", "ts"),
                          ("x = a satisfies {b: 1} / 2; // c\n", "ts")):
            with self.subTest(src=src):
                try:
                    found = texts(src, lang)
                except commentscan.ScanError:
                    continue
                self.assertEqual(found, ["// c"])

    def test_arrow_and_comparison_before_a_regex(self):
        for src in ("f = x => /\\/\\//.test(x); // c\n", "ok = a > /\\/\\//.exec(s).length; // c\n"):
            for lang in ("ts", "tsx"):
                with self.subTest(src=src, lang=lang):
                    self.assertEqual(texts(src, lang), ["// c"])


if __name__ == "__main__":
    unittest.main()
