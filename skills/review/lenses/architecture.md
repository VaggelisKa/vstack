# Lens: architecture

Judge whether the change fits the system's structure, or quietly bends it in
ways that will cost later: wrong layer, leaked internals, new coupling,
duplicated mechanisms, or a public contract that's hard to take back.

## Look for

- **Boundaries and dependency direction**: domain code importing from
  web/framework/infra layers; lower layers importing higher ones; new cyclic
  imports; reaching into another module's internals (private paths,
  `internal/`, underscore names) instead of its public API.
- **Public API and contracts**: exported functions, types, endpoints, events,
  CLI flags or config keys that were added, changed or removed. Breaking changes
  without versioning, deprecation or a migration path; internal types leaking
  into public signatures; new required parameters on widely used functions.
- **Duplication of mechanisms**: a second HTTP client, retry helper, config
  loader, logger, cache or validation scheme next to an existing one. Quote the
  existing one.
- **Responsibility**: god objects growing further; business rules in
  controllers, views or SQL; config scattered instead of centralised; feature
  flags woven through many layers.
- **New dependencies**: is the library needed, maintained and license
  compatible, and is the size proportionate? Is it pinned? Does it duplicate a
  dependency the project already has?
- **Testability and seams**: hard-wired singletons, I/O in constructors,
  time and randomness without injection, where the change makes testing
  noticeably harder.
- **Reversibility**: how costly the decision is to undo. A data format or a
  public endpoint deserves more scrutiny than an internal helper.

## Red flags

Imports that cross layers (`from app.web` inside `domain/`), new
`utils`/`helpers`/`common` dumping grounds, `export *`, new top-level packages,
`package.json`/`go.mod`/`pyproject` dependency additions, changes to
OpenAPI/proto/GraphQL schemas, `internal`/`_private` imports from outside.

## Gather evidence

1. Quote the import or call that crosses the boundary, and show the
   convention: other modules that go through the intended API, or a documented
   architecture rule (ADR, README, lint config such as dependency-cruiser,
   import-linter, ArchUnit).
2. For duplication, quote both mechanisms side by side.
3. For breaking API changes, list the callers (`grep` them) or the external
   consumers, with paths.
4. Say what the consequence is concretely: "every new payment provider will
   now need edits in 3 layers", not "this is not clean".

## Not in scope

Line-level bugs (bug-hunt), naming and ergonomics of a single API (design),
code-level naming, idioms and style conventions (consistency), performance (scalability), and personal taste in patterns when the codebase
has no established convention.

## False-positive traps

- The codebase may deliberately use the "violating" pattern everywhere. If
  it's the local convention, don't flag it as a regression introduced here.
- Small scripts, prototypes and tests don't need layered architecture.
- A duplicated helper of a few lines can be cheaper than a shared abstraction.
  Flag duplication only when the mechanisms will drift apart in ways that matter.
- An "internal" API marked as such in the docs isn't a public contract.

## Severity

- **blocker**: an irreversible public contract or data format that's clearly
  wrong; a breaking change to external consumers with no path forward.
- **major**: a new dependency cycle or layer violation that others will copy; a
  duplicated core mechanism; a breaking internal API change with many callers
  left inconsistent.
- **minor**: misplaced code, missing seams, slightly leaky abstractions.
- **nit**: file and module naming or placement preferences.
