"""Shared helpers for the review skill's script tests. Everything runs in temp dirs."""
import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILL = SCRIPTS.parent

# Test repos ignore the user's global and system git config, so a
# diff.noprefix or init.defaultBranch there can't change the results.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def run(script, *args, stdin=None, cwd=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *map(str, args)],
                          input=stdin, capture_output=True, cwd=cwd)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args],
                       capture_output=True, env=GIT_ENV)
    if check and r.returncode:
        raise AssertionError(f"git {args} exited {r.returncode}: {r.stderr.decode()}")
    return r.stdout if check else r


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="review-test-"))


def new_repo():
    repo = tmpdir() / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def commit_all(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").decode().strip()
