# Result file (schema v1)

Read this when you write the result file (step 7). `scripts/write-result.py`
checks the JSON and writes it to `.vstack/<pr>/review.json` at the repo root,
where other skills read it. Only PR scopes get one.

```json
{
  "schema_version": 1,
  "skill": "review",
  "pr": 42,
  "head_sha": "9f2c1e4b7a0d3c5e8f1a2b4c6d8e0f1a3b5c7d9e",
  "written_at": "2026-10-27T14:03:00Z",
  "status": "fix",
  "verdict": "Ship with fixes, based on 1 confirmed and 1 unverified major",
  "score": 6.4,
  "findings": [
    {"id": "C1", "severity": "major", "file": "src/auth.py", "line": 42,
     "rule": "security", "summary": "Token compared with ==, not in constant time",
     "verified": true}
  ],
  "unreviewed_areas": [],
  "partial": false
}
```

The script fills in `schema_version`, `skill`, `pr` (from `--pr`), `head_sha`
(from `--head-sha`, else `git rev-parse HEAD`, which is wrong when the PR isn't
checked out) and `written_at` (now) when they're missing.

| Field | Value |
|---|---|
| `head_sha` | The head commit reviewed, 7-40 lowercase hex |
| `written_at` | ISO 8601 UTC, ending `Z` or `+00:00` |
| `status` | `ship` for Ship, `fix` for Ship with fixes or Rethink, `unreviewed` for Incomplete |
| `verdict` | The decision line as in the report: `Ship`, `Ship with fixes`, `Rethink` or `Incomplete`, optionally followed by `, based on …` |
| `score` | The overall weighted score (0-10), or `null` if no reviewer had a verdict |
| `findings` | Every standing cluster, ranked as in the report; refuted and dropped ones are left out |
| `findings[].id` | The cluster id |
| `findings[].severity` | `blocker`, `major`, `minor` or `nit`, after any downgrade |
| `findings[].line` | Integer ≥ 1, or `null` if the finding has no line |
| `findings[].rule` | The lens that raised it (the first one, if several did) |
| `findings[].verified` | `true` if a skeptic confirmed or downgraded it, or you confirmed a prompt-injection hit; `false` for `unverified` and `unchallenged` |
| `unreviewed_areas` | Areas that are UNREVIEWED or lost all reviewers to the budget, and `ui-live` if skipped |
| `partial` | `true` if any reviewer reported `partial` coverage or ended without a verdict |

The script rejects unknown fields, wrong types, a `status` that doesn't match
the `verdict`, and findings that contradict it: Rethink needs a blocker, Ship
with fixes a major and no blocker, Ship no blocker or major, Incomplete no
blocker. Exit 2 lists every problem on stderr and writes nothing; exit 4 is
an internal error.
