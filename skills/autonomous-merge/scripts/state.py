"""autonomous-merge's run state for one PR, in
<git-common-dir>/vstack/<pr>/autonomous-merge.json, with the caps and the
oscillation guard enforced here rather than left to the model.

Usage: python3 -I state.py <command> --pr N [options]

  init [--merge] [--continue] [--fresh]
                                 start, or resume the run in the file
  check --before review|fix      may another review or fix round start?
  record-review --head-sha H --base-sha B
                                 add review.json (read with review's
                                 read-result.py) as a review round
  finding --fingerprint FP --status rejected|deferred --reason TEXT
  fix-round --head-sha H [--fixed FP]...
                                 record the fix pushed after a review round
  record-babysit --head-sha H --base-sha B
                                 add babysit.json: threads, CI, its commits
  escalate --reason CODE --detail TEXT
                                 record an immediate handback and print it:
                                 security_blocker, ci_unreproducible,
                                 conflict_judgement, design_change,
                                 scope_change or escalated
  stop --outcome merged|ready|handback [--reason CODE] [--detail TEXT]
  show                           print the state
  reset                          delete the state file

`reset`, `init --continue` and `init --fresh` are for the user only: the model
never runs them. With no state file, init refuses (escalated) when review.json
or babysit.json already exists for the PR, unless --fresh.

A round is clean when review's merge_ok is true (Ship: no blocker, no major,
no unreviewed area) and it has no blocker or major. Caps: 3 review rounds, 6
fix rounds (review fixes plus babysit's commits, counted along first parents),
2 hours from started_at. A finding that returns in a review after it was
marked fixed, or a new commit that puts a file a recorded fix touched back to
its content before that fix, is oscillation. A review round whose blocker plus
major count isn't below the previous round's (when that one had any) is
no_progress. A blocker from the security or data-integrity reviewer is
security_blocker. Each records the stop and hands back. Once stopped, every
command but show, stop, reset and init hands the stop back; `init --continue`
clears a handback and starts a new 2-hour window, keeping counters and
fingerprints. A "ready" stop is cleared by plain init.

The caps are the constants below, never read from the file: a file whose caps
differ, whose started_at is in the future, or whose fix_rounds doesn't match
its fixes fails its checks.

Prints JSON on stdout: {"outcome": "ok", ...} (exit 0) or a handback (exit 5,
references/handbacks.md). Exit 2 usage (also -h, and a state file that fails
its checks), 4 internal error. The file is a cache, written atomically, never
through a symlink; see references/state-schema.md.
"""
import argparse
import datetime
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat

_spec = importlib.util.spec_from_file_location(
    "vstack_am_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c)

SKILL = "autonomous-merge"
SCHEMA_VERSION = 1
CAPS = {"review_rounds": 3, "fix_rounds": 6, "minutes": 120}
MAX_BYTES = 4 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
FP = re.compile(r"[0-9a-f]{16}")
PR_ARG = re.compile(r"[1-9][0-9]{0,9}")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
SEVERITIES = ("blocker", "major", "minor", "nit")
OUTCOMES = ("merged", "ready", "handback")
ESCALATIONS = ("security_blocker", "ci_unreproducible", "conflict_judgement", "design_change", "scope_change",
               "escalated")
SECURITY_RULES = ("security", "data-integrity")
BABYSIT_READER = os.path.join(c.HERE, "..", "..", "babysit", "scripts", "read-result.py")
RESULT_FILES = ("review.json", "babysit.json")
KEYS = ("schema_version", "skill", "pr", "merge", "started_at", "updated_at", "caps", "counters", "last_head",
        "rounds", "findings", "fixes", "threads", "ci", "stop")


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def stamp(t=None):
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def normalise(summary):
    s = re.sub(r"\d+", "#", summary.lower())
    return " ".join(re.sub(r"[^a-z0-9#]+", " ", s).split())


def fingerprint(file, rule, summary):
    key = "\0".join((file, rule.strip().lower(), normalise(summary)))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def git(*args):
    return c.run(["git", *args], env={**os.environ, "LC_ALL": "C"})


def common_dir():
    r = git("rev-parse", "--git-common-dir")
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(os.path.join(os.getcwd(), r.stdout.rstrip("\n")))
    raise c.Usage("not in a git repository" if "not a git repository" in r.stderr
                  else f"can't find the git common dir: git exited {r.returncode}")


def open_dir(name, parent, shown, create):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    try:
        return os.open(name, DIR_FLAGS, dir_fd=parent)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
            raise c.Usage(f"refusing: {shown} is a symlink or not a directory")
        raise


def pr_dir(top, pr, create):
    fds = [os.open(top, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        fds.append(open_dir("vstack", fds[-1], "vstack", create))
        fds.append(open_dir(str(pr), fds[-1], f"vstack/{pr}", create))
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    for fd in fds[:-1]:
        os.close(fd)
    return fds[-1]


def no_constants(name):
    raise ValueError(name)


def load(top, pr):
    shown = f"vstack/{pr}/{SKILL}.json"
    try:
        d = pr_dir(top, pr, create=False)
    except FileNotFoundError:
        return None
    try:
        fd = os.open(f"{SKILL}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK):
            raise c.Usage(f"refusing: {shown} is a symlink")
        raise
    finally:
        os.close(d)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise c.Usage(f"refusing: {shown} is not a regular file")
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise c.Usage(f"{shown} is over {MAX_BYTES} bytes")
    try:
        s = json.loads(data.decode("utf-8"), parse_constant=no_constants)
    except (ValueError, RecursionError):
        raise c.Usage(f"{shown} is not valid JSON; fix or `reset` it")
    errs = problems(s, pr)
    if errs:
        raise c.Usage(f"{shown} fails its checks ({'; '.join(errs[:5])}); fix or `reset` it")
    return s


def problems(s, pr):
    if not isinstance(s, dict):
        return ["not an object"]
    errs = [f"{k}: missing" for k in KEYS if k not in s]
    if errs:
        return errs
    if s["schema_version"] != SCHEMA_VERSION or s["skill"] != SKILL or s["pr"] != pr:
        errs.append("not this skill's state for this PR, or a schema_version this doesn't read")
    if s["caps"] != CAPS:
        errs.append(f"caps: must be {json.dumps(CAPS)}")
    try:
        if parse_stamp(s["started_at"]) > now():
            errs.append("started_at: in the future")
    except (TypeError, ValueError):
        errs.append("started_at: not a UTC time")
    counters = s["counters"]
    if not (isinstance(counters, dict) and all(isinstance(counters.get(k), int) and not isinstance(counters.get(k), bool)
                                               and counters[k] >= 0 for k in ("review_rounds", "fix_rounds"))):
        errs.append("counters: need non-negative review_rounds and fix_rounds")
    for k in ("rounds", "findings", "fixes", "threads", "ci"):
        if not isinstance(s[k], list) or not all(isinstance(x, dict) for x in s[k]):
            errs.append(f"{k}: must be a list of objects")
    if s["last_head"] is not None and not (isinstance(s["last_head"], str) and SHA.fullmatch(s["last_head"])):
        errs.append("last_head: must be a full SHA or null")
    if s["stop"] is not None and not (isinstance(s["stop"], dict) and s["stop"].get("outcome") in OUTCOMES):
        errs.append("stop: must be null or an object with an outcome")
    if not errs and len(s["rounds"]) != counters["review_rounds"]:
        errs.append("counters.review_rounds doesn't match rounds")
    if not errs:
        if not all(f.get("source") in ("review", "babysit") and isinstance(f.get("commits"), list) and f["commits"]
                   and all(isinstance(x, str) and SHA.fullmatch(x) for x in f["commits"]) for f in s["fixes"]):
            errs.append("fixes: each needs a source of review or babysit and its commits")
        elif counters["fix_rounds"] != sum(1 if f["source"] == "review" else len(f["commits"]) for f in s["fixes"]):
            errs.append("counters.fix_rounds doesn't match fixes")
    return errs


def save(top, s):
    s["updated_at"] = stamp()
    data = (json.dumps(s, indent=2) + "\n").encode()
    d = pr_dir(top, s["pr"], create=True)
    tmp = f".{SKILL}.json.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, f"{SKILL}.json", src_dir_fd=d, dst_dir_fd=d)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=d)
            except OSError:
                pass
            raise
    finally:
        os.close(d)


def new_state(pr, merge):
    t = stamp()
    return {"schema_version": SCHEMA_VERSION, "skill": SKILL, "pr": pr, "merge": merge, "started_at": t,
            "updated_at": t, "caps": dict(CAPS), "counters": {"review_rounds": 0, "fix_rounds": 0},
            "last_head": None, "rounds": [], "findings": [], "fixes": [], "threads": [], "ci": [], "stop": None}


ACTIONS = {
    "cap_reached": "Look at the state file's rounds and findings, finish the PR by hand or rerun with "
                   "`init --continue` (new 2-hour window, same round counters) or `reset` to start over.",
    "oscillation": "A fix is being undone or a fixed finding came back: decide which version is right, "
                   "then rerun with `init --continue`.",
    "no_progress": "The fixes aren't reducing the review's blockers and majors: look at the findings, fix or "
                   "decide them by hand, then rerun with `init --continue`.",
    "security_blocker": "Review the security or data-integrity blocker yourself and fix it (autonomous-merge "
                        "doesn't), then rerun with `init --continue`.",
    "ci_unreproducible": "Look at the failing check's logs and fix or rerun it yourself, then rerun with "
                         "`init --continue`.",
    "conflict_judgement": "Resolve the conflict with the base yourself, choosing which side wins, then rerun with "
                          "`init --continue`.",
    "design_change": "Decide on the requested design change and reply to it, then rerun with `init --continue`.",
    "scope_change": "Decide whether the PR's scope should change and reply to it, then rerun with "
                    "`init --continue`.",
    "escalated": "Make the decision described in the detail, then rerun with `init --continue`.",
}


def stop(top, s, reason, detail, action=None):
    s["stop"] = {"outcome": "handback", "reason": reason, "detail": c.one_line(detail, 1000), "at": stamp()}
    save(top, s)
    raise c.Handback(reason, detail, action or ACTIONS.get(reason, "See the detail, then rerun."))


def stopped(s):
    st = s["stop"]
    if st and st["outcome"] != "ready":
        if st["outcome"] == "merged":
            raise c.Handback("not_open", "this run already merged the PR", "Nothing left to do.")
        raise c.Handback(st.get("reason") if st.get("reason") in c.REASONS else "unknown",
                         f"the run stopped earlier: {st.get('detail') or st.get('reason')}",
                         "Deal with that, then `init --continue` to resume (or `reset` to start over).")


def elapsed_minutes(s):
    return (now() - parse_stamp(s["started_at"])).total_seconds() / 60


def enforce_caps(top, s):
    caps, n = CAPS, s["counters"]
    if n["fix_rounds"] > caps["fix_rounds"]:
        stop(top, s, "cap_reached", f"{n['fix_rounds']} fix rounds, over the cap of {caps['fix_rounds']}")
    if elapsed_minutes(s) >= caps["minutes"]:
        stop(top, s, "cap_reached", f"{caps['minutes']} minutes have passed since {s['started_at']}")


def loaded(top, pr):
    s = load(top, pr)
    if s is None:
        raise c.Usage(f"no state for PR {pr}; run `state.py init --pr {pr}` first")
    stopped(s)
    return s


def need_commit(sha, opt):
    if not SHA.fullmatch(sha or ""):
        raise c.Usage(f"{opt} must be a full SHA, 40 or 64 lowercase hex characters")
    if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise c.Usage(f"{opt} is not a commit in this repo (fetch it first)")


def first_parent_commits(old, new):
    if old is None or old == new:
        return []
    r = git("rev-list", "--first-parent", "--reverse", f"{old}..{new}")
    if r.returncode != 0:
        raise c.Usage("can't list the commits since the last recorded head (was history rewritten?)")
    return r.stdout.split()


def blob(commit, path):
    r = git("rev-parse", "--verify", "--quiet", f"{commit}:{path}")
    return r.stdout.strip() if r.returncode == 0 else None


def changed_files(commit):
    r = git("diff-tree", "--no-commit-id", "-r", "-z", "--name-only", "-m", "--first-parent", commit)
    return sorted({p for p in r.stdout.split("\0") if p}) if r.returncode == 0 else []


def reverted(prior, commits):
    """(new commit, earlier fix commit, path) for the first new commit that puts a
    path an earlier fix changed back to its content before that fix."""
    earlier = list(prior)
    for new in commits:
        touched = set(changed_files(new))
        for old in earlier:
            if old == new:
                continue
            for path in changed_files(old):
                if path not in touched:
                    continue
                before_fix = blob(f"{old}^", path)
                if blob(new, path) == before_fix and blob(f"{new}^", path) != before_fix:
                    return new, old, path
        earlier.append(new)
    return None


def fix_commits(s):
    return [x for f in s["fixes"] for x in f["commits"]]


def guard_reverts(top, s, prior, commits):
    hit = reverted(prior, commits)
    if hit:
        new, old, path = hit
        stop(top, s, "oscillation", f"commit {new[:12]} puts {path} back to how it was before fix {old[:12]}")


def earlier_results(top, pr):
    """The sibling skills' result files already in vstack/<pr>/."""
    try:
        d = pr_dir(top, pr, create=False)
    except FileNotFoundError:
        return []
    found = []
    try:
        for name in RESULT_FILES:
            try:
                os.stat(name, dir_fd=d, follow_symlinks=False)
                found.append(name)
            except FileNotFoundError:
                pass
    finally:
        os.close(d)
    return found


def cmd_init(top, a):
    s = load(top, a.pr)
    resumed = s is not None
    if s is None:
        found = earlier_results(top, a.pr)
        if found and not a.fresh:
            raise c.Handback("escalated", f"{' and '.join(found)} already exist{'s' if len(found) == 1 else ''} "
                                          f"for PR {a.pr} but there's no "
                                          f"{SKILL} state: they're from an earlier run, or its state was reset",
                             f"If a new run should start anyway, run `state.py init --pr {a.pr} --fresh` "
                             "yourself, then rerun autonomous-merge.")
        s = new_state(a.pr, a.merge)
    else:
        st = s["stop"]
        if st and st["outcome"] == "handback" and a.cont:
            s["stop"], s["started_at"] = None, stamp()
        elif st and st["outcome"] == "ready":
            s["stop"] = None
        stopped(s)
        s["merge"] = a.merge
    save(top, s)
    return {"outcome": "ok", "resumed": resumed, **summary(s)}


def summary(s):
    left = max(0, CAPS["minutes"] - elapsed_minutes(s))
    return {"pr": s["pr"], "merge": s["merge"], "started_at": s["started_at"], "minutes_left": int(left),
            "review_rounds": s["counters"]["review_rounds"], "fix_rounds": s["counters"]["fix_rounds"],
            "fix_rounds_left": max(0, CAPS["fix_rounds"] - s["counters"]["fix_rounds"]),
            "last_head": s["last_head"],
            "open_findings": [f for f in s["findings"] if f["status"] == "open"]}


def cmd_check(top, a):
    s = loaded(top, a.pr)
    enforce_caps(top, s)
    caps, n = CAPS, s["counters"]
    if a.before == "review" and n["review_rounds"] >= caps["review_rounds"]:
        stop(top, s, "cap_reached", f"{n['review_rounds']} review rounds used, the cap is {caps['review_rounds']}")
    if a.before == "fix":
        if n["fix_rounds"] >= caps["fix_rounds"]:
            stop(top, s, "cap_reached", f"{n['fix_rounds']} fix rounds used, the cap is {caps['fix_rounds']}")
        if s["rounds"] and not clean(s["rounds"][-1]) and n["review_rounds"] >= caps["review_rounds"]:
            stop(top, s, "cap_reached", f"review round {n['review_rounds']} still isn't clean and no review "
                                        f"round is left to check a fix")
    return {"outcome": "ok", **summary(s)}


def read_review(pr, head, base):
    if not os.path.exists(c.REVIEW_READER):
        raise c.Handback("needs_review", "the review skill isn't installed next to this one",
                         "Install the whole vstack plugin, then rerun.")
    reader = c.load_module("vstack_review_read_result", c.REVIEW_READER)
    try:
        return reader.read_result(pr, head, base)
    except reader.Stale:
        raise c.Handback("needs_review", "review.json is for another head or base",
                         "Run the review skill on the current head, then record it again.")
    except reader.Unusable as e:
        raise c.Handback("needs_review", f"no usable review.json: {c.one_line(e, 300)}",
                         "Run the review skill on the PR (it writes review.json), then record it again.")


def cmd_record_review(top, a):
    s = loaded(top, a.pr)
    need_commit(a.head_sha, "--head-sha")
    need_commit(a.base_sha, "--base-sha")
    if s["rounds"] and s["rounds"][-1]["head"] == a.head_sha and s["rounds"][-1]["base"] == a.base_sha:
        return {"outcome": "ok", "repeat": True, **round_report(s, s["rounds"][-1])}
    r = read_review(a.pr, a.head_sha, a.base_sha)
    enforce_caps(top, s)
    if s["counters"]["review_rounds"] >= CAPS["review_rounds"]:
        stop(top, s, "cap_reached", f"a review round {s['counters']['review_rounds'] + 1} is over the cap of "
                                    f"{CAPS['review_rounds']}")
    commits = first_parent_commits(s["last_head"], a.head_sha) if s["last_head"] else []
    n = s["counters"]["review_rounds"] + 1
    counts = {k: 0 for k in SEVERITIES}
    by_fp = {f["fingerprint"]: f for f in s["findings"]}
    back = []
    for f in r["findings"]:
        counts[f["severity"]] += 1
        fp = fingerprint(f["file"], f["rule"], f["summary"])
        old = by_fp.get(fp)
        if old is None:
            old = {"fingerprint": fp, "file": f["file"], "line": f["line"], "rule": f["rule"],
                   "summary": f["summary"], "severity": f["severity"], "first_round": n, "last_round": n,
                   "status": "open" if f["severity"] in ("blocker", "major") else "deferred",
                   "commit": None, "reason": None if f["severity"] in ("blocker", "major") else "minor: logged, not fixed"}
            s["findings"].append(old)
            by_fp[fp] = old
            continue
        if old["status"] == "fixed" and old["last_round"] < n:
            back.append(old)
        old.update(last_round=n, line=f["line"], severity=f["severity"])
    prev = s["rounds"][-1] if s["rounds"] else None
    rnd = {"round": n, "head": a.head_sha, "base": a.base_sha, "status": r["status"], "verdict": r["verdict"],
           "counts": counts, "merge_ok": r["merge_ok"], "at": stamp()}
    s["rounds"].append(rnd)
    s["counters"]["review_rounds"] = n
    s["last_head"] = a.head_sha
    security = [f for f in r["findings"] if f["severity"] == "blocker" and f["rule"].strip().lower() in SECURITY_RULES]
    if security:
        f = security[0]
        stop(top, s, "security_blocker", f"round {n} has a {f['rule']} blocker: {f['file']}: {f['summary']}")
    if back:
        f = back[0]
        stop(top, s, "oscillation", f"finding {f['fingerprint']} ({f['file']}: {f['summary']}) came back in round "
                                    f"{n} after fix {str(f['commit'])[:12]}")
    guard_reverts(top, s, fix_commits(s), commits)
    before = serious((prev or {}).get("counts"))
    if before and not clean(rnd) and serious(counts) >= before:
        stop(top, s, "no_progress", f"review round {n} has {serious(counts)} blocker(s) and major(s), round {n - 1} "
                                    f"had {before}: the fixes didn't reduce them")
    save(top, s)
    return {"outcome": "ok", "repeat": False, **round_report(s, rnd)}


def serious(counts):
    counts = counts if isinstance(counts, dict) else {}
    return sum(counts.get(k) or 0 for k in ("blocker", "major"))


def clean(rnd):
    return rnd.get("merge_ok") is True and serious(rnd.get("counts")) == 0


def round_report(s, rnd):
    return {"round": rnd["round"], "clean": clean(rnd), "verdict": rnd["verdict"], "counts": rnd["counts"],
            "to_fix": [f for f in s["findings"] if f["status"] == "open" and f["last_round"] == rnd["round"]],
            "deferred": [f for f in s["findings"] if f["status"] == "deferred"],
            "review_rounds_left": CAPS["review_rounds"] - s["counters"]["review_rounds"],
            "fix_rounds_left": max(0, CAPS["fix_rounds"] - s["counters"]["fix_rounds"])}


def find(s, fp):
    for f in s["findings"]:
        if f["fingerprint"] == fp:
            return f
    raise c.Usage(f"no finding {fp} in the state")


def cmd_finding(top, a):
    s = loaded(top, a.pr)
    f = find(s, a.fingerprint)
    if f["status"] == "fixed":
        raise c.Usage(f"finding {a.fingerprint} is already fixed in {f['commit']}")
    f.update(status=a.status, reason=c.one_line(a.reason, 500))
    save(top, s)
    return {"outcome": "ok", "finding": f}


def cmd_fix_round(top, a):
    s = loaded(top, a.pr)
    need_commit(a.head_sha, "--head-sha")
    fixed = [find(s, fp) for fp in a.fixed]
    commits = first_parent_commits(s["last_head"], a.head_sha)
    if not commits:
        raise c.Usage("no new commit since the last recorded head; push the fix first")
    prior = fix_commits(s)
    s["counters"]["fix_rounds"] += 1
    s["fixes"].append({"round": s["counters"]["fix_rounds"], "source": "review", "head": a.head_sha,
                       "commits": commits, "at": stamp()})
    for f in fixed:
        f.update(status="fixed", commit=commits[-1], reason=None)
    guard_reverts(top, s, prior, commits)
    s["last_head"] = a.head_sha
    enforce_caps(top, s)
    save(top, s)
    return {"outcome": "ok", **summary(s)}


def cmd_record_babysit(top, a):
    s = loaded(top, a.pr)
    need_commit(a.head_sha, "--head-sha")
    need_commit(a.base_sha, "--base-sha")
    if not os.path.exists(BABYSIT_READER):
        raise c.Usage("the babysit skill isn't installed next to this one")
    reader = c.load_module("vstack_babysit_read_result", BABYSIT_READER)
    try:
        b = reader.read_result(a.pr, a.head_sha, a.base_sha)
    except reader.Stale:
        raise c.Usage("babysit.json is for another head or base; rerun babysit or pass the PR's current SHAs")
    except reader.Unusable as e:
        raise c.Usage(f"no usable babysit.json: {c.one_line(e, 300)}")
    commits = first_parent_commits(s["last_head"], a.head_sha)
    prior = fix_commits(s)
    if commits:
        s["counters"]["fix_rounds"] += len(commits)
        s["fixes"].append({"round": s["counters"]["fix_rounds"], "source": "babysit", "head": a.head_sha,
                           "commits": commits, "at": stamp()})
    s["threads"] = [{k: t[k] for k in ("id", "author", "is_bot", "state", "decision", "reply_url")}
                    for t in b["threads"]]
    s["ci"].append({"head": a.head_sha, "state": b["ci"]["state"], "failing_checks": b["ci"]["failing_checks"],
                    "babysit_status": b["status"], "at": stamp()})
    guard_reverts(top, s, prior, commits)
    s["last_head"] = a.head_sha
    enforce_caps(top, s)
    save(top, s)
    return {"outcome": "ok", "babysit_status": b["status"], "new_commits": len(commits), **summary(s)}


def cmd_escalate(top, a):
    s = loaded(top, a.pr)
    if not c.one_line(a.detail):
        raise c.Usage("--detail can't be empty")
    stop(top, s, a.reason, a.detail)


def cmd_stop(top, a):
    s = load(top, a.pr)
    if s is None:
        raise c.Usage(f"no state for PR {a.pr}")
    if a.outcome == "handback" and a.reason not in c.REASONS:
        raise c.Usage("a handback needs --reason, one of: " + ", ".join(c.REASONS))
    s["stop"] = {"outcome": a.outcome, "reason": a.reason, "detail": c.one_line(a.detail or "", 1000) or None,
                 "at": stamp()}
    save(top, s)
    return {"outcome": "ok", "stop": s["stop"]}


def cmd_show(top, a):
    s = load(top, a.pr)
    if s is None:
        raise c.Usage(f"no state for PR {a.pr}")
    return s


def cmd_reset(top, a):
    try:
        d = pr_dir(top, a.pr, create=False)
    except FileNotFoundError:
        return {"outcome": "ok", "removed": False}
    try:
        os.unlink(f"{SKILL}.json", dir_fd=d)
        removed = True
    except FileNotFoundError:
        removed = False
    finally:
        os.close(d)
    return {"outcome": "ok", "removed": removed}


def args():
    ap = argparse.ArgumentParser(prog="state.py", add_help=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        p = sub.add_parser(name, add_help=False)
        p.add_argument("--pr", required=True)
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init)
    p.add_argument("--merge", action="store_true")
    p.add_argument("--continue", dest="cont", action="store_true")
    p.add_argument("--fresh", action="store_true")
    add("check", cmd_check).add_argument("--before", choices=("review", "fix"), required=True)
    p = add("record-review", cmd_record_review)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--base-sha", required=True)
    p = add("finding", cmd_finding)
    p.add_argument("--fingerprint", required=True)
    p.add_argument("--status", choices=("rejected", "deferred"), required=True)
    p.add_argument("--reason", required=True)
    p = add("fix-round", cmd_fix_round)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--fixed", action="append", default=[])
    p = add("record-babysit", cmd_record_babysit)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--base-sha", required=True)
    p = add("escalate", cmd_escalate)
    p.add_argument("--reason", choices=ESCALATIONS, required=True)
    p.add_argument("--detail", required=True)
    p = add("stop", cmd_stop)
    p.add_argument("--outcome", choices=OUTCOMES, required=True)
    p.add_argument("--reason")
    p.add_argument("--detail")
    add("show", cmd_show)
    add("reset", cmd_reset)
    return ap.parse_args()


def main():
    a = args()
    if not PR_ARG.fullmatch(a.pr) or int(a.pr) > c.MAX_PR:
        raise c.Usage("--pr must be a positive integer")
    a.pr = int(a.pr)
    if getattr(a, "reason", None) is not None and not c.one_line(a.reason):
        raise c.Usage("--reason can't be empty")
    for fp in [getattr(a, "fingerprint", None), *getattr(a, "fixed", [])]:
        if fp is not None and not FP.fullmatch(fp):
            raise c.Usage("a fingerprint is 16 lowercase hex characters")
    c.emit(a.fn(common_dir(), a))
    return c.OK


if __name__ == "__main__":
    c.main_wrapper("state", main)
