"""Tests for check-text.py. Run from the repo root:

    python3 -m unittest discover -s skills/pr-writing/scripts/tests
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "check-text.py"
spec = importlib.util.spec_from_file_location("check_text", SCRIPT)
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


def rules(text, kind="reply", against=()):
    return {f["rule"] for f in ct.check(text, kind, against)["findings"] if f["severity"] == "fix"}


def checks(text, kind="reply"):
    return {f["rule"] for f in ct.check(text, kind)["findings"] if f["severity"] == "check"}


class Tells(unittest.TestCase):
    def test_flags_praise_and_epiphany(self):
        for text, rule in [
            ("Good catch, fixed in 4e1a9c2.", "praise"),
            ("Great question. Keys are paths.", "praise"),
            ("That's a fair point, but I'd keep it.", "praise"),
            ("Thanks for catching this, fixed in 4e1a9c2.", "praise"),
            ("Thank you so much for pointing that out.", "praise"),
            ("Thanks for the thorough review.", "praise"),
            ("I really appreciate your feedback here.", "praise"),
            ("You're absolutely right, it runs twice.", "epiphany"),
            ("you are totally correct about the loop", "epiphany"),
            ("Ah, I see now. The loop is off by one.", "epiphany"),
            ("I completely agree with this.", "epiphany"),
            ("You're right, the loop runs twice.", "agreement-opener"),
            ("Agreed! Fixed in 4e1a9c2.", "agreement-opener"),
            ("Absolutely, I'll change it.", "agreement-opener"),
            ("Makes sense, fixed in 4e1a9c2.", "agreement-opener"),
        ]:
            with self.subTest(text=text):
                self.assertIn(rule, rules(text))

    def test_flags_apology_filler_and_stock_words(self):
        for text, rule in [
            ("I apologize for the oversight.", "apology"),
            ("Sorry for the confusion, it's milliseconds.", "apology"),
            ("My apologies, fixed now.", "apology"),
            ("Fixed in 4e1a9c2. Let me know if you have any other questions.", "filler-closer"),
            ("It's milliseconds. Hope this helps.", "filler-closer"),
            ("Keeping it inline, but happy to change it if you feel strongly.", "filler-closer"),
            ("Feel free to resolve.", "filler-closer"),
            ("I've gone ahead and fixed it.", "ai-tell"),
            ("This adds a robust retry mechanism.", "ai-tell"),
            ("We leverage the Map to dedupe.", "ai-tell"),
            ("It's worth noting that 4xx is not retried.", "ai-tell"),
        ]:
            with self.subTest(text=text):
                self.assertIn(rule, rules(text))

    def test_flags_jabs_and_tone_commentary(self):
        for text, rule in [
            ("As I mentioned above, 404s aren't retried.", "passive-aggressive"),
            ("Per my previous comment, it breaks on 4xx.", "passive-aggressive"),
            ("Respectfully, I disagree.", "passive-aggressive"),
            ("With all due respect, it's fine.", "passive-aggressive"),
            ("Obviously it doesn't retry 404s.", "passive-aggressive"),
            ("Actually, it breaks on 4xx.", "passive-aggressive"),
            ("It's fine. Actually, it breaks on 4xx.", "passive-aggressive"),
            ("If you read line 17 it breaks.", "passive-aggressive"),
            ("No need to be rude. It breaks on 4xx.", "tone-commentary"),
            ("Let's keep this constructive.", "tone-commentary"),
            ("I understand your frustration, but it breaks on 4xx.", "tone-commentary"),
            ("I hear you. Keeping backoff.", "tone-commentary"),
            ("So you're saying the loop runs twice?", "restating"),
            ("If I understand correctly, you want a helper.", "restating"),
            ("To address your question, it's milliseconds.", "restating"),
        ]:
            with self.subTest(text=text):
                self.assertIn(rule, rules(text))

    def test_flags_formatting_in_replies(self):
        self.assertIn("emoji", rules("Fixed in 4e1a9c2 🎉"))
        self.assertIn("emoji", rules("✅ Done in 4e1a9c2"))
        self.assertIn("em-dash", rules("Fixed in 4e1a9c2 — the loop runs three times."))
        self.assertIn("exclamation", rules("Fixed! Thanks!"))
        self.assertIn("heading", rules("## Summary\nFixed."))
        self.assertIn("bold", rules("**Fixed** in 4e1a9c2."))
        self.assertIn("long", rules(" ".join(["word"] * 121) + "."))

    def test_description_allows_headings_and_bold_but_not_exclamations(self):
        body = "## What\n**Retry** 5xx responses.\n\n## Why\nThe gateway returns 503s during deploys.\n"
        self.assertEqual(rules(body, "description"), set())
        self.assertIn("exclamation", rules(body + "Ship it!\n", "description"))
        self.assertIn("long", rules(" ".join(["word"] * 401), "description"))


class CleanText(unittest.TestCase):
    def test_good_replies_pass(self):
        for text in [
            "Fixed in 4e1a9c2. The loop runs `attempts` times now, and `get retries up to attempts times` covers it.",
            "404s aren't retried: line 17 breaks on any status below 500, and `get does not retry a 404` asserts a single call.",
            "Milliseconds. It's the wait before the first retry, and it doubles after each one.",
            "I'd keep it inline: it's one expression used once.",
            "That's a separate change: fetch throws before there's a status. I can open an issue for it.",
            "Yes, 503s are retried up to three times.",
            "No, the delay only applies between attempts.",
            "Δεν γίνεται retry στα 429: η γραμμή 17 σταματά σε κάθε status κάτω από 500.",
            "Keeping exponential backoff: the gateway rate-limits bursts during deploys.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(ct.check(text)["findings"], [])

    def test_code_quotes_and_urls_are_ignored(self):
        text = ("> Great catch? You're absolutely right?\n\n"
                "Fixed in 4e1a9c2, see https://github.com/acme/api/pull/14/commits/4e1a9c2#robust.\n"
                "```js\n// I apologize: robust code! ✅ —\n```\n"
                "The error is `robust failure!!`.")
        self.assertEqual(rules(text), set())

    def test_one_exclamation_is_fine_in_a_reply(self):
        self.assertEqual(rules("Fixed in 4e1a9c2, nice to see the test go green!"), set())


class Checks(unittest.TestCase):
    def test_fixed_needs_a_commit(self):
        self.assertIn("fixed-without-commit", checks("Fixed, the loop runs three times now."))
        self.assertNotIn("fixed-without-commit", checks("Fixed in 4e1a9c2."))
        self.assertNotIn("fixed-without-commit", checks("Done: https://github.com/acme/api/commit/4e1a9c2"))

    def test_test_claims_are_checks_not_failures(self):
        out = ct.check("All tests pass on 4e1a9c2.")
        self.assertIn("claim", {f["rule"] for f in out["findings"]})
        self.assertEqual(rules("All tests pass on 4e1a9c2."), set())


class Repetition(unittest.TestCase):
    def test_same_opener_as_another_reply(self):
        self.assertIn("same-opener", rules("The loop is fine here.", against=["The loop is bounded by attempts."]))
        self.assertNotIn("same-opener", rules("Milliseconds.", against=["Fixed in 9b8c7d6: the typo."]))

    def test_fix_status_lines_may_share_an_opener(self):
        self.assertNotIn("same-opener", rules("Fixed in 4e1a9c2: the loop.", against=["Fixed in 9b8c7d6: the typo."]))

    def test_repeated_sentence_across_and_within_replies(self):
        s = "The loop now runs all three attempts before giving up."
        self.assertIn("repeated-sentence", rules(f"No. {s}", against=[f"Fixed in 4e1a9c2. {s}"]))
        self.assertIn("repeated-sentence", rules(f"{s} Also, {s[0].lower()}{s[1:]} {s}"))
        self.assertNotIn("repeated-sentence", rules("No, it's fine.", against=["No, it's fine."]))


class Cli(unittest.TestCase):
    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], input=stdin, capture_output=True, text=True)

    def test_exit_codes_and_json(self):
        bad = self.run_cli(stdin="Great catch! Fixed!")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("praise", {f["rule"] for f in json.loads(bad.stdout)["findings"]})
        good = self.run_cli(stdin="Fixed in 4e1a9c2.")
        self.assertEqual(good.returncode, 0)
        self.assertEqual(json.loads(good.stdout), {"findings": [], "words": 3})
        self.assertEqual(self.run_cli("--kind", "nope").returncode, 2)
        self.assertEqual(self.run_cli("/nonexistent/draft.md").returncode, 2)

    def test_files_and_against(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = pathlib.Path(d, "a.md"), pathlib.Path(d, "b.md")
            a.write_text("The loop is fine here.")
            b.write_text("The loop is bounded by attempts.")
            r = self.run_cli("--kind", "reply", str(a), "--against", str(b))
            self.assertEqual(r.returncode, 1)
            self.assertEqual([f["rule"] for f in json.loads(r.stdout)["findings"]], ["same-opener"])


if __name__ == "__main__":
    unittest.main()
