"""Checks the plugin version that Codex, Gemini CLI and Antigravity read to decide whether an install is out of date.

- .codex-plugin/plugin.json, gemini-extension.json and plugin.json (Antigravity) carry the same X.Y.Z version.
- .claude-plugin/plugin.json and the marketplace entries carry none, so Claude Code versions the
  plugin by git commit.
- With --base <rev> (pull requests), the version is higher than the one at <rev>: every change
  bumps it, or Codex, Gemini and Antigravity users never see it.

Usage: python3 .github/scripts/check-version.py [--base <rev>] [--root <repo>]
Exits 0 when every check passes, 1 with one line per problem on stderr otherwise.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

VERSIONED = (".codex-plugin/plugin.json", "gemini-extension.json", "plugin.json")
UNVERSIONED = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json")
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def parse(v):
    m = SEMVER.fullmatch(v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def has_version(doc):
    if isinstance(doc, dict):
        return "version" in doc or any(has_version(v) for v in doc.values())
    if isinstance(doc, list):
        return any(has_version(v) for v in doc)
    return False


def base_version(root, base):
    r = subprocess.run(["git", "show", f"{base}:{VERSIONED[0]}"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"check-version: can't read {VERSIONED[0]} at {base}: {r.stderr.strip()}")
    return json.loads(r.stdout).get("version")


def check(root, base=None):
    problems, versions = [], {}
    for path in VERSIONED:
        v = json.loads((root / path).read_text()).get("version")
        if parse(v) is None:
            problems.append(f"{path}: version {v!r} is not X.Y.Z")
        versions[path] = v
    if len(set(versions.values())) > 1:
        problems.append("versions differ: " + ", ".join(f"{p} {v}" for p, v in versions.items()))
    for path in UNVERSIONED:
        f = root / path
        if f.exists() and has_version(json.loads(f.read_text())):
            problems.append(f"{path}: remove 'version'; the plugin is versioned in {', '.join(VERSIONED)} only")
    if base and not problems:
        old, new = base_version(root, base), versions[VERSIONED[0]]
        if parse(old) is not None and parse(new) <= parse(old):
            problems.append(f"version {new} is not higher than {old} on {base}: bump it in "
                            + ", ".join(VERSIONED) + " (see docs/contributing.md, Releasing)")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    problems = check(pathlib.Path(a.root), a.base)
    for p in problems:
        print(f"check-version: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
