# Harnesses: running reviewers outside the current session

Read this for `--cross`, or when the host has no subagent tool and you want to
run reviewers in parallel through its own CLI.

Contents: [Verified versions](#verified-versions) · [Detect](#detect) ·
[Privacy warning](#privacy-warning) · [Common setup](#common-setup) ·
[codex](#codex) · [claude](#claude) · [gemini](#gemini) · [agy](#agy) ·
[Models by tier](#models-by-tier) · [Running in parallel](#running-in-parallel) ·
[Parsing and validation](#parsing-and-validation) ·
[In-harness subagents](#in-harness-subagents)

## Verified versions

Every flag below was checked against `--help` for these versions:

| CLI | Version | Provider |
|---|---|---|
| `codex` | codex-cli 0.160.0 | OpenAI |
| `claude` | Claude Code 2.1.310 | Anthropic |
| `gemini` | gemini 0.65.0 | Google |
| `agy` | 1.4.7 (Antigravity) | Google (the model you pick may come from another provider) |

Use only these flags. If an installed version is different, run
`<cli> --help` and confirm each flag you're about to use is listed. If one is
missing, don't use that harness and record "skipped: <cli> <version> lacks
<flag>". Guessing at flags in a non-interactive run can hang it, or worse, run
it with write access.

## Detect

```bash
for c in codex claude gemini agy; do
  command -v "$c" >/dev/null 2>&1 && echo "$c: $("$c" --version 2>/dev/null | head -1)"
done
```

Don't count the CLI of the harness you're running in as a separate harness for
`--cross`. Its subagents already cover that provider. The exception is a host
with no subagent tool: then its own CLI is how you get parallel reviewers.

## Privacy warning

`--cross` sends code to other companies. Print this before running anything,
listing only the providers you'll actually use:

```
--cross: the diff and any files reviewers open will be sent to <OpenAI (codex)>, <Google (gemini, agy)>, <Anthropic (claude)>.
Excluded as likely secrets: <files, or "none">.
```

Leave `.env*`, `*.pem`, `*.key`, `*secret*`, `*credential*`, `id_rsa*` and
similar files out of the area diffs you send, and name them in the report's
"Not reviewed" section. The user opted in with `--cross`, so go ahead after the
warning without asking again.

## Common setup

```bash
REPO=$(git rev-parse --show-toplevel)
SKILL_DIR=<absolute path of this skill>        # the folder holding SKILL.md
TMP=$(mktemp -d)                               # prompts, outputs, logs: outside the repo
# Per reviewer: write the filled prompt from reviewer-contract.md to $TMP/<id>.prompt
```

Pass the prompt as an argument with `"$(cat "$TMP/<id>.prompt")"`. Areas are
kept to about 400 changed lines, so they fit comfortably within argument length
limits. Run every CLI from `$REPO` (or with `-C`) so its read tools can see the
code. Wrap each call in `timeout <secs>` using the tier timeout (360, 600 or 900
seconds). Exit code 124 means a timeout, which is **no verdict**.

## codex

Read-only sandbox, ephemeral session, with the schema enforced by the CLI:

```bash
timeout 900 codex exec -s read-only --skip-git-repo-check --ephemeral \
  -C "$REPO" -m "$MODEL" -c model_reasoning_effort="high" \
  --output-schema "$SKILL_DIR/references/reviewer.schema.json" \
  -o "$TMP/R3.json" \
  "$(cat "$TMP/R3.prompt")" > "$TMP/R3.log" 2>&1
```

- `-s read-only` is the sandbox. Never use `workspace-write` or
  `danger-full-access` here.
- `-o` writes the final message, which is the JSON, to the file. `--json`
  streams JSONL events to stdout instead. You don't need them, but they help
  debug a failure.
- `-c key=value` sets config values. `model_reasoning_effort` (low, medium or
  high) is a Codex config key, not a `--help` flag. If the CLI rejects it, drop
  it and note the effort as "default".
- Skeptics: pass `--output-schema "$SKILL_DIR/references/skeptic.schema.json"`.

## claude

Plan mode, with an allowlist of read-only tools and the schema enforced through
`--json-schema`, which takes the schema **text**:

```bash
(cd "$REPO" && timeout 900 claude -p "$(cat "$TMP/R4.prompt")" \
  --model opus --effort high --permission-mode plan \
  --allowed-tools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(gh pr view:*),Bash(gh pr diff:*)" \
  --output-format json \
  --json-schema "$(cat "$SKILL_DIR/references/reviewer.schema.json")") \
  > "$TMP/R4.out.json" 2> "$TMP/R4.err"
```

- `--output-format json` prints a single envelope object. Use
  `structured_output` if it's there. Otherwise parse the `result` string as
  JSON. If `is_error` is true, there's no verdict.
- Don't add tools to the allowlist. Anything that writes stays blocked by plan
  mode and the list.

## gemini

Plan approval mode (read-only), JSON output. Gemini doesn't enforce a schema,
so keep `{{schema_json}}` in the prompt:

```bash
(cd "$REPO" && timeout 900 gemini -p "$(cat "$TMP/R5.prompt")" \
  -m "$MODEL" --approval-mode plan -o json) \
  > "$TMP/R5.out.json" 2> "$TMP/R5.err"
```

- Add `--skip-trust` when the repo isn't a trusted folder for Gemini. Without
  it, a non-interactive run can refuse or stall on the trust prompt.
- The envelope's `response` field holds the model's text. Pull the JSON out of
  it (see [Parsing and validation](#parsing-and-validation)).

## agy

Plan mode inside the sandbox, with the schema enforced from a **file**, and its
own print timeout set just under the outer `timeout`:

```bash
(cd "$REPO" && timeout 930 agy -p "$(cat "$TMP/R6.prompt")" \
  --model "$MODEL" --effort high --mode plan --sandbox \
  --output-format json \
  --json-schema "$SKILL_DIR/references/reviewer.schema.json" \
  --print-timeout 15m) \
  > "$TMP/R6.out.json" 2> "$TMP/R6.err"
```

- Use both `--mode plan` and `--sandbox`. Neither alone is the read-only guarantee you want.
- The JSON envelope isn't documented in `--help`. Look at the first output:
  use a structured or schema field if there is one, otherwise the final text
  field, then validate.

## Models by tier

Model names change often. The examples below are illustrations. Check what the
user's install accepts (the CLI's model list, its config, or a rejected `-m`
error) and record the model you actually used. If a name is rejected, retry
once with the CLI's default model (leave out `-m`/`--model`) and note the
fallback.

| Tier (C+K) | Effort | claude / Claude Code | codex | gemini | agy |
|---|---|---|---|---|---|
| cheap (0-2) | low | `haiku` | a `-mini` model | a Flash-Lite model | a fast model from its picker |
| mid (3-4) | medium | `sonnet` | the account default | a Flash model | the default model |
| strong (5-6) | high | `opus` | the strongest model offered | a Pro model | the strongest model offered |

Effort flags: `claude --effort`, `agy --effort`, and
`codex -c model_reasoning_effort=...`. gemini has no effort flag here, so tier
it by model alone.

Skeptics need a **different model** from every reviewer in their cluster.
Within one provider, that means another tier (a sonnet reviewer gets an opus
skeptic, or the other way round). With `--cross`, prefer a different provider.

## Running in parallel

Start every call in the background, then wait for all of them:

```bash
( cd "$REPO" && timeout 900 codex exec -s read-only ... -o "$TMP/R3.json" "$(cat "$TMP/R3.prompt")" \
    > "$TMP/R3.log" 2>&1; echo $? > "$TMP/R3.exit" ) &
( cd "$REPO" && timeout 900 gemini -p "$(cat "$TMP/R5.prompt")" -m "$MODEL" --approval-mode plan -o json \
    > "$TMP/R5.out.json" 2> "$TMP/R5.err"; echo $? > "$TMP/R5.exit" ) &
wait
```

With your harness's background-command support, the equivalent is to start
each call as its own background task and collect them all before
aggregating. Exit 124 is a timeout. Any other non-zero exit is an error. In
both cases there's no verdict, and you record the code plus the last lines of
stderr. Don't put stderr into the report verbatim if it might contain tokens.

## Parsing and validation

Structured output is a request, not a guarantee, so **validate every result**:

1. Take the JSON from the envelope (`-o` file for codex,
   `structured_output`/`result` for claude, `response` for gemini, the
   structured or text field for agy).
2. If it's text, strip one surrounding code fence. If the text doesn't hold
   exactly one JSON object, it's malformed.
3. Validate against `reviewer.schema.json` (or `skeptic.schema.json`). If
   `python3 -c "import jsonschema"` works, use that. Otherwise check required
   fields, enums and types by hand.
4. Then check the ranges, which the schemas leave out on purpose: OpenAI strict
   structured outputs reject `minimum`/`maximum`, and every property has to be
   `required` with `additionalProperties: false`. So check score 0-10,
   confidences 0-1, and line ≥ 1 or null yourself.

**A malformed or invalid result counts as no verdict.** Don't repair it by
guessing missing fields, and don't re-run it in place of a timeout. Record the
reason in the reviewer table.

## In-harness subagents

This is the default mode, with no code leaving the current provider.

- **Claude Code**: use the Agent (Task) tool, one call per reviewer, all in one
  message so they run at the same time. Set `model` to `haiku`, `sonnet` or
  `opus` by tier. Use a read-only agent type if one can read files and run
  `git`. Otherwise use a general agent and rely on the read-only rule in the
  prompt. Run them in the background and collect every result before aggregating.
- **Codex / Antigravity**: if the host offers a subagent or parallel-task
  tool, use it the same way. If it doesn't but the host's CLI is on `PATH`, run
  that CLI in parallel as shown above (same provider, so no warning needed).
  If neither works, fall back to sequential personas.
- If the host can't choose a model per subagent, record `model: "inherited"`
  and tell copies apart by persona.
