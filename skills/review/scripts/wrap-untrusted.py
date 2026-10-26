"""Wrap author text in an <untrusted-NAME> block that the text can't close.

Usage: python3 -I wrap-untrusted.py <diff|pr-description|finding> [file] > block
Reads the file, or stdin without one, and writes
"<untrusted-NAME>\n<content>\n</untrusted-NAME>\n" to stdout.

The content is NFKC-normalised first, so look-alike characters such as a
full-width "＜" become the ASCII they stand for and can't hide a tag from the
match below. The wrapped text is therefore the normalised text, not the exact
bytes: a reviewer that needs those reads the file itself. Then every "&"
becomes "&amp;", so text that already reads "&lt;/untrusted-" can't be
decoded back into a real tag, and every opening or closing untrusted tag, in
any case and with any spacing (regex <\\s*/?\\s*untrusted-, case-insensitive),
has its "<" replaced with "&lt;", so it can't end the block early or open a
fake one.

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

text = unicodedata.normalize("NFKC", data.decode("utf-8", "surrogateescape"))
text, count = TAG.subn("&lt;", text.replace("&", "&amp;"))
if text and not text.endswith("\n"):
    text += "\n"
sys.stdout.buffer.write(
    f"<untrusted-{name}>\n{text}</untrusted-{name}>\n".encode("utf-8", "surrogateescape"))
print(count, file=sys.stderr)
