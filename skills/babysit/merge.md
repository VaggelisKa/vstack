# babysit merge mode: merge gates

Read this only when the request explicitly asked to merge (see SKILL.md). Otherwise, never merge,
and read only gate 2: SKILL.md's done condition uses its approval rule.

Evaluate every gate against the **current head SHA** (`headRefOid`). Re-fetch
state right before deciding; if the SHA changed, start over.

```bash
gh pr view <pr> --json author,baseRefName,headRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,reviews,labels,statusCheckRollup,autoMergeRequest
```

## Gates (all must pass)

1. **Required checks are green** on the head SHA. Find which are required from
   branch protection (`gh api repos/OWNER/REPO/branches/BASE/protection`, or
   `gh pr checks <pr> --required`). If protection can't be read, **fail closed**:
   treat every check as required, and say so in the final report.
2. **Approvals, when required, are on the head SHA.** An approval is needed
   only when the repo requires one or `reviewDecision` is `APPROVED`. The repo
   requires one when `reviewDecision` is `REVIEW_REQUIRED`, or a ruleset on the
   base sets `required_approving_review_count` above 0: read
   `gh api --paginate --slurp repos/OWNER/REPO/rules/branches/BASE` and take
   the highest `parameters.required_approving_review_count` among its
   `pull_request` rules (a 404 means no rulesets).
   - When needed: that many approvals, and at least one. Each must be a
     reviewer's latest verdict (approve, dismiss or request changes), be
     `APPROVED`, have `commit.oid == headRefOid`, and come from someone other
     than the PR author and not a bot (`author.__typename == "Bot"`).
     Approvals on any older commit are stale and don't count, so every push
     needs re-approval.
   - With no review requirement, this gate passes without an approval and
     branch protection decides (gate 5, and GitHub when it merges).
   - If the rulesets can't be read (any error but a 404), the requirement is
     unknown: the done condition treats an approval as needed, and in merge
     mode this gate fails. Say so in the final report.
3. **No changes-requested reviews** outstanding (latest review per reviewer),
   and **zero unresolved review threads**, with every page fetched (see SKILL.md;
   an unfetched page fails this gate).
4. **Not a draft.**
5. **Mergeable:** `mergeable` is `MERGEABLE`, and if protection requires branches
   to be up to date, the branch is not behind base. If behind and no escalation
   is open, run `gh pr update-branch <pr>` (merge, never `--rebase`); that counts
   as a round's push (see SKILL.md) and creates a new SHA, so go back to the loop.
   Conflicts → escalate.
6. **No hold label.** Case-insensitive exact match against: `hold`, `on-hold`,
   `do-not-merge`, `do not merge`, `DNM`, `wip`.
7. **No open escalations** from the loop.
8. **Settled, nothing claimed:** the last `coordination.py poll` was settled
   with `claimed_count` 0 (SKILL.md, Coordination).

## Arming

```bash
gh pr merge <pr> --auto --match-head-commit <headRefOid>
```

- `--match-head-commit` pins the merge to the SHA you checked; if the head moves,
  GitHub refuses it.
- Use the repo's default merge method. Don't pass `--squash`/`--merge`/`--rebase`
  unless the repo allows only one method and `gh` asks for it; then use that one.
- **Never** use `--admin`, bypass branch protection, or force-push.

## After arming: keep polling

Auto-merge stays armed until GitHub merges, so keep running the loop at the
normal cadence. Disarm and go back to the loop if any of these appears:

- a new head SHA,
- a hold label,
- a new changes-requested review,
- an unresolved thread,
- a new escalation,
- a claimed item.

```bash
gh pr merge <pr> --disable-auto
```

Re-arm only after every gate passes again on the new head SHA. Stop when the PR
is merged (or another stop condition in SKILL.md hits; disarm first if it's
still armed and not merged).

## Log

Record each decision (arm, disarm, merged, or blocked): the head SHA, each
gate's result, the reason, and a short summary (fix rounds, replies, reruns).
Put it in the final report.
