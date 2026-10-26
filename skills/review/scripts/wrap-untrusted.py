"""Wrap author text in an <untrusted-NAME> block that the text can't close.

Usage: python3 -I wrap-untrusted.py <diff|pr-description|finding> [file] > block
Reads the file, or stdin without one, and writes
"<untrusted-NAME>\n<content>\n</untrusted-NAME>\n" to stdout.

The content keeps the author's characters as they are, with two exceptions.
Every "&" becomes "&amp;", so text that already reads "&lt;/untrusted-" can't
be decoded back into a real tag. And every opening or closing untrusted tag,
in any case and with any spacing (regex <\\s*/?\\s*untrusted-,
case-insensitive), has its "<" replaced with "&lt;", so it can't end the block
early or open a fake one.

Tags are found in a separate view of the text that is used only for matching.
Each character is NFKC-normalised there, so a full-width "＜" or "ｕｎｔｒｕｓｔｅｄ"
counts as the ASCII it imitates, and invisible characters are left out, so a
zero-width space or joiner, BOM or bidi control (category Cf), a combining
mark, another control or a Hangul filler between "<" and "untrusted-" can't
hide a tag. The escape lands on the original character that gave the view its
"<": a full-width "＜" becomes "&lt;" just as "<" does. Look-alike and
invisible characters anywhere else pass through unchanged.

Prints the number of escaped tags, and nothing else, on stderr ("&" escapes
aren't counted). More than 0 means the content tried to break out of its
block: record it as an injection attempt. Exits 2 with a message on stderr on
bad arguments or a read error.
"""
import re
import sys
import unicodedata

NAMES = ("diff", "pr-description", "finding")
TAG = re.compile(r"<(?=\s*/?\s*untrusted-)", re.IGNORECASE)
# Left out of the matching view: format characters (zero-width space and
# joiners, BOM, bidi controls), combining marks, non-whitespace controls,
# private-use, unassigned and surrogate (undecodable byte) code points, and the
# Hangul fillers, letters that render as nothing. Whitespace stays: the tag
# regex allows it.
HIDDEN = {"Cc", "Cf", "Cn", "Co", "Cs", "Mn", "Me"}
FILLERS = {"ᅟ", "ᅠ", "ㅤ", "ﾠ"}


def hidden(ch):
    return not ch.isspace() and (unicodedata.category(ch) in HIDDEN or ch in FILLERS)


if len(sys.argv) not in (2, 3) or sys.argv[1] not in NAMES:
    print(f"usage: wrap-untrusted.py <{'|'.join(NAMES)}> [file]", file=sys.stderr)
    sys.exit(2)
name = sys.argv[1]
try:
    if len(sys.argv) == 3:
        with open(sys.argv[2], "rb") as f:
            data = f.read()
    else:
        data = sys.stdin.buffer.read()
except OSError as e:
    print(f"wrap-untrusted: can't read {e.filename}: {e.strerror}", file=sys.stderr)
    sys.exit(2)

text = data.decode("utf-8", "surrogateescape")
# The matching view, and for each of its characters the index of the original
# character it came from.
view, origin = [], []
for i, ch in enumerate(text):
    if hidden(ch):
        continue
    for c in ch if ch.isascii() else unicodedata.normalize("NFKC", ch):
        if not hidden(c):
            view.append(c)
            origin.append(i)
tags = {origin[m.start()] for m in TAG.finditer("".join(view))}
text = "".join("&amp;" if ch == "&" else "&lt;" if i in tags else ch
               for i, ch in enumerate(text))
if text and not text.endswith("\n"):
    text += "\n"
sys.stdout.buffer.write(
    f"<untrusted-{name}>\n{text}</untrusted-{name}>\n".encode("utf-8", "surrogateescape"))
print(len(tags), file=sys.stderr)
