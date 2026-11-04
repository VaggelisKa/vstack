# Working on vstack

Instructions for coding agents (Claude Code, Codex, Copilot, Gemini CLI,
Antigravity) changing this repository. The layout, conventions, tests and
eval harness are in [docs/contributing.md](docs/contributing.md); read it
before changing a skill.

## Keep the README's eval results current

The README's "Does it help?" section is the public record of what the evals
show. Update it in the same pull request whenever either of these happens:

- **You ran evals and are reporting results.** Regenerate the chart and the
  table from that run, and commit the README and both SVGs with it:

  ```
  python3 evals/_harness/chart.py evals/results/<timestamp>/aggregate-result.json
  ```

- **Eval cases or graders changed** (a new skill, new cases, new or changed
  graders or expectations). Run the affected cases with both arms
  (`evals/run.sh --case '<skill>-*'`, or `--tag smoke` for the headline set),
  then regenerate as above. If you can't run them where you are, say so in
  the PR: `chart.py` and the README name every skill with cases but no
  results ("Not in this run yet"), and CI fails if a skill is missing from
  that section or its case count there is stale. Never hand-edit the numbers.

Never report eval scores from memory, from a different run, or from a run
whose cost cap cut it short without saying so (`chart.py` adds "Partial run"
to the caption).

## Bump every versioned manifest

Every pull request raises `version` to the same new value in all three
manifests that carry one:

- `.codex-plugin/plugin.json` (Codex)
- `gemini-extension.json` (Gemini CLI)
- `plugin.json` at the root (Antigravity)

Patch for fixes and wording, minor for new behaviour, major for breaking
changes. Keep `version` out of `.claude-plugin/plugin.json` and the
marketplace files: Claude Code versions the plugin by commit.
`python3 .github/scripts/check-version.py --base origin/main` checks this, and
CI runs it on every PR. If another PR merges first with the same version,
bump again.

## When you add a skill

Also list it in the README's skill list, in each manifest's `description`,
and in `NOTICE`, and give it eval cases (see "Adding a skill" in
docs/contributing.md).
