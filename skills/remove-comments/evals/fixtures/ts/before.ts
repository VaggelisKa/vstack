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
