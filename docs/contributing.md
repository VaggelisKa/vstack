# Contributing

## Layout

`skills/<name>/SKILL.md` is the only source of truth. There is no build step and
there are no symlinks; the manifests just point at the repo root.

```
.claude-plugin/plugin.json        # Claude Code plugin manifest (skills/ is scanned automatically)
.claude-plugin/marketplace.json   # Claude Code marketplace, one entry, source "./"
.codex-plugin/plugin.json         # Codex plugin manifest (name, version, metadata)
plugin.json                       # Antigravity plugin manifest (name and description only)
gemini-extension.json             # Gemini CLI extension manifest (name, version, description; skills/ is scanned automatically)
.agents/plugins/marketplace.json  # Codex marketplace, one entry, source.path "./"
skills/
  <skill-name>/
    SKILL.md                      # name, description and instructions
    ...                           # references, scripts, assets, evals
    LICENSE (or LICENSE.txt), NOTICE.md  # upstream license and origin, for skills copied from elsewhere
evals/<case>/                     # cases for `claude plugin eval` (see Testing)
.github/workflows/ci.yml          # tests and plugin validation on every PR
LICENSE                           # Apache-2.0, this repo's own work
NOTICE                            # bundled skills and their upstreams
```

GitHub Copilot needs no manifest of its own: it reads `.claude-plugin/marketplace.json` and loads the root `plugin.json` as a legacy plugin; `.codex-plugin/` is ignored.

## Arguments

Arguments are free text: `$ARGUMENTS` in Claude Code, the request itself on
other harnesses, so the same skill works whether it was invoked as
`/vstack:babysit 42 merge`, `$babysit 42`, `/babysit 42`, or in plain words.

## Authoring skills

New skills are authored using Anthropic's
[skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
as the baseline for structure, descriptions and evals. It's a dev tool: install it
separately as a personal skill (for Claude Code, copy it to
`~/.claude/skills/skill-creator`). It is not shipped in this plugin.

## Conventions

- Scripts are referenced as `${CLAUDE_SKILL_DIR}/scripts/...`. Claude Code
  substitutes it. Other harnesses use the folder holding SKILL.md.
- Every SKILL.md has an `### Arguments` section with a `Request: $ARGUMENTS`
  line and a fallback sentence for harnesses that leave it unfilled: read the
  arguments from the request itself.
- Descriptions stay under 700 characters, in the third person. They say what
  the skill does, when to use it, and what it is not for.
- Skills with side effects (push, comment, merge) set
  `disable-model-invocation: true`, or keep their triggers to explicit asks.

## Adding a skill

1. Author it as described above: `skills/<skill-name>/SKILL.md`
   with frontmatter `name` and a strong `description`, references for detail,
   and `evals/evals.json`.
2. If the skill comes from another repo, keep its files and upstream license,
   add a `NOTICE.md` with the source repo, path and commit, record any changes as
   `Modified by <name>, <date>: <what>` lines, and list it in the root `NOTICE`.
3. Open a pull request.

## Testing

Each skill with scripts has unittest tests in `skills/<name>/scripts/tests`.
Run them from the repo root:

```
python3 -m unittest discover -s skills/review/scripts/tests
python3 -m unittest discover -s skills/babysit/scripts/tests
python3 -m unittest discover -s skills/autonomous-merge/scripts/tests
python3 -m unittest discover -s skills/remove-comments/scripts/tests
```

The tests create git repos, so git needs `user.name` and `user.email`. CI
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs them on every
pull request and push to `main`, on Python 3.11 and 3.13. It also checks script
syntax, lints the html-plan example and runs `claude plugin validate .`.

There are two eval layouts:

- `skills/<name>/evals/evals.json` is the skill-creator format. It holds each
  skill's full set of cases.
- `evals/<case>/` at the repo root is read by `claude plugin eval`. Each case has
  a `prompt.md` (frontmatter, then the prompt) and `graders/*.md`. These cover
  the riskiest rules only and are taken from the `evals.json` cases. Run them
  with `claude plugin eval .`; results go to `evals/results/`, which is ignored.

Neither eval layout runs in CI, because both cost API calls.

## Releasing

Leave `version` out of `.claude-plugin/plugin.json` and the marketplace entries: without it Claude Code versions the plugin by git commit, so every push to `main` is an update; with it, users stay on that version until it's bumped. Codex caches installs by the `version` in `.codex-plugin/plugin.json`, so bump it when releasing changes, and keep `gemini-extension.json` at the same version. Gemini CLI shows it, but a git install updates by comparing commits with the repo's `HEAD`, not versions; only local-path installs compare version against their source folder; linked installs read the folder live. Still bump the version on each change: for a git install, Gemini prints "already up to date" when the version is unchanged, even after re-cloning. Installs from the URL try GitHub releases first; there are none now, so installs are `Type: git`, but if a release ever exists, new installs become `github-release` installs and compare release tags. If an update doesn't show up, remove and re-add the plugin (`codex plugin remove vstack@vaggeliska`, then `codex plugin add vstack@vaggeliska`).
