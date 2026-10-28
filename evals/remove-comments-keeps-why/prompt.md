---
name: remove-comments-keeps-why
description: Licence headers and "why" comments survive while noise goes. From skills/remove-comments/evals/evals.json id 1, with the fixture inlined.
expected_outcome: "Pre-existing committed comments are untouched; agent tells, banners, narration and commented-out code on added lines are deleted; the rate-limit and Safari workaround comments survive (the first shortened); the eslint-disable directive stays byte-identical and is listed as needing a reason. Nothing is committed."
tags: [remove-comments, keep-why]
runs: 1
max_turns: 10
---
remove the comments you added. (Uncommitted changes: src/client.ts was edited and now matches the file below; the licence header, `// This file contains...` and everything above `// ---- Types ----` were already committed, the rest was added in this change.)

```ts
// Copyright 2025 Acme Corp.
// SPDX-License-Identifier: Apache-2.0

// This file contains the HTTP client used by the dashboard.

import { sleep } from "./sleep";

// ---- Types ----

/**
 * Options for the client.
 */
export interface ClientOptions {
  /** The base URL */
  baseUrl: string;
  /** Request timeout in milliseconds; the gateway kills requests after 30 s anyway. */
  timeoutMs: number;
}

// ---- Helpers ----

// Added retry logic with exponential backoff
export async function fetchWithRetry(url: string, opts: ClientOptions): Promise<Response> {
  // Initialize the attempt counter
  let attempt = 0;
  // Loop until we succeed or run out of attempts
  while (true) {
    try {
      // Make the request
      return await fetch(new URL(url, opts.baseUrl), {
        signal: AbortSignal.timeout(opts.timeoutMs), // set the timeout
      });
    } catch (err) {
      attempt++; // increment attempt
      // const delay = 100 * attempt;
      if (attempt >= 3) throw err;
      // Note that we need to wait before retrying because the upstream API
      // rate-limits bursts (see https://github.com/acme/api/issues/412)
      await sleep(2 ** attempt * 100);
    }
  }
}

/**
 * Parses the JSON body.
 * @param res the response
 * @returns the parsed body
 */
export async function parseBody(res: Response): Promise<unknown> {
  // TODO: handle errors
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const body: any = await res.json();
  return body; // return the body
}

// Updated to use the new header name
export function authHeader(token: string): Record<string, string> {
  // Safari 17 strips custom Authorization headers on redirects:
  // https://bugs.webkit.org/show_bug.cgi?id=230255
  return { "X-Auth-Token": token };
}
```
