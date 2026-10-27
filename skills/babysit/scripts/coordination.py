"""Fixer coordination for babysit: is the PR settled, and which failing checks
and review threads is another fixer (a person, Copilot, Codex, Claude, ...)
already working on? See references/coordination.md.

Usage: python3 -I coordination.py poll --pr N [-R [HOST/]OWNER/REPO] [--input FILE|-] [--take-over]
       python3 -I coordination.py claim --pr N --item ID [-R [HOST/]OWNER/REPO]
       python3 -I coordination.py markers --item ID

poll reads the PR with gh (`gh pr view --json headRefOid,commits,...`, the
review threads over GraphQL and the PR's reactions), or a bundle from --input
({"view": that pr view JSON, "threads": review thread nodes, "pr_reactions":
REST reactions, "viewer": login}; nothing is fetched then), updates the PR's
coordination state in <git-common-dir>/vstack/<pr>/coordination.json and
prints {settled, settled_reasons, items, claimed_count, actionable,
own_fix_rounds, handback}. Item ids are check:<name> and thread:<threadId>;
states are open, claimed, addressed and stale-claim.

Settled: no check queued or running on the head, the head is the one the last
poll saw (so the first poll is never settled), and no other actor's working
signal (a "working" comment with no later "done" or commit, a 👀 on the PR
newer than the head commit, an @copilot/@codex/@claude mention not yet
answered, a pending review). Not settled means actionable is empty.

Own work carries a marker: `<!-- vstack:babysit:<item> -->` in a comment, a
plain `vstack:babysit:<item>` line in a commit message (autonomous-merge's
review fixes use `vstack:autonomous-merge:<fingerprint>`, also own). A claimed
item becomes stale-claim after 2 completed CI cycles on newer heads or 3
polls with CI idle and nothing new; that hands back `stale_claim` unless
--take-over (pass it only when the user's own words in this run's request say
take over), which makes stale items actionable instead. A check fixed by one
side that fails again after the other side's commit, or that fails again after
foreign commits twice, hands back `conflicting_fixer`.

claim adds a 👀 reaction (content=eyes) to the thread's first comment, or to
the PR for a check, and records it as babysit's own; it posts nothing else.
markers prints the comment and commit markers for an item.

Never prints a token. Exits 0 ok, 5 handback, 2 usage (also -h, or a state
file that fails its checks), 4 internal error (after printing an `unknown`
handback).
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
import urllib.parse

OK, USAGE, INTERNAL, HANDBACK = 0, 2, 4, 5
SCHEMA_VERSION = 1
STATE_FILE = "coordination.json"
STALE_CYCLES = 2
STALE_POLLS = 3
MAX_TRANSITIONS = 20
MAX_BYTES = 4 * 1024 * 1024
MAX_PR = 2**31 - 1
PR_ARG = re.compile(r"[1-9][0-9]{0,9}")
REPO_ARG = re.compile(r"(?:[A-Za-z0-9.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ITEM = re.compile(r"check:(?:(?!-->)[^\x00-\x1f\x7f])+|thread:[A-Za-z0-9_=-]+")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

COMMIT_MARKER = re.compile(r"^vstack:(babysit|autonomous-merge):(\S.*?)\s*$", re.M)
COMMENT_MARKER = re.compile(r"<!-- vstack:(babysit|autonomous-merge):(.+?) -->")
WORKING = re.compile(r"(?i)👀|\b(on it|working on (it|this|that)|i'?m working|i'?ll (take|fix|handle|look into|address)"
                     r"|looking into|taking (a look|this|it)|wip|in progress)\b")
DONE = re.compile(r"(?i)\b(done|fixed|addressed|pushed|resolved|finished|completed)\b")
MENTION = re.compile(r"(?i)(?<![\w@/])@(copilot|codex|claude)\b")
AGENT_LOGIN = re.compile(r"(?i)^(copilot|codex|claude)|\[bot\]$")
PASS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
CLAIMED = ("claimed", "stale-claim")
TOKENS = [re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),
          re.compile(r"(?i)(bearer|token)\s+[A-Za-z0-9._~+/=-]{8,}")]

THREADS_QUERY = """
query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
  viewer{ login }
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100, after:$endCursor){
      pageInfo{ hasNextPage endCursor }
      nodes{ id isResolved path
        comments(first:100){ pageInfo{ hasNextPage }
          nodes{ id author{ __typename login } body createdAt
            reactions(first:100){ nodes{ content createdAt user{ login } } } } } } } } } }
"""
FIRST_COMMENT_QUERY = """
query($id:ID!){ node(id:$id){ ... on PullRequestReviewThread { comments(first:1){ nodes{ id databaseId } } } } }
"""
VIEW_FIELDS = "headRefOid,commits,statusCheckRollup,reviews,comments,url,author"


class Usage(Exception):
    pass


class Internal(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(text):
    for pat in TOKENS:
        text = pat.sub("[redacted]", text)
    return " ".join(text.split())[:300]


def after(when, since):
    """Is GitHub time `when` at or after `since`? Unknown times count as after."""
    return not when or not since or when >= since


def login(x):
    return ((x or {}).get("author") or (x or {}).get("user") or {}).get("login")


def own_marker(body):
    return bool(COMMENT_MARKER.search(body or ""))


# ---- gh -------------------------------------------------------------------

def gh_env():
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1",
            "GH_PAGER": "cat", "PAGER": "cat", "GH_SPINNER_DISABLED": "1", "LC_ALL": "C"}


def default_gh(*args, timeout=120):
    try:
        return subprocess.run(["gh", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, env=gh_env())
    except FileNotFoundError:
        raise Internal("gh is not on PATH")
    except subprocess.TimeoutExpired:
        raise Internal(f"gh timed out after {timeout}s")


def gh_json(r, what):
    if r.returncode != 0:
        raise Internal(f"{what}: gh exited {r.returncode}: {redact(r.stderr or '')}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Internal(f"{what}: gh printed something that isn't JSON")


def repo_of(view):
    u = urllib.parse.urlsplit(str(view.get("url") or ""))
    parts = [p for p in u.path.split("/") if p]
    if u.scheme != "https" or not u.hostname or len(parts) < 4 or parts[2] != "pull":
        raise Internal("gh gave no PR URL")
    return u.hostname.lower(), parts[0], parts[1]


def pr_view(gh, pr, repo_arg, fields):
    r = gh("pr", "view", str(pr), *(["-R", repo_arg] if repo_arg else []), "--json", fields)
    view = gh_json(r, "gh pr view")
    if not isinstance(view, dict):
        raise Internal("gh pr view: not an object")
    return view


def fetch_threads(gh, host, owner, repo, pr):
    """(thread nodes or None if they can't all be read, viewer login or None)."""
    r = gh("api", "graphql", "--hostname", host, "--paginate", "--slurp", "-f", f"owner={owner}",
           "-f", f"repo={repo}", "-F", f"pr={pr}", "-f", f"query={THREADS_QUERY}")
    if r.returncode != 0:
        return None, None
    try:
        threads, viewer, more = [], None, False
        for page in json.loads(r.stdout):
            viewer = viewer or ((page["data"].get("viewer") or {}).get("login"))
            rt = page["data"]["repository"]["pullRequest"]["reviewThreads"]
            threads += rt["nodes"]
            more = rt["pageInfo"]["hasNextPage"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None
    return (None if more else threads), viewer


def fetch_reactions(gh, host, owner, repo, pr):
    r = gh("api", "--hostname", host, "--paginate", "--slurp", f"repos/{owner}/{repo}/issues/{pr}/reactions?per_page=100")
    if r.returncode != 0:
        return None
    try:
        pages = json.loads(r.stdout)
    except ValueError:
        return None
    if not (isinstance(pages, list) and all(isinstance(p, list) for p in pages)):
        return None
    return [x for p in pages for x in p if isinstance(x, dict)]


def fetch(pr, repo_arg=None, gh=default_gh):
    view = pr_view(gh, pr, repo_arg, VIEW_FIELDS)
    host, owner, repo = repo_of(view)
    threads, viewer = fetch_threads(gh, host, owner, repo, pr)
    return {"view": view, "threads": threads, "pr_reactions": fetch_reactions(gh, host, owner, repo, pr),
            "viewer": viewer, "repo": [host, owner, repo]}


def commit_files(sha, cache, gh, repo):
    """Paths a commit changed: local git, else gh; None when unknown."""
    if sha in cache:
        return cache[sha]
    files = None
    env = {**os.environ, "LC_ALL": "C"}
    try:
        if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, env=env).returncode == 0:
            r = subprocess.run(["git", "diff-tree", "--no-commit-id", "-r", "-z", "--name-only", "-m", "--first-parent",
                                sha], capture_output=True, text=True, env=env)
            files = [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else None
    except OSError:
        files = None
    if files is None and gh and repo:
        host, owner, name = repo
        r = gh("api", "--hostname", host, "--method", "GET", f"repos/{owner}/{name}/commits/{sha}")
        try:
            data = json.loads(r.stdout) if r.returncode == 0 else None
            files = [f["filename"] for f in data["files"]] if isinstance(data, dict) else None
        except (ValueError, KeyError, TypeError):
            files = None
    if files is not None:
        cache[sha] = files
    return files


# ---- state ----------------------------------------------------------------

def common_dir():
    try:
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True,
                           env={**os.environ, "LC_ALL": "C"})
    except OSError:
        raise Usage("can't run git")
    if r.returncode == 0 and r.stdout.strip():
        return os.path.realpath(os.path.join(os.getcwd(), r.stdout.rstrip("\n")))
    raise Usage("not in a git repository" if "not a git repository" in r.stderr
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
            raise Usage(f"refusing: {shown} is a symlink or not a directory")
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


def new_state(pr):
    return {"schema_version": SCHEMA_VERSION, "pr": pr, "last_head": None, "polls": 0, "items": {}, "claims": {},
            "self_subjects": [], "working": {}, "files": {}}


def problems(s, pr):
    if not isinstance(s, dict):
        return ["not an object"]
    want = new_state(pr)
    errs = [f"{k}: missing or the wrong type" for k, v in want.items()
            if k != "last_head" and not isinstance(s.get(k), type(v))]
    if s.get("schema_version") != SCHEMA_VERSION or s.get("pr") != pr:
        errs.append("not this PR's state, or a schema_version this doesn't read")
    if not (s.get("last_head") is None or isinstance(s.get("last_head"), str)):
        errs.append("last_head: must be a SHA or null")
    if not errs and not all(isinstance(v, dict) for v in s["items"].values()):
        errs.append("items: must map ids to objects")
    return errs


def load(top, pr):
    shown = f"vstack/{pr}/{STATE_FILE}"
    try:
        d = pr_dir(top, pr, create=False)
    except FileNotFoundError:
        return new_state(pr)
    try:
        fd = os.open(STATE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
    except FileNotFoundError:
        return new_state(pr)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EMLINK):
            raise Usage(f"refusing: {shown} is a symlink")
        raise
    finally:
        os.close(d)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Usage(f"refusing: {shown} is not a regular file")
        data = f.read(MAX_BYTES + 1)
    try:
        s = json.loads(data.decode("utf-8")) if len(data) <= MAX_BYTES else None
    except (ValueError, RecursionError):
        s = None
    errs = problems(s, pr) if s is not None else ["not valid JSON, or too big"]
    if errs:
        raise Usage(f"{shown} fails its checks ({'; '.join(errs[:5])}); delete it to start over")
    return s


def save(top, s):
    data = (json.dumps(s, indent=2) + "\n").encode()
    d = pr_dir(top, s["pr"], create=True)
    tmp = f".{STATE_FILE}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_FILE, src_dir_fd=d, dst_dir_fd=d)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=d)
            except OSError:
                pass
            raise
    finally:
        os.close(d)


# ---- evaluation -----------------------------------------------------------

def parse_commits(raw):
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("oid"), str):
            continue
        msg = f"{c.get('messageHeadline') or ''}\n\n{c.get('messageBody') or ''}"
        marks = COMMIT_MARKER.findall(msg)
        out.append({"oid": c["oid"], "message": msg, "date": c.get("committedDate"),
                    "login": next((a.get("login") for a in c.get("authors") or [] if isinstance(a, dict)
                                   and a.get("login")), None),
                    "own": bool(marks), "babysit": any(skill == "babysit" for skill, _ in marks)})
    return out


def is_babysit_commit(message):
    """Does a commit message carry babysit's marker? (state.py counts only these as fix rounds.)"""
    return any(skill == "babysit" for skill, _ in COMMIT_MARKER.findall(message or ""))


def check_states(rollup):
    """{name: passing | failing | running} for the head's checks."""
    out = {}
    for x in rollup if isinstance(rollup, list) else []:
        if not isinstance(x, dict):
            continue
        name = x.get("name") or x.get("context") or "?"
        if x.get("__typename") == "StatusContext" or "context" in x:
            s = x.get("state")
            st = "passing" if s == "SUCCESS" else "running" if s in ("PENDING", "EXPECTED") else "failing"
        elif x.get("status") != "COMPLETED":
            st = "running"
        else:
            st = "passing" if x.get("conclusion") in PASS else "failing"
        rank = {"passing": 0, "failing": 1, "running": 2}
        if rank[st] >= rank.get(out.get(name), -1):
            out[name] = st
    return out


def working_signals(view, reactions, commits, viewer, self_subjects, head_date):
    """[(who, what, when)] for other actors who say they're working right now."""
    comments = [c for c in view.get("comments") or [] if isinstance(c, dict)]
    out = []
    for i, c in enumerate(comments):
        body, who, when = c.get("body") or "", login(c), c.get("createdAt")
        if own_marker(body):
            continue
        later = comments[i + 1:]
        foreign_commit_later = any(not k["own"] and k["date"] and when and k["date"] > when for k in commits)
        if WORKING.search(body):
            done = any(login(x) == who and DONE.search(x.get("body") or "") for x in later)
            if not done and not foreign_commit_later:
                out.append((who, "said they're working on the PR", when))
        for tool in {m.lower() for m in MENTION.findall(body)}:
            answered = any(tool in (login(x) or "").lower() for x in later)
            if not answered and not foreign_commit_later:
                out.append((who, f"asked @{tool} to work on the PR", when))
    for rx in reactions or []:
        if str(rx.get("content")).lower() != "eyes":
            continue
        if login(rx) and login(rx) == viewer and "pr" in self_subjects:
            continue
        if after(rx.get("created_at"), head_date):
            out.append((login(rx), "reacted 👀 on the PR after the last commit", rx.get("created_at")))
    for rv in view.get("reviews") or []:
        if isinstance(rv, dict) and rv.get("state") == "PENDING":
            out.append((login(rv), "has a pending review", None))
    return out


def resolve_claim(foreign, self_claim):
    """(state, claimant, own) from foreign claims [(who, when)] and babysit's own claim."""
    if foreign and not (self_claim and all(w and w > self_claim["at"] for _, w in foreign)):
        return "claimed", foreign[0][0] or "someone", False
    if self_claim:
        return "claimed", "babysit", True
    return "open", None, False


def thread_raw(node, ctx, st):
    """(state, claimant, own) for a review thread right now."""
    iid = f"thread:{node.get('id')}"
    comments = [c for c in ((node.get("comments") or {}).get("nodes") or []) if isinstance(c, dict)]
    own = any(own_marker(c.get("body")) for c in comments)
    if node.get("isResolved"):
        return "addressed", None, own
    if not comments:
        return "open", None, False
    reviewer, last = login(comments[0]), comments[-1]
    if own_marker(last.get("body")):
        return "addressed", "babysit", True
    who = login(last)
    reactors = {login(rx) for c in comments for rx in (c.get("reactions") or {}).get("nodes") or []
                if str(rx.get("content")).upper() == "EYES"}
    answerer = who in (ctx["pr_author"], ctx["viewer"]) or who in reactors \
        or (last.get("author") or {}).get("__typename") == "Bot" or bool(who and AGENT_LOGIN.search(who))
    if who and who != reviewer and answerer:
        if WORKING.search(last.get("body") or ""):
            return "claimed", who, False
        return "addressed", who, False
    since, foreign = last.get("createdAt"), []
    for c in comments:
        for rx in (c.get("reactions") or {}).get("nodes") or []:
            if str(rx.get("content")).upper() != "EYES" or not after(rx.get("createdAt"), since):
                continue
            if not (login(rx) and login(rx) == ctx["viewer"] and c.get("id") in ctx["self_subjects"]):
                foreign.append((login(rx), rx.get("createdAt")))
    path, tid = node.get("path"), str(node.get("id"))
    for k in ctx["commits"]:
        if k["own"] or not after(k["date"], since):
            continue
        if tid in k["message"] or (path and path in (ctx["files"](k["oid"]) or ())):
            foreign.append((k["login"], k["date"]))
    return resolve_claim(foreign, st["claims"].get(iid))


def check_raw(name, status, prev, ctx, st):
    iid = f"check:{name}"
    if status == "passing" or (status == "missing" and not ctx["ci_running"]):
        return "addressed", None, False
    if status in ("running", "missing"):
        return (prev["state"], prev.get("claimant"), prev.get("own", False)) if prev else ("open", None, False)
    since_head = prev.get("since_head") if prev and prev.get("state") != "addressed" else ctx["head"]
    since_at = prev.get("since_at") if prev and prev.get("state") != "addressed" else ctx["now"]
    idx = ctx["index"].get(since_head)
    later = ctx["commits"][idx + 1:] if idx is not None else []
    low, foreign = name.lower(), []
    for k in later:
        if not k["own"] and (low in k["message"].lower() or iid in k["message"]):
            foreign.append((k["login"], k["date"]))
    for c in ctx["view"].get("comments") or []:
        body = (c or {}).get("body") or ""
        if not own_marker(body) and WORKING.search(body) and low in body.lower() and after(c.get("createdAt"), since_at):
            foreign.append((login(c), c.get("createdAt")))
    return resolve_claim(foreign, st["claims"].get(iid))


def party(since, ctx):
    """own, foreign or None: who pushed the commits after head `since`."""
    if since in (None, ctx["head"]):
        return None
    i = ctx["index"].get(since)
    new = ctx["commits"][i + 1:] if i is not None else ctx["commits"]
    return None if not new else "foreign" if any(not k["own"] for k in new) else "own"


def track(iid, kind, raw, sig, ctx, st):
    """Update item iid's history from its raw state; returns its output entry,
    and appends to ctx["conflicts"] on oscillation."""
    state, claimant, own = raw
    it = st["items"].setdefault(iid, {})
    for k, v in {"state": None, "since_head": ctx["head"], "since_at": ctx["now"], "claimed_since_head": None,
                 "cycle_heads": [], "ci_cycles_completed_since_claim": 0, "polls_without_progress": 0, "sig": None,
                 "transitions": [], "addressed_by": None, "foreign_regressions": 0, "state_head": ctx["head"]}.items():
        it.setdefault(k, v)
    prev = it["state"]
    by = party(it.get("state_head"), ctx)
    if state == "claimed" and not own:
        if prev not in CLAIMED or it.get("own") or it["claimed_since_head"] is None:
            it.update(claimed_since_head=ctx["head"], cycle_heads=[], polls_without_progress=0, sig=None)
        if not ctx["ci_running"] and ctx["head"] != it["claimed_since_head"] and ctx["head"] not in it["cycle_heads"]:
            it["cycle_heads"].append(ctx["head"])
        if it["sig"] == sig and not ctx["ci_running"] and ctx["prev_head"] == ctx["head"]:
            it["polls_without_progress"] += 1
        elif it["sig"] != sig:
            it["polls_without_progress"] = 0
        it["sig"] = sig
        it["ci_cycles_completed_since_claim"] = len(it["cycle_heads"])
        if it["ci_cycles_completed_since_claim"] >= STALE_CYCLES or it["polls_without_progress"] >= STALE_POLLS:
            state = "stale-claim"
    else:
        it.update(claimed_since_head=None, cycle_heads=[], ci_cycles_completed_since_claim=0,
                  polls_without_progress=0, sig=None)
    if prev is not None and prev != state:
        it["transitions"] = (it["transitions"] + [{"from": prev, "to": state, "head": ctx["head"], "by": by,
                                                   "at": ctx["now"]}])[-MAX_TRANSITIONS:]
    if state == "addressed" and prev != "addressed":
        it["addressed_by"] = by or ("own" if own else "foreign")
    if kind == "check" and state == "addressed":
        own = it["addressed_by"] == "own"
    if prev == "addressed" and state != "addressed":
        it.update(since_head=ctx["head"], since_at=ctx["now"])
        if kind == "check" and by:
            if by == "foreign":
                it["foreign_regressions"] += 1
            if it["addressed_by"] not in (None, by) or it["foreign_regressions"] >= 2:
                ctx["conflicts"].append(iid)
    if state == "addressed":
        st["claims"].pop(iid, None)
    if prev != state:
        it["state_head"] = ctx["head"]
    it.update(state=state, claimant=claimant, own=own)
    return {"id": iid, "kind": kind, "state": state, "claimant": claimant, "own": own}


def evaluate(b, st, take_over=False, gh=None):
    view = b["view"]
    head = view.get("headRefOid")
    if not isinstance(head, str) or not head:
        raise Internal("gh gave no head SHA")
    commits = parse_commits(view.get("commits"))
    index = {k["oid"]: i for i, k in enumerate(commits)}
    prev_head = st["last_head"]
    checks = check_states(view.get("statusCheckRollup"))
    ci_running = any(s == "running" for s in checks.values())
    viewer, threads, reactions = b.get("viewer"), b.get("threads"), b.get("pr_reactions")
    files = st["files"]
    ctx = {"view": view, "head": head, "prev_head": prev_head, "commits": commits, "index": index,
           "ci_running": ci_running, "viewer": viewer, "pr_author": login(view), "now": now(),
           "self_subjects": set(st["self_subjects"]), "conflicts": [],
           "files": lambda sha: commit_files(sha, files, gh, b.get("repo"))}

    reasons = []
    if prev_head is None:
        reasons.append("first_look: head recorded, poll again")
    elif prev_head != head:
        reasons.append("new_head: a commit arrived since the last poll")
    if ci_running:
        reasons.append("checks_running: " + ", ".join(sorted(n for n, s in checks.items() if s == "running"))[:300])
    if threads is None:
        reasons.append("threads_unreadable: couldn't read every review thread")
    if reactions is None:
        reasons.append("reactions_unreadable: couldn't read the PR's reactions")

    head_date = commits[-1]["date"] if commits and commits[-1]["oid"] == head else None
    signals = working_signals(view, reactions, commits, viewer, ctx["self_subjects"], head_date)
    stale_working = None
    w = st["working"]
    if signals:
        sig = json.dumps([head, sorted(map(str, signals)), len(view.get("comments") or [])])
        quiet = w.get("sig") == sig and not ci_running and prev_head == head
        w.update(sig=sig, polls=w.get("polls", 0) + 1 if quiet else 0 if w.get("sig") != sig else w.get("polls", 0))
        who, what, _ = signals[0]
        if w["polls"] >= STALE_POLLS:
            stale_working = f"{who or 'someone'} {what}, with nothing new for {w['polls']} polls"
        if not (stale_working and take_over):
            reasons.append(f"working: {who or 'someone'} {what}")
    else:
        st["working"] = {}

    items = []
    for name in sorted(set(n for n, s in checks.items() if s == "failing")
                       | {i[6:] for i in st["items"] if i.startswith("check:")}):
        prev = st["items"].get(f"check:{name}")
        raw = check_raw(name, checks.get(name, "missing"), prev, ctx, st)
        items.append(track(f"check:{name}", "check", raw, json.dumps([head, checks.get(name)]), ctx, st))
    seen = set()
    for node in threads or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        iid = f"thread:{node['id']}"
        seen.add(iid)
        nodes = (node.get("comments") or {}).get("nodes") or []
        sig = json.dumps([head, bool(node.get("isResolved")), len(nodes),
                          sum(len((c.get("reactions") or {}).get("nodes") or []) for c in nodes if isinstance(c, dict))])
        items.append(track(iid, "thread", thread_raw(node, ctx, st), sig, ctx, st))
    if threads is not None:
        for iid in [i for i in st["items"] if i.startswith("thread:") and i not in seen]:
            del st["items"][iid]
    for iid in [i for i, it in st["items"].items()
                if i.startswith("check:") and i[6:] not in checks and it.get("state") == "addressed"]:
        del st["items"][iid]

    settled = not reasons
    stale = [i for i in items if i["state"] == "stale-claim"]
    handback = None
    if ctx["conflicts"]:
        iid = ctx["conflicts"][0]
        handback = {"reason": "conflicting_fixer",
                    "detail": f"{iid} was fixed by one fixer and broke again after the other's commit "
                              f"(babysit and another fixer are undoing each other)",
                    "action": f"Pick one fixer for {iid} and pause the other, then rerun."}
    elif (stale or stale_working) and not take_over:
        what = stale[0]["id"] if stale else "the PR"
        detail = (f"{stale[0]['claimant']} claimed {what} and it's still not addressed after "
                  f"{st['items'][what]['ci_cycles_completed_since_claim']} CI cycle(s) and "
                  f"{st['items'][what]['polls_without_progress']} quiet poll(s)") if stale else stale_working
        handback = {"reason": "stale_claim", "detail": detail,
                    "action": f"the other fixer seems stuck on {what}; fix it yourself or rerun with 'take over'"}
    actionable = [i["id"] for i in items if settled and (
        i["state"] == "open" or (i["state"] == "claimed" and i["own"]) or (i["state"] == "stale-claim" and take_over))]

    st["last_head"] = head
    st["polls"] += 1
    st["files"] = {k: v for k, v in files.items() if k in index}
    return {"pr": st["pr"], "head": head, "settled": settled, "settled_reasons": reasons, "items": items,
            "claimed_count": sum(1 for i in items if i["state"] in CLAIMED), "actionable": actionable,
            "own_fix_rounds": sum(1 for k in commits if k["babysit"]), "handback": handback}


def poll(pr, bundle, take_over=False, gh=None, top=None):
    """Evaluate a fetched bundle against the PR's saved state and save it. Used by
    autonomous-merge's gate too."""
    top = top or common_dir()
    st = load(top, pr)
    out = evaluate(bundle, st, take_over, gh)
    save(top, st)
    return out


# ---- commands -------------------------------------------------------------

def markers(item):
    return {"item": item, "comment": f"<!-- vstack:babysit:{item} -->", "commit": f"vstack:babysit:{item}"}


def claim(pr, item, repo_arg=None, gh=default_gh):
    top = common_dir()
    st = load(top, pr)
    host, owner, repo = repo_of(pr_view(gh, pr, repo_arg, "url"))
    if item.startswith("thread:"):
        r = gh("api", "graphql", "--hostname", host, "-f", f"id={item[7:]}", "-f", f"query={FIRST_COMMENT_QUERY}")
        try:
            first = gh_json(r, "reading the thread")["data"]["node"]["comments"]["nodes"][0]
            db_id, subject = int(first["databaseId"]), str(first["id"])
        except (KeyError, TypeError, IndexError, ValueError):
            raise Internal(f"couldn't find the first comment of {item}")
        endpoint = f"repos/{owner}/{repo}/pulls/comments/{db_id}/reactions"
    else:
        endpoint, subject = f"repos/{owner}/{repo}/issues/{pr}/reactions", "pr"
    r = gh("api", "--hostname", host, "-X", "POST", endpoint, "-f", "content=eyes")
    if r.returncode != 0:
        raise Internal(f"adding the reaction: gh exited {r.returncode}: {redact(r.stderr or '')}")
    st["claims"][item] = {"at": now()}
    if subject not in st["self_subjects"]:
        st["self_subjects"].append(subject)
    save(top, st)
    return {"claimed": item, "reaction": "eyes", "endpoint": endpoint, **{k: v for k, v in markers(item).items()
                                                                           if k != "item"}}


def read_bundle(src):
    try:
        if src == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with open(src, "rb") as f:
                data = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise Usage(f"can't read --input: {e.strerror}")
    try:
        b = json.loads(data.decode("utf-8")) if len(data) <= MAX_BYTES else None
    except (ValueError, RecursionError):
        b = None
    if not (isinstance(b, dict) and isinstance(b.get("view"), dict)
            and all(b.get(k) is None or isinstance(b.get(k), list) for k in ("threads", "pr_reactions"))
            and (b.get("viewer") is None or isinstance(b.get("viewer"), str))):
        raise Usage('--input must be JSON {"view": {...}, "threads": [...], "pr_reactions": [...], "viewer": "..."}')
    b.setdefault("threads", None)
    b.setdefault("pr_reactions", None)
    b.pop("repo", None)
    return b


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="coordination.py", add_help=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll", add_help=False)
    p.add_argument("--pr", required=True)
    p.add_argument("-R", "--repo")
    p.add_argument("--input")
    p.add_argument("--take-over", action="store_true")
    p = sub.add_parser("claim", add_help=False)
    p.add_argument("--pr", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("-R", "--repo")
    p = sub.add_parser("markers", add_help=False)
    p.add_argument("--item", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        raise Usage("bad arguments; see the usage at the top of coordination.py")
    if getattr(a, "pr", None) is not None:
        if not PR_ARG.fullmatch(a.pr) or int(a.pr) > MAX_PR:
            raise Usage("--pr must be a positive integer")
        a.pr = int(a.pr)
    if getattr(a, "item", None) is not None and not ITEM.fullmatch(a.item):
        raise Usage("--item must be check:<name> or thread:<threadId>")
    if getattr(a, "repo", None) is not None and not REPO_ARG.fullmatch(a.repo):
        raise Usage("-R must be OWNER/REPO or HOST/OWNER/REPO")
    return a


def main(argv):
    a = parse_args(argv)
    if a.cmd == "markers":
        return markers(a.item), OK
    if a.cmd == "claim":
        return claim(a.pr, a.item, a.repo), OK
    top = common_dir()
    bundle = read_bundle(a.input) if a.input else fetch(a.pr, a.repo)
    out = poll(a.pr, bundle, a.take_over, None if a.input else default_gh, top)
    return out, HANDBACK if out["handback"] else OK


if __name__ == "__main__":
    try:
        result, code = main(sys.argv[1:])
        print(json.dumps(result))
    except Usage as e:
        print(f"coordination: {e}", file=sys.stderr)
        code = USAGE
    except Exception as e:
        msg = str(e) if isinstance(e, Internal) else f"internal error: {type(e).__name__}"
        print(json.dumps({"settled": False, "settled_reasons": [], "items": [], "claimed_count": 0, "actionable": [],
                          "handback": {"reason": "unknown", "detail": f"coordination: {msg}",
                                       "action": "Rerun; if it happens again, check gh and report the stderr line."}}))
        print(f"coordination: {msg}", file=sys.stderr)
        code = INTERNAL
    sys.exit(code)
