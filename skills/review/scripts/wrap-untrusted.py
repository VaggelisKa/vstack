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
counts as the ASCII it imitates, then case-folded and, if it is a known
look-alike of a character the tag needs (Cyrillic "е", Greek "υ", a dash, a
division slash, "‹"), replaced by that ASCII character. Invisible characters
are left out, so a zero-width space or joiner, BOM or bidi control (category
Cf), a combining mark, another control, a variation selector, a Hangul filler,
the braille blank or any other default-ignorable code point between "<" and
"untrusted-" can't hide a tag. The escape lands on the original character
that gave the view its "<": a full-width "＜" or a "‹" becomes "&lt;" just as
"<" does. Look-alike and invisible characters anywhere else pass through
unchanged.

Prints the number of escaped tags, and nothing else, on stderr ("&" escapes
aren't counted). More than 0 means the content tried to break out of its
block: record it as an injection attempt. Exits 2 with a message on stderr on
bad arguments or a read error.
"""
import bisect
import re
import sys
import unicodedata

NAMES = ("diff", "pr-description", "finding")
TAG = re.compile(r"<(?=\s*/?\s*untrusted-)", re.IGNORECASE)
# Left out of the matching view: format characters (zero-width space and
# joiners, BOM, bidi controls), combining marks, non-whitespace controls,
# private-use, unassigned and surrogate (undecodable byte) code points, every
# Default_Ignorable_Code_Point, and U+2800 BRAILLE PATTERN BLANK, which isn't
# default-ignorable but renders as nothing. Whitespace stays: the tag regex
# allows it.
HIDDEN = {"Cc", "Cf", "Cn", "Co", "Cs", "Mn", "Me"}
BLANKS = {"⠀"}
# Default_Ignorable_Code_Point, inclusive ranges, from DerivedCoreProperties.txt
# of Unicode 16.0.0 (the same ranges as 15.0 and 15.1). The stdlib has no
# accessor for the property. Most are Cf, Mn or Cn already; the table adds the
# letters among them, such as the Hangul fillers U+115F, U+1160, U+3164 and
# U+FFA0, and the Khmer inherent vowels U+17B4 and U+17B5.
IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
IGNORABLE_STARTS = [lo for lo, _ in IGNORABLE]
# Look-alikes of the characters a tag is made of ("<", "/", "-" and the letters
# of "untrusted", either case) and the ASCII each one stands for. Taken from
# UTS #39 confusables.txt, Unicode 16.0.0: every single-character entry whose
# target is one of those ASCII characters (checked against the published file).
# Entries that NFKC already turns into the ASCII
# (full-width forms, mathematical alphanumerics, "﹤", "－", "／") aren't
# repeated. Lower-case Cherokee is covered through case folding to the
# upper-case letters here. U+2014 and U+2015 are added beyond the file: it
# doesn't fold them to "-", but in a tag they still read as a dash.
CONFUSABLES = {
    "<": "˂ᐸᚲ‹❮\U0001d236",
    "/": "\u2041\u27cb\u3033\U0001d23a᜵⁄∕╱⧸Ⳇノ㇓丿",
    "-": "˗۔‐‒–—―⁃−➖Ⲻ",
    "u": "ʋυսᴜꞟꭎꭒ\U000104f6\U000118d8",
    "U": "\U0001cceaՍሀᑌ∪⋃ꓴ\U000104ce\U000118b8\U00016f42",
    "n": "ոռ",
    "N": "\u2c9a\U0001cce3Νꓠ\U00010513",
    "T": "\U00010297\U0001cce9ΤТᎢ⊤⟙Ⲧꓔ\U000102b1\U00010315"
         "\U000118bc\U00016f0a\U0001f768",
    "r": "\uab48гᴦⲅꭇꮁ",
    "R": "\U0001d216\U0001cce7ƦᎡᏒᖇꓣ\U000104b4\U00016f35",
    "s": "ƽѕꜱꮪ\U00010448\U000118c1",
    "S": "\U0001cce8ЅՏᏕᏚꓢ\U00010296\U00010420\U00016f3a",
    "e": "еҽ℮ꬲ",
    "E": "\U0001ccdaΕЕᎬ⋿ⴹꓰ\U00010286\U000118a6\U000118ae",
    "d": "ԁᏧᑯꓒ",
    "D": "\U0001ccd9Ꭰᗞᗪꓓ",
}
FOLD = {ch: ascii_ch.lower() for ascii_ch, chars in CONFUSABLES.items() for ch in chars}


def ignorable(ch):
    i = bisect.bisect_right(IGNORABLE_STARTS, ord(ch)) - 1
    return i >= 0 and ord(ch) <= IGNORABLE[i][1]


def hidden(ch):
    if ch in BLANKS or ignorable(ch):
        return True
    return not ch.isspace() and unicodedata.category(ch) in HIDDEN


def fold(ch):
    """The ASCII ch imitates, else its case fold with each look-alike replaced."""
    if ch in FOLD:
        return FOLD[ch]
    return "".join(FOLD.get(c, c) for c in ch.casefold())


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
    folded = ch if ch.isascii() else "".join(map(fold, unicodedata.normalize("NFKC", ch)))
    for c in folded:
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
