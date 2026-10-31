# autonomous-merge: what's tested

`/vstack:autonomous-merge <pr> [no-merge]` (`$autonomous-merge` in Codex). The user invokes it, or the model picks it
up from an explicit ask ("take PR 14 all the way", "auto-merge this PR"). Merging is its job: it merges through the
gate without asking for confirmation, and stops at ready only when the request opts out (`no-merge`, "don't merge",
or a condition the gate can't check).
Scripts and sibling skills are found through `${CLAUDE_SKILL_DIR}`, the folder holding SKILL.md (other harnesses
use its absolute path).

- 116 script tests, with fake `gh` and `curl` on a `PATH` holding only git, in temp repos:
  `python3 -m unittest discover -s skills/autonomous-merge/scripts/tests`.
- `preflight.py`: GitHub and Bitbucket ok paths, host from URL or remote, one test per handback
  (`no_cli`, `auth_missing`, `auth_expired`, `insufficient_permission`, `not_open`, `host_unsupported`,
  `unknown`), a fork PR (`isCrossRepository` true, missing or not a boolean) handed back `escalated` before any of
  its code runs unless `--allow-fork` is passed, no token in output or argv, `-h` exits 2, and exit 4 prints an
  `unknown` handback first.
- `gate.py`: ready, one test per block, a hold label (babysit's merge.md set, case-insensitive exact match;
  unreadable labels count as one) blocking before arming and disabling a pending auto-merge, every check treated as required when none are configured or the required
  ones can't be read (each with its own wording), a human's standing CHANGES_REQUESTED, approvals that must be on the
  head and not the author's (and only when required), unknown mergeability re-polled (recovers, or stays
  `unknown`), the exact `gh pr merge` arguments (`--auto`, method, `--match-head-commit`) only after every
  check passes, then polling until merged with `--disable-auto` on a head or base change, a new thread,
  changes requested, a failing check, a timeout or an unreadable PR; never `--admin`, a push or a review
  dismissal; before arming, babysit's coordination must be settled with nothing claimed (`not_settled`,
  `claimed_items`, `conflicting_fixer`, `stale_claim`). Bitbucket: every block, no build status handed back, credentials with line breaks refused,
  and never a merge call, with or without `--merge` (`merge_unsupported_host`).
- `state.py`: resume, fingerprints, the three caps from constants only, both oscillation cases,
  `no_progress`, `security_blocker`, `escalate` for each immediate-handback code, a file with other caps, a
  future `started_at` or a mismatched `fix_rounds` refused, atomic and symlink-safe writes, and only
  babysit's marked commits counted as fix rounds. A fresh review of the same head and base supersedes a
  round that isn't clean and has nothing to fix (in a real run, an Incomplete from one partial reviewer
  dead-ended there), counting toward the cap and still checked for oscillation and `no_progress`; a stale
  review.json or a round with open findings just repeats.
- SKILL.md only, not script-tested: that `--allow-fork` is passed only when the user's own words in the request
  allow running the fork's code; findings, comments and CI logs go
  through review's `wrap-untrusted.py` before triage, and an escape count above 0 hands back `escalated`; fixes
  are staged by path, never `git add .` or `-A`.

> Not yet verified: a live run on a real GitHub or Bitbucket PR (responses are modelled on the
> docs and `gh` 2.103 `--help`), `gh pr merge --auto --match-head-commit` and `--disable-auto` against a
> real auto-merge, the rulesets endpoint on real data, and Bitbucket comment `resolution` on real data. The model
> following SKILL.md is checked only by the eval smoke run in the README (2 autonomous-merge cases, 1 run per arm,
> Claude Code, 2026-10-29), not across harnesses or repeated runs.
