# Scoring areas

Read this when you plan (SKILL.md, step 2). Score every area for complexity
and risk, and note the risk flags you saw: they decide which lenses run.

**Complexity C**: how hard the change is to reason about.

| C | Meaning |
|---|---|
| 0 | Docs, comments, config values, renames, formatting; under ~20 lines of logic |
| 1 | Simple, local logic; one file or a few obvious edits |
| 2 | Non-trivial control flow, new abstractions, several files, roughly 100-400 lines |
| 3 | Intricate: state machines, concurrency, algorithms, cross-cutting refactors, or over 400 lines that interact |

**Risk K**: how bad and how likely the damage is if the change is wrong.

| K | Meaning |
|---|---|
| 0 | No runtime effect: docs, tests only, dev tooling |
| 1 | Local and easy to revert: internal tools, code behind an off-by-default flag, logging |
| 2 | User-facing behaviour, public API or contracts, performance-sensitive paths, data reads |
| 3 | Authn/authz, crypto, secrets, money, PII, data writes, migrations or deletes, shared mutable state across threads or processes, prod infra or deploy config, anything irreversible |

**Risk flags** decide which reviewers run:

| Flag in the area | Lens |
|---|---|
| auth, sessions, tokens, crypto, input parsing, deserialization, shelling out, file paths from input, CORS/CSP, secrets | `security` |
| migrations, schema changes, writes/updates/deletes, transactions, caches of persisted data, serialization formats | `data-integrity` |
| locks, async/await, threads, goroutines, queues, retries, shared or global state, idempotency | `concurrency` |
| new modules, changed module boundaries, public API or exported types, dependency direction, new dependencies | `architecture` |
| UX flows, copy, API ergonomics, CLI flags, error messages, naming that callers see | `design` |
| UI files (components, styles, templates, screens) **and** a dev server you can run or reach | `ui-live` |
| hot paths, DB queries, loops over unbounded data, N+1, caching, payload sizes | `scalability` |
| every area | `bug-hunt` |
| every area except `generated` and C=0 areas | `consistency` |
