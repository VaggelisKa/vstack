# babysit --merge: merge gates

Read this only when `babysit` was invoked with `--merge`. Without it, never merge.

Evaluate every gate against the **current head SHA**. Re-fetch state right
before deciding; if the SHA changed, start over.

```bash
gh pr view <pr> --json headRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,reviews,labels,statusCheckRollup,commits
```

## Gates (all must pass)

1. **Required checks are green** on the head SHA. Find which are required from
   branch protection (`gh api repos/OWNER/REPO/branches/BASE/protection`, or
   `gh pr checks <pr> --required`). If protection can't be read, require all checks.
2. **Approvals are fresh:** at least one approval (or what protection requires)
   submitted **on or after the last commit**. Compare each review's
   `submittedAt` with the last commit's `committedDate`. Any push by you means
   reviewers must re-approve; older approvals don't count.
3. **No changes-requested reviews** outstanding (latest review per reviewer),
   and **zero unresolved review threads** (GraphQL `isResolved`, see SKILL.md).
4. **Not a draft.**
5. **Mergeable:** `mergeable` is `MERGEABLE`, and if protection requires branches
   to be up to date, the branch is not behind base. If behind, update it with a
   normal merge from base (`gh pr update-branch <pr>`) only if that's a plain
   update; that's a new SHA, so re-run checks and gates. Conflicts → escalate.
6. **No hold label:** no `hold`, `on-hold`, `do-not-merge`, `do not merge`,
   `DNM`, `wip` or similar label.
7. **No open escalations** from the loop.

## Merging

```bash
gh pr merge <pr> --auto
```

- Use the repo's default merge method. Don't pass `--squash`/`--merge`/`--rebase`
  unless the repo allows only one method and `gh` asks for it; then use that one.
- **Never** use `--admin`, bypass branch protection, or force-push.
- If any gate fails, don't merge. Go back to the loop (if the gate can still
  clear) or stop and report what's blocking.

## Log

Record the decision: merge or not, the head SHA, each gate's result, and a
short summary (fix rounds, replies, reruns). Put it in the final report.
