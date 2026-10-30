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
2. **Approvals, only as many as the repo requires, are on the head SHA.**
   Work out the base branch's review requirement from three sources:
   - `reviewDecision`: any value (`REVIEW_REQUIRED`, `APPROVED`,
     `CHANGES_REQUESTED`) means GitHub applies a review rule, so at least one
     approval. Empty means it applies none.
   - Classic branch protection: `gh api repos/OWNER/REPO/branches/BASE/protection`,
     then `.required_pull_request_reviews.required_approving_review_count`
     (absent means 0). A 404 whose message is `Branch not protected` means 0.
     Any other error (403; a plain `Not Found` 404, which is what a non-admin
     gets on a protected branch; anything else): read
     `gh api repos/OWNER/REPO/branches/BASE --jq .protected`, which needs no
     admin. `false` means 0; otherwise protection is **unreadable**.
   - Rulesets: `gh api --paginate --slurp repos/OWNER/REPO/rules/branches/BASE`,
     the highest `parameters.required_approving_review_count` among its
     `pull_request` rules (0 if none, and 0 on a 404). Any other error:
     **unreadable**.

   Then:
   - **Required** (any source above asks for one): the highest count, and at
     least one. Each approval must be a reviewer's latest verdict (approve,
     dismiss or request changes), be `APPROVED`, have
     `commit.oid == headRefOid`, and come from someone other than the PR
     author and not a bot (`author.__typename == "Bot"`). Approvals on any
     older commit are stale and don't count, so every push needs re-approval.
   - **None** (`reviewDecision` empty, protection says 0, rulesets say 0):
     this gate passes with no approval. That includes an owner's own PR on an
     unprotected repo, which nobody else may ever review.
   - **Unknown** (`reviewDecision` empty, nothing readable asks for one, but
     protection or rulesets are unreadable): fail closed, and treat it as
     required with a count of one. It can't be proven that no review is
     needed, so never merge unreviewed code on a guess; but don't block a
     reviewed PR either, since GitHub still enforces any higher count when it
     merges. Say in the final report which read failed and that one approval
     was required because of it.
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
gh pr merge <pr> --auto --<method> --match-head-commit <headRefOid>
```

- `--match-head-commit` pins the merge to the SHA you checked; if the head moves,
  GitHub refuses it.
- Always pass a method: `gh pr merge` won't run without a prompt otherwise.
  Read what the repo allows
  (`gh repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed`).
  If only one is allowed, use it. If several are, match how recent PRs were
  merged into BASE: for the last few `gh pr list --base BASE --state merged -L 5 --json mergeCommit`,
  fetch BASE and look at each merge commit (`git log -1 --format='%P%x09%s' <oid>`):
  two parents is `--merge`; one parent with a subject ending `(#N)` is
  `--squash`; anything else is `--rebase`. Use the method most of them used.
  With no history or a tie, use `--squash` if allowed, else `--merge` (as
  autonomous-merge's gate does). Method words in the request don't change this
  (SKILL.md). Log the method and why.
- **Never** use `--admin`, bypass branch protection, or force-push.

## After arming: keep polling

Auto-merge stays armed until GitHub merges, so keep running the loop.
Disarm and go back to the loop if any of these appears:

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
