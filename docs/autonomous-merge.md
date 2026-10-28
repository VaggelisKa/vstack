# autonomous-merge: what's tested

`/vstack:autonomous-merge <pr> [merge]` (`$autonomous-merge` in Codex), or in plain words ("... and merge when green"). It merges only when that run's request explicitly asks to.

- 93 script tests, with fake `gh` and `curl` on a `PATH` holding only git, in temp repos:
  `python3 -m unittest discover -s skills/autonomous-merge/scripts/tests`.
- `preflight.py`: GitHub and Bitbucket ok paths, host from URL or remote, one test per handback
  (`no_cli`, `auth_missing`, `auth_expired`, `insufficient_permission`, `not_open`, `host_unsupported`,
  `unknown`), no token in output or argv, `-h` exits 2, and exit 4 prints an `unknown` handback first.
- `gate.py`: ready, one test per block, a human's standing CHANGES_REQUESTED, approvals that must be on the
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
  babysit's marked commits counted as fix rounds.

> Not yet verified: a live run on a real GitHub or Bitbucket PR (responses are modelled on the
> docs and `gh` 2.103 `--help`), `gh pr merge --auto --match-head-commit` and `--disable-auto` against a
> real auto-merge, the rulesets endpoint on real data, Bitbucket comment `resolution` on real data, and the
> model following SKILL.md in any harness (evals not run).
