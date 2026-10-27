"""Create or remove the review's session folder, the one place for files that
have to exist on disk (area diffs, --cross prompts and outputs, logs,
screenshots).

Usage: python3 -I session.py new
       python3 -I session.py clean <dir>

new creates <root>/.review-session-<16 hex> with mode 0700, where <root> is
the git toplevel of the current directory, or the current directory outside a
repo (git exits 128 with "not a git repository"), and prints its absolute
path. If git is missing or fails any other way, it exits 2. The folder holds a
.gitignore of "*", so it ignores itself: no tracked file, .git/info/exclude or
`git status` output changes. It lives in the repo, not /tmp, because subagents
treat files outside the workspace as untrusted and refuse to open them.

clean removes it, and refuses (exit 2, nothing deleted) unless the path has no
".." component, its name is .review-session-<16 hex>, it is a directory and not
a symlink, and its parent is <root>. An entry it can't delete gets u+rwx on it
and its parent (inside the folder only) and one more try; what still fails is
named in a warning, and clean exits 2 if the folder remains. Exits 2 with a
message on stderr on bad arguments or any error.
"""
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys

PREFIX = ".review-session-"
NAME = re.compile(r"\.review-session-[0-9a-f]{16}")


def fail(msg):
    print(f"session: {msg}", file=sys.stderr)
    sys.exit(2)


def root():
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        fail(f"can't determine the repo root: can't run git: {e.strerror}")
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(r.stdout.strip())
    if r.returncode == 128 and "not a git repository" in r.stderr:
        return os.path.realpath(os.getcwd())
    why = (r.stderr.strip().splitlines() or [""])[0][:200]
    fail(f"can't determine the repo root: git exited {r.returncode}" + (f": {why}" if why else ""))


def new():
    path = os.path.join(root(), PREFIX + secrets.token_hex(8))
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
        fd = os.open(os.path.join(path, ".gitignore"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("*\n")
    except OSError as e:
        fail(f"can't create {path}: {e.strerror}")
    print(path)


def clean(arg):
    if ".." in arg.replace("\\", "/").split("/"):
        fail("refusing: path contains '..'")
    path = os.path.abspath(arg)
    if not NAME.fullmatch(os.path.basename(path)):
        fail(f"refusing: name is not {PREFIX}<16 hex>")
    try:
        st = os.lstat(path)
    except OSError as e:
        fail(f"refusing: can't stat it: {e.strerror}")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        fail("refusing: not a real directory")
    if os.path.realpath(os.path.dirname(path)) != root():
        fail("refusing: parent is not the repo toplevel (or the current directory outside a repo)")

    tried = set()

    def retry(func, p, _):
        # Once per entry: make it and its parent writable (inside the folder only), try again.
        if not os.path.lexists(p):
            return
        if p in tried:
            print(f"session: warning: couldn't remove {p}", file=sys.stderr)
            return
        tried.add(p)
        for d in (os.path.dirname(p), p):
            if d == path or d.startswith(path + os.sep):
                try:
                    st = os.lstat(d)
                    if not stat.S_ISLNK(st.st_mode):
                        os.chmod(d, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
                except OSError:
                    pass
        try:
            if func in (os.rmdir, os.unlink, os.remove):
                func(p)
            else:
                rmtree(p)
        except OSError:
            if os.path.lexists(p):
                print(f"session: warning: couldn't remove {p}", file=sys.stderr)

    def rmtree(p):
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=retry)
        else:
            shutil.rmtree(p, onerror=retry)

    rmtree(path)
    if os.path.lexists(path):
        fail(f"can't remove {path} completely")


if len(sys.argv) == 2 and sys.argv[1] == "new":
    new()
elif len(sys.argv) == 3 and sys.argv[1] == "clean":
    clean(sys.argv[2])
else:
    fail("usage: session.py new | session.py clean <dir>")
