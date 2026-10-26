# Lens: scalability

Find what gets slow, expensive or falls over as data, traffic or tenants grow.
Estimate the growth: say what N is, how big it gets in production, and what
the cost is per unit of N.

## Look for

- **Queries**: N+1 (a query inside a loop or a lazy relation accessed per row);
  missing indexes for new `WHERE`/`ORDER BY`/`JOIN` columns; `SELECT *` on wide
  tables; unbounded queries without `LIMIT` or pagination; `OFFSET` pagination
  on big tables; `COUNT(*)` on hot paths; leading-wildcard `LIKE`; functions on
  indexed columns.
- **Algorithms**: nested loops over collections that grow (O(n²) membership
  checks with `list.includes` or `in list`); sorting or regex in tight loops;
  catastrophic regex backtracking on user input; repeated parsing or
  serialization of the same data.
- **Memory**: loading whole tables or files into memory; unbounded caches,
  maps or queues (no TTL or max size); accumulating results instead of
  streaming; large payloads built in memory.
- **I/O and network**: sequential remote calls that could be batched or run
  concurrently; calls inside loops; missing timeouts; chatty APIs; payloads
  without pagination or compression; synchronous work in the request path that
  belongs in a job.
- **Contention**: global locks or single hot rows (counters, "last_seen"
  updates) on every request; cache stampedes when a popular key expires.
- **Frontend**: re-renders of large lists without virtualization or
  memoization; big new bundles or dependencies on the critical path;
  unthrottled scroll and resize handlers; images without sizing or lazy-loading.
- **Cost**: per-request calls to paid APIs or LLMs, logs or metrics with
  unbounded cardinality (user IDs as metric labels).

## Gather evidence

1. Quote the loop or query and say what **N** is ("orders per customer:
   p99 ≈ 5k according to <source or assumption>").
2. Give the cost: "1 query per order → 5k queries per page load", or the
   complexity class with the realistic N.
3. Where you can, quote the schema or migration that shows the index is
   missing. You may run `EXPLAIN` only against a local or dev database you were
   pointed to. Never run anything that writes.
4. If N is unknown, say what you assumed. A finding that depends on N being
   large has to say why N will be large.

## Not in scope

Correctness bugs (bug-hunt), migrations that lock tables (data-integrity),
races (concurrency), and micro-optimizations with no measurable effect at
realistic N.

## False-positive traps

- Small, bounded N (enum values, config entries, a page of 20 items) makes
  O(n²) irrelevant.
- ORMs may already batch or eager-load (`select_related`, `includes`,
  DataLoader). Check the query construction.
- Admin pages, one-off scripts and cron jobs on small data are held to a
  different bar.
- Indexes may come from an earlier migration, a composite index, or the
  primary key. Search the schema before flagging.
- Premature optimization: don't flag readable code for theoretical speedups.

## Severity

- **blocker**: something that will clearly time out or exhaust memory at
  current production scale on a main path (an unbounded query on a large table
  per request).
- **major**: N+1 or O(n²) on a user-facing path with realistic N in the
  hundreds or more; a missing index on a new hot query; an unbounded cache.
- **minor**: inefficiency that only matters at future scale, or on cold paths.
- **nit**: micro-optimizations.
