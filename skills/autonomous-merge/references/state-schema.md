# State file (schema v1)

`scripts/state.py` owns `vstack/<pr>/autonomous-merge.json` in
`git rev-parse --git-common-dir`, next to review's and babysit's result files:
outside the tree, shared by every worktree. Each write goes to a temp file in
the same folder and is renamed over the old one; a symlinked `vstack`, `<pr>`
folder or state file is refused. On load the file is checked (keys, types,
`caps` equal to state.py's constants, `started_at` not in the future,
`counters.review_rounds` equal to the number of rounds, `counters.fix_rounds`
equal to the fixes it lists); a file that fails is refused with exit 2, for
the user to fix or `state.py reset --pr N`. The caps are always state.py's
constants, never the file's. It's a cache for resuming, not an attestation:
the gate never reads it.

```json
{
  "schema_version": 1, "skill": "autonomous-merge", "pr": 42, "merge": false,
  "started_at": "2026-10-27T14:00:00Z", "updated_at": "2026-10-27T14:31:09Z",
  "caps": {"review_rounds": 3, "fix_rounds": 6, "minutes": 120},
  "counters": {"review_rounds": 2, "fix_rounds": 1},
  "last_head": "<sha>",
  "rounds": [{"round": 1, "head": "<sha>", "base": "<sha>", "status": "fix",
              "verdict": "Ship with fixes", "counts": {"blocker": 0, "major": 1, "minor": 1, "nit": 0},
              "merge_ok": false, "at": "…"}],
  "findings": [{"fingerprint": "3f9a0c1d2e4b5a67", "file": "src/a.py", "line": 3, "rule": "bug-hunt",
                "summary": "Off by one in loop", "severity": "major", "first_round": 1, "last_round": 1,
                "status": "fixed", "commit": "<sha>", "reason": null}],
  "fixes": [{"round": 1, "source": "review", "head": "<sha>", "commits": ["<sha>"], "at": "…"}],
  "threads": [{"id": "PRRT_1", "author": "alice", "is_bot": false, "state": "open",
               "decision": "rejected", "reply_url": "https://github.com/o/r/pull/42#discussion_r1"}],
  "ci": [{"head": "<sha>", "state": "passing", "failing_checks": [], "babysit_status": "green", "at": "…"}],
  "stop": null
}
```

| Field | Value |
|---|---|
| `merge` | Whether this run's request explicitly asked to merge (`init --merge`); set again by every `init` |
| `started_at` | Start of the 2-hour window; `init --continue` restarts it |
| `counters.review_rounds` | Review rounds recorded; a 4th is refused |
| `caps` | Always `{"review_rounds": 3, "fix_rounds": 6, "minutes": 120}`; any other value fails the checks |
| `counters.fix_rounds` | Review fix rounds (one per `fix-round`) plus babysit's commits (first-parent, so `gh pr update-branch` counts once); must equal that sum over `fixes`; over 6 stops the run |
| `last_head` | The head last recorded; the next fix or babysit record counts commits from here |
| `rounds[]` | One per reviewed head and base: review's `status`, `verdict`, severity counts and `merge_ok` |
| `findings[].fingerprint` | First 16 hex of SHA-256 over file, lower-cased rule, and the summary lower-cased with digit runs as `#` and punctuation and spacing collapsed |
| `findings[].status` | `open` (blocker or major, not handled), `fixed` (with `commit`), `rejected` (with `reason`), `deferred` (minors and nits, automatically) |
| `fixes[]` | Each recorded push with its commits; `source` is `review` or `babysit` |
| `threads[]` | Copied from babysit's result at each `record-babysit` |
| `ci[]` | One entry per `record-babysit` |
| `stop` | `null`, or `{"outcome": "merged" \| "ready" \| "handback", "reason", "detail", "at"}` |

**Oscillation.** `record-review` stops the run when a finding whose status is
`fixed` shows up again in a later round. `record-review`, `fix-round` and
`record-babysit` stop it when a new commit sets a file that a recorded fix
changed back to that file's content before the fix (a revert, whole or of that
file).

**No progress.** `record-review` stops the run (`no_progress`) when the
previous round had blockers or majors and this one, not clean, has at least as
many. It also stops at once (`security_blocker`) on a blocker whose rule is
`security` or `data-integrity`.

**Resume.** A new session runs `init` and continues from `counters`,
`last_head` and the open findings. After a `ready` stop, `init` clears it (say,
to rerun asking for a merge); after a handback, only `init --continue` does, and it
keeps the counters and fingerprints. After `merged`, nothing resumes.
With no state file, `init` refuses (`escalated`) when `review.json` or
`babysit.json` already exists for the PR, unless `--fresh`.
`init --continue`, `init --fresh` and `reset` are the user's commands; the
model never runs them.
