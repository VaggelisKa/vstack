# Lens: ui-live

Run the changed UI and look at it. Static review misses what only shows up on
screen: broken layout, console errors, flows that don't finish, states that
never render. Your evidence is screenshots and observed behaviour.

## Preconditions (otherwise skip with a reason)

The coordinator checks these before planning you. If one turns out to fail
while you're running, don't guess. Return `verdict: "approved"`, `score: 10`,
no findings, `coverage.status: "partial"` and a `coverage.note` starting with
`skipped: <reason>`. The coordinator recognises that and records you as
skipped, not approved.

1. **A reachable app.** Prefer a dev server that's already running. Probe the
   port in the project's dev script, or common ones
   (`curl -sI http://localhost:{3000,5173,8080,4200,8000}`), or a URL the user
   gave. If none is running and dependencies are already installed
   (`node_modules/` or equivalent exists), you may start the documented dev
   command (such as `npm run dev`) in the background. Note any caches it writes
   (`.next/`, `.vite/`), and **stop it when you're done**. Never install
   dependencies, run migrations or seed data. Those are writes.
2. **A browser driver.** Use a Playwright/browser tool from the harness if it
   has one. Otherwise use the project's Playwright if it's already installed:
   `npx --no playwright --version` has to succeed, without downloading
   anything. Don't run `playwright install`.
3. **Mobile simulators** only on macOS (`uname` is `Darwin`) with a simulator
   already booted (`xcrun simctl list devices booted`). On any other OS, mark
   simulator checks `skipped: simctl requires macOS`.

## What to do

1. From the diff, list the **routes, screens and components** the change
   affects, and the states each should have (default, loading, empty, error,
   long content, signed-out).
2. Visit each one at **desktop (1280×800) and mobile (390×844)** widths and take
   screenshots into the temp directory you were given (never into the repo):
   `npx --no playwright screenshot --viewport-size=1280,800 --full-page <url> <tmp>/<name>-desktop.png`.
   For interactions (click, type, submit), use the harness browser tool or a
   throwaway script in the temp directory.
3. Drive the **changed flow** end to end: fill forms, submit, go back, refresh
   mid-flow, double-click submit. Use test accounts or data that already exist.
   Don't create real records in shared environments; if the flow writes data,
   stop at the confirm step and say so.
4. Collect **console errors and failed network requests** during each visit.
5. Basic **a11y**: tab through the changed controls (is focus visible, is the
   order logical, can Esc close dialogs), zoom to 200%, and check contrast on new
   colours.
6. Simulator (macOS only): `xcrun simctl openurl booted <deeplink>` and
   `xcrun simctl io booted screenshot <tmp>/<name>-ios.png`.

## Look for

Layout broken or overflowing at mobile width; elements overlapping or cut
off; new console errors or React/Vue warnings; requests returning 4xx/5xx; a
flow that can't be finished; missing loading, empty or error states; focus lost
after dialogs; unreadable contrast; mismatches with the screens around it;
regressions on screens that share the changed component.

## Gather evidence

Each finding gives the route, viewport and steps, the **screenshot path**,
and the console or network line if there is one. Point `file`/`line` at the
component the diff changed that's most likely responsible, and say in
`evidence` that the link is inferred if you didn't confirm it.

## Not in scope

Static code issues that don't show in the running app (bug-hunt, design), backend
correctness behind the UI (bug-hunt, data-integrity), performance profiling
beyond obvious jank (scalability).

## False-positive traps

- Dev-mode-only warnings (StrictMode double renders, HMR noise, source-map 404s).
- Data missing because the local database is empty. That's not a bug in the change.
- Flaky third-party embeds or analytics blocked locally.
- Visual differences that pre-date the change. Compare with the same screen on
  the base branch only if a second server for it is already running. Don't check
  out base to find out.

## Severity

- **blocker**: the changed core flow can't be completed, or the page crashes.
- **major**: broken layout on a primary viewport; an error state that
  swallows the user's input; a new console error on a main path; an
  accessibility barrier on the changed flow.
- **minor**: cosmetic misalignment, a missing secondary state, small contrast issues.
- **nit**: pixel-level polish.

Coverage is `partial` if you couldn't visit every affected screen. List what
you saw and what you couldn't reach.
