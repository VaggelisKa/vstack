"""Test helpers shared by every skill's script tests. Everything runs in temp dirs.

Each skills/<skill>/scripts/tests/helpers.py loads this file by path with
importlib.util.spec_from_file_location (the tests import `helpers`, never a
`lib` package), calls configure() once with its scripts folder, temp-dir prefix
and git environment, re-exports the names its tests import and keeps its own
extras (fake gh or curl, fixtures). Every load is a fresh copy of this module,
so one skill's settings never reach another's.
"""
import atexit
import contextlib
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile


def git_env(drop=()):
    """os.environ without the names starting with any of drop. Test repos ignore
    the user's global and system git config, so a diff.noprefix or
    init.defaultBranch there can't change the results."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    return {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


SCRIPTS = None
PREFIX = "vstack-test-"
GIT_ENV = git_env()
GIT_RUN = {}


def configure(scripts, prefix, env=None, **git_run):
    """scripts: the skill's scripts folder, which run() resolves names in.
    prefix: for temp dir names. env: GIT_ENV, by default git_env(). git_run:
    extra subprocess.run arguments for git(), such as a timeout."""
    global SCRIPTS, PREFIX, GIT_ENV, GIT_RUN
    SCRIPTS = pathlib.Path(scripts)
    PREFIX = prefix
    GIT_ENV = git_env() if env is None else env
    GIT_RUN = git_run


def run(script, *args, stdin=None, cwd=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *map(str, args)],
                          input=stdin, capture_output=True, cwd=cwd)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args],
                       capture_output=True, env=GIT_ENV, **GIT_RUN)
    if check and r.returncode:
        raise AssertionError(f"git {args} exited {r.returncode}: {r.stderr.decode()}")
    return r.stdout if check else r


_LIVE = set()  # this run's tmpdir() dirs not yet removed; swept at exit if a run is interrupted


def _writable(p):
    if not os.path.islink(p):
        with contextlib.suppress(OSError):
            os.chmod(p, stat.S_IRWXU)


def rmtree(path):
    """Remove path, making read-only or unreadable dirs and files (git objects, permission tests) usable first.
    Dirs are fixed top-down, before os.walk lists them, and symlinks are never followed."""
    path = os.fspath(path)
    _writable(path)
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            _writable(os.path.join(root, name))
    shutil.rmtree(path, ignore_errors=True)
    _LIVE.discard(path)


@atexit.register
def _sweep():
    for d in list(_LIVE):
        rmtree(d)


def tmpdir(test):
    """A fresh dir, removed when test finishes (or, given a TestCase class in setUpClass, when the class does)."""
    d = tempfile.mkdtemp(prefix=PREFIX)
    _LIVE.add(d)
    (test.addClassCleanup if isinstance(test, type) else test.addCleanup)(rmtree, d)
    return pathlib.Path(d)


@contextlib.contextmanager
def scratch():
    """A dir for a probe outside any test (a skip decorator's check), removed when the block ends."""
    with tempfile.TemporaryDirectory(prefix=PREFIX, ignore_cleanup_errors=True) as d:
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


def pr_repo(test, object_format=None, remote=None):
    """A repo on branch feature, one commit ahead of main, with remote as origin
    if given. Returns (repo, base, head); base is also the merge base."""
    repo = tmpdir(test) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", *([f"--object-format={object_format}"] if object_format else []))
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n")
    base = commit_all(repo, "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "src/a.py").write_text("x = 2\n")
    head = commit_all(repo, "head")
    if remote:
        git(repo, "remote", "add", "origin", remote)
    return repo, base, head


def sha256_supported():
    with scratch() as probe:
        return git(probe, "init", "-q", "--object-format=sha256", check=False).returncode == 0


def unrelated_repo(test):
    """A repo whose feature branch shares no history with main. Returns (repo, base, head)."""
    repo, base, _ = pr_repo(test)
    git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "b.txt").write_text("b\n")
    return repo, base, commit_all(repo, "other")


def criss_cross_repo(test):
    """A repo where base and head have two merge bases. Returns (repo, base, head)."""
    repo = new_repo(test)
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
