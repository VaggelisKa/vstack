"""Tests for state.py in temp git repos with real review and babysit result
files: init and resume, review rounds, finding fingerprints, fix rounds, the
caps (3 review rounds, 6 fix rounds, 2 hours, from the constants only), the
oscillation guard (a fixed finding coming back, a commit undoing a fix), no
progress between rounds, security blockers, escalations, stops and --continue,
fix rounds and the 12-commit backstop counted by the viewer's GitHub identity
(a fake gh answers `gh api user`, `gh api user/emails` and each commit's author
and committer: by login, by email, and unlinked commits counted), the
file's checks (caps, started_at, fix_rounds, viewer_commits), and its atomic,
symlink-refusing writes.

Run from the repo root: python3 -m unittest discover -s skills/autonomous-merge/scripts/tests
"""
import datetime
import importlib.util
import json
import os
import pathlib
import unittest

from helpers import (BABYSIT_WRITER, GIT_ENV, REVIEW_WRITER, SCRIPTS, SHIP, Stubs, babysit, commit_all, finding,
                     gh_rule, pr_repo, review, run, tmpdir, write_result)

spec = importlib.util.spec_from_file_location("am_state", SCRIPTS / "state.py")
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)

MAJOR = finding()
MINOR = finding(severity="minor", rule="design", summary="Name could be clearer", fid="C2")


def incomplete(*findings):
    return {**review(*findings), "status": "unreviewed", "verdict": "Incomplete", "score": None, "partial": True,
            "partial_k3": True}


def at(i):
    return f"2026-01-01T00:00:{i:02d}Z"


def majors(n):
    return [finding(summary=f"Problem {chr(97 + i)}", fid=f"M{i}") for i in range(n)]


class State(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(self)
        self.gitdir = os.path.realpath(self.repo / ".git")
        self.vstack = os.path.join(self.gitdir, "vstack")
        self.path = os.path.join(self.vstack, "42", "autonomous-merge.json")
        self.n = 0
        self.stubs = Stubs(self, tools=("gh",))
        self.logins = {}
        self.emails = gh_rule(["user/emails"], [[{"email": "me@example.com"}]])

    def gh_rules(self):
        """gh api user is "me" (id 7); a commit is authored by "me" unless self.logins says
        (author, committer[, email]), a login of None meaning no linked account."""
        def who(author, committer, email=None):
            return {"author": author and {"login": author}, "committer": committer and {"login": committer},
                    "commit": {"author": {"email": email}, "committer": {"email": email}}}
        return [self.emails, gh_rule(["api --hostname github.com user"], {"login": "me", "id": 7}),
                *[gh_rule([f"repos/o/r/commits/{sha}"], who(*ac)) for sha, ac in self.logins.items()],
                gh_rule(["repos/o/r/commits/"], who("me", "web-flow"))]

    def st(self, *args, code=0, cwd=None):
        self.stubs.set(self.gh_rules())
        rc, data, out, err = run("state.py", *args, "--pr", 42, cwd=cwd or self.repo, env=self.stubs.env())
        self.assertEqual(rc, code, (out, err))
        return data if data is not None else err

    def handback(self, reason, *args):
        data = self.st(*args, code=5)
        self.assertEqual((data["outcome"], data["reason"]), ("handback", reason), data)
        return data

    def load(self):
        with open(self.path) as f:
            return json.load(f)

    def edit(self, fn):
        s = self.load()
        fn(s)
        with open(self.path, "w") as f:
            json.dump(s, f)

    def commit(self, text=None, marker=None):
        self.n += 1
        (self.repo / "src/a.py").write_text(text or f"x = {self.n + 10}\n")
        return commit_all(self.repo, f"fix {self.n}" + (f"\n\n{marker}" if marker else ""))

    def reviewed(self, head, *findings):
        write_result(REVIEW_WRITER, self.repo, 42, head, self.base, review(*findings))
        return self.st("record-review", "--head-sha", head, "--base-sha", self.base)

    def rereviewed(self, result, i, code=0):
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, {**result, "written_at": at(i)})
        return self.st("record-review", "--head-sha", self.head, "--base-sha", self.base, code=code)

    def test_same_head_review_supersedes_an_unclean_round_with_nothing_to_fix(self):
        self.st("init")
        out = self.rereviewed(incomplete(MINOR), 1)
        self.assertEqual((out["round"], out["clean"], out["to_fix"], out["next"]), (1, False, [], "review"))
        again = self.st("record-review", "--head-sha", self.head, "--base-sha", self.base)
        self.assertEqual((again["repeat"], again["round"], again["next"]), (True, 1, "review"))
        self.rereviewed(incomplete(MINOR), 0)
        self.assertEqual(self.load()["counters"]["review_rounds"], 1)
        out = self.rereviewed(review(MINOR), 2)
        self.assertEqual((out["repeat"], out["round"], out["clean"], out["next"], out["review_rounds_left"]),
                         (False, 2, True, "babysit", 1))
        s = self.load()
        self.assertEqual((s["counters"]["review_rounds"], s["rounds"][1]["supersedes"],
                          s["rounds"][1]["review_written_at"]), (2, 1, at(2)))
        self.assertTrue(self.st("record-review", "--head-sha", self.head, "--base-sha", self.base)["repeat"])
        self.assertEqual(self.rereviewed(review(MINOR), 3)["round"], 2)

    def test_same_head_repeats_while_something_is_left_to_fix(self):
        self.st("init")
        fp = self.rereviewed(review(MAJOR), 1)["to_fix"][0]["fingerprint"]
        out = self.rereviewed(review(), 2)
        self.assertEqual((out["repeat"], out["round"], out["next"]), (True, 1, "fix"))
        self.st("finding", "--fingerprint", fp, "--status", "rejected", "--reason", "src/a.py:3 is right")
        out = self.rereviewed(review(), 2)
        self.assertEqual((out["repeat"], out["round"], out["clean"]), (False, 2, True))

    def test_same_head_rereview_of_a_rejected_major_is_no_progress(self):
        self.st("init")
        fp = self.rereviewed(review(MAJOR), 1)["to_fix"][0]["fingerprint"]
        self.st("finding", "--fingerprint", fp, "--status", "rejected", "--reason", "src/a.py:3 is right")
        out = self.rereviewed(review(MAJOR), 2, code=5)
        self.assertEqual(out["reason"], "no_progress")
        self.assertIn("round 2", out["detail"])

    def test_same_head_rereview_still_catches_oscillation(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        self.head = self.commit()
        self.st("fix-round", "--head-sha", self.head, "--fixed", fp)
        self.assertEqual(self.rereviewed(incomplete(), 1)["next"], "review")
        self.assertEqual(self.rereviewed(review(MAJOR), 2, code=5)["reason"], "oscillation")

    def test_same_head_rereviews_count_toward_the_cap(self):
        self.st("init")
        for i in (1, 2):
            self.st("check", "--before", "review")
            self.assertEqual(self.rereviewed(incomplete(), i)["round"], i)
        self.st("check", "--before", "review")
        out = self.rereviewed(incomplete(), 3, code=5)
        self.assertEqual(out["reason"], "cap_reached")
        self.assertIn("nothing to fix", out["detail"])
        self.assertEqual(self.load()["counters"]["review_rounds"], 3)
        self.st("init", "--continue")
        self.handback("cap_reached", "check", "--before", "review")
        self.st("init", "--continue")
        self.assertEqual(self.rereviewed(review(), 4, code=5)["reason"], "cap_reached")
        self.assertEqual(self.load()["counters"]["review_rounds"], 3)

    def test_same_head_rereview_of_an_older_round(self):
        self.st("init")
        self.rereviewed(incomplete(), 1)
        self.edit(lambda s: (s["rounds"][0].pop("review_written_at"), s["rounds"][0].update(at=at(5))))
        self.assertTrue(self.rereviewed(review(), 5)["repeat"])
        self.assertEqual(self.rereviewed(review(), 6)["round"], 2)

    def test_init_and_resume(self):
        out = self.st("init")
        self.assertEqual((out["outcome"], out["resumed"], out["merge"], out["review_rounds"]), ("ok", False, False, 0))
        self.assertIn(out["minutes_left"], (119, 120))
        s = self.load()
        self.assertEqual((s["skill"], s["pr"], s["caps"]), ("autonomous-merge", 42,
                                                            {"review_rounds": 3, "fix_rounds": 6, "minutes": 120}))
        self.reviewed(self.head, MAJOR)
        out = self.st("init", "--merge")
        self.assertEqual((out["resumed"], out["merge"], out["review_rounds"]), (True, True, 1))
        self.assertEqual(len(out["open_findings"]), 1)
        self.assertEqual(self.load()["started_at"], s["started_at"])

    def test_review_round(self):
        self.st("init")
        out = self.reviewed(self.head, MAJOR, MINOR)
        self.assertEqual((out["round"], out["clean"], out["counts"]), (1, False, {"blocker": 0, "major": 1,
                                                                                   "minor": 1, "nit": 0}))
        self.assertEqual([f["summary"] for f in out["to_fix"]], [MAJOR["summary"]])
        self.assertEqual([(f["status"], f["reason"]) for f in out["deferred"]], [("deferred", "minor: logged, not fixed")])
        rnd = self.load()["rounds"][0]
        self.assertEqual((rnd["head"], rnd["base"], rnd["status"], rnd["merge_ok"]), (self.head, self.base, "fix", False))
        again = self.st("record-review", "--head-sha", self.head, "--base-sha", self.base)
        self.assertTrue(again["repeat"])
        self.assertEqual(self.load()["counters"]["review_rounds"], 1)

    def test_clean_review(self):
        self.st("init")
        out = self.reviewed(self.head, MINOR)
        self.assertEqual((out["clean"], out["to_fix"]), (True, []))

    def test_record_review_needs_a_matching_review(self):
        self.st("init")
        self.handback("needs_review", "record-review", "--head-sha", self.head, "--base-sha", self.base)
        write_result(REVIEW_WRITER, self.repo, 42, self.base, self.base, SHIP)
        self.handback("needs_review", "record-review", "--head-sha", self.head, "--base-sha", self.base)
        self.assertEqual(self.load()["counters"]["review_rounds"], 0)

    def test_fingerprint(self):
        fp = state.fingerprint("src/a.py", "bug-hunt", "Off by one in loop at line 3")
        self.assertRegex(fp, r"^[0-9a-f]{16}$")
        for summary in ["off by one in loop at line 3", "Off  by one, in loop at line 17.", " OFF BY ONE in loop at line 3 "]:
            self.assertEqual(state.fingerprint("src/a.py", "Bug-Hunt ", summary), fp, summary)
        self.assertNotEqual(state.fingerprint("src/b.py", "bug-hunt", "Off by one in loop at line 3"), fp)
        self.assertNotEqual(state.fingerprint("src/a.py", "security", "Off by one in loop at line 3"), fp)
        self.assertNotEqual(state.fingerprint("src/a.py", "bug-hunt", "Off by two in loop at line 3"), fp)

    def test_fix_round_marks_findings(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        fix = self.commit()
        out = self.st("fix-round", "--head-sha", fix, "--fixed", fp)
        self.assertEqual((out["fix_rounds"], out["last_head"], out["open_findings"]), (1, fix, []))
        f = self.load()["findings"][0]
        self.assertEqual((f["status"], f["commit"]), ("fixed", fix))
        self.assertEqual(self.reviewed(fix)["clean"], True)
        self.assertIn("no new commit", self.st("fix-round", "--head-sha", fix, code=2))
        self.assertIn("no finding", self.st("fix-round", "--head-sha", fix, "--fixed", "0" * 16, code=2))

    def test_rejected_finding_returning_is_not_oscillation(self):
        self.st("init")
        other = finding(summary="Unchecked return value", fid="C3")
        fp, other_fp = [f["fingerprint"] for f in self.reviewed(self.head, MAJOR, other)["to_fix"]]
        self.st("finding", "--fingerprint", fp, "--status", "rejected", "--reason", "spec says so: docs/x.md:3")
        self.st("fix-round", "--head-sha", self.commit(), "--fixed", other_fp)
        out = self.reviewed(self.commit(), MAJOR)
        self.assertEqual(out["to_fix"], [])
        self.assertEqual(self.load()["findings"][0]["status"], "rejected")

    def test_no_progress(self):
        self.st("init")
        fps = [f["fingerprint"] for f in self.reviewed(self.head, *majors(2))["to_fix"]]
        self.st("fix-round", "--head-sha", self.commit(), "--fixed", fps[0])
        head = self.commit()
        write_result(REVIEW_WRITER, self.repo, 42, head, self.base,
                     review(majors(2)[1], finding(summary="New problem", fid="N1")))
        data = self.handback("no_progress", "record-review", "--head-sha", head, "--base-sha", self.base)
        self.assertIn("round 1 had 2", data["detail"])
        self.assertEqual(self.load()["stop"]["reason"], "no_progress")
        self.handback("no_progress", "check", "--before", "fix")

    def test_progress_and_a_clean_round_after_none_are_fine(self):
        self.st("init")
        fps = [f["fingerprint"] for f in self.reviewed(self.head, *majors(2))["to_fix"]]
        self.st("fix-round", "--head-sha", self.commit(), "--fixed", fps[0])
        self.assertEqual(self.reviewed(self.commit(), majors(2)[1])["round"], 2)

    def test_security_blocker_hands_back_at_once(self):
        for rule in ("security", "Data-Integrity"):
            with self.subTest(rule):
                self.st("reset")
                self.st("init", "--fresh")
                blocker = finding(severity="blocker", rule=rule, summary="SQL built from user input")
                write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, review(blocker))
                data = self.handback("security_blocker", "record-review", "--head-sha", self.head,
                                     "--base-sha", self.base)
                self.assertIn("SQL built from user input", data["detail"])
                self.assertEqual(len(self.load()["rounds"]), 1)
        self.st("reset")
        self.st("init", "--fresh")
        self.assertFalse(self.reviewed(self.head, finding(severity="blocker", rule="bug-hunt"))["clean"])

    def test_init_refuses_earlier_results_without_state(self):
        review_json = os.path.join(self.vstack, "42", "review.json")
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, SHIP)
        data = self.handback("escalated", "init", "--merge")
        self.assertIn("review.json already exists", data["detail"])
        self.assertIn("init --pr 42 --fresh", data["action"])
        self.assertFalse(os.path.exists(self.path))
        write_result(BABYSIT_WRITER, self.repo, 42, self.head, self.base, babysit())
        self.assertIn("review.json and babysit.json", self.handback("escalated", "init", "--continue")["detail"])
        os.unlink(review_json)
        self.assertIn("babysit.json already exists", self.handback("escalated", "init")["detail"])
        self.assertEqual(self.st("init", "--fresh")["resumed"], False)
        self.assertEqual(self.st("init")["resumed"], True)
        self.st("reset")
        self.handback("escalated", "init")

    def test_escalate(self):
        for reason in ("security_blocker", "ci_unreproducible", "conflict_judgement", "design_change",
                       "scope_change", "escalated"):
            with self.subTest(reason):
                self.st("reset")
                self.st("init")
                data = self.handback(reason, "escalate", "--reason", reason, "--detail", "alice asks for a queue")
                self.assertEqual(data["detail"], "alice asks for a queue")
                self.assertEqual((self.load()["stop"]["outcome"], self.load()["stop"]["reason"]), ("handback", reason))
                self.handback(reason, "check", "--before", "review")
        self.st("init", "--continue")
        self.st("escalate", "--reason", "cap_reached", "--detail", "x", code=2)
        self.st("escalate", "--reason", "escalated", "--detail", " ", code=2)

    def test_oscillation_finding_returns_after_fix(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        fix = self.commit()
        self.st("fix-round", "--head-sha", fix, "--fixed", fp)
        write_result(REVIEW_WRITER, self.repo, 42, fix, self.base, review(finding(summary="Off by ONE in loop at line 9")))
        data = self.handback("oscillation", "record-review", "--head-sha", fix, "--base-sha", self.base)
        self.assertIn(fp, data["detail"])
        s = self.load()
        self.assertEqual((s["stop"]["outcome"], s["stop"]["reason"], len(s["rounds"])), ("handback", "oscillation", 2))
        self.handback("oscillation", "check", "--before", "fix")

    def test_oscillation_revert_of_a_fix(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        fix = self.commit("x = 3\n")
        self.st("fix-round", "--head-sha", fix, "--fixed", fp)
        undo = self.commit("x = 2\n")
        data = self.handback("oscillation", "fix-round", "--head-sha", undo)
        self.assertIn("src/a.py", data["detail"])
        self.assertIn(fix[:12], data["detail"])

    def test_revert_seen_in_a_review_round_and_babysit(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        fix = self.commit("x = 3\n")
        self.st("fix-round", "--head-sha", fix, "--fixed", fp)
        other = self.repo / "src/b.py"
        other.write_text("y = 1\n")
        commit_all(self.repo, "unrelated")
        undo = self.commit("x = 2\n")
        write_result(BABYSIT_WRITER, self.repo, 42, undo, self.base, babysit())
        self.handback("oscillation", "record-babysit", "--head-sha", undo, "--base-sha", self.base)

    def test_new_edit_to_a_fixed_file_is_not_a_revert(self):
        self.st("init")
        fp = self.reviewed(self.head, MAJOR)["to_fix"][0]["fingerprint"]
        fix = self.commit("x = 3\n")
        self.st("fix-round", "--head-sha", fix, "--fixed", fp)
        self.assertEqual(self.st("fix-round", "--head-sha", self.commit("x = 4\n"))["fix_rounds"], 2)

    def test_review_cap(self):
        self.st("init")
        head = self.head
        for i in range(3):
            self.st("check", "--before", "review")
            fp = self.reviewed(head, *majors(3)[i:])["to_fix"][0]["fingerprint"]
            if i < 2:
                self.st("check", "--before", "fix")
                head = self.commit()
                self.st("fix-round", "--head-sha", head, "--fixed", fp)
        self.handback("cap_reached", "check", "--before", "review")
        self.assertEqual(self.load()["stop"]["reason"], "cap_reached")
        self.st("init", "--continue")
        self.assertIn("still isn't clean", self.handback("cap_reached", "check", "--before", "fix")["detail"])

    def test_review_cap_in_record_review(self):
        self.st("init")
        self.edit(lambda s: (s["rounds"].extend({"round": i, "head": self.base, "base": self.base, "merge_ok": False}
                                                for i in (1, 2, 3)), s["counters"].update(review_rounds=3)))
        self.handback("cap_reached", "check", "--before", "fix")
        self.st("init", "--continue")
        write_result(REVIEW_WRITER, self.repo, 42, self.head, self.base, review(MAJOR))
        self.handback("cap_reached", "record-review", "--head-sha", self.head, "--base-sha", self.base)

    def test_fix_cap(self):
        self.st("init")
        self.reviewed(self.head)
        self.edit(lambda s: (s["counters"].update(fix_rounds=5),
                             s["fixes"].append({"round": 5, "source": "babysit", "head": self.head,
                                                "commits": [self.head] * 5, "at": s["started_at"]})))
        self.st("check", "--before", "fix")
        self.assertEqual(self.st("fix-round", "--head-sha", self.commit())["fix_rounds_left"], 0)
        self.handback("cap_reached", "check", "--before", "fix")
        self.st("init", "--continue")
        self.handback("cap_reached", "fix-round", "--head-sha", self.commit())
        self.assertEqual(self.load()["counters"]["fix_rounds"], 7)

    def test_babysit_commits_count_toward_the_fix_cap(self):
        self.st("init")
        self.reviewed(self.head)
        heads = [self.commit(marker=f"vstack:babysit:check:test {i}") for i in range(6)]
        write_result(BABYSIT_WRITER, self.repo, 42, heads[-1], self.base, babysit())
        out = self.st("record-babysit", "--head-sha", heads[-1], "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["fix_rounds"], out["babysit_status"]), (6, 6, "green"))
        head = self.commit(marker="vstack:babysit:thread:PRRT_1")
        write_result(BABYSIT_WRITER, self.repo, 42, head, self.base, babysit())
        self.handback("cap_reached", "record-babysit", "--head-sha", head, "--base-sha", self.base)

    def test_only_babysit_marked_commits_are_fix_rounds(self):
        self.st("init")
        self.reviewed(self.head)
        self.commit()
        self.commit(marker="vstack:autonomous-merge:0123456789abcdef")
        self.commit(marker="see vstack:babysit:check:test")
        own = self.commit(marker="vstack:babysit:check:test\nvstack:babysit:thread:PRRT_1")
        forged = self.commit(marker="vstack:babysit:check:test")
        self.logins[forged] = ("mallory", "web-flow")
        committed = self.commit(marker="vstack:babysit:check:test")
        self.logins[committed] = ("mallory", "me")
        head = self.commit()
        write_result(BABYSIT_WRITER, self.repo, 42, head, self.base, babysit())
        out = self.st("record-babysit", "--head-sha", head, "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["babysit_commits"], out["your_commits"], out["fix_rounds"]),
                         (7, 2, 6, 2))
        self.assertEqual(self.load()["fixes"][-1]["commits"], [own, committed])
        calls = [c["argv"] for c in self.stubs.calls("gh")]
        self.assertEqual(calls[0], ["api", "--hostname", "github.com", "user"])
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(c[:3] == ["api", "--hostname", "github.com"] and c[3].startswith("repos/o/r/commits/")
                            for c in calls[1:]), calls)
        self.assertNotIn(forged, self.load()["viewer_commits"])

    def test_viewer_commit_backstop(self):
        self.st("init")
        self.reviewed(self.head)
        for i in range(3):
            self.logins[self.commit()] = ("mallory", "web-flow")
        heads = [self.commit() for _ in range(12)]
        write_result(BABYSIT_WRITER, self.repo, 42, heads[-1], self.base, babysit())
        out = self.st("record-babysit", "--head-sha", heads[-1], "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["your_commits"], out["viewer_commits"], out["fix_rounds"]),
                         (15, 12, 12, 0))
        self.assertIn("backstop is 12", self.handback("cap_reached", "check", "--before", "fix")["detail"])
        self.st("init", "--continue")
        self.handback("cap_reached", "fix-round", "--head-sha", self.commit())
        self.assertEqual(len(self.load()["viewer_commits"]), 13)
        self.st("init", "--continue")
        head = self.commit()
        write_result(REVIEW_WRITER, self.repo, 42, head, self.base, SHIP)
        data = self.handback("cap_reached", "record-review", "--head-sha", head, "--base-sha", self.base)
        self.assertIn("pushed 14 commits", data["detail"])

    def test_unlinked_commits_count_as_the_viewers(self):
        self.st("init")
        self.reviewed(self.head)
        self.logins[self.commit()] = ("mallory", "web-flow", "mallory@example.com")
        marked = []
        for ac in ((None, "web-flow", "x@unlinked.example"), (None, None), ("mallory", None, "x@unlinked.example")):
            marked.append(self.commit(marker="vstack:babysit:check:test"))
            self.logins[marked[-1]] = ac
        plain = self.commit()
        self.logins[plain] = (None, "web-flow")
        head = self.commit()
        write_result(BABYSIT_WRITER, self.repo, 42, head, self.base, babysit())
        out = self.st("record-babysit", "--head-sha", head, "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["your_commits"], out["unidentified_commits"], out["babysit_commits"],
                          out["fix_rounds"]), (6, 5, 4, 3, 3))
        self.assertEqual(self.load()["fixes"][-1]["commits"], marked)
        self.assertEqual(self.load()["viewer_commits"], [*marked, plain, head])

    def test_viewer_matched_by_email(self):
        self.st("init")
        self.reviewed(self.head)
        ids = {("old-me", "old-me", "Me@Example.com"): True, (None, "web-flow", "7+me@users.noreply.github.com"): True,
               ("someone", "someone", "me@users.noreply.github.com"): True,
               ("mallory", "mallory", "mallory@example.com"): False}
        for ac in ids:
            self.logins[self.commit(marker="vstack:babysit:check:test")] = ac
        head = list(self.logins)[-1]
        write_result(BABYSIT_WRITER, self.repo, 42, head, self.base, babysit())
        out = self.st("record-babysit", "--head-sha", head, "--base-sha", self.base)
        self.assertEqual((out["your_commits"], out["unidentified_commits"], out["fix_rounds"]), (3, 0, 3))
        self.assertEqual(self.load()["viewer_commits"], [sha for sha, ac in self.logins.items() if ids[ac]])
        emails = [c["argv"] for c in self.stubs.calls("gh") if "user/emails" in c["argv"]]
        self.assertEqual(emails, [["api", "--hostname", "github.com", "--paginate", "--slurp", "user/emails"]])
        self.emails = gh_rule(["user/emails"], code=1, stderr="gh: Not Found (HTTP 404)")
        listed = self.commit()
        self.logins[listed] = ("old-me", "old-me", "me@example.com")
        noreply = self.commit()
        self.logins[noreply] = ("old-me", "old-me", "7+ME@users.noreply.github.com")
        write_result(BABYSIT_WRITER, self.repo, 42, noreply, self.base, babysit())
        out = self.st("record-babysit", "--head-sha", noreply, "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["your_commits"]), (2, 1))
        self.assertNotIn(listed, self.load()["viewer_commits"])
        self.emails = gh_rule(["user/emails"], code=1, stderr="HTTP 502: Bad Gateway")
        head = self.commit()
        self.logins[head] = ("old-me", "old-me", "me@example.com")
        write_result(BABYSIT_WRITER, self.repo, 42, head, self.base, babysit())
        self.handback("unknown", "record-babysit", "--head-sha", head, "--base-sha", self.base)

    def test_record_babysit(self):
        self.st("init")
        self.reviewed(self.head)
        t = {"id": "PRRT_1", "author": "alice", "is_bot": False, "state": "open", "decision": "rejected",
             "reply_url": "https://github.com/o/r/pull/42#discussion_r1"}
        write_result(BABYSIT_WRITER, self.repo, 42, self.head, self.base,
                     babysit(status="handed_back", threads=[t], checks=[("test", "failing")]))
        out = self.st("record-babysit", "--head-sha", self.head, "--base-sha", self.base)
        self.assertEqual((out["new_commits"], out["fix_rounds"]), (0, 0))
        s = self.load()
        self.assertEqual(s["threads"], [t])
        self.assertEqual({k: s["ci"][0][k] for k in ("head", "state", "failing_checks", "babysit_status")},
                         {"head": self.head, "state": "failing", "failing_checks": ["test"],
                          "babysit_status": "handed_back"})
        self.assertIn("babysit.json", self.st("record-babysit", "--head-sha", self.base, "--base-sha", self.base, code=2))

    def test_time_cap(self):
        self.st("init")
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2, minutes=1))
        self.edit(lambda s: s.update(started_at=old.strftime("%Y-%m-%dT%H:%M:%SZ")))
        self.assertIn("120 minutes", self.handback("cap_reached", "check", "--before", "review")["detail"])
        self.handback("cap_reached", "init")
        out = self.st("init", "--continue")
        self.assertGreaterEqual(out["minutes_left"], 119)
        self.assertIsNone(self.load()["stop"])

    def test_stops(self):
        self.st("init")
        self.assertIn("--reason", self.st("stop", "--outcome", "handback", code=2))
        self.st("stop", "--outcome", "handback", "--reason", "unresolved_threads", "--detail", "alice's thread")
        data = self.handback("unresolved_threads", "check", "--before", "review")
        self.assertIn("alice's thread", data["detail"])
        self.assertIn("--continue", data["action"])
        self.st("init", "--continue")
        self.st("stop", "--outcome", "ready")
        self.assertEqual(self.st("init", "--merge")["merge"], True)
        self.st("stop", "--outcome", "merged")
        self.handback("not_open", "init", "--continue")

    def test_show_and_reset(self):
        self.assertIn("no state", self.st("show", code=2))
        self.st("init")
        self.assertEqual(self.st("show")["pr"], 42)
        self.assertEqual(self.st("reset")["removed"], True)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.st("reset")["removed"], False)

    def test_refuses_a_tampered_file(self):
        self.st("init")
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1))
        for name, fn in {"counters": lambda s: s["counters"].update(review_rounds=2),
                         "caps raised": lambda s: s["caps"].update(review_rounds=30),
                         "caps dropped": lambda s: s["caps"].pop("minutes"),
                         "future start": lambda s: s.update(started_at=future.strftime("%Y-%m-%dT%H:%M:%SZ")),
                         "fix rounds lowered": lambda s: s["counters"].update(fix_rounds=0) or s["fixes"].append(
                             {"round": 1, "source": "review", "head": self.head, "commits": [self.head]}),
                         "fix rounds without fixes": lambda s: s["counters"].update(fix_rounds=2),
                         "bad fix": lambda s: s["fixes"].append({"source": "manual", "commits": []}),
                         "skill": lambda s: s.update(skill="babysit"),
                         "schema": lambda s: s.update(schema_version=2),
                         "missing": lambda s: s.pop("findings"),
                         "head": lambda s: s.update(last_head="HEAD"),
                         "viewer commits": lambda s: s.update(viewer_commits=["HEAD"])}.items():
            with self.subTest(name):
                self.st("reset")
                self.st("init")
                self.edit(fn)
                self.assertIn("fails its checks", self.st("check", "--before", "review", code=2))
        with open(self.path, "w") as f:
            f.write("{nope")
        self.assertIn("not valid JSON", self.st("show", code=2))

    def test_atomic_write(self):
        self.st("init")
        ino = os.stat(self.path).st_ino
        with open(self.path) as old:
            self.reviewed(self.head)
            self.assertEqual(json.load(old)["rounds"], [])
        self.assertNotEqual(os.stat(self.path).st_ino, ino)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["autonomous-merge.json", "review.json"])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_refuses_symlinks(self):
        elsewhere = tmpdir(self)
        os.symlink(elsewhere, self.vstack)
        self.assertIn("refusing", self.st("init", code=2))
        self.assertEqual(os.listdir(elsewhere), [])
        os.unlink(self.vstack)
        os.mkdir(self.vstack)
        os.symlink(elsewhere, os.path.join(self.vstack, "42"))
        self.assertIn("refusing", self.st("init", code=2))
        os.unlink(os.path.join(self.vstack, "42"))
        self.st("init")
        target = elsewhere / "x.json"
        target.write_text(pathlib.Path(self.path).read_text())
        os.unlink(self.path)
        os.symlink(target, self.path)
        self.assertIn("refusing", self.st("show", code=2))
        self.assertIn("refusing", self.st("init", code=2))

    def test_outside_a_repo_and_bad_args(self):
        cwd = tmpdir(self) / "plain"
        cwd.mkdir()
        rc, _, _, err = run("state.py", "init", "--pr", 42, cwd=cwd,
                            env={**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(cwd.parent)})
        self.assertEqual(rc, 2, err)
        self.assertIn("not in a git repository", err)
        for args in (["-h"], ["--help"], ["init", "-h"], ["init", "--pr", "42", "--help"],
                     ["init", "--pr", "0"], ["init", "--pr", "abc"], ["finding", "--pr", "42", "--fingerprint", "XYZ",
                                                                       "--status", "rejected", "--reason", "r"],
                     ["fix-round", "--pr", "42", "--head-sha", "HEAD"], ["stop", "--pr", "42", "--outcome", "done"]):
            with self.subTest(args):
                self.st("init")
                rc, _, out, err = run("state.py", *args, cwd=self.repo, env=GIT_ENV)
                self.assertEqual(rc, 2, (out, err))


if __name__ == "__main__":
    unittest.main()
