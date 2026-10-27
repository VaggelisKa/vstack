# review: what's tested

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

