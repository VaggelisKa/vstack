"""The merge gate: fresh live checks of a PR right before merging, and the merge
itself only with --merge, and only on GitHub.

Usage: python3 -I gate.py <pr-number-or-url> [--merge]

Run from the PR's repo (the review result is read from its git common dir).
Every decision comes from the forge's live state, re-read on each run. The one
file it reads is review's result, through review's read-result.py, and only to
require a review whose head and base match the live PR and whose merge_ok is
true; the file can't make a blocked PR pass.

GitHub blocks: closed or merged, draft, conflicts, changes requested (by
reviewDecision, or by any human reviewer whose latest approving, dismissed or
changes-requested review is CHANGES_REQUESTED), a missing approval, a required
check not passing (all checks, if the required ones can't be read), any
unresolved review thread (a human's stays until a human resolves it),
mergeStateStatus BLOCKED or BEHIND, unknown mergeability (re-polled first, after
each delay in VSTACK_POLL_DELAYS, comma-separated seconds, default 2,4,8,16),
and no clean review on this head. An approval is needed only when the repo
requires one (reviewDecision REVIEW_REQUIRED, or a ruleset's
required_approving_review_count) or reviewDecision is APPROVED; then it must be
an APPROVED review by someone other than the author on the current head (an
approval of an older commit is stale and doesn't count). With no review
requirement, branch protection decides.

With --merge, and only once every block above is clear, it runs
`gh pr merge <n> -R <host/o/r> --auto --<method> --match-head-commit <head>`,
where method is the only one the repo allows, else squash if allowed (also when
the repo's settings can't be read), else merge. GitHub fires an auto-merge
later without checking again, so the gate then polls (after each delay in
VSTACK_MERGE_POLL_DELAYS, default about 5 minutes in total) until the PR is
merged, re-running every check each time; on a head or base change, any new
block, an unreadable PR or the time running out it runs
`gh pr merge <n> -R <host/o/r> --disable-auto` and hands back. Never --admin,
never a push, never a review dismissal.

Bitbucket Cloud blocks: not OPEN, draft, a changes-requested participant, no
approval, no build status or one that isn't SUCCESSFUL, a merge conflict in the
diffstat, an unresolved task or inline comment, and no clean review on this
head. It never merges, with or without --merge: a PR that passes is handed back
as merge_unsupported_host, for the user to merge (Bitbucket's merge API can't
be pinned to a head commit, there's no babysit gate, and merge permission only
shows at merge time). It only reads.

Prints one JSON object: {"outcome": "ready" | "merged", ...} (exit 0) or a
handback (exit 5) naming the first block in the order above, with the rest in
its detail. Exit 2 usage (also -h), 4 internal error.
"""
import argparse
import importlib.util
import json
import os
import re
import time
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
POLL_DELAYS = (2, 4, 8, 16)
MERGE_POLL_DELAYS = (5, 10, 15) + (30,) * 9
VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
VIEW_FIELDS = ("state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefOid,baseRefName,author,"
               "statusCheckRollup")

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

    def handback(self):
        reason, detail, action = self.items[0]
        rest = [f"{r}: {d}" for r, d, _ in self.items[1:]]
        return c.Handback(reason, detail + (f"; also {'; '.join(rest)}" if rest else ""), action)

    def raise_first(self):
        if self.items:
            raise self.handback()


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


def env_delays(name, default, most):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        delays = tuple(float(x) for x in raw.split(",") if x.strip())
    except ValueError:
        delays = None
    if delays is None or not all(0 <= d <= most for d in delays):
        raise c.Usage(f"{name} must be comma-separated seconds, each from 0 to {most}")
    return delays


def mergeability_unknown(view):
    return view.get("mergeable") == "UNKNOWN" or view.get("mergeStateStatus") == "UNKNOWN"


def github_view(t, delays=None, sleep=time.sleep):
    """gh pr view, re-read after each delay while an open PR's mergeability is UNKNOWN."""
    delays = env_delays("VSTACK_POLL_DELAYS", POLL_DELAYS, 60) if delays is None else delays
    for i in range(len(delays) + 1):
        r = c.gh("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json", VIEW_FIELDS)
        if r.returncode != 0:
            raise c.gh_api_error(r, t, f"reading PR {t['pr']}")
        view = c.gh_json(r, "gh pr view")
        if view.get("state") != "OPEN" or not mergeability_unknown(view) or i == len(delays):
            return view
        sleep(delays[i])


def github_reviews(t):
    """Every review on the PR, oldest first, or None if they can't all be read."""
    r = c.gh("api", "--hostname", t["hostname"], "--paginate", "--slurp",
             f"repos/{t['owner']}/{t['repo']}/pulls/{t['pr']}/reviews?per_page=100")
    if r.returncode != 0:
        return None
    try:
        pages = json.loads(r.stdout)
    except ValueError:
        return None
    if not (isinstance(pages, list) and all(isinstance(p, list) for p in pages)):
        return None
    reviews = [rv for p in pages for rv in p]
    return reviews if all(isinstance(rv, dict) and isinstance(rv.get("user") or {}, dict) for rv in reviews) else None


def latest_verdicts(reviews):
    """{login: latest approving, dismissed or changes-requested review}: a later
    comment-only review doesn't clear an earlier verdict."""
    latest = {}
    for rv in reviews:
        if rv.get("state") in VERDICTS:
            latest[(rv.get("user") or {}).get("login") or "?"] = rv
    return latest


def changes_requested_by(reviews):
    return sorted(u for u, rv in latest_verdicts(reviews).items()
                  if rv["state"] == "CHANGES_REQUESTED" and (rv.get("user") or {}).get("type") != "Bot")


def fresh_approvers(reviews, head, author):
    return sorted(u for u, rv in latest_verdicts(reviews).items()
                  if rv["state"] == "APPROVED" and rv.get("commit_id") == head and u != author)


def required_approvals(t, branch):
    """The highest required_approving_review_count among the base branch's
    rulesets, or 0 (also when they can't be read: branch protection still holds)."""
    if not branch:
        return 0
    r = c.gh("api", "--hostname", t["hostname"],
             f"repos/{t['owner']}/{t['repo']}/rules/branches/{urllib.parse.quote(branch, safe='')}")
    try:
        rules = json.loads(r.stdout) if r.returncode == 0 else []
    except ValueError:
        return 0
    counts = [(x.get("parameters") or {}).get("required_approving_review_count") for x in rules
              if isinstance(x, dict) and x.get("type") == "pull_request"] if isinstance(rules, list) else []
    return max([n for n in counts if isinstance(n, int) and not isinstance(n, bool)] + [0])


def open_shas(t, view):
    head, base = view.get("headRefOid"), view.get("baseRefOid")
    if view.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(view.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    if not (isinstance(head, str) and SHA.fullmatch(head) and isinstance(base, str) and SHA.fullmatch(base)):
        raise c.Handback("unknown", "gh gave no full head or base SHA", "Rerun the gate.")
    return head, base


def approval_block(blocks, t, view, reviews, head):
    decision = view.get("reviewDecision")
    if reviews is None:
        blocks.add("changes_requested", "couldn't read every review, so treating one as requesting changes",
                   "Check the PR's reviews by hand, then rerun.")
        return
    requesters = changes_requested_by(reviews)
    if decision == "CHANGES_REQUESTED" or requesters:
        who = f" ({', '.join(requesters[:10])})" if requesters else ""
        blocks.add("changes_requested", f"a reviewer requested changes{who}",
                   "Get that reviewer to re-review or dismiss their review, then rerun.")
    need = required_approvals(t, view.get("baseRefName"))
    if decision not in ("REVIEW_REQUIRED", "APPROVED") and not need:
        return
    want = max(1, need)
    fresh = fresh_approvers(reviews, head, (view.get("author") or {}).get("login"))
    if len(fresh) < want:
        stale = " (approvals of older commits are stale and don't count)" if any(
            rv["state"] == "APPROVED" for rv in latest_verdicts(reviews).values()) else ""
        blocks.add("not_approved", f"{want} approval(s) of head {head[:12]} by someone other than the author "
                                   f"needed, found {len(fresh)}{stale}",
                   "Get an approving review on the current head, then rerun.")


def github_blocks(t, view, head, base, settled=True):
    blocks = Blocks()
    if view.get("isDraft"):
        blocks.add("draft", f"PR {t['pr']} is a draft",
                   f"Mark it ready for review yourself (`gh pr ready {t['pr']}`) when it is, then rerun.")
    if view.get("mergeable") == "CONFLICTING" or view.get("mergeStateStatus") == "DIRTY":
        blocks.add("conflicts", "the PR conflicts with its base",
                   "Resolve the conflicts with the base branch, then rerun.")
    approval_block(blocks, t, view, github_reviews(t), head)
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
    if settled and mergeability_unknown(view):
        blocks.add("unknown", "GitHub still hadn't computed mergeability after re-polling",
                   "Rerun the gate in a minute.")
    review_block(blocks, t["pr"], head, base)
    return blocks


def disable_auto(t, h):
    """Turn the queued auto-merge off, then return the handback h saying so."""
    r = c.gh("pr", "merge", str(t["pr"]), "-R", c.gh_repo(t), "--disable-auto")
    if r.returncode == 0:
        return c.Handback(h.reason, f"{h.detail}; auto-merge disabled", h.action)
    err = c.one_line(r.stderr + r.stdout, 200) or f"gh exited {r.returncode}"
    return c.Handback(h.reason, f"{h.detail}; couldn't disable auto-merge ({err})",
                      f"First turn auto-merge off yourself (`gh pr merge {t['pr']} -R {c.gh_repo(t)} "
                      f"--disable-auto`). Then: {h.action}")


def wait_for_merge(t, head, base, delays, sleep=time.sleep):
    """Poll the auto-merge just enabled until the PR is merged, re-running every
    check; any change or new block turns auto-merge off and hands back."""
    for i in range(len(delays) + 1):
        if i:
            sleep(delays[i - 1])
        r = c.gh("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json", VIEW_FIELDS)
        try:
            view = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            view = None
        if not isinstance(view, dict):
            err = c.one_line(r.stderr, 200) or "not JSON"
            raise disable_auto(t, c.Handback("unknown", f"auto-merge was requested, but reading PR {t['pr']} back "
                                                        f"failed (gh exited {r.returncode}: {err})",
                                             "Check on GitHub whether the PR merged, then rerun the gate."))
        if view.get("state") == "MERGED":
            return
        if view.get("state") != "OPEN":
            raise disable_auto(t, c.Handback("not_open", f"PR {t['pr']} was {str(view.get('state')).lower()} "
                                                         "while auto-merge was pending", "Nothing to merge."))
        now = (view.get("headRefOid"), view.get("baseRefOid"))
        if now != (head, base):
            raise disable_auto(t, c.Handback(
                "needs_review", f"the head or base moved while auto-merge was pending (head {head[:12]} -> "
                                f"{str(now[0])[:12]}, base {base[:12]} -> {str(now[1])[:12]})",
                "Run the review loop on the new head and base, then the gate again."))
        blocks = github_blocks(t, view, head, base, settled=False)
        if blocks.items:
            raise disable_auto(t, blocks.handback())
    raise disable_auto(t, c.Handback("merge_failed", f"auto-merge was enabled but PR {t['pr']} hadn't merged after "
                                                     f"{int(sum(delays))}s",
                                     "See what GitHub is waiting for on the PR, then rerun the gate with --merge."))


def github_gate(t, do_merge):
    repo, _ = c.github_access(t)
    view = github_view(t)
    head, base = open_shas(t, view)
    merge_delays = env_delays("VSTACK_MERGE_POLL_DELAYS", MERGE_POLL_DELAYS, 600) if do_merge else ()
    github_blocks(t, view, head, base).raise_first()
    out = {"host": "github", "pr": t["pr"], "head_sha": head, "base_sha": base}
    if not do_merge:
        return {"outcome": "ready", **out}
    method = merge_method(repo)
    r = c.gh("pr", "merge", str(t["pr"]), "-R", c.gh_repo(t), "--auto", f"--{method}",
             "--match-head-commit", head)
    if r.returncode != 0:
        raise github_merge_error(r, t)
    wait_for_merge(t, head, base, merge_delays)
    return {"outcome": "merged", "method": method, **out}


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


def bitbucket_gate(t):
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
    elif not statuses:
        blocks.add("checks_failing", "no build status on the PR, so nothing shows its CI passed",
                   "Check the PR's builds by hand (or set up CI for it), then rerun.")
    tasks = [x for x in c.bb_pages(f"{prefix}/tasks", "reading tasks") if x.get("state") == "UNRESOLVED"]
    comments = [x for x in c.bb_pages(f"{prefix}/comments", "reading comments")
                if x.get("inline") and not x.get("parent") and not x.get("deleted") and not x.get("resolution")]
    if tasks or comments:
        blocks.add("unresolved_threads", f"{len(tasks)} open task(s) and {len(comments)} unresolved inline comment(s)",
                   "A human must resolve the open tasks and comments (autonomous-merge only replies), then rerun.")
    review_block(blocks, t["pr"], head, base)
    blocks.raise_first()
    raise c.Handback("merge_unsupported_host",
                     f"PR {t['pr']} passed every gate check on head {head} and is ready to merge, but autonomous-merge "
                     "never merges on Bitbucket: its merge API can't be pinned to a head commit, there's no babysit "
                     "gate, and merge permission only shows at merge time",
                     f"Merge it yourself in Bitbucket, after checking the head is still {head[:12]}.")


def main():
    ap = argparse.ArgumentParser(prog="gate.py", add_help=False)
    ap.add_argument("pr")
    ap.add_argument("--merge", action="store_true")
    try:
        a = ap.parse_args()
    except SystemExit:
        return c.USAGE
    t = c.parse_target(a.pr)
    out = github_gate(t, a.merge) if t["host"] == "github" else bitbucket_gate(t)
    c.emit(out)
    return c.OK


if __name__ == "__main__":
    c.main_wrapper("gate", main)
