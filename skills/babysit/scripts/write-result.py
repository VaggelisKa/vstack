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

This file holds only what is babysit's own: SKILL, FIELDS and consistent().
The validation combinators, the git checks, the atomic write and the CLI are
shared with review's write-result.py and live in lib/result_file.py at the
plugin root, loaded by path because this runs under python3 -I.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location("vstack_result_file", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "result_file.py"))
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

SKILL = "babysit"
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
        "reply_url": kind(lambda v: v is None or rf.is_text(v), "a non-empty string or null"),
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


def all_green(r):
    return (r["ci"]["state"] in ("passing", "none") and r["approved_on_head"] and r["mergeable"] is True
            and not r["changes_requested"] and not any(t["state"] == "open" for t in r["threads"])
            and all(c["state"] == "passing" for c in r["required_checks"]))


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
    if r["status"] == "failed" and all_green(r):
        errs.append("status: failed can't have everything green (CI passing or none, approved, mergeable, "
                    "no changes requested, no open thread); that's green")
    ids = [t["id"] for t in r["threads"]]
    if len(ids) != len(set(ids)):
        errs.append("threads: duplicate id")
    names = [c["name"] for c in r["required_checks"]]
    if len(names) != len(set(names)):
        errs.append("required_checks: duplicate name")
    if any(t["state"] == "resolved" and t["decision"] == "escalated" for t in r["threads"]):
        errs.append("threads: a resolved thread can't have decision escalated")


_writer = rf.Writer(SKILL, FIELDS, consistent)
COMMON = _writer.common
validate = _writer.validate
write = _writer.write
main = _writer.main

if __name__ == "__main__":
    rf.run(main, "write-result")
