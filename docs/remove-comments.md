# remove-comments: what's tested

`/vstack:remove-comments [PR|path...] [--all] [--dry-run] [--report PATH]`
(`$remove-comments` in Codex).

The model decides which comments go; `skills/remove-comments/scripts/comments.py`
lists them, shows them to the model inside untrusted blocks, applies the
decisions only inside comment spans, and verifies that the code tokens didn't
change, cross-checked by tools that don't share its scanner and come from
outside the repo (no cross-check runs the repo's own code; the formatter,
lint and tests the skill runs afterwards do, which is why it skips them on
fork PRs). Its tests are in
`skills/remove-comments/scripts/tests/` and use only temp dirs and temp git
repos:

```bash
python3 -m unittest discover -s skills/remove-comments/scripts/tests
# or: cd skills/remove-comments/scripts && python3 -m pytest -q tests
```

That runs 188 tests in about 70 seconds. With gcc, node and gofmt installed
and nothing else set, 180 pass and 8 skip. Seven of the skips are tests whose
tool outside the repo isn't set up: four (the TypeScript cross-check, its runs
in chunks, a trusted `RC_TS_ROOT` being used, and the JS fuzz) need
`RC_TS_ROOT` naming a directory whose `node_modules` has `typescript`, the
Sass oracle needs `RC_SASS_ROOT` (or `RC_TS_ROOT`) whose `node_modules` has
`sass`, and the two Less oracles need `RC_LESS_ROOT` (or `RC_TS_ROOT`) whose
`node_modules` has `less`. The eighth checks that the test helper removes
unreadable directories and skips when run as root, which ignores
permissions.

```bash
RC_TS_ROOT=/path/with/node_modules RC_SASS_ROOT=/path/with/node_modules \
  RC_LESS_ROOT=/path/with/node_modules \
  python3 -m unittest discover -s skills/remove-comments/scripts/tests
```

With all three set (TypeScript 6.0.2, sass 1.106.1, less 4.9.1, node 22),
187 pass and only the root-only test skips (run as root). The C tests used
gcc 13 in C mode (`-std=gnu2x`, no C++ front end installed); the Go test used
gofmt from Go 1.24. Tests whose tool is missing skip.

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
  conditional comments, comments in attributes and `<script>`, comments
  inside `<pre>` (protected); BOM and CRLF offsets; unterminated strings,
  templates, regexes, heredocs and bare CR as errors; a JS `/` after the `)`
  of an `if`/`while`/`for`/`for await` head read as a regex
  (`if (p) /\//.test(s)`), a `/` after `}` scanned when both readings agree
  and an error when they don't (`function f(){}\n/\//.test(s)`), U+2028,
  U+2029 and CRLF ending a JS `//` comment; TypeScript's non-null assertion
  before a division (`x = a! / b; /* c // d */` followed by a regex literal
  with `//` and `/*`, in `.ts` and `.tsx`; `a!.b / c`, `a!! / b`,
  `f()! / 2`, `a!=b`, `a !== b / c // d`) while a prefix `!` still starts a
  regex (`if (!/re/.test(s))`, in JS always), a line break before `!`
  making it prefix, `{}! / a` and type arguments before a `/`
  (`a as Array<number> / 2`) scanned right or refused, never misread, and
  `=>` or a comparison before a regex; the logical lines of C
  preprocessor directives (backslash continuations, comments running onto the
  next line, `#if 0` bodies);
- protected detection: 35 directive cases across the languages (eslint,
  `@ts-expect-error`, prettier, biome, istanbul, webpack magic comments,
  `#__PURE__`, JSX pragmas, triple-slash, test-environment docblocks, `type:
  ignore`, `noqa`, pylint, pragma, fmt, shellcheck, `//go:build`,
  `//go:embed`, `//nolint`, NOLINT, fallthrough, Rust `SAFETY:`,
  yaml-language-server, renovate, taplo, hadolint, stylelint, modelines),
  with and without a reason; prose that mentions a tool but isn't a
  directive; legal headers after a shebang, encoding cookie or directive;
  PEP 723 blocks and cgo preambles; the hint flags, including comments that
  address the agent; 15 phrasings aimed at the agent (look-alike letters,
  zero-width and full-width characters, German, "Dear reviewer", "TOOLS:
  ...") of which the flag catches some and misses others, all listed, deleted
  or kept like any other comment;
- `apply`: own-line, trailing, inline and multi-line block deletes, tokens
  that must not merge (`a/**/-/**/-b`), two comments on one line, JSX
  children, HTML inline comments, a shell comment after a `\` continuation,
  Python comments and docstrings, a docstring followed by `; code`; a
  multi-line comment in a `#define` (also on a continuation line, and two of
  them) replaced by one space so the directive stays one line; CSS
  comments between tokens (`.a/* c */.b` becomes `.a.b`, not `.a .b`;
  `#id/**/.c`, `a/**/:hover`, a multi-line one) and `1/**/.5px`, which no
  comment-free text can keep as two tokens, refused; SCSS comments, which
  Sass reads as whitespace, leaving a space (13 cases: `.a/**/.b` to
  `.a .b`, `&/**/.e`, `f/**/:hover`, `a/**/>/**/.b`, `1px/**/2px`,
  `1px+/**/2px`, `1/**/.5px`, two comments in a row, a multi-line one), the
  comment before a negative number (`$x/**/-1`, a subtraction, which
  `$x -1` and `$x-1` aren't) protected, and, with `RC_SASS_ROOT`, Sass
  compiling every case to the same CSS before and after; Less comments
  (below); the cross-checks run before writing, so an edit a fooled scanner passed (`edit` and
  `compare` patched) is refused when Python's `ast` or `node --check`
  disagrees and no file changes; an escaped space before
  a shell comment kept; a JS `//` comment ending at U+2028; blank-line
  collapsing (between blocks, after an opener, before a closer, at file start
  and end); BOM, CRLF and a missing final newline preserved; rewrites
  (indentation kept, a multi-line run, a CRLF file, a docstring) and 15
  invalid rewrites (two with U+2028/U+2029) refused with no file changed; missing, unknown, protected and reasonless decisions refused; a
  docstring that is the only statement can't be deleted; a stale file skipped,
  a second apply reporting "file changed since list", a file changed while
  the cross-checks ran (after planning, before writing) left as the other
  writer left it and reported "file changed while apply was checking it; not
  written" while the other file is written; the summary counting edited files
  as cross-checked ok, syntax-only (weak) and no independent check, as verify
  does (Python and Rust: 1, 0, 1), with and without `--dry-run`; `--dry-run`; `restore`
  to the snapshot and back to the applied state; the `report` sections;
- `verify`: comment and whitespace changes pass in JS, Python, YAML and
  shell; it exits 1 when a code token changes, string content changes, a `#`
  inside a string is removed, code is uncommented, a protected directive is
  removed, Python indentation, YAML indentation or a shell continuation
  changes, the Python AST changes, code moves into or out of a C directive
  (the `#define SUM` newline), or CSS gains or loses whitespace between two
  tokens (`.a.b` vs `.a .b` both ways, `1.5px`, `1 .5px`, `ab`), where an
  SCSS comment counts as whitespace (`.a/**/.b` to `.a.b` fails, to `.a .b`
  passes; CSS the other way round) and removing a protected Less comment
  fails (`.a/* c */.b` to `.a .b`, `.a-b /**/.c` to `.a-b.c`); docstring
  changes are reported separately and fail only with `--strict`; a scan
  error exits 2; the snapshot mode vs `--base HEAD`;
- `verify` cross-checks: Python `ast` ok; `gcc -E` ok on a real edit,
  "differs" on the hand-made `#define` newline, "skipped" when the before
  file doesn't preprocess (also when it includes a header that sits next to
  it in the repo); TypeScript's emitter ok on a TSX edit and "differs" on a
  dropped statement and on a syntax error (with `RC_TS_ROOT`); `node
  --check` on `.js` and `gofmt -e` reported as `weak` (syntax only), not
  `ok`; the summary line counting edited files as cross-checked ok,
  syntax-only (weak) and no independent check (Python, JS and Rust files:
  1, 1, 1; with `--no-cross-check` 0, 0, 3); with `PATH` emptied, C, TS, Go
  and Rust files all report "skipped" and verify still exits 0;
  `--no-cross-check`; TypeScript runs on at most 200 files at a time (with
  the chunk size set to 2, five files take three runs of 2, 2 and 1 files and
  all five get a status, with `RC_TS_ROOT` too); a run that times out or is
  killed reports "typescript check didn't finish: timed out, was killed, or
  wrote too much" for its own files only, and so does finding typescript,
  instead of "isn't available" or "RC_TS_ROOT has no typescript";
- repo code never runs: in a temp repo with a committed
  `node_modules/typescript` symlink to repo code that writes a marker file
  when loaded, `list`, `apply` and `verify` leave no marker, verify reports
  "typescript: skipped (typescript isn't available outside the repo; set
  RC_TS_ROOT to a trusted install)" and never `typescript: ok`; the same with
  the repo's `bin/` (shims for node, npm, gcc, cc, clang, gofmt) and a
  relative directory first on `PATH`, `NODE_OPTIONS=--require` a repo file
  and `CPATH` set to the repo; `RC_TS_ROOT` inside the repo refused ("RC_TS_ROOT
  is inside the repo"), and an `RC_TS_ROOT` outside it whose `typescript` is
  a symlink back into the repo refused; an `RC_TS_ROOT` outside the repo used
  (`typescript: ok`); the environment checkers get: only `HOME`, locale,
  `TERM`, temp-dir and npm-prefix variables (and their Windows
  counterparts), so `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD`, `GCC_*`,
  `CPATH`, `GOFLAGS`, `PYTHONPATH`, `BASH_ENV`, `RC_TS_ROOT` and any
  unknown variable are dropped, checked both on the dict and in a child's
  environment, PATH reduced to absolute directories outside the repo, and
  `npm root -g` giving the same answer as with the full environment;
- checker limits: each checker runs in a session of its own with
  `RLIMIT_AS` 4 GiB, `RLIMIT_CPU` 30 s and `RLIMIT_FSIZE` 16 MiB on its output
  files; a C file with `#include "/dev/zero"` through verify's gcc
  cross-check ends in about a second as "skipped", never "ok", with the
  children's peak memory under the cap; a tool that starts a background
  `sleep` and then sleeps itself returns nothing after a 1 s timeout and the
  background child is gone; a 5 GiB allocation fails with `MemoryError`;
  output over the cap is refused; a cap the system refuses (`setrlimit`
  raising) is left out instead of failing the run; stdin still reaches the
  tool;
- Less: a comment lessc could read differently stays protected: one that
  touches code (`.a-b /**/.c`, which lessc compiles to `.a-b.c`; `1 /**/-2`,
  which is `-1`; `@x -/**/ 1` and `@x -// c` + newline + `1`, which are lists
  where `@x - 1` is 2; a comment at the start of a line before `-2` or `.c`;
  `.@{n} /**/.c`; a multi-line one), one inside parentheses or brackets
  (`:not( // c` + newline + `.b)`, which lessc copies into the CSS;
  `:extend(.b all /**/ )`, which then extends nothing) and one between an
  `@name` and its `:` or `(` (`@x /**/ : 3` is an at-rule); a `;` or `{`
  inside an earlier comment is no statement start (`1 // note;` + newline +
  `/**/-2` and `.a // x;` + newline + `/**/.b` stay protected); a `//`
  comment and a `/* */` one right after it in a value both stay (`@x: 3 //
  c` + newline + `/**/ ;` is 3, `3 /**/` without the `//` one); delete and
  rewrite of them are refused through the CLI; own-line comments, trailing
  ones after whitespace, `.a /* c */ {`, comments between declarations
  (also `x:y;/* c */z:w`), after `{`, at the start of the file, `//`
  comments after whitespace, runs of comments after `;` or `{` (`// one`,
  `// two`, then `/* three */.b`; `/* a */ /* b */.b`) and an own-line
  `/**/` after `y; // z;` are deleted (20 golden cases); with
  `RC_LESS_ROOT`, lessc compiles every golden case to the same CSS before
  and after, every protected case to different CSS without its comment(s),
  and a fuzz inserting eleven comment forms (two of them a `// c;` or `//
  c{` line before a `/**/`) at every position of nine stylesheets (about
  2,700 edits that delete something) finds no difference (comments lessc
  copies through and whitespace are left out of the comparison);
- seeded fuzz (fixed seeds, small N): random C lines with comments, every
  comment deleted, compared with `gcc -E -P -dD` output whenever verify's token
  check passes (the same test finds 2 mismatches in the previous version of
  the scripts); random JS/TS lines compared with TypeScript's emitter (with
  `RC_TS_ROOT`);
- untrusted text: `show` prints one `<untrusted-finding>` block per comment
  via the review skill's `wrap-untrusted.py`, JSON-escapes a file name with a
  newline and an ANSI escape, spells control and bidi characters as escapes,
  escapes a `</untrusted-finding>` in a comment, reports the injection
  attempt (the comment's own text vs the lines next to it) and records it for
  the report's Needs a human list; `--ids` and `--file`; exit 2 when
  `wrap-untrusted.py` is missing; report tables escape `|`, backticks,
  newlines and untrusted tags;
- `precheck`: a clean tree and ignored-only files pass; an untracked file, an
  untracked file in a new directory, a staged change, a modified file, a
  hostile file name (escaped), a merge (a `MERGE_HEAD` and a real
  conflicted merge), a rebase, a cherry-pick and running outside a repo exit
  2;
- file names: a name that isn't valid UTF-8 (`\xff.py`) is skipped with a
  reason in diff and paths mode and by `verify --base`, the listing is
  ASCII-only JSON replaced atomically, and `apply`, `verify`, `report` and
  both `restore` modes cope with the skipped entry and print the hostile name
  escaped;
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

Also run by hand (not part of the test suite), with the first version of
the scripts and not repeated since:

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
> path (`gh pr view`'s `isCrossRepository` and the fork-PR skip of the
> formatter and tests are instructions to the model, not code) and the
> babysit integration, the formatter and test steps, the C cross-check in C++
> mode and with clang, languages and real-world files beyond the fixtures and
> corpus above (the scanner fails closed on syntax it can't decide, but it's
> a heuristic lexer, not a parser), and large repos.
>
> Known limitations:
>
> - A JS `/` right after `}` whose two readings (regex, division) find
>   different comments or strings on the line is a scan error, so the file
>   is left alone, even when the code is fine (`x = function () {} / 2; // d`).
> - A CSS comment between two tokens that would merge without it
>   (`1/**/.5px`, `a/**/b`) can't be deleted: apply refuses the run.
> - CSS verify treats whitespace next to `{ } ; , > ~`, after `(` and `:`,
>   and before `)` as insignificant; everywhere else gained or lost
>   whitespace fails it, including spaces a formatter adds around `+`.
> - HTML comments inside `<pre>` are protected instead of deleted, and verify
>   compares HTML as whitespace-separated words, so a whitespace change
>   inside `<pre>` or `<textarea>` made by hand or a formatter goes unnoticed.
> - Shell `let x=1<<2` reads as a heredoc and is a scan error (bash parses
>   it the same way).
> - A TypeScript `/` right after a `>` (type arguments or a comparison)
>   whose two readings find different comments or strings on the line is a
>   scan error too (`x = a as Array<number> / 2; // c`).
> - The C cross-check preprocesses each file on its own from stdin in an
>   empty directory, without the build's `-D` flags and with no include path
>   but the system's: a file that includes one of the repo's own headers is
>   "skipped", and code under an `#if` that is false without those flags
>   isn't compared. The JS cross-check needs a `typescript` outside the repo
>   (`RC_TS_ROOT` or the global npm root); the repo's own `node_modules` is
>   never used, so in a typical project without `RC_TS_ROOT`, `.ts`/`.tsx`/
>   `.jsx` are "skipped" and `.js` gets only `node --check`, reported as
>   `weak`. Go gets a `weak` `gofmt -e`; C#, Java, Kotlin, Swift, Rust,
>   Scala, Dart, shell, YAML, TOML, Dockerfile, CSS, SCSS, Less and HTML have
>   no cross-check (lessc and sass are only test oracles), so apply's
>   pre-write cross-check only guards Python, C, JS/TS and Go.
> - Less: comments that touch code, sit inside `( )` or `[ ]`, sit
>   between an `@name` and its `:` or `(`, or are a `//` comment and a
>   `/* */` one right after it outside a statement start are protected,
>   never deleted, even where lessc would compile the same CSS without them
>   (`a:not( /* c */ .b)`, `.m(1, // c` + newline + `2)`, `@x: // c` +
>   newline + `/**/ 3;`). The rule was derived from less 4.9's
>   parser (it tests the single character next to a token, and some
>   constructs are one regex) and checked with lessc 4.9.1 only, on files
>   that compile: a file lessc rejects can compile after a deletion.
>   Otherwise verify compares Less like CSS (the whitespace rule above).
> - The checkers (`gcc -E`, `node`, `npm root -g`, TypeScript, `gofmt`) run
>   with 4 GiB of address space, 30 s of CPU and wall time and 16 MiB of
>   output, in a session of their own that a timeout kills; a hostile file
>   can still use those 4 GiB (gcc on `#include "/dev/zero"` peaks near
>   2 GB for about a second). The caps are best effort, not a sandbox: the
>   tools run with the user's permissions and can read what the user can, and
>   on Windows only the timeout (which kills the tool, not what it started)
>   and the refusal of output over 16 MiB apply.
> - The C cross-check runs the preprocessor twice per file (before and
>   after), each run allowed the full 30 s, so a file whose includes are slow
>   to preprocess can add up to a minute to apply's pre-write check and to
>   verify, and many such files add up. TypeScript runs 200 files at a time,
>   each run with the same 30 s; a run that doesn't finish leaves its files
>   "skipped" (`.js` files fall back to `node --check`).
> - SCSS: a comment right before a negative number (`$x/**/-1`) is
>   protected, never deleted. Verify, as for CSS, treats whitespace after
>   `:` as insignificant, so a hand edit or formatter turning `a:/**/hover`
>   or `a: hover` into `a:hover` (a nested property vs a selector in Sass)
>   passes; apply itself always leaves the space. The Sass rules were checked
>   against dart-sass 1.106 only (and a 3,000-input fuzz, not part of the
>   suite: 1,441 inputs that compile, none differing).
> - `show` runs `wrap-untrusted.py` once per comment (8 at a time), about
>   30 ms each.

