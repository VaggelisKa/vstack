# Handbacks

Every script prints exactly one JSON object on stdout. A handback is

```json
{"outcome": "handback", "reason": "auth_missing",
 "detail": "gh is not logged in to github.com",
 "action": "Run `gh auth login --hostname github.com` yourself, then rerun."}
```

with exit code 5, plus one human-readable line on stderr. `detail` and
`action` are single lines with tokens redacted. Exit codes: 0 ok, ready or
merged; 2 usage (a bad call, or a state file that fails its checks); 4
internal error; 5 handback.

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
| `changes_requested` | gate | A reviewer requested changes | Get a re-review or dismissal |
| `not_approved` | gate | Protection requires an approval (GitHub), or nobody approved (Bitbucket) | Get an approval on the head |
| `checks_failing` | gate | A required check is failing or pending (all checks when the required ones can't be read) | Fix, rerun or wait |
| `unresolved_threads` | gate | An unresolved review thread (Bitbucket: open task or inline comment), or threads couldn't all be read | A human resolves theirs |
| `branch_protection` | gate | `mergeStateStatus` BLOCKED or BEHIND, or the merge was refused by protection or merge checks | Meet the rule; never bypass it |
| `needs_review` | gate, state | No `review.json` for this head and base, or it isn't a clean Ship | Run review on the head |
| `escalated` | the skill | A finding or comment needs a human decision, or babysit escalated | Decide it |
| `cap_reached` | state | 3 review rounds, 6 fix rounds or 2 hours used | Finish by hand, `init --continue` or `reset` |
| `oscillation` | state | A fixed finding came back, or a commit put a file back to how it was before a fix | Decide which version is right |
| `merge_failed` | gate | The merge call failed for another reason (gh's or Bitbucket's message is in `detail`), or the head moved | Read the message |
| `unknown` | any | Anything else (no git remote, mergeability still computing, network) | Read `detail` |

The gate collects every block it finds, reports the first in the table's gate
order (not_open, draft, conflicts, changes_requested, not_approved,
checks_failing, unresolved_threads, branch_protection, unknown, needs_review)
and lists the rest in `detail` after "also".

No script prompts, logs in, or prints a token. A Bitbucket token goes to curl
as a config file on stdin, never in argv, and any token-shaped text in a
forge's error message is replaced with `[redacted]`.
