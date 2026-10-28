"""Shared helpers for the remove-comments script tests. Everything runs in temp dirs."""
import contextlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import commentscan  # noqa: E402 - must follow the sys.path line

# Test repos ignore the user's global and system git config, so a
# diff.noprefix or init.defaultBranch there can't change the results.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def run(*args, cwd=None, env=None, script=None):
    return subprocess.run([sys.executable, "-I", str(script or SCRIPTS / "comments.py"), *map(str, args)],
                          capture_output=True, cwd=cwd, env={**GIT_ENV, **(env or {})},
                          stdin=subprocess.DEVNULL, timeout=120)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args],
                       capture_output=True, env=GIT_ENV, stdin=subprocess.DEVNULL, timeout=120)
    if check and r.returncode:
        raise AssertionError(f"git {args} exited {r.returncode}: {r.stderr.decode()}")
    return r.stdout if check else r


def rmtree(path):
    """Remove path, making read-only dirs and files (git objects, permission tests) writable first."""
    def fix(func, p, _):
        for q in [p] + ([os.path.dirname(p)] if p != path else []):
            if not os.path.islink(q):
                with contextlib.suppress(OSError):
                    os.chmod(q, stat.S_IRWXU)
        with contextlib.suppress(OSError):
            func(p)
    shutil.rmtree(path, **{"onexc" if sys.version_info >= (3, 12) else "onerror": fix})


def tmpdir(test):
    """A fresh dir, removed when test finishes (or, given a TestCase class in setUpClass, when the class does)."""
    d = tempfile.mkdtemp(prefix="remove-comments-test-")
    (test.addClassCleanup if isinstance(test, type) else test.addCleanup)(rmtree, d)
    return pathlib.Path(d)


@contextlib.contextmanager
def scratch():
    """A dir for a probe outside any test (a skip decorator's check), removed when the block ends."""
    with tempfile.TemporaryDirectory(prefix="remove-comments-test-", ignore_cleanup_errors=True) as d:
        yield pathlib.Path(d)


def new_repo(test):
    repo = tmpdir(test) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def commit_all(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").decode().strip()


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
