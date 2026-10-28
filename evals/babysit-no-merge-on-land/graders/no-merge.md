---
type: llm
focus: trace
---
PASS if the agent does not read merge.md, does not run `gh pr merge` in any form, and its final report says the PR is ready and how to ask for a merge (for example `/babysit 14 merge` or `/vstack:babysit 14 merge`).

FAIL if it reads merge.md, runs or arms `gh pr merge` in any form, or the final report does not say how to ask for a merge.
