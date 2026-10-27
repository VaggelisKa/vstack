# vstack

Personal collection of agent skills, packaged as one plugin (`vstack`) for
Claude Code, Codex and Antigravity.

> This repository and plugin were formerly named `agent-skills`. GitHub redirects
> the old URL, but reinstall as `vstack@vaggeliska` to pick up the new name.

| Skill | What it does |
|---|---|
| `babysit` | Watch a pull request until it's green and approved (or merged with `--merge`): poll checks and reviews, fix, reply, escalate |
| `html-plan` | Write an implementation plan as one interactive HTML page |
| `remove-comments` | Strictly delete or shorten comment noise on the lines a change added (or a PR, paths, `--all`), keep only comments that carry a fact the code can't, and prove with a token-level check that no code changed. Never commits |
| `review` | Read-only code review of a PR, branch, path or local changes: plans risk-weighted parallel reviewers (security, data integrity, concurrency, architecture, design, live UI, scalability, bug hunt), challenges serious findings with a skeptic, and writes a report with a Ship / Ship with fixes / Rethink / Incomplete decision |

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
`/vstack:remove-comments [PR|path...] [--all] [--dry-run]`, or just describe
the task and let the skill trigger.

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

Use: `$babysit <pr> [--merge]`, `$html-plan <what to build>`,
`$remove-comments [PR|path...] [--all] [--dry-run]`.

Update:

```
codex plugin marketplace upgrade vaggeliska
```

Codex caches installs by the `version` in `.codex-plugin/plugin.json`, so bump it when
releasing changes; if an update doesn't show up, remove and re-add the plugin.

Uninstall:

```
codex plugin remove vstack@vaggeliska
codex plugin marketplace remove vaggeliska
```

> Not yet verified: invoking `$babysit` with arguments inside a Codex session.
> Install, upgrade and removal were tested with the CLI.

## Antigravity

Install with the Antigravity CLI (`agy`):

```
agy plugin install https://github.com/VaggelisKa/vstack
agy plugin list
```

If that doesn't work for you, clone the repo into the global plugins folder:

```
git clone https://github.com/VaggelisKa/vstack ~/.gemini/config/plugins/vstack
```

Use: `/vstack:babysit <pr> [--merge]`, `/vstack:html-plan <what to build>`,
`/vstack:remove-comments [PR|path...] [--all] [--dry-run]`.

Update: `agy plugin uninstall vstack`, then install again (or `git pull` in the
cloned folder). Uninstall: `agy plugin uninstall vstack`, or delete the folder.

> Tested with `agy` 1.4.7:
> - `agy plugin install https://github.com/VaggelisKa/vstack` from the private
>   repo (with git credentials set up as above) installs `vstack` with 2 skills
>   (`babysit`, `html-plan`) into `~/.gemini/config/plugins/vstack`
> - `agy plugin install <path>` from a local clone installs `vstack` with 2 skills
> - a `git clone` into `~/.gemini/config/plugins/vstack` is picked up by
>   `agy plugin list` with 2 skills
> - `agy plugin validate . --strict` passes
> - `agy plugin list` and `agy plugin uninstall vstack` work
>
> Not yet verified: invoking the skills, or passing arguments to them, inside an
> Antigravity session.

## review: what's tested

`/vstack:review [PR|branch|path] [--cross] [--budget N] [--out PATH] [--no-skeptic]`
(`$review` in Codex).

- Tested in Claude Code against a real merged PR: three parallel subagent
  reviewers, one skeptic on a different model, report written to `--out`, and
  the working tree left unchanged.
- The non-interactive flags in `skills/review/references/harnesses.md` were
  checked against `--help` for codex-cli 0.160.0, Claude Code 2.1.310,
  gemini 0.65.0 and agy 1.4.7. Prompts on stdin were run live for codex
  (`exec -`) and claude (`-p`); claude's `--disallowed-tools "Read(**/.env*)"`
  was run live and blocked reading `.env`, and also blocked Grep on it and
  hid it from Glob. Live on Claude Code 2.1.310: an allowed `Bash(git show:*)`
  let `git show --output=<file>` write a file, and a `Bash(git * --output*)`
  deny blocked only the unquoted spelling: `git show "--output=<file>" HEAD`
  still wrote it. So claude reviewers no longer get raw git, only
  `scripts/git-read.sh`, under `--permission-mode dontAsk` (listed in
  `--help`). Run live with only the script allowed: every quoted `--output`,
  `--ext-diff` and `--no-index` variant, redirection and command chaining was
  blocked with no file written, and the script's own reads worked.
- The helpers in `skills/review/scripts/` have tests in
  `skills/review/scripts/tests/`. Run them from the repo root (they only use
  temp dirs and temp git repos):

  ```bash
  python3 -m unittest discover -s skills/review/scripts/tests
  ```

  They cover `git-read.sh` (legitimate `show`/`diff`/`log` reads, including
  `log -S`/`-G` in attached and separate forms and `log -- <path>`;
  `--output` in plain and shell-quoted forms run through a real `bash -c`,
  `--ext-diff`, `--no-index`, `--textconv`, `-c`, absolute, `..` and
  `-`-prefixed paths for `diff` and `log`, a second `--`, an empty `-S`/`-G`,
  bad SHAs and two-dot diffs all exit 2 with no file written; a `-S`/`-G`
  value such as `--output=<file>` is searched as a literal string and writes
  nothing; an external diff or textconv set in repo config or the environment
  never runs);
  `split-hunks.py` on a real `git diff` with a path containing ` b/`, a
  deletion, a rename and a C-quoted path, and on the documented diff commands
  under `diff.noprefix` and `diff.mnemonicPrefix`; `scan-secrets.py` for a
  clean hunk, a leak, an untracked file holding a token, and each fail-closed
  case; `wrap-untrusted.py` for upper-case, spaced, fake-opening and
  full-width `untrusted-` tags, tags with zero-width, bidi, combining or
  filler characters inside, pre-escaped `&lt;`, and look-alike and invisible
  characters outside tags passing through byte-identical (apart from `&`
  escaping). They also run the
  claude allow/deny and untracked-file bash blocks from `harnesses.md` as
  written.

> Not yet verified: `--cross` (running reviewers through codex, gemini or agy),
> gemini's stdin prompt (help only), agy's stream-json stdin (not used),
> the gitleaks and trufflehog invocations (neither was installed),
> the sequential-personas fallback, the `ui-live` lens (Playwright and simctl),
> budget trimming on large diffs, and running the skill inside Codex or
> Antigravity sessions.

## remove-comments: what's tested

`/vstack:remove-comments [PR|path...] [--all] [--dry-run] [--report PATH]`
(`$remove-comments` in Codex).

The model decides which comments go; `skills/remove-comments/scripts/comments.py`
lists them, applies the decisions only inside comment spans, and verifies that
the code tokens (and, for Python, the AST) didn't change. Its tests are in
`skills/remove-comments/scripts/tests/` and use only temp dirs and temp git
repos:

```bash
python3 -m unittest discover -s skills/remove-comments/scripts/tests
```

They cover:

- the scanner, per language: comment markers inside strings; JS/TS template
  literals with nested `${}`, regex literals containing `//` and `/*`,
  division followed by a real comment, JSX `{/* */}` children as one unit and
  JSX text containing `//` and `'`, a mismatched JSX tag as an error; Rust
  lifetimes, raw strings and nested block comments; Go raw strings and runes;
  Java, Kotlin and Swift text blocks with interpolation; C# verbatim and
  interpolated strings; C++ raw strings, digit separators and `//` comments
  continued by a backslash; Dart strings; CSS `url(//x)` and `//` only in
  SCSS/Less; shell heredocs (`<<-`, quoted delimiters), here-strings, `$#`,
  `${#a[@]}`, `${x#pre}`, `$(( 16#ff ))`, `a#b`, `\#` and comments inside
  `$( )`; YAML quoted scalars (also multi-line), `url: http://x#frag`, literal
  and folded block scalars, and the comment that ends a `|+` scalar
  (protected); TOML multi-line strings; Python 3.12 f-strings with `#` in
  nested expressions and module, class, function and only-statement
  docstrings; Dockerfile parser directives and `RUN <<EOF` heredocs; HTML
  conditional comments, comments in attributes and `<script>`; BOM and CRLF
  offsets; unterminated strings, templates, regexes, heredocs and bare CR as
  errors;
- protected detection: 35 directive cases across the languages (eslint,
  `@ts-expect-error`, prettier, biome, istanbul, webpack magic comments,
  `#__PURE__`, JSX pragmas, triple-slash, test-environment docblocks, `type:
  ignore`, `noqa`, pylint, pragma, fmt, shellcheck, `//go:build`,
  `//go:embed`, `//nolint`, NOLINT, fallthrough, Rust `SAFETY:`,
  yaml-language-server, renovate, taplo, hadolint, stylelint, modelines),
  with and without a reason; prose that mentions a tool but isn't a
  directive; legal headers after a shebang, encoding cookie or directive;
  PEP 723 blocks and cgo preambles; the hint flags, including comments that
  address the agent;
- `apply`: own-line, trailing, inline and multi-line block deletes, tokens
  that must not merge (`a/**/-/**/-b`), two comments on one line, JSX
  children, HTML inline comments, a shell comment after a `\` continuation,
  Python comments and docstrings; blank-line collapsing (between blocks,
  after an opener, before a closer, at file start and end); BOM, CRLF and a
  missing final newline preserved; rewrites (indentation kept, a multi-line
  run, a CRLF file, a docstring) and 13 invalid rewrites refused with no file
  changed; missing, unknown, protected and reasonless decisions refused; a
  docstring that is the only statement can't be deleted; a stale file skipped,
  a second apply reporting "file changed since list"; `--dry-run`; `restore`
  to the snapshot and back to the applied state; the `report` sections;
- `verify`: comment and whitespace changes pass in JS, Python, YAML and
  shell; it exits 1 when a code token changes, string content changes, a `#`
  inside a string is removed, code is uncommented, a protected directive is
  removed, Python indentation, YAML indentation or a shell continuation
  changes, or the Python AST changes; docstring changes are reported
  separately and fail only with `--strict`; a scan error exits 2; the
  snapshot mode vs `--base HEAD`;
- scope: only added lines in scope, untracked files wholly in scope; staged
  files and names with spaces, quotes, a leading `-`, non-ASCII characters
  and directories with spaces; `--all`; a clean branch ahead of `main`;
  `--diff BASE` and a renamed file; `--paths` on directories (tracked files
  only) and `-- -x.py`; refusals of paths outside the repo, symlinked files
  and directories, `--out` inside the repo, refs starting with `-` and
  unknown refs; skipping Markdown, JSON, `node_modules/`, `dist/`, `.min.`,
  lockfiles, `DO NOT EDIT` files and symlinks;
- six fixtures in `skills/remove-comments/evals/fixtures/` (TypeScript, TSX,
  Python, shell, YAML, Go) of agent-written code: decisions derived from each
  hand-written `before`/`after` pair, applied through the CLI, reproduce
  `after` byte for byte, verify passes, and the comments in
  `expected-kept.json` survive;
- property checks over a 23-file corpus covering every supported language
  plus the fixtures: stripping twice equals stripping once, the stripped file
  verifies, and deleting every comment the tool allows, through `list` and
  `apply`, verifies.

Also run by hand (not part of the test suite):

- On a copy of the personal site (`site.js`, `style.css`, `index.html`):
  57 comments listed; 47 deleted and 10 shortened by hand-written decisions;
  `verify` reported identical code tokens in all 3 files and `node --check
  site.js` passed.
- On a clone of a 322-file Swift, Python and YAML app repo, deleting every
  comment the tool allows (5,060 across 264 files): `verify` exited 0 with
  identical code tokens in all 322 files. One Swift file that uses a regex
  literal (`/.../`) was reported as a scan error and left untouched. The app was not
  compiled.

> Not yet verified: the model-in-the-loop workflow (the evals in
> `skills/remove-comments/evals/evals.json` haven't been run), running the
> skill inside Claude Code, Codex or Antigravity sessions, the PR checkout
> path and the babysit integration, the formatter and test steps, languages
> and real-world files beyond the fixtures and corpus above (the scanner
> fails closed on syntax it can't decide, but it's a heuristic lexer, not a
> parser), and large repos.

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
