"""Split a unified diff into one file per hunk, so a secret scanner hit maps
straight back to a hunk.

Usage: python3 -I split-hunks.py <area.diff> <out-dir>
Writes <out-dir>/<n>.patch (file header + one hunk) and <out-dir>/index.tsv
with "<n>.patch<TAB><path><TAB><@@ header @@>" per hunk. Never prints hunk
content.

The path comes from the "+++ b/<path>" line (from "--- a/<path>" for a
deletion), not from "diff --git", which is ambiguous when a path contains
" b/". Expects git's default a/ and b/ prefixes. C-quoted paths
("b/caf\\303\\251.md") are decoded; one that still holds a tab or newline is
kept in its quoted form so index.tsv stays one line per hunk.
"""
import os
import re
import sys

ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def header_path(raw, prefix):
    """Path from the rest of a ---/+++ line, or None for /dev/null."""
    raw = raw.rstrip("\r\n")
    if raw.endswith("\t"):            # git adds a tab after names with spaces
        raw = raw[:-1]
    if raw == "/dev/null":
        return None
    if len(raw) > 1 and raw[0] == raw[-1] == '"':
        out, body, i = bytearray(), raw[1:-1], 0
        while i < len(body):
            m = re.match(r"\\([0-7]{3}|.)", body[i:])
            if m:
                e = m.group(1)
                out.append(int(e, 8) if len(e) == 3 else ESCAPES.get(e, ord(e)))
                i += len(m.group(0))
            else:
                out += body[i].encode("utf-8", "surrogateescape")
                i += 1
        path = out.decode("utf-8", "replace")
        if any(c in path for c in "\t\n\r"):
            path = raw
        elif path.startswith(prefix):
            path = path[len(prefix):]
        return path
    return raw[len(prefix):] if raw.startswith(prefix) else raw


if len(sys.argv) != 3:
    sys.exit("usage: split-hunks.py <area.diff> <out-dir>")
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
head, old, path, n, cur, index = [], None, "", 0, None, []


def flush():
    if cur is not None:
        with open(f"{out}/{n}.patch", "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("".join(cur))


for line in open(src, encoding="utf-8", errors="surrogateescape"):
    if line.startswith("diff --git "):
        flush()
        cur, head, old, path = None, [line], None, ""
    elif line.startswith("@@") and (cur is not None or head):
        flush()
        n += 1
        cur = head + [line]
        index.append(f"{n}.patch\t{path}\t@@{line.split('@@')[1]}@@")
    elif cur is not None:
        cur.append(line)
    else:
        # Only header lines reach here, so a removed "-- x" line in a hunk
        # (which reads "--- x") is never mistaken for a file header.
        if line.startswith("--- "):
            old = header_path(line[4:], "a/")
        elif line.startswith("+++ "):
            path = header_path(line[4:], "b/") or old or ""
        head.append(line)
flush()
with open(f"{out}/index.tsv", "w", encoding="utf-8", errors="surrogateescape") as f:
    f.write("".join(i + "\n" for i in index))
