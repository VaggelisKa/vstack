// Copyright 2025 Acme Corp.
// SPDX-License-Identifier: Apache-2.0

import { sleep } from "./sleep";

export interface ClientOptions {
  baseUrl: string;
  /** The gateway kills requests after 30 s. */
  timeoutMs: number;
}

export async function fetchWithRetry(url: string, opts: ClientOptions): Promise<Response> {
  let attempt = 0;
  while (true) {
    try {
      return await fetch(new URL(url, opts.baseUrl), {
        signal: AbortSignal.timeout(opts.timeoutMs),
      });
    } catch (err) {
      attempt++;
      if (attempt >= 3) throw err;
      // Upstream API rate-limits bursts: https://github.com/acme/api/issues/412
      await sleep(2 ** attempt * 100);
    }
  }
}

export async function parseBody(res: Response): Promise<unknown> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const body: any = await res.json();
  return body;
}

export function authHeader(token: string): Record<string, string> {
  // Safari 17 strips custom Authorization headers on redirects:
  // https://bugs.webkit.org/show_bug.cgi?id=230255
  return { "X-Auth-Token": token };
}
