# Handbacks

Every script prints exactly one JSON object on stdout. A handback is

```json
{"outcome": "handback", "reason": "auth_missing",
 "detail": "gh is not logged in to github.com",
 "action": "Run `gh auth login --hostname github.com` yourself, then rerun."}
```

with exit code 5, plus one human-readable line on stderr. `detail` and
`action` are single lines with tokens redacted. Exit codes: 0 ok, ready or
merged; 2 usage (a bad call, `-h`, or a state file that fails its checks); 4
internal error, which still prints an `unknown` handback on stdout first; 5
handback.

| Reason | From | Means | What the user does |
|---|---|---|---|
| `no_cli` | preflight, gate | `gh` (GitHub) or `curl` (Bitbucket) isn't on PATH | Install it |
| `auth_missing` | preflight, gate | gh isn't logged in, or no `BITBUCKET_TOKEN` / `BITBUCKET_USERNAME`+`BITBUCKET_APP_PASSWORD` | `gh auth login`, or set the env vars |
| `auth_expired` | preflight, gate | The token was rejected (401, invalid, expired) | Log in again or replace the token |
| `insufficient_permission` | preflight, gate | No push or merge permission, or the repo or PR can't be read | `gh auth refresh -s repo`, or a token with PR write access |
| `host_unsupported` | preflight, gate | Not github.com, *.ghe.com or bitbucket.org | Merge it by hand |
| `not_open` | preflight, gate, state | Closed, merged or declined (or this run already merged it) | Nothing, or reopen |
| `draft` | gate | The PR is a draft | Mark it ready when it is |
| `conflicts` | gate | Conflicts with the base | Resolve them |
| `changes_requested` | gate | A reviewer requested changes (`reviewDecision`, or a human's latest verdict), or the reviews couldn't be read | Get a re-review or dismissal |
| `not_approved` | gate | An approval is required (or `reviewDecision` is APPROVED) but none from a non-author, non-bot reviewer is on the current head; stale approvals don't count (GitHub). Nobody approved (Bitbucket) | Get an approval on the head |
| `checks_failing` | gate | A required check is failing or pending (all checks when the required ones can't be read); on Bitbucket also no build status at all | Fix, rerun or wait |
| `unresolved_threads` | gate | An unresolved review thread (Bitbucket: open task or inline comment), or threads couldn't all be read | A human resolves theirs |
| `branch_protection` | gate | `mergeStateStatus` BLOCKED or BEHIND, or the merge was refused by protection or merge checks | Meet the rule; never bypass it |
| `needs_review` | gate, state | No `review.json` for this head and base, or it isn't a clean Ship; or the head or base moved while auto-merge was pending | Run review on the head |
| `escalated` | state escalate, state init | A finding or comment needs a human decision (ambiguous, a code owner's), or babysit escalated; or `init` found review's or babysit's result for the PR with no state file | Decide it; for `init`, run `init --fresh` yourself if a new run should start |
| `security_blocker` | state | A `security` or `data-integrity` blocker (from review, or escalated) | Fix it yourself |
| `ci_unreproducible` | state escalate | A red check that can't be reproduced locally | Read the logs, fix or rerun |
| `conflict_judgement` | state escalate | A conflict with the base that needs a choice between both sides | Resolve it |
| `design_change` | state escalate | A reviewer asked for a design change | Decide and reply |
| `scope_change` | state escalate | A request to change the PR's scope | Decide and reply |
| `cap_reached` | state | 3 review rounds, 6 fix rounds or 2 hours used | Finish by hand, `init --continue` or `reset` |
| `oscillation` | state | A fixed finding came back, or a commit put a file back to how it was before a fix | Decide which version is right |
| `no_progress` | state | A review round didn't reduce the blocker plus major count | Fix or decide the findings by hand |
| `not_settled` | gate | babysit's coordination says the PR isn't settled: checks still running, a new head since babysit last looked (or babysit never looked), or another actor still working | Let babysit keep polling, then rerun |
| `claimed_items` | gate | A failing check or thread is claimed by another fixer (or by babysit) and not addressed yet | Wait for them to finish, then rerun |
| `conflicting_fixer` | gate, babysit | A check fixed by one fixer broke again after the other's commit (or after foreign commits twice) | Pick one fixer and pause the other |
| `stale_claim` | gate, babysit | Another fixer claimed an item and went quiet (2 CI cycles or 3 polls) | Fix it yourself, or rerun babysit asking it to take over |
| `merge_unsupported_host` | gate | A Bitbucket PR passed every check; autonomous-merge never merges there (no head-pinned merge, no babysit gate, merge permission only known at merge time) | Merge it yourself in Bitbucket |
| `merge_failed` | gate | `gh pr merge` failed for another reason (gh's message is in `detail`), or auto-merge hadn't merged when the poll delays or the 8-minute deadline ran out (it's then disabled) | Read the message |
| `unknown` | any | Anything else (no git remote, mergeability still unknown after re-polling, the base branch's rulesets unreadable, the PR unreadable after auto-merge was enabled, the gate interrupted by Ctrl-C or SIGTERM or an internal error while auto-merge was pending, network, an internal error) | Read `detail` |

`init --continue`, `init --fresh` and `reset` are the user's to run, never the
model's. When the gate hands back after enabling auto-merge, it has already run
`gh pr merge --disable-auto` (or says in `action` that you must); if that
failed because the PR had just merged, the outcome is `merged` instead. If
`gate.py --merge` printed nothing or no JSON (SIGKILL, a suspended machine),
run `gh pr merge N --disable-auto` (or check `autoMergeRequest` with
`gh pr view N --json autoMergeRequest`) before anything else.

The gate collects every block it finds, reports the first in the table's gate
order (not_open, draft, conflicts, changes_requested, not_approved,
checks_failing, unresolved_threads, branch_protection, unknown, needs_review,
then coordination's conflicting_fixer or stale_claim, not_settled, claimed_items
on GitHub, or merge_unsupported_host on Bitbucket)
and lists the rest in `detail` after "also".

No script prompts, logs in, or prints a token. A Bitbucket token goes to curl
as a config file on stdin, never in argv (a token, username or password with a
line break is refused as `auth_missing`), and any token-shaped text in a
forge's error message is replaced with `[redacted]`.
