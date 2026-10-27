"""The merge gate: fresh live checks of a PR right before merging, and the merge
itself only with --merge.

Usage: python3 -I gate.py <pr-number-or-url> [--merge]

Run from the PR's repo (the review result is read from its git common dir).
Every decision comes from the forge's live state, re-read on each run. The one
file it reads is review's result, through review's read-result.py, and only to
require a review whose head and base match the live PR and whose merge_ok is
true; the file can't make a blocked PR pass.

GitHub blocks: closed or merged, draft, conflicts, changes requested, review
required, a required check not passing (all checks, if the required ones can't
be read), any unresolved review thread (a human's stays until a human resolves
it), mergeStateStatus BLOCKED or BEHIND, unknown mergeability, and no clean
review on this head. With --merge it runs
`gh pr merge <n> -R <host/o/r> --auto --<method> --match-head-commit <head>`,
where method is the only one the repo allows, else squash if allowed (also when
the repo's settings can't be read), else merge. Never --admin, never a push.

Bitbucket Cloud blocks: not OPEN, draft, a changes-requested participant, no
approval, a build status that isn't SUCCESSFUL, a merge conflict in the
diffstat, an unresolved task or inline comment, and no clean review on this
head. With --merge it re-reads the head and POSTs /pullrequests/{id}/merge with
the destination branch's default_merge_strategy (else squash).

Prints one JSON object: {"outcome": "ready" | "merged" | "auto_merge_enabled" |
"merge_pending", ...} (exit 0) or a handback (exit 5) naming the first block in
the order above, with the rest in its detail. Exit 2 usage, 4 internal error.
"""
import argparse
import importlib.util
import json
import os
import re
import urllib.parse

_spec = importlib.util.spec_from_file_location(
    "vstack_am_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c)

SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
PASS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BUCKET = {"pass": "passing", "skipping": "passing", "pending": "pending", "fail": "failing", "cancel": "failing"}
METHODS = (("merge", "allow_merge_commit"), ("squash", "allow_squash_merge"), ("rebase", "allow_rebase_merge"))
BB_CONFLICT = {"merge conflict", "local deleted", "remote deleted"}
BB_STRATEGIES = {"merge_commit", "squash", "fast_forward", "squash_fast_forward", "rebase_fast_forward",
                 "rebase_merge"}

THREADS_QUERY = """
query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100, after:$endCursor){
      pageInfo{ hasNextPage endCursor }
      nodes{ id isResolved
        comments(first:100){ pageInfo{ hasNextPage } nodes{ author{ __typename login } url } } } } } } }
"""


class Blocks:
    def __init__(self):
        self.items = []

    def add(self, reason, detail, action):
        self.items.append((reason, detail, action))

    def raise_first(self):
        if not self.items:
            return
        reason, detail, action = self.items[0]
        rest = [f"{r}: {d}" for r, d, _ in self.items[1:]]
        raise c.Handback(reason, detail + (f"; also {'; '.join(rest)}" if rest else ""), action)


def review_block(blocks, pr, head, base):
    why = c.review_ok(pr, head, base)
    if why:
        blocks.add("needs_review", why,
                   "Run the review skill on the current head until it decides Ship (it writes review.json), "
                   "then rerun the gate.")


def check_state(item):
    if item.get("__typename") == "StatusContext" or "context" in item:
        s = item.get("state")
        return "passing" if s == "SUCCESS" else "pending" if s in ("PENDING", "EXPECTED") else "failing"
    if item.get("status") != "COMPLETED":
        return "pending"
    return "passing" if item.get("conclusion") in PASS else "failing"


def github_checks(t, view):
    r = c.gh("pr", "checks", str(t["pr"]), "-R", c.gh_repo(t), "--required", "--json", "name,bucket")
    try:
        required = json.loads(r.stdout)
    except ValueError:
        required = None
    if isinstance(required, list) and required:
        return [(x.get("name") or "?", BUCKET.get(x.get("bucket"), "failing")) for x in required], True
    rollup = view.get("statusCheckRollup") or []
    return [(x.get("name") or x.get("context") or "?", check_state(x)) for x in rollup], False


def github_threads(t):
    r = c.gh("api", "graphql", "--hostname", t["hostname"], "--paginate", "--slurp",
             "-f", f"owner={t['owner']}", "-f", f"repo={t['repo']}", "-F", f"pr={t['pr']}",
             "-f", f"query={THREADS_QUERY}")
    if r.returncode != 0:
        return None
    try:
        pages = json.loads(r.stdout)
        threads, more = [], False
        for page in pages:
            rt = page["data"]["repository"]["pullRequest"]["reviewThreads"]
            threads += rt["nodes"]
            more = rt["pageInfo"]["hasNextPage"]
    except (ValueError, KeyError, TypeError):
        return None
    return None if more else threads


def thread_block(blocks, threads):
    if threads is None:
        blocks.add("unresolved_threads", "couldn't read every review thread, so treating them as unresolved",
                   "Check the PR's conversations by hand, then rerun.")
        return
    human, bot = [], []
    for th in threads:
        if th.get("isResolved"):
            continue
        comments = th.get("comments") or {}
        nodes = comments.get("nodes") or []
        is_bot = bool(nodes) and not (comments.get("pageInfo") or {}).get("hasNextPage") and all(
            (n.get("author") or {}).get("__typename") == "Bot" for n in nodes)
        url = nodes[0].get("url") if nodes else None
        (bot if is_bot else human).append(url or th.get("id") or "?")
    if human:
        blocks.add("unresolved_threads", f"{len(human)} unresolved human review thread(s): {', '.join(human[:10])}",
                   "A human reviewer must resolve these threads (autonomous-merge only replies to them), "
                   "then rerun the gate.")
    elif bot:
        blocks.add("unresolved_threads", f"{len(bot)} unresolved bot review thread(s): {', '.join(bot[:10])}",
                   "Run babysit to answer and resolve the bot threads, or resolve them yourself, then rerun.")


def merge_method(repo):
    allowed = [m for m, k in METHODS if repo.get(k) is True]
    if len(allowed) == 1:
        return allowed[0]
    if not allowed or "squash" in allowed:
        return "squash"
    return allowed[0]


def github_merge_error(r, t):
    err = c.one_line(r.stderr + r.stdout, 600) or f"gh exited {r.returncode}"
    low = err.lower()
    if any(s in low for s in ("not permitted", "permission", "resource not accessible", "http 403",
                              "must have write")):
        return c.Handback("insufficient_permission", f"gh pr merge failed: {err}", c.refresh_action(t))
    if any(s in low for s in ("protected branch", "branch protection", "required status", "review is required",
                              "approving review", "merge queue", "rule", "is not mergeable")):
        return c.Handback("branch_protection", f"gh pr merge failed: {err}",
                          "Check the base branch's protection rules and what they still need; "
                          "don't bypass them. Then rerun.")
    return c.Handback("merge_failed", f"gh pr merge failed: {err}", "Read gh's message above, fix the cause, rerun.")


def github_gate(t, do_merge):
    repo, _ = c.github_access(t)
    r = c.gh("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json",
             "state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefOid,statusCheckRollup")
    if r.returncode != 0:
        raise c.gh_api_error(r, t, f"reading PR {t['pr']}")
    view = c.gh_json(r, "gh pr view")
    head, base = view.get("headRefOid"), view.get("baseRefOid")
    if view.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(view.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    if not (isinstance(head, str) and SHA.fullmatch(head) and isinstance(base, str) and SHA.fullmatch(base)):
        raise c.Handback("unknown", "gh gave no full head or base SHA", "Rerun the gate.")
    blocks = Blocks()
    if view.get("isDraft"):
        blocks.add("draft", f"PR {t['pr']} is a draft",
                   f"Mark it ready for review yourself (`gh pr ready {t['pr']}`) when it is, then rerun.")
    if view.get("mergeable") == "CONFLICTING" or view.get("mergeStateStatus") == "DIRTY":
        blocks.add("conflicts", "the PR conflicts with its base",
                   "Resolve the conflicts with the base branch, then rerun.")
    decision = view.get("reviewDecision")
    if decision == "CHANGES_REQUESTED":
        blocks.add("changes_requested", "a reviewer requested changes",
                   "Get that reviewer to re-review or dismiss their review, then rerun.")
    elif decision == "REVIEW_REQUIRED":
        blocks.add("not_approved", "an approving review is required and missing",
                   "Get an approving review on the current head, then rerun.")
    checks, from_protection = github_checks(t, view)
    bad = [f"{n} ({s})" for n, s in checks if s != "passing"]
    if bad:
        scope = "required check(s)" if from_protection else "check(s) (required ones unreadable, all treated as required)"
        blocks.add("checks_failing", f"{len(bad)} {scope} not passing: {', '.join(bad[:10])}",
                   "Fix or rerun the failing checks (babysit does this) and wait for pending ones, then rerun.")
    thread_block(blocks, github_threads(t))
    status = view.get("mergeStateStatus")
    if status in ("BLOCKED", "BEHIND"):
        why = "behind its base, and protection requires it up to date" if status == "BEHIND" else "blocked"
        blocks.add("branch_protection", f"branch protection: the PR is {why} (mergeStateStatus {status})",
                   "Check what the base branch's protection rules still need (approvals, checks, an up-to-date "
                   "branch via babysit); never bypass them. Then rerun.")
    if view.get("mergeable") == "UNKNOWN" or status == "UNKNOWN":
        blocks.add("unknown", "GitHub hasn't finished computing mergeability", "Rerun the gate in a minute.")
    review_block(blocks, t["pr"], head, base)
    blocks.raise_first()
    out = {"host": "github", "pr": t["pr"], "head_sha": head, "base_sha": base}
    if not do_merge:
        return {"outcome": "ready", **out}
    method = merge_method(repo)
    r = c.gh("pr", "merge", str(t["pr"]), "-R", c.gh_repo(t), "--auto", f"--{method}",
             "--match-head-commit", head)
    if r.returncode != 0:
        raise github_merge_error(r, t)
    r = c.gh("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json", "state,autoMergeRequest")
    after = c.gh_json(r, "gh pr view") if r.returncode == 0 else {}
    outcome = ("merged" if after.get("state") == "MERGED" else
               "auto_merge_enabled" if after.get("autoMergeRequest") else "merge_requested")
    return {"outcome": outcome, "method": method, **out}


def full_sha(t, commit, what):
    short = (commit or {}).get("hash")
    if not isinstance(short, str) or not re.fullmatch(r"[0-9a-f]{7,64}", short):
        raise c.Handback("unknown", f"Bitbucket gave no {what} commit", "Rerun the gate.")
    if SHA.fullmatch(short):
        return short
    full = c.bb_get(f"{c.bb_base(t)}/commit/{short}", f"reading the {what} commit").get("hash")
    if not isinstance(full, str) or not SHA.fullmatch(full):
        raise c.Handback("unknown", f"Bitbucket gave no full {what} SHA", "Rerun the gate.")
    return full


def bb_strategy(t, branch):
    try:
        b = c.bb_get(f"{c.bb_base(t)}/refs/branches/{urllib.parse.quote(branch, safe='')}", "reading the base branch")
    except c.Handback:
        return "squash"
    default = b.get("default_merge_strategy")
    allowed = [s for s in b.get("merge_strategies") or [] if s in BB_STRATEGIES]
    if default in BB_STRATEGIES and (not allowed or default in allowed):
        return default
    if not allowed or "squash" in allowed:
        return "squash"
    return allowed[0]


def bitbucket_gate(t, do_merge):
    _, pr = c.bitbucket_access(t)
    if pr.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(pr.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    src, dst = pr.get("source") or {}, pr.get("destination") or {}
    head, base = full_sha(t, src.get("commit"), "head"), full_sha(t, dst.get("commit"), "base")
    prefix = f"{c.bb_base(t)}/pullrequests/{t['pr']}"
    blocks = Blocks()
    if pr.get("draft") is True:
        blocks.add("draft", f"PR {t['pr']} is a draft", "Mark it ready for review yourself when it is, then rerun.")
    conflicted = [d for d in c.bb_pages(f"{prefix}/diffstat", "reading the diffstat") if d.get("status") in BB_CONFLICT]
    if conflicted:
        blocks.add("conflicts", f"{len(conflicted)} file(s) conflict with the base",
                   "Resolve the conflicts with the base branch, then rerun.")
    author = ((pr.get("author") or {}).get("uuid"))
    people = [p for p in pr.get("participants") or [] if (p.get("user") or {}).get("uuid") != author or not author]
    if any(p.get("state") == "changes_requested" for p in people):
        blocks.add("changes_requested", "a participant requested changes",
                   "Get that reviewer to approve or withdraw the request, then rerun.")
    elif not any(p.get("approved") is True for p in people):
        blocks.add("not_approved", "no participant has approved", "Get an approval on the current head, then rerun.")
    statuses = c.bb_pages(f"{prefix}/statuses", "reading build statuses")
    bad = [f"{s.get('name') or s.get('key') or '?'} ({str(s.get('state')).lower()})"
           for s in statuses if s.get("state") != "SUCCESSFUL"]
    if bad:
        blocks.add("checks_failing", f"{len(bad)} build(s) not successful: {', '.join(bad[:10])}",
                   "Fix or rerun the failing builds and wait for running ones, then rerun.")
    tasks = [x for x in c.bb_pages(f"{prefix}/tasks", "reading tasks") if x.get("state") == "UNRESOLVED"]
    comments = [x for x in c.bb_pages(f"{prefix}/comments", "reading comments")
                if x.get("inline") and not x.get("parent") and not x.get("deleted") and not x.get("resolution")]
    if tasks or comments:
        blocks.add("unresolved_threads", f"{len(tasks)} open task(s) and {len(comments)} unresolved inline comment(s)",
                   "A human must resolve the open tasks and comments (autonomous-merge only replies), then rerun.")
    review_block(blocks, t["pr"], head, base)
    blocks.raise_first()
    out = {"host": "bitbucket", "pr": t["pr"], "head_sha": head, "base_sha": base}
    if not do_merge:
        return {"outcome": "ready", **out}
    strategy = bb_strategy(t, (dst.get("branch") or {}).get("name") or "")
    now = c.bb_get(prefix, f"re-reading PR {t['pr']}")
    if full_sha(t, (now.get("source") or {}).get("commit"), "head") != head:
        raise c.Handback("merge_failed", "the head moved after the gate's checks", "Rerun the gate on the new head.")
    status, data = c.bb_request(f"{prefix}/merge", method="POST", body={"merge_strategy": strategy})
    if status == 200:
        return {"outcome": "merged", "method": strategy, **out}
    if status == 202:
        return {"outcome": "merge_pending", "method": strategy, **out}
    msg = c.bb_error(data) or f"HTTP {status}"
    if status == 401:
        raise c.Handback("auth_expired", f"merge: Bitbucket rejected the credentials ({msg})",
                         "Create a new token or app password, set it in your environment yourself, then rerun.")
    if status == 403:
        raise c.Handback("insufficient_permission", f"merge refused: {msg}", c.bb_action())
    if "check" in msg.lower() or "restriction" in msg.lower():
        raise c.Handback("branch_protection", f"merge refused by merge checks: {msg}",
                         "See what the branch's merge checks still need; don't bypass them. Then rerun.")
    raise c.Handback("merge_failed", f"merge failed: HTTP {status} {msg}", "Read Bitbucket's message, fix it, rerun.")


def main():
    ap = argparse.ArgumentParser(prog="gate.py")
    ap.add_argument("pr")
    ap.add_argument("--merge", action="store_true")
    try:
        a = ap.parse_args()
    except SystemExit as e:
        return c.USAGE if e.code else c.OK
    t = c.parse_target(a.pr)
    out = github_gate(t, a.merge) if t["host"] == "github" else bitbucket_gate(t, a.merge)
    c.emit(out)
    return c.OK


if __name__ == "__main__":
    c.main_wrapper("gate", main)
