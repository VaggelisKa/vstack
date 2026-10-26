# vstack

Personal collection of agent skills, packaged as one plugin (`vstack`) for
Claude Code and Codex, and as plain skill folders for Antigravity.

> This repository and plugin were formerly named `agent-skills`. GitHub redirects
> the old URL, but reinstall as `vstack@vaggeliska` to pick up the new name.

| Skill | What it does |
|---|---|
| `babysit` | Watch a pull request until it's green and approved (or merged with `--merge`): poll checks and reviews, fix, reply, escalate |
| `html-plan` | Write an implementation plan as one interactive HTML page |

## Layout

`skills/<name>/SKILL.md` is the only source of truth. There is no build step and
there are no symlinks; the manifests just point at the repo root.

```
.claude-plugin/plugin.json        # Claude Code plugin manifest (skills/ is scanned automatically)
.claude-plugin/marketplace.json   # Claude Code marketplace, one entry, source "./"
plugin.json                       # portable Agent Plugins manifest (read by Codex)
.agents/plugins/marketplace.json  # Codex marketplace, one entry, source.path "./"
skills/
  <skill-name>/
    SKILL.md                      # name, description and instructions
    ...                           # references, scripts, assets, evals
    LICENSE (or LICENSE.txt), NOTICE.md  # upstream license and origin, for skills copied from elsewhere
LICENSE                           # Apache-2.0, this repo's own work
NOTICE                            # bundled skills and their upstreams
```

## Private repo: git credentials

This repository is private. Every install below clones it with your normal git
credentials, so make sure `git clone https://github.com/VaggelisKa/vstack`
works in a terminal first (for example after `gh auth login` and
`gh auth setup-git`, or with an SSH key). Desktop apps that run in the
background may not see credentials that only your shell has.

## Claude Code

Install:

```
/plugin marketplace add VaggelisKa/vstack
/plugin install vstack@vaggeliska
```

The same works from a shell: `claude plugin marketplace add VaggelisKa/vstack`
then `claude plugin install vstack@vaggeliska`.

Use: `/vstack:babysit <pr> [--merge]`, `/vstack:html-plan <what to build>`,
or just describe the task and let the skill trigger.

Update (the Claude manifest has no `version`, so updates follow the latest commit):

```
/plugin marketplace update vaggeliska
/plugin update vstack@vaggeliska
```

Uninstall:

```
/plugin uninstall vstack@vaggeliska
/plugin marketplace remove vaggeliska
```

> Tested: the `claude plugin ...` shell commands above (add, install, update,
> uninstall, remove). Not yet tested: the in-session `/plugin ...` slash commands,
> and `/vstack:babysit` typed inside an interactive session.

## Codex

Plugins work in the Codex CLI and the ChatGPT desktop app, not in the IDE extension.

Install:

```
codex plugin marketplace add VaggelisKa/vstack
codex plugin add vstack@vaggeliska
```

or, after adding the marketplace, open `/plugins` in the CLI and install it from
the `vaggeliska` tab. Start a new session afterwards.

Use: `$babysit <pr> [--merge]`, `$html-plan <what to build>`.

Update:

```
codex plugin marketplace upgrade vaggeliska
```

Codex caches installs by the `version` in the root `plugin.json`, so bump it when
releasing changes; if an update doesn't show up, remove and re-add the plugin.

Uninstall:

```
codex plugin remove vstack@vaggeliska
codex plugin marketplace remove vaggeliska
```

> Not yet verified: invoking `$babysit` with arguments inside a Codex session.
> Install, upgrade and removal were tested with the CLI.

## Antigravity

Skills only, installed by copying (no plugin manifest is used):

```
git clone https://github.com/VaggelisKa/vstack
# all workspaces (Antigravity app and IDE):
mkdir -p ~/.gemini/config/skills && cp -R vstack/skills/* ~/.gemini/config/skills/
# or one workspace:
mkdir -p .agents/skills && cp -R vstack/skills/* .agents/skills/
```

The Antigravity CLI reads global skills from `~/.gemini/antigravity-cli/skills/`
instead. Use: `/babysit <pr> [--merge]`, `/html-plan <what to build>`.

Update: pull the clone and copy again. Uninstall: delete the copied skill folders.

> Not yet verified: installing and invoking the skills in Antigravity.

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

## License

Apache-2.0 for this repository's own work (see `LICENSE`). Bundled skills keep
their upstream licenses; see `NOTICE`.
