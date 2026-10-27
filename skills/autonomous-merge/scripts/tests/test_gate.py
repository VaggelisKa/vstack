"""Tests for gate.py against fake gh and curl on PATH and a real temp repo with
review's result file: ready, one test per handback reason, approvals that must
be fresh and not the author's, re-polling unknown mergeability, the merge call's
exact arguments (--auto, the method, --match-head-commit) only after every
check passes, polling until merged with --disable-auto on any change (a
foreign 👀 on the PR after arming included: claimed_items), the
deadline, Ctrl-C, SIGTERM or an internal error (merged if the PR just landed),
bot approvals not counting, paginated rulesets that block when unreadable, merge
errors handed back with gh's message, never --admin, a push or a dismissal,
Bitbucket handbacks and never a Bitbucket merge, and no token in output.

Run from the repo root: python3 -m unittest discover -s skills/autonomous-merge/scripts/tests
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import (BB, COORD_MATCH, REVIEW_WRITER, SCRIPTS, SHIP, VIEW_MATCH, Stubs, bb_pr, bb_rule,
                     bitbucket_rules, commit_all, coord_view, finding, gh_review, gh_rule, git, github_rules,
                     github_view, pr_repo, review, run, seed_coordination, thread, threads_page, write_result)

spec = importlib.util.spec_from_file_location("am_gate", SCRIPTS / "gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
REAL_GH = gate.c.gh

TOKEN = "bbtok_s3cr3t_VALUE_1234567890"
BB_URL = "https://bitbucket.org/ws/r/pull-requests/42"
HEAD, BASE = "a" * 40, "b" * 40


class Gate(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo()
        self.stubs = Stubs()
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)
        seed_coordination(self.repo, self.head)

    def tearDown(self):
        for call in self.stubs.calls():
            argv = call["argv"]
            self.assertFalse({"--admin", "--force", "--force-with-lease", "push", "dismiss"} & set(argv), argv)
            self.assertFalse(any("dismissals" in a or "-X" == a for a in argv), argv)
            self.assertNotEqual(call["cmd"], "git", argv)

    def view(self, *seq, **over):
        """The gh pr view rule; with seq, one answer per call in order, the last repeating."""
        views = [github_view(self.head, self.base, **v) for v in seq] or [github_view(self.head, self.base, **over)]
        rules = [gh_rule(VIEW_MATCH, v) for v in views]
        return [{**r, "once": True} for r in rules[:-1]] + rules[-1:]

    def merged(self, *before):
        """Views for --merge: open for the gate, then each of before, then merged."""
        return self.view({}, *before, {"state": "MERGED"})

    def reviews(self, *rvs):
        return gh_rule(["repos/o/r/pulls/42/reviews"], [list(rvs)])

    def disables(self):
        return [c["argv"] for c in self.stubs.calls("gh") if "--disable-auto" in c["argv"]]

    def gate(self, *args, rules=None, env=None, **over):
        self.stubs.set(rules or github_rules(self.head, self.base, **over))
        return run("gate.py", "42", *args, cwd=self.repo, env=env or self.stubs.env())

    def handback(self, reason, *args, **kw):
        code, data, out, err = self.gate(*args, **kw)
        self.assertEqual(code, 5, (out, err))
        self.assertEqual((data["outcome"], data["reason"]), ("handback", reason), data)
        self.assertEqual(sorted(data), ["action", "detail", "outcome", "reason"])
        self.assertIn(f"({reason})", err)
        self.assertEqual(self.merges(), [])
        return data

    def merges(self):
        return [c["argv"] for c in self.stubs.calls("gh") if c["argv"][:2] == ["pr", "merge"]
                and "--disable-auto" not in c["argv"]]

    def test_ready(self):
        code, data, out, err = self.gate()
        self.assertEqual(code, 0, err)
        self.assertEqual(data, {"outcome": "ready", "host": "github", "pr": 42, "head_sha": self.head,
                                "base_sha": self.base})
        self.assertEqual(self.merges(), [])

    def test_merge_args(self):
        code, data, out, err = self.gate("--merge", view=self.merged())
        self.assertEqual(code, 0, err)
        self.assertEqual((data["outcome"], data["method"]), ("merged", "squash"))
        self.assertEqual(self.merges(), [["pr", "merge", "42", "-R", "github.com/o/r", "--auto", "--squash",
                                          "--match-head-commit", self.head]])
        self.assertEqual(self.disables(), [])

    def test_merge_polls_until_merged(self):
        code, data, out, err = self.gate("--merge", view=self.merged({}, {"mergeStateStatus": "UNKNOWN"}))
        self.assertEqual((code, data["outcome"]), (0, "merged"), err)
        views = [c for c in self.stubs.calls("gh") if "state,isDraft,mergeable" in " ".join(c["argv"])]
        self.assertEqual(len(views), 4)
        self.assertEqual(self.disables(), [])

    def test_merge_head_change_disables_auto(self):
        moved = {"headRefOid": "f" * 40}
        code, data, out, err = self.gate("--merge", view=self.merged(moved))
        self.assertEqual((code, data["reason"]), (5, "needs_review"), (out, err))
        self.assertIn("auto-merge disabled", data["detail"])
        self.assertEqual(self.disables(), [["pr", "merge", "42", "-R", "github.com/o/r", "--disable-auto"]])
        self.assertEqual(len(self.merges()), 1)
        self.stubs.clear()
        code, data, out, err = self.gate("--merge", view=self.merged({"baseRefOid": "e" * 40}))
        self.assertEqual((code, data["reason"], len(self.disables())), (5, "needs_review", 1), (out, err))

    def test_merge_new_block_disables_auto(self):
        threads = [{**gh_rule(["api graphql"], [threads_page(thread(resolved=True))]), "once": True},
                   gh_rule(["api graphql"], [threads_page(thread())])]
        code, data, out, err = self.gate("--merge", view=self.merged({}), threads=threads)
        self.assertEqual((code, data["reason"]), (5, "unresolved_threads"), (out, err))
        self.assertEqual(len(self.disables()), 1)
        for name, over in {"changes": {"reviews": [{**self.reviews(gh_review(commit=self.head)), "once": True},
                                                   self.reviews(gh_review(commit=self.head),
                                                                gh_review("CHANGES_REQUESTED", "carol"))]},
                           "checks": {"checks": [{**gh_rule(["pr checks"], [{"name": "test", "bucket": "pass"}]),
                                                  "once": True},
                                                 gh_rule(["pr checks"], [{"name": "test", "bucket": "fail"}], 1)]},
                           "blocked": {"view": self.merged({"mergeStateStatus": "BLOCKED"})}}.items():
            with self.subTest(name):
                self.stubs.clear()
                code, data, out, err = self.gate("--merge", **{"view": self.merged({}), **over})
                self.assertEqual(code, 5, (out, err))
                self.assertEqual(len(self.disables()), 1, data)

    def test_merge_foreign_claim_after_arming_disables_auto(self):
        eyes = {"id": 7, "content": "eyes", "user": {"login": "bob"}, "created_at": "2026-10-27T11:00:00Z"}
        rx = [{**gh_rule(["issues/42/reactions"], [[]]), "once": True}, gh_rule(["issues/42/reactions"], [[eyes]])]
        code, data, out, err = self.gate("--merge", view=self.view({}, {}), pr_reactions=rx)
        self.assertEqual((code, data["reason"]), (5, "claimed_items"), (out, err))
        self.assertIn("the PR (bob reacted 👀 on the PR after the last commit)", data["detail"])
        self.assertIn("auto-merge disabled", data["detail"])
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(self.disables(), [["pr", "merge", "42", "-R", "github.com/o/r", "--disable-auto"]])

    def test_merge_timeout_and_unreadable_pr_disable_auto(self):
        code, data, out, err = self.gate("--merge", view=self.view({}))
        self.assertEqual((code, data["reason"], len(self.disables())), (5, "merge_failed", 1), (out, err))
        self.stubs.clear()
        gone = [self.view({}, {})[0], gh_rule(VIEW_MATCH, "", 1, "HTTP 502")]
        code, data, out, err = self.gate("--merge", view=gone)
        self.assertEqual((code, data["outcome"], data["reason"]), (5, "handback", "unknown"), (out, err))
        self.assertIn("reading PR 42 back failed", data["detail"])
        self.assertEqual(len(self.disables()), 1)

    def test_disable_auto_failure_is_reported(self):
        code, data, out, err = self.gate("--merge", view=self.merged({"headRefOid": "f" * 40}),
                                         disable=gh_rule(["--disable-auto"], "", 1, "HTTP 502"))
        self.assertEqual((code, data["reason"]), (5, "needs_review"), (out, err))
        self.assertIn("couldn't disable auto-merge", data["detail"])
        self.assertIn("--disable-auto", data["action"])

    def test_disable_auto_failing_because_it_merged_reports_merged(self):
        merged_now = gh_rule(["pr view 42 -R github.com/o/r --json state"], {"state": "MERGED"})
        code, data, out, err = self.gate("--merge", view=self.view({}, {"headRefOid": "f" * 40}),
                                         disable=gh_rule(["--disable-auto"], "", 1, "Pull request is merged"),
                                         reread=merged_now)
        self.assertEqual((code, data["outcome"]), (0, "merged"), (out, err))
        self.assertEqual(len(self.disables()), 1)

    def test_merge_deadline_disables_auto(self):
        code, data, out, err = self.gate("--merge", view=self.view({}),
                                         env=self.stubs.env(VSTACK_MERGE_DEADLINE="0"))
        self.assertEqual((code, data["reason"]), (5, "merge_failed"), (out, err))
        self.assertIn("0s deadline", data["detail"])
        self.assertIn("auto-merge disabled", data["detail"])
        self.assertEqual((len(self.merges()), len(self.disables())), (1, 1))
        for bad in ("481", "-1", "x", "nan"):
            with self.subTest(bad):
                self.stubs.clear()
                code, _, out, err = self.gate("--merge", env=self.stubs.env(VSTACK_MERGE_DEADLINE=bad))
                self.assertEqual((code, self.merges()), (2, []), (out, err))

    def test_sigterm_while_polling_disables_auto(self):
        self.stubs.set(github_rules(self.head, self.base))
        proc = subprocess.Popen([sys.executable, "-I", str(SCRIPTS / "gate.py"), "42", "--merge"], cwd=self.repo,
                                env=self.stubs.env(VSTACK_MERGE_POLL_DELAYS="60"), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            limit = time.monotonic() + 30
            while len([c for c in self.stubs.calls("gh") if "graphql" in c["argv"]]) < 3:
                self.assertIsNone(proc.poll(), "the gate exited before it polled")
                self.assertLess(time.monotonic(), limit, "the gate never polled")
                time.sleep(0.05)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=60)
        finally:
            proc.kill()
        data = json.loads(out)
        self.assertEqual((proc.returncode, data["reason"]), (5, "unknown"), (out, err))
        self.assertIn("stopped by SIGTERM", data["detail"])
        self.assertIn("auto-merge disabled", data["detail"])
        self.assertEqual(self.disables(), [["pr", "merge", "42", "-R", "github.com/o/r", "--disable-auto"]])

    def test_merge_method(self):
        def repo(**allow):
            return gh_rule(["api --hostname github.com repos/o/r"], {"permissions": {"push": True}, **allow})
        cases = [({"allow_rebase_merge": True, "allow_squash_merge": False, "allow_merge_commit": False}, "rebase"),
                 ({"allow_merge_commit": True, "allow_squash_merge": False, "allow_rebase_merge": False}, "merge"),
                 ({"allow_merge_commit": True, "allow_squash_merge": False, "allow_rebase_merge": True}, "merge"),
                 ({"allow_merge_commit": True, "allow_squash_merge": True, "allow_rebase_merge": False}, "squash"),
                 ({}, "squash")]
        for allow, method in cases:
            with self.subTest(allow=allow):
                self.stubs.clear()
                code, data, out, err = self.gate("--merge", repo=repo(**allow), view=self.merged())
                self.assertEqual((code, data["method"]), (0, method), err)
                self.assertIn(f"--{method}", self.merges()[0])

    def test_merge_errors_handed_back_with_gh_message(self):
        cases = {"insufficient_permission": "GraphQL: user does not have permission to merge (mergePullRequest)",
                 "branch_protection": "X Pull request o/r#42 is not mergeable: the base branch policy prohibits "
                                      "the merge. Protected branch rules not satisfied",
                 "merge_failed": "GraphQL: Head branch was modified. Review and try the merge again."}
        for reason, msg in cases.items():
            with self.subTest(reason):
                self.stubs.set(github_rules(self.head, self.base, merge=gh_rule(["pr merge"], "", 1, msg)))
                code, data, out, err = run("gate.py", "42", "--merge", cwd=self.repo, env=self.stubs.env())
                self.assertEqual((code, data["reason"]), (5, reason), (out, err))
                self.assertIn(msg.split(":")[0][:20], data["detail"])
                self.assertEqual((len(self.merges()), self.disables()), (1, []))
                self.stubs.clear()

    def test_not_open(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state):
                self.assertIn(state.lower(), self.handback("not_open", "--merge", view=self.view(state=state))["detail"])

    def test_draft(self):
        self.handback("draft", "--merge", view=self.view(isDraft=True, mergeStateStatus="DRAFT"))

    def test_conflicts(self):
        data = self.handback("conflicts", "--merge", view=self.view(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.handback("conflicts", view=self.view(mergeStateStatus="DIRTY"))
        self.assertNotIn("also", data["detail"])

    def test_changes_requested(self):
        self.handback("changes_requested", "--merge", view=self.view(reviewDecision="CHANGES_REQUESTED"))
        standing = self.reviews(gh_review("CHANGES_REQUESTED", "carol"), gh_review("COMMENTED", "carol"),
                                gh_review(commit=self.head))
        self.assertIn("carol", self.handback("changes_requested", reviews=standing)["detail"])
        cleared = self.reviews(gh_review("CHANGES_REQUESTED", "carol"), gh_review(login="carol", commit=self.head))
        self.assertEqual(self.gate(reviews=cleared)[0], 0)
        bot = self.reviews(gh_review("CHANGES_REQUESTED", "lint-bot", bot=True), gh_review(commit=self.head))
        self.assertEqual(self.gate(reviews=bot)[0], 0)
        self.assertIn("couldn't read", self.handback(
            "changes_requested", reviews=gh_rule(["pulls/42/reviews"], "", 1, "HTTP 502"))["detail"])

    def test_not_approved(self):
        self.handback("not_approved", "--merge", view=self.view(reviewDecision="REVIEW_REQUIRED"),
                      reviews=self.reviews())
        stale = self.reviews(gh_review(commit=self.base))
        self.assertIn("stale", self.handback("not_approved", "--merge", reviews=stale)["detail"])
        own = self.reviews(gh_review(login="me", commit=self.head))
        self.handback("not_approved", reviews=own)
        withdrawn = self.reviews(gh_review(commit=self.head), gh_review("DISMISSED"))
        self.handback("not_approved", reviews=withdrawn)
        bots = self.reviews(gh_review(login="ci-bot", commit=self.head, bot=True),
                            {**gh_review(login="app", commit=self.head), "author": {"__typename": "Bot"}})
        self.assertIn("found 0", self.handback("not_approved", "--merge", reviews=bots)["detail"])

    def test_approval_only_when_required(self):
        none = self.view(reviewDecision=None)
        self.assertEqual(self.gate(view=none, reviews=self.reviews())[0], 0)
        self.assertEqual(self.gate(view=none, reviews=self.reviews(gh_review(commit=self.base)))[0], 0)
        ruleset = gh_rule(["rules/branches/main"], [[{"type": "pull_request",
                                                      "parameters": {"required_approving_review_count": 2}}]])
        data = self.handback("not_approved", view=none, rulesets=ruleset)
        self.assertIn("2 approval(s)", data["detail"])
        two = self.reviews(gh_review(commit=self.head), gh_review(login="dan", commit=self.head))
        self.assertEqual(self.gate(view=none, rulesets=ruleset, reviews=two)[0], 0)
        absent = gh_rule(["rules/branches/main"], "", 1, "gh: Not Found (HTTP 404)")
        self.assertEqual(self.gate(view=none, reviews=self.reviews(), rulesets=absent)[0], 0)

    def test_rulesets_paginated_and_unreadable_blocks(self):
        none = self.view(reviewDecision=None)
        paged = gh_rule(["rules/branches/main"], [[{"type": "required_signatures"}],
                                                  [{"type": "pull_request",
                                                    "parameters": {"required_approving_review_count": 3}}]])
        self.assertIn("3 approval(s)", self.handback("not_approved", view=none, rulesets=paged)["detail"])
        call = next(c["argv"] for c in self.stubs.calls("gh") if "rules/branches" in " ".join(c["argv"]))
        self.assertIn("--paginate", call)
        for name, rule in {"403": gh_rule(["rules/branches/main"], "", 1, "HTTP 403"),
                           "502": gh_rule(["rules/branches/main"], "", 1, "HTTP 502"),
                           "not JSON": gh_rule(["rules/branches/main"], "<html>"),
                           "not pages": gh_rule(["rules/branches/main"], {"message": "x"})}.items():
            with self.subTest(name):
                self.stubs.clear()
                data = self.handback("unknown", "--merge", view=none, reviews=self.reviews(), rulesets=rule)
                self.assertIn("rulesets", data["detail"])

    def test_checks_failing(self):
        checks = gh_rule(["pr checks"], [{"name": "test", "bucket": "fail"}, {"name": "lint", "bucket": "pass"}], 1)
        self.assertIn("test (failing)", self.handback("checks_failing", "--merge", checks=checks)["detail"])
        pending = gh_rule(["pr checks"], [{"name": "test", "bucket": "pending"}], 8)
        self.assertIn("test (pending)", self.handback("checks_failing", checks=pending)["detail"])

    def test_checks_fall_back_to_every_check(self):
        none = gh_rule(["pr checks"], "", 1, "no required checks reported on the 'feature' branch")
        rollup = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
                  {"__typename": "StatusContext", "context": "ci/legacy", "state": "FAILURE"},
                  {"__typename": "CheckRun", "name": "slow", "status": "IN_PROGRESS", "conclusion": None}]
        data = self.handback("checks_failing", checks=none, view=self.view(statusCheckRollup=rollup))
        self.assertIn("ci/legacy (failing)", data["detail"])
        self.assertIn("slow (pending)", data["detail"])
        self.assertIn("all treated as required", data["detail"])
        self.assertEqual(self.gate(checks=none)[0], 0)

    def test_unresolved_threads(self):
        human = gh_rule(["api graphql"], [threads_page(thread(bot=True, resolved=True)), threads_page(thread())])
        data = self.handback("unresolved_threads", "--merge", threads=human)
        self.assertIn("human", data["detail"])
        self.assertIn("A human reviewer must resolve", data["action"])
        bot = gh_rule(["api graphql"], [threads_page(thread(bot=True))])
        self.assertIn("babysit", self.handback("unresolved_threads", threads=bot)["action"])
        for name, rule in {"error": gh_rule(["api graphql"], "", 1, "HTTP 502"),
                           "page left": gh_rule(["api graphql"], [threads_page(more=True)])}.items():
            with self.subTest(name):
                self.assertIn("couldn't read", self.handback("unresolved_threads", threads=rule)["detail"])

    def test_branch_protection(self):
        self.handback("branch_protection", "--merge", view=self.view(mergeStateStatus="BLOCKED"))
        self.assertIn("behind", self.handback("branch_protection", view=self.view(mergeStateStatus="BEHIND"))["detail"])

    def view_calls(self):
        return [c for c in self.stubs.calls("gh") if "state,isDraft,mergeable" in " ".join(c["argv"])]

    def test_unknown_mergeability(self):
        data = self.handback("unknown", "--merge", view=self.view(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN"))
        self.assertIn("after re-polling", data["detail"])
        self.assertEqual(len(self.view_calls()), 5)

    def test_unknown_mergeability_recovers(self):
        unknown = {"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}
        code, data, out, err = self.gate(view=self.view(unknown, unknown, {}))
        self.assertEqual((code, data["outcome"]), (0, "ready"), (out, err))
        self.assertEqual(len(self.view_calls()), 3)

    def test_poll_delays_validated(self):
        for bad in ("x", "-1", "61", "nan"):
            with self.subTest(bad):
                self.stubs.clear()
                code, _, out, err = self.gate(env=self.stubs.env(VSTACK_POLL_DELAYS=bad))
                self.assertEqual(code, 2, (out, err))

    def test_first_block_wins_and_the_rest_are_listed(self):
        data = self.handback("draft", view=self.view(isDraft=True, reviewDecision="CHANGES_REQUESTED"))
        self.assertIn("also changes_requested", data["detail"])

    def test_needs_review(self):
        out = os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42", "review.json")
        os.unlink(out)
        self.assertIn("no usable review.json", self.handback("needs_review", "--merge")["detail"])
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, review(finding()))
        self.assertIn("isn't a clean Ship", self.handback("needs_review", "--merge")["detail"])
        write_result(REVIEW_WRITER, self.repo, 42, self.base, self.base, SHIP)
        self.assertIn("another head", self.handback("needs_review", "--merge")["detail"])
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)
        with open(out) as f:
            data = json.load(f)
        data["merge_base"] = self.head
        with open(out, "w") as f:
            json.dump(data, f)
        self.assertIn("merge_base", self.handback("needs_review", "--merge")["detail"])

    def test_update_branch_moves_the_head(self):
        """gh pr update-branch merges the base in: a new head, so coordination isn't settled and review reruns."""
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "README").write_text("base moved\n")
        base = commit_all(self.repo, "Base moves")
        git(self.repo, "checkout", "-q", "feature")
        git(self.repo, "merge", "-q", "--no-edit", "main")
        head = git(self.repo, "rev-parse", "HEAD").decode().strip()
        commits = [{"oid": self.head, "messageHeadline": "head", "authors": [{"login": "me"}]},
                   {"oid": head, "messageHeadline": "Merge branch 'main' into feature", "authors": [{"login": "me"}]}]
        data = self.handback("needs_review", "--merge", view=gh_rule(VIEW_MATCH, github_view(head, base)),
                             reviews=self.reviews(gh_review(commit=head)),
                             coord_view=gh_rule(COORD_MATCH, coord_view(head, commits=commits)))
        self.assertIn("review.json is for another head or base", data["detail"])
        self.assertIn("also not_settled: the PR isn't settled: new_head", data["detail"])

    def coord_item(self, **item):
        path = os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42", "coordination.json")
        with open(path) as f:
            st = json.load(f)
        st["items"]["check:lint"] = {"state": "open", "since_head": self.base, "state_head": self.base, **item}
        with open(path, "w") as f:
            json.dump(st, f)

    def lint(self, message="Fix lint", author="copilot-swe-agent"):
        """The coordination view: lint failing on head, a commit by another fixer on top of base."""
        commits = [{"oid": self.base, "messageHeadline": "base", "authors": [{"login": "me"}]},
                   {"oid": self.head, "messageHeadline": message, "authors": [{"login": author}]}]
        rollup = [{"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}]
        return gh_rule(COORD_MATCH, coord_view(self.head, commits=commits, statusCheckRollup=rollup))

    def test_not_settled(self):
        os.unlink(os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42", "coordination.json"))
        data = self.handback("not_settled", "--merge")
        self.assertIn("first_look", data["detail"])
        self.assertEqual(self.gate()[0], 0)
        working = coord_view(self.head, comments=[{"author": {"login": "alice"}, "body": "I'm working on this",
                                                   "createdAt": "2026-10-27T11:00:00Z"}])
        self.assertIn("working: alice", self.handback("not_settled", coord_view=gh_rule(COORD_MATCH, working))["detail"])
        running = coord_view(self.head, statusCheckRollup=[{"__typename": "CheckRun", "name": "e2e",
                                                            "status": "IN_PROGRESS"}])
        self.assertIn("checks_running: e2e",
                      self.handback("not_settled", coord_view=gh_rule(COORD_MATCH, running))["detail"])

    def test_claimed_items(self):
        self.coord_item()
        data = self.handback("claimed_items", "--merge", coord_view=self.lint())
        self.assertIn("check:lint (copilot-swe-agent)", data["detail"])

    def test_stale_claim_and_conflicting_fixer(self):
        self.coord_item(state="claimed", claimed_since_head=self.base, cycle_heads=["c" * 40], claimant="x")
        data = self.handback("stale_claim", "--merge", coord_view=self.lint())
        self.assertIn("take over", data["action"])
        self.assertIn("also claimed_items", data["detail"])
        self.coord_item(state="addressed", addressed_by="own")
        data = self.handback("conflicting_fixer", "--merge", coord_view=self.lint("Refactor"))
        self.assertIn("check:lint", data["detail"])

    def test_coordination_unreadable_is_unknown(self):
        path = os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42", "coordination.json")
        with open(path, "w") as f:
            f.write("{")
        self.assertIn("coordination", self.handback("unknown", "--merge")["detail"])

    def test_review_file_cannot_override_live_state(self):
        self.handback("checks_failing", "--merge", checks=gh_rule(["pr checks"], [{"name": "t", "bucket": "fail"}], 1))

    def test_access_handbacks(self):
        stubs = Stubs(tools=("curl",))
        stubs.set([])
        self.handback("no_cli", "--merge", env=stubs.env())
        self.handback("auth_missing", "--merge", auth=gh_rule(["auth status"], "", 1, "You are not logged into any GitHub hosts."))
        self.handback("auth_expired", "--merge", auth=gh_rule(["auth status"], "", 1, "The token in GH_TOKEN is invalid."))
        self.handback("insufficient_permission", "--merge",
                      repo=gh_rule(["api --hostname github.com repos/o/r"], {"permissions": {"push": False}}))

    def test_host_unsupported(self):
        self.stubs.set([])
        code, data, _, _ = run("gate.py", "https://gitlab.com/o/r/-/merge_requests/42", "--merge", cwd=self.repo,
                               env=self.stubs.env())
        self.assertEqual((code, data["reason"]), (5, "host_unsupported"))

    def test_usage(self):
        self.stubs.set([])
        for args in (["--admin"], [], ["42", "--force"], ["-h"], ["--help"], ["42", "-h"]):
            with self.subTest(args):
                self.assertEqual(run("gate.py", *args, cwd=self.repo, env=self.stubs.env())[0], 2)
        self.assertEqual(self.stubs.calls(), [])


class MergeWait(unittest.TestCase):
    """merge_and_wait in-process, with gh, the checks, sleep and the clock faked."""
    T = {"host": "github", "hostname": "github.com", "owner": "o", "repo": "r", "pr": 42}

    def setUp(self):
        self.calls, self.now, self.slept = [], 0.0, []
        self.signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        for target, name, fake in ((gate.c, "gh", self.gh), (gate, "github_blocks", lambda *a, **k: gate.Blocks()),
                                   (gate, "coordination_block", lambda *a, **k: None)):
            p = mock.patch.object(target, name, fake)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.assertEqual({s: signal.getsignal(s) for s in self.signals}, self.signals)
        self.assertIsNone(gate.c.DEADLINE)

    def gh(self, *args, timeout=120):
        self.calls.append(list(args))
        out = json.dumps(github_view(HEAD, BASE)) if "--json" in args else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def disables(self):
        return [a for a in self.calls if "--disable-auto" in a]

    def wait(self, delays=(5,), seconds=480, sleep=None):
        with self.assertRaises(gate.c.Handback) as cm:
            gate.merge_and_wait(self.T, HEAD, BASE, "squash", delays, seconds, sleep=sleep or self.sleep,
                                clock=self.clock)
        return cm.exception

    def test_keyboard_interrupt_while_polling_disables_auto(self):
        def interrupt(_):
            raise KeyboardInterrupt
        h = self.wait(sleep=interrupt)
        self.assertEqual(h.reason, "unknown")
        self.assertIn("interrupted (Ctrl-C)", h.detail)
        self.assertIn("auto-merge disabled", h.detail)
        self.assertEqual(self.disables(), [["pr", "merge", "42", "-R", "github.com/o/r", "--disable-auto"]])
        self.assertEqual(len([a for a in self.calls if "--auto" in a]), 1)

    def test_internal_error_while_polling_disables_auto(self):
        with mock.patch.object(gate, "github_blocks", side_effect=TypeError("boom")):
            h = self.wait()
        self.assertEqual(h.reason, "unknown")
        self.assertIn("internal error (TypeError)", h.detail)
        self.assertEqual(len(self.disables()), 1)

    def test_deadline_cuts_polling_short(self):
        h = self.wait(delays=(100, 100, 100), seconds=150)
        self.assertEqual(h.reason, "merge_failed")
        self.assertIn("150s deadline", h.detail)
        self.assertEqual(self.slept, [100, 50])
        self.assertEqual(len(self.disables()), 1)

    def test_gh_timeouts_capped_to_the_deadline(self):
        seen = []

        def fake_run(argv, env=None, timeout=120):
            seen.append(timeout)
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.object(gate.c, "run", fake_run), \
                mock.patch.object(gate.c, "DEADLINE", time.monotonic() + 30):
            REAL_GH("pr", "view", "42")
            REAL_GH("pr", "view", "42", timeout=5)
        with mock.patch.object(gate.c, "run", fake_run):
            REAL_GH("pr", "view", "42")
        self.assertTrue(25 <= seen[0] <= 30, seen)
        self.assertEqual(seen[1:], [5, 120])


class BitbucketGate(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(remote="git@bitbucket.org:ws/r.git")
        self.stubs = Stubs()
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)

    def tearDown(self):
        for call in self.stubs.calls("curl"):
            self.assertFalse({"-X", "POST", "--data-binary", "--data", "-d"} & set(call["argv"]), call["argv"])
            self.assertFalse(call["argv"][-1].endswith("/merge"), call["argv"])

    def gate(self, *args, env=None, **over):
        self.stubs.set(bitbucket_rules(self.head, self.base, **over))
        return run("gate.py", BB_URL, *args, cwd=self.repo, env=env or self.stubs.env(BITBUCKET_TOKEN=TOKEN))

    def handback(self, reason, *args, **over):
        code, data, out, err = self.gate(*args, **over)
        self.assertEqual((code, data["reason"]), (5, reason), (out, err))
        return data

    def pr(self, **over):
        return bb_rule(f"{BB}/pullrequests/42", bb_pr(self.head, self.base, **over))

    def test_never_merges(self):
        for args in ([], ["--merge"]):
            with self.subTest(args):
                self.stubs.clear()
                data = self.handback("merge_unsupported_host", *args)
                self.assertIn("ready to merge", data["detail"])
                self.assertIn("can't be pinned to a head commit", data["detail"])
                self.assertIn("Merge it yourself in Bitbucket", data["action"])
                self.assertIn(self.head[:12], data["action"])
                calls = self.stubs.calls("curl")
                self.assertTrue(calls)
                self.assertFalse(any("/merge" in c["argv"][-1] for c in calls))

    def test_handbacks(self):
        self.handback("not_open", "--merge", pr=self.pr(state="DECLINED"))
        self.handback("draft", "--merge", pr=self.pr(draft=True))
        self.handback("conflicts", "--merge",
                      diffstat=bb_rule(f"{BB}/pullrequests/42/diffstat", {"values": [{"status": "merge conflict"}]}))
        self.handback("changes_requested", "--merge", pr=self.pr(participants=[
            {"user": {"uuid": "{rev}"}, "approved": False, "state": "changes_requested"}]))
        self.handback("not_approved", "--merge", pr=self.pr(participants=[
            {"user": {"uuid": "{author}"}, "approved": True, "state": "approved"}]))
        self.handback("checks_failing", "--merge", statuses=bb_rule(
            f"{BB}/pullrequests/42/statuses", {"values": [{"name": "build", "state": "INPROGRESS"}]}))
        self.handback("unresolved_threads", "--merge",
                      tasks=bb_rule(f"{BB}/pullrequests/42/tasks", {"values": [{"state": "UNRESOLVED"}]}))
        self.handback("unresolved_threads", "--merge", comments=bb_rule(
            f"{BB}/pullrequests/42/comments", {"values": [{"inline": {"path": "a"}, "deleted": False}]}))
        self.handback("auth_missing", "--merge", env=self.stubs.env())
        self.handback("auth_expired", "--merge", repo=bb_rule(BB, {}, 401), pr=bb_rule(f"{BB}/pullrequests/42", {}, 401))

    def test_no_build_status_hands_back(self):
        data = self.handback("checks_failing", "--merge",
                             statuses=bb_rule(f"{BB}/pullrequests/42/statuses", {"values": []}))
        self.assertIn("no build status", data["detail"])

    def test_line_breaks_in_credentials_refused(self):
        for env in ({"BITBUCKET_TOKEN": "abc\ndef"}, {"BITBUCKET_TOKEN": "abc\rheader = x"},
                    {"BITBUCKET_USERNAME": "me\n", "BITBUCKET_APP_PASSWORD": "pw"},
                    {"BITBUCKET_USERNAME": "me", "BITBUCKET_APP_PASSWORD": "p\rw"}):
            with self.subTest(env):
                self.stubs.clear()
                data = self.handback("auth_missing", env=self.stubs.env(**env))
                self.assertIn("line break", data["detail"])
                self.assertEqual(self.stubs.calls("curl"), [])

    def test_needs_review(self):
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, review(finding(severity="blocker")))
        self.handback("needs_review", "--merge")

    def test_pages_followed(self):
        page2 = f"{BB}/pullrequests/42/statuses?page=2"
        statuses = bb_rule(f"{BB}/pullrequests/42/statuses", {"values": [{"name": "a", "state": "SUCCESSFUL"}],
                                                              "next": page2})
        self.stubs.set([bb_rule(page2, {"values": [{"name": "b", "state": "FAILED"}]}),
                        *bitbucket_rules(self.head, self.base, statuses=statuses)])
        code, data, _, err = run("gate.py", BB_URL, cwd=self.repo, env=self.stubs.env(BITBUCKET_TOKEN=TOKEN))
        self.assertEqual((code, data["reason"]), (5, "checks_failing"), err)
        self.assertIn("b (failed)", data["detail"])

    def test_token_never_printed(self):
        leak = bb_rule(f"{BB}/pullrequests/42/statuses", {"error": {"message": f"bad token {TOKEN}"}}, 500,
                       stderr=f"Authorization: Bearer {TOKEN}")
        code, data, out, err = self.gate("--merge", statuses=leak)
        self.assertEqual(code, 5)
        self.assertNotIn(TOKEN, out + err)
        self.assertIn("[redacted]", data["detail"])
        for c in self.stubs.calls("curl"):
            self.assertNotIn(TOKEN, " ".join(c["argv"]))


if __name__ == "__main__":
    unittest.main()
