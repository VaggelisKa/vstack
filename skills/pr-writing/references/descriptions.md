# PR titles and descriptions

## Title

What the change does, in the imperative, specific enough to tell apart from
the next ten PRs: "Retry 5xx responses with exponential backoff", not "Retry
improvements" or "Update client.js". Under about 70 characters. Follow the
repo's convention when it has one: read `git log --oneline -20` on the base
and match a prefix style (`feat:`, `client:`, a ticket key) only if most
commits use it.

## Body

A reviewer reads the description to learn what the change does, why, and
where to look, before opening the diff. It's a summary of the work, not a
second copy of it: the diff holds every name, value and line, and the
description is the one place that says what they add up to. Write for a
teammate who knows the codebase but hasn't seen this branch.

1. **What and why, first.** One to three sentences: the change in behaviour,
   as a user or caller would see it, and the reason for it. The reason comes
   from the commits, the linked issue, the code or the user. Never invent one
   ("to improve reliability") when you don't know it; say what changed and
   leave the why to the author, or ask.
2. **What a reviewer needs.** Only what applies, a sentence or a bullet each:
   anything surprising in the approach and why it beat the obvious one,
   behaviour that deliberately didn't change, risk (a migration, a changed
   default, a config the deploy needs), how to try it when that isn't obvious,
   and what was left out on purpose.
3. **Links.** `Fixes #123` when the issue is known. Nothing invented.

**Stay at the level of behaviour, not implementation.** Say "retries failed
uploads a few times before giving up", not the function, its parameters,
their defaults and the backoff formula. Name an identifier only when the
reviewer needs it to find their way, and then only a couple. Leave out file
lists, step-by-step walkthroughs of the code, type signatures, config keys
and exact values (default counts, delays, timeouts, sizes), and anything
that reads like a spec. A number belongs only when it is the change itself
("raises the upload limit to 50 MB"). If a detail matters
that much (a new public API, a schema), link the doc or the file instead of
pasting it.

**Length follows the change.** A one-line fix gets one sentence. A typical
change gets a short paragraph and maybe three or four bullets. Only a large
or risky change earns headings, and then short ones. No `## Summary` /
`## Changes` / `## Test plan` scaffolding on a description that fits on a
screen, unless the repo's template asks for it.

### A sketch, when the shape is the point

When the change alters how pieces fit together, a small diagram says in five
lines what takes a paragraph: a new flow across three or more components, a
request or job that now takes a different path, a state machine, a module
split, data moving between services. Add one, after the opening sentences.
Most changes don't need it: a bug fix, a refactor inside one module, a new
field, a copy change. When in doubt, leave it out.

- **Keep it a sketch.** About 3 to 10 nodes, plain-word labels ("API",
  "upload queue", "worker"), the main path and at most one branch. It shows
  the shape of the change, not every function. Mark what's new when that's
  the point (`:::new`, or "(new)" in the label).
- **Pick the format the forge renders.** Find the host with
  `git remote get-url origin`. GitHub, GitLab and Azure DevOps render a
  fenced `mermaid` block: use `flowchart LR` for components and data flow,
  `sequenceDiagram` for a request that bounces between parties,
  `stateDiagram-v2` for states. Bitbucket doesn't render Mermaid natively, and
  an unknown host may not: there, draw the same sketch as plain text in a
  ```` ```text ```` block (`API -> upload queue -> worker -> S3`), which reads
  anywhere.
- **Check it parses.** Quote labels with spaces or punctuation
  (`A["upload queue"]`), avoid parentheses and semicolons in bare labels, and
  keep `end` out of node ids. A diagram that fails to render is worse than
  none.

### Templates

If the repo has a PR template (`.github/pull_request_template.md`,
`.github/PULL_REQUEST_TEMPLATE.md`, `.github/PULL_REQUEST_TEMPLATE/*.md`,
`PULL_REQUEST_TEMPLATE.md` or `docs/PULL_REQUEST_TEMPLATE.md`), use its
headings in its order and fill each one. A section that doesn't apply gets
"None" or "n/a", not filler. Leave checklist boxes unticked unless you
checked the thing in this session: a ticked "Tests added" with no tests is a
false statement under the author's name. Drop the template's instructions
(HTML comments, "describe your change here").

### Testing claims

Write "Tests pass" or tick a test box only if the tests ran in this session
and passed, or CI on the head is green. Otherwise say how to test it, not
that it was tested.

### Updating an existing description

Keep the author's words. Fix what's stale, add what's missing, and don't
append an "Updated:" log: the description describes the PR as it is now.
If the body is empty or a placeholder ("wip", "todo"), replace it.

## Examples

A small change. Commits: "Retry 5xx responses with exponential backoff",
with the body "The exports job fails a few times a day on 503s from the
gateway during deploys."

Good:

> **Retry 5xx responses with exponential backoff**
>
> The API client now retries server errors a few times, waiting longer
> between tries, before failing. Client errors (4xx) still fail at once.
>
> The exports job fails a few times a day on 503s from the gateway during
> deploys; they're short, and a retry gets through.

Bad:

> ## 🚀 Summary
> This PR introduces a robust and comprehensive retry mechanism to enhance the reliability of the API client.
>
> ## 📝 Changes
> - Updated `src/client.js` to add `retry(fn, { attempts = 3, delayMs = 100 })`
> - `delayMs` doubles after each attempt via `delayMs * 2 ** i`
> - Updated `test/client.test.js` to add tests
>
> ## ✅ Testing
> - All tests pass

The bad one says nothing the title didn't, lists the files and the
signature the diff already shows, invents a reason ("enhance the
reliability"), and claims a test run that may not have happened.

A change whose shape matters. Commits move thumbnail generation out of the
upload request into a background worker, because large uploads were timing
out (issue #412), on a GitHub repo.

Good:

> **Generate thumbnails in a background worker**
>
> Uploads now return as soon as the file is stored; thumbnails are made by a
> new worker that picks jobs off a queue. Large uploads were timing out while
> the request waited for thumbnails (#412).
>
> ```mermaid
> flowchart LR
>   U[Upload API] --> S[(Storage)]
>   U --> Q["thumbnail queue (new)"]
>   Q --> W["worker (new)"]
>   W --> S
> ```
>
> Until the worker has run, the gallery shows a placeholder. The deploy needs
> the worker process started; nothing else changes for existing images.

The same sketch for Bitbucket, as text:

> ```text
> Upload API -> storage
>            -> thumbnail queue (new) -> worker (new) -> storage
> ```

## Review comments you write

When the user asks you to leave a comment as a reviewer: say what's wrong,
the concrete case where it breaks, and what to do instead, in that order.
One issue per comment, on the line it's about. Label what isn't blocking
("nit:", "non-blocking:"). Ask real questions, not rhetorical ones ("did you
consider X?" when you mean "X breaks"). A GitHub `suggestion` block beats a
description of a one-line change.
