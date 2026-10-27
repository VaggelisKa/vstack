"""Tests for gate.py against fake gh and curl on PATH and a real temp repo with
review's result file: ready, one test per handback reason, the merge call's
exact arguments (--auto, the method, --match-head-commit, never --admin), merge
errors handed back with gh's message, Bitbucket ready, merge and handbacks, and
no token in output.

Run from the repo root: python3 -m unittest discover -s skills/autonomous-merge/scripts/tests
"""
import json
import os
import unittest

from helpers import (BB, REVIEW_WRITER, SHIP, Stubs, bb_pr, bb_rule, bitbucket_rules, finding, gh_rule,
                     github_rules, pr_repo, review, run, thread, threads_page, write_result)

TOKEN = "bbtok_s3cr3t_VALUE_1234567890"
BB_URL = "https://bitbucket.org/ws/r/pull-requests/42"


class Gate(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo()
        self.stubs = Stubs()
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)

    def view(self, **over):
        v = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
             "reviewDecision": "APPROVED", "headRefOid": self.head, "baseRefOid": self.base,
             "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                                    "conclusion": "SUCCESS"}]}
        v.update(over)
        return gh_rule(["pr view 42 -R github.com/o/r --json state,isDraft,mergeable"], v)

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
        return [c["argv"] for c in self.stubs.calls("gh") if c["argv"][:2] == ["pr", "merge"]]

    def test_ready(self):
        code, data, out, err = self.gate()
        self.assertEqual(code, 0, err)
        self.assertEqual(data, {"outcome": "ready", "host": "github", "pr": 42, "head_sha": self.head,
                                "base_sha": self.base})
        self.assertEqual(self.merges(), [])

    def test_merge_args(self):
        code, data, out, err = self.gate("--merge")
        self.assertEqual(code, 0, err)
        self.assertEqual((data["outcome"], data["method"]), ("merged", "squash"))
        self.assertEqual(self.merges(), [["pr", "merge", "42", "-R", "github.com/o/r", "--auto", "--squash",
                                          "--match-head-commit", self.head]])
        argv = [a for c in self.stubs.calls() for a in c["argv"]]
        self.assertNotIn("--admin", argv)
        self.assertFalse(any(c["cmd"] == "git" or "push" in c["argv"] for c in self.stubs.calls()))

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
                code, data, out, err = self.gate("--merge", repo=repo(**allow))
                self.assertEqual((code, data["method"]), (0, method), err)
                self.assertIn(f"--{method}", self.merges()[0])

    def test_auto_merge_enabled(self):
        after = gh_rule(["--json state,autoMergeRequest"], {"state": "OPEN", "autoMergeRequest": {"enabledAt": "x"}})
        code, data, _, err = self.gate("--merge", after=after)
        self.assertEqual((code, data["outcome"]), (0, "auto_merge_enabled"), err)

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
                self.assertEqual(sum(1 for c in self.stubs.calls("gh") if c["argv"][:2] == ["pr", "merge"]), 1)
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

    def test_not_approved(self):
        self.handback("not_approved", "--merge", view=self.view(reviewDecision="REVIEW_REQUIRED"))

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

    def test_unknown_mergeability(self):
        self.handback("unknown", "--merge", view=self.view(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN"))

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
        for args in (["--admin"], [], ["42", "--force"]):
            with self.subTest(args):
                self.assertEqual(run("gate.py", *args, cwd=self.repo, env=self.stubs.env())[0], 2)
        self.assertEqual(self.stubs.calls(), [])


class BitbucketGate(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(remote="git@bitbucket.org:ws/r.git")
        self.stubs = Stubs()
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)

    def gate(self, *args, env=None, **over):
        self.stubs.set(bitbucket_rules(self.head, self.base, **over))
        return run("gate.py", BB_URL, *args, cwd=self.repo, env=env or self.stubs.env(BITBUCKET_TOKEN=TOKEN))

    def posts(self):
        return [c for c in self.stubs.calls("curl") if "POST" in c["argv"]]

    def handback(self, reason, *args, **over):
        code, data, out, err = self.gate(*args, **over)
        self.assertEqual((code, data["reason"]), (5, reason), (out, err))
        self.assertEqual(self.posts(), [])
        return data

    def pr(self, **over):
        return bb_rule(f"{BB}/pullrequests/42", bb_pr(self.head, self.base, **over))

    def test_ready(self):
        code, data, out, err = self.gate()
        self.assertEqual(code, 0, err)
        self.assertEqual((data["outcome"], data["head_sha"], data["base_sha"]), ("ready", self.head, self.base))
        self.assertEqual(self.posts(), [])

    def test_merge(self):
        code, data, out, err = self.gate("--merge")
        self.assertEqual((code, data["outcome"], data["method"]), (0, "merged", "merge_commit"), err)
        [post] = self.posts()
        self.assertTrue(post["argv"][-1].endswith("/pullrequests/42/merge"))
        body = post["argv"][post["argv"].index("--data-binary") + 1]
        self.assertTrue(body.startswith("@"))
        for c in self.stubs.calls("curl"):
            self.assertNotIn(TOKEN, " ".join(c["argv"]))

    def test_merge_strategy_fallback(self):
        branch = bb_rule(f"{BB}/refs/branches/main", {"name": "main"})
        self.assertEqual(self.gate("--merge", branch=branch)[1]["method"], "squash")
        branch = bb_rule(f"{BB}/refs/branches/main", {}, 404)
        self.assertEqual(self.gate("--merge", branch=branch)[1]["method"], "squash")

    def test_merge_refused(self):
        cases = {"insufficient_permission": (403, "You don't have write access"),
                 "branch_protection": (400, "Merge checks failed: 2 approvals needed"),
                 "merge_failed": (500, "Something went wrong")}
        for reason, (http, msg) in cases.items():
            with self.subTest(reason):
                code, data, out, err = self.gate("--merge", merge=bb_rule(f"{BB}/pullrequests/42/merge",
                                                                          {"error": {"message": msg}}, http))
                self.assertEqual((code, data["reason"]), (5, reason), (out, err))
                self.assertIn(msg, data["detail"])

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

    def test_head_moved_before_merge(self):
        first = {**self.pr(), "once": True}
        moved = bb_rule(f"{BB}/pullrequests/42", bb_pr("f" * 40, self.base))
        self.stubs.set([bb_rule(f"{BB}/commit/{'f' * 12}", {"hash": "f" * 40}),
                        *bitbucket_rules(self.head, self.base, pr=None, repo=None), first, moved,
                        bb_rule(BB, {"full_name": "ws/r"})])
        code, data, out, err = run("gate.py", BB_URL, "--merge", cwd=self.repo,
                                   env=self.stubs.env(BITBUCKET_TOKEN=TOKEN))
        self.assertEqual((code, data["reason"]), (5, "merge_failed"), (out, err))
        self.assertIn("head moved", data["detail"])
        self.assertEqual(self.posts(), [])

    def test_token_never_printed(self):
        leak = bb_rule(f"{BB}/pullrequests/42/merge", {"error": {"message": f"bad token {TOKEN}"}}, 500,
                       stderr=f"Authorization: Bearer {TOKEN}")
        code, data, out, err = self.gate("--merge", merge=leak)
        self.assertEqual(code, 5)
        self.assertNotIn(TOKEN, out + err)
        self.assertIn("[redacted]", data["detail"])


if __name__ == "__main__":
    unittest.main()
