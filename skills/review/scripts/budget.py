"""Hold one review run to its reviewer budget: a hard cap on reviewer agents started.

Usage: python3 -I budget.py plan <session> --budget N   < plan.json
       python3 -I budget.py start <session> <id> [--retry]
       python3 -I budget.py status <session>

<session> is the folder session.py made. The ledger is <session>/budget.json.

plan records the reviewers the printed plan starts, as JSON on stdin:
  {"reviewers": [{"id": "R1", "area": "api/auth", "lens": "security"}, ...]}
It refuses a plan with more reviewers than N, duplicate or malformed ids, or a
second plan once any reviewer has started (a new plan is allowed before that).

start records one launch, and must run before every reviewer agent starts,
on any harness, retries included. It refuses (exit 1, nothing recorded) when:
  - the id isn't in the plan (no reviewer is added after the plan is printed),
  - the id already started and --retry isn't given, or it was already retried,
  - N launches have already been recorded (the cap counts retries too).
Skeptics are not reviewers and don't go through this script.

status prints the ledger as one JSON line.

Prints one line on stdout and exits 0 ok, 1 refused (the reason on stdout),
2 usage or I/O error (message on stderr).
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import sys

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
MAX_BUDGET = 64
LEDGER = "budget.json"


def fail(msg):
    print(f"budget: {msg}", file=sys.stderr)
    sys.exit(2)


def refuse(msg):
    print(f"refused: {msg}")
    sys.exit(1)


@contextlib.contextmanager
def locked(session):
    if not os.path.isdir(session) or os.path.islink(session):
        fail(f"no session folder at {session}")
    lock = os.path.join(session, ".budget.lock")
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        fail(f"can't open {lock}: {e.strerror}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield os.path.join(session, LEDGER)
    finally:
        os.close(fd)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        fail(f"can't read the ledger {path}: {e}")


def save(path, ledger):
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(ledger, f, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        fail(f"can't write the ledger {path}: {e.strerror}")


def summary(ledger):
    used = sum(ledger["launches"].values())
    return {"budget": ledger["budget"], "planned": len(ledger["reviewers"]),
            "launched": used, "left": ledger["budget"] - used,
            "launches": ledger["launches"]}


def cmd_plan(session, budget):
    if not 1 <= budget <= MAX_BUDGET:
        fail(f"--budget must be 1-{MAX_BUDGET}")
    try:
        data = json.loads(sys.stdin.read())
    except ValueError as e:
        fail(f"plan on stdin isn't JSON: {e}")
    reviewers = data.get("reviewers") if isinstance(data, dict) else None
    if not isinstance(reviewers, list) or not reviewers:
        fail('plan needs {"reviewers": [{"id", "area", "lens"}, ...]} with at least one reviewer')
    seen = []
    for r in reviewers:
        if not isinstance(r, dict) or not all(isinstance(r.get(k), str) and r.get(k) for k in ("id", "area", "lens")):
            fail("every reviewer needs a non-empty id, area and lens")
        if not ID.fullmatch(r["id"]):
            fail(f"bad reviewer id {r['id'][:40]!r}")
        if r["id"] in seen:
            fail(f"duplicate reviewer id {r['id']}")
        seen.append(r["id"])
    with locked(session) as path:
        old = load(path)
        if old and sum(old["launches"].values()):
            refuse("reviewers have already started; the plan can't change now")
        if len(reviewers) > budget:
            refuse(f"the plan has {len(reviewers)} reviewers and the budget is {budget}: "
                   f"drop {len(reviewers) - budget} before starting any")
        ledger = {"budget": budget,
                  "reviewers": [{k: r[k] for k in ("id", "area", "lens")} for r in reviewers],
                  "launches": {}}
        save(path, ledger)
    print(json.dumps({"status": "ok", **summary(ledger)}))


def cmd_start(session, rid, retry):
    with locked(session) as path:
        ledger = load(path)
        if ledger is None:
            refuse("no plan recorded: run `budget.py plan` first")
        if rid not in {r["id"] for r in ledger["reviewers"]}:
            refuse(f"{rid[:40]} isn't in the plan; reviewers can't be added after it")
        n = ledger["launches"].get(rid, 0)
        if n and not retry:
            refuse(f"{rid} already started; pass --retry for its one re-run")
        if n >= 2:
            refuse(f"{rid} already had its one re-run")
        if retry and not n:
            refuse(f"{rid} hasn't started yet, so there's nothing to retry")
        used = sum(ledger["launches"].values())
        if used >= ledger["budget"]:
            refuse(f"all {ledger['budget']} reviewer launches are used; {rid} gets no "
                   f"{'re-run' if retry else 'launch'} (record it as no verdict)")
        ledger["launches"][rid] = n + 1
        save(path, ledger)
    left = ledger["budget"] - used - 1
    print(f"ok: {rid}{' retry' if retry else ''} is launch {used + 1} of {ledger['budget']} ({left} left)")


def main():
    p = argparse.ArgumentParser(prog="budget.py", add_help=False)
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("plan", add_help=False)
    a.add_argument("session")
    a.add_argument("--budget", type=int, required=True)
    b = sub.add_parser("start", add_help=False)
    b.add_argument("session")
    b.add_argument("id")
    b.add_argument("--retry", action="store_true")
    c = sub.add_parser("status", add_help=False)
    c.add_argument("session")
    try:
        args = p.parse_args()
    except SystemExit:
        fail("usage: budget.py plan <session> --budget N | start <session> <id> [--retry] | status <session>")
    if args.cmd == "plan":
        cmd_plan(args.session, args.budget)
    elif args.cmd == "start":
        cmd_start(args.session, args.id, args.retry)
    elif args.cmd == "status":
        with locked(args.session) as path:
            ledger = load(path)
        if ledger is None:
            refuse("no plan recorded")
        print(json.dumps(summary(ledger)))
    else:
        fail("usage: budget.py plan <session> --budget N | start <session> <id> [--retry] | status <session>")


if __name__ == "__main__":
    main()
