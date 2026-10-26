"""Fallback secret scan for when neither gitleaks nor trufflehog is installed.

Usage: python3 -I scan-secrets.py <hunk-dir>
Prints the name of each <n>.patch that matches a token pattern, one per line,
and nothing else: never the matched value. Removed lines are scanned too,
because a deleted secret is still in history.
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

for p in sorted(pathlib.Path(sys.argv[1]).glob("*.patch")):
    if PATTERN.search(p.read_text(errors="replace")):
        print(p.name)
