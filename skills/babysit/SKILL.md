---
name: babysit
argument-hint: "<pr> [--merge]"
description: Watch a GitHub pull request until it is done - poll CI checks, reviews and review threads, triage every comment, fix what is in scope, reply to the rest, escalate what needs a human, and (only with --merge) merge it once every gate passes. Use this skill whenever the user says "babysit <pr>", "babysit <pr> --merge", or asks to watch, shepherd, monitor, land, or get a PR green, keep an eye on CI, handle review feedback on a PR, or "take this PR to merge", even if they don't say "babysit".
---

# babysit

Shepherd one pull request from "open" to "green and approved" (or merged) with
plain `gh` and `git`, without a human driving each step.

```
babysit <pr>            # watch, fix, reply, report. Never merges.
babysit <pr> --merge    # same loop, then merge when every gate in merge.md passes
```

### Arguments

Read the arguments from the user's message, whatever the invocation looks like
(`/vstack:babysit 42 --merge`, `$babysit 42`, `/babysit 42`, or "babysit
PR 42 and merge it"). Don't rely on a template variable being filled in.

- `<pr>`: the first PR number, PR URL or branch name in the message, as accepted
  by `gh pr view`. If there is none, use the PR for the current branch
  (`gh pr view --json number`); if that fails, ask which PR.
- `--merge`: on only if the message contains `--merge` (or plainly asks you to
  merge when done). Otherwise merge mode is off.

`<pr>` is a number, URL or branch accepted by `gh pr view`. **Default is no
merge.** Read [merge.md](merge.md) only when `--merge` was given.

Requires `gh` 2.42 or newer (for `gh pr update-branch`; `gh pr checks --required`
and `gh pr merge --match-head-commit` are older). Check with `gh --version`.

## Setup

1. Resolve the PR and repo: `gh pr view <pr> --json number,url,headRefName,baseRefName,headRefOid,state,isDraft`.
2. Check out the head branch locally (`gh pr checkout <pr>`). Make sure you can
   push to it; if not, run in reply-and-escalate mode only and say so.
3. Find how to run the repo's tests, lint and type checks locally (README,
   CONTRIBUTING, package scripts, Makefile, CI workflow files). You need these
   before every push.
4. Record the start time, the last human activity time (= start), `fix_rounds = 0`,
   an empty escalation list, an empty set of already-handled comment/thread IDs,
   and a per-check failure history keyed by check name and head SHA.

## Polling

Each poll, gather state for the current head SHA:

```bash
gh pr view <pr> --json state,isDraft,headRefOid,mergeable,mergeStateStatus,reviewDecision,reviews,comments,labels,statusCheckRollup
```

Each entry in `reviews` carries `commit.oid`, the SHA the review was made on.

`gh pr view` does not expose review-thread resolution, so fetch threads with
GraphQL, **paging through everything**:

```bash
gh api graphql --paginate --slurp -f owner=OWNER -f repo=REPO -F pr=NUMBER -f query='
query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100, after:$endCursor){
      pageInfo{ hasNextPage endCursor }
      nodes{
        id isResolved isOutdated path line
        comments(first:100){
          pageInfo{ hasNextPage endCursor }
          nodes{ id databaseId author{__typename login} body createdAt url }
        }
      } } } } }'
```

`--paginate` walks `reviewThreads` via `$endCursor`, and `--slurp` wraps the
pages into one JSON array (one element per page), so concatenate each page's
`reviewThreads.nodes` to get the full list. If any thread's
`comments.pageInfo.hasNextPage` is true, page that thread with
`node(id: THREAD_ID){ ... on PullRequestReviewThread { comments(first:100, after:CURSOR){...} } }`.
If you can't fetch every page, treat the thread state as **unknown**: that fails
the "zero unresolved threads" condition and blocks merge.

A comment author is a bot only when `author.__typename == "Bot"`.

An **event** is anything new since the last poll: a new head SHA, a check
changing state, a new review, comment or thread, a thread resolved/unresolved,
a label change.

### Cadence

- While any check is queued or running: poll every **2 min**.
- When idle (nothing running, waiting on humans): back off **2 → 4 → 8 → 15 min**,
  then stay at 15.
- Any new event resets the interval to **2 min**.

### Stop conditions

Stop and write the final report when the first of these is true:

1. The PR is **merged or closed**.
2. It is **done**: all checks green on the head SHA, approved by a review whose
   `commit.oid` equals the current `headRefOid` (older approvals don't count),
   no outstanding changes-requested review, and zero unresolved threads.
   Without `--merge`, this is a stop: report. With `--merge`, it is **not** a
   stop: hand off to merge.md, which keeps the loop running until the PR merges
   or another stop condition hits.
3. **24 h** of wall time since start.
4. **6 h idle waiting on humans**: 6 h since the last human activity. Only human
   activity resets this timer (a review, comment, thread change, label change or
   push by a non-bot user). Your own pushes, reruns and replies, and bot comments,
   do **not** reset it. (They still reset the poll interval.)
5. An escalation that blocks all further progress (see limits below).

## Each poll: what to do

1. **Checks.** For each failing check on the head SHA, read its logs
   (`gh run view <run-id> --log-failed`, or the check's details URL).
   - Failure caused by the PR → a **fix** item.
   - Looks flaky or infra (timeouts, runner loss, network, rate limits, unrelated
     to the diff): rerun it **once** (`gh run rerun <run-id> --failed`). Each
     check gets at most one rerun for the whole session. If it fails again, check
     the same workflow on the base branch (`gh run list --branch <base> --workflow <wf>`):
     - fails on base too → **escalate** as a pre-existing failure. Don't fix the
       base branch from this PR.
     - green on base → it's the PR's failure: make it a **fix** item. It counts
       toward the "same check fails on two consecutive head SHAs" rule and is
       never rerun again.
   - A real (non-flaky) failure is **never rerun**; it goes straight to a fix item.
   - Record each failure in the per-check history with the head SHA.
2. **Feedback.** Triage every unhandled review, review comment, thread and PR
   comment (humans and bots) using the table below. Mark each as handled once
   acted on.
3. **Act.** If there are fix items, run one fix round (below). Post replies.
4. **Re-evaluate** stop conditions, then sleep for the current interval.

## Triage

| Bucket | What goes in it | Action |
|---|---|---|
| **Fix** | In-scope correctness bugs; tests; types; lint; repo conventions; actionable bot suggestions; checks this PR broke | Change code in the next fix round, then reply with the commit link |
| **Reply only** | Questions | Answer |
| | Already fixed | Reply linking the commit that fixed it |
| | Nits you disagree with | Explain once, briefly. Don't argue a second time; if they push back, it's theirs to decide |
| | Out of scope | Say so and offer to open a follow-up issue |
| **Escalate** (don't touch code) | Design pushback; scope changes; reviewers who conflict; anything security or auth; migrations; CI or secrets configuration; major dependency bumps; product calls | Add to the escalation list, reply that a maintainer needs to decide, move on |

Rules:

- **Never resolve a human's thread.** The reviewer resolves it. You may resolve a
  thread only if **every** comment in it has `author.__typename == "Bot"` and
  you've fixed or answered it:

  ```bash
  gh api graphql -f id=THREAD_ID -f query='
  mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id isResolved } } }'
  ```
- **Never paste raw CI logs into PR replies** (no `--log-failed` output). Summarize
  the failure in your own words, and redact anything token-like (keys, tokens,
  passwords, bearer headers, long random strings, signed URLs).
- When unsure between Fix and Escalate, escalate.
- **Mixed comments** (one comment with parts in different buckets): split it.
  Handle each part in its own bucket: fix the fixable part (e.g. a typo), reply
  to the reply-only part, and escalate the escalation part. Never touch code
  related to the escalated part. The comment counts as handled only when every
  part is. If parts can't be separated cleanly, the strictest bucket wins for
  the whole comment (Escalate > Reply > Fix).

## Fix rounds

One round = one push. **At most 3 rounds.**

1. Group fix items by the files they touch. Items that share a file go in the
   same group, so groups never overlap.
2. **Parallel:** if your environment can run subagents, give each group its own
   subagent with the item(s), the file list, and the instruction to edit only
   those files and not commit. **Sequential fallback:** without subagents, fix the
   groups one after another yourself.
3. **Single commit point:** after all groups finish, review the combined diff,
   run the local tests, lint and type checks. Fix or drop items until they pass.
   A dropped item is never silent: reply on its comment explaining why, or add
   it to the escalation list.
   **Optional comment pass:** if the `remove-comments` skill is installed and
   the round's diff adds comments, run it on the PR branch scoped to the lines
   the PR added (its PR scope, no new checkout) after the diff review and
   before the commit. Keep its report out of PR replies. Skip it otherwise.
4. Commit once and push once (`git push`, never `--force`). `fix_rounds += 1`.
5. Reply on each handled comment with the commit link.

### Updating the branch from base

If the branch must be brought up to date with base (see merge.md), use
`gh pr update-branch <pr>` (a merge from base; **never** `--rebase`; check
`gh pr update-branch --help` for the flags your version supports). It creates a
new head SHA, so it **counts as that round's push** (`fix_rounds += 1`, no
separate fix push in the same round), and it's **not allowed once any
escalation is open**.

### Limits (escalate and stop pushing when hit)

- A 4th round would be needed.
- The **same check fails on two consecutive head SHAs** (per the failure history).
- Local tests can't be made to pass for the round.

After escalating, keep polling and replying if useful, but don't push or update
the branch.

## Logging and the final report

Keep a running log (in your notes or a scratch file, not in the PR) of every
poll that had an event, each triage decision, each push and each rerun.

The final report states: PR URL, final state and head SHA, why you stopped,
checks status, review status and unresolved thread count, fix rounds used with
commit links, replies posted, reruns, and **every open escalation with a link and
the decision needed**. If branch protection couldn't be read, say so and that
all checks were treated as required. Without `--merge`, end with whether it is ready to merge.
