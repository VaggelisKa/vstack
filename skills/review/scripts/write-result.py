"""Write the review's result for one PR to <git-common-dir>/vstack/<pr>/review.json,
where other skills can read it.

Usage: python3 -I write-result.py [--pr N] --head-sha SHA --base-sha SHA [--file PATH]

The result is a JSON object on stdin, or in --file, matching
references/result-schema.md. schema_version, skill, pr (from --pr), head_sha,
base_sha (from their options), merge_base
(`git merge-base --all`, which must give exactly one commit), merge_ok (computed) and written_at
(now, UTC) are filled in when missing; a value given both in the result and as
an option, or computed, must agree. Unknown fields are rejected. Each SHA must
be a full 40 or 64 hex commit that exists in the repo.

The file goes in `git rev-parse --git-common-dir`, so it can't be committed and
every worktree of the repo shares it. Outside a git repo nothing is written. A
vstack or <pr> folder that is a symlink or not a directory is refused. The JSON
is written to a temp file in the same folder and renamed over the old one, so a
reader sees the old file or the new one, never part of one. Prints the path
written.

Exits 0 ok, 2 usage or validation error (reasons on stderr; a result that fails
validation writes nothing), 4 internal error.

This file holds only what is review's own: SKILL, FIELDS, consistent() and
merge_ok. The validation combinators, the git checks, the atomic write and the
CLI are shared with babysit's write-result.py and live in lib/result_file.py at
the plugin root, loaded by path because this runs under python3 -I.
"""
import importlib.util
import os
import re

_spec = importlib.util.spec_from_file_location("vstack_result_file", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "result_file.py"))
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

SKILL = "review"
SCHEMA_VERSION = rf.SCHEMA_VERSION
MAX_BYTES = rf.MAX_BYTES
MAX_ERRORS = rf.MAX_ERRORS
MAX_PR = rf.MAX_PR
PR_ARG = rf.PR_ARG
SHA = rf.SHA
DIR_FLAGS = rf.DIR_FLAGS
is_int = rf.is_int
no_constants = rf.no_constants
no_duplicates = rf.no_duplicates
kind, enum, list_of, obj = rf.kind, rf.enum, rf.list_of, rf.obj
TEXT, BOOL, LINE = rf.TEXT, rf.BOOL, rf.LINE

DECISION = re.compile(r"(Ship with fixes|Ship|Rethink|Incomplete)(, based on .+)?")
STATUS_FOR = {"Ship": "ship", "Ship with fixes": "fix", "Rethink": "rethink", "Incomplete": "unreviewed"}


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_verdict(v):
    return rf.is_line(v) and DECISION.fullmatch(v)


def is_repo_path(v):
    if not rf.is_text(v) or "\\" in v or "\0" in v or v.startswith("/") or re.match(r"[A-Za-z]:", v):
        return False
    return ".." not in v.split("/")


FIELDS = {
    "status": enum("ship", "fix", "rethink", "unreviewed"),
    "verdict": kind(is_verdict, "one line with no control characters: Ship, Ship with fixes, Rethink "
                                "or Incomplete, optionally followed by ', based on ...'"),
    "score": kind(lambda v: v is None or (is_number(v) and 0 <= v <= 10), "a number from 0 to 10 or null"),
    "findings": list_of(obj({
        "id": LINE,
        "severity": enum("blocker", "major", "minor", "nit"),
        "file": kind(is_repo_path, "a repo-relative path (no leading /, drive, backslash or .. segment)"),
        "line": kind(lambda v: v is None or (is_int(v) and v >= 1), "an integer >= 1 or null"),
        "rule": LINE,
        "summary": LINE,
        "verified": BOOL,
    })),
    "unreviewed_areas": list_of(TEXT),
    "partial": BOOL,
    "partial_k3": BOOL,
    "merge_ok": BOOL,
}


def merge_ok(r):
    return r.get("status") == "ship" and r.get("partial_k3") is False and r.get("unreviewed_areas") == []


def consistent(r, errs):
    decision = DECISION.fullmatch(r["verdict"]).group(1)
    if r["status"] != STATUS_FOR[decision]:
        errs.append(f"status: must be {STATUS_FOR[decision]} for {decision}")
    severities = {f["severity"] for f in r["findings"]}
    if decision == "Rethink" and "blocker" not in severities:
        errs.append("verdict: Rethink needs a blocker finding")
    if decision in ("Ship", "Ship with fixes", "Incomplete") and "blocker" in severities:
        errs.append(f"verdict: {decision} can't have a blocker finding")
    if decision == "Ship" and "major" in severities:
        errs.append("verdict: Ship can't have a major finding")
    if decision == "Ship with fixes" and "major" not in severities:
        errs.append("verdict: Ship with fixes needs a major finding")
    if r["score"] is None and decision != "Incomplete":
        errs.append("score: only Incomplete can have a null score")
    if r["partial_k3"] and not r["partial"]:
        errs.append("partial: must be true when partial_k3 is")
    if r["partial_k3"] and decision in ("Ship", "Ship with fixes"):
        errs.append(f"verdict: {decision} can't have a partial K=3 area (that's Incomplete)")
    if decision == "Ship" and r["unreviewed_areas"]:
        errs.append("verdict: Ship can't have unreviewed areas")
    if r["merge_ok"] != merge_ok(r):
        errs.append("merge_ok: must be true exactly when status is ship, partial_k3 is false "
                    "and unreviewed_areas is empty")


_writer = rf.Writer(SKILL, FIELDS, consistent, computed={"merge_ok": merge_ok})
COMMON = _writer.common
validate = _writer.validate
write = _writer.write
main = _writer.main

if __name__ == "__main__":
    rf.run(main, "write-result")
