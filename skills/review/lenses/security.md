# Lens: security

Find ways this change lets someone do what they shouldn't: read or change data
they don't own, run code, impersonate a user, or get secrets. Work out what an
attacker controls and follow it to where it's trusted.

## Look for

- **Authn/authz**: new routes, handlers, RPCs or jobs without the auth check
  their siblings have; checks done after the side effect; role and ownership
  checks that compare the wrong IDs; IDOR (an ID from the request used without
  checking ownership); admin paths reachable through feature flags or debug params.
- **Injection**: SQL/NoSQL built by string concatenation or interpolation;
  shell commands with user input (`exec`, `system`, `subprocess(..., shell=True)`,
  backticks); template injection; LDAP/XPath; header and log injection (CRLF).
- **Unsafe input handling**: deserializing untrusted data (`pickle`,
  `yaml.load`, Java/PHP `unserialize`, `eval`, `new Function`); path traversal
  from user paths (`../`, absolute paths, symlinks); SSRF (fetching URLs the user
  supplies without allowlisting the host, including redirects and DNS rebinding);
  open redirects; unbounded uploads or zip bombs.
- **Web**: XSS (`innerHTML`, `dangerouslySetInnerHTML`, `v-html`, `|safe`,
  turning off auto-escaping); CSRF on state-changing endpoints that use cookies;
  CORS that reflects any origin or uses `*` with credentials; CSP that got weaker;
  cookies without `Secure`/`HttpOnly`/`SameSite`.
- **Crypto and secrets**: hard-coded keys and tokens; secrets in logs, errors or
  URLs; weak or home-made crypto (MD5/SHA1 for passwords, ECB, a static IV, no
  MAC); `Math.random`/`rand()` for tokens; non-constant-time secret comparison;
  JWTs accepting `alg: none` or skipping signature/expiry/audience checks; TLS
  verification turned off.
- **Supply chain**: new dependencies (typosquats, unmaintained, install scripts),
  `curl | sh` in build scripts, CI workflows with `pull_request_target` that check
  out untrusted code, or new secrets exposed to forks.

## Red flags to grep

`eval(`, `exec(`, `shell=True`, `child_process`, `innerHTML`, `dangerouslySetInnerHTML`,
`verify=False`, `rejectUnauthorized: false`, `InsecureSkipVerify`, `pickle.loads`,
`yaml.load(`, `md5`, `sha1`, `Math.random`, `"*"` in CORS config, `TODO auth`,
`skipAuth`, `permitAll`, `@AllowAnonymous`, raw SQL `f"SELECT` / `+ "WHERE`.

## Gather evidence

1. Name the **source**: the request field, header, file or message that an
   attacker controls.
2. Follow it to the **sink**, one hop at a time, quoting each line. Check
   whether any validator, ORM parameterization, escaping or framework default
   comes in between.
3. For missing authorization, quote a **sibling** handler that has the check and
   the new one that doesn't. "Consistent with the rest of the codebase" is
   strong evidence.
4. Give a concrete exploit input or request (`GET /api/orders/123` as user B
   returns user A's order) where you can. No working exploit code, just enough to
   reproduce it.

## Not in scope

Data correctness that has nothing to do with an attacker (data-integrity),
races that aren't security relevant (concurrency), performance-based DoS unless
an attacker can trigger it cheaply (scalability), code style.

## False-positive traps

- The ORM or query builder already parameterizes. Interpolating an
  **identifier** from a fixed allowlist isn't injection.
- The framework escapes by default (React JSX, Jinja autoescape, Rails ERB). Only
  explicit opt-outs matter.
- The auth check lives in middleware, a decorator or a router group. Look at
  the route registration before you call it missing.
- Test fixtures, example configs and obviously fake keys (`sk_test_...`,
  `changeme`) aren't leaked secrets, unless prod code loads them.
- Internal-only code (CLI tools, migrations run by operators) has a different
  attacker model. Say who the attacker would be.

## Severity

- **blocker**: an exploitable authz bypass, injection, RCE, secret leak or
  disabled TLS on a reachable path.
- **major**: exploitable only under plausible extra conditions, or defence in
  depth removed on a sensitive path.
- **minor**: hardening such as missing headers or overly broad scopes on low-value data.
- **nit**: rarely appropriate for this lens.
