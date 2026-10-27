"""End-to-end runs of the CLI on evals/fixtures/<lang>/before.<ext>.

Each fixture has a hand-written after.<ext>: the golden result of a correct
decision set. The decisions are derived from the pair (a comment in before
and after is kept, a new comment at the same place is a rewrite, the rest are
deleted), so applying them must reproduce after.<ext> byte for byte, verify
must pass, and every comment expected-kept.json names must survive.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import shutil
import unittest

from helpers import SKILL, commentscan, commit_all, decide, listing, new_repo, run

FIXTURES = SKILL / "evals" / "fixtures"


def anchors(text, lang):
    """(comment, number of non-space code characters before it), in order."""
    out, code, pos = [], 0, 0
    for c in commentscan.scan(text, lang).comments:
        code += sum(not ch.isspace() for ch in text[pos:c.start])
        out.append((c, code))
        pos = c.end
    return out


def derive(doc, before, after, lang):
    """Decisions that turn before into after."""
    by_span = {tuple(c["span"]): c for c in doc["comments"]}
    later = [(after[c.start:c.end], k) for c, k in anchors(after, lang)]
    used = set()
    dec = {}
    for c, k in anchors(before, lang):
        entry = by_span[(c.start, c.end)]
        if entry["protected"]:
            continue
        raw = before[c.start:c.end]
        same = next((i for i, (t, a) in enumerate(later) if a == k and t == raw and i not in used), None)
        if same is not None:
            used.add(same)
            dec[entry["id"]] = {"action": "keep", "reason": "fixture"}
            continue
        olds = {before[x.start:x.end] for x, a in anchors(before, lang) if a == k}
        new = next((i for i, (t, a) in enumerate(later)
                    if a == k and i not in used and t not in olds), None)
        if new is not None:
            used.add(new)
            dec[entry["id"]] = {"action": "rewrite", "text": later[new][0]}
        else:
            dec[entry["id"]] = {"action": "delete"}
    return dec


class Fixtures(unittest.TestCase):
    def test_fixtures(self):
        dirs = sorted(d for d in FIXTURES.iterdir() if d.is_dir())
        self.assertGreaterEqual(len(dirs), 6)
        for d in dirs:
            with self.subTest(fixture=d.name):
                self.check(d)

    def check(self, d):
        before_path = next(d.glob("before.*"))
        after_path = next(d.glob("after.*"))
        expected = json.loads((d / "expected-kept.json").read_text())
        repo = new_repo()
        name = "src/" + before_path.name.replace("before", "example")
        (repo / "src").mkdir()
        shutil.copy(before_path, repo / name)
        commit_all(repo, "fixture")
        r, doc, out = listing(repo, "--paths", name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(doc["files"][0]["status"], "ok", doc["files"][0])
        lang = doc["files"][0]["lang"]
        before, after = before_path.read_text(), after_path.read_text()

        dec = derive(doc, before, after, lang)
        self.assertEqual(sorted(dec), sorted(doc["pending"]))
        r = run("apply", decide(out, dec), "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual((repo / name).read_text(), after)
        r = run("verify", "--list", out, cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout.decode())

        by_id = {c["id"]: c for c in doc["comments"]}
        for item in expected["protected"]:
            self.assertEqual(by_id[item["id"]]["text"], item["text"])
            self.assertTrue(by_id[item["id"]]["protected"], item)
            self.assertIn(item["text"], after)
        for item in expected["kept"]:
            self.assertIn(dec[item["id"]]["action"], ("keep", "rewrite"), item)
            self.assertIn(item["text"], after)
        for item in expected.get("needs_human", []):
            self.assertIn("needs-reason", by_id[item["id"]]["flags"], item)
        survivors = {i["id"] for i in expected["kept"]} | {i["id"] for i in expected["protected"]}
        deleted = {cid for cid, x in dec.items() if x["action"] == "delete"}
        self.assertEqual(survivors & deleted, set())
        self.assertEqual(len(deleted), expected["deleted"])


if __name__ == "__main__":
    unittest.main()
