# Lens: concurrency

Find interleavings that break the change: races, deadlocks, lost updates,
double execution, starvation and leaked tasks. Think in terms of two copies of
this code running at once, on two threads, two requests, two pods or two retries.

## Look for

- **Shared mutable state**: module-level variables, singletons, class
  attributes, static fields or caches mutated from request handlers, goroutines
  or async tasks without synchronization.
- **Check-then-act**: `if not exists: create`, `if balance >= x: debit`, or
  lazy init that two callers can both pass before either acts.
- **Locks**: inconsistent lock order (deadlock), locks held across `await`, I/O
  or callbacks; locks not released on error paths; a lock in process memory
  guarding a resource shared across processes or hosts.
- **Async**: missing `await` (floating promises, fire-and-forget tasks whose
  errors vanish); blocking calls inside the event loop (`time.sleep`, sync I/O,
  CPU-heavy loops); `Promise.all` with no limit on fan-out; cancellation and
  timeouts that leave state half updated.
- **Go/JVM/Rust specifics**: maps written from several goroutines; loop
  variable capture in goroutines and closures (check the Go version);
  unbuffered channels that can block forever; `WaitGroup.Add` inside the
  goroutine; `synchronized` on the wrong monitor; `Arc<Mutex<>>` held across
  `.await`.
- **Distributed**: at-least-once consumers without idempotency keys; retries
  around non-idempotent calls; cron/leader jobs that can run on two instances;
  distributed locks without fencing tokens or expiry handling.
- **UI concurrency**: stale closures, responses arriving out of order (the
  last request wins over the newest), effects without cleanup, double-submit.

## Red flags to grep

`global `, `static `, `threading`, `Lock(`, `Mutex`, `synchronized`, `go func`,
`async def`/`async function` without a matching `await`, `.then(` without return
or catch, `setTimeout`/`setInterval`, `Promise.all`, `asyncio.create_task`,
`ExecutorService`, `@Scheduled`, `retry`, `lazy`, `once`, `sync.Map`,
`useEffect` without cleanup.

## Gather evidence

1. Quote the shared state and every place it is read or written.
2. Write out the **interleaving** step by step: "T1 reads count=5 (line 40) →
   T2 reads count=5 → T1 writes 6 → T2 writes 6: one increment lost".
3. Show that concurrency actually happens here: the handler runs per request on
   a thread pool, the consumer has concurrency > 1, the job runs on N replicas.
   Quote the config or framework fact.
4. For deadlocks, give the two lock orders with line references.

## Not in scope

Data-model consequences such as missing DB constraints or transactions
(data-integrity, though mention it when the race is the cause), throughput
(scalability), security impact beyond the race itself (security).

## False-positive traps

- Single-threaded runtimes. Node and browser JS can't interleave between
  `await`s, so only the code across an `await` can race. Python asyncio is the
  same, but threads and the GIL are another matter.
- Request-scoped objects aren't shared even when they look global
  (dependency-injection scopes, `contextvars`, thread-locals).
- Immutable data and copy-on-write structures don't need locks.
- Idempotency handled one layer down (unique constraints, upserts, queue dedup).
  Check before reporting double execution.
- Go 1.22+ fixed loop-variable capture. Check `go.mod`.

## Severity

- **blocker**: a race or deadlock on a hot path that corrupts money or data, or
  hangs the service.
- **major**: a race reachable under normal load with user-visible effect; lost
  errors from floating promises on important paths; blocking the event loop in
  request handling.
- **minor**: races with benign or self-healing results; unbounded fan-out
  under low traffic.
- **nit**: style of synchronization primitives.
