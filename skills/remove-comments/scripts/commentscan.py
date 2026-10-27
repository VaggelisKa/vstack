"""Find every comment in a source file, and the string-like spans around them.

scan(text, lang) returns a Scan: comments (with their protected reason and
flags), literals (strings, template pieces, regexes, heredocs, block scalars:
text a comment can't start in, compared verbatim by verify) and JSX text.
Anything the scanner can't decide raises ScanError, and the caller leaves the
file alone: a missed comment costs nothing, a misread string can delete code.
"""
import ast
import io
import os
import re
import tokenize
from dataclasses import dataclass, field

EXT = {
    "py": "python", "pyi": "python",
    "js": "js", "mjs": "js", "cjs": "js", "jsx": "jsx",
    "ts": "ts", "mts": "ts", "cts": "ts", "tsx": "tsx",
    "c": "c", "h": "c", "cc": "c", "cpp": "c", "cxx": "c", "hpp": "c", "hh": "c", "hxx": "c",
    "cs": "cs", "java": "java", "kt": "kotlin", "kts": "kotlin", "swift": "swift",
    "go": "go", "rs": "rust", "scala": "scala", "sc": "scala", "dart": "dart",
    "css": "css", "scss": "scss", "less": "less",
    "sh": "sh", "bash": "sh", "ksh": "sh", "zsh": "zsh",
    "yaml": "yaml", "yml": "yaml", "toml": "toml",
    "html": "html", "htm": "html", "xhtml": "html", "xml": "xml", "svg": "xml",
}
JS = {"js", "jsx", "ts", "tsx"}
CFAM = {"c", "cs", "java", "kotlin", "swift", "go", "rust", "scala", "dart"}
CSS = {"css", "scss", "less"}
SH = {"sh", "zsh"}
HTML = {"html", "xml"}
ALL = JS | CFAM | CSS | SH | HTML | {"python", "yaml", "toml", "dockerfile"}
LINE_MODE = SH | {"yaml", "toml", "dockerfile"}
WS = " \t\f\v\r\n\u00a0\ufeff\u2028\u2029"


class ScanError(Exception):
    def __init__(self, msg, pos=None):
        super().__init__(msg)
        self.pos = pos


@dataclass
class Comment:
    start: int
    end: int
    kind: str                     # line | block | docstring | html
    jsx: bool = False             # a {/* */} JSX child: start and end include the braces
    protected: str | None = None
    flags: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class Scan:
    lang: str
    comments: list
    literals: list
    texts: list
    notes: list


def detect(path, head=b""):
    """Language id for a path, or None when unsupported."""
    name = os.path.basename(path)
    low = name.lower()
    if low in ("dockerfile", "containerfile") or low.startswith("dockerfile.") or low.endswith(".dockerfile"):
        return "dockerfile"
    ext = low.rsplit(".", 1)[1] if "." in low.lstrip(".") else ""
    if ext or not head.startswith(b"#!"):
        return EXT.get(ext)
    words = head[2:].split(b"\n", 1)[0].decode("utf-8", "replace").split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-")]
    prog = re.sub(r"[0-9.]+$", "", os.path.basename(words[0])) if words else ""
    return {"sh": "sh", "bash": "sh", "ksh": "sh", "dash": "sh", "zsh": "zsh",
            "python": "python", "node": "js", "deno": "ts", "bun": "ts"}.get(prog)


class Base:
    def __init__(self, text, lang):
        self.s, self.n, self.i, self.lang = text, len(text), 0, lang
        self.comments, self.literals, self.texts, self.notes = [], [], [], []

    def at(self, k=0):
        return self.s[self.i + k] if self.i + k < self.n else ""

    def fail(self, msg, pos=None):
        raise ScanError(msg, self.i if pos is None else pos)

    def eol(self, j):
        while j < self.n and self.s[j] not in "\r\n":
            j += 1
        return j

    def next_line(self, j):
        """Start of the line after the one j is on (or n)."""
        j = self.eol(j)
        return j + 2 if self.s.startswith("\r\n", j) else min(j + 1, self.n)

    def skip_escape(self, j):
        return j + 3 if self.s.startswith("\r\n", j + 1) else j + 2

    def comment(self, start, end, kind, **kw):
        self.comments.append(Comment(start, end, kind, **kw))

    def literal(self, start, end):
        if end > start:
            self.literals.append((start, end))

    def line_comment(self):
        start, self.i = self.i, self.eol(self.i)
        self.comment(start, self.i, "line")

    def block_comment(self, nested=False):
        s, start = self.s, self.i
        if not nested:
            j = s.find("*/", start + 2)
            if j < 0:
                self.fail("unterminated block comment")
            self.i = j + 2
        else:
            depth, j = 1, start + 2
            while depth:
                if j >= self.n:
                    self.fail("unterminated block comment", start)
                if s.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif s.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            self.i = j
        self.comment(start, self.i, "block")

    def quoted(self, start, close, escapes=True, multiline=False, what="string"):
        """Scan a plain quoted literal whose opening ends at start; returns its end."""
        s, j = self.s, start
        while j < self.n:
            c = s[j]
            if escapes and c == "\\":
                j = self.skip_escape(j)
                continue
            if s.startswith(close, j):
                return j + len(close)
            if c in "\r\n" and not multiline:
                break
            j += 1
        self.fail(f"unterminated {what}", start)



JS_EXPR_KEYWORDS = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await".split())
IDENT = re.compile(r"[\w$\u200c\u200d]+")
JS_NUMBER = re.compile(r"\.?\d[\w.]*")
JSX_NAME = re.compile(r"[\w$][\w$.:\-]*")
CLOSERS = {")": "(", "]": "[", "}": "{"}


class JsScanner(Base):
    def __init__(self, text, lang):
        super().__init__(text, lang)
        self.jsx = lang in ("js", "jsx", "tsx")
        self.prev = None          # None | punct | kw | value: a regex or JSX can't follow a value
        self.after_dot = False

    def run(self):
        if self.s.startswith("#!"):
            self.line_comment()
        self.code(None)
        return self

    def code(self, stop):
        s, stack = self.s, []
        while self.i < self.n:
            c, nx = s[self.i], s[self.i + 1:self.i + 2]
            if c in WS:
                self.i += 1
            elif c == "/" and nx == "/":
                self.line_comment()
            elif c == "/" and nx == "*":
                self.block_comment()
            elif c in "'\"":
                end = self.quoted(self.i + 1, c)
                self.literal(self.i, end)
                self.i, self.prev = end, "value"
            elif c == "`":
                self.template()
                self.prev = "value"
            elif c == "/":
                if self.prev == "value":
                    self.i, self.prev = self.i + 1, "punct"
                else:
                    self.regex()
                    self.prev = "value"
            elif (c == "<" and self.jsx and self.prev != "value"
                  and (nx == ">" or (nx and (nx.isalpha() or nx in "_$")))):
                self.jsx_element()
                self.prev = "value"
            elif c.isdigit() or (c == "." and nx.isdigit()):
                self.i, self.prev = JS_NUMBER.match(s, self.i).end(), "value"
            elif m := IDENT.match(s, self.i):
                word = m.group()
                self.i = m.end()
                self.prev = "kw" if word in JS_EXPR_KEYWORDS and not self.after_dot else "value"
                self.after_dot = False
            elif c in "([{":
                stack.append(c)
                self.i, self.prev = self.i + 1, "punct"
            elif c in ")]}":
                if not stack:
                    if c == stop:
                        return
                    self.fail(f"unbalanced '{c}'")
                if stack.pop() != CLOSERS[c]:
                    self.fail(f"mismatched '{c}'")
                self.i, self.prev = self.i + 1, "value"
            elif s.startswith(("++", "--"), self.i):
                self.i, self.prev = self.i + 2, "value"
            else:
                self.after_dot = c == "."
                self.i, self.prev = self.i + 1, "punct"
        if stop or stack:
            self.fail(f"missing '{stop or {'(': ')', '[': ']', '{': '}'}[stack[-1]]}'")

    def nested(self, stop):
        """Scan an embedded expression up to its closing stop char, leaving i on it."""
        self.prev = None
        self.code(stop)

    def regex(self):
        s, start, j, in_class = self.s, self.i, self.i + 1, False
        while j < self.n and s[j] not in "\r\n":
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            elif c == "/" and not in_class:
                m = IDENT.match(s, j + 1)
                self.i = m.end() if m else j + 1
                self.literal(start, self.i)
                return
            j += 1
        self.fail("unterminated regular expression", start)

    def template(self):
        s, piece, j = self.s, self.i, self.i + 1
        while j < self.n:
            if s[j] == "\\":
                j += 2
            elif s[j] == "`":
                self.literal(piece, j + 1)
                self.i = j + 1
                return
            elif s.startswith("${", j):
                self.literal(piece, j + 2)
                self.i = j + 2
                self.nested("}")
                piece, j = self.i, self.i + 1
            else:
                j += 1
        self.fail("unterminated template literal", piece)

    def skip_tag_space(self):
        while self.i < self.n:
            if self.s[self.i] in WS:
                self.i += 1
            elif self.s.startswith("//", self.i):
                self.line_comment()
            elif self.s.startswith("/*", self.i):
                self.block_comment()
            else:
                return

    def jsx_element(self):
        s = self.s
        self.i += 1
        self.skip_tag_space()
        if self.at() == ">":
            self.i += 1
            self.jsx_children("")
            return
        m = JSX_NAME.match(s, self.i)
        if not m:
            self.fail("can't parse JSX tag")
        name, self.i = m.group(), m.end()
        while True:
            self.skip_tag_space()
            c = self.at()
            if not c:
                self.fail(f"unterminated JSX tag <{name}>")
            if s.startswith("/>", self.i):
                self.i += 2
                return
            if c == ">":
                self.i += 1
                self.jsx_children(name)
                return
            if c == "{":
                self.i += 1
                self.nested("}")
                self.i += 1
                continue
            m = JSX_NAME.match(s, self.i)
            if not m:
                self.fail(f"can't parse JSX attribute in <{name}>")
            self.i = m.end()
            self.skip_tag_space()
            if self.at() != "=":
                continue
            self.i += 1
            self.skip_tag_space()
            c = self.at()
            if c in ("'", '"'):
                end = s.find(c, self.i + 1)
                if end < 0:
                    self.fail("unterminated JSX attribute")
                self.literal(self.i, end + 1)
                self.i = end + 1
            elif c == "{":
                self.i += 1
                self.nested("}")
                self.i += 1
            elif c == "<":
                self.jsx_element()
            else:
                self.fail(f"can't parse JSX attribute value in <{name}>")

    def jsx_children(self, name):
        s, text = self.s, self.i
        while self.i < self.n:
            c = s[self.i]
            if c == "{":
                self.jsx_text(text, self.i)
                self.jsx_brace()
                text = self.i
            elif c == "<":
                self.jsx_text(text, self.i)
                j = self.i + 1
                while j < self.n and s[j] in WS:
                    j += 1
                if s.startswith("/", j):
                    k = s.find(">", j)
                    if k < 0 or s[j + 1:k].strip() != name:
                        self.fail(f"JSX closing tag doesn't match <{name}>")
                    self.i = k + 1
                    return
                self.jsx_element()
                text = self.i
            else:
                self.i += 1
        self.fail(f"unterminated JSX element <{name}>")

    def jsx_text(self, a, b):
        if b > a:
            self.texts.append((a, b))

    def jsx_brace(self):
        """A {...} child. One comment alone in the braces is a single unit."""
        s, start, j = self.s, self.i, self.i + 1
        while j < self.n and s[j] in WS:
            j += 1
        block = s.startswith("/*", j)
        if block or s.startswith("//", j):
            k = s.find("*/", j + 2) + 2 if block else self.eol(j)
            if k > 1:
                e = k
                while e < self.n and s[e] in WS:
                    e += 1
                if s.startswith("}", e):
                    self.comment(start, e + 1, "block" if block else "line", jsx=True)
                    self.i = e + 1
                    return
        self.i += 1
        self.nested("}")
        self.i += 1



NESTING = {"rust", "swift", "kotlin", "scala", "dart"}
C_NUMBER = re.compile(r"\.?\d(?:[\w.']|(?<=[eEpP])[+-])*")
OTHER_NUMBER = re.compile(r"\.?\d(?:[\w.]|(?<=[eE])[+-])*")
HOLES = {"kotlin": "${", "dart": "${", "swift": "\\("}
TRIPLE = {"java", "kotlin", "swift", "scala", "dart", "cs"}
CPP_RAW = re.compile(r'(?:u8|[uUL])?R"([^ ()\\\t\v\f\r\n]{0,16})\(')
PP_OPAQUE = re.compile(r"[ \t]*#[ \t]*(error|warning|include|include_next|import|if[ \t]+0\b)")


class CScanner(Base):
    def run(self):
        if self.lang == "rust" and self.s.startswith("#!") and not self.s.startswith("#!["):
            self.line_comment()
        if self.lang == "c":
            self.i = self.preprocessor(0)
        self.code(None)
        return self

    def code(self, stop):
        s, L, stack = self.s, self.lang, []
        strict = L != "c"
        while self.i < self.n:
            c, nx = s[self.i], s[self.i + 1:self.i + 2]
            if c in WS:
                self.i += 1
                if c == "\n" and L == "c":
                    self.i = self.preprocessor(self.i)
            elif c == "/" and nx == "/":
                self.c_line_comment()
            elif c == "/" and nx == "*":
                self.block_comment(nested=L in NESTING)
            elif c == '"':
                self.dquote(self.i)
            elif c == "'":
                self.squote()
            elif c == "`":
                self.backtick()
            elif L == "cs" and c in "@$" and self.cs_string():
                pass
            elif L == "swift" and c == "#" and re.match(r'#+"', s[self.i:self.i + 20]):
                self.swift_raw()
            elif c.isdigit() or (c == "." and nx.isdigit()):
                self.i = (C_NUMBER if L == "c" else OTHER_NUMBER).match(s, self.i).end()
            elif m := IDENT.match(s, self.i):
                self.word(m)
            elif c in "([{":
                stack.append(c)
                self.i += 1
            elif c in ")]}":
                if not stack:
                    if c == stop:
                        return
                    if strict:
                        self.fail(f"unbalanced '{c}'")
                elif stack.pop() != CLOSERS[c] and strict:
                    self.fail(f"mismatched '{c}'")
                self.i += 1
            else:
                self.i += 1
        if stop:
            self.fail(f"missing '{stop}'")
        if stack and strict:
            self.fail("unbalanced brackets")

    def preprocessor(self, j):
        """At a C line start: #error/#include lines and #if 0 blocks are opaque.
        Returns where scanning resumes."""
        s = self.s
        m = PP_OPAQUE.match(s, j)
        if not m:
            return j
        if not m.group(1).startswith("if"):
            end = self.eol(j)
            self.literal(m.end(), end)
            return end
        depth, k = 0, j
        while k < self.n:
            d = re.match(r"[ \t]*#[ \t]*(if|ifdef|ifndef|endif|else|elif)\b", s[k:k + 40])
            if d and d.group(1).startswith("if"):
                depth += 1
            elif d and depth == 1:
                self.literal(j, k)
                return k
            elif d and d.group(1) == "endif":
                depth -= 1
            k = self.next_line(k)
        self.fail("unterminated #if 0", j)

    def c_line_comment(self):
        start, j = self.i, self.eol(self.i)
        while self.lang == "c" and j > start and self.s[j - 1] == "\\" and j < self.n:
            j = self.eol(self.next_line(j))
        self.i = j
        self.comment(start, j, "line")

    def word(self, m):
        s, L, w, j = self.s, self.lang, m.group(), m.end()
        nx = s[j:j + 1]
        self.i = j
        if L == "rust" and w in ("r", "br", "cr") and nx in ('"', "#"):
            k = j
            while k < self.n and s[k] == "#":
                k += 1
            if s.startswith('"', k):
                close = '"' + "#" * (k - j)
                end = s.find(close, k + 1)
                if end < 0:
                    self.fail("unterminated raw string", m.start())
                self.i = end + len(close)
                self.literal(m.start(), self.i)
        elif L == "rust" and w in ("b", "c") and nx == '"':
            self.dquote(m.start(), j)
        elif L == "rust" and w == "b" and nx == "'":
            end = self.quoted(j + 1, "'", what="byte literal")
            self.literal(m.start(), end)
            self.i = end
        elif L == "c" and nx == '"' and (r := CPP_RAW.match(s, m.start())) and r.end() > j:
            close = ")" + r.group(1) + '"'
            end = s.find(close, r.end())
            if end < 0:
                self.fail("unterminated raw string", m.start())
            self.i = end + len(close)
            self.literal(m.start(), self.i)
        elif L == "dart" and w == "r" and nx in ("'", '"'):
            q = s[j:j + 3] if s[j:j + 3] in ("'''", '"""') else nx
            end = self.quoted(j + len(q), q, escapes=False, multiline=len(q) == 3)
            self.literal(m.start(), end)
            self.i = end
        elif L == "scala" and nx == '"':
            self.dquote(m.start(), j, hole="${")

    def dquote(self, start, j=None, hole=None):
        s, L = self.s, self.lang
        j = start if j is None else j
        if s.startswith('"""', j) and L in TRIPLE:
            if L == "cs":
                self.cs_raw(start, j)
                return
            escapes = L in ("java", "swift", "dart")
            self.interpolated(start, j + 3, '"""', escapes, hole or HOLES.get(L), multiline=True,
                              extra=L in ("kotlin", "scala"))
            return
        self.interpolated(start, j + 1, '"', True, hole or HOLES.get(L))

    def squote(self):
        s, L, start = self.s, self.lang, self.i
        if L == "dart":
            q = "'''" if s.startswith("'''", start) else "'"
            self.interpolated(start, start + len(q), q, True, "${", multiline=len(q) == 3)
        elif L in ("rust", "scala"):
            nx, after = s[start + 1:start + 2], s[start + 2:start + 3]
            if nx == "\\" or (after == "'" and nx not in ("", "'")):
                end = self.quoted(start + 1, "'", what="char literal")
                self.literal(start, end)
                self.i = end
            else:
                self.i += 1
        elif L == "swift":
            self.i += 1
        else:
            end = self.quoted(start + 1, "'", what="char literal")
            self.literal(start, end)
            self.i = end

    def backtick(self):
        s, L, start = self.s, self.lang, self.i
        if L == "go":
            end = s.find("`", start + 1)
            if end < 0:
                self.fail("unterminated raw string")
            self.literal(start, end + 1)
            self.i = end + 1
        elif L in ("kotlin", "scala", "swift"):
            self.i = self.quoted(start + 1, "`", escapes=False, what="quoted identifier")
        else:
            self.i += 1

    def interpolated(self, start, j, close, escapes, hole, multiline=False, extra=False,
                     doubled=False):
        """A string from start whose body begins at j; holes are scanned as code."""
        s, piece = self.s, start
        while j < self.n:
            c = s[j]
            if escapes and c == "\\":
                if hole == "\\(" and s.startswith("(", j + 1):
                    self.literal(piece, j + 2)
                    self.i = j + 2
                    self.code(")")
                    piece, j = self.i, self.i + 1
                    continue
                j = self.skip_escape(j)
                continue
            if doubled and s.startswith(close * 2, j):
                j += 2
                continue
            if s.startswith(close, j):
                j += len(close)
                while extra and s.startswith('"', j):
                    j += 1
                self.literal(piece, j)
                self.i = j
                return
            if hole == "${" and s.startswith("${", j) or hole == "{" and c == "{":
                if hole == "{" and s.startswith("{{", j):
                    j += 2
                    continue
                self.literal(piece, j + len(hole))
                self.i = j + len(hole)
                self.code("}")
                piece, j = self.i, self.i + 1
                continue
            if hole == "{" and s.startswith("}}", j):
                j += 2
                continue
            if c in "\r\n" and not multiline:
                break
            j += 1
        self.fail("unterminated string", start)

    def cs_string(self):
        s, i = self.s, self.i
        m = re.match(r'(\$+@?|@\$+|@)(")', s[i:i + 12])
        if not m:
            return False
        prefix, j = m.group(1), i + m.start(2)
        if s.startswith('"""', j):
            self.cs_raw(i, j)
        elif "$" in prefix:
            self.interpolated(i, j + 1, '"', "@" not in prefix, "{", multiline="@" in prefix,
                              doubled="@" in prefix)
        else:
            self.interpolated(i, j + 1, '"', False, None, multiline=True, doubled=True)
        return True

    def cs_raw(self, start, j):
        k = j
        while k < self.n and self.s[k] == '"':
            k += 1
        close = '"' * (k - j)
        end = self.s.find(close, k)
        if end < 0:
            self.fail("unterminated raw string", start)
        self.i = end + len(close)
        self.literal(start, self.i)

    def swift_raw(self):
        s, start = self.s, self.i
        hashes = len(re.match(r"#+", s[start:]).group())
        j = start + hashes
        q = '"""' if s.startswith('"""', j) else '"'
        close = q + "#" * hashes
        end = s.find(close, j + len(q))
        if end < 0:
            self.fail("unterminated raw string")
        self.i = end + len(close)
        self.literal(start, self.i)



URL = re.compile(r"url\(\s*", re.I)


class CssScanner(Base):
    def run(self):
        s, stack = self.s, []
        while self.i < self.n:
            c = s[self.i]
            if s.startswith("/*", self.i):
                self.block_comment()
            elif s.startswith("//", self.i) and self.lang in ("scss", "less"):
                self.line_comment()
            elif c in "'\"":
                end = self.quoted(self.i + 1, c)
                self.literal(self.i, end)
                self.i = end
            elif (m := URL.match(s, self.i)) and not re.match(r"[\w-]", s[self.i - 1:self.i] or " ") \
                    and s[m.end():m.end() + 1] not in ("'", '"'):
                end = s.find(")", m.end())
                if end < 0:
                    self.fail("unterminated url(")
                self.literal(self.i, end + 1)
                self.i = end + 1
            elif c == "\\":
                self.i += 2
            elif c in "([{":
                stack.append(c)
                self.i += 1
            elif c in ")]}":
                if not stack or stack.pop() != CLOSERS[c]:
                    self.fail(f"unbalanced '{c}'")
                self.i += 1
            else:
                self.i += 1
        if stack:
            self.fail("unbalanced brackets")
        return self



HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|<>()'\"]+))")
SH_SPECIAL = set("#?$!@*-0123456789_")


class ShScanner(Base):
    def __init__(self, text, lang):
        super().__init__(text, lang)
        self.heredocs = []
        self.quiet = 0

    def run(self):
        if self.s.startswith("#!"):
            self.line_comment()
        self.code(None)
        if self.heredocs:
            self.fail("unterminated heredoc")
        return self

    def comment(self, start, end, kind, **kw):
        if not self.quiet:
            super().comment(start, end, kind, **kw)

    def literal(self, start, end):
        if not self.quiet:
            super().literal(start, end)

    def code(self, stop, arith=False):
        s, depth, word_start = self.s, 0, True
        while self.i < self.n:
            c = s[self.i]
            if c == "\n":
                self.i += 1
                if self.heredocs:
                    self.read_heredocs()
                word_start = True
            elif c in " \t\r":
                self.i += 1
                word_start = True
            elif c == "#" and word_start and not arith:
                self.line_comment()
            elif c == "\\":
                if s.startswith("\n", self.i + 1) or s.startswith("\r\n", self.i + 1):
                    self.i = self.skip_escape(self.i)
                else:
                    self.i += 2
                    word_start = False
            elif c in "'\"`" or c == "$":
                self.expansion()
                word_start = False
            elif s.startswith("<<<", self.i):
                self.i, word_start = self.i + 3, True
            elif c == "<" and s.startswith("<<", self.i) and not arith:
                m = HEREDOC.match(s, self.i)
                if not m:
                    self.fail("can't parse heredoc")
                delim = next(g for g in m.group(2, 3, 4) if g is not None)
                self.heredocs.append((delim, m.group(1) == "-"))
                self.i, word_start = m.end(), True
            elif c == "(" and word_start and s.startswith("((", self.i):
                self.i += 2
                self.code(")", arith=True)
                if not s.startswith("))", self.i):
                    self.fail("unterminated (( ))")
                self.i += 2
                word_start = False
            elif c in ";&|()<>":
                if c == ")" and depth == 0 and stop == ")":
                    return
                depth += {"(": 1, ")": -1}.get(c, 0)
                self.i += 1
                word_start = not (c == "(" and self.lang == "zsh")
            else:
                self.i += 1
                word_start = False
        if stop:
            self.fail(f"missing '{stop}'")

    def expansion(self):
        """A quote, backtick or $-expansion at i; records it as one literal."""
        s, start = self.s, self.i
        self.quiet += 1
        try:
            c = s[start]
            if c == "'":
                self.i = self.quoted(start + 1, "'", escapes=False, multiline=True)
            elif s.startswith("$'", start):
                self.i = self.quoted(start + 2, "'", multiline=True)
            elif c == '"' or s.startswith('$"', start):
                self.double_quoted(start + (2 if c == "$" else 1))
            elif c == "`":
                self.i = self.quoted(start + 1, "`", multiline=True, what="backquote")
            elif s.startswith("$((", start):
                self.i = start + 3
                self.code(")", arith=True)
                if not s.startswith("))", self.i):
                    self.fail("unterminated $(( ))", start)
                self.i += 2
            elif s.startswith("$(", start):
                self.quiet -= 1
                try:
                    self.i = start + 2
                    self.code(")")
                    self.i += 1
                finally:
                    self.quiet += 1
                return
            elif s.startswith("${", start):
                self.braced(start + 2)
            else:
                self.i = start + (2 if s[start + 1:start + 2] in SH_SPECIAL else 1)
                return
        finally:
            self.quiet -= 1
        self.literal(start, self.i)

    def double_quoted(self, j):
        s = self.s
        while j < self.n:
            c = s[j]
            if c == "\\":
                j = self.skip_escape(j)
            elif c == '"':
                self.i = j + 1
                return
            elif c == "`" or (c == "$" and s[j + 1:j + 2] in ("(", "{")):
                self.i = j
                self.expansion()
                j = self.i
            else:
                j += 1
        self.fail("unterminated string")

    def braced(self, j):
        s, depth = self.s, 1
        while j < self.n:
            c = s[j]
            if c == "\\":
                j = self.skip_escape(j)
            elif c in "'\"`" or (c == "$" and s[j + 1:j + 2] in ("(", "{", "'", '"')):
                self.i = j
                self.expansion()
                j = self.i
            elif c == "{":
                depth, j = depth + 1, j + 1
            elif c == "}":
                depth, j = depth - 1, j + 1
                if not depth:
                    self.i = j
                    return
            else:
                j += 1
        self.fail("unterminated ${")

    def read_heredocs(self):
        pending, self.heredocs = self.heredocs, []
        for delim, strip_tabs in pending:
            start = j = self.i
            while True:
                if j >= self.n:
                    self.fail(f"unterminated heredoc {delim}", start)
                end = self.eol(j)
                line = self.s[j:end]
                if (line.lstrip("\t") if strip_tabs else line) == delim:
                    self.literal(start, end)
                    self.i = self.next_line(j)
                    break
                j = self.next_line(j)



BLOCK_HEADER = re.compile(r"(?:^|[ \t])([|>])([1-9][+-]?|[+-][1-9]?)?$")


class YamlScanner(Base):
    def __init__(self, text, lang):
        super().__init__(text, lang)
        self.keep_boundaries = []

    def run(self):
        s, pos, block, quote = self.s, 0, None, None
        while pos < self.n:
            eol, nxt = self.eol(pos), self.next_line(pos)
            line = s[pos:eol]
            if block is not None:
                ind = len(line) - len(line.lstrip(" "))
                if not line.strip():
                    pos = nxt
                    continue
                if block["indent"] is None and ind > block["parent"]:
                    block["indent"] = ind
                if block["indent"] is not None and ind >= block["indent"]:
                    block["last"], pos = eol, nxt
                    continue
                if block["last"] is not None:
                    self.literal(block["start"], block["last"])
                ends_keep, block = block["keep"], None
                if ends_keep and line.lstrip(" ").startswith("#"):
                    self.keep_boundaries.append(pos)
            quote, block = self.line(pos, eol, nxt, quote)
            pos = nxt
        if quote:
            self.fail("unterminated quoted scalar", quote[1])
        if block is not None and block["last"] is not None:
            self.literal(block["start"], block["last"])
        return self

    def line(self, pos, eol, nxt, quote):
        s, j, code_end = self.s, pos, eol
        if quote:
            end = self.close_quote(quote[0], j, eol)
            if end is None:
                return quote, None
            self.literal(quote[1], end)
            j = end
        while j < eol:
            c = s[j]
            if c == "#" and (j == pos or s[j - 1] in " \t"):
                self.comment(j, eol, "line")
                code_end = j
                break
            if c in "'\"" and self.quote_ok(pos, j):
                end = self.close_quote(c, j + 1, eol)
                if end is None:
                    return (c, j), None
                self.literal(j, end)
                j = end
                continue
            j += 1
        code = s[pos:code_end].rstrip(" \t")
        m = BLOCK_HEADER.search(code)
        if not m:
            return None, None
        before = code[:m.start(1)].rstrip()
        bare = before.strip()
        last = bare.split()[-1] if bare else ""
        if bare and not (before.endswith((":", "?", "---")) or last == "-" or last[0] in "!&"):
            return None, None
        if before.endswith(":"):
            parent = len(re.match(r"[ ]*(?:-[ ]+)*", code).group())
        elif last == "-":
            parent = len(before) - 1
        else:
            parent = len(code) - len(code.lstrip(" ")) if bare else -1
        digits = re.sub(r"\D", "", m.group(2) or "")
        return None, {"indent": parent + int(digits) if digits else None, "parent": parent,
                      "keep": "+" in (m.group(2) or ""), "start": nxt, "last": None}

    def quote_ok(self, pos, j):
        s, k = self.s, j - 1
        while k >= pos and s[k] in " \t":
            k -= 1
        if k < pos or s[k] in "[{,":
            return True
        if s[k] in ":?-" and k + 1 < j:
            return s[k] != "-" or k == pos or s[k - 1] in " \t"
        t = k
        while t >= pos and s[t] not in " \t":
            t -= 1
        return s[t + 1] in "!&" and (t < pos or s[t] in " \t")

    def close_quote(self, q, j, eol):
        """End of a quoted scalar starting before j, or None if it continues past eol."""
        s = self.s
        while j < eol:
            if q == '"' and s[j] == "\\":
                j += 2
            elif s[j] == q:
                if q == "'" and s.startswith("''", j):
                    j += 2
                else:
                    return j + 1
            else:
                j += 1
        return None


class TomlScanner(Base):
    def run(self):
        s = self.s
        while self.i < self.n:
            c = s[self.i]
            if c == "#":
                self.line_comment()
                continue
            for q, esc, multi in (('"""', True, True), ("'''", False, True),
                                  ('"', True, False), ("'", False, False)):
                if s.startswith(q, self.i):
                    end = self.quoted(self.i + len(q), q, escapes=esc, multiline=multi)
                    extra = 0
                    while multi and extra < 2 and s.startswith(q[0], end):
                        end, extra = end + 1, extra + 1
                    self.literal(self.i, end)
                    self.i = end
                    break
            else:
                self.i += 1
        return self


DOCKER_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)([\"']?)([A-Za-z_][\w.-]*)\2")
DOCKER_DIRECTIVE = re.compile(r"#[ \t]*([A-Za-z][\w-]*)[ \t]*=[ \t]*(\S*)")


class DockerScanner(Base):
    def run(self):
        s, pos, pending, instr, cont, escape = self.s, 0, [], "", False, "\\"
        directives = True
        while pos < self.n:
            eol, nxt = self.eol(pos), self.next_line(pos)
            line = s[pos:eol]
            body = line.lstrip(" \t")
            if directives:
                m = DOCKER_DIRECTIVE.fullmatch(line.strip())
                if m and m.group(1).lower() == "escape" and m.group(2) in ("`", "\\"):
                    escape = m.group(2)
                directives = bool(m)
            if body.startswith("#"):
                self.comment(eol - len(body), eol, "line")
                pos = nxt
                continue
            if body and not cont:
                instr = body.split(None, 1)[0].upper()
            if instr in ("RUN", "COPY", "ADD"):
                pending += [(m.group(3), m.group(1) == "-") for m in DOCKER_HEREDOC.finditer(line)]
            cont = bool(body) and line.rstrip().endswith(escape) or (cont and not body)
            pos = nxt
            if pending and not cont:
                for delim, strip_tabs in pending:
                    start = pos
                    while True:
                        if pos >= self.n:
                            self.fail(f"unterminated heredoc {delim}", start)
                        end = self.eol(pos)
                        text = s[pos:end]
                        pos = self.next_line(pos)
                        if (text.lstrip("\t") if strip_tabs else text) == delim:
                            self.literal(start, end)
                            break
                pending = []
        if pending:
            self.fail("unterminated heredoc")
        return self



TAG = re.compile(r"</?([A-Za-z][\w:.-]*)")


class HtmlScanner(Base):
    def run(self):
        s = self.s
        raw = ("script", "style") if self.lang == "xml" else ("script", "style", "textarea", "title")
        while self.i < self.n:
            i = self.i
            if s.startswith("<!--", i):
                if s.startswith("<!-->", i) or s.startswith("<!--->", i):
                    end = s.find(">", i + 4) + 1
                else:
                    end = s.find("-->", i + 4)
                    if end < 0:
                        self.fail("unterminated comment")
                    end += 3
                self.comment(i, end, "html")
                self.i = end
            elif s.startswith("<![CDATA[", i):
                end = s.find("]]>", i)
                if end < 0:
                    self.fail("unterminated CDATA")
                self.literal(i, end + 3)
                self.i = end + 3
            elif s.startswith(("<?", "<!"), i):
                end = s.find(">", i)
                if end < 0:
                    self.fail("unterminated markup declaration")
                self.i = end + 1
            elif m := TAG.match(s, i):
                end = self.tag_end(m.end())
                name = m.group(1).lower()
                self.i = end
                if s[i + 1] != "/" and name in raw and s[end - 2] != "/":
                    close = re.compile(rf"</{name}\b", re.I).search(s, end)
                    if not close:
                        self.fail(f"unterminated <{name}>")
                    self.literal(end, close.start())
                    if s[end:close.start()].strip():
                        self.notes.append(f"<{name}> contents are not scanned")
                    self.i = close.start()
            else:
                self.i += 1
        return self

    def tag_end(self, j):
        s = self.s
        while j < self.n:
            c = s[j]
            if c in "'\"":
                k = s.find(c, j + 1)
                if k < 0:
                    self.fail("unterminated attribute value", j)
                j = k + 1
            elif c == ">":
                return j + 1
            else:
                j += 1
        self.fail("unterminated tag")



def lines_of(text):
    """Lines split on \\n only, keeping their endings (str.splitlines splits on more)."""
    out = text.split("\n")
    return [l + "\n" for l in out[:-1]] + ([out[-1]] if out[-1] else [])


def line_starts(text):
    starts = [0]
    for l in lines_of(text):
        starts.append(starts[-1] + len(l))
    return starts


def py_tokens(text):
    lines = iter(lines_of(text))
    try:
        return list(tokenize.generate_tokens(lambda: next(lines, "")))
    except (tokenize.TokenError, SyntaxError) as e:
        raise ScanError(f"python tokenize: {e}")


def py_docstrings(text, tree=None):
    """(node, owner, body, start, end) per docstring; offsets in characters."""
    lines = lines_of(text)
    starts = line_starts(text)
    if tree is None:
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            raise ScanError(f"python syntax error: {e.msg}", None)

    def off(line, col):
        return starts[line - 1] + len(lines[line - 1].encode()[:col].decode())

    out = []

    def visit(node, owner):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            d = body[0]
            out.append((node, owner, body, off(d.lineno, d.col_offset), off(d.end_lineno, d.end_col_offset)))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, f"{owner}.{child.name}" if owner else child.name)
            elif not isinstance(child, (ast.expr, ast.Lambda)):
                visit(child, owner)

    visit(tree, "")
    return out


class PyScanner(Base):
    def run(self):
        s = self.s
        starts = line_starts(s)
        for tok in py_tokens(s):
            if tok.type == tokenize.COMMENT:
                a = starts[tok.start[0] - 1] + tok.start[1]
                self.comment(a, a + len(tok.string), "line")
        for node, owner, body, a, b in py_docstrings(s):
            public = not any(p.startswith("_") and not p.startswith("__") for p in owner.split(".") if p)
            self.comment(a, b, "docstring", meta={
                "owner": owner or "<module>", "public": public,
                "only_statement": len(body) == 1 and not isinstance(node, ast.Module)})
        return self


SCANNERS = {**{l: JsScanner for l in JS}, **{l: CScanner for l in CFAM}, **{l: CssScanner for l in CSS},
            **{l: ShScanner for l in SH}, **{l: HtmlScanner for l in HTML}, "python": PyScanner,
            "yaml": YamlScanner, "toml": TomlScanner, "dockerfile": DockerScanner}


def scan(text, lang):
    if lang not in SCANNERS:
        raise ScanError(f"unsupported language {lang}")
    if "\r" in text.replace("\r\n", ""):
        raise ScanError("bare CR line endings")
    off = 1 if text.startswith("\ufeff") else 0
    sc = SCANNERS[lang](text[off:], lang).run()
    for c in sc.comments:
        c.start, c.end = c.start + off, c.end + off
    literals = [(a + off, b + off) for a, b in sc.literals]
    texts = [(a + off, b + off) for a, b in sc.texts]
    comments = sorted(sc.comments, key=lambda c: c.start)
    spans = sorted([(c.start, c.end) for c in comments] + literals)
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        if a2 < b1:
            raise ScanError("overlapping spans (scanner bug)", a2)
    for b in getattr(sc, "keep_boundaries", []):
        for c in comments:
            if c.start >= b + off and text[b + off:c.start].strip() == "":
                c.meta["keep_boundary"] = True
    result = Scan(lang, comments, literals, texts, sorted(set(sc.notes)))
    classify(text, result)
    return result



def rx(p, flags=0):
    return re.compile(p, flags)


SUPP = rx(r"(?:#|//|--|\s-\s|\u2014|\):)\s*[A-Za-z]{2,}")
TS_SUPP = rx(r"[\s:\-]*[A-Za-z]{2,}")
DASH_SUPP = rx(r"\s--\s*\S")
BIOME_SUPP = rx(r":\s*[^<\s]")

# (languages, pattern matched at the start of a body line, label, reason pattern or None)
DIRECTIVES = [
    (ALL, rx(r"prettier-ignore\b"), "prettier-ignore", None),
    (ALL, rx(r"biome-ignore(?:-all|-start|-end)?\b"), "biome-ignore", BIOME_SUPP),
    (ALL, rx(r"SPDX-(?:License-Identifier|FileCopyrightText)\b"), "legal: SPDX", None),
    (ALL, rx(r"(?:NOSONAR|nosemgrep|gitleaks:allow|trunk-ignore|lgtm\[|codeql\[)"), "scanner suppression", SUPP),
    (ALL, rx(r"(?:pragma:\s*allowlist\s+secret|cspell:|spell-checker:|codespell:ignore|@formatter:(?:off|on)|language=\w)"), "tool directive", None),
    (ALL, rx(r"noinspection\b"), "noinspection", SUPP),
    (ALL, rx(r"(?:(?:vim?|ex):\s*(?:set?\s+)?\w+[=:]|.*-\*-.*-\*-)"), "editor modeline", None),
    (ALL, rx(r"(?:x-release-please-|renovate:\s)"), "release tooling marker", None),
    (JS, rx(r"eslint-disable(?:-next-line|-line)?\b"), "eslint-disable", DASH_SUPP),
    (JS, rx(r"(?:eslint-enable\b|eslint-env\s|eslint\s+[\w/@-]+\s*:|jshint\s|jscs:|tslint:)"), "linter config", None),
    (JS, rx(r"(?:globals?|exported)\s+[\w$]+(?:\s*:\s*\w+)?(?:\s*,\s*[\w$]+(?:\s*:\s*\w+)?)*\s*$"), "linter globals", None),
    (JS, rx(r"@ts-(?:expect-error|ignore|nocheck)\b"), "@ts directive", TS_SUPP),
    (JS, rx(r"@ts-check\b"), "@ts-check", None),
    (JS | CSS, rx(r"(?:istanbul|c8|v8)\s+ignore\b"), "coverage ignore", SUPP),
    (JS, rx(r"webpack(?:ChunkName|Prefetch|Preload|Mode|Ignore|Exports|Include|Exclude|FetchPriority)\s*:"), "webpack magic comment", None),
    (JS, rx(r"@vite-ignore\b"), "vite directive", None),
    (JS, rx(r"[#@]__(?:PURE|NO_SIDE_EFFECTS|INLINE|NOINLINE|KEY|MANGLE_PROP)__"), "bundler annotation", None),
    (JS, rx(r"@jsx(?:ImportSource|Runtime|Frag)?\s+\S"), "JSX pragma", None),
    (JS, rx(r"<(?:reference|amd-module|amd-dependency)\s"), "triple-slash directive", None),
    (JS, rx(r"@(?:vitest|jest)-environment(?:-options)?\b"), "test environment docblock", None),
    (JS, rx(r"@(?:no)?flow\b"), "flow pragma", None),
    (JS, rx(r"\$Flow(?:FixMe|Ignore|ExpectedError|Issue)"), "flow suppression", TS_SUPP),
    (JS, rx(r"@refresh\s+reset\b"), "fast refresh directive", None),
    (JS | CSS, rx(r"[#@]\s*source(?:Mapping)?URL="), "source map", None),
    (JS | CSS, rx(r"@(?:license|preserve)\b"), "legal: @license", None),
    (JS, rx(r"deno-(?:lint|fmt)-ignore(?:-file)?\b"), "deno directive", SUPP),
    (CSS, rx(r"stylelint-disable(?:-next-line|-line)?\b"), "stylelint-disable", DASH_SUPP),
    (CSS, rx(r"(?:stylelint-enable\b|autoprefixer\s*:|rtl:|purgecss\s+(?:start\s+|end\s+)?ignore|webpackIgnore\s*:)"), "css tool directive", None),
    ({"python"}, rx(r"type:\s*ignore\b"), "type: ignore", SUPP),
    ({"python"}, rx(r"type:\s*\S"), "type comment", None),
    ({"python"}, rx(r"noqa\b", re.I), "noqa", SUPP),
    ({"python"}, rx(r"(?:pylint|pyright|ruff|flake8|bandit)\s*:\s*(?:disable|ignore|skip|noqa)"), "linter suppression", SUPP),
    ({"python"}, rx(r"(?:pylint|mypy|pyright|ruff|flake8|isort|yapf|autopep8|pytype|cython|pyre)\s*:"), "tool directive", None),
    ({"python"}, rx(r"pyre-(?:ignore|fixme)"), "pyre suppression", SUPP),
    ({"python"}, rx(r"pyre-(?:strict|unsafe)\b"), "pyre mode", None),
    ({"python"}, rx(r"fmt:\s*(?:off|on|skip)\b"), "fmt directive", None),
    ({"python"}, rx(r"pragma:\s*\S"), "pragma", SUPP),
    ({"python"}, rx(r"nosec\b"), "nosec", SUPP),
    ({"python"} | SH, rx(r"shellcheck\s+disable="), "shellcheck disable", SUPP),
    ({"python"} | SH, rx(r"shellcheck\s+\w+="), "shellcheck directive", None),
    (CFAM, rx(r"(?:NOLINT(?:NEXTLINE|BEGIN|END)?\b|nolint\b|cppcheck-suppress)"), "lint suppression", SUPP),
    (CFAM, rx(r"(?:clang-format\s+(?:off|on)\b|IWYU\s+pragma|NOTREACHED|LINTED|ARGSUSED|LCOV_EXCL|GCOVR_EXCL|coverity\[)"), "tool directive", None),
    (CFAM, rx(r"(?:else,?\s+|intentional(?:ly)?\s+)?fall(?:s\s?|-)?\s*thr(?:ough|u)[\s.!]*(?:-.*)?$", re.I), "fallthrough marker", None),
    (CFAM, rx(r"(?:CHECKSTYLE:|\$NON-NLS-\d+\$|NOPMD|ktlint-(?:disable|enable)|swiftlint:(?:disable|enable)|swift-format-ignore|sourcery:|ignore(?:_for_file)?:\s*\w|coverage:ignore|ReSharper\s+(?:disable|restore)|scalastyle:(?:off|on)|scalafmt:|format:\s*(?:off|on)|\$COVERAGE-(?:OFF|ON)\$)"), "tool directive", None),
    ({"rust"}, rx(r"SAFETY:"), "SAFETY comment (clippy undocumented_unsafe_blocks)", None),
    ({"yaml"}, rx(r"(?:yaml-language-server:|yamllint\s+(?:disable|enable)|\$schema[=:]|checkov:skip|kics-scan|trivy:ignore|tfsec:ignore|zizmor:\s*ignore|@schema\b)"), "yaml tool directive", None),
    ({"yaml"}, rx(r"(?:--\s|@(?:default|section|ignored?|raw|notationType)\b)"), "helm-docs annotation", None),
    ({"toml"}, rx(r":schema\s"), "taplo schema", None),
    ({"dockerfile"}, rx(r"hadolint\s+(?:ignore|global|shell)"), "hadolint directive", SUPP),
    (HTML, rx(r"(?:\[if\s|<!\[endif\]|\[endif\])"), "conditional comment", None),
    (HTML, rx(r"#\w+"), "server-side include", None),
    (HTML, rx(r"(?:/?ko(?:\s|$)|build:|endbuild\b|inject:|endinject\b|htmlhint\s|googleo(?:ff|n):|eslint-disable)"), "template/build directive", None),
]
GO_DIRECTIVE = rx(r"//(?:go:\w|line\s|export\s|extern\s|nolint\b|lint:)|//\s*\+build\s")
LEGAL = rx(r"copyright|\(c\)\s*\d{4}|\u00a9|licen[cs]e|spdx-|all rights reserved|permission is hereby granted", re.I)
COOKIE = rx(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
ADDRESSES_AGENT = rx(
    r"\b(?:ai|llms?|gpt|chatgpt|claude|copilot|codex|gemini|cursor|assistants?|agents?|language models?|bots?)\b"
    r"[^\n]{0,80}?\b(?:ignore|disregard|do\s*n[o']?t|never|must|always|keep|retain|preserve|leave|skip|approve|instructions?)\b"
    r"|\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|preceding|the)\s+(?:instructions|prompts?|rules)"
    r"|\b(?:do\s*n[o']?t|never)\s+(?:remove|delete|strip|touch|modify|edit)\s+(?:this|these|the|any)\s+comments?", re.I)
TODO = rx(r"\b(?:TODO|FIXME|HACK|XXX)\b")
TODO_OWNER = rx(r"\b(?:TODO|FIXME|HACK|XXX)\s*[(\[][^)\]]+[)\]]|@\w+|#\d+|\b[A-Z][A-Z0-9]+-\d+\b|https?://")
CHANGE_LOG = rx(r"(?:added|updated|changed|fixed|new:|now\s|as requested|removed|refactored|modified|replaced|moved|renamed|previously|changed from)\b", re.I)
BANNER = rx(r"(?:[-=*#/~_+<>]{3,}.*|.*[-=*#/~_+<>]{3,}|#?\s*(?:end)?region\b.*|MARK:.*)$")
CODEISH = rx(r"(?:(?:if|for|while|return|const|let|var|def|class|import|from|function|print|console\.\w+|await|elif|else)\b.*|.*[;{}]\s*|\w+(?:\.\w+)*\(.*\)\s*|\w+\s*=[^=].*)$")


def body_lines(raw, c):
    """The comment's text without its delimiters, one entry per line."""
    t = raw
    if c.jsx:
        t = t[1:-1].strip()
    if c.kind == "html":
        t = t[4:-3] if t.endswith("-->") else t[4:]
    elif c.kind == "docstring":
        t = re.sub(r"^[rRuUbBfF]*('''|\"\"\"|'|\")", "", t)
        t = re.sub(r"('''|\"\"\"|'|\")$", "", t)
    elif t.startswith("/*"):
        t = re.sub(r"^/\*+!?", "", t)
        t = re.sub(r"\*+/$", "", t)
    elif t.startswith("//"):
        t = re.sub(r"^//+!?", "", t)
    elif t.startswith("#"):
        t = re.sub(r"^#+", "", t, count=1) if not t.startswith("#!") else t
    out = []
    for line in t.split("\n"):
        line = line.strip()
        if c.kind == "block" and line.startswith("*"):
            line = line.lstrip("*").strip()
        out.append(line)
    return out


def classify(text, sc):
    lang, comments = sc.lang, sc.comments
    starts = line_starts(text)

    def line_no(pos):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    for c in comments:
        c.meta["line"], c.meta["end_line"] = line_no(c.start), line_no(max(c.start, c.end - 1))
        raw = text[c.start:c.end]
        lines = body_lines(raw, c)
        body = " ".join(l for l in lines if l)
        if c.start <= 1 and raw.startswith("#!"):
            c.protected = "shebang"
        elif lang == "python" and c.meta["line"] <= 2 and c.kind == "line" and COOKIE.match(raw):
            c.protected = "encoding cookie"
        elif lang == "go" and GO_DIRECTIVE.match(raw):
            c.protected = "go directive"
            if raw.startswith("//nolint") and not SUPP.search(raw[len("//nolint"):]):
                c.flags.append("needs-reason")
        elif c.meta.get("keep_boundary"):
            c.protected = "ends a keep (+) block scalar: removing it would change the value"
        elif lang == "yaml" and c.meta["line"] == 1 and raw.startswith("#cloud-config"):
            c.protected = "cloud-init header"
        else:
            for langs, pat, label, reason in DIRECTIVES:
                if lang not in langs or c.kind == "docstring":
                    continue
                for line in lines:
                    m = pat.match(line)
                    if m:
                        c.protected = label
                        if reason is not None and not reason.search(line[m.end():]):
                            c.flags.append("needs-reason")
                        break
                if c.protected:
                    break
        if ADDRESSES_AGENT.search(body):
            c.flags.append("addresses-agent")
        if c.protected:
            continue
        if TODO.search(body):
            c.flags.append("todo-with-owner" if TODO_OWNER.search(body) else "todo-without-owner")
        if c.kind != "docstring":
            if CHANGE_LOG.match(body):
                c.flags.append("change-log")
            if body == "" or BANNER.match(body):
                c.flags.append("banner")
            elif CODEISH.match(body):
                c.flags.append("looks-like-code")
        if c.kind == "docstring" and ">>>" in body:
            c.flags.append("doctest")
        if lang == "rust" and "```" in raw and raw.startswith(("///", "//!", "/**")):
            c.flags.append("doctest")

    structural(text, sc, line_no)


def structural(text, sc, line_no):
    lang, comments = sc.lang, sc.comments

    def protect(cs, why):
        for c in cs:
            if not c.protected:
                c.protected = why

    def runs(cs):
        out = []
        for c in cs:
            if out and c.meta["line"] <= out[-1][-1].meta["end_line"] + 1:
                out[-1].append(c)
            else:
                out.append([c])
        return out

    if lang == "dockerfile":
        for expected, c in enumerate(comments, 1):
            if c.meta["line"] != expected or not DOCKER_DIRECTIVE.fullmatch(text[c.start:c.end].strip()):
                break
            protect([c], "parser directive")

    if lang == "python":
        open_block = None
        for c in comments:
            raw = text[c.start:c.end]
            if c.kind == "line" and re.fullmatch(r"# /// [a-zA-Z0-9-]+", raw.rstrip()):
                open_block = [c]
            elif open_block is not None and c.kind == "line":
                open_block.append(c)
                if raw.rstrip() == "# ///":
                    protect(open_block, "inline script metadata (PEP 723)")
                    open_block = None

    if lang == "go":
        for m in re.finditer(r"(?m)^import[ \t]+\"C\"", text):
            ln = line_no(m.start())
            above = [c for c in comments if c.meta["end_line"] < ln]
            run = runs(above)[-1] if above else []
            if run and run[-1].meta["end_line"] == ln - 1:
                protect(run, "cgo preamble")

    code_start = first_code(text, comments)
    header = [c for c in comments if c.end <= code_start]
    for run in runs([c for c in header if c.kind != "docstring"]):
        if any(LEGAL.search(text[c.start:c.end]) for c in run):
            protect(run, "legal header")
    protect([c for c in header if c.kind == "docstring" and LEGAL.search(text[c.start:c.end])],
            "legal header")
    for c in comments:
        if c.protected and "needs-reason" not in c.flags:
            for f in ("todo-with-owner", "todo-without-owner", "change-log", "banner", "looks-like-code"):
                if f in c.flags:
                    c.flags.remove(f)


def first_code(text, comments):
    """Offset of the first character that is neither whitespace nor in a comment."""
    pos = 1 if text.startswith("\ufeff") else 0
    for c in comments:
        if text[pos:c.start].strip():
            break
        pos = c.end
    return pos + (len(text[pos:]) - len(text[pos:].lstrip()))
