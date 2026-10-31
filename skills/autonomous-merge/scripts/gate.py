"""The merge gate: fresh live checks of a PR right before merging, and the merge
itself only with --merge, and only on GitHub.

Usage: python3 -I gate.py <pr-number-or-url> [--merge]

Run from the PR's repo (the review result is read from its git common dir).
Every decision comes from the forge's live state, re-read on each run. The one
file it reads is review's result, through review's read-result.py, and only to
require a review whose head and base match the live PR and whose merge_ok is
true; the file can't make a blocked PR pass.

GitHub blocks: closed or merged, draft, a hold label (hold, on-hold,
do-not-merge, "do not merge", DNM or wip, case-insensitive exact match, the
same set as babysit's merge.md; labels that can't be read count as one),
conflicts, changes requested (by
reviewDecision, or by any human reviewer whose latest approving, dismissed or
changes-requested review is CHANGES_REQUESTED), a missing approval, a required
check not passing (all checks, if none are required or the required ones
can't be read), any
unresolved review thread (a human's stays until a human resolves it),
mergeStateStatus BLOCKED or BEHIND (the gate never updates the branch: babysit
does that, and the new head goes back through review), unknown mergeability
(re-polled first, after each delay in VSTACK_POLL_DELAYS, comma-separated
seconds, default 2,4,8,16),
no clean review on this head, and, before a merge is armed,
babysit's fixer coordination (../../babysit/scripts/coordination.py, which
records this look in its state): a handback from it (conflicting_fixer,
escalated, stale_claim), a PR that isn't settled (not_settled; with no earlier babysit
poll of this head it never is), or any item another fixer or babysit has
claimed (claimed_items). An approval is needed only when
the repo requires one (reviewDecision REVIEW_REQUIRED, or a
required_approving_review_count from classic branch protection or a ruleset)
or reviewDecision is APPROVED; then it must be an APPROVED review on the
current head by someone other than the author and not a bot (an approval of an
older commit is stale and doesn't count). With no review requirement (the
protection read 404s "Branch not protected" or the branch says protected false,
and the paginated rulesets have no pull_request rule above 0, or 404), no
approval is needed. When protection or rulesets can't be read (403, a plain 404
for a non-admin, anything else), the requirement is unknown and fails closed:
at least one such approval is needed (not_approved), and GitHub enforces any
higher count when it merges.

With --merge, and only once every block above is clear, it runs
`gh pr merge <n> -R <host/o/r> --auto --<method> --match-head-commit <head>`,
where method is the only one the repo allows, else squash if allowed (also when
the repo's settings can't be read), else merge. GitHub fires an auto-merge
later without checking again, so the gate then polls (after each delay in
VSTACK_MERGE_POLL_DELAYS, default about 5 minutes in total, and for at most
VSTACK_MERGE_DEADLINE seconds, default and most 480, which also caps each gh
call's timeout) until the PR is merged, re-running every check each time,
coordination included (settled isn't needed then, but a coordination handback,
a claimed item or another actor's working signal, such as a new 👀 on the PR,
hands back as claimed_items or the coordination reason). From
the merge call on, every other way out (a head or base change, any new block,
an unreadable PR, the delays or the deadline running out, an internal error,
Ctrl-C or SIGTERM) runs `gh pr merge <n> -R <host/o/r> --disable-auto` and
hands back; if that fails because the PR has just merged, the outcome is
merged. A SIGKILL or a suspended machine can't be caught: if the gate printed
nothing, auto-merge may still be on. Never --admin, never a push, never a
review dismissal.

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
import signal
import time
import types
import urllib.parse

_spec = importlib.util.spec_from_file_location(
    "vstack_am_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c)

SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
PASS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BUCKET = {"pass": "passing", "skipping": "passing", "pending": "pending", "fail": "failing", "cancel": "failing"}
# gh pr checks --required exits 1 with this message when the branch has no required checks.
NO_REQUIRED = re.compile(r"no required checks reported", re.IGNORECASE)
CHECK_SCOPES = {"required": "required check(s)",
                "none_required": "check(s) (no required checks configured, all treated as required)",
                "unreadable": "check(s) (required ones unreadable, all treated as required)"}
METHODS = (("merge", "allow_merge_commit"), ("squash", "allow_squash_merge"), ("rebase", "allow_rebase_merge"))
BB_CONFLICT = {"merge conflict", "local deleted", "remote deleted"}
POLL_DELAYS = (2, 4, 8, 16)
MERGE_POLL_DELAYS = (5, 10, 15) + (30,) * 9
MERGE_DEADLINE = 480
VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
VIEW_FIELDS = ("state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefOid,baseRefName,author,"
               "statusCheckRollup,labels")
# The same set as babysit's merge.md gate 6, matched case-insensitively and exactly.
HOLD_LABELS = {"hold", "on-hold", "do-not-merge", "do not merge", "dnm", "wip"}

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
    """(checks, scope): scope is "required" when the required checks were read, else every check in the
    rollup is treated as required, with scope "none_required" (an empty list, or gh's own "no required
    checks reported") or "unreadable" (gh failed or printed something that isn't a list)."""
    r = c.gh("pr", "checks", str(t["pr"]), "-R", c.gh_repo(t), "--required", "--json", "name,bucket")
    try:
        required = json.loads(r.stdout)
    except ValueError:
        required = None
    if isinstance(required, list) and required:
        return [(x.get("name") or "?", BUCKET.get(x.get("bucket"), "failing")) for x in required], "required"
    none = required == [] or NO_REQUIRED.search(r.stderr or "") is not None
    rollup = view.get("statusCheckRollup") or []
    checks = [(x.get("name") or x.get("context") or "?", check_state(x)) for x in rollup]
    return checks, "none_required" if none else "unreadable"


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


def label_block(blocks, view):
    labels = view.get("labels")
    if not (isinstance(labels, list) and all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in labels)):
        blocks.add("hold_label", "couldn't read the PR's labels, so treating one as a hold label",
                   "Check the PR's labels by hand, then rerun.")
        return
    held = [x["name"] for x in labels if x["name"].lower() in HOLD_LABELS]
    if held:
        blocks.add("hold_label", f"the PR has a hold label: {', '.join(held)}",
                   "Whoever added it removes it when the PR may merge; never remove it yourself. Then rerun.")


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


def is_bot(rv):
    user, author = rv.get("user"), rv.get("author")
    return ((isinstance(user, dict) and user.get("type") == "Bot")
            or (isinstance(author, dict) and author.get("__typename") == "Bot"))


def changes_requested_by(reviews):
    return sorted(u for u, rv in latest_verdicts(reviews).items()
                  if rv["state"] == "CHANGES_REQUESTED" and not is_bot(rv))


def fresh_approvers(reviews, head, author):
    return sorted(u for u, rv in latest_verdicts(reviews).items()
                  if rv["state"] == "APPROVED" and rv.get("commit_id") == head and u != author and not is_bot(rv))


def protection_approvals(t, branch):
    """The base branch's classic protection required_approving_review_count: 0
    when the branch isn't protected (a 404 "Branch not protected", or protected
    false on the branch, which needs no admin), None when it can't be read."""
    if not branch:
        return None
    path = f"repos/{t['owner']}/{t['repo']}/branches/{urllib.parse.quote(branch, safe='')}"
    r = c.gh("api", "--hostname", t["hostname"], f"{path}/protection")
    if r.returncode == 0:
        try:
            p = json.loads(r.stdout)
        except ValueError:
            return None
        if not isinstance(p, dict):
            return None
        n = (p.get("required_pull_request_reviews") or {}).get("required_approving_review_count", 0)
        return n if isinstance(n, int) and not isinstance(n, bool) else None
    if "branch not protected" in (r.stderr + r.stdout).lower():
        return 0
    r = c.gh("api", "--hostname", t["hostname"], path)
    try:
        return 0 if r.returncode == 0 and json.loads(r.stdout).get("protected") is False else None
    except (ValueError, AttributeError):
        return None


def ruleset_approvals(t, branch):
    """The highest required_approving_review_count among the base branch's
    rulesets: 0 when there are none (or the endpoint 404s), None when they
    can't be read."""
    if not branch:
        return None
    r = c.gh("api", "--hostname", t["hostname"], "--paginate", "--slurp",
             f"repos/{t['owner']}/{t['repo']}/rules/branches/{urllib.parse.quote(branch, safe='')}?per_page=100")
    if r.returncode != 0:
        return 0 if "HTTP 404" in r.stderr + r.stdout else None
    try:
        pages = json.loads(r.stdout)
    except ValueError:
        return None
    if not (isinstance(pages, list) and all(isinstance(p, list) for p in pages)):
        return None
    counts = [(x.get("parameters") or {}).get("required_approving_review_count") for p in pages for x in p
              if isinstance(x, dict) and x.get("type") == "pull_request"]
    return max([n for n in counts if isinstance(n, int) and not isinstance(n, bool)] + [0])


def open_shas(t, view):
    head, base = view.get("headRefOid"), view.get("baseRefOid")
    if view.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(view.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    if not (isinstance(head, str) and SHA.fullmatch(head) and isinstance(base, str) and SHA.fullmatch(base)):
        raise c.Handback("unknown", "gh gave no full head or base SHA", "Rerun the gate.")
    return head, base


def required_approvals(t, branch):
    """(count, unreadable): the most approvals classic protection or a ruleset
    requires on branch, and which of the two couldn't be read."""
    counts = {"branch protection": protection_approvals(t, branch), "rulesets": ruleset_approvals(t, branch)}
    return max([n for n in counts.values() if n is not None] + [0]), [k for k, n in counts.items() if n is None]


def approval_block(blocks, view, reviews, head, need, unreadable=()):
    """An approval is needed when the repo requires one or reviewDecision says a
    review applies. When protection or rulesets can't be read, the requirement
    is unknown: fail closed and need at least one, but don't block outright,
    since GitHub still enforces any higher count when it merges."""
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
    if decision not in ("REVIEW_REQUIRED", "APPROVED") and not need and not unreadable:
        return
    want = max(1, need)
    fresh = fresh_approvers(reviews, head, (view.get("author") or {}).get("login"))
    if len(fresh) < want:
        stale = " (approvals of older commits are stale and don't count)" if any(
            rv["state"] == "APPROVED" for rv in latest_verdicts(reviews).values()) else ""
        why = (f"; couldn't read the base branch's {' or '.join(unreadable)}, so an approval is required"
               if unreadable else "")
        blocks.add("not_approved", f"{want} approval(s) of head {head[:12]} by someone other than the author "
                                   f"or a bot needed, found {len(fresh)}{stale}{why}",
                   "Get an approving review on the current head, or let gh read the base branch's protection "
                   "and rulesets, then rerun." if unreadable else
                   "Get an approving review on the current head, then rerun.")


def github_blocks(t, view, head, base, settled=True):
    blocks = Blocks()
    if view.get("isDraft"):
        blocks.add("draft", f"PR {t['pr']} is a draft",
                   f"Mark it ready for review yourself (`gh pr ready {t['pr']}`) when it is, then rerun.")
    label_block(blocks, view)
    if view.get("mergeable") == "CONFLICTING" or view.get("mergeStateStatus") == "DIRTY":
        blocks.add("conflicts", "the PR conflicts with its base",
                   "Resolve the conflicts with the base branch, then rerun.")
    need, unreadable = required_approvals(t, view.get("baseRefName"))
    approval_block(blocks, view, github_reviews(t), head, need, unreadable)
    checks, source = github_checks(t, view)
    bad = [f"{n} ({s})" for n, s in checks if s != "passing"]
    if bad:
        scope = CHECK_SCOPES[source]
        blocks.add("checks_failing", f"{len(bad)} {scope} not passing: {', '.join(bad[:10])}",
                   "Fix or rerun the failing checks (babysit does this) and wait for pending ones, then rerun.")
    thread_block(blocks, github_threads(t))
    status = view.get("mergeStateStatus")
    if status in ("BLOCKED", "BEHIND"):
        why = "behind its base, and protection requires it up to date" if status == "BEHIND" else "blocked"
        blocks.add("branch_protection", f"branch protection: the PR is {why} (mergeStateStatus {status})",
                   "Check what the base branch's protection rules still need (approvals, checks, an up-to-date "
                   "branch: babysit runs `gh pr update-branch`, then review reruns on the new head); never bypass "
                   "them. Then rerun.")
    if settled and mergeability_unknown(view):
        blocks.add("unknown", "GitHub still hadn't computed mergeability after re-polling",
                   "Rerun the gate in a minute.")
    review_block(blocks, t["pr"], head, base)
    return blocks


def coordination_block(blocks, t, armed=False):
    """Before arming: the PR must be settled, with no claimed item (babysit's
    coordination.py, run as one more poll). Once auto-merge is armed, settled
    isn't needed, but a coordination handback, a claimed item or another
    actor's working signal on the PR still blocks."""
    try:
        coord = c.load_module("vstack_babysit_coordination", c.COORDINATION)
        res = coord.poll(t["pr"], coord.fetch(t["pr"], c.gh_repo(t), gh=c.gh), gh=c.gh)
    except Exception as e:
        blocks.add("unknown", f"couldn't check for other fixers with babysit's coordination.py: "
                              f"{c.one_line(e, 200) or type(e).__name__}", "Fix the cause, then rerun the gate.")
        return
    if res["handback"]:
        hb = res["handback"]
        blocks.add(hb["reason"], hb["detail"], hb["action"])
    if not res["settled"] and not armed:
        blocks.add("not_settled", "the PR isn't settled: " + "; ".join(res["settled_reasons"]),
                   "Let babysit keep polling until checks finish and nobody else is working on the PR, "
                   "then rerun the gate.")
    claimed = [f"{i['id']} ({i['claimant']})" for i in res["items"] if i["state"] in ("claimed", "stale-claim")]
    if armed:
        claimed += [f"the PR ({r[len('working: '):]})" for r in res["settled_reasons"] if r.startswith("working: ")]
    if claimed:
        blocks.add("claimed_items", f"{len(claimed)} item(s) still claimed by a fixer: {', '.join(claimed[:10])}",
                   "Wait for those fixers to finish (babysit tracks them), then rerun the gate.")


class Terminated(BaseException):
    """SIGTERM, raised so the gate can turn auto-merge off before it exits."""


def on_sigterm(signum, frame):
    raise Terminated()


def gh_quiet(*args):
    """c.gh for cleanup: a timeout comes back as a failed result, not a handback."""
    try:
        return c.gh(*args, timeout=60)
    except c.Handback as h:
        return types.SimpleNamespace(returncode=124, stdout="", stderr=h.detail)


def pr_state(t):
    r = gh_quiet("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json", "state")
    try:
        view = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None
    return view.get("state") if isinstance(view, dict) else None


def disable_auto(t, h):
    """Turn the queued auto-merge off and return the handback h saying so, or
    None when that failed because the PR had just merged."""
    r = gh_quiet("pr", "merge", str(t["pr"]), "-R", c.gh_repo(t), "--disable-auto")
    if r.returncode == 0:
        return c.Handback(h.reason, f"{h.detail}; auto-merge disabled", h.action)
    if pr_state(t) == "MERGED":
        return None
    err = c.one_line(r.stderr + r.stdout, 200) or f"gh exited {r.returncode}"
    return c.Handback(h.reason, f"{h.detail}; couldn't disable auto-merge ({err})",
                      f"First turn auto-merge off yourself (`gh pr merge {t['pr']} -R {c.gh_repo(t)} "
                      f"--disable-auto`). Then: {h.action}")


def interrupted(e):
    what = {KeyboardInterrupt: "was interrupted (Ctrl-C)", Terminated: "was stopped by SIGTERM"}.get(
        type(e), f"hit an internal error ({type(e).__name__})")
    return c.Handback("unknown", f"the gate {what} while auto-merge was pending",
                      "Check on GitHub whether the PR merged, then rerun the gate.")


def poll_merged(t, head, base):
    """True once the PR has merged; a handback on any change or new block."""
    r = c.gh("pr", "view", str(t["pr"]), "-R", c.gh_repo(t), "--json", VIEW_FIELDS)
    try:
        view = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        view = None
    if not isinstance(view, dict):
        err = c.one_line(r.stderr, 200) or "not JSON"
        raise c.Handback("unknown", f"auto-merge was requested, but reading PR {t['pr']} back "
                                    f"failed (gh exited {r.returncode}: {err})",
                         "Check on GitHub whether the PR merged, then rerun the gate.")
    if view.get("state") == "MERGED":
        return True
    if view.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} was {str(view.get('state')).lower()} "
                                     "while auto-merge was pending", "Nothing to merge.")
    now = (view.get("headRefOid"), view.get("baseRefOid"))
    if now != (head, base):
        raise c.Handback("needs_review", f"the head or base moved while auto-merge was pending (head {head[:12]} -> "
                                         f"{str(now[0])[:12]}, base {base[:12]} -> {str(now[1])[:12]})",
                         "Run the review loop on the new head and base, then the gate again.")
    blocks = github_blocks(t, view, head, base, settled=False)
    coordination_block(blocks, t, armed=True)
    if blocks.items:
        raise blocks.handback()
    return False


def wait_for_merge(t, head, base, delays, seconds, sleep=time.sleep, clock=time.monotonic):
    """Poll the auto-merge just enabled until the PR is merged, re-running every
    check, for at most seconds; any change, new block or the deadline raises a
    handback (the caller turns auto-merge off)."""
    deadline = clock() + seconds
    late = c.Handback("merge_failed", f"auto-merge was enabled but PR {t['pr']} hadn't merged by the "
                                      f"{seconds:g}s deadline",
                      "See what GitHub is waiting for on the PR, then rerun the gate with --merge.")
    for i in range(len(delays) + 1):
        if i:
            sleep(max(0, min(delays[i - 1], deadline - clock())))
        if clock() >= deadline:
            raise late
        try:
            if poll_merged(t, head, base):
                return
        except c.Handback:
            if clock() >= deadline:
                raise late from None
            raise
    raise c.Handback("merge_failed", f"auto-merge was enabled but PR {t['pr']} hadn't merged after "
                                     f"{int(sum(delays))}s",
                     "See what GitHub is waiting for on the PR, then rerun the gate with --merge.")


def merge_and_wait(t, head, base, method, delays, seconds, sleep=time.sleep, clock=time.monotonic):
    """Enable auto-merge and wait for it. From the merge call on, every way out
    but a merge (a handback, an internal error, Ctrl-C, SIGTERM, the deadline)
    turns auto-merge off first, with SIGINT and SIGTERM ignored while it does.
    A SIGKILL or a suspended machine can't be caught."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, on_sigterm)
    refused = False
    try:
        r = c.gh("pr", "merge", str(t["pr"]), "-R", c.gh_repo(t), "--auto", f"--{method}",
                 "--match-head-commit", head)
        if r.returncode != 0:
            refused = True
            raise github_merge_error(r, t)
        c.DEADLINE = time.monotonic() + seconds
        wait_for_merge(t, head, base, delays, seconds, sleep, clock)
    except BaseException as e:
        for s in saved:
            signal.signal(s, signal.SIG_IGN)
        c.DEADLINE = None
        if refused:
            raise
        h = disable_auto(t, e if isinstance(e, c.Handback) else interrupted(e))
        if h is not None:
            raise h from None
    finally:
        c.DEADLINE = None
        for s, old in saved.items():
            signal.signal(s, signal.SIG_DFL if old is None else old)


def env_seconds(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        seconds = None
    if seconds is None or not 0 <= seconds <= default:
        raise c.Usage(f"{name} must be seconds from 0 to {default}")
    return seconds


def github_gate(t, do_merge):
    repo, _ = c.github_access(t)
    view = github_view(t)
    head, base = open_shas(t, view)
    merge_delays = env_delays("VSTACK_MERGE_POLL_DELAYS", MERGE_POLL_DELAYS, 600) if do_merge else ()
    merge_seconds = env_seconds("VSTACK_MERGE_DEADLINE", MERGE_DEADLINE) if do_merge else 0
    blocks = github_blocks(t, view, head, base)
    coordination_block(blocks, t)
    blocks.raise_first()
    out = {"host": "github", "pr": t["pr"], "head_sha": head, "base_sha": base}
    if not do_merge:
        return {"outcome": "ready", **out}
    method = merge_method(repo)
    merge_and_wait(t, head, base, method, merge_delays, merge_seconds)
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
