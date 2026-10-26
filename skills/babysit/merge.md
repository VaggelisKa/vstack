# babysit --merge: merge gates

Read this only when `babysit` was invoked with `--merge`. Without it, never merge.

Evaluate every gate against the **current head SHA** (`headRefOid`). Re-fetch
state right before deciding; if the SHA changed, start over.

```bash
gh pr view <pr> --json headRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,reviews,labels,statusCheckRollup,autoMergeRequest
```

## Gates (all must pass)

1. **Required checks are green** on the head SHA. Find which are required from
   branch protection (`gh api repos/OWNER/REPO/branches/BASE/protection`, or
   `gh pr checks <pr> --required`). If protection can't be read, **fail closed**:
   treat every check as required, and say so in the final report.
2. **Approvals are on the head SHA:** at least one approval (or the number
   protection requires) whose `commit.oid == headRefOid`. Approvals on any older
   commit don't count, so every push needs re-approval.
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
- a new escalation.

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
