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


def pr_repo(object_format=None):
    """A repo on branch feature, one commit ahead of main. Returns (repo, base, head);
    base is also the merge base."""
    repo = tmpdir() / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", *([f"--object-format={object_format}"] if object_format else []))
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n")
    base = commit_all(repo, "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "src/a.py").write_text("x = 2\n")
    return repo, base, commit_all(repo, "head")


def sha256_supported():
    probe = tmpdir()
    return git(probe, "init", "-q", "--object-format=sha256", check=False).returncode == 0


def unrelated_repo():
    """A repo whose feature branch shares no history with main. Returns (repo, base, head)."""
    repo, base, _ = pr_repo()
    git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "b.txt").write_text("b\n")
    return repo, base, commit_all(repo, "other")


def criss_cross_repo():
    """A repo where base and head have two merge bases. Returns (repo, base, head)."""
    repo = new_repo()
    (repo / "a").write_text("a\n")
    commit_all(repo, "a")
    git(repo, "checkout", "-qb", "x")
    (repo / "x").write_text("x\n")
    x = commit_all(repo, "x")
    git(repo, "checkout", "-qb", "y", "main")
    (repo / "y").write_text("y\n")
    y = commit_all(repo, "y")
    git(repo, "merge", "-q", "--no-edit", x)
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "checkout", "-q", "x")
    git(repo, "merge", "-q", "--no-edit", y)
    return repo, git(repo, "rev-parse", "HEAD").decode().strip(), head
