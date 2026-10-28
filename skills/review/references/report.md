# Report

Read this when you write the report (SKILL.md, step 6). It goes to `--out`, or
by default to `vstack/reports/review-<scope>-<YYYYMMDD-HHMM>.md` in the git
common dir. Use this structure:

```markdown
# Review: <scope title> (<scope slug>)
<date/time> · base <sha> → head <sha> · mode: <parallel subagents | cross: … | sequential personas>

## Decision: <Ship | Ship with fixes | Rethink | Incomplete>[, based on <n> [confirmed and <m>] unverified <blockers|majors>]
<one or two sentences on why; Incomplete names the K=3 area and the gap>
Score: <weighted mean>/10 (min <x> from <reviewer>), advisory.

## Findings
<ranked: severity (blocker > major > minor > nit), then number of agreeing reviewers, then confidence>
### 1. [blocker · confirmed · 3 reviewers] <claim>
`path/file.ts:42`
> <quoted code>
Evidence: … · Fix: … · Skeptic (<model>): …

## Plan
<the plan table, with trimmed and skipped reviewers and why>

## Reviewers
| ID | Lens | Area | Harness | Model | Verdict | Score (raw→final) | Conf | Coverage | B/M/m/n | Notes |
<Notes: "retried" when the first try failed, "no verdict: <reason>", "recomputed: <why>">

## Failed and partial reviewers
<every reviewer or skeptic that wasn't `ok` on the first try, or "none"; a skeptic's lens is `skeptic`, its area the cluster>
| ID | Lens | Area | What happened (check-result reasons) | Retry | Consequence |
<Retry: recovered / still failed / not retried (partial). Consequence: area UNREVIEWED / cluster unverified / coverage partial / none>

## Refuted and dropped
<refuted clusters with the skeptic's reason; count of findings dropped for lack of evidence>

## Not reviewed
<UNREVIEWED areas' files, files a `partial` reviewer left out (`missing`), budget-trimmed areas, skipped ui-live, files excluded from --cross, hunks held back by the secret scan (file + @@ header only), unreviewed lower-priority changes, timeouts>
```

If the `git status` check at the end found a change in the tree (other than an
`--out` path inside it), put it at the top, above the decision.

If the diff adds agent-tell comments ("// Added X", narration, banners), the
report's footer may name `remove-comments` as a follow-up; review itself never edits.

Screenshots go away with the session folder, so each ui-live finding says
what its screenshot showed.

Never quote a secret's value anywhere in the report; redact it.

## Plan table

Print this in the chat before starting any reviewer (SKILL.md, step 2), and
repeat it under "Plan" in the report:

```
| Area | Files | +/- | C | K | Flags | Reviewers (lens × n, tier) |
|------|-------|-----|---|---|-------|----------------------------|
| api/auth | 3 | +120/-40 | 2 | 3 | tokens, input | security×3 strong, bug-hunt×3 strong |
| docs | 2 | +15/-3 | 0 | 0 | - | bug-hunt×1 cheap |
Total reviewers: 7 / budget 8. Skeptics: est. 1-2. Agent runs: est. 8-9, plus one retry per failed result. Mode: parallel subagents.
```
