"""Flag the phrases that make PR text read as written by a bot.

Usage: python3 -I check-text.py [--kind reply|comment|review|description] [--against FILE ...] [FILE]

Reads the draft from FILE, or stdin without one, and prints one JSON object:

    {"findings": [{"rule": ..., "severity": "fix"|"check", "match": ..., "line": N, "why": ...}],
     "words": N}

`fix` findings are phrases to rewrite. `check` findings are claims to verify before posting
(a "Fixed" with no commit, "tests pass"): they may be true, so they don't fail the check.
--against takes the other bodies posted, or about to be posted, in the same run (and earlier
replies in the thread); a draft that opens like one of them or repeats one of their sentences
is flagged.

Code (fenced blocks and `inline`), quoted lines ("> ...") and link URLs are ignored, so
quoting a reviewer or a line of code never trips a rule.

Exit 0: no `fix` findings. Exit 1: at least one. Exit 2: bad arguments or unreadable input.
A clean result doesn't make a reply good; it only means none of the known tells are in it.
"""
import argparse
import json
import re
import sys

# Word caps per kind. Over the cap is a `fix`: say less, or split the point out.
CAPS = {"reply": 120, "comment": 200, "review": 120, "description": 400}

I = re.IGNORECASE
RULES = [
    # (rule, severity, pattern, kinds or None for all, why)
    ("praise", "fix", re.compile(
        r"\b(great|good|nice|excellent|awesome|fantastic|solid|sharp|keen|astute|brilliant)"
        r"\s+(catch|find|spot|point|question|call|suggestion|idea|eye|observation|thought)s?\b", I), None,
     "Grades the reviewer's comment. Lead with what you did or the answer instead."),
    ("praise", "fix", re.compile(
        r"\b(that'?s|this is|what) an?\s+(really\s+|very\s+)?(great|good|fair|valid|excellent|interesting|important)"
        r"\s+(point|question|catch|concern|suggestion|observation)\b", I), None,
     "Grades the reviewer's comment. Lead with what you did or the answer instead."),
    ("praise", "fix", re.compile(
        r"\bthanks?(\s+you)?(\s+(so|very)\s+much|\s+a\s+lot)?\s+for\s+(catching|spotting|flagging|noticing|raising|"
        r"pointing\s+(this|that|it)\s+out|bringing\s+(this|that)\s+up|the\s+(great|good|helpful|thorough|detailed|"
        r"careful|thoughtful|quick|kind|feedback|review|suggestion|catch))", I), None,
     "Thanking for the catch is the same tell as \"good catch\". The fix is the acknowledgement."),
    ("praise", "fix", re.compile(r"\b(I\s+)?(really\s+|truly\s+)?appreciate\s+(you|your|the)\b", I), None,
     "Gratitude padding. Drop it and answer."),
    ("epiphany", "fix", re.compile(
        r"\b(you'?re|you\s+are)\s+(absolutely|totally|completely|entirely|so|100%|quite)\s+(right|correct)\b", I), None,
     "The \"you're absolutely right\" tell. Say what changes."),
    ("epiphany", "fix", re.compile(
        r"\b(ah|oh|ahh|ohh|aha|whoa|wow)\b[,!.]?\s*(I\s+see|you'?re\s+right|good|great|that|yes|yeah|right|indeed|now|of\s+course)", I),
     None, "Performed realisation. State the fact."),
    ("epiphany", "fix", re.compile(r"\b(I\s+)?(completely|totally|fully|wholeheartedly|100%)\s+agree\b", I), None,
     "Intensified agreement. If you agree, the change or answer shows it."),
    ("agreement-opener", "fix", re.compile(
        r"^\W*(you'?re|you\s+are)\s+(right|correct)\b|^\W*(agreed|absolutely|definitely|certainly|of\s+course|"
        r"great|perfect|awesome|excellent|sure\s+thing|totally|indeed|good\s+call|makes\s+sense)\b[\s!.,:—-]", I),
     None, "Opens by agreeing. Open with the answer or what changed."),
    ("apology", "fix", re.compile(
        r"\b(I\s+)?(sincerely\s+|deeply\s+|truly\s+)?apologi[sz]e\b|\bmy\s+(sincere\s+|deepest\s+)?apologies\b|"
        r"\bsorry\s+(for|about)\s+(the|any|my|this|that)\s+(confusion|oversight|inconvenience|mistake|trouble|mix-?up|error)", I),
     None, "Ceremonial apology. If it was a mistake, say what was wrong, once."),
    ("filler-closer", "fix", re.compile(
        r"\blet\s+me\s+know\s+if\b|\bhope\s+(this|that|it)\s+(helps|clarifies|makes\s+sense|answers)\b|"
        r"\bfeel\s+free\s+to\b|\bdon'?t\s+hesitate\b|\bplease\s+let\s+me\s+know\b|"
        r"\bhappy\s+to\s+(make|help|adjust|change|discuss|revisit|tweak|update|iterate|address|hear)\b|"
        r"\bif\s+you\s+have\s+any\s+(other|further|more|additional)?\s*(questions|concerns|feedback|thoughts)\b|"
        r"\bany\s+(other|further)\s+(feedback|thoughts)\s+(is|are)\s+welcome\b", I),
     None, "Filler closer. End when the point is made."),
    ("ai-tell", "fix", re.compile(
        r"\bI'?ve\s+gone\s+ahead\s+and\b|\bcertainly!|\bdelv(e|es|ing)\b|\bleverag(e|es|ed|ing)\b|\bseamless(ly)?\b|"
        r"\brobust(ness)?\b|\bcomprehensive\b|\bit'?s\s+worth\s+noting\b|\bplease\s+note\s+that\b|\bin\s+summary\b|"
        r"\bto\s+summari[sz]e\b|\bas\s+an\s+ai\b|\bI\s+hope\s+this\s+(message|finds)\b|\bstreamlin(e|ed|es|ing)\b|"
        r"\benhanc(e|ed|es|ing|ement|ements)\b|\butiliz(e|ed|es|ing)\b|\bin\s+order\s+to\b|\bkey\s+(changes|improvements)\b|"
        r"\bthis\s+(change|PR)\s+(ensures|introduces\s+a\s+(new\s+)?(robust|comprehensive))", I),
     None, "Stock phrasing. Use the plain word."),
    ("passive-aggressive", "fix", re.compile(
        r"\bas\s+(I|previously|already)\s+(already\s+)?(mentioned|stated|said|noted|explained)\b|\bper\s+my\s+(last|previous|earlier)\b|"
        r"\bas\s+(noted|mentioned|stated|explained)\s+(above|earlier|before)\b|\brespectfully\b|\bwith\s+(all\s+due\s+)?respect\b|"
        r"\bobviously\b|\bclearly,|\bif\s+you\s+(had\s+)?(read|look(ed)?\s+at)\b|(^|[.!?]\s+)actually,|\bto\s+be\s+fair\b|"
        r"\bI'?m\s+not\s+sure\s+(what|why)\s+you\b|\bnot\s+sure\s+if\s+you\s+(saw|noticed|read)\b", I),
     None, "Reads as a jab. State the fact without the framing."),
    ("tone-commentary", "fix", re.compile(
        r"\b(your|the|that)\s+tone\b|\bno\s+need\s+(to\s+be|for\s+(that|the))\b|\blet'?s\s+keep\s+(this|it)\s+(civil|professional|constructive|respectful)\b|"
        r"\bI\s+understand\s+(your|the|that)\s+(frustration|concern|concerns|point|perspective)\b|\bI\s+hear\s+you\b|"
        r"\bI\s+can\s+see\s+(why|how)\s+(this|that|you)\b|\bunderstandabl[ey]\b|\bfrustrat(ing|ed|ion)\b", I),
     None, "Comments on feelings or tone. Answer the technical point only."),
    ("restating", "fix", re.compile(
        r"^\W*(so,?\s+)?(you'?re|you\s+are)\s+(saying|suggesting|asking|proposing)\b|^\W*if\s+I\s+understand\s+(you\s+|this\s+|that\s+)?correctly\b|"
        r"^\W*to\s+(address|answer)\s+your\s+(question|comment|point)\b|^\W*regarding\s+your\s+(question|comment|point)\b", I),
     None, "Restates the comment. The thread already shows it."),
    ("emoji", "fix", re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕✅❌✨]"), None,
     "Emoji. Leave them out."),
    ("em-dash", "fix", re.compile("—"), None,
     "Em dash, a strong machine-writing tell. Use a comma, a colon or a new sentence."),
    ("heading", "fix", re.compile(r"^#{1,6}\s", re.MULTILINE), {"reply", "review"},
     "Headings in a reply. Write sentences."),
    ("bold", "fix", re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__"), {"reply", "review", "comment"},
     "Bold in a comment. Plain text carries it."),
    ("claim", "check", re.compile(r"\b(all\s+)?tests\s+(now\s+)?(pass|passed|are\s+passing|are\s+green)\b|\bverified\b|\btested\s+(it|this|locally)\b", I),
     None, "Claims a test run. Only if it ran in this session or the check is green."),
]
EXCLAIM = re.compile(r"!(?![=\[(])")
FIXED = re.compile(r"^\W*(fixed|done|addressed|updated|changed|resolved)\b", I)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b|/commit/|/pull/\d+/commits/")


def visible(text):
    """The text a reader takes as prose: code, quotes and URLs blanked, line numbers kept."""
    text = re.sub(r"```.*?```|~~~.*?~~~", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "CODE", text)
    text = re.sub(r"^\s*>.*$", "", text, flags=re.M)
    text = re.sub(r"\]\([^)\s]*\)", "]", text)
    text = re.sub(r"https?://\S+", "URL", text)
    return text


def sentences(text):
    flat = " ".join(visible(text).split())
    return [s for s in re.split(r"(?<=[.!?])\s+", flat) if s]


def norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def opener(text):
    """The first three words, or "" for a status line like "Fixed in 4e1a9c2" that every fix reply may share."""
    first = next(iter(sentences(text)), "")
    if FIXED.search(first) and SHA.search(first):
        return ""
    return " ".join(norm(first).split()[:3])


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def check(text, kind="reply", against=()):
    prose = visible(text)
    out = []
    for rule, sev, pat, kinds, why in RULES:
        if kinds and kind not in kinds:
            continue
        for m in pat.finditer(prose):
            out.append({"rule": rule, "severity": sev, "match": m.group(0).strip(), "line": line_of(prose, m.start()), "why": why})
    bangs = EXCLAIM.findall(prose)
    if len(bangs) >= 2 or (bangs and kind == "description"):
        out.append({"rule": "exclamation", "severity": "fix", "match": "!" * len(bangs), "line": line_of(prose, prose.index("!")),
                    "why": "Exclamation marks read as performed enthusiasm. Use full stops."})
    words = len(re.findall(r"\b\w+\b", prose))
    cap = CAPS.get(kind)
    if cap and words > cap:
        out.append({"rule": "long", "severity": "fix", "match": f"{words} words", "line": 1,
                    "why": f"Over {cap} words for a {kind}. Cut to the answer and its evidence."})
    if kind in ("reply", "comment") and FIXED.search(prose) and not SHA.search(text):
        out.append({"rule": "fixed-without-commit", "severity": "check", "match": FIXED.search(prose).group(0).strip(), "line": 1,
                    "why": "Says it's fixed without linking the commit. Add the short SHA, or don't say fixed."})
    seen = {}
    for s in sentences(text):
        n = norm(s)
        if len(n.split()) >= 6:
            if n in seen:
                out.append({"rule": "repeated-sentence", "severity": "fix", "match": s, "line": 1,
                            "why": "Says the same sentence twice."})
            seen[n] = True
    mine = opener(text)
    others = [a for a in against if a.strip()]
    for other in others:
        if mine and len(mine.split()) >= 2 and mine == opener(other):
            out.append({"rule": "same-opener", "severity": "fix", "match": mine, "line": 1,
                        "why": "Opens the same way as another reply in this run. Each reply starts from its own answer."})
            break
    theirs = {norm(s) for other in others for s in sentences(other) if len(norm(s).split()) >= 6}
    for s in sentences(text):
        if norm(s) in theirs:
            out.append({"rule": "repeated-sentence", "severity": "fix", "match": s, "line": 1,
                        "why": "Repeats a sentence from another reply or an earlier comment. Say it once, or link it."})
    return {"findings": out, "words": words}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--kind", choices=sorted(CAPS), default="reply")
    ap.add_argument("--against", nargs="*", default=[], metavar="FILE")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        against = [open(f, encoding="utf-8").read() for f in args.against]
    except (OSError, UnicodeDecodeError) as e:
        print(f"check-text: {e}", file=sys.stderr)
        return 2
    result = check(text, args.kind, against)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 1 if any(f["severity"] == "fix" for f in result["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
