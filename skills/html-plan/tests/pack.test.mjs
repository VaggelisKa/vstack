// Tests for pack.mjs --lint-only: it refuses secret files and secret-looking text, and fails a plan whose claims
// break the tree rules. Each case writes a page to a temp dir and runs pack.mjs on it; no browser, no dependencies.
//   node --test skills/html-plan/tests/pack.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const RUNTIME = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'runtime');
const PACK = join(RUNTIME, 'pack.mjs');

const page = (main) => `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Test Plan</title>
<link rel="stylesheet" href="${join(RUNTIME, 'htmlplan.css')}">
<script src="${join(RUNTIME, 'htmlplan.js')}" defer></script>
<body>
<header><h1>Test Plan for Packing</h1></header>
<main>
${main}
</main>
</body>
</html>
`;

function lint(t, main, files = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'html-plan-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text);
  writeFileSync(join(dir, 'plan.html'), page(main));
  const r = spawnSync(process.execPath, [PACK, join(dir, 'plan.html'), '--lint-only'], { cwd: dir, encoding: 'utf8' });
  return { code: r.status, out: r.stdout + r.stderr };
}

const plan = (claims) => `<doc-plan>\n${claims}\n<doc-claim aux="scope"><p>Nothing else changes.</p></doc-claim>\n</doc-plan>`;
const claim = (body) => `<doc-claim>${body}</doc-claim>`;

test('a clean plan lints clean', (t) => {
  const { code, out } = lint(t, plan(claim('<p>The page has one claim.</p><doc-code lang="js">const x = 1;</doc-code>')));
  assert.equal(code, 0, out);
  assert.match(out, /lint clean/);
});

test('a secrets file is never read', (t) => {
  const { code, out } = lint(t, plan(claim('<p>The config holds the key.</p><doc-code src=".env"></doc-code>')),
    { '.env': 'A=1\n' });
  assert.equal(code, 1, out);
  assert.match(out, /"\.env" looks like a secrets file — not reading it/);
});

test('a file holding secret-looking text is not packaged', (t) => {
  const { code, out } = lint(t, plan(claim('<p>The client reads the key.</p><doc-code src="client.js"></doc-code>')),
    { 'client.js': 'const key = "AKIAABCDEFGHIJKLMNOP";\n' });
  assert.equal(code, 1, out);
  assert.match(out, /client\.js looks like it holds a secret/);
  assert.doesNotMatch(out, /AKIAABCDEFGHIJKLMNOP/);
});

test('a claim with no sentence fails', (t) => {
  const { code, out } = lint(t, plan(claim('<doc-code lang="js">const x = 1;</doc-code>')));
  assert.equal(code, 1, out);
  assert.match(out, /claim 1: no claim — the first child must be a <p>/);
});

test('an unclosed or stray claim fails', (t) => {
  let r = lint(t, '<doc-plan>\n<doc-claim><p>This claim never closes.</p>\n</doc-plan>');
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /1 <doc-claim> left open/);
  r = lint(t, '<doc-claim><p>This claim has no plan.</p></doc-claim>\n<doc-plan></doc-plan>');
  assert.equal(r.code, 1, r.out);
  assert.match(r.out, /a <doc-claim> sits outside any <doc-plan>/);
});
