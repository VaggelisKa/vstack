# Origin

Copied from:

- Upstream: https://github.com/anthropics/claude-plugins-community (`html-plan/skills/html-plan`),
  where the plugin was added in commit f2ab43e09b ("Add html-plan plugin"). Upstream
  has no earlier source; the plugin was authored there.
- Fork: https://github.com/VaggelisKa/claude-plugins-community/tree/f60f0454df3045f724c43c6346ec80bdcc3472b2/html-plan/skills/html-plan
- Commit: f60f0454df3045f724c43c6346ec80bdcc3472b2
- Author (per upstream plugin.json): Thariq Shihipar

## License

`LICENSE` is the upstream repository license (Apache-2.0), which covers the
repository's contents. The upstream `html-plan/.claude-plugin/plugin.json` also
declares `"license": "MIT"`, and no MIT license text ships with it. This copy is
redistributed under the repository's Apache-2.0 license; the MIT declaration is
recorded here for completeness. If the MIT declaration in upstream plugin.json
governs, this copy also complies with it.

## Modifications

- Modified by Evangelos Karavasileiadis, 2026-10-26: `SKILL.md` no longer uses the
  harness-specific `$ARGUMENTS` placeholder; it reads the request from the user's message so the
  skill works across agent harnesses. A header comment in `SKILL.md` marks the change.
- Modified by Evangelos Karavasileiadis, 2026-10-28: `SKILL.md` gains an `argument-hint`
  frontmatter field, and its pack step uses `${CLAUDE_SKILL_DIR}` for the skill's folder, with a
  sentence explaining it. The header comment in `SKILL.md` records this change too.

All other files are unmodified.
