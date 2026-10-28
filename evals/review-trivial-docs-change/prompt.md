---
name: review-trivial-docs-change
description: A trivial docs-only change gets one cheap reviewer and no edits. From skills/review/evals/evals.json id 2.
expected_outcome: "Uses uncommitted changes as the scope, recognises a trivial docs-only change, runs exactly one cheap reviewer, and reports Ship (unless that reviewer finds a real issue)."
tags: [review, scope]
runs: 1
max_turns: 10
---
review my changes before I commit, just fixed a typo in docs/setup.md (3 lines). no PR open for this branch yet
