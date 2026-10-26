# Lens: data-integrity

Find ways this change could lose, corrupt, duplicate or orphan stored data, or
leave it in a state the code can't read back. The question every time: if this
runs against production data, and maybe fails halfway, what's in the database
afterwards?

## Look for

- **Migrations**: destructive steps (`DROP`, `ALTER ... TYPE`, renames) that old
  code still running mid-deploy depends on; `NOT NULL` added without a default or
  backfill; backfills that load the whole table into memory or run in one giant
  transaction; locks on large tables (adding an index without `CONCURRENTLY`,
  rewriting a column); no down migration, or one that loses data; migrations that
  depend on application code that will change later.
- **Deploy ordering**: new code reading a column the migration hasn't created
  yet, or old code writing a column the migration removed. Expand/contract has to
  be safe in both directions while a rollout is half done.
- **Writes**: multi-step writes without a transaction; a transaction that spans
  external calls (HTTP, queue publish) so a rollback can't undo them; missing
  unique constraints that the code assumes; upserts keyed on non-unique fields;
  soft-delete filters forgotten in new queries; cascade deletes wider than intended.
- **Read-modify-write** without optimistic locking or `SELECT ... FOR UPDATE`,
  where two writers can overwrite each other (flag it here as data loss, the
  concurrency lens covers the mechanism).
- **Serialization and formats**: renamed or removed fields in persisted JSON,
  events or protobufs without a compatibility path; enum values reordered or
  reused; timezone-naive timestamps; float for money; truncation (string
  lengths, int32 IDs).
- **Caches and derived data** that aren't invalidated when the source of
  truth changes; denormalized counters updated on one path only.
- **Idempotency** of jobs, webhooks and consumers that will be retried:
  double-charging, double-inserting.

## Red flags to grep

`DROP `, `ALTER TABLE`, `RENAME`, `NOT NULL`, `DELETE FROM` / `.delete(` without a
narrow `WHERE`, `UPDATE` without `WHERE`, `truncate`, `on_delete=CASCADE`,
`ON DELETE CASCADE`, `float`/`double` near `price|amount|balance`, `bulk_`,
`save()` inside loops, `transaction`/`atomic` (and their absence), `upsert`,
`ON CONFLICT`.

## Gather evidence

1. Quote the migration or write statement and say what state the data is in
   **before**, **during** (partial failure, half-done deploy) and **after**.
2. For deploy-ordering issues, name the two code versions and the column, and
   show the failing read or write.
3. For missing constraints, quote the code that assumes uniqueness or presence,
   and the schema (or its absence) that doesn't enforce it.
4. Give a concrete sequence: "request A writes X, the job fails after line 88,
   retry writes X again → two rows".

## Not in scope

Attacker-driven tampering (security), query speed and index *performance*
(scalability, though a table-locking migration belongs here), API naming
(design), general logic bugs that don't reach stored data (bug-hunt).

## False-positive traps

- The framework wraps each request or job in a transaction (Django
  `ATOMIC_REQUESTS`, Rails callbacks, a unit-of-work). Check before calling a
  write non-atomic.
- A migration tool may add `CONCURRENTLY` or batching automatically, or the
  table may be known to be small. Say what you assumed.
- A column drop in a contract-phase migration is fine if an earlier release
  already stopped using it. Check the history (`git-read.sh log --stat`, then
  `git-read.sh show <sha>:<path>`) and search the head for remaining uses.
- Test databases and seed files aren't production data.

## Severity

- **blocker**: irreversible loss or corruption on a normal path; a migration
  that breaks the running version mid-deploy; double money movement.
- **major**: loss or duplication under plausible failure or retry; a missing
  constraint the code depends on; a large-table lock in a migration.
- **minor**: a missing down migration on additive changes; weak cache
  invalidation for low-impact data.
- **nit**: naming in migrations.
