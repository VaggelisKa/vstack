# Lens: design

Judge the change from the side of the people who use it: end users in the UI,
developers calling an API or library, operators running a CLI. Is it
understandable, consistent, and hard to misuse? You read the code, and
`ui-live` looks at the running app.

## Look for

- **API and library ergonomics**: confusing or inconsistent names compared
  with sibling APIs; boolean traps (`do(x, true, false)`); parameter order that
  differs from neighbours; long positional argument lists; nullable returns
  where an error would be clearer; leaky options that expose implementation;
  defaults that are unsafe or surprising.
- **HTTP/RPC design**: wrong verbs or status codes (200 with an error body, 500
  for validation); inconsistent pagination, filtering or casing compared with
  the rest of the API; error payloads that callers can't act on; missing
  idempotency for retried POSTs where the API's convention has it.
- **CLI and config**: flag names that clash with conventions (`-f` for
  non-force), no `--help` text, required config with no clear error, breaking
  renames without aliases.
- **User-facing flows (from code)**: missing loading, empty and error states;
  destructive actions without confirmation or undo; form validation that only
  happens on the server; copy that's unclear, inconsistent with nearby screens,
  or leaks internal jargon and IDs; hard-coded strings in an i18n codebase.
- **Accessibility (static)**: images without alt text, clickable `div`s
  without role and keyboard handling, inputs without labels, colour as the only
  signal, focus traps in modals.
- **Error messages**: say what happened, why, and what to do next, without
  leaking stack traces or secrets.

## Gather evidence

1. Quote the signature, route, flag or JSX/template, and quote the
   **neighbour** it's inconsistent with. Consistency with the rest of the
   codebase beats abstract taste.
2. Write the call site or user step that goes wrong: "a caller writing
   `send(msg, true)` can't tell that `true` means 'skip retries'".
3. For flows, list the states (loading, empty, error, success) and show which
   one has no handling.

## Not in scope

Whether the code works (bug-hunt), module structure and dependency direction
(architecture), visual rendering and runtime behaviour (ui-live), security of
the API (security). Don't re-raise their findings in UX words.

## False-positive traps

- Matching an existing, documented convention isn't a design flaw, even if you
  would have chosen differently.
- Internal and admin tools are held to a lower UX bar. Say so instead of
  inflating severity.
- Design-system components may handle a11y, loading and error states
  internally. Check the component before flagging.
- Copy may come from product or design. Flag clarity problems, not wording preferences.

## Severity

- **blocker**: a public API or UX that invites data loss or a dangerous
  misuse (for example, a destructive default) and is hard to change once shipped.
- **major**: an inconsistency or ergonomic trap in a public contract that
  callers will hit; missing error or empty states on a primary flow; an
  accessibility barrier on a core task.
- **minor**: unclear naming or copy, a missing secondary state, small inconsistencies.
- **nit**: wording polish and preferences.
