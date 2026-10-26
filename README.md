# agent-skills

Personal collection of agent skills.

## Layout

One folder per skill under `skills/`, each with a `SKILL.md` at its root:

```
skills/
  <skill-name>/
    SKILL.md        # name, description and instructions
    ...             # any references, scripts or assets the skill uses
    LICENSE         # upstream license, for skills copied from elsewhere
    NOTICE.md       # origin, for skills copied from elsewhere
```

## Using a skill

Copy or symlink `skills/<skill-name>` into your agent's skills directory
(for Claude Code, `~/.claude/skills/<skill-name>`).

## Adding a skill

New skills use [`skill-creator`](skills/skill-creator) as the baseline for structure and quality.

1. Create `skills/<skill-name>/SKILL.md` with frontmatter `name` and `description`.
2. If the skill comes from another repo, keep its files intact, copy the upstream
   license next to it, and add a `NOTICE.md` with the source repo, path and commit.
3. Open a pull request.
