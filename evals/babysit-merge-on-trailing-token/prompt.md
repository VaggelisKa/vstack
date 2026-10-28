---
name: babysit-merge-on-trailing-token
description: A trailing `merge` token turns merge mode on. From skills/babysit/evals/evals.json id 15.
expected_outcome: "The trailing `merge` turns merge mode on. Merges once every gate passes."
tags: [babysit, merge-gate]
runs: 1
max_turns: 10
---
/vstack:babysit 14 merge. Every gate passes on the head SHA.
