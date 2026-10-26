"""Split a unified diff into one file per hunk, so a secret scanner hit maps
straight back to a hunk.

Usage: python3 -I split-hunks.py <area.diff> <out-dir>
Writes <out-dir>/<n>.patch (file header + one hunk) and <out-dir>/index.tsv
with "<n>.patch<TAB><path><TAB><@@ header @@>" per hunk. Never prints hunk
content.
"""
import os
import sys

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
head, path, n, cur, index = [], "", 0, None, []


def flush():
    if cur is not None:
        with open(f"{out}/{n}.patch", "w") as f:
            f.write("".join(cur))


for line in open(src, errors="replace"):
    if line.startswith("diff --git "):
        flush()
        cur, head = None, [line]
        path = line.rstrip("\n").split(" b/", 1)[-1]
    elif line.startswith("@@") and (cur is not None or head):
        flush()
        n += 1
        cur = head + [line]
        index.append(f"{n}.patch\t{path}\t@@{line.split('@@')[1]}@@")
    elif cur is not None:
        cur.append(line)
    else:
        head.append(line)
flush()
with open(f"{out}/index.tsv", "w") as f:
    f.write("".join(i + "\n" for i in index))
