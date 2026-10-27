# Result file (schema v1)

Read this when you write the result file at the end of a run.
`scripts/write-result.py` checks the JSON and writes it to
`.vstack/<pr>/babysit.json` at the repo root, where other skills read it.

```json
{
  "schema_version": 1,
  "skill": "babysit",
  "pr": 42,
  "head_sha": "9f2c1e4b7a0d3c5e8f1a2b4c6d8e0f1a3b5c7d9e",
  "written_at": "2026-10-27T14:03:00Z",
  "status": "blocked",
  "ci": {"state": "failing", "failing_checks": ["test (ubuntu-latest)"]},
  "threads": [
    {"id": "PRRT_kwDOA1", "author": "alice", "is_bot": false, "state": "open",
     "decision": "escalated", "reply_url": "https://github.com/o/r/pull/42#discussion_r101"}
  ],
  "last_action": "Escalated: test (ubuntu-latest) failed on two consecutive head SHAs",
  "mergeable": false
}
```

The script fills in `schema_version`, `skill`, `pr` (from `--pr`), `head_sha`
(from `--head-sha`, else `git rev-parse HEAD`) and `written_at` (now) when
they're missing.

| Field | Value |
|---|---|
| `head_sha` | The PR's `headRefOid` when you stopped, 7-40 lowercase hex |
| `written_at` | ISO 8601 UTC, ending `Z` or `+00:00` |
| `status` | Why you stopped (first match): `merged`; `green` (the done condition held); `blocked` (an escalation or limit stopped progress); `failing` (stopped on time with checks failing); `pending` (stopped on time, waiting on checks or reviewers); `stopped` (closed without merging, or anything else) |
| `ci.state` | `passing`, `pending` or `failing` on the head SHA, or `none` if it has no checks |
| `ci.failing_checks` | Names of the failing checks; non-empty exactly when `state` is `failing` |
| `threads` | Every review thread on the PR |
| `threads[].id` | The GraphQL thread id |
| `threads[].author` | Login of the thread's first comment (`ghost` for a deleted account) |
| `threads[].is_bot` | `author.__typename == "Bot"` |
| `threads[].state` | `open` or `resolved` |
| `threads[].decision` | `fixed`, `rejected` (replied without changing code), `escalated`, `pending` (not handled yet), or `null` if you never triaged it |
| `threads[].reply_url` | URL of your reply, or `null` |
| `last_action` | One line: the last thing you did, or why you stopped |
| `mergeable` | `true` or `false` from `gh pr view --json mergeable`, `null` if `UNKNOWN` |

The script rejects unknown fields, wrong types, `green` with checks that
aren't `passing` or `none` or with an open thread, and `failing` without
failing checks. Exit 2 lists every problem on stderr and writes nothing; exit
4 is an internal error.
