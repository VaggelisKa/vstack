---
name: babysit
description: Watch a GitHub pull request until it is done - poll CI checks, reviews and review threads, triage every comment, fix what is in scope, reply to the rest, escalate what needs a human, and (only with --merge) merge it once every gate passes. Use this skill whenever the user says "babysit <pr>", "babysit <pr> --merge", or asks to watch, shepherd, monitor, land, or get a PR green, keep an eye on CI, handle review feedback on a PR, or "take this PR to merge", even if they don't say "babysit".
---

# babysit

Shepherd one pull request from "open" to "green and approved" (or merged) with
plain `gh` and `git`, without a human driving each step.

```
babysit <pr>            # watch, fix, reply, report. Never merges.
babysit <pr> --merge    # same loop, then merge when every gate in merge.md passes
```

`<pr>` is a number, URL or branch accepted by `gh pr view`. **Default is no
merge.** Read [merge.md](merge.md) only when `--merge` was given.

## Setup

1. Resolve the PR and repo: `gh pr view <pr> --json number,url,headRefName,baseRefName,headRefOid,state,isDraft`.
2. Check out the head branch locally (`gh pr checkout <pr>`). Make sure you can
   push to it; if not, run in reply-and-escalate mode only and say so.
3. Find how to run the repo's tests, lint and type checks locally (README,
   CONTRIBUTING, package scripts, Makefile, CI workflow files). You need these
   before every push.
4. Record the start time, `fix_rounds = 0`, an empty escalation list, and an
   empty set of already-handled comment/thread IDs.

## Polling

Each poll, gather state for the current head SHA:

```bash
gh pr view <pr> --json state,isDraft,headRefOid,mergeable,mergeStateStatus,reviewDecision,reviews,comments,labels,statusCheckRollup
```

`gh pr view` does not expose review-thread resolution, so fetch threads with GraphQL:

```bash
gh api graphql -f owner=OWNER -f repo=REPO -F pr=NUMBER -f query='
query($owner:String!,$repo:String!,$pr:Int!){
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100){ nodes{
      id isResolved isOutdated path line
      comments(first:50){ nodes{ id databaseId author{login} body createdAt url } }
    } } } } }'
```

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
2. It is **done**: all checks green on the head SHA, approved, and zero
   unresolved threads. Without `--merge`, report. With `--merge`, go to merge.md.
3. **24 h** of wall time since start.
4. **6 h idle waiting on humans** (no event, nothing for you to do).
5. An escalation that blocks all further progress (see limits below).

## Each poll: what to do

1. **Checks.** For each failing check on the head SHA, read its logs
   (`gh run view <run-id> --log-failed`, or the check's details URL).
   - Failure caused by the PR → a **fix** item.
   - Looks flaky or infra (timeouts, runner loss, network, rate limits, unrelated
     to the diff): rerun it **once** (`gh run rerun <run-id> --failed`). If it
     fails again, check whether the same check fails on the base branch
     (`gh run list --branch <base> --workflow <wf>`); if it does, **escalate**
     as a pre-existing failure. Don't fix the base branch from this PR.
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

- **Never resolve a human's thread.** The reviewer resolves it. You may resolve
  threads opened by bots once fixed, if the repo's practice allows it.
- When unsure between Fix and Escalate, escalate.
- If two bucket rules apply, the stricter wins (Escalate > Reply > Fix).

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
4. Commit once and push once (`git push`, never `--force`). `fix_rounds += 1`.
5. Reply on each handled comment with the commit link.

Limits (escalate and stop fixing when hit):

- A 4th round would be needed.
- The **same check fails twice after a fix** aimed at it.
- Local tests can't be made to pass for the round.

After escalating, keep polling and replying if useful, but don't push.

## Logging and the final report

Keep a running log (in your notes or a scratch file, not in the PR) of every
poll that had an event, each triage decision, each push and each rerun.

The final report states: PR URL, final state and head SHA, why you stopped,
checks status, review status and unresolved thread count, fix rounds used with
commit links, replies posted, reruns, and **every open escalation with a link and
the decision needed**. Without `--merge`, end with whether it is ready to merge.
