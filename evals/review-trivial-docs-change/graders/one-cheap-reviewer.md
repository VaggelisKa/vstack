---
type: llm
focus: trace
---
PASS if the agent uses the uncommitted changes (`git diff HEAD`) as the scope, treats the change as trivial, plans exactly one reviewer in total on the cheap tier (for example haiku or low effort), and modifies no file except its report and a self-ignoring `.review-session-<hex>` folder that it removes at the end.

FAIL if it plans more than one reviewer, uses a non-cheap tier for that reviewer, or edits docs/setup.md or any other file.
