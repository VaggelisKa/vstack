"""Shared helpers for the babysit skill's script tests. Everything runs in temp dirs."""
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
    d = tempfile.mkdtemp(prefix="babysit-test-")
    (test.addClassCleanup if isinstance(test, type) else test.addCleanup)(rmtree, d)
    return pathlib.Path(d)


@contextlib.contextmanager
def scratch():
    """A dir for a probe outside any test (a skip decorator's check), removed when the block ends."""
    with tempfile.TemporaryDirectory(prefix="babysit-test-", ignore_cleanup_errors=True) as d:
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


def pr_repo(test, object_format=None):
    """A repo on branch feature, one commit ahead of main. Returns (repo, base, head);
    base is also the merge base."""
    repo = tmpdir(test) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", *([f"--object-format={object_format}"] if object_format else []))
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n")
    base = commit_all(repo, "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "src/a.py").write_text("x = 2\n")
    return repo, base, commit_all(repo, "head")


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


GH_STUB = r'''#!%(python)s
import json, os, sys
d = os.environ["STUB_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
joined = " ".join(args)
with open(os.path.join(d, "rules.json")) as f:
    rules = json.load(f)
for rule in rules:
    if all(m in joined for m in rule["match"]):
        break
else:
    sys.stderr.write("stub: no rule for gh %%s\n" %% joined)
    sys.exit(99)
out = rule.get("stdout", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(rule.get("stderr", ""))
sys.exit(rule.get("code", 0))
'''


class FakeGh:
    """A PATH holding git and a fake gh that answers from rules (first match wins) and logs its argv."""

    def __init__(self, test):
        self.dir = tmpdir(test) / "bin"
        self.dir.mkdir()
        os.symlink(shutil.which("git"), self.dir / "git")
        p = self.dir / "gh"
        p.write_text(GH_STUB % {"python": sys.executable})
        p.chmod(0o755)
        self.set([])

    def set(self, rules):
        (self.dir / "rules.json").write_text(json.dumps(rules))

    def calls(self):
        log = self.dir / "log.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def env(self):
        env = {k: v for k, v in GIT_ENV.items() if not k.startswith(("GH_", "GITHUB_"))}
        return {**env, "PATH": str(self.dir), "STUB_DIR": str(self.dir)}
