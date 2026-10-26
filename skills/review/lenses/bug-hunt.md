# Lens: bug-hunt

Find inputs and paths where the changed code does the wrong thing. This lens
runs on every area, so it's the general correctness pass: logic, edge cases,
error handling, and tests that miss the point.

## Look for

- **Logic**: inverted conditions, off-by-one in ranges, slices and loops, wrong
  operator precedence, `=`/`==`/`===` mixups, short-circuit side effects, a
  wrong variable used (copy-paste from the neighbouring branch), unreachable or
  dead branches, `switch` fall-through, missing `default`/`else`.
- **Edge cases**: empty, null/undefined/None, zero, negative, very large,
  unicode, whitespace, duplicates, a single element; timezones and DST, leap
  days, locale-dependent parsing and formatting; float comparison and rounding.
- **Error handling**: swallowed exceptions (`catch {}`, `except: pass`); errors
  logged but execution continues with bad state; resources not closed on error
  (files, connections, locks); wrong error type, so callers' handling misses it;
  retries on errors that can't succeed.
- **Contracts between changed and unchanged code**: callers not updated for a
  new parameter, return shape or exception; changed defaults that existing
  callers rely on; type assertions and casts that hide mismatches.
- **State**: stale or missing updates (UI state, cache, memoization dependency
  arrays); mutation of shared inputs; ordering assumptions about maps and sets.
- **Tests**: tests that can't fail (asserting on mocks, the wrong variable, no
  assertion); snapshots updated blindly; new branches with no test; tests that
  depend on order or time.
- **Leftovers**: debug prints, commented-out code, TODOs that hide unfinished
  behaviour, feature flags left on, hard-coded test values in prod paths.

## Gather evidence

1. Quote the line, and give a **concrete input or call** with the actual versus
   expected result: "`parseRange('5-')` returns `[5, NaN]`; callers on line 88
   index with it → `undefined`".
2. For contract breaks, `grep` the callers and quote one that's now wrong.
3. For test gaps, name the branch and the input that would exercise it.
4. Don't run the test suite or build if that writes into the repo (build
   output, caches, snapshots). Reasoning with a concrete input is enough evidence.

## Not in scope

Leave findings that belong to a specialist lens to that lens: attacker-driven
issues (security), persisted-data corruption and migrations (data-integrity),
interleavings (concurrency), performance (scalability), API taste (design),
structure (architecture). If a specialist lens isn't running on this area and
you find a serious problem in its domain, report it anyway and say so in the claim.

## False-positive traps

- Types, schemas or validation upstream may already rule out the "bad" input.
  Check the caller and the type before reporting null-handling bugs.
- Language semantics: JS `?.`/`??`, Python truthiness, Go zero values, Rust
  `Option`. Make sure the bug exists in this language.
- Pre-existing bugs the diff doesn't touch or make reachable are out of scope.
- Style differences (early return versus nested `if`) aren't bugs.
- A test that looks redundant may be pinning a regression. Check `git log` for it.

## Severity

- **blocker**: a core flow breaks for common inputs, crashes at startup, or
  fails silently in a way that loses user work.
- **major**: wrong results for plausible inputs; an error path that leaves bad
  state; a caller left broken by a contract change.
- **minor**: rare edge cases with limited impact; missing tests for new branches.
- **nit**: leftovers and clarity issues with no behavioural effect.
