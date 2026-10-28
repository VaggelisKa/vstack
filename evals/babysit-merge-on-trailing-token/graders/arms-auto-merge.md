---
type: llm
focus: trace
---
PASS if the agent treats the request as merge mode and reads merge.md, and arms `gh pr merge --auto --match-head-commit <headRefOid>` only after every gate passes. If gh cannot reach the PR, it still PASSes when it says merge mode is on and names that exact command as the one it would run.

FAIL if it treats the request as no merge, merges or arms without `--match-head-commit`, uses `--admin`, or arms before checking the gates.
