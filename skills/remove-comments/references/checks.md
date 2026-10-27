# Formatter and checks after apply

## Formatter (only if the repo has one configured)

Run it on the touched files only, never the whole repo: reformatting
untouched code is the unrelated churn this skill exists to avoid. Detect it
from the repo's own config; don't install anything.

| Look for | Run on the touched files |
|---|---|
| `biome.json` / `biome.jsonc` | `npx biome format --write <files>` |
| `.prettierrc*`, `prettier.config.*`, or `"prettier"` in package.json | `npx prettier --write <files>` |
| a `format` script in package.json that takes paths | that script with the paths |
| `[tool.ruff]` in pyproject.toml / `ruff.toml` | `ruff format <files>` |
| `[tool.black]` in pyproject.toml | `black <files>` |
| `go.mod` | `gofmt -w <files>` |
| `Cargo.toml` (+ `rustfmt.toml` optional) | `rustfmt <files>` |
| `.clang-format` | `clang-format -i <files>` |
| `.editorconfig` + `shfmt` on PATH, for shell | `shfmt -w <files>` |

No config, no formatter. If the tool isn't installed, skip it and say so.

Then run verify again:

```bash
python3 -I "$RC/comments.py" verify --list "$TMP/comments.json"
```

If it fails, the formatter changed more than whitespace (it re-wrapped or
re-quoted code). Put back the post-apply content and tell the user the
formatter was not applied:

```bash
python3 -I "$RC/comments.py" restore --list "$TMP/comments.json" --applied
```

## Tests, lint, type check

Find how the repo runs them the same way babysit does: README, CONTRIBUTING,
package.json scripts, Makefile, pyproject/tox/nox, CI workflow files. Run the
cheapest set that covers the touched files (lint and type check on those
files, the test suite or the tests next to them).

Report each result honestly: command, pass or fail, and the first error line
on failure. Removing comments can legitimately break a check: a deleted
directive (impossible through apply, but a hand edit would do it), a doctest,
a lint rule that requires doc comments on exported symbols.

If something fails:

1. Don't edit code to make it pass.
2. If the same check is cheap to run on the pre-change state, find out
   whether it was already failing: `restore --list` puts the snapshot back,
   run the check, then `restore --list ... --applied` returns to the edited
   state. Never use `git stash` or checkout for this; they can lose the
   user's uncommitted work.
3. Otherwise report the failure and stop. If it plausibly comes from a
   deleted comment (a doctest, a doc lint rule), say which one and offer to
   restore the snapshot.
