"""Draws the README's eval chart from a `claude plugin eval` run with a no-plugin baseline arm.

Writes docs/evals-light.svg and docs/evals-dark.svg (one dumbbell per skill: the mean score without
vstack, then with it) and rewrites the README between the evals markers with the chart and a table.
A case's score is the mean over its runs; a skill's score is the mean over its cases; "All skills"
is the mean over every case.

Usage: python3 evals/_harness/chart.py evals/results/<timestamp>/aggregate-result.json
"""
import argparse
import html
import json
import pathlib
import re
import sys

HARNESS = pathlib.Path(__file__).resolve().parent
ROOT = HARNESS.parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"
START, END = "<!-- evals:start -->", "<!-- evals:end -->"

# One hue, two shades: the baseline recedes, the plugin arm carries the story. Text uses GitHub's
# own foreground colors so the chart reads as part of the page in either theme.
THEMES = {
    "light": {"without": "#86b6ef", "with": "#1c5cab", "rail": "#d1d9e0", "grid": "#eaeef2",
              "text": "#1f2328", "muted": "#59636e", "surface": "#ffffff"},
    "dark": {"without": "#1c5cab", "with": "#86b6ef", "rail": "#3d444d", "grid": "#262c36",
             "text": "#f0f6fc", "muted": "#9198a1", "surface": "#0d1117"},
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def summarize(result):
    """Rows of (label, cases, without, with), the "All skills" row first, then skills by gain."""
    skills = sorted((p.name for p in (ROOT / "skills").iterdir() if p.is_dir()), key=len, reverse=True)
    by_skill, every = {}, {"with": [], "without": []}
    for case in result["cases"]:
        skill = next((s for s in skills if case["name"].startswith(s + "-")), None)
        arms = case.get("arms", {})
        scores = {arm: mean([r["score"] for r in arms.get(arm, []) if r.get("score") is not None])
                  for arm in ("with", "without")}
        if skill is None or None in scores.values():
            continue
        row = by_skill.setdefault(skill, {"with": [], "without": []})
        for arm, score in scores.items():
            row[arm].append(score)
            every[arm].append(score)
    if not by_skill:
        sys.exit("chart.py: no case has scored runs in both arms; run the evals with the default "
                 "--ablation with-without")
    rows = [(s, len(v["with"]), mean(v["without"]), mean(v["with"])) for s, v in by_skill.items()]
    rows.sort(key=lambda r: (r[2] - r[3], r[0]))
    return [("All skills", len(every["with"]), mean(every["without"]), mean(every["with"]))] + rows


def pts(x):
    return f"{round(x * 100):d}"


def delta(without, with_):
    d = int(pts(with_)) - int(pts(without))  # from the rounded scores, so the table's row adds up
    return f"+{d} pts" if d > 0 else ("±0 pts" if d == 0 else f"−{-d} pts")


def svg(rows, note, t):
    width, left, right, top, row_h = 760, 170, 640, 64, 42
    height = top + row_h * len(rows) + 46
    x = lambda v: left + (right - left) * v  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="{html.escape(FONT)}" role="img" '
           f'aria-label="Eval score per skill, without and with vstack">']
    # Legend, top left, so identity is never color alone.
    out.append(f'<circle cx="{left + 6}" cy="22" r="6" fill="{t["without"]}"/>'
               f'<text x="{left + 18}" y="26" font-size="13" fill="{t["muted"]}">without vstack</text>'
               f'<circle cx="{left + 136}" cy="22" r="6" fill="{t["with"]}"/>'
               f'<text x="{left + 148}" y="26" font-size="13" fill="{t["muted"]}">with vstack</text>')
    plot_bottom = top + row_h * len(rows) - 12
    for tick in (0, 0.25, 0.5, 0.75, 1):
        out.append(f'<line x1="{x(tick):.1f}" y1="{top - 14}" x2="{x(tick):.1f}" y2="{plot_bottom}" '
                   f'stroke="{t["grid"]}" stroke-width="1"/>'
                   f'<text x="{x(tick):.1f}" y="{plot_bottom + 18}" font-size="12" text-anchor="middle" '
                   f'fill="{t["muted"]}">{pts(tick)}%</text>')
    for i, (label, cases, without, with_) in enumerate(rows):
        cy = top + row_h * i + 8
        weight = "600" if i == 0 else "400"
        out.append(f'<text x="{left - 16}" y="{cy + 1}" font-size="14" font-weight="{weight}" '
                   f'text-anchor="end" fill="{t["text"]}">{html.escape(label)}</text>'
                   f'<text x="{left - 16}" y="{cy + 16}" font-size="11" text-anchor="end" '
                   f'fill="{t["muted"]}">{cases} case{"s" if cases != 1 else ""}</text>')
        lo, hi = sorted((x(without), x(with_)))
        out.append(f'<line x1="{lo:.1f}" y1="{cy + 4}" x2="{hi:.1f}" y2="{cy + 4}" '
                   f'stroke="{t["rail"]}" stroke-width="4" stroke-linecap="round"/>')
        for arm, v in (("without", without), ("with", with_)):
            out.append(f'<circle cx="{x(v):.1f}" cy="{cy + 4}" r="7" fill="{t[arm]}" '
                       f'stroke="{t["surface"]}" stroke-width="2"><title>{html.escape(label)}, {arm} '
                       f'vstack: {pts(v)}%</title></circle>')
        out.append(f'<text x="{right + 22}" y="{cy + 9}" font-size="14" font-weight="{weight}" '
                   f'fill="{t["text"]}">{delta(without, with_)}</text>')
    out.append(f'<text x="{left}" y="{height - 10}" font-size="11" fill="{t["muted"]}">'
               f'{html.escape(note)}</text></svg>')
    return "\n".join(out) + "\n"


def section(rows, note):
    lines = [START,
             "<picture>",
             '  <source media="(prefers-color-scheme: dark)" srcset="docs/evals-dark.svg">',
             '  <img alt="Eval score per skill, without and with vstack" src="docs/evals-light.svg">',
             "</picture>",
             "",
             "| Skill | Cases | Without vstack | With vstack | Change |",
             "|---|--:|--:|--:|--:|"]
    for i, (label, cases, without, with_) in enumerate(rows):
        cell = (lambda s: f"**{s}**") if i == 0 else (lambda s: s)  # noqa: E731
        name = label if i == 0 else f"`{label}`"
        lines.append(f"| {cell(name)} | {cases} | {pts(without)}% | {cell(pts(with_) + '%')} "
                     f"| {cell(delta(without, with_))} |")
    lines += ["", f"<sub>{note}</sub>", END]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("result", type=pathlib.Path, help="aggregate-result.json from claude plugin eval")
    p.add_argument("--note", help="the caption under the chart; defaults to the run's date, size and version")
    args = p.parse_args()
    result = json.loads(args.result.read_text())
    rows = summarize(result)
    runs = {len(c["arms"].get("with", [])) for c in result["cases"]}
    n_runs = "/".join(map(str, sorted(runs)))
    note = args.note or (f"{rows[0][1]} eval cases, {n_runs} run{'' if runs == {1} else 's'} per arm, "
                         f"Claude Code {result.get('claudeVersion', '?')}, "
                         f"{str(result.get('startedAt', ''))[:10]}. Score: share of a case's graders passed.")
    if result.get("partial"):
        note += " Partial run (cost cap hit)."
    for name, theme in THEMES.items():
        (DOCS / f"evals-{name}.svg").write_text(svg(rows, note, theme))
    text = README.read_text()
    if START not in text or END not in text:
        sys.exit(f"chart.py: README.md has no {START} ... {END} section to fill")
    README.write_text(re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: section(rows, note),
                             text, flags=re.S))
    for label, cases, without, with_ in rows:
        print(f"{label:18} {cases:3} cases  {pts(without):>3}% -> {pts(with_):>3}%  {delta(without, with_)}")


if __name__ == "__main__":
    main()
