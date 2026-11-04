# Replies: examples

Every example below is a review comment, a reply that works, and the reply an
assistant tends to write instead. The bad ones aren't wrong on facts. They read
as written by a bot: they grade the comment, perform agreement, apologise for
existing, restate the question, or close with an offer nobody asked for.

The pattern in every good reply: **the first sentence is the answer or the
outcome**, the second (if any) is the evidence, and then it stops.

## When they're right and it's fixed

> `attempt < this.attempts` only tries twice when attempts is 3.

Good:

> Fixed in 4e1a9c2. The loop runs `attempts` times now, and `get retries up to attempts times` covers it.

Bad:

> Great catch, you're absolutely right! I apologize for the oversight. I've gone ahead and fixed the off-by-one error so the loop now correctly runs the configured number of attempts. Let me know if there's anything else!

Agreement shows in the commit, not in adjectives. "Fixed in <sha>" is the
whole acknowledgement. Name the test if you added one.

## When they're right and it isn't fixed yet

You can't post "will fix" for the author unless they said they will. If the
user told you they'll change it:

> Yes, that's a real bug: 3 attempts runs twice. I'll fix it in this PR.

If they didn't, don't post. Put it in the report as needing their call.

## When they're wrong

> This retries 404s, which will hammer the API.

Good:

> 404s aren't retried: line 17 breaks on any status below 500, and `get does not retry a 404` asserts a single call.

Bad:

> Thanks for raising this! Actually, if you look at line 17, you'll see that we break out of the loop for any status code below 500, so 404s are not retried. Hope this clears things up!

Correct with the evidence and nothing else. No "actually", no "if you look
at", no "as you can see": they imply the reviewer didn't look. Point to the
line or the test and let it speak.

## When it's a question

> Why a Map here and not an object?

Good:

> Keys are request paths, and a Map keeps `__proto__` and `constructor` from colliding with real paths. It also gives `.size` for the cap check on line 31.

Bad:

> Great question! The reason I chose a Map over a plain object is because Maps offer several advantages...

Answer it directly. If the honest answer is "no strong reason", say that and
say what you'll do: "No strong reason. An object works the same here; I'll
keep the Map unless you prefer otherwise." (Only "I'll" if the user agreed.)

## When you disagree (and the user told you their position)

> nit: pull `2 ** (attempt - 1)` into a `backoff()` helper.

Good:

> I'd keep it inline: it's one expression used once, and the name `delayMs` next to it already says what it is.

Bad:

> That's a fair point and I can definitely see the value in extracting this! However, I think in this case it might be slightly over-engineered, but I'm happy to change it if you feel strongly!

Say the position and the reason once. Don't pre-surrender ("happy to change
it if you feel strongly") and don't stack hedges ("I think it might be
slightly"). If they push back again, don't argue a second time: that's the
author's call, so leave it for them.

## When it's out of scope

> While you're here, can we retry network errors too?

Good:

> That's a separate change: fetch throws before there's a status, so it needs its own error path and tests. I can open an issue for it.

Bad:

> Absolutely, that's a great idea for a future enhancement! To keep this PR focused, I'd suggest we handle it in a follow-up. Happy to create an issue to track this!

Name why it's separate in one clause, offer the follow-up, stop.

## When it needs no reply

"LGTM", "nice, much cleaner", 👍, a thread the author or someone else already
answered, a thread that's resolved. Don't reply. A "Thanks!" under praise is
noise in the reviewer's inbox, and a second answer to an answered question
says the same thing twice.

## Rude comments

The rule for every hostile comment: **answer the technical content as if it
had been asked politely.** Not warmer to compensate, not colder to match.

Don't:

- mention tone, feelings or the way it was said ("no need for that", "let's
  keep this constructive", "I understand your frustration");
- apologise to make it go away ("sorry, you're right" when they aren't);
- thank them for it ("thanks for the honest feedback");
- defend yourself or the author ("I did test this", "I've been doing this
  for years");
- reach for sarcasm or polite-sounding jabs ("respectfully", "as I said",
  "obviously", "if you'd read the PR");
- echo their wording back ("not amateur hour", "cargo cult or not").

### Rude and wrong

> Did you even run this? It retries 404s forever. Amateur hour.

Good:

> It doesn't retry 404s: line 17 breaks on any status below 500, and `get does not retry a 404` checks there's exactly one call.

Bad:

> I understand the concern, but I did run it. Actually, 404s aren't retried at all. Respectfully, I'd suggest taking another look at line 17.

### Rude and right

> Sloppy. `attempt < this.attempts` means 3 attempts runs twice. Does anyone review this stuff?

Good:

> Fixed in 4e1a9c2: the loop now runs all `attempts`, with a test for 3.

Bad:

> You're absolutely right, and I'm sorry about that, it was sloppy of me. Thanks for catching it!

Being right doesn't earn a grovel. Same reply you'd give a polite reviewer.

### Rude and a design demand

> Exponential backoff is cargo-cult nonsense. Use a fixed 1s delay like a normal person.

If the user said to keep it and why:

> Keeping exponential backoff: the gateway rate-limits bursts during deploys, and a fixed 1s delay keeps every client retrying in step. Doubling from 100ms spreads them out and still gives up within a second.

Bad:

> I hear you, and I understand why it might look like overkill! However, with respect, there are some good reasons for it...

If the user didn't say, don't decide for them: leave it and report it.

### Rude with no technical content

> Whoever wrote this shouldn't be allowed near production.

No reply. There's nothing to answer, and any answer reads as defensive or as
a performance of patience. Tell the user the comment is there and that you
left it.

### A rude question

> How is anyone supposed to know what delayMs means? Units?

Good:

> Milliseconds. It's the wait before the first retry, and it doubles after each one.

Treat the question as a question. If the doc or name could be clearer and
the user agreed to change it, say so with the commit.

## Pushback after an earlier reply

> **dave:** Use a fixed delay.
> **alice:** Keeping backoff: the gateway rate-limits bursts during deploys...
> **dave:** No. Fixed delay. Change it.

Don't reply again with the same argument, or a reworded one. The reasons are
in the thread already. It's the author's decision now: leave the thread and
report it, unless the user told you what to say.

## Replying in another language

Reply in the language the reviewer wrote in, at the same register. A Greek
comment gets a Greek reply that reads like a Greek engineer wrote it, not a
translation of an English template. Identifiers, file names and SHAs stay as
they are.

## Several replies in one run

Read them side by side before posting. Each starts from its own answer, so
they won't share an opening. No sentence appears in two replies. If two
threads have the same answer, give it in one and link it from the other
("Same as the 429 question above: <link>").

## Length

Most replies are one or two sentences. Three is a lot. Write more only when
the question needs it (a trade-off with two sides, a reproduction), and even
then no headings, no bold, no bullet list for two points.
