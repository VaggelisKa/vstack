"""Shared code for the per-PR result files, <git-common-dir>/vstack/<pr>/<skill>.json,
that review and babysit write and other skills read.

A skill's write-result.py declares SKILL, its FIELDS (built from the
combinators below), its consistent() rules and any computed fields, and hands
them to Writer, which supplies validate, the git checks, the atomic write and
the CLI. Its read-result.py hands that loaded write-result.py to Reader, which
supplies read_result and the CLI. Nothing here knows about a particular skill.

The scripts run under python3 -I (the script's folder isn't on sys.path), so
this file is loaded by path with importlib.util.spec_from_file_location from
<plugin root>/lib/result_file.py, never imported as a package.
"""
import argparse
import datetime
import errno
import json
import os
import re
import secrets
import stat
import subprocess
import sys

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
    """A writer's usage or validation error: exit 2, nothing written."""


class Unusable(Exception):
    """A reader can't use the file: exit 2."""


class Missing(Unusable):
    pass


class Unsupported(Unusable):
    pass


class Stale(Exception):
    def __init__(self, msg, result):
        super().__init__(msg)
        self.result = result


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


def is_sha(v):
    return isinstance(v, str) and SHA.fullmatch(v)


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
SHA_FIELD = kind(is_sha, "40 or 64 lowercase hex characters")


def common_fields(skill):
    """The fields every skill's result has, in file order."""
    return {
        "schema_version": kind(lambda v: is_int(v) and v == SCHEMA_VERSION, str(SCHEMA_VERSION)),
        "skill": kind(lambda v: v == skill, f'"{skill}"'),
        "pr": kind(lambda v: is_int(v) and 1 <= v <= MAX_PR, "a positive integer"),
        "head_sha": SHA_FIELD,
        "base_sha": SHA_FIELD,
        "merge_base": SHA_FIELD,
        "written_at": kind(is_utc, "an ISO 8601 UTC time such as 2026-01-31T12:00:00Z"),
    }


def no_constants(name):
    raise ValueError(name)


def no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def parse(data):
    """JSON bytes to a value; NaN, Infinity and duplicate keys raise ValueError."""
    return json.loads(data.decode("utf-8"), parse_constant=no_constants, object_pairs_hook=no_duplicates)


def listed(errs):
    """At most MAX_ERRORS messages, one per indented line."""
    more = [f"... and {len(errs) - MAX_ERRORS} more"] if len(errs) > MAX_ERRORS else []
    return "\n  " + "\n  ".join(errs[:MAX_ERRORS] + more)


def run_git(args, err, cwd=None):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd,
                              env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        raise err(f"can't run git: {e.strerror}")


def git(*args, cwd=None):
    return run_git(args, Usage, cwd)


def common_dir(err=Usage, not_repo="not in a git repository: nothing written"):
    r = run_git(["rev-parse", "--git-common-dir"], err)
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(os.path.join(os.getcwd(), r.stdout.rstrip("\n")))
    if r.returncode == 128 and "not a git repository" in r.stderr:
        raise err(not_repo)
    raise err(f"can't find the git common dir: git exited {r.returncode}")


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
        r = parse(data)
    except (ValueError, RecursionError):
        raise Usage("result is not valid JSON (or has NaN, Infinity or a duplicate key)")
    if not isinstance(r, dict):
        raise Usage("result must be a JSON object")
    return r


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


def run(main, prog):
    """Run a CLI's main; an unexpected error exits 4 without a traceback."""
    try:
        main()
    except OSError as e:
        print(f"{prog}: internal error: {e.strerror or type(e).__name__}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"{prog}: internal error: {type(e).__name__}", file=sys.stderr)
        sys.exit(4)


class Writer:
    """write-result.py for one skill. fields maps each skill-specific field to
    its check, in file order; consistent(r, errs) adds the cross-field rules for
    a result whose fields all passed; computed maps a field to a function of
    the result that fills it in when missing (before validation)."""

    def __init__(self, skill, fields, consistent, computed=None):
        self.skill = skill
        self.common = common_fields(skill)
        self.fields = fields
        self.consistent = consistent
        self.computed = computed or {}

    def validate(self, r, strict=True):
        """Every problem with result r, as a list of messages that don't echo its
        values. With strict=False (readers), unknown fields are allowed."""
        errs = []
        obj({**self.common, **self.fields})(r, "", errs, strict)
        if not errs:
            self.consistent(r, errs)
        return errs

    def write(self, top, r):
        fds = []
        try:
            fds.append(os.open(top, os.O_RDONLY | os.O_DIRECTORY))
            fds.append(open_dir("vstack", fds[-1], "vstack"))
            fds.append(open_dir(str(r["pr"]), fds[-1], f"vstack/{r['pr']}"))
            replace(fds[-1], f"{self.skill}.json", (json.dumps(r, indent=2) + "\n").encode())
        finally:
            for fd in fds:
                os.close(fd)
        return os.path.join(top, "vstack", str(r["pr"]), f"{self.skill}.json")

    def main(self):
        ap = argparse.ArgumentParser(prog="write-result.py")
        ap.add_argument("--pr")
        for opt in SHA_OPTS.values():
            ap.add_argument(opt, required=True)
        ap.add_argument("--file", default="-")
        a = ap.parse_args()
        try:
            given = {"schema_version": SCHEMA_VERSION, "skill": self.skill}
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
            for k, compute in self.computed.items():
                r.setdefault(k, compute(r))
            errs += self.validate(r)
            if errs:
                raise Usage("invalid result:" + listed(errs))
            path = self.write(top, {k: r[k] for k in (*self.common, *self.fields)})
        except Usage as e:
            print(f"write-result: {e}", file=sys.stderr)
            sys.exit(2)
        print(path)


class Reader:
    """read-result.py for the skill whose loaded write-result.py is writer: its
    SKILL names the file and its validate checks it."""

    def __init__(self, writer):
        self.skill = writer.SKILL
        self.validate = writer.validate

    @staticmethod
    def merge_base(top, base, head):
        r = run_git(["--git-dir", top, "merge-base", "--all", base, head], Unusable)
        if r.returncode not in (0, 1):
            raise Unsupported(f"can't recompute merge_base: git exited {r.returncode} (fetch the head and base first)")
        shas = r.stdout.split()
        if len(shas) != 1:
            raise Unsupported(f"base_sha and head_sha have {'no' if not shas else 'several'} merge bases; "
                              "need exactly one")
        return shas[0]

    def load(self, top, pr):
        shown = f"vstack/{pr}/{self.skill}.json"
        fds = []
        try:
            fds.append(os.open(top, os.O_RDONLY | os.O_DIRECTORY))
            for name in ("vstack", str(pr)):
                fds.append(os.open(name, DIR_FLAGS, dir_fd=fds[-1]))
            fd = os.open(f"{self.skill}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
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
            return parse(data)
        except (ValueError, RecursionError):
            raise Unsupported(f"{shown} is not valid JSON")

    def read_result(self, pr, expect_head_sha, expect_base_sha, top=None):
        if not is_int(pr) or not 1 <= pr <= MAX_PR:
            raise Unusable("pr must be a positive integer")
        for name, sha in (("expect_head_sha", expect_head_sha), ("expect_base_sha", expect_base_sha)):
            if not is_sha(sha):
                raise Unusable(f"{name} must be a full SHA, 40 or 64 lowercase hex characters")
        top = top or common_dir(Unusable, "not in a git repository")
        r = self.load(top, pr)
        if not isinstance(r, dict):
            raise Unsupported("the result is not a JSON object")
        v = r.get("schema_version")
        if not is_int(v) or v < 1:
            raise Unsupported("schema_version is missing or not a positive integer")
        if v > SCHEMA_VERSION:
            raise Unsupported(f"schema_version {v} is newer than {SCHEMA_VERSION}, the newest this reads")
        if r.get("skill") != self.skill or not is_int(r.get("pr")) or r["pr"] != pr:
            raise Unsupported(f"the file isn't {self.skill}'s result for PR {pr}")
        for k in ("head_sha", "base_sha"):
            if not is_sha(r.get(k)):
                raise Unsupported(f"{k} is missing or not a full SHA")
        errs = self.validate(r, strict=False)
        if errs:
            raise Unsupported(f"vstack/{pr}/{self.skill}.json fails validation:" + listed(errs))
        if r["head_sha"] != expect_head_sha:
            raise Stale(f"stale: written for head {r['head_sha']}, the PR head is {expect_head_sha}", r)
        if r["base_sha"] != expect_base_sha:
            raise Stale(f"stale: written for base {r['base_sha']}, the PR base is {expect_base_sha}", r)
        if r["merge_base"] != self.merge_base(top, r["base_sha"], r["head_sha"]):
            raise Unsupported("merge_base doesn't match `git merge-base --all` of base_sha and head_sha")
        return r

    def main(self):
        ap = argparse.ArgumentParser(prog="read-result.py")
        ap.add_argument("--pr", required=True)
        ap.add_argument("--expect-head-sha", required=True)
        ap.add_argument("--expect-base-sha", required=True)
        a = ap.parse_args()
        try:
            if not PR_ARG.fullmatch(a.pr) or int(a.pr) > MAX_PR:
                raise Unusable("--pr must be a positive integer")
            r = self.read_result(int(a.pr), a.expect_head_sha, a.expect_base_sha)
        except Stale as e:
            print(f"read-result: {e}", file=sys.stderr)
            sys.exit(3)
        except Unusable as e:
            print(f"read-result: {e}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(r, indent=2))
