# autonomous-merge: what's tested

`/vstack:autonomous-merge <pr> [--merge]` (`$autonomous-merge` in Codex).

- 69 script tests, with fake `gh` and `curl` on a `PATH` holding only git, in temp repos:
  `python3 -m unittest discover -s skills/autonomous-merge/scripts/tests`.
- `preflight.py`: GitHub and Bitbucket ok paths, host from URL or remote, one test per handback
  (`no_cli`, `auth_missing`, `auth_expired`, `insufficient_permission`, `not_open`, `host_unsupported`,
  `unknown`), and no token in output or argv.
- `gate.py`: ready, one test per block, the exact `gh pr merge` arguments (`--auto`, method,
  `--match-head-commit`, never `--admin`), gh errors handed back, Bitbucket ready, merge and blocks.
- `state.py`: resume, fingerprints, the three caps, both oscillation cases, atomic and symlink-safe writes.

> Not yet verified: a live run on a real GitHub or Bitbucket PR (responses are modelled on the
> docs and `gh` 2.103 `--help`), Bitbucket comment `resolution` and `default_merge_strategy` on
> real data, and the model following SKILL.md in any harness (evals not run).
