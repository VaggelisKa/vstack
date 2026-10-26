"""Fallback secret scan for when neither gitleaks nor trufflehog is installed.

Usage: python3 -I scan-secrets.py <hunk-dir>
Prints the name of each <n>.patch that matches a token pattern, one per line,
and nothing else: never the matched value. Removed lines are scanned too,
because a deleted secret is still in history.

Fails closed: exits 2 with a message on stderr when the argument is missing,
the directory or its index.tsv (from split-hunks.py) is missing, a hunk that
index.tsv lists has no .patch file, or a file can't be read. Exit 2 means
"scan didn't happen": hold back every hunk, don't treat the empty stdout as
clean.
"""
import pathlib
import re
import sys

PATTERN = re.compile(
    r"AKIA[0-9A-Z]{16}"                          # AWS access key id
    r"|gh[pousr]_[A-Za-z0-9]{36,}"               # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{22,}"             # GitHub fine-grained PAT
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"             # Slack tokens
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"       # PEM/OpenSSH private keys
    r"""|(?i:(api|secret|token|passw)[^\n]{0,20}[:=]\s*['"][^'"]{12,})"""  # generic assignment
)


def fail(msg):
    print(f"scan-secrets: {msg}; hold back every hunk", file=sys.stderr)
    sys.exit(2)


if len(sys.argv) != 2:
    fail("usage: scan-secrets.py <hunk-dir>")
d = pathlib.Path(sys.argv[1])
if not d.is_dir():
    fail(f"{d} is not a directory")
try:
    listed = [l.split("\t", 1)[0] for l in (d / "index.tsv").read_text().splitlines() if l]
    missing = [n for n in listed if not (d / n).is_file()]
    if missing:
        fail(f"index.tsv lists {len(listed)} hunk(s) but {len(missing)} .patch file(s) are missing")
    flagged = [p.name for p in sorted(d.glob("*.patch"))
               if PATTERN.search(p.read_text(errors="replace"))]
except OSError as e:
    fail(f"can't read {e.filename}: {e.strerror}")
print("".join(n + "\n" for n in flagged), end="")
