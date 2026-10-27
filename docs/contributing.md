# Contributing

## Layout

`skills/<name>/SKILL.md` is the only source of truth. There is no build step and
there are no symlinks; the manifests just point at the repo root.

```
.claude-plugin/plugin.json        # Claude Code plugin manifest (skills/ is scanned automatically)
.claude-plugin/marketplace.json   # Claude Code marketplace, one entry, source "./"
.codex-plugin/plugin.json         # Codex plugin manifest (name, version, metadata)
plugin.json                       # Antigravity plugin manifest (name and description only)
.agents/plugins/marketplace.json  # Codex marketplace, one entry, source.path "./"
skills/
  <skill-name>/
    SKILL.md                      # name, description and instructions
    ...                           # references, scripts, assets, evals
    LICENSE (or LICENSE.txt), NOTICE.md  # upstream license and origin, for skills copied from elsewhere
LICENSE                           # Apache-2.0, this repo's own work
NOTICE                            # bundled skills and their upstreams
```

GitHub Copilot needs no manifest of its own: it reads `.claude-plugin/marketplace.json` and loads the root `plugin.json` as a legacy plugin; `.codex-plugin/` is ignored.

## Arguments

Every `SKILL.md` reads its arguments from the user's message, so the same skill
works whether it was invoked as `/vstack:babysit 42 --merge`, `$babysit 42`,
`/babysit 42`, or in plain words. None rely on `$ARGUMENTS`.

## Authoring skills

New skills are authored using Anthropic's
[skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
as the baseline for structure, descriptions and evals. It's a dev tool: install it
separately as a personal skill (for Claude Code, copy it to
`~/.claude/skills/skill-creator`). It is not shipped in this plugin.

## Adding a skill

1. Author it as described above: `skills/<skill-name>/SKILL.md`
   with frontmatter `name` and a strong `description`, references for detail,
   and `evals/evals.json`.
2. If the skill comes from another repo, keep its files and upstream license,
   add a `NOTICE.md` with the source repo, path and commit, record any changes as
   `Modified by <name>, <date>: <what>` lines, and list it in the root `NOTICE`.
3. Open a pull request.
