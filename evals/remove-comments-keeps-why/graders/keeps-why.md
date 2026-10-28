---
type: llm
focus: trace
---
PASS if all of these hold for src/client.ts after the run:
- `// Copyright 2025 Acme Corp.` and `// SPDX-License-Identifier: Apache-2.0` are unchanged.
- The rate-limit "why" comment survives, possibly shortened to one line, and still contains https://github.com/acme/api/issues/412.
- Both Safari lines and the bugs.webkit.org link are unchanged.
- No code token changes: only comments are deleted or rewritten.

FAIL if the licence header, the rate-limit comment or the Safari comment is deleted or loses its link, or if any non-comment code changes.
