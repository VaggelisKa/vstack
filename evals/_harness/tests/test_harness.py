"""Tests for the eval harness: sync.py, scaffold.py and the fake gh. No model calls.

The scenario tests drive the fake gh, and the skills' own scripts, against a scaffolded workspace,
so a change to either side that breaks the other shows up here instead of as a confusing eval score.

Run from the repo root: python3 -m unittest discover -s evals/_harness/tests
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
ROOT = HARNESS.parent.parent
EVALS = HARNESS.parent
sys.path.insert(0, str(HARNESS))
import chart  # noqa: E402
import scaffold  # noqa: E402
import sync  # noqa: E402

PY = scaffold.find_python()


def workspace(test, fixture):
    """Scaffold fixture (a dict) into a fresh run dir. Returns (cwd, env) for commands in it."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="vstack-eval-test-"))
    test.addCleanup(shutil.rmtree, root, True)
    home, cwd = root / "home", root / "home" / "cwd"
    cwd.mkdir(parents=True)
    (root / "fixture.json").write_text(json.dumps(fixture))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "GITHUB_"))}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    r = subprocess.run([sys.executable, str(HARNESS / "scaffold.py"), str(root / "fixture.json")],
                       cwd=cwd, env=env, capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr)
    env["PATH"] = f"{home / 'bin'}{os.pathsep}{env['PATH']}"
    return cwd, env


def sh(cwd, env, *args, stdin=None):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, input=stdin)


def log(cwd):
    return (cwd / ".vstack-eval" / "gh-calls.log").read_text()


def fixture(**github):
    fx = json.loads((HARNESS / "fixtures" / "pr-green.json").read_text())
    return sync.deep_merge(fx, {"github": github}) if github else fx


@unittest.skipUnless(PY, "needs a Python 3.10+ outside the home directory for the skills' scripts")
class Scenarios(unittest.TestCase):
    def test_green_pr_reads_and_merges(self):
        cwd, env = workspace(self, fixture())
        head = sh(cwd, env, "git", "rev-parse", "HEAD").stdout.strip()
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "14", "--json", "headRefOid,reviewDecision,mergeStateStatus,url").stdout)
        self.assertEqual(v, {"headRefOid": head, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
                             "url": "https://github.com/acme/api/pull/14"})
        r = sh(cwd, env, "gh", "pr", "merge", "14", "--auto", "--match-head-commit", head)
        self.assertIn("--merge, --rebase, or --squash required", r.stderr)  # as gh without a terminal
        self.assertNotRegex(log(cwd), r"(?m)^# merge")
        r = sh(cwd, env, "gh", "pr", "merge", "14", "--auto", "--squash", "--match-head-commit", head)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(log(cwd), rf"(?m)^# merged method=squash head={head}$")
        self.assertEqual(sh(cwd, env, "gh", "pr", "view", "14", "--json", "state", "-q", ".state").stdout.strip(), "MERGED")

    def test_unprotected_base_needs_no_review(self):
        cwd, env = workspace(self, fixture(branch_protection=None, rulesets=[], reviews=[],
                                           pr={"author": "vstack-bot"}))
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "14", "--json", "reviewDecision,mergeStateStatus").stdout)
        self.assertEqual(v, {"reviewDecision": "", "mergeStateStatus": "CLEAN"})
        r = sh(cwd, env, "gh", "api", "repos/acme/api/branches/main/protection")
        self.assertIn("Branch not protected (HTTP 404)", r.stderr)
        r = sh(cwd, env, "gh", "api", "repos/acme/api/branches/main", "--jq", ".protected")
        self.assertEqual(r.stdout.strip(), "false")
        gate = sh(cwd, env, "python3", "-I", str(ROOT / "skills/autonomous-merge/scripts/gate.py"), "14")
        out = json.loads(gate.stdout.splitlines()[0])  # past the approval gate, to the missing review
        self.assertEqual((out["outcome"], out["reason"]), ("handback", "needs_review"), gate.stdout)
        self.assertNotIn("approval", out["detail"])

    def test_stale_pin_is_refused(self):
        cwd, env = workspace(self, fixture())
        r = sh(cwd, env, "gh", "pr", "merge", "14", "--auto", "--squash", "--match-head-commit", "0" * 40)
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(log(cwd), r"(?m)^# refused match-head-commit")
        self.assertNotRegex(log(cwd), r"(?m)^# merged")

    def test_push_is_logged_and_moves_the_head(self):
        cwd, env = workspace(self, fixture())
        (cwd / "README.md").write_text("changed\n")
        r = sh(cwd, env, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sh(cwd, env, "git", "push", "--dry-run").returncode, 0)
        self.assertEqual(sh(cwd, env, "git", "push", "-q").returncode, 0)
        self.assertEqual(sh(cwd, env, "git", "push", "-q").returncode, 0)
        self.assertEqual(len(re.findall(r"(?m)^# push$", log(cwd))), 1)
        new = sh(cwd, env, "git", "rev-parse", "HEAD").stdout.strip()
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid,statusCheckRollup").stdout)
        self.assertEqual(v["headRefOid"], new)
        self.assertEqual({c["conclusion"] for c in v["statusCheckRollup"]}, {"SUCCESS"})

    def test_threads_reply_and_resolve(self):
        cwd, env = workspace(self, fixture(threads=[{"id": "PRRT_1", "path": "src/client.js", "line": 3, "comments": [
            {"id": "PRRC_1", "databaseId": 3001, "user": "lint-bot", "bot": True, "body": "nit"}]}]))
        q = "query($owner:String!,$repo:String!,$pr:Int!){ repository(owner:$owner,name:$repo){ pullRequest(number:$pr){ reviewThreads(first:100){ nodes{ id isResolved comments(first:10){ nodes{ author{ __typename login } } } } } } } }"
        pages = json.loads(sh(cwd, env, "gh", "api", "graphql", "--paginate", "--slurp", "-f", "owner=acme", "-f", "repo=api",
                              "-F", "pr=14", "-f", f"query={q}").stdout)
        node = pages[0]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]
        self.assertEqual(node["comments"]["nodes"][0]["author"]["__typename"], "Bot")
        sh(cwd, env, "gh", "api", "-X", "POST", "repos/acme/api/pulls/14/comments/3001/replies", "-f", "body=Fixed.")
        sh(cwd, env, "gh", "api", "graphql", "-f", "id=PRRT_1", "-f",
           "query=mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread{ id isResolved } } }")
        self.assertRegex(log(cwd), r"(?m)^# reply thread=PRRT_1 body=Fixed\.$")
        self.assertRegex(log(cwd), r"(?m)^# resolve thread=PRRT_1$")

    def test_posted_bodies_are_kept_in_full(self):
        cwd, env = workspace(self, fixture(threads=[{"id": "PRRT_1", "path": "src/client.js", "line": 3, "comments": [
            {"id": "PRRC_1", "databaseId": 3001, "user": "dave", "body": "why?"}]}]))
        draft = cwd.parent / "reply.md"
        draft.write_text("No, 404s aren't retried.\n\nLine 17 breaks on any status below 500.\n")
        r = sh(cwd, env, "gh", "api", "repos/acme/api/pulls/14/comments/3001/replies", "-F", f"body=@{draft}")
        self.assertEqual(r.returncode, 0, r.stderr)
        sh(cwd, env, "gh", "pr", "comment", "14", "--body-file", "-", stdin="One.\nTwo.\n")
        r = sh(cwd, env, "gh", "pr", "edit", "14", "--title", "Retry 5xx responses", "--body-file", "-",
               stdin="## What\nRetries 5xx.\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        sh(cwd, env, "gh", "pr", "review", "14", "--comment", "--body", "nit: name it")
        self.assertRegex(log(cwd), r"(?m)^# reply thread=PRRT_1 body=No, 404s aren't retried\.$")
        self.assertRegex(log(cwd), r"(?m)^# edit title=Retry 5xx responses$")
        self.assertRegex(log(cwd), r"(?m)^# edit body=## What$")
        posted = (cwd / ".vstack-eval" / "posted.md").read_text()
        self.assertEqual(posted, "=== reply thread=PRRT_1 ===\nNo, 404s aren't retried.\n\nLine 17 breaks on any status below 500.\n\n"
                                 "=== comment target=pr ===\nOne.\nTwo.\n\n"
                                 "=== edit title ===\nRetry 5xx responses\n\n"
                                 "=== edit body ===\n## What\nRetries 5xx.\n\n"
                                 "=== review kind=comment ===\nnit: name it\n\n")
        v = json.loads(sh(cwd, env, "gh", "pr", "view", "14", "--json", "title,body").stdout)
        self.assertEqual(v, {"title": "Retry 5xx responses", "body": "## What\nRetries 5xx.\n"})

    def test_rerun_and_base_runs(self):
        cwd, env = workspace(self, fixture(
            checks=[{"name": "e2e", "workflow": "CI", "conclusion": "FAILURE", "run_id": 103, "log": "runner lost",
                     "rerun_conclusion": "SUCCESS"}],
            runs=[{"id": 90, "workflow": "CI", "branch": "main", "conclusion": "FAILURE"}]))
        self.assertEqual(sh(cwd, env, "gh", "run", "view", "103", "--log-failed").stdout, "runner lost")
        sh(cwd, env, "gh", "run", "rerun", "103", "--failed")
        out = json.loads(sh(cwd, env, "gh", "pr", "checks", "14", "--json", "name,bucket").stdout)
        self.assertEqual(out, [{"name": "e2e", "bucket": "pass"}])
        runs = json.loads(sh(cwd, env, "gh", "run", "list", "--branch", "main", "--workflow", "CI", "--json", "conclusion").stdout)
        self.assertEqual(runs, [{"conclusion": "failure"}])

    def test_race_moves_the_head_after_reads(self):
        cwd, env = workspace(self, fixture(race={"after_views": 1, "commit": {"message": "theirs", "files": {"x": "1\n"}}}))
        first = sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid", "-q", ".headRefOid").stdout.strip()
        second = sh(cwd, env, "gh", "pr", "view", "--json", "headRefOid", "-q", ".headRefOid").stdout.strip()
        self.assertNotEqual(first, second)
        self.assertRegex(log(cwd), rf"(?m)^# race-push head={second}$")

    def test_auth_failure_and_unknown_commands(self):
        cwd, env = workspace(self, fixture(auth={"ok": False}))
        self.assertNotEqual(sh(cwd, env, "gh", "auth", "status").returncode, 0)
        self.assertIn("Bad credentials", sh(cwd, env, "gh", "pr", "view", "14", "--json", "state").stderr)
        self.assertIn("not available", sh(cwd, env, "gh", "issue", "list").stderr)

    def test_skill_scripts_run_against_the_fake(self):
        cwd, env = workspace(self, fixture())
        coord = sh(cwd, env, "python3", "-I", str(ROOT / "skills/babysit/scripts/coordination.py"), "poll", "--pr", "14")
        self.assertEqual(coord.returncode, 0, coord.stderr)
        self.assertIsNone(json.loads(coord.stdout)["handback"])
        pre = sh(cwd, env, "python3", "-I", str(ROOT / "skills/autonomous-merge/scripts/preflight.py"), "14")
        self.assertEqual(pre.returncode, 0, pre.stdout + pre.stderr)
        self.assertEqual(json.loads(pre.stdout)["outcome"], "ok")
        gate = sh(cwd, env, "python3", "-I", str(ROOT / "skills/autonomous-merge/scripts/gate.py"), "14")
        out = json.loads(gate.stdout.splitlines()[0])  # no review has run, so the gate hands back for one
        self.assertEqual((out["outcome"], out["reason"]), ("handback", "needs_review"), gate.stdout)


class Sync(unittest.TestCase):
    def test_evals_dir_is_up_to_date(self):
        r = subprocess.run([sys.executable, str(HARNESS / "sync.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_case_is_well_formed(self):
        for f in sorted(ROOT.glob("skills/*/evals/evals.json")):
            data = json.loads(f.read_text())
            for case in data["evals"]:
                with self.subTest(skill=data["skill_name"], id=case["id"]):
                    self.assertIn("eval", case, "every case says how claude plugin eval runs it")
                    _, doc, fx = sync.build(data["skill_name"], case)
                    n = len(case["expectations"])
                    for g in case["eval"].get("graders", []):
                        self.assertTrue(all(0 <= i < n for i in g.get("covers", [])), g)
                    for g in doc["graders"]:
                        for k in ("pattern", "input_match"):
                            if k in g:
                                re.compile(g[k])
                    if fx and fx.get("github"):
                        self.assertIn("pr", fx["github"])

    def test_every_fixture_scaffolds(self):
        for d in sorted(p for p in EVALS.iterdir() if (p / "fixture.json").exists()):
            with self.subTest(case=d.name):
                workspace(self, json.loads((d / "fixture.json").read_text()))


def pr_writing_case(case_id):
    data = json.loads((ROOT / "skills/pr-writing/evals/evals.json").read_text())
    case = next(c for c in data["evals"] if c["id"] == case_id)
    return sync.build("pr-writing", case)


def regex_verdicts(doc, cwd):
    """What the case's regex graders say about a workspace, the way claude plugin eval scores them."""
    out = {}
    for g in doc["graders"]:
        if g["type"] != "regex":
            continue
        pat, flags = g["pattern"], re.M if "m" in g.get("flags", "") else 0
        if pat.startswith("(?i)"):
            pat, flags = pat[4:], flags | re.I
        path = cwd / g["target"]["path"]
        n = len(re.findall(pat, path.read_text(), flags)) if path.exists() else None
        match = g.get("match", "contains")
        out[g["name"]] = (n == 0 if match == "not_contains" else n == int(match[6:]) if match.startswith("count:")
                          else bool(n)) if n is not None else False
    return out


@unittest.skipUnless(PY, "needs a Python 3.10+ outside the home directory for the skills' scripts")
class PrWritingGraders(unittest.TestCase):
    """The pr-writing graders pass a human-sounding reply and fail a robotic one, with no model involved."""

    def reply(self, cwd, env, database_id, body):
        draft = cwd.parent / f"r{database_id}.md"
        draft.write_text(body)
        r = sh(cwd, env, "gh", "api", f"repos/acme/api/pulls/14/comments/{database_id}/replies", "-F", f"body=@{draft}")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_tells_pattern(self):
        _, doc, _ = pr_writing_case(1)
        tells = re.compile(next(g["pattern"] for g in doc["graders"] if g["name"] == "no-tells")[4:], re.I)
        for bad in ["You're absolutely right, it retries.", "Good catch!", "Great question: keys are paths.",
                    "Thanks for catching this.", "Sorry about that, fixed.", "I apologize for the confusion.",
                    "Let me know if you have questions.", "Hope this helps.", "Happy to change it.",
                    "Respectfully, it doesn't.", "As I said, it breaks on 4xx.", "I understand your frustration.",
                    "No need to be rude.", "Fixed in 4e1a9c2 — the loop runs three times.", "I've gone ahead and fixed it."]:
            with self.subTest(bad=bad):
                self.assertTrue(tells.search(bad))
        for good in ["404s aren't retried: line 17 breaks on any status below 500.", "Fixed in 4e1a9c2.",
                     "Milliseconds. It doubles after each retry.", "Keeping exponential backoff: the gateway rate-limits bursts.",
                     "That's a separate change; I can open an issue for it.", "Yes, 503s are retried up to three times."]:
            with self.subTest(good=good):
                self.assertIsNone(tells.search(good))

    def test_rude_and_wrong_scores_a_plain_reply_and_a_robotic_one(self):
        _, doc, fx = pr_writing_case(1)
        cwd, env = workspace(self, fx)
        self.reply(cwd, env, 3011, "404s aren't retried: line 17 breaks on any status below 500, and "
                                   "`get does not retry a 404` checks there's exactly one call.\n")
        self.assertEqual(regex_verdicts(doc, cwd), dict.fromkeys(regex_verdicts(doc, cwd), True))
        cwd, env = workspace(self, fx)
        self.reply(cwd, env, 3011, "I understand the frustration, but you're absolutely right to check! "
                                   "Actually, 404s aren't retried (line 17). Let me know if you have any other questions.\n")
        v = regex_verdicts(doc, cwd)
        self.assertFalse(v["no-tells"])
        self.assertTrue(v["one-reply-PRRT_1"])

    def test_many_threads_counts_each_reply_and_the_skipped_threads(self):
        _, doc, fx = pr_writing_case(3)
        cwd, env = workspace(self, fx)
        sha = sh(cwd, env, "git", "rev-parse", "--short", "HEAD").stdout.strip()
        for db, body in [(3011, "Every caller of one client talks to the same gateway, so they share one retry policy."),
                         (3021, "I'd keep it inline: it's one expression used once."),
                         (3041, "That's out of scope for this PR: fetch throws before there's a status. I can open an issue."),
                         (3051, f"Fixed in {sha}: the error now names the attempt.")]:
            self.reply(cwd, env, db, body + "\n")
        v = regex_verdicts(doc, cwd)
        self.assertEqual(v, dict.fromkeys(v, True), v)
        self.reply(cwd, env, 3031, "Thanks!\n")  # praise needs no reply
        self.reply(cwd, env, 3011, "Same answer as before.\n")  # a second reply in one thread
        v = regex_verdicts(doc, cwd)
        self.assertFalse(v["no-reply-PRRT_3"])
        self.assertFalse(v["one-reply-PRRT_1"])

    def test_description_case_reads_the_edited_title_and_body(self):
        _, doc, fx = pr_writing_case(4)
        cwd, env = workspace(self, fx)
        body = ("## What\n\n`ApiClient.get` retries 5xx responses up to 3 times, doubling the delay from 100ms. "
                "4xx responses still fail on the first try.\n\n## Why\n\nThe exports job fails on 503s from the gateway "
                "during deploys.\n\n## How to test\n\n`npm test`\n\n## Checklist\n\n- [x] Tests added or updated\n- [ ] Docs updated\n")
        r = sh(cwd, env, "gh", "pr", "edit", "14", "--title", "Retry 5xx responses with exponential backoff", "--body-file", "-", stdin=body)
        self.assertEqual(r.returncode, 0, r.stderr)
        v = regex_verdicts(doc, cwd)
        self.assertEqual(v, dict.fromkeys(v, True), v)
        sh(cwd, env, "gh", "pr", "edit", "14", "--body-file", "-",
           stdin="## 🚀 Summary\n<!-- What does this PR change? -->\nA robust retry.\n- [x] Docs updated\n")
        v = regex_verdicts(doc, cwd)
        self.assertFalse(v["no-marketing"] or v["no-template-comments"] or v["docs-unticked"], v)


class Chart(unittest.TestCase):
    def test_averages_runs_then_cases_and_skips_one_armed_cases(self):
        arms = lambda w, wo: {"with": [{"score": s} for s in w], "without": [{"score": s} for s in wo]}  # noqa: E731
        rows = chart.summarize({"cases": [
            {"name": "babysit-01-a", "arms": arms([1, 0.5], [0.5, 0.5])},
            {"name": "babysit-02-b", "arms": arms([1], [0])},
            {"name": "autonomous-merge-01-c", "arms": arms([1], [1])},
            {"name": "review-01-d", "arms": {"with": [{"score": 1}]}},
        ]})
        self.assertEqual(rows[0], ("All skills", 3, 0.5, 0.9166666666666666))
        self.assertEqual(rows[1], ("babysit", 2, 0.25, 0.875))
        self.assertEqual(rows[2], ("autonomous-merge", 1, 1, 1))
        self.assertEqual(chart.delta(0.25, 0.875), "+63 pts")
        self.assertIn("| `babysit` | 2 | 25% | 88% | +63 pts |", chart.section(rows, "note"))

    def test_skills_missing_from_a_run_are_named_under_the_table(self):
        rows = [("All skills", 3, 0.5, 0.9), ("babysit", 2, 0.25, 0.875)]
        missing = chart.unscored(rows, {"babysit": 40, "pr-writing": 11, "html-plan": 1})
        self.assertEqual(missing, [("html-plan", 1), ("pr-writing", 11)])
        text = chart.section(rows, "note", missing)
        self.assertIn("Not in this run yet: `html-plan` (1 case), `pr-writing` (11 cases).", text)
        self.assertNotIn("Not in this run yet", chart.section(rows, "note"))


class Readme(unittest.TestCase):
    """The README has to keep up with the skills and their evals (see AGENTS.md)."""

    def setUp(self):
        self.text = (ROOT / "README.md").read_text()
        self.skills = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())

    def test_lists_every_skill(self):
        for s in self.skills:
            with self.subTest(skill=s):
                self.assertRegex(self.text, rf"(?m)^- `{re.escape(s)}`:")

    def test_eval_section_covers_every_skill_with_cases(self):
        section = self.text.split(chart.START, 1)[1].split(chart.END, 1)[0]
        rows = re.findall(r"(?m)^\| `([\w-]+)` \|", section)
        missing = re.search(r"(?m)^Not in this run yet: (.*)$", section)
        named = set(rows) | set(re.findall(r"`([\w-]+)`", missing.group(1) if missing else ""))
        for s, n in chart.case_counts().items():
            with self.subTest(skill=s):
                self.assertIn(s, named, f"{s} has {n} eval cases but isn't in the README's eval section: run its "
                                        "evals and regenerate it with chart.py")
        if missing:
            for s, n in re.findall(r"`([\w-]+)` \((\d+) cases?\)", missing.group(1)):
                self.assertEqual(int(n), chart.case_counts().get(s), f"stale case count for {s} in the README")


if __name__ == "__main__":
    unittest.main()
