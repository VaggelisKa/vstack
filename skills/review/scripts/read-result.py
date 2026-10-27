"""Read the review's result for one PR from <git-common-dir>/vstack/<pr>/review.json.

Usage: python3 -I read-result.py --pr N --expect-head-sha SHA --expect-base-sha SHA

Pass the PR's current head and base (`gh pr view N --json headRefOid,baseRefOid`),
as full SHAs. Prints the result JSON on stdout. Exits 0 ok, 3 stale (the file's
head_sha or base_sha doesn't match; nothing on stdout), 2 usage error, not in a
git repo, missing file or unsupported file (newer schema_version, not JSON,
wrong skill or pr, fails validation), 4 internal error. Reasons go to stderr.
Any non-zero exit means don't use the file.

Readers follow references/result-schema.md: a schema_version newer than
SCHEMA_VERSION is refused, and a head_sha or base_sha that isn't exactly the
PR's current one means stale. merge_base is recomputed with
`git merge-base --all base_sha head_sha` and the file refused (exit 2) unless
that gives exactly one commit equal to it, so the commits must be local. The
whole file is also checked as
write-result.py checks it (it's loaded from this folder), so a missing field,
wrong type or contradiction such as a merge_ok the rest doesn't support is
refused (exit 2); only unknown fields are ignored. The folder is found as
write-result.py finds it, and a symlinked vstack, <pr> folder or file is
refused. The file is a cache, not an attestation: never merge on it alone.

To import it, load this file with importlib.util.spec_from_file_location and
call read_result(pr, expect_head_sha, expect_base_sha); it returns the result
dict or raises Stale (which carries .result), Missing or Unsupported.
"""
import argparse
import errno
import importlib.util
import json
import os
import stat
import subprocess
import sys

_spec = importlib.util.spec_from_file_location(
    "vstack_review_write_result", os.path.join(os.path.dirname(os.path.abspath(__file__)), "write-result.py"))
writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writer)

SKILL = writer.SKILL
SCHEMA_VERSION = writer.SCHEMA_VERSION
MAX_BYTES = writer.MAX_BYTES
MAX_ERRORS = writer.MAX_ERRORS
MAX_PR = writer.MAX_PR
PR_ARG = writer.PR_ARG
SHA = writer.SHA
DIR_FLAGS = writer.DIR_FLAGS


class Unusable(Exception):
    pass


class Missing(Unusable):
    pass


class Unsupported(Unusable):
    pass


class Stale(Exception):
    def __init__(self, msg, result):
        super().__init__(msg)
        self.result = result


is_int = writer.is_int
no_duplicates = writer.no_duplicates
no_constants = writer.no_constants


def is_sha(v):
    return isinstance(v, str) and SHA.fullmatch(v)


def common_dir():
    try:
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True,
                           env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        raise Unusable(f"can't run git: {e.strerror}")
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(os.path.join(os.getcwd(), r.stdout.rstrip("\n")))
    if r.returncode == 128 and "not a git repository" in r.stderr:
        raise Unusable("not in a git repository")
    raise Unusable(f"can't find the git common dir: git exited {r.returncode}")


def merge_base(top, base, head):
    try:
        r = subprocess.run(["git", "--git-dir", top, "merge-base", "--all", base, head], capture_output=True,
                           text=True, env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        raise Unusable(f"can't run git: {e.strerror}")
    if r.returncode not in (0, 1):
        raise Unsupported(f"can't recompute merge_base: git exited {r.returncode} (fetch the head and base first)")
    shas = r.stdout.split()
    if len(shas) != 1:
        raise Unsupported(f"base_sha and head_sha have {'no' if not shas else 'several'} merge bases; need exactly one")
    return shas[0]


def load(top, pr):
    shown = f"vstack/{pr}/{SKILL}.json"
    fds = []
    try:
        fds.append(os.open(top, os.O_RDONLY | os.O_DIRECTORY))
        for name in ("vstack", str(pr)):
            fds.append(os.open(name, DIR_FLAGS, dir_fd=fds[-1]))
        fd = os.open(f"{SKILL}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise Unsupported(f"{shown} is not a regular file")
            data = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise Missing(f"no {shown}")
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
            raise Unsupported(f"refusing: {shown} or a folder above it is a symlink or not a directory")
        raise
    finally:
        for fd in fds:
            os.close(fd)
    if len(data) > MAX_BYTES:
        raise Unsupported(f"{shown} is over {MAX_BYTES} bytes")
    try:
        return json.loads(data.decode("utf-8"), parse_constant=no_constants, object_pairs_hook=no_duplicates)
    except (ValueError, RecursionError):
        raise Unsupported(f"{shown} is not valid JSON")


def read_result(pr, expect_head_sha, expect_base_sha, top=None):
    if not is_int(pr) or not 1 <= pr <= MAX_PR:
        raise Unusable("pr must be a positive integer")
    for name, sha in (("expect_head_sha", expect_head_sha), ("expect_base_sha", expect_base_sha)):
        if not is_sha(sha):
            raise Unusable(f"{name} must be a full SHA, 40 or 64 lowercase hex characters")
    top = top or common_dir()
    r = load(top, pr)
    if not isinstance(r, dict):
        raise Unsupported("the result is not a JSON object")
    v = r.get("schema_version")
    if not is_int(v) or v < 1:
        raise Unsupported("schema_version is missing or not a positive integer")
    if v > SCHEMA_VERSION:
        raise Unsupported(f"schema_version {v} is newer than {SCHEMA_VERSION}, the newest this reads")
    if r.get("skill") != SKILL or not is_int(r.get("pr")) or r["pr"] != pr:
        raise Unsupported(f"the file isn't {SKILL}'s result for PR {pr}")
    for k in ("head_sha", "base_sha"):
        if not is_sha(r.get(k)):
            raise Unsupported(f"{k} is missing or not a full SHA")
    errs = writer.validate(r, strict=False)
    if errs:
        more = [f"... and {len(errs) - MAX_ERRORS} more"] if len(errs) > MAX_ERRORS else []
        raise Unsupported(f"vstack/{pr}/{SKILL}.json fails validation:\n  " + "\n  ".join(errs[:MAX_ERRORS] + more))
    if r["head_sha"] != expect_head_sha:
        raise Stale(f"stale: written for head {r['head_sha']}, the PR head is {expect_head_sha}", r)
    if r["base_sha"] != expect_base_sha:
        raise Stale(f"stale: written for base {r['base_sha']}, the PR base is {expect_base_sha}", r)
    if r["merge_base"] != merge_base(top, r["base_sha"], r["head_sha"]):
        raise Unsupported("merge_base doesn't match `git merge-base --all` of base_sha and head_sha")
    return r


def main():
    ap = argparse.ArgumentParser(prog="read-result.py")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--expect-head-sha", required=True)
    ap.add_argument("--expect-base-sha", required=True)
    a = ap.parse_args()
    try:
        if not PR_ARG.fullmatch(a.pr) or int(a.pr) > MAX_PR:
            raise Unusable("--pr must be a positive integer")
        r = read_result(int(a.pr), a.expect_head_sha, a.expect_base_sha)
    except Stale as e:
        print(f"read-result: {e}", file=sys.stderr)
        sys.exit(3)
    except Unusable as e:
        print(f"read-result: {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"read-result: internal error: {e.strerror or type(e).__name__}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"read-result: internal error: {type(e).__name__}", file=sys.stderr)
        sys.exit(4)
