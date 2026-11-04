# PR titles and descriptions

## Title

What the change does, in the imperative, specific enough to tell apart from
the next ten PRs: "Retry 5xx responses with exponential backoff", not "Retry
improvements" or "Update client.js". Under about 70 characters. Follow the
repo's convention when it has one: read `git log --oneline -20` on the base
and match a prefix style (`feat:`, `client:`, a ticket key) only if most
commits use it.

## Body

A reviewer reads the description to learn three things before the diff: what
changes, why, and what to look at. Write for that reader.

1. **What and why, first.** One short paragraph: the behaviour change and the
   reason for it. The reason comes from the commits, the linked issue, the
   code or the user. Never invent one ("to improve reliability") when you
   don't know it; say what changed and leave the why to the author, or ask.
2. **What a reviewer needs.** Only what applies: anything surprising in the
   approach and why it was chosen over the obvious one, behaviour that
   deliberately didn't change, risk (migrations, config, a default that
   changed), how to try it, follow-ups left out on purpose.
3. **Links.** `Fixes #123` when the issue is known. Nothing invented.

Length follows the change. A one-line fix gets a sentence or two. A larger
change gets a few short paragraphs or a short list. Nobody needs a
file-by-file changelog: the diff is right there.

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

## Example

Commits: "Retry 5xx responses with exponential backoff" with the body "The
exports job fails a few times a day on 503s from the gateway during deploys."

Good:

> **Retry 5xx responses with exponential backoff**
>
> `ApiClient.get` now retries 5xx responses up to `attempts` times (default 3), waiting `delayMs` (default 100ms) and doubling it after each try. 4xx responses fail on the first try as before.
>
> The exports job fails a few times a day on 503s from the gateway during deploys; these are short and a retry gets through.
>
> To try it: `npm test` covers a 503 that recovers and a 404 that isn't retried.

Bad:

> ## 🚀 Summary
> This PR introduces a robust and comprehensive retry mechanism to enhance the reliability of the API client.
>
> ## 📝 Changes
> - Updated `src/client.js` to add retry logic
> - Updated `test/client.test.js` to add tests
>
> ## ✅ Testing
> - All tests pass

The bad one says nothing the title didn't, lists the files the diff already
shows, invents a reason ("enhance the reliability"), and claims a test run
that may not have happened.

## Review comments you write

When the user asks you to leave a comment as a reviewer: say what's wrong,
the concrete case where it breaks, and what to do instead, in that order.
One issue per comment, on the line it's about. Label what isn't blocking
("nit:", "non-blocking:"). Ask real questions, not rhetorical ones ("did you
consider X?" when you mean "X breaks"). A GitHub `suggestion` block beats a
description of a one-line change.
