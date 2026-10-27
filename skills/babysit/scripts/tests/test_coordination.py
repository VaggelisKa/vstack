"""Tests for coordination.py against a fake gh on PATH and a real temp repo:
settled and not settled, each item state for checks and threads, own vs
foreign by marker, stale claims by CI cycles and by quiet polls, take-over,
conflicting_fixer, the claim subcommand's exact gh calls, --input, the state
file, usage errors and no token in output.

Run from the repo root: python3 -m unittest discover -s skills/babysit/scripts/tests
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import SCRIPTS, FakeGh, commit_all, pr_repo, tmpdir

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123"


def at(n):
    return f"2026-10-27T{10 + n:02d}:00:00Z"


def commit(oid, msg, login="me", n=0):
    headline, _, body = msg.partition("\n")
    return {"oid": oid, "messageHeadline": headline, "messageBody": body.strip("\n"), "committedDate": at(n),
            "authors": [{"login": login}]}


def check(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def comment(login, body, n, cid=None, eyes=None, bot=False):
    reactions = [{"content": "EYES", "createdAt": at(n + 1), "user": {"login": eyes}}] if eyes else []
    return {"id": cid or f"C_{login}_{n}", "author": {"__typename": "Bot" if bot else "User", "login": login},
            "body": body, "createdAt": at(n), "reactions": {"nodes": reactions}}


def thread(tid, *comments, resolved=False, path="src/a.py"):
    return {"id": tid, "isResolved": resolved, "path": path,
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": list(comments)}}


class Coordination(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo()
        self.gh = FakeGh()
        self.commits = [commit(self.head, "head")]
        self.headsha = self.head
        self.rollup = [check("test")]
        self.comments, self.threads, self.reactions, self.reviews = [], [], [], []
        self.extra = []
        self.n = 0

    def tearDown(self):
        for argv in self.gh.calls():
            if "-X" in argv:
                self.assertIn("content=eyes", argv)
                self.assertTrue(argv[argv.index("-X") + 2].endswith("/reactions"), argv)

    def rules(self):
        view = {"headRefOid": self.headsha, "url": "https://github.com/o/r/pull/42", "author": {"login": "me"},
                "commits": self.commits, "statusCheckRollup": self.rollup, "reviews": self.reviews,
                "comments": self.comments}
        page = {"data": {"viewer": {"login": "me"}, "repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.threads}}}}}
        return [*self.extra,
                {"match": ["pr view 42 --json headRefOid,commits"], "stdout": view},
                {"match": ["pr view 42 --json url"], "stdout": {"url": "https://github.com/o/r/pull/42"}},
                {"match": ["api graphql", "reviewThreads"], "stdout": [page]},
                {"match": ["issues/42/reactions?per_page"], "stdout": [self.reactions]},
                {"match": ["--method GET", "/commits/"], "stdout": {"files": []}}]

    def cmd(self, *args, stdin=None, cwd=None):
        self.gh.set(self.rules())
        r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "coordination.py"), *map(str, args)],
                           input=(stdin or "").encode(), capture_output=True, cwd=cwd or self.repo, env=self.gh.env())
        out, err = r.stdout.decode(), r.stderr.decode()
        self.assertNotIn(TOKEN, out + err)
        return r.returncode, (json.loads(out) if out.strip() else None), err

    def poll(self, *args, code=0):
        rc, data, err = self.cmd("poll", "--pr", 42, *args)
        self.assertEqual(rc, code, (data, err))
        return data

    def settle(self, *args, code=0):
        self.poll(*args, code=code)
        return self.poll(*args, code=code)

    def states(self, out):
        return {i["id"]: (i["state"], i["claimant"], i["own"]) for i in out["items"]}

    def push(self, msg, login="copilot-swe-agent", real_file=None):
        self.n += 1
        if real_file:
            (self.repo / real_file).write_text(f"v{self.n}\n")
            sha = commit_all(self.repo, msg)
        else:
            sha = f"{self.n:040x}"
        self.commits.append(commit(sha, msg, login, self.n))
        self.headsha = sha
        return sha

    def state_file(self):
        with open(os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42", "coordination.json")) as f:
            return json.load(f)

    def test_settled_needs_a_second_look(self):
        out = self.poll()
        self.assertFalse(out["settled"])
        self.assertTrue(out["settled_reasons"][0].startswith("first_look"))
        out = self.poll()
        self.assertEqual((out["settled"], out["settled_reasons"], out["items"], out["claimed_count"],
                          out["actionable"], out["handback"]), (True, [], [], 0, [], None))

    def test_not_settled_while_checks_run_or_the_head_moves(self):
        self.settle()
        self.rollup = [check("test"), check("e2e", "IN_PROGRESS", None)]
        out = self.poll()
        self.assertEqual((out["settled"], out["settled_reasons"]), (False, ["checks_running: e2e"]))
        self.rollup = [check("test")]
        self.push("Tweak")
        self.assertTrue(self.poll()["settled_reasons"][0].startswith("new_head"))
        self.assertTrue(self.poll()["settled"])

    def test_working_signals(self):
        self.settle()
        self.comments = [{"author": {"login": "alice"}, "body": "I'm working on this", "createdAt": at(5)}]
        self.assertEqual(self.poll()["settled_reasons"], ["working: alice said they're working on the PR"])
        self.comments.append({"author": {"login": "alice"}, "body": "Done, pushed", "createdAt": at(6)})
        self.assertTrue(self.poll()["settled"])
        self.reactions = [{"content": "eyes", "user": {"login": "bob"}, "created_at": at(7)}]
        self.assertIn("bob reacted", self.poll()["settled_reasons"][0])
        self.reactions = [{"content": "eyes", "user": {"login": "bob"}, "created_at": "2026-10-27T09:00:00Z"}]
        self.assertTrue(self.poll()["settled"])
        self.comments = [{"author": {"login": "carol"}, "body": "@copilot please fix the tests", "createdAt": at(8)}]
        self.assertIn("asked @copilot", self.poll()["settled_reasons"][0])
        self.comments.append({"author": {"login": "Copilot"}, "body": "Sure.", "createdAt": at(9)})
        self.assertTrue(self.poll()["settled"])
        self.reviews = [{"author": {"login": "me"}, "state": "PENDING"}]
        self.assertIn("pending review", self.poll()["settled_reasons"][0])

    def test_thread_states(self):
        self.threads = [
            thread("T_open", comment("alice", "Rename this", 1)),
            thread("T_bot", comment("alice", "Typo", 1), comment("Copilot", "I'm working on it", 2, bot=True)),
            thread("T_eyes", comment("alice", "Off by one", 1, eyes="bob")),
            thread("T_resolved", comment("alice", "Nit", 1), resolved=True),
            thread("T_answered", comment("alice", "Why?", 1), comment("me", "Because of the cache.", 2)),
            thread("T_own", comment("alice", "Fix it", 1),
                   comment("me", "Fixed in abc.\n<!-- vstack:babysit:thread:T_own -->", 2)),
            thread("T_pushback", comment("alice", "Wrong", 1), comment("me", "It's fine", 2),
                   comment("alice", "No, still wrong", 3)),
            thread("T_touch", comment("alice", "Handle None", 0), path="src/b.py"),
        ]
        self.push("Handle None in b", real_file="src/b.py")
        out = self.settle()
        self.assertEqual(self.states(out), {
            "thread:T_open": ("open", None, False),
            "thread:T_bot": ("claimed", "Copilot", False),
            "thread:T_eyes": ("claimed", "bob", False),
            "thread:T_resolved": ("addressed", None, False),
            "thread:T_answered": ("addressed", "me", False),
            "thread:T_own": ("addressed", "babysit", True),
            "thread:T_pushback": ("open", None, False),
            "thread:T_touch": ("claimed", "copilot-swe-agent", False)})
        self.assertEqual((out["claimed_count"], out["actionable"]), (3, ["thread:T_open", "thread:T_pushback"]))
        self.assertEqual({i["kind"] for i in out["items"]}, {"thread"})

    def test_check_states(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        out = self.settle()
        self.assertEqual((self.states(out), out["actionable"]), ({"check:lint": ("open", None, False)}, ["check:lint"]))
        self.push("Fix lint warnings")
        self.rollup = [check("lint", "IN_PROGRESS", None)]
        out = self.poll()
        self.assertEqual((out["settled"], self.states(out)["check:lint"][0], out["actionable"]), (False, "open", []))
        self.rollup = [check("lint", conclusion="FAILURE")]
        out = self.poll()
        self.assertEqual(self.states(out)["check:lint"], ("claimed", "copilot-swe-agent", False))
        self.assertEqual((out["settled"], out["actionable"], out["claimed_count"]), (True, [], 1))
        self.push("Fix lint, take two")
        self.rollup = [check("lint")]
        self.poll()
        out = self.poll()
        self.assertEqual((self.states(out)["check:lint"], out["claimed_count"]), (("addressed", None, False), 0))
        item = self.state_file()["items"]["check:lint"]
        self.assertEqual([(t["from"], t["to"], t["by"]) for t in item["transitions"]],
                         [("open", "claimed", "foreign"), ("claimed", "addressed", "foreign")])

    def test_own_marker_commits(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        self.push("Fix lint\n\nvstack:babysit:check:lint", login="me")
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("open", None, False), 1))
        self.push("Fix lint again\n\nsee vstack:babysit:check:lint", login="me")
        self.assertEqual(self.states(self.settle())["check:lint"], ("claimed", "me", False))
        self.push("Fix lint\n\nvstack:autonomous-merge:0123456789abcdef", login="me")
        self.rollup = [check("lint")]
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("addressed", None, True), 1))

    def stale_by_cycles(self, *args):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle(*args)
        claimed_at = self.push("Fix lint")
        self.assertEqual(self.states(self.settle(*args))["check:lint"][0], "claimed")
        self.push("lint: another try")
        out = self.settle(*args)
        self.assertEqual(self.states(out)["check:lint"][0], "claimed")
        self.assertEqual(self.state_file()["items"]["check:lint"]["claimed_since_head"], claimed_at)
        self.push("lint: third try")

    def test_stale_by_ci_cycles_hands_back(self):
        self.stale_by_cycles()
        out = self.poll(code=5)
        self.assertEqual(self.states(out)["check:lint"], ("stale-claim", "copilot-swe-agent", False))
        self.assertEqual(out["handback"]["reason"], "stale_claim")
        self.assertIn("2 CI cycle(s)", out["handback"]["detail"])
        self.assertEqual(out["handback"]["action"],
                         "the other fixer seems stuck on check:lint; fix it yourself or rerun with 'take over'")
        self.assertEqual(self.poll(code=5)["actionable"], [])

    def test_take_over_makes_stale_items_actionable(self):
        self.stale_by_cycles("--take-over")
        self.poll("--take-over")
        out = self.poll("--take-over")
        self.assertEqual((out["handback"], out["actionable"], out["claimed_count"]), (None, ["check:lint"], 1))

    def test_stale_by_quiet_polls(self):
        self.threads = [thread("T1", comment("alice", "Off by one", 1, eyes="bob"))]
        self.rollup = [check("e2e", "IN_PROGRESS", None)]
        for _ in range(5):
            self.assertEqual(self.states(self.poll())["thread:T1"][0], "claimed")
        self.rollup = [check("e2e")]
        for polls in (1, 2):
            self.assertEqual(self.states(self.poll())["thread:T1"][0], "claimed")
            self.assertEqual(self.state_file()["items"]["thread:T1"]["polls_without_progress"], polls)
        out = self.poll(code=5)
        self.assertEqual((self.states(out)["thread:T1"][0], out["handback"]["reason"]), ("stale-claim", "stale_claim"))
        self.threads[0]["comments"]["nodes"].append(comment("bob", "Fixed in 1234567", 5))
        out = self.poll()
        self.assertEqual((self.states(out)["thread:T1"][0], out["handback"]), ("addressed", None))

    def test_stale_working_signal(self):
        self.settle()
        self.comments = [{"author": {"login": "alice"}, "body": "on it", "createdAt": at(5)}]
        for _ in range(3):
            self.assertFalse(self.poll()["settled"])
        out = self.poll(code=5)
        self.assertEqual(out["handback"]["reason"], "stale_claim")
        self.assertIn("the PR", out["handback"]["action"])
        self.assertTrue(self.poll("--take-over")["settled"])

    def test_conflicting_fixer_after_own_fix(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        self.push("Fix lint\n\nvstack:babysit:check:lint", login="me")
        self.rollup = [check("lint")]
        self.assertEqual(self.states(self.settle())["check:lint"], ("addressed", None, True))
        self.push("Refactor parser")
        self.rollup = [check("lint", conclusion="FAILURE")]
        out = self.poll(code=5)
        self.assertEqual(out["handback"]["reason"], "conflicting_fixer")
        self.assertIn("check:lint", out["handback"]["detail"])

    def test_conflicting_fixer_after_two_foreign_regressions(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        for i in range(2):
            self.push(f"Fix lint {i}")
            self.rollup = [check("lint")]
            self.settle()
            self.push(f"Refactor {i}")
            self.rollup = [check("lint", conclusion="FAILURE")]
            if i == 0:
                self.assertEqual(self.states(self.settle())["check:lint"][0], "open")
        self.assertEqual(self.poll(code=5)["handback"]["reason"], "conflicting_fixer")

    def test_claim_thread_and_check(self):
        self.extra = [{"match": ["api graphql", "databaseId"],
                       "stdout": {"data": {"node": {"comments": {"nodes": [{"id": "C1", "databaseId": 77}]}}}}},
                      {"match": ["-X POST"], "stdout": "{}"}]
        rc, out, err = self.cmd("claim", "--pr", 42, "--item", "thread:T1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out["comment"], "<!-- vstack:babysit:thread:T1 -->")
        post = [a for a in self.gh.calls() if "-X" in a]
        self.assertEqual(post, [["api", "--hostname", "github.com", "-X", "POST", "repos/o/r/pulls/comments/77/reactions",
                                 "-f", "content=eyes"]])
        rc, out, err = self.cmd("claim", "--pr", 42, "--item", "check:build (ubuntu)")
        self.assertEqual(rc, 0, err)
        self.assertEqual([a for a in self.gh.calls() if "-X" in a][-1],
                         ["api", "--hostname", "github.com", "-X", "POST", "repos/o/r/issues/42/reactions",
                          "-f", "content=eyes"])
        writes = [a for a in self.gh.calls() if "-X" in a or "--method" in a and "GET" not in a]
        self.assertEqual(len(writes), 2)
        self.threads = [thread("T1", comment("alice", "Off by one", 1, cid="C1", eyes="me"))]
        self.reactions = [{"content": "eyes", "user": {"login": "me"}, "created_at": at(9)}]
        out = self.settle()
        self.assertTrue(out["settled"])
        self.assertEqual((self.states(out)["thread:T1"], out["actionable"]), (("claimed", "babysit", True), ["thread:T1"]))

    def test_the_earlier_claim_wins(self):
        self.extra = [{"match": ["api graphql", "databaseId"],
                       "stdout": {"data": {"node": {"comments": {"nodes": [{"id": "C1", "databaseId": 77}]}}}}},
                      {"match": ["-X POST"], "stdout": "{}"}]
        self.assertEqual(self.cmd("claim", "--pr", 42, "--item", "thread:T1")[0], 0)
        first = comment("alice", "Off by one", 0, cid="C1")
        first["createdAt"] = "2000-01-01T00:00:00Z"
        for when, claimant in (("2999-01-01T00:00:00Z", "babysit"), ("2000-01-02T00:00:00Z", "bob")):
            with self.subTest(when):
                first["reactions"] = {"nodes": [{"content": "EYES", "createdAt": when, "user": {"login": "bob"}}]}
                self.threads = [thread("T1", first, path="src/c.py")]
                self.assertEqual(self.states(self.poll())["thread:T1"][:2], ("claimed", claimant))

    def test_markers(self):
        rc, out, _ = self.cmd("markers", "--item", "check:lint")
        self.assertEqual((rc, out), (0, {"item": "check:lint", "comment": "<!-- vstack:babysit:check:lint -->",
                                         "commit": "vstack:babysit:check:lint"}))

    def test_input_mode_reads_no_gh(self):
        bundle = {"view": {"headRefOid": self.head, "commits": self.commits, "statusCheckRollup": [check("test")],
                           "comments": [], "reviews": []}, "threads": [], "pr_reactions": [], "viewer": "me"}
        for _ in range(2):
            rc, out, err = self.cmd("poll", "--pr", 42, "--input", "-", stdin=json.dumps(bundle))
        self.assertEqual((rc, out["settled"]), (0, True), err)
        self.assertEqual(self.gh.calls(), [])
        self.assertEqual(self.cmd("poll", "--pr", 42, "--input", "-", stdin="[]")[0], 2)

    def test_state_file(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        st = self.state_file()
        self.assertEqual((st["last_head"], st["polls"]), (self.head, 2))
        self.assertTrue({"state", "claimed_since_head", "ci_cycles_completed_since_claim", "polls_without_progress",
                         "transitions"} <= set(st["items"]["check:lint"]))
        d = os.path.join(os.path.realpath(self.repo / ".git"), "vstack", "42")
        self.assertEqual(sorted(os.listdir(d)), ["coordination.json"])
        with open(os.path.join(d, "coordination.json"), "w") as f:
            f.write('{"schema_version": 1}')
        rc, out, err = self.cmd("poll", "--pr", 42)
        self.assertEqual((rc, out), (2, None))
        self.assertIn("fails its checks", err)

    def test_refuses_symlinks(self):
        vstack = os.path.join(os.path.realpath(self.repo / ".git"), "vstack")
        os.symlink(tmpdir(), vstack)
        rc, _, err = self.cmd("poll", "--pr", 42)
        self.assertEqual(rc, 2)
        self.assertIn("symlink", err)
        os.unlink(vstack)
        os.makedirs(os.path.join(vstack, "42"))
        os.symlink(tmpdir() / "x.json", os.path.join(vstack, "42", "coordination.json"))
        self.assertEqual(self.cmd("poll", "--pr", 42)[0], 2)

    def test_usage(self):
        for args in ([], ["-h"], ["poll", "-h"], ["poll"], ["poll", "--pr", "0"], ["claim", "--pr", "4", "--item", "x"],
                     ["markers", "--item", "thread:a b"], ["markers", "--item", "check:a-->b"],
                     ["poll", "--pr", "4", "-R", "o"], ["poll", "--pr", "4", "--bogus"]):
            with self.subTest(args):
                self.assertEqual(self.cmd(*args)[0], 2)
        self.assertEqual(self.cmd("poll", "--pr", 42, cwd=tmpdir())[0], 2)
        self.assertEqual(self.gh.calls(), [])

    def test_gh_failure_is_internal_with_an_unknown_handback(self):
        self.extra = [{"match": ["pr view"], "code": 1, "stderr": f"HTTP 401: Bad credentials (token {TOKEN})"}]
        rc, out, err = self.cmd("poll", "--pr", 42)
        self.assertEqual((rc, out["handback"]["reason"], out["settled"]), (4, "unknown", False))
        self.assertIn("gh exited 1", out["handback"]["detail"])


if __name__ == "__main__":
    unittest.main()
