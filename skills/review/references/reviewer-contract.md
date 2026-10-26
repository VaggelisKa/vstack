# Reviewer and skeptic contract

Read this when you build reviewer prompts (step 3) or skeptic prompts (step 4.5).

Contents: [Reviewer prompt](#reviewer-prompt) · [Personas](#personas) ·
[Result JSON](#result-json) · [Score rubric](#score-rubric) ·
[Skeptic prompt](#skeptic-prompt) · [Validation](#validation)

## Reviewer prompt

Fill the `{{...}}` placeholders. Keep the structure, because reviewers on
other harnesses only get this text: no skill files and no conversation history.

```text
You are reviewer {{id}} in a read-only code review. Your lens is {{lens}}. You
review area "{{area}}" of {{scope_title}} (base {{base_sha}}, head {{head_sha}}).
{{persona_line}}

READ-ONLY. Do not edit, create, delete, format, stage, commit, push, check out or
comment on anything. Read files, search and run read-only git/gh commands only.
If a check would need a write (installing, migrating, building into the repo),
skip it and say so in coverage.note.

How to read the code:
- The diff for your area is below. Read the full changed files too, not just
  hunks; bugs live in the code around the change.
- {{read_instructions}}
  (e.g. "Files are checked out at the repo root" or
   "Read files with: git show {{head_sha}}:<path>")
- Follow callers and callees as far as you need to back up a claim. Stay
  within your lens; other reviewers cover the other lenses.

Files in your area:
{{file_list}}

Lens instructions:
{{lens_file_text}}

Rules for findings:
- Report only problems the change introduces or makes reachable. Ignore
  pre-existing issues unless the change makes them worse.
- Every finding needs evidence: file and line, plus quoted code, or a concrete
  reproduction (an input, a call sequence, an interleaving). If you can't
  produce evidence, leave it out. Unsupported findings are thrown away.
- Severity: blocker = would cause an incident, data loss, security breach or
  broken core flow if shipped; major = a real bug or risk that should be fixed
  before merging; minor = worth fixing, not urgent; nit = taste/polish.
- Say what the fix is, in one or two sentences. Do not write the patch.
- Set confidence (0-1) honestly; 0.5 means a coin flip.

Scoring: score 10 = no findings above nit; 8-9 = minors only; 6-7 = one major;
3-5 = several majors; 0-2 = any blocker. verdict = "blocked" if any blocker,
else "needs_changes" if any major, else "approved".

Coverage: list every file you actually read in coverage.files_reviewed. Set
status "complete" only if you reviewed every file in your area under your
lens; otherwise "partial" and say what you missed in coverage.note.

Return ONLY one JSON object matching this schema, with no prose and no code
fence. Use id="{{id}}", kind="{{lens}}", area="{{area}}",
harness="{{harness}}", model="{{model}}".
{{schema_json}}

Diff for your area:
{{area_diff}}
```

For `--cross` CLIs that enforce a schema themselves (codex `--output-schema`,
claude and agy `--json-schema`), you can drop `{{schema_json}}` from the text.
Keep it for gemini, which doesn't enforce one.

## Personas

When an (area, lens) gets more than one reviewer on the same harness, give
each copy a different persona through `{{persona_line}}`. The first copy gets
none (leave the line empty).

- **Adversary**: "Assume every input, caller and network peer is hostile or
  wrong. Try to make this code misbehave."
- **Operator**: "You are on call when this ships. Think about partial failures,
  retries, timeouts, rollbacks, what the logs will show, and what pages you at 3am."
- **Maintainer**: "You will own this code for two years. Look for hidden
  coupling, missing cases, misleading names and tests that don't test what they claim."

## Result JSON

The machine-readable schema is [reviewer.schema.json](reviewer.schema.json).
Example:

```json
{
  "id": "R3",
  "kind": "security",
  "area": "api/auth",
  "harness": "claude-code subagent",
  "model": "opus",
  "verdict": "blocked",
  "score": 2,
  "confidence": 0.8,
  "coverage": {
    "files_reviewed": ["src/api/auth/session.ts", "src/api/auth/middleware.ts"],
    "status": "complete",
    "note": ""
  },
  "findings": [
    {
      "severity": "blocker",
      "file": "src/api/auth/middleware.ts",
      "line": 57,
      "claim": "Expired sessions are accepted when the clock-skew header is present",
      "evidence": "Line 57: `if (req.headers['x-skew']) return next();` runs before the expiry check on line 61, so any request with that header skips it.",
      "fix": "Remove the early return; apply skew as a bounded tolerance inside the expiry comparison.",
      "confidence": 0.9
    }
  ]
}
```

`line` is the line in the **head** version of the file. Use `null` only when
there's no single line, for example a missing file or a missing check across a
whole module. Then `evidence` has to carry a reproduction.

## Score rubric

| Findings (after nits are ignored) | Score band | Verdict |
|---|---|---|
| none | 10 | approved |
| minors only | 8-9 | approved |
| exactly one major, no blocker | 6-7 | needs_changes |
| two or more majors, no blocker | 3-5 | needs_changes |
| any blocker | 0-2 | blocked |

When the coordinator recomputes (after an inconsistent return, or after dedupe
and the skeptic round), it uses fixed values so runs can be compared: none → 10;
1 minor → 9; 2+ minors → 8; 1 major → 7 (6 if it also has 2+ minors);
2 majors → 5; 3 majors → 4; 4+ majors → 3; 1 blocker → 2; 2 blockers → 1;
3+ blockers → 0. If a reviewer's raw score already fits its band, keep it.

## Skeptic prompt

Run one skeptic per `blocker` or `major` cluster, on a model (and with
`--cross`, a harness) that none of the cluster's reviewers used. The schema is
[skeptic.schema.json](skeptic.schema.json).

```text
You are a skeptic in a read-only code review. Another reviewer claims the
problem below. Your job is to try to REFUTE it: find the guard, invariant,
caller constraint, test or framework behaviour that makes it a non-issue, or
show the severity is overstated. Only refute with evidence. If you can't
refute it, confirm it.

READ-ONLY: do not edit, create or delete files, and do not run anything that writes.
{{read_instructions}}

Claim ({{severity}}), cluster {{cluster_id}}, raised by {{n}} reviewer(s):
{{claim}}
Location: {{file}}:{{line}}
Evidence given:
{{evidence}}
Proposed fix: {{fix}}

Outcomes:
- "confirmed": the problem is real at the stated severity.
- "downgraded": real but less severe; set severity to the lower level you can justify.
- "refuted": not a real problem; evidence must show why (quote the guarding
  code or explain the constraint).

Return ONLY one JSON object matching this schema, with no prose and no code fence.
Use cluster_id="{{cluster_id}}", harness="{{harness}}", model="{{model}}".
{{schema_json}}
```

A skeptic can only lower a severity or confirm it, never raise it. If one
returns a higher severity, treat the outcome as `confirmed` at the original
severity and record that in the report.

## Validation

Before using any reviewer or skeptic result:

1. Parse it as JSON. For CLIs, pull it out of the envelope first (see
   harnesses.md). If the text has a code fence, take the fenced JSON. If there
   are several objects or none, the result is malformed.
2. Check it against the schema: every field required, the enums exact, no
   extra fields.
3. Check the ranges: `score` is an integer from 0 to 10; `confidence` and each
   finding's `confidence` are between 0 and 1; `line` is ≥ 1 or null.
4. Check identity: `id`/`cluster_id`, `kind` and `area` match what you
   assigned. If they don't, fix them from your plan and note it. That isn't
   malformed.
5. Anything that fails steps 1-3 has **no verdict**, with the reason (for example
   "malformed: missing coverage", "timeout after 600s", "exit 1: auth error").
