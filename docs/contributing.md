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
lib/                              # shared Python for the skills' scripts, loaded by path
skills/
  <skill-name>/
    SKILL.md                      # name, description and instructions
    ...                           # references, scripts, assets, evals
    LICENSE (or LICENSE.txt), NOTICE.md  # upstream license and origin, for skills copied from elsewhere
evals/<skill>-<id>-<slug>/        # cases for `claude plugin eval`, generated from evals.json (see Evals)
evals/_harness/                   # sync.py, scaffold.py, the fake gh, fixtures, and their tests
evals/run.sh                      # runs the evals locally
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
   and `evals/evals.json`. Give every case an `eval` object, include at least one
   case where the skill should not start, and run `python3 evals/_harness/sync.py`
   (see Evals).
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
python3 -m unittest discover -s evals/_harness/tests
```

The tests create git repos, so git needs `user.name` and `user.email`. CI
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs them on every
pull request and push to `main`, on Python 3.11 and 3.13. It also checks script
syntax, lints the html-plan example and runs `claude plugin validate .`.

### Evals

`skills/<name>/evals/evals.json` (the skill-creator format) is the only source of
eval cases. Each case's `eval` object says how `claude plugin eval` runs it: the
prompt, a fixture to build the workspace from, and deterministic graders. Every
expectation without one becomes an LLM-judged grader. The format is in
[`evals/_harness/sync.py`](../evals/_harness/sync.py). After editing an
`evals.json`, regenerate the runnable cases in `evals/<skill>-<id>-<slug>/`
(never edit those by hand):

```
python3 evals/_harness/sync.py
```

Cases with a fixture run against a fake GitHub instead of a prose description of
one. The case's scaffold makes the run's working directory a checkout of PR
branches from `evals/_harness/fixtures/<name>.json`, plus the case's `patch`. Its
`origin` is `git@github.com:acme/api.git`, served offline from a local bare repo,
so fetch and push work. A fake `gh` ([`fakegh.py`](../evals/_harness/fakegh.py))
answers from the fixture's PR state: checks, reviews, threads, labels and branch
protection. Every call it gets, and every side effect (a push, a merge, a reply, a
resolved thread, a rerun), is logged in `.vstack-eval/gh-calls.log`, which the
graders match. So "never merges" is checked as `# merged` never appearing, not
inferred from the transcript.

Run them locally with your Claude Code login:

```
evals/run.sh --tag smoke --runs 1      # the quick set (~15 cases, about $10)
evals/run.sh --case 'babysit-*'        # one skill
evals/run.sh                           # everything: 3 runs per case, with and without the plugin
```

`run.sh` runs `claude plugin eval` with the scaffold enabled, Bash, Write and Edit
granted, and a sonnet judge (pass `--judge-model opus` if the agent runs on
sonnet). Results go to `evals/results/`, which is ignored. The headline number
is the with-minus-without delta per case. The full suite is expensive: the
babysit and autonomous-merge cases run whole review and CI loops ($0.30 to $3
per run), so cap it with `--max-cost-usd`. Tags: `smoke`, `merge-intent` and
`trigger` (cases checking that a skill does or doesn't start from plain words).

The eval sandbox hides your home directory, and the skills' scripts need Python
3.10+. If the only Python 3.10+ you have is under `~` (pyenv, uv), `run.sh` puts
one in `evals/.python/` (ignored), or set `VSTACK_EVAL_PYTHON`.

CI runs the harness tests (`python3 -m unittest discover -s evals/_harness/tests`)
on every pull request. They check that `evals/` matches the `evals.json` files,
that every case is well-formed and every fixture scaffolds, and that the skills'
own scripts work against the fake `gh`. CI doesn't run the agent evals, because
they cost API calls. To run them in CI, add a workflow with an
`ANTHROPIC_API_KEY` secret that runs `evals/run.sh --tag smoke --runs 1
--max-cost-usd 15` on a label or a schedule.

## Releasing

Leave `version` out of `.claude-plugin/plugin.json` and the marketplace entries: without it Claude Code versions the plugin by git commit, so every push to `main` is an update; with it, users stay on that version until it's bumped. Codex caches installs by the `version` in `.codex-plugin/plugin.json`, so bump it when releasing changes, and keep `gemini-extension.json` at the same version. Gemini CLI shows it, but a git install updates by comparing commits with the repo's `HEAD`, not versions; only local-path installs compare version against their source folder; linked installs read the folder live. Still bump the version on each change: for a git install, Gemini prints "already up to date" when the version is unchanged, even after re-cloning. Installs from the URL try GitHub releases first; there are none now, so installs are `Type: git`, but if a release ever exists, new installs become `github-release` installs and compare release tags. If an update doesn't show up, remove and re-add the plugin (`codex plugin remove vstack@vaggeliska`, then `codex plugin add vstack@vaggeliska`).
