"""Shared helpers for the remove-comments script tests. Everything runs in temp dirs.

The common helpers are shared by every skill's tests and live in
lib/testing.py at the plugin root, loaded by path; this file configures them
for remove-comments (git gets no stdin and a timeout), re-exports what the
tests import and adds the comments.py runner and scanner fixtures."""
import importlib.util
import json
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import commentscan  # noqa: E402 - must follow the sys.path line

_spec = importlib.util.spec_from_file_location("vstack_testing", SKILL.parent.parent / "lib" / "testing.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
T.configure(SCRIPTS, "remove-comments-test-", stdin=subprocess.DEVNULL, timeout=120)

GIT_ENV = T.GIT_ENV
git, rmtree, tmpdir, scratch, new_repo, commit_all = T.git, T.rmtree, T.tmpdir, T.scratch, T.new_repo, T.commit_all


def run(*args, cwd=None, env=None, script=None):
    return subprocess.run([sys.executable, "-I", str(script or SCRIPTS / "comments.py"), *map(str, args)],
                          capture_output=True, cwd=cwd, env={**GIT_ENV, **(env or {})},
                          stdin=subprocess.DEVNULL, timeout=120)


def texts(src, lang):
    """The comments the scanner finds, as source text."""
    return [src[c.start:c.end] for c in commentscan.scan(src, lang).comments]


def listing(test, repo, *args):
    """Run list in repo; returns (result, doc, path of comments.json)."""
    out = tmpdir(test) / "comments.json"
    r = run("list", "--out", out, *args, cwd=repo)
    doc = json.loads(out.read_text()) if out.exists() else None
    return r, doc, out


def decide(out, decisions):
    path = out.parent / "decisions.json"
    path.write_text(json.dumps(decisions))
    return path
