"""List comments in scope, apply the model's decisions, prove no code changed.

  comments.py list [--diff [BASE] | --paths PATH...] [--all] --out FILE
  comments.py apply DECISIONS --list FILE [--dry-run]
  comments.py verify [--list FILE | --base REF | --before FILE --after FILE] [--strict]
  comments.py restore --list FILE [--applied]
  comments.py report --list FILE --decisions FILE [--out PATH]
  comments.py strip FILE

list writes the comments in scope as JSON (plus a snapshot of every scanned
file next to it), apply edits only inside comment spans and refuses to write a
file whose code tokens would change, verify compares code tokens (and, for
Python, the AST) of the snapshot or a git ref against the working tree.

Exit codes: 0 ok; 1 verify found a code change, or apply skipped a file that
changed since list; 2 bad arguments or decisions, git or scan errors.
"""
import argparse
import ast
import bisect
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tokenize

# python3 -I leaves the script's own directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commentscan import (LINE_MODE, ScanError, detect, line_starts, lines_of,  # noqa: E402 - must follow the sys.path line
                         py_docstrings, py_tokens, scan)

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
GENERATED_DIRS = ("node_modules/", "vendor/", "dist/", "build/", "third_party/")
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "Cargo.lock",
             "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock", "composer.lock", "go.sum",
             "flake.lock", "Package.resolved", "pubspec.lock", "mix.lock", "packages.lock.json",
             "gradle.lockfile", "npm-shrinkwrap.json"}
OPS = set("+-*/%=<>!&|^~?:.@#\\")


def die(msg, code=2):
    print(f"remove-comments: {msg}", file=sys.stderr)
    sys.exit(code)


def git(root, *args, check=True):
    r = subprocess.run(["git", "--literal-pathspecs", "-c", "core.quotepath=off", "-C", root, *args],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if check and r.returncode:
        die(f"git {args[0]} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode:
        die("not inside a git repository")
    return os.path.realpath(r.stdout.decode().strip())


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".remove-comments-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if os.path.exists(path):
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def decode(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ScanError("not UTF-8")


def skip_reason(path, data):
    p, base = "/" + path, os.path.basename(path)
    if any("/" + d in p for d in GENERATED_DIRS):
        return "generated or vendored path"
    if ".min." in base:
        return "minified"
    if base in LOCKFILES or base.endswith(".lock"):
        return "lockfile"
    head = b"\n".join(data.split(b"\n", 5)[:5])
    if b"@generated" in head or b"DO NOT EDIT" in head:
        return "generated (says so in its first lines)"
    if b"\0" in data:
        return "binary"
    return None



def rev(root, ref):
    if ref.startswith("-"):
        die(f"refusing ref that starts with '-': {ref}")
    r = git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}", check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def default_branch(root):
    r = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    cands = ([r.stdout.decode().strip()] if r.returncode == 0 else []) + ["main", "master", "origin/main",
                                                                          "origin/master"]
    for c in cands:
        if rev(root, c):
            return c
    return None


def added_ranges(root, base, paths):
    out = git(root, "diff", "-U0", "--no-ext-diff", "--no-textconv", "--no-color", "-M", base, "--",
              *paths).stdout
    ranges = []
    for m in re.finditer(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", out, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        if count:
            ranges.append([start, start + count - 1])
    return ranges


def diff_scope(root, base_arg):
    head = rev(root, "HEAD")
    if base_arg is None:
        dirty = git(root, "status", "--porcelain", "-z", "--untracked-files=all").stdout
        if dirty or not head:
            base, label = head or EMPTY_TREE, "uncommitted changes vs HEAD"
        else:
            default = default_branch(root)
            if not default:
                die("clean tree and no default branch found: pass --diff BASE or paths")
            base = git(root, "merge-base", default, "HEAD").stdout.decode().strip()
            label = f"branch vs its merge base with {default}"
    else:
        sha_ = rev(root, base_arg)
        if not sha_:
            die(f"not a commit: {base_arg}")
        r = git(root, "merge-base", sha_, "HEAD", check=False) if head else None
        base = r.stdout.decode().strip() if r is not None and r.returncode == 0 else sha_
        label = f"changes since the merge base with {base_arg}"
    files, scope = [], {}
    raw = git(root, "diff", "--name-status", "-z", "-M", "--no-ext-diff", "--no-textconv",
              "--diff-filter=ACMRT", base).stdout.decode("utf-8", "surrogateescape").split("\0")
    i = 0
    while i < len(raw) - 1:
        status = raw[i]
        if status[:1] in "RC":
            old, new = raw[i + 1], raw[i + 2]
            i += 3
            scope[new] = added_ranges(root, base, [old, new])
        else:
            new = raw[i + 1]
            i += 2
            scope[new] = "all" if status == "A" else added_ranges(root, base, [new])
        files.append(new)
    untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout
    for f in untracked.decode("utf-8", "surrogateescape").split("\0"):
        if f and f not in scope:
            files.append(f)
            scope[f] = "all"
    return files, scope, {"mode": "diff", "base": base, "label": f"{label} ({base[:12]})"}


def paths_scope(root, paths):
    files = []
    for p in paths:
        ab = os.path.abspath(p)
        if not os.path.lexists(ab):
            die(f"no such path: {p}")
        if not inside(ab, root):
            die(f"path is outside the repository: {p}")
        cur = ab
        while cur != os.path.dirname(cur):
            if os.path.islink(cur) and inside(os.path.dirname(cur), root):
                die(f"path is or goes through a symlink: {p}")
            cur = os.path.dirname(cur)
        rel = os.path.relpath(os.path.realpath(ab), root)
        out = git(root, "ls-files", "-z", "--", rel).stdout.decode("utf-8", "surrogateescape")
        tracked = [f for f in out.split("\0") if f]
        if not tracked and os.path.isfile(ab):
            files.append((rel, "untracked (paths mode covers tracked files only)"))
        files += [(f, None) for f in tracked]
    seen, out = set(), []
    for f, why in files:
        if f not in seen:
            seen.add(f)
            out.append((f, why))
    return out



def placement(text, c):
    ls = text.rfind("\n", 0, c.start) + 1
    le = text.find("\n", c.end)
    le = len(text) if le < 0 else le
    before, after = text[ls:c.start].strip(), text[c.end:le].strip()
    return "own-line" if not before and not after else "trailing" if not after else "inline"


def context(lines, first, last, n=2):
    def pick(idx):
        out = []
        for i in idx:
            if 0 <= i < len(lines) and lines[i].strip():
                out.append(lines[i].rstrip("\r\n")[:120])
                if len(out) == n:
                    break
        return out
    return {"before": pick(range(first - 2, -1, -1))[::-1], "after": pick(range(last, len(lines)))}


def in_scope(c, rng):
    if rng == "all":
        return True
    lo, hi = c.meta["line"], c.meta["end_line"]
    return any(a <= hi and lo <= b for a, b in rng)


RUNTIME_DOCS = re.compile(r"\b(?:argparse|click|typer|fastapi|fire|docopt|__doc__|doctest|sphinx|autodoc)\b")


def cmd_list(a):
    root = repo_root()
    out = os.path.abspath(a.out)
    if inside(out, root):
        die("--out must be outside the repository (use a mktemp -d directory)")
    if a.paths_mode:
        if not a.paths:
            die("--paths needs at least one path")
        listed = paths_scope(root, a.paths)
        scope = {f: "all" for f, _ in listed}
        info = {"mode": "paths", "base": None, "label": "paths: " + ", ".join(a.paths)}
    else:
        files, scope, info = diff_scope(root, a.diff)
        listed = [(f, None) for f in files]
    info["all"] = a.all
    snapdir = out + ".snapshot"
    os.makedirs(snapdir, exist_ok=True)
    entries, comments = [], []
    for idx, (path, why) in enumerate(listed):
        rec = {"path": path}
        entries.append(rec)
        full = os.path.join(root, path)
        if why:
            rec.update(status="skipped", reason=why)
            continue
        if os.path.islink(full) or not os.path.isfile(full):
            rec.update(status="skipped", reason="symlink" if os.path.islink(full) else "not a regular file")
            continue
        data = open(full, "rb").read()
        lang = detect(path, data[:200])
        reason = skip_reason(path, data) or (None if lang else "unsupported file type")
        if reason:
            rec.update(status="skipped", reason=reason)
            continue
        text = None
        try:
            text = decode(data)
            sc = scan(text, lang)
        except ScanError as e:
            where = f" (line {text.count(chr(10), 0, e.pos) + 1})" if text and e.pos is not None else ""
            rec.update(status="error", lang=lang, reason=f"{e}{where}")
            continue
        snap = os.path.join(snapdir, str(idx))
        with open(snap, "wb") as f:
            f.write(data)
        rec.update(status="ok", lang=lang, sha256=sha(data), snapshot=snap, notes=sc.notes)
        if lang == "python" and RUNTIME_DOCS.search(text):
            rec["notes"] = rec["notes"] + ["file mentions argparse/click/typer/fastapi/__doc__/doctest:"
                                           " its docstrings may be read at runtime"]
        lines = lines_of(text)
        rng = scope.get(path, "all")
        file_comments = []
        for c in sc.comments:
            cid = f"c{len(comments) + 1}"
            raw = text[c.start:c.end]
            entry = {"id": cid, "file": path, "line": c.meta["line"],
                     "col": c.start - text.rfind("\n", 0, c.start),
                     "end_line": c.meta["end_line"], "kind": c.kind,
                     "in_scope": bool(a.all or in_scope(c, rng)), "protected": c.protected,
                     "flags": c.flags, "span": [c.start, c.end]}
            if entry["in_scope"]:
                entry.update(placement=placement(text, c), text=raw,
                             context=context(lines, c.meta["line"], c.meta["end_line"]))
                if c.jsx:
                    entry["jsx"] = True
                if c.kind == "docstring":
                    entry.update(owner=c.meta["owner"], public=c.meta["public"],
                                 only_statement=c.meta["only_statement"])
            else:
                entry["text"] = raw if len(raw) <= 100 else raw[:100] + "..."
            comments.append(entry)
            file_comments.append(entry)
        group = None
        for prev, cur in zip([None] + file_comments, file_comments):
            joins = (prev and cur.get("placement") == prev.get("placement") == "own-line"
                     and cur["kind"] == prev["kind"] == "line" and not cur["protected"]
                     and not prev["protected"] and cur["line"] == prev["end_line"] + 1
                     and cur["col"] == prev["col"])
            if joins:
                group = group or prev
                group["group"] = cur["group"] = group["id"]
            else:
                group = None
        rec.update(comments=len(file_comments), in_scope=sum(c["in_scope"] for c in file_comments))
    pending = [c["id"] for c in comments if c["in_scope"] and not c["protected"]]
    doc = {"version": 1, "repo": root, "scope": info, "files": entries, "comments": comments,
           "pending": pending}
    with open(out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    ok = [e for e in entries if e["status"] == "ok"]
    print(f"remove-comments list: {info['label']}")
    print(f"  {len(entries)} files: {len(ok)} scanned"
          + "".join(f", {sum(e['status'] == s for e in entries)} {s}" for s in ("skipped", "error")
                    if any(e["status"] == s for e in entries)))
    for e in entries:
        if e["status"] != "ok":
            print(f"    {e['status']}: {e['path']}: {e['reason']}")
    ins = [c for c in comments if c["in_scope"]]
    print(f"  {len(comments)} comments, {len(ins)} in scope "
          f"({sum(bool(c['protected']) for c in ins)} protected), {len(pending)} need a decision")
    print(f"  wrote {out}")



def merges(left, right):
    word = lambda ch: ch.isalnum() or ch in "_$"
    return (word(left) and word(right)) or (left in OPS and right in OPS) \
        or (left == "." and word(right)) or (word(left) and right == ".")


def odd_backslash(line):
    line = line.rstrip("\r\n")
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


def delete_inline(t, s, e, c):
    """Remove the comment at t[s:e] that shares its line with code."""
    ls = t.rfind("\n", 0, s) + 1
    le = t.find("\n", e)
    le = len(t) if le < 0 else le
    end = le - 1 if le > e and t[le - 1] == "\r" else le
    if not t[e:end].strip():
        a = s
        while a > ls and t[a - 1] in " \t":
            a -= 1
        return t[:a] + t[end:]
    if not t[ls:s].strip():
        b = e
        while b < len(t) and t[b] in " \t":
            b += 1
        return t[:s] + t[b:]
    if "\n" in t[s:e]:
        a = s
        while a > ls and t[a - 1] in " \t":
            a -= 1
        return t[:a] + ("\r\n" if "\r\n" in t[s:e] else "\n") + t[e:]
    left, right = t[s - 1], t[e]
    if right in " \t" and (left in " \t" or left in "([{"):
        b = e
        while t[b] in " \t":
            b += 1
        return t[:s] + t[b:]
    if left in " \t" and right in ")]},;":
        a = s
        while t[a - 1] in " \t":
            a -= 1
        return t[:a] + t[e:]
    if left in " \t" or right in " \t" or c.jsx or c.kind == "html":
        return t[:s] + t[e:]
    return t[:s] + (" " if merges(left, right) else "") + t[e:]


def place(t, s, e, new):
    """Replace t[s:e] with new, indenting its later lines like the first."""
    ls = t.rfind("\n", 0, s) + 1
    indent = t[ls:s] if not t[ls:s].strip() else ""
    le = t.find("\n", s)
    nl = "\r\n" if le > 0 and t[le - 1] == "\r" else "\n"
    first, *rest = new.split("\n")
    return t[:s] + first + "".join(nl + (indent + l if l.strip() else l) for l in rest) + t[e:]


def edit(text, lang, actions):
    """actions: [(Comment, None to delete | new source)]. Returns the new text."""
    lines = lines_of(text)
    starts = line_starts(text)

    def line_of(pos):
        return bisect.bisect_right(starts, pos) - 1

    deleted = [c for c, new in actions if new is None]
    spans_on = {}
    for c in deleted:
        for ln in range(line_of(c.start), line_of(max(c.start, c.end - 1)) + 1):
            spans_on.setdefault(ln, []).append((max(c.start, starts[ln]), min(c.end, starts[ln + 1])))
    whole = set()
    for ln, spans in spans_on.items():
        rest, pos = [], starts[ln]
        for a, b in sorted(spans):
            rest.append(text[pos:a])
            pos = b
        rest.append(text[pos:starts[ln + 1]])
        rest[0] = rest[0].lstrip("\ufeff")
        prev_cont = ln > 0 and odd_backslash(lines[ln - 1]) and (ln - 1) not in spans_on
        if not "".join(rest).strip() and not prev_cont:
            whole.add(ln)
    changed = True
    while changed:
        changed = False
        for c in deleted:
            rng = set(range(line_of(c.start), line_of(max(c.start, c.end - 1)) + 1))
            if rng & whole and not rng <= whole:
                whole -= rng
                changed = True

    def blank(i):
        return 0 <= i < len(lines) and not lines[i].strip().lstrip("\ufeff") and i not in whole

    openers = ("{", "(", "[") + ((":",) if lang in ("python", "yaml") else ())
    extra = set()
    for ln in sorted(whole):
        if ln - 1 in whole:
            continue
        b = ln
        while b + 1 in whole:
            b += 1
        prev, nxt = ln - 1, b + 1
        if blank(prev) and blank(nxt):
            pick = nxt
        elif prev < 0 and blank(nxt):
            pick = nxt
        elif prev >= 0 and lines[prev].rstrip().endswith(openers) and blank(nxt):
            pick = nxt
        elif blank(prev) and (nxt >= len(lines) or lines[nxt].lstrip().startswith(("}", ")", "]"))):
            pick = prev
        else:
            continue
        if not (pick > 0 and odd_backslash(lines[pick - 1])):
            extra.add(pick)
    bom = 1 if text.startswith("\ufeff") else 0
    ops = [(starts[ln] + (bom if ln == 0 else 0), "lines", starts[ln + 1], None) for ln in whole | extra]
    in_whole = lambda c: line_of(c.start) in whole
    ops += [(c.start, "comment", c, new) for c, new in actions if new is not None or not in_whole(c)]
    t = text
    for start, kind, x, new in sorted(ops, key=lambda o: o[0], reverse=True):
        if kind == "lines":
            t = t[:start] + t[x:]
        elif new is None:
            t = delete_inline(t, x.start, x.end, x)
        else:
            t = place(t, x.start, x.end, new)
    if not text.endswith("\n") and t.endswith("\n"):
        t = t[:-2] if t.endswith("\r\n") else t[:-1]
    return t


def check_rewrite(c, new, lang, text, where):
    """Why the rewrite text can't replace comment c, or None."""
    if not isinstance(new, str) or not new.strip():
        return "rewrite text is empty"
    if "\r" in new:
        return "rewrite text contains a carriage return"
    if c.kind == "docstring":
        try:
            toks = [t for t in py_tokens(new + "\n") if t.type not in (tokenize.NEWLINE, tokenize.NL,
                                                                       tokenize.ENDMARKER)]
            mod = ast.parse(new)
        except (ScanError, SyntaxError):
            return "rewrite is not a Python string literal"
        if len(toks) != 1 or toks[0].type != tokenize.STRING or len(mod.body) != 1 \
                or not isinstance(getattr(mod.body[0], "value", None), ast.Constant) \
                or not isinstance(mod.body[0].value.value, str):
            return "a docstring rewrite must be a single string literal"
        return None
    if c.jsx:
        m = re.fullmatch(r"\{\s*(/\*.*\*/|//[^\n]*\n)\s*\}", new, re.S)
        inner = m.group(1) if m else ""
        try:
            ok = m and len(scan(inner + "\n", lang).comments) == 1
        except ScanError:
            ok = False
        return None if ok else "a JSX comment rewrite must be {/* ... */}"
    multi = "\n" in new
    if multi and not (c.kind == "line" and where == "own-line"):
        return "only an own-line line comment can be rewritten as several lines"
    probe = new + ("\nx\n" if c.kind != "block" or where == "own-line" else " x\n")
    try:
        found = scan(probe, lang).comments
    except ScanError as e:
        return f"rewrite doesn't scan: {e}"
    pos, want = 0, []
    for line in new.split("\n"):
        a = pos + len(line) - len(line.lstrip())
        want.append((a, pos + len(line.rstrip())))
        pos += len(line) + 1
    want = [w for w in want if w[1] > w[0]] if multi else [(0, len(new))]
    got = [(x.start, x.end) for x in found if x.kind == c.kind]
    if got != want or len(found) != len(want):
        return f"rewrite must scan as exactly one {c.kind} comment" + (" per line" if multi else "")
    if any(x.protected for x in found):
        return "rewrite would create a tool directive"
    return None



FREE_TOKEN = re.compile(r"[\w$]+|[+\-*/%=<>!&|^~?:.@#\\]+|\S")
SENTINEL = "\x00"


def normalize(text, lang):
    """Code tokens as [(token, line)], or raise ScanError."""
    sc = scan(text, lang)
    if lang == "python":
        return py_normal(text, sc), sc
    starts = line_starts(text)
    segs = sorted([(c.start, c.end, c) for c in sc.comments] + [(a, b, None) for a, b in sc.literals],
                  key=lambda x: x[0])
    parts, offsets, verbatim = [], [], []
    pos = acc = 0

    def add(piece, orig):
        nonlocal acc
        parts.append(piece)
        offsets.append((acc, orig))
        acc += len(piece)

    for a, b, c in segs:
        add(text[pos:a], pos)
        if c is None or c.protected:
            add(f" {SENTINEL} ", a)
            verbatim.append(text[a:b])
        elif not (c.jsx or c.kind == "html" or lang in LINE_MODE):
            add(" ", a)
        pos = b
    add(text[pos:], pos)
    joined = "".join(parts)
    accs = [x for x, _ in offsets]

    def line(i):
        k = bisect.bisect_right(accs, i) - 1
        return bisect.bisect_right(starts, offsets[k][1] + (i - offsets[k][0])) if k >= 0 else 1

    lit = iter(verbatim)
    out = []
    if lang in LINE_MODE:
        if lang in ("sh", "zsh"):
            joined = re.sub(r"\\\r?\n", "", joined)
            # positions after a join drift by a line; line numbers stay a hint
        pos = 0
        for raw in joined.split("\n"):
            content = raw.rstrip("\r")
            body = re.sub(r"[ \t]+", " ", content.strip())
            if body:
                lead = content[:len(content) - len(content.lstrip(" "))] if lang == "yaml" else ""
                out.append((lead + body, line(pos)))
            pos += len(raw) + 1
    else:
        pat = re.compile(r"\S+") if lang in ("html", "xml") else FREE_TOKEN
        out = [(m.group(), line(m.start())) for m in pat.finditer(joined)]
    result = []
    for tok, ln in out:
        while SENTINEL in tok:
            tok = tok.replace(SENTINEL, "\x01" + next(lit) + "\x01", 1)
        result.append((tok.replace("\x01", ""), ln))
    return result, sc


def py_normal(text, sc):
    docs = [(c.start, c.end) for c in sc.comments if c.kind == "docstring"]
    kept = {c.start for c in sc.comments if c.kind == "line" and c.protected}
    starts = line_starts(text)
    out, skip_newline = [], False
    for tok in py_tokens(text):
        a = starts[tok.start[0] - 1] + tok.start[1]
        if tok.type == tokenize.COMMENT:
            if a in kept:
                out.append((tok.string, tok.start[0]))
            continue
        if tok.type in (tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        if any(x <= a < y for x, y in docs):
            skip_newline = True
            continue
        if tok.type == tokenize.NEWLINE:
            if not skip_newline:
                out.append(("<newline>", tok.start[0]))
            skip_newline = False
        elif tok.type in (tokenize.INDENT, tokenize.DEDENT):
            out.append((tokenize.tok_name[tok.type].lower(), tok.start[0]))
        else:
            out.append((tok.string, tok.start[0]))
    return out


def py_ast(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body \
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body = body[1:]
        if all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                           and s.value.value is Ellipsis) for s in body):
            node.body = []
    return ast.dump(tree)


def docstring_changes(before, after):
    def docs(text):
        return {owner or "<module>": ast.literal_eval(text[a:b]) for _, owner, _, a, b in py_docstrings(text)}
    b, a = docs(before), docs(after)
    out = []
    for owner in sorted(set(b) | set(a)):
        if owner not in a:
            out.append(f"docstring of {owner} removed")
        elif owner not in b:
            out.append(f"docstring of {owner} added")
        elif a[owner] != b[owner]:
            out.append(f"docstring of {owner} changed")
    return out


def compare(before, after, lang):
    """{'ok': bool, 'diff': str|None, 'docstrings': [...]}; raises ScanError."""
    tb, _ = normalize(before, lang)
    ta, _ = normalize(after, lang)
    res = {"ok": True, "diff": None, "docstrings": []}
    if [t for t, _ in tb] != [t for t, _ in ta]:
        i = next((k for k, (x, y) in enumerate(zip(tb, ta)) if x[0] != y[0]), min(len(tb), len(ta)))
        x = tb[i] if i < len(tb) else ("<end of file>", tb[-1][1] if tb else 1)
        y = ta[i] if i < len(ta) else ("<end of file>", ta[-1][1] if ta else 1)
        res.update(ok=False, diff=f"line {y[1]}: expected {x[0][:60]!r} (before, line {x[1]}), "
                                  f"found {y[0][:60]!r}")
    if lang == "python":
        try:
            same = py_ast(before) == py_ast(after)
        except SyntaxError as e:
            raise ScanError(f"python syntax error: {e.msg}")
        if not same and res["ok"]:
            res.update(ok=False, diff="AST differs")
        res["docstrings"] = docstring_changes(before, after)
    return res



def load_list(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict) or doc.get("version") != 1:
        die(f"can't read listing {path}")
    return doc


def read_decisions(path):
    try:
        with open(path) as f:
            dec = json.load(f)
    except (OSError, ValueError) as e:
        die(f"can't read decisions {path}: {e}")
    if not isinstance(dec, dict):
        die("decisions must be a JSON object keyed by comment id")
    return dec


def check_decisions(doc, dec):
    by_id = {c["id"]: c for c in doc["comments"]}
    errors = []
    missing = [cid for cid in doc["pending"] if cid not in dec]
    if missing:
        errors.append(f"missing decisions ({len(missing)}): {', '.join(missing)}")
    for cid, d in dec.items():
        c = by_id.get(cid)
        act = d.get("action") if isinstance(d, dict) else None
        if c is None:
            errors.append(f"{cid}: no such comment in the listing")
        elif act not in ("delete", "keep", "rewrite"):
            errors.append(f"{cid}: action must be delete, keep or rewrite")
        elif not c["in_scope"]:
            errors.append(f"{cid}: out of scope, leave it out of the decisions")
        elif c["protected"] and act != "keep":
            errors.append(f"{cid}: protected ({c['protected']}), it stays byte-identical")
        elif act == "keep" and not (isinstance(d.get("reason"), str) and d["reason"].strip()):
            errors.append(f"{cid}: keep needs a one-line reason")
        elif act == "delete" and c["kind"] == "docstring" and c.get("only_statement"):
            errors.append(f"{cid}: only statement in {c['owner']}: rewrite it shorter or keep it")
        elif act == "rewrite" and not isinstance(d.get("text"), str):
            errors.append(f"{cid}: rewrite needs the full new comment in \"text\"")
    return errors


def plan_file(doc, rec, dec):
    """(new bytes or None, error or None, stale) for one listed file."""
    root = doc["repo"]
    full = os.path.join(root, rec["path"])
    if os.path.islink(full):
        return None, "file is now a symlink", False
    try:
        data = open(full, "rb").read()
    except OSError as e:
        return None, f"can't read: {e.strerror}", True
    if sha(data) != rec["sha256"]:
        return None, "file changed since list", True
    text = decode(data)
    lang = rec["lang"]
    sc = scan(text, lang)
    by_span = {(c.start, c.end): c for c in sc.comments}
    actions, errors = [], []
    for entry in doc["comments"]:
        d = dec.get(entry["id"])
        if entry["file"] != rec["path"] or not d or d["action"] == "keep":
            continue
        c = by_span.get(tuple(entry["span"]))
        if c is None or text[c.start:c.end] != entry["text"]:
            return None, f"{entry['id']}: listing doesn't match the file", False
        if d["action"] == "delete":
            if c.kind == "docstring":
                a = text.rfind("\n", 0, c.start) + 1
                b = text.find("\n", c.end)
                if text[a:c.start].strip() or text[c.end:len(text) if b < 0 else b].strip():
                    errors.append(f"{entry['id']}: docstring shares a line with code; rewrite or keep it")
            actions.append((c, None))
        else:
            new = d["text"].replace("\r\n", "\n")
            if new == entry["text"]:
                continue
            why = check_rewrite(c, new, lang, text, entry.get("placement"))
            if why:
                errors.append(f"{entry['id']}: {why}")
            actions.append((c, new))
    if errors:
        return None, "; ".join(errors), False
    if not actions:
        return data, None, False
    new = edit(text, lang, actions)
    try:
        res = compare(text, new, lang)
    except ScanError as e:
        return None, f"edited file doesn't scan ({e}); not written", False
    if not res["ok"]:
        return None, f"edit would change code ({res['diff']}); not written", False
    return new.encode("utf-8"), None, False


def cmd_apply(a):
    doc = load_list(a.list)
    dec = read_decisions(a.decisions)
    errors = check_decisions(doc, dec)
    if errors:
        print("remove-comments apply: refusing, no file was changed", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(2)
    plans, stale, failed = [], [], []
    for rec in doc["files"]:
        if rec["status"] != "ok":
            continue
        try:
            new, err, is_stale = plan_file(doc, rec, dec)
        except ScanError as e:
            new, err, is_stale = None, str(e), False
        if err:
            (stale if is_stale else failed).append((rec["path"], err))
        else:
            plans.append((rec, new))
    if failed:
        print("remove-comments apply: refusing, no file was changed", file=sys.stderr)
        for path, err in failed:
            print(f"  {path}: {err}", file=sys.stderr)
        sys.exit(2)
    done = {rec["path"] for rec, _ in plans}
    file_of = {c["id"]: c["file"] for c in doc["comments"]}
    counts = {k: sum(1 for cid, d in dec.items() if d["action"] == k and file_of[cid] in done)
              for k in ("delete", "rewrite", "keep")}
    copies = a.list + ".applied"
    record = {}
    edited = 0
    for rec, new in plans:
        full = os.path.join(doc["repo"], rec["path"])
        if sha(new) == rec["sha256"]:
            continue
        edited += 1
        if a.dry_run:
            continue
        write_atomic(full, new)
        os.makedirs(copies, exist_ok=True)
        copy = os.path.join(copies, os.path.basename(rec["snapshot"]))
        with open(copy, "wb") as f:
            f.write(new)
        record[rec["path"]] = {"sha256": sha(new), "copy": copy}
    if not a.dry_run and record:
        with open(a.list + ".applied.json", "w") as f:
            json.dump(record, f, indent=1)
    by_id = {c["id"]: c for c in doc["comments"]}
    verb = "would edit" if a.dry_run else "edited"
    print(f"remove-comments apply: {verb} {edited} file(s): {counts['delete']} deleted, "
          f"{counts['rewrite']} rewritten, {counts['keep']} kept")
    for cid, d in dec.items():
        c = by_id[cid]
        if d["action"] != "delete" and "addresses-agent" in c["flags"] and not c["protected"]:
            print(f"  warning: {cid} {c['file']}:{c['line']} addresses the agent but was not deleted")
    for path, err in stale:
        print(f"  skipped {path}: {err}")
    sys.exit(1 if stale else 0)


def verify_pairs(a):
    if a.before or a.after:
        if not (a.before and a.after):
            die("--before and --after go together")
        lang = a.lang or detect(a.after, open(a.after, "rb").read(200))
        return [(a.after, lang, open(a.before, "rb").read(), open(a.after, "rb").read(), None)]
    if a.base:
        root = repo_root()
        base = rev(root, a.base)
        if not base:
            die(f"not a commit: {a.base}")
        files, _, _ = diff_scope(root, base)
        out = []
        for path in files:
            full = os.path.join(root, path)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            after = open(full, "rb").read()
            lang = detect(path, after[:200])
            if not lang or skip_reason(path, after):
                continue
            r = git(root, "cat-file", "blob", f"{base}:{path}", check=False)
            out.append((path, lang, r.stdout if r.returncode == 0 else None, after, None))
        return out
    doc = load_list(a.list)
    out = []
    for rec in doc["files"]:
        if rec["status"] != "ok":
            continue
        full = os.path.join(doc["repo"], rec["path"])
        after = open(full, "rb").read() if os.path.isfile(full) else None
        out.append((rec["path"], rec["lang"], open(rec["snapshot"], "rb").read(), after, None))
    return out


def cmd_verify(a):
    if not (a.list or a.base or a.before or a.after):
        die("verify needs --list, --base or --before/--after")
    diffs, errors, docs, same = [], [], [], 0
    for path, lang, before, after, _ in verify_pairs(a):
        if before is None:
            print(f"  {path}: new file, nothing to compare against")
            continue
        if after is None:
            diffs.append(f"{path}: file is gone")
            continue
        try:
            res = compare(decode(before), decode(after), lang)
        except ScanError as e:
            errors.append(f"{path}: {e}")
            continue
        if not res["ok"]:
            diffs.append(f"{path}: {res['diff']}")
        else:
            same += 1
        docs += [f"{path}: {d}" for d in res["docstrings"]]
    print(f"remove-comments verify: {same} file(s) with identical code tokens, {len(diffs)} changed, "
          f"{len(errors)} error(s), {len(docs)} docstring change(s)")
    for d in diffs:
        print(f"  CODE CHANGED {d}")
    for d in docs:
        print(f"  docstring: {d}")
    for e in errors:
        print(f"  error: {e}")
    if diffs or (a.strict and docs):
        sys.exit(1)
    sys.exit(2 if errors else 0)


def cmd_restore(a):
    doc = load_list(a.list)
    applied = {}
    if a.applied:
        try:
            with open(a.list + ".applied.json") as f:
                applied = json.load(f)
        except (OSError, ValueError):
            die("no applied record next to the listing (was apply run?)")
    restored = 0
    for rec in doc["files"]:
        if rec["status"] != "ok" or (a.applied and rec["path"] not in applied):
            continue
        src = applied[rec["path"]]["copy"] if a.applied else rec["snapshot"]
        want = open(src, "rb").read()
        full = os.path.join(doc["repo"], rec["path"])
        if os.path.islink(full):
            print(f"  skipped {rec['path']}: now a symlink")
            continue
        if not os.path.exists(full) or open(full, "rb").read() != want:
            write_atomic(full, want)
            restored += 1
            print(f"  restored {rec['path']}")
    print(f"remove-comments restore: {restored} file(s) restored to the "
          f"{'post-apply' if a.applied else 'pre-apply snapshot'} content")



def one_line(text, n=80):
    t = re.sub(r"\s+", " ", text).strip().replace("`", "'").replace("|", "\\|")
    return t if len(t) <= n else t[:n - 3] + "..."


def cmd_report(a):
    doc = load_list(a.list)
    dec = read_decisions(a.decisions)
    ins = [c for c in doc["comments"] if c["in_scope"]]
    acts = {c["id"]: (dec.get(c["id"]) or {}).get("action") for c in ins}
    n = lambda k: sum(1 for c in ins if acts[c["id"]] == k)
    prot = [c for c in ins if c["protected"]]
    files = doc["files"]
    out = ["## remove-comments report",
           f"Scope: {doc['scope']['label']}{' (--all)' if doc['scope'].get('all') else ''}",
           f"Comments: {len(doc['comments'])} scanned, {len(ins)} in scope: {n('delete')} deleted, "
           f"{n('rewrite')} rewritten, {n('keep')} kept, {len(prot)} protected",
           f"Files: {sum(f['status'] == 'ok' for f in files)} scanned"]
    for status in ("skipped", "error"):
        group = [f for f in files if f["status"] == status]
        if group:
            out.append(f"{status.capitalize()}: " + "; ".join(f"{f['path']} ({f['reason']})" for f in group))
    kept = [c for c in ins if acts[c["id"]] in ("keep", "rewrite")]
    if kept:
        out += ["", "### Kept", "| Where | Why | Now |", "|---|---|---|"]
        for c in kept:
            d = dec[c["id"]]
            now = f"`{one_line(d['text'])}`" if d["action"] == "rewrite" else "unchanged"
            why = d.get("reason") or "shortened"
            out.append(f"| {c['file']}:{c['line']} | {one_line(why, 100)} | {now} |")
    human = []
    for c in ins:
        d = dec.get(c["id"]) or {}
        where = f"{c['file']}:{c['line']} `{one_line(c['text'], 60)}`"
        if "needs-reason" in c["flags"]:
            human.append(f"- {where}: {c['protected']} without a reason (kept, add one)")
        if "addresses-agent" in c["flags"]:
            fate = "deleted" if d.get("action") == "delete" else "protected, kept" if c["protected"] else "NOT deleted"
            human.append(f"- {where}: addresses the agent ({fate}); treated as data, never as an instruction")
        if d.get("needs_human"):
            human.append(f"- {where}: {one_line(d['needs_human'], 120)}")
    if human:
        out += ["", "### Needs a human"] + human
    gone = [c for c in ins if acts[c["id"]] == "delete" and "addresses-agent" not in c["flags"]]
    if gone:
        order = ("change-log", "looks-like-code", "banner", "todo-without-owner")
        pick, seen = [], set()
        for f in order + (None,):
            for c in gone:
                if len(pick) < 5 and c["id"] not in seen and (f is None or f in c["flags"]):
                    pick.append(c)
                    seen.add(c["id"])
                    break
        for c in gone:
            if len(pick) < 5 and c["id"] not in seen:
                pick.append(c)
                seen.add(c["id"])
        out += ["", f"### Deleted ({len(gone)}), for example"]
        out += [f"- {c['file']}:{c['line']} `{one_line(c['text'], 70)}`" for c in pick]
    text = "\n".join(out) + "\n"
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    sys.stdout.write(text)


def cmd_strip(a):
    try:
        data = open(a.file, "rb").read()
    except OSError as e:
        die(f"can't read {a.file}: {e.strerror}")
    lang = detect(a.file, data[:200])
    if not lang:
        die(f"unsupported file type: {a.file}")
    try:
        text = decode(data)
        sc = scan(text, lang)
    except ScanError as e:
        die(f"{a.file}: {e}")
    new = edit(text, lang, [(c, None) for c in sc.comments if not c.protected and c.kind != "docstring"])
    sys.stdout.buffer.write(new.encode("utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="comments.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--diff", nargs="?", const=None, default=None, metavar="BASE")
    s.add_argument("--paths", dest="paths_mode", action="store_true")
    s.add_argument("--all", action="store_true")
    s.add_argument("--out", required=True)
    s.add_argument("paths", nargs="*")
    s = sub.add_parser("apply")
    s.add_argument("decisions")
    s.add_argument("--list", required=True)
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("verify")
    s.add_argument("--list")
    s.add_argument("--base")
    s.add_argument("--before")
    s.add_argument("--after")
    s.add_argument("--lang")
    s.add_argument("--strict", action="store_true")
    s = sub.add_parser("restore")
    s.add_argument("--list", required=True)
    s.add_argument("--applied", action="store_true")
    s = sub.add_parser("report")
    s.add_argument("--list", required=True)
    s.add_argument("--decisions", required=True)
    s.add_argument("--out")
    s = sub.add_parser("strip")
    s.add_argument("file")
    a = p.parse_args(argv)
    if a.cmd == "list" and a.paths and not a.paths_mode:
        die("paths given without --paths")
    {"list": cmd_list, "apply": cmd_apply, "verify": cmd_verify, "restore": cmd_restore,
     "report": cmd_report, "strip": cmd_strip}[a.cmd](a)


if __name__ == "__main__":
    main()
