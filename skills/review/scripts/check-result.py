"""Check one reviewer or skeptic result before the coordinator uses it.

Usage: python3 -I check-result.py reviewer --id R1 [--files LIST] RESULT
       python3 -I check-result.py skeptic --id C2 RESULT
RESULT and LIST are paths, or "-" for stdin (only one of them). LIST holds the
area's files, one per line, split on LF only (a CR, form feed or U+2028 inside a
name stays part of it).

The result is untrusted text: it is parsed as JSON and nothing in it is run
or followed. The object is the whole text, else the last fenced block that
parses as an object, else the last balanced top-level {...} that does. It is
checked against references/<kind>.schema.json, then the ranges (score 0-10,
confidences 0-1, line >= 1 or null), the id or cluster_id, the completion
sentinel (done = "REVIEWED <id>" or "CHECKED <id>"), the unreadable list
(must be empty) and, for a reviewer given LIST, that every listed file is in
coverage.files_reviewed.

Prints one JSON line, {"status": ..., "reasons": [...], "missing": [...]},
and exits:
  0 ok       use it
  1 partial  valid and finished, but the reviewer reported coverage "partial"
             (deliberate: don't retry; listed files it skipped are in missing)
  3 retry    no parseable result, schema or range failure, wrong id or
             sentinel, unreadable inputs, or a "complete" claim with files
             missing: re-dispatch once
  2 usage error (message on stderr)
  4 internal error (exception type on stderr, nothing on stdout)
Reasons never quote the result's text; paths are cut to 200 characters.
"""
import argparse
import json
import pathlib
import re
import sys

MAX_BYTES = 1024 * 1024
MAX_REASONS = 20
MAX_MISSING = 50
MAX_CANDIDATES = 50
SCHEMAS = pathlib.Path(__file__).resolve().parent.parent / "references"
SCAN = re.compile(r'[{}"\\]')
FENCE = re.compile(r"```[^\n`]*\n(.*?)\n[ \t]*```", re.DOTALL)
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
                         or (isinstance(v, float) and v.is_integer()),
}


class Usage(Exception):
    pass


def cap(s, n=200):
    return s if len(s) <= n else s[:n] + "..."


def read(src):
    if src == "-":
        return sys.stdin.buffer.read(MAX_BYTES + 1)
    with open(src, "rb") as f:
        return f.read(MAX_BYTES + 1)


def read_files(src):
    try:
        data = read(src)
    except OSError as e:
        raise Usage(f"can't read --files: {e.strerror}")
    if len(data) > MAX_BYTES:
        raise Usage(f"--files is over {MAX_BYTES} bytes")
    return [p for p in map(norm, data.decode("utf-8", errors="replace").split("\n")) if p]


def no_constants(name):
    raise ValueError(name)


def no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def parse(text):
    try:
        v = json.loads(text, parse_constant=no_constants, object_pairs_hook=no_duplicates)
    except (ValueError, RecursionError):
        return None
    return v if isinstance(v, dict) else None


def top_level_objects(text):
    """Spans of balanced {...} outside any other object, string-aware inside them."""
    spans, depth, start, in_str, skip = [], 0, 0, False, -1
    for m in SCAN.finditer(text):
        i, c = m.start(), m.group()
        if i == skip:
            continue
        if in_str:
            if c == "\\":
                skip = i + 1
            elif c == '"':
                in_str = False
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif depth == 0:
            continue
        elif c == '"':
            in_str = True
        elif c == "}":
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
    return spans


def extract(text):
    whole = parse(text.strip())
    if whole is not None:
        return whole
    for block in FENCE.findall(text)[::-1][:MAX_CANDIDATES]:
        v = parse(block)
        if v is not None:
            return v
    for a, b in top_level_objects(text)[::-1][:MAX_CANDIDATES]:
        v = parse(text[a:b])
        if v is not None:
            return v
    return None


def validate(v, schema, path, errs):
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(TYPES[t](v) for t in types):
            errs.append(f"schema: {path} is not {'/'.join(types)}")
            return
    if "enum" in schema and v not in schema["enum"]:
        errs.append(f"schema: {path} is not one of the allowed values")
    if isinstance(v, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in v:
                errs.append(f"schema: {path} lacks required '{k}'")
        if schema.get("additionalProperties") is False:
            extra = [k for k in v if k not in props]
            if extra:
                errs.append(f"schema: {path} has {len(extra)} unexpected propert{'y' if len(extra) == 1 else 'ies'}")
        for k, sub in props.items():
            if k in v:
                validate(v[k], sub, f"{path}.{k}", errs)
    elif isinstance(v, list) and "items" in schema:
        for i, item in enumerate(v):
            validate(item, schema["items"], f"{path}[{i}]", errs)


def in_unit(x):
    return 0 <= x <= 1


def ranges(kind, r):
    errs = []
    if kind == "reviewer":
        if not 0 <= r["score"] <= 10:
            errs.append("range: $.score is outside 0-10")
        if not in_unit(r["confidence"]):
            errs.append("range: $.confidence is outside 0-1")
        for i, f in enumerate(r["findings"]):
            if not in_unit(f["confidence"]):
                errs.append(f"range: $.findings[{i}].confidence is outside 0-1")
            if f["line"] is not None and f["line"] < 1:
                errs.append(f"range: $.findings[{i}].line is below 1")
    elif not in_unit(r["confidence"]):
        errs.append("range: $.confidence is outside 0-1")
    return errs


def norm(p):
    p = p.strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def check(kind, rid, raw, files):
    if raw is None:
        return "retry", ["result is missing or unreadable"], []
    if len(raw) > MAX_BYTES:
        return "retry", [f"result is over {MAX_BYTES} bytes"], []
    r = extract(raw.decode("utf-8-sig", errors="replace"))
    if r is None:
        return "retry", ["no JSON object found"], []
    schema = json.loads((SCHEMAS / f"{kind}.schema.json").read_text())
    errs = []
    validate(r, schema, "$", errs)
    if errs:
        return "retry", errs, []
    errs = ranges(kind, r)
    id_key, word = ("id", "REVIEWED") if kind == "reviewer" else ("cluster_id", "CHECKED")
    if r[id_key] != rid:
        errs.append(f"identity: $.{id_key} is not {json.dumps(cap(rid))}")
    if r["done"] != f"{word} {rid}":
        errs.append(f"sentinel: $.done is not {json.dumps(cap(word + ' ' + rid))}")
    unreadable = r["coverage"]["unreadable"] if kind == "reviewer" else r["unreadable"]
    if unreadable:
        errs.append(f"unreadable: reported {len(unreadable)} input(s) it could not open")
        errs += [f"unreadable: {json.dumps(cap(u))}" for u in unreadable[:5]]
    missing = []
    if kind == "reviewer" and files is not None:
        seen = {norm(p) for p in r["coverage"]["files_reviewed"]}
        missing = [cap(f) for f in files if norm(f) not in seen]
        if missing and r["coverage"]["status"] == "complete":
            errs.append(f"manifest: coverage is 'complete' but {len(missing)} listed file(s) are not in files_reviewed")
    if len(missing) > MAX_MISSING:
        missing = missing[:MAX_MISSING] + [f"... and {len(missing) - MAX_MISSING} more"]
    if errs:
        return "retry", errs, missing
    if kind == "reviewer" and r["coverage"]["status"] == "partial":
        return "partial", ["coverage: reviewer reported partial"], missing
    return "ok", [], missing


def main():
    ap = argparse.ArgumentParser(prog="check-result.py")
    ap.add_argument("kind", choices=["reviewer", "skeptic"])
    ap.add_argument("--id", required=True)
    ap.add_argument("--files")
    ap.add_argument("result")
    a = ap.parse_args()
    if not a.id.strip():
        ap.error("--id is empty")
    if a.files is not None and a.kind == "skeptic":
        ap.error("--files is for reviewers only")
    if a.files == "-" and a.result == "-":
        ap.error("only one of --files and RESULT can be stdin")
    try:
        files = None if a.files is None else read_files(a.files)
        try:
            raw = read(a.result)
        except OSError:
            raw = None
        status, reasons, missing = check(a.kind, a.id, raw, files)
    except Usage as e:
        print(f"check-result: {e}", file=sys.stderr)
        sys.exit(2)
    if len(reasons) > MAX_REASONS:
        reasons = reasons[:MAX_REASONS] + [f"... and {len(reasons) - MAX_REASONS} more"]
    print(json.dumps({"status": status, "reasons": reasons, "missing": missing}))
    sys.exit({"ok": 0, "partial": 1, "retry": 3}[status])


try:
    main()
except Exception as e:
    print(f"check-result: internal error: {type(e).__name__}", file=sys.stderr)
    sys.exit(4)
