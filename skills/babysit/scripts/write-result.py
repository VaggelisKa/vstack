"""Write babysit's result for one PR to <git-common-dir>/vstack/<pr>/babysit.json,
where other skills can read it.

Usage: python3 -I write-result.py [--pr N] --head-sha SHA --base-sha SHA [--file PATH]

The result is a JSON object on stdin, or in --file, matching
references/result-schema.md. schema_version, skill, pr (from --pr), head_sha,
base_sha (from their options), merge_base
(`git merge-base --all`, which must give exactly one commit) and written_at (now, UTC) are filled
in when missing; a value given both in the result and as an option must agree.
Unknown fields are rejected. Each SHA must be a full 40 or 64 hex commit that
exists in the repo.

The file goes in `git rev-parse --git-common-dir`, so it can't be committed and
every worktree of the repo shares it. Outside a git repo nothing is written. A
vstack or <pr> folder that is a symlink or not a directory is refused. The JSON
is written to a temp file in the same folder and renamed over the old one, so a
reader sees the old file or the new one, never part of one. Prints the path
written.

Exits 0 ok, 2 usage or validation error (reasons on stderr; a result that fails
validation writes nothing), 4 internal error.
"""
import argparse
import datetime
import errno
import json
import os
import re
import secrets
import subprocess
import sys

SKILL = "babysit"
SCHEMA_VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_ERRORS = 20
MAX_PR = 2**31 - 1
PR_ARG = re.compile(r"[1-9][0-9]*")
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SHA_OPTS = {"head_sha": "--head-sha", "base_sha": "--base-sha"}
UTC = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?(Z|\+00:00)")
LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
CONTROLS = frozenset(map(chr, [*range(0x20), 0x7f])) | frozenset(LINE_BREAKS)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Usage(Exception):
    pass


def cap(s, n=60):
    return s if len(s) <= n else s[:n] + "..."


def join(path, key):
    return f"{path}.{key}" if path else key


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_text(v):
    return isinstance(v, str) and v.strip() != ""


def is_line(v):
    return is_text(v) and CONTROLS.isdisjoint(v)


def is_utc(v):
    m = isinstance(v, str) and UTC.fullmatch(v)
    if not m:
        return False
    try:
        datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def kind(test, what):
    def check(v, path, errs, strict=True):
        if not test(v):
            errs.append(f"{path}: must be {what}")
    return check


def enum(*values, null=False):
    what = ", ".join(values) + (" or null" if null else "")
    return kind(lambda v: (null and v is None) or (isinstance(v, str) and v in values), f"one of {what}")


def list_of(item):
    def check(v, path, errs, strict=True):
        if not isinstance(v, list):
            errs.append(f"{path}: must be a list")
            return
        for i, x in enumerate(v):
            item(x, f"{path}[{i}]", errs, strict)
    return check


def obj(spec):
    def check(v, path, errs, strict=True):
        if not isinstance(v, dict):
            errs.append(f"{path or 'result'}: must be an object")
            return
        for k in v:
            if strict and k not in spec:
                errs.append(f"{join(path, repr(cap(k)))}: unknown field")
        for k, field in spec.items():
            if k in v:
                field(v[k], join(path, k), errs, strict)
            else:
                errs.append(f"{join(path, k)}: missing")
    return check


TEXT = kind(is_text, "a non-empty string")
BOOL = kind(lambda v: isinstance(v, bool), "true or false")
LINE = kind(is_line, "one line of text with no control characters")

COMMON = {
    "schema_version": kind(lambda v: is_int(v) and v == SCHEMA_VERSION, str(SCHEMA_VERSION)),
    "skill": kind(lambda v: v == SKILL, f'"{SKILL}"'),
    "pr": kind(lambda v: is_int(v) and 1 <= v <= MAX_PR, "a positive integer"),
    "head_sha": kind(lambda v: isinstance(v, str) and SHA.fullmatch(v), "40 or 64 lowercase hex characters"),
    "base_sha": kind(lambda v: isinstance(v, str) and SHA.fullmatch(v), "40 or 64 lowercase hex characters"),
    "merge_base": kind(lambda v: isinstance(v, str) and SHA.fullmatch(v), "40 or 64 lowercase hex characters"),
    "written_at": kind(is_utc, "an ISO 8601 UTC time such as 2026-01-31T12:00:00Z"),
}

FIELDS = {
    "status": enum("running", "green", "handed_back", "failed"),
    "ci": obj({
        "state": enum("passing", "pending", "failing", "none"),
        "failing_checks": list_of(TEXT),
    }),
    "threads": list_of(obj({
        "id": LINE,
        "author": TEXT,
        "is_bot": BOOL,
        "state": enum("open", "resolved"),
        "decision": enum("fixed", "rejected", "escalated", "pending", null=True),
        "reply_url": kind(lambda v: v is None or is_text(v), "a non-empty string or null"),
    })),
    "last_action": LINE,
    "mergeable": kind(lambda v: v is None or isinstance(v, bool), "true, false or null"),
    "approved_on_head": BOOL,
    "changes_requested": BOOL,
    "required_checks": list_of(obj({
        "name": TEXT,
        "state": enum("passing", "pending", "failing"),
    })),
}


def ci_state(checks):
    states = {c["state"] for c in checks}
    for state in ("failing", "pending", "passing"):
        if state in states:
            return state
    return "none"


def consistent(r, errs):
    ci = r["ci"]
    if ci["state"] != ci_state(r["required_checks"]):
        errs.append(f"ci.state: must be {ci_state(r['required_checks'])} for these required_checks")
    if (ci["state"] == "failing") != bool(ci["failing_checks"]):
        errs.append("ci.failing_checks: must be non-empty exactly when ci.state is failing")
    if r["status"] == "green" and ci["state"] not in ("passing", "none"):
        errs.append("status: green needs ci.state passing or none")
    if r["status"] == "green" and any(t["state"] == "open" for t in r["threads"]):
        errs.append("status: green can't have an open thread")
    if r["status"] == "green" and not r["approved_on_head"]:
        errs.append("status: green needs approved_on_head")
    if r["status"] == "green" and r["mergeable"] is not True:
        errs.append("status: green needs mergeable true")
    if r["status"] == "green" and r["changes_requested"]:
        errs.append("status: green can't have changes_requested")
    if r["status"] == "green" and any(c["state"] != "passing" for c in r["required_checks"]):
        errs.append("status: green needs every required check passing")
    ids = [t["id"] for t in r["threads"]]
    if len(ids) != len(set(ids)):
        errs.append("threads: duplicate id")
    names = [c["name"] for c in r["required_checks"]]
    if len(names) != len(set(names)):
        errs.append("required_checks: duplicate name")
    if any(t["state"] == "resolved" and t["decision"] == "escalated" for t in r["threads"]):
        errs.append("threads: a resolved thread can't have decision escalated")


def validate(r, strict=True):
    """Every problem with result r, as a list of messages that don't echo its
    values. With strict=False (readers), unknown fields are allowed."""
    errs = []
    obj({**COMMON, **FIELDS})(r, "", errs, strict)
    if not errs:
        consistent(r, errs)
    return errs


def no_constants(name):
    raise ValueError(name)


def no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def read(src):
    try:
        if src == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with open(src, "rb") as f:
                data = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise Usage(f"can't read --file: {e.strerror}")
    if len(data) > MAX_BYTES:
        raise Usage(f"result is over {MAX_BYTES} bytes")
    try:
        r = json.loads(data.decode("utf-8"), parse_constant=no_constants, object_pairs_hook=no_duplicates)
    except (ValueError, RecursionError):
        raise Usage("result is not valid JSON (or has NaN, Infinity or a duplicate key)")
    if not isinstance(r, dict):
        raise Usage("result must be a JSON object")
    return r


def git(*args, cwd=None):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd,
                              env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        raise Usage(f"can't run git: {e.strerror}")


def common_dir():
    r = git("rev-parse", "--git-common-dir")
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(os.path.join(os.getcwd(), r.stdout.rstrip("\n")))
    if r.returncode == 128 and "not a git repository" in r.stderr:
        raise Usage("not in a git repository: nothing written")
    raise Usage(f"can't find the git common dir: git exited {r.returncode}")


def check_commit(opt, sha):
    if not SHA.fullmatch(sha):
        raise Usage(f"{opt} must be a full SHA, 40 or 64 lowercase hex characters")
    if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise Usage(f"{opt} is not a commit in this repo (fetch it first)")


def merge_base(base, head):
    r = git("merge-base", "--all", base, head)
    if r.returncode not in (0, 1):
        raise Usage(f"can't find the merge base: git exited {r.returncode}")
    shas = r.stdout.split()
    if len(shas) != 1:
        raise Usage(f"the base and head have {'no' if not shas else 'several'} merge bases; "
                    "need exactly one")
    return shas[0]


def fill(r, given, errs):
    for k, v in given.items():
        if k not in r:
            r[k] = v
        elif type(r[k]) is not type(v) or r[k] != v:
            errs.append(f"{k}: the result and the option disagree")
    if "written_at" not in r:
        r["written_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_dir(name, parent, shown):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    try:
        return os.open(name, DIR_FLAGS, dir_fd=parent)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
            raise Usage(f"refusing: {shown} is a symlink or not a directory")
        raise


def replace(dir_fd, name, data):
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def write(top, r):
    fds = []
    try:
        fds.append(os.open(top, os.O_RDONLY | os.O_DIRECTORY))
        fds.append(open_dir("vstack", fds[-1], "vstack"))
        fds.append(open_dir(str(r["pr"]), fds[-1], f"vstack/{r['pr']}"))
        replace(fds[-1], f"{SKILL}.json", (json.dumps(r, indent=2) + "\n").encode())
    finally:
        for fd in fds:
            os.close(fd)
    return os.path.join(top, "vstack", str(r["pr"]), f"{SKILL}.json")


def main():
    ap = argparse.ArgumentParser(prog="write-result.py")
    ap.add_argument("--pr")
    for opt in SHA_OPTS.values():
        ap.add_argument(opt, required=True)
    ap.add_argument("--file", default="-")
    a = ap.parse_args()
    try:
        given = {"schema_version": SCHEMA_VERSION, "skill": SKILL}
        if a.pr is not None:
            if not PR_ARG.fullmatch(a.pr) or int(a.pr) > MAX_PR:
                raise Usage("--pr must be a positive integer")
            given["pr"] = int(a.pr)
        top = common_dir()
        for k, opt in SHA_OPTS.items():
            check_commit(opt, getattr(a, k))
            given[k] = getattr(a, k)
        given["merge_base"] = merge_base(a.base_sha, a.head_sha)
        r = read(a.file)
        errs = []
        fill(r, given, errs)
        errs += validate(r)
        if errs:
            more = [f"... and {len(errs) - MAX_ERRORS} more"] if len(errs) > MAX_ERRORS else []
            raise Usage("invalid result:\n  " + "\n  ".join(errs[:MAX_ERRORS] + more))
        path = write(top, {k: r[k] for k in (*COMMON, *FIELDS)})
    except Usage as e:
        print(f"write-result: {e}", file=sys.stderr)
        sys.exit(2)
    print(path)


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"write-result: internal error: {e.strerror or type(e).__name__}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"write-result: internal error: {type(e).__name__}", file=sys.stderr)
        sys.exit(4)
