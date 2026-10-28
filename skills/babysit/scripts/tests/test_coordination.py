"""Tests for coordination.py against a fake gh on PATH and a real temp repo:
settled and not settled, each item state for checks and threads, own vs
foreign by marker and by who wrote it (forged markers, a marked commit with no
linked author: own only when recorded with `pushed` or committed or amended in
this clone by its reflog, not when only fetched under the same email; a
committer read over REST, also for unlinked authors; two clones' unlinked
fixers conflicting), bots' "working" comments ignored
but coding agents' claiming, newer 👀 after the author's or viewer's answer,
👀 on every thread comment, @mentions in thread replies, a thread with a second page of comments unreadable, who may
answer a thread, 👀 read over REST across pages,
stale claims by CI cycles and by quiet polls, take-over, conflicting_fixer, a
reopened thread escalating, a head moved by update-branch, the claim and
remove-claim subcommands' exact gh calls, the pushed subcommand and what it refuses, --input, the state file, usage
errors and no token in output.

Run from the repo root: python3 -m unittest discover -s skills/babysit/scripts/tests
"""
import itertools
import json
import os
import subprocess
import sys
import unittest

from helpers import SCRIPTS, FakeGh, commit_all, git, pr_repo, sha256_supported, tmpdir

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123"
DB_IDS = itertools.count(100)


def at(n):
    return f"2026-10-27T{10 + n:02d}:00:00Z"


def commit(oid, msg, login="me", n=0):
    headline, _, body = msg.partition("\n")
    return {"oid": oid, "messageHeadline": headline, "messageBody": body.strip("\n"), "committedDate": at(n),
            "authors": [{"login": login}]}


def check(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def reaction(login, when, rid=1):
    return {"id": rid, "content": "eyes", "created_at": when, "user": {"login": login}}


def comment(login, body, n, cid=None, eyes=None, bot=False, db=None):
    """A thread comment; "eyes" (pages of REST reactions) is served by the fake gh, not in the GraphQL node."""
    return {"id": cid or f"C_{login}_{n}", "databaseId": db or next(DB_IDS),
            "author": {"__typename": "Bot" if bot else "User", "login": login}, "body": body, "createdAt": at(n),
            "eyes": [[reaction(eyes, at(n + 1))] if eyes else []]}


def thread(tid, *comments, resolved=False, path="src/a.py"):
    return {"id": tid, "isResolved": resolved, "path": path,
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": list(comments)}}


class Coordination(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo(self)
        self.gh = FakeGh(self)
        self.commits = [commit(self.head, "head")]
        self.headsha = self.head
        self.rollup = [check("test")]
        self.comments, self.threads, self.reactions, self.reviews = [], [], [], []
        self.reaction_pages, self.extra = None, []
        self.author = "me"
        self.n = 0

    def tearDown(self):
        for argv in self.gh.calls():
            if "-X" in argv:
                method, endpoint = argv[argv.index("-X") + 1:argv.index("-X") + 3]
                if method == "POST":
                    self.assertIn("content=eyes", argv)
                    self.assertTrue(endpoint.endswith("/reactions"), argv)
                else:
                    self.assertEqual(method, "DELETE", argv)
                    self.assertRegex(endpoint, r"/reactions/[0-9]+$")

    def rules(self):
        view = {"headRefOid": self.headsha, "url": "https://github.com/o/r/pull/42", "author": {"login": self.author},
                "commits": self.commits, "statusCheckRollup": self.rollup, "reviews": self.reviews,
                "comments": self.comments}
        threads, eyes = [], []
        for t in self.threads:
            nodes = []
            for c in t["comments"]["nodes"]:
                c = dict(c)
                eyes.append({"match": [f"pulls/comments/{c['databaseId']}/reactions?content=eyes&per_page=100"],
                             "stdout": c.pop("eyes", [[]])})
                nodes.append(c)
            threads.append({**t, "comments": {**t["comments"], "nodes": nodes}})
        page = {"data": {"viewer": {"login": "me"}, "repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": threads}}}}}
        return [*self.extra, *eyes,
                {"match": ["pr view 42 --json headRefOid,commits"], "stdout": view},
                {"match": ["pr view 42 --json url"], "stdout": {"url": "https://github.com/o/r/pull/42"}},
                {"match": ["api graphql", "reviewThreads"], "stdout": [page]},
                {"match": ["issues/42/reactions?content=eyes&per_page=100"],
                 "stdout": self.reaction_pages or [self.reactions]},
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
        self.reviews = [{"author": {"login": "me"}, "state": "PENDING"}, {"author": {"login": "dave"}, "state": "COMMENTED"}]
        self.assertTrue(self.poll()["settled"])
        self.reviews.append({"author": {"login": "alice"}, "state": "PENDING"})
        self.assertEqual(self.poll()["settled_reasons"], ["working: alice has a pending review"])

    def test_mentions_in_own_marked_comments_are_ignored(self):
        self.settle()
        for skill in ("babysit", "autonomous-merge"):
            with self.subTest(skill):
                self.comments = [{"author": {"login": "me"}, "createdAt": at(5),
                                  "body": f"Asked @copilot and @codex, on it.\n<!-- vstack:{skill}:report -->"}]
                self.assertTrue(self.poll()["settled"])
        self.comments = [{"author": {"login": "me"}, "body": "@claude please look", "createdAt": at(5)}]
        self.assertEqual(self.poll()["settled_reasons"], ["working: me asked @claude to work on the PR"])

    def test_thread_states(self):
        self.threads = [
            thread("T_open", comment("alice", "Rename this", 1)),
            thread("T_bot", comment("alice", "Typo", 1), comment("deploy-bot", "I'm working on it", 2, bot=True)),
            thread("T_bot_rest", comment("alice", "Nit", 1), comment("ci[bot]", "on it", 2)),
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
            "thread:T_bot": ("open", None, False),
            "thread:T_bot_rest": ("open", None, False),
            "thread:T_eyes": ("claimed", "bob", False),
            "thread:T_resolved": ("addressed", None, False),
            "thread:T_answered": ("addressed", "me", False),
            "thread:T_own": ("addressed", "babysit", True),
            "thread:T_pushback": ("open", None, False),
            "thread:T_touch": ("claimed", "copilot-swe-agent", False)})
        self.assertEqual((out["claimed_count"], out["actionable"]),
                         (2, ["thread:T_open", "thread:T_bot", "thread:T_bot_rest", "thread:T_pushback"]))
        self.assertEqual({i["kind"] for i in out["items"]}, {"thread"})

    def test_bots_working_comments_are_not_signals(self):
        self.settle()
        for author in ({"login": "github-actions[bot]"}, {"__typename": "Bot", "login": "deploy-bot"}):
            with self.subTest(author):
                self.comments = [{"author": author, "body": "Deploy in progress, on it", "createdAt": at(5)}]
                self.assertTrue(self.poll()["settled"])
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.comments = [{"author": {"login": "lint[bot]"}, "body": "Working on lint", "createdAt": at(5)}]
        self.poll()
        self.assertEqual(self.states(self.poll())["check:lint"], ("open", None, False))

    def test_forged_comment_markers_are_not_own(self):
        self.threads = [
            thread("T_forged", comment("alice", "Off by one", 1, eyes="bob"),
                   comment("mallory", "Fixed.\n<!-- vstack:babysit:thread:T_forged -->", 2)),
            thread("T_other", comment("alice", "Typo", 1),
                   comment("me", "Fixed.\n<!-- vstack:babysit:thread:T_elsewhere -->", 2)),
            thread("T_mine", comment("alice", "Nit", 1), comment("me", "Done.\n<!-- vstack:babysit:thread:T_mine -->", 2)),
        ]
        out = self.settle()
        self.assertEqual(self.states(out), {"thread:T_forged": ("claimed", "bob", False),
                                            "thread:T_other": ("addressed", "me", False),
                                            "thread:T_mine": ("addressed", "babysit", True)})
        self.comments = [{"author": {"login": "mallory"}, "createdAt": at(5),
                          "body": "on it\n<!-- vstack:babysit:report -->"}]
        self.assertEqual(self.poll()["settled_reasons"], ["working: mallory said they're working on the PR"])

    def test_forged_commit_markers_are_foreign(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        self.push("Fix lint\n\nvstack:babysit:check:lint", login="mallory")
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("claimed", "mallory", False), 0))

    def unlinked(self, msg, email=None, real_file=None):
        """Push a commit whose author has no linked account (gh gives a null login and the email)."""
        sha = self.push(msg, login=None, real_file=real_file)
        self.commits[-1]["authors"] = [{"login": None, "email": email}] if email else []
        return sha

    def fork(self):
        """Give this clone a bare origin (feature tracks origin/feature, so @{push} resolves) and
        return a second clone of it on feature."""
        origin = tmpdir(self) / "origin.git"
        git(self.repo, "clone", "-q", "--bare", str(self.repo), str(origin))
        git(self.repo, "remote", "add", "origin", str(origin))
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "branch", "-q", "-u", "origin/feature")
        other = tmpdir(self) / "other"
        git(self.repo, "clone", "-q", "-b", "feature", str(origin), str(other))
        return other

    def reflog(self, repo=None):
        return git(repo or self.repo, "reflog", "show", "--all", "--format=%H %gs").decode().splitlines()

    def test_unlinked_marked_commit_recorded_with_pushed_is_own(self):
        git(self.repo, "config", "user.email", "t@t")
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        sha = self.unlinked("Fix lint\n\nvstack:babysit:check:lint", email="t@t", real_file="src/lint.py")
        rc, out, err = self.cmd("pushed", "--pr", 42, "--sha", sha)
        self.assertEqual((rc, out), (0, {"pr": 42, "pushed": sha, "recorded": 1}), err)
        git(self.repo, "reflog", "expire", "--expire=all", "--all")
        self.assertEqual(self.reflog(), [])
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("open", None, False), 1))
        self.assertEqual(self.state_file()["pushed"], [sha])

    def test_unlinked_marked_commit_committed_in_this_clone_is_own(self):
        for how in ("commit", "commit (amend)"):
            with self.subTest(how):
                self.setUp()
                self.rollup = [check("lint", conclusion="FAILURE")]
                self.settle()
                sha = self.unlinked("Fix lint\n\nvstack:babysit:check:lint", email="laptop@example.com",
                                    real_file="src/lint.py")
                if how == "commit (amend)":
                    git(self.repo, "commit", "-q", "--amend", "-m", "Fix lint, amended\n\nvstack:babysit:check:lint")
                    sha = self.headsha = self.commits[-1]["oid"] = git(self.repo, "rev-parse", "HEAD").decode().strip()
                self.assertIn(sha, {line.split(" ", 1)[0] for line in self.reflog() if line.startswith(f"{sha} {how}: ")})
                out = self.settle()
                self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("open", None, False), 1))
                self.assertEqual(self.state_file()["pushed"], [])

    def test_unlinked_marked_commit_not_committed_in_this_clone_is_foreign(self):
        def loose(msg):
            """A commit in the local object store, authored with the local email, that no reflog
            says this clone committed."""
            tree = git(self.repo, "rev-parse", "HEAD^{tree}").decode().strip()
            return git(self.repo, "commit-tree", tree, "-p", "HEAD", "-m", msg).decode().strip()

        msg = "Fix lint\n\nvstack:babysit:check:lint"
        cases = [("not in this clone", lambda: f"{self.n + 1:040x}"),
                 ("in the object store with the local email, no commit reflog entry", lambda: loose(msg))]
        for why, make in cases:
            with self.subTest(why):
                self.setUp()
                git(self.repo, "config", "user.email", "t@t")
                self.rollup = [check("lint", conclusion="FAILURE")]
                self.settle()
                sha = make()
                self.n += 1
                self.commits.append({**commit(sha, msg, None, self.n), "authors": [{"login": None, "email": "t@t"}]})
                self.headsha = sha
                self.assertNotIn(sha, {line.split(" ", 1)[0] for line in self.reflog()})
                out = self.settle()
                self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]),
                                 (("claimed", "someone", False), 0))
        tidy = self.unlinked("Tidy lint config", email="t@t", real_file="src/lint.py")
        self.assertEqual(self.states(self.settle())["check:lint"], ("claimed", "someone", False))
        reads = {a[-1] for a in self.gh.calls() if "--method" in a}
        self.assertEqual(reads, {f"repos/o/r/commits/{sha}"})
        self.assertNotIn(f"repos/o/r/commits/{tidy}", reads)

    def test_fetched_commit_from_a_clone_sharing_the_email_is_foreign(self):
        """Clones A (this one, the viewer) and B share one user.email, which GitHub links to no
        account. B pushes a babysit-marked fix for check:lint and A fetches it, so the commit is in
        A's object store under A's email: still B's, so the two fixers are conflicting."""
        msg = "Fix lint\n\nvstack:babysit:check:lint"
        other = self.fork()
        for clone in (self.repo, other):
            git(clone, "config", "user.email", "t@t")
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        ours = self.unlinked(msg, email="t@t", real_file="src/lint.py")
        git(self.repo, "push", "-q", "origin", "feature")
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", ours)[0], 0)
        self.rollup = [check("lint")]
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("addressed", None, True), 1))
        git(other, "pull", "-q", "--ff-only")
        (other / "src/lint.py").write_text("b\n")
        theirs = commit_all(other, msg)
        git(other, "push", "-q", "origin", "feature")
        self.assertNotEqual(git(self.repo, "cat-file", "-e", f"{theirs}^{{commit}}", check=False).returncode, 0)
        git(self.repo, "fetch", "-q", "origin")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%ae", theirs), b"t@t\n")
        self.assertEqual([x for x in self.reflog() if x.startswith(theirs)], [f"{theirs} fetch -q origin: fast-forward"])
        self.n += 1
        self.commits.append({**commit(theirs, msg, None, self.n), "authors": [{"login": None, "email": "t@t"}]})
        self.headsha = theirs
        self.rollup = [check("lint", conclusion="FAILURE")]
        out = self.poll(code=5)
        self.assertEqual((out["handback"]["reason"], out["own_fix_rounds"]), ("conflicting_fixer", 1))
        self.assertIn("check:lint", out["handback"]["detail"])
        item = self.state_file()["items"]["check:lint"]
        self.assertEqual((item["addressed_by"], item["transitions"][-1]["by"]), ("own", "foreign"))

    def test_unlinked_marked_commit_with_no_viewer_is_foreign(self):
        git(self.repo, "config", "user.email", "t@t")
        sha = self.unlinked("Fix lint\n\nvstack:babysit:check:lint", email="t@t", real_file="src/lint.py")
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", sha)[0], 0)
        bundle = {"view": {"headRefOid": sha, "commits": self.commits, "statusCheckRollup": [check("test")],
                           "comments": [], "reviews": []}, "threads": [], "pr_reactions": [], "comment_reactions": {}}
        rc, out, err = self.cmd("poll", "--pr", 42, "--input", "-", stdin=json.dumps(bundle))
        self.assertEqual((rc, out["own_fix_rounds"]), (0, 0), err)

    def test_pushed_subcommand(self):
        vstack = os.path.join(os.path.realpath(self.repo / ".git"), "vstack")
        for sha in ("a" * 39, "a" * 41, "a" * 63, self.head.upper(), "g" * 40, "--" + "a" * 38, f" {self.head}", ""):
            with self.subTest(sha):
                self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", sha)[0], 2)
        for args in (["pushed", "--pr", 42], ["pushed", "--sha", self.head], ["pushed", "--pr", "0", "--sha", self.head]):
            with self.subTest(args):
                self.assertEqual(self.cmd(*args)[0], 2)
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", self.head, cwd=tmpdir(self))[0], 2)
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        git(self.repo, "config", "user.email", "t@t")
        loose = git(self.repo, "commit-tree", "-m", "loose", tree).decode().strip()
        cases = [("no user.email", None, self.head, "user.email is not set"),
                 ("not a commit in this clone", "t@t", "c" * 40, "not a commit"),
                 ("a tree, not a commit", "t@t", tree, "not a commit"),
                 ("another author email", "me@example.com", self.head, "author email"),
                 ("no commit reflog entry", "t@t", loose, "reflog entry")]
        for why, email, sha, error in cases:
            with self.subTest(why):
                if email:
                    git(self.repo, "config", "user.email", email)
                else:
                    git(self.repo, "config", "--unset", "user.email", check=False)
                rc, out, err = self.cmd("pushed", "--pr", 42, "--sha", sha)
                self.assertEqual((rc, out), (2, None), err)
                self.assertIn(error, err)
                self.assertFalse(os.path.exists(vstack))
        git(self.repo, "config", "user.email", "T@T")
        self.fork()
        (self.repo / "src/a.py").write_text("x = 3\n")
        later = commit_all(self.repo, "Later")
        self.assertEqual(git(self.repo, "rev-parse", "@{push}").decode().strip(), self.head)
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", self.head)[:2],
                         (0, {"pr": 42, "pushed": self.head, "recorded": 1}))
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", later)[1]["recorded"], 2)
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", self.head)[1]["recorded"], 2)
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", loose)[0], 2)
        self.settle()
        st = self.state_file()
        self.assertEqual((st["pushed"], st["polls"]), ([self.head, later], 2))
        d = os.path.join(vstack, "42")
        self.assertEqual(sorted(os.listdir(d)), ["coordination.json"])
        self.assertEqual(self.gh.calls()[0][:2], ["pr", "view"])
        with open(os.path.join(d, "coordination.json"), "w") as f:
            json.dump({**st, "pushed": ["not a sha"]}, f)
        rc, _, err = self.cmd("pushed", "--pr", 42, "--sha", later)
        self.assertEqual(rc, 2)
        self.assertIn("pushed: must list commit SHAs", err)
        del st["pushed"]
        with open(os.path.join(d, "coordination.json"), "w") as f:
            json.dump(st, f)
        self.assertEqual(self.poll()["settled"], True)
        self.assertEqual(self.state_file()["pushed"], [])

    def test_pushed_rejects_a_reset_to_sibling_commit(self):
        """B shares A's email and pushes; A fetches and reset --hard to B's commit, so it is HEAD,
        @{push}, local and under A's email, but A never committed it: pushed exits 2."""
        other = self.fork()
        for clone in (self.repo, other):
            git(clone, "config", "user.email", "t@t")
        (other / "src/lint.py").write_text("b\n")
        theirs = commit_all(other, "Fix lint\n\nvstack:babysit:check:lint")
        git(other, "push", "-q", "origin", "feature")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "reset", "-q", "--hard", theirs)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD", "@{push}").decode().split(), [theirs, theirs])
        rc, out, err = self.cmd("pushed", "--pr", 42, "--sha", theirs)
        self.assertEqual((rc, out), (2, None), err)
        self.assertIn("reflog entry", err)
        self.assertFalse(os.path.exists(os.path.join(os.path.realpath(self.repo / ".git"), "vstack")))

    @unittest.skipUnless(sha256_supported(), "git without sha256 support")
    def test_pushed_sha256_head(self):
        repo, _, head = pr_repo(self, object_format="sha256")
        git(repo, "config", "user.email", "t@t")
        self.assertEqual(len(head), 64)
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", head, cwd=repo)[:2],
                         (0, {"pr": 42, "pushed": head, "recorded": 1}))

    def test_two_instances_unlinked_fixers_undoing_each_other(self):
        """Instance A (this clone, the viewer) and instance B (another clone, another user) both push
        babysit-marked fixes for check:lint from emails GitHub links to no account."""
        git(self.repo, "config", "user.email", "t@t")
        other = tmpdir(self) / "other"
        git(self.repo, "clone", "-q", str(self.repo), str(other))
        git(other, "config", "user.email", "b@example.com")
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        ours = self.unlinked("Fix lint\n\nvstack:babysit:check:lint", email="t@t", real_file="src/lint.py")
        self.assertEqual(self.cmd("pushed", "--pr", 42, "--sha", ours)[0], 0)
        self.rollup = [check("lint")]
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("addressed", None, True), 1))
        (other / "src/lint.py").write_text("b\n")
        theirs = commit_all(other, "Fix lint\n\nvstack:babysit:check:lint")
        self.assertNotEqual(git(self.repo, "cat-file", "-e", f"{theirs}^{{commit}}", check=False).returncode, 0)
        self.n += 1
        self.commits.append({**commit(theirs, "Fix lint\n\nvstack:babysit:check:lint", None, self.n),
                             "authors": [{"login": None, "email": "b@example.com"}]})
        self.headsha = theirs
        self.rollup = [check("lint", conclusion="FAILURE")]
        out = self.poll(code=5)
        self.assertEqual((out["handback"]["reason"], out["own_fix_rounds"]), ("conflicting_fixer", 1))
        self.assertIn("check:lint", out["handback"]["detail"])
        item = self.state_file()["items"]["check:lint"]
        self.assertEqual((item["addressed_by"], item["transitions"][-1]["from"], item["transitions"][-1]["to"],
                          item["transitions"][-1]["by"]), ("own", "addressed", "open", "foreign"))

    def test_committer_of_a_marked_commit_read_over_rest(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        theirs = self.push("Fix lint\n\nvstack:babysit:check:lint", login="mallory")
        mine = self.push("Fix lint again\n\nvstack:babysit:check:lint", login="mallory")
        self.push("Unmarked", login="mallory")
        self.extra = [{"match": [f"repos/o/r/commits/{mine}"], "stdout": {"committer": {"login": "me"}}},
                      {"match": [f"repos/o/r/commits/{theirs}"], "stdout": {"committer": {"login": "web-flow"}}}]
        out = self.settle()
        self.assertEqual((self.states(out)["check:lint"], out["own_fix_rounds"]), (("claimed", "mallory", False), 1))
        reads = {a[-1] for a in self.gh.calls() if "--method" in a}
        self.assertEqual(reads, {f"repos/o/r/commits/{theirs}", f"repos/o/r/commits/{mine}"})
        self.assertTrue(all(a[:5] == ["api", "--hostname", "github.com", "--method", "GET"]
                            for a in self.gh.calls() if "--method" in a))
        self.extra = [{"match": [f"repos/o/r/commits/{mine}"], "code": 1, "stderr": "HTTP 502"}]
        self.assertEqual(self.poll()["own_fix_rounds"], 0)

    def test_committer_of_an_unlinked_marked_commit_read_over_rest(self):
        self.rollup = [check("lint", conclusion="FAILURE")]
        self.settle()
        theirs = self.unlinked("Fix lint\n\nvstack:babysit:check:lint", email="laptop@example.com")
        mine = self.unlinked("Fix lint again\n\nvstack:babysit:check:lint", email="laptop@example.com")
        tidy = self.unlinked("Unmarked", email="laptop@example.com")
        self.extra = [{"match": [f"repos/o/r/commits/{mine}"], "stdout": {"committer": {"login": "me"}}},
                      {"match": [f"repos/o/r/commits/{theirs}"], "stdout": {"committer": {"login": "web-flow"}}}]
        self.assertEqual(self.settle()["own_fix_rounds"], 1)
        reads = [a[-1] for a in self.gh.calls() if "--method" in a]
        self.assertEqual(set(reads), {f"repos/o/r/commits/{theirs}", f"repos/o/r/commits/{mine}"})
        self.assertNotIn(f"repos/o/r/commits/{tidy}", reads)

    def test_newer_eyes_after_the_author_or_viewer_answer_keep_a_thread_claimed(self):
        self.author = "pat"

        def answered(tid, who, eyes_by, eyes_at, body="Because of the cache."):
            first = comment("alice", "Why?", 1)
            first["eyes"] = [[reaction(eyes_by, at(eyes_at))]]
            return thread(tid, first, comment(who, body.replace("TID", tid), 3))

        self.threads = [answered("T_viewer", "me", "bob", 4), answered("T_author", "pat", "carol", 4),
                        answered("T_author_eyes", "pat", "pat", 4),
                        answered("T_marked", "me", "bob", 4, "Fixed.\n<!-- vstack:babysit:thread:TID -->"),
                        answered("T_old_eyes", "me", "bob", 2), answered("T_own_eyes", "me", "me", 4)]
        out = self.settle()
        self.assertEqual(self.states(out), {"thread:T_viewer": ("claimed", "bob", False),
                                            "thread:T_author": ("claimed", "carol", False),
                                            "thread:T_author_eyes": ("claimed", "pat", False),
                                            "thread:T_marked": ("claimed", "bob", False),
                                            "thread:T_old_eyes": ("addressed", "me", False),
                                            "thread:T_own_eyes": ("addressed", "me", False)})

    def test_eyes_on_any_comment_of_a_thread_count(self):
        middle = comment("alice", "ping", 2, db=702, eyes="bob")
        self.threads = [thread("T1", comment("alice", "Off by one", 1, db=701), middle,
                               comment("alice", "again", 3, db=703))]
        middle["eyes"] = [[reaction("bob", at(4))]]
        out = self.settle()
        self.assertEqual(self.states(out)["thread:T1"], ("claimed", "bob", False))
        self.assertIn(["api", "--hostname", "github.com", "--paginate", "--slurp",
                       "repos/o/r/pulls/comments/702/reactions?content=eyes&per_page=100"], self.gh.calls())

    def test_mentions_in_thread_replies(self):
        self.settle()
        asked = thread("T1", comment("alice", "Off by one", 1), comment("carol", "@codex can you fix this?", 2))
        done = thread("T2", comment("alice", "@claude look", 1), resolved=True)
        self.threads = [asked, done]
        self.assertEqual(self.poll()["settled_reasons"], ["working: carol asked @codex to work on a review thread"])
        asked["comments"]["nodes"].append(comment("chatgpt-codex-connector[bot]", "Fixed in 1234567", 3))
        self.assertTrue(self.poll()["settled"])
        self.threads = [thread("T3", comment("me", "@copilot please\n<!-- vstack:babysit:thread:T3 -->", 1))]
        self.assertTrue(self.poll()["settled"])

    def test_coding_agents_working_replies_are_claims(self):
        agents = [("Copilot", True), ("copilot-swe-agent[bot]", False), ("COPILOT-SWE-AGENT", True),
                  ("chatgpt-codex-connector[bot]", False), ("Codex", False), ("claude[bot]", True), ("Claude", False)]
        self.threads = [thread(f"T{i}", comment("alice", "Typo", 1), comment(who, "I'm working on it", 2, bot=bot))
                        for i, (who, bot) in enumerate(agents)]
        self.threads.append(thread("T_bot", comment("alice", "Typo", 1),
                                   comment("renovate[bot]", "I'm working on it", 2, bot=True)))
        out = self.settle()
        self.assertEqual(self.states(out), {**{f"thread:T{i}": ("claimed", who, False) for i, (who, _) in enumerate(agents)},
                                            "thread:T_bot": ("open", None, False)})
        self.comments = [{"author": {"__typename": "Bot", "login": "copilot-swe-agent"}, "createdAt": at(5),
                          "body": "I'm working on the failing tests"}]
        self.assertEqual(self.poll()["settled_reasons"],
                         ["working: copilot-swe-agent said they're working on the PR"])

    def test_a_thread_with_more_comments_is_unreadable(self):
        self.settle()
        long = thread("T1", comment("alice", "Off by one", 1))
        long["comments"]["pageInfo"]["hasNextPage"] = True
        self.threads = [long]
        out = self.poll()
        self.assertEqual((out["settled"], out["settled_reasons"], out["items"]),
                         (False, ["threads_unreadable: couldn't read every review thread"], []))

    def test_only_the_author_viewer_or_claimant_answers_a_thread(self):
        self.threads = [
            thread("T_ci_bot", comment("alice", "Why?", 1), comment("github-actions[bot]", "Coverage 91%", 2, bot=True)),
            thread("T_agent", comment("alice", "Why?", 1), comment("codex", "Because.", 2)),
            thread("T_claimant", comment("alice", "Off by one", 1, eyes="copilot"),
                   comment("copilot", "Fixed in 1234567", 3, bot=True)),
            thread("T_worker", comment("alice", "Typo", 1), comment("bob", "on it", 2), comment("bob", "Fixed", 3)),
        ]
        out = self.settle()
        self.assertEqual(self.states(out), {
            "thread:T_ci_bot": ("open", None, False),
            "thread:T_agent": ("open", None, False),
            "thread:T_claimant": ("addressed", "copilot", False),
            "thread:T_worker": ("addressed", "bob", False)})

    def test_reactions_read_over_rest_across_pages(self):
        first = comment("alice", "Off by one", 1, db=501)
        first["eyes"] = [[reaction("carol", "2026-10-27T09:00:00Z")], [reaction("bob", at(4), 2)]]
        self.threads = [thread("T1", first, comment("alice", "ping", 2, db=502), comment("alice", "again", 3, db=503)),
                        thread("T_done", comment("alice", "Nit", 1, db=601), resolved=True)]
        self.reaction_pages = [[reaction("carol", "2026-10-27T09:00:00Z")], [reaction("dave", at(5), 2)]]
        out = self.settle()
        self.assertEqual(self.states(out)["thread:T1"], ("claimed", "bob", False))
        self.assertEqual(out["settled_reasons"], ["working: dave reacted 👀 on the PR after the last commit"])
        prefix = ["api", "--hostname", "github.com", "--paginate", "--slurp"]
        self.assertEqual([a for a in self.gh.calls() if "reactions?" in a[-1]][-4:], [
            prefix + ["repos/o/r/issues/42/reactions?content=eyes&per_page=100"],
            prefix + ["repos/o/r/pulls/comments/501/reactions?content=eyes&per_page=100"],
            prefix + ["repos/o/r/pulls/comments/502/reactions?content=eyes&per_page=100"],
            prefix + ["repos/o/r/pulls/comments/503/reactions?content=eyes&per_page=100"]])
        query = next(a for a in self.gh.calls() if "reviewThreads" in a[-1])[-1]
        self.assertNotIn("reactions", query)
        self.assertIn("databaseId", query)
        self.extra = [{"match": ["pulls/comments/503/reactions"], "code": 1, "stderr": "HTTP 502"}]
        self.reaction_pages = None
        out = self.poll()
        self.assertEqual(out["settled_reasons"],
                         ["reactions_unreadable: couldn't read every 👀 on the PR and its unresolved threads"])

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

    def test_reopened_thread_after_another_fixers_reply_escalates(self):
        theirs = thread("T1", comment("alice", "Off by one", 1), comment("copilot", "Fixed in 1234567", 2, bot=True),
                        resolved=True)
        ours = thread("T2", comment("alice", "Typo", 1),
                      comment("me", "Fixed.\n<!-- vstack:babysit:thread:T2 -->", 2), resolved=True)
        self.threads = [theirs, ours]
        self.assertEqual({i["state"] for i in self.settle()["items"]}, {"addressed"})
        theirs["isResolved"] = ours["isResolved"] = False
        out = self.poll(code=5)
        self.assertEqual((out["handback"]["reason"], out["handback"]["detail"]),
                         ("escalated", "thread:T1 was resolved, then reopened after another fixer replied in it"))
        self.assertEqual({i["id"]: i["escalate"] for i in out["items"]}, {"thread:T1": True, "thread:T2": False})
        self.assertEqual(out["actionable"], [])
        out = self.poll()
        self.assertEqual((out["handback"], out["actionable"], self.states(out)["thread:T1"][0]), (None, [], "open"))
        self.assertTrue(out["items"][0]["escalate"])
        theirs["isResolved"] = True
        self.assertFalse(self.poll()["items"][0]["escalate"])

    def test_update_branch_merge_is_a_new_head(self):
        self.settle()
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "README").write_text("base moved\n")
        commit_all(self.repo, "Base moves")
        git(self.repo, "checkout", "-q", "feature")
        git(self.repo, "merge", "-q", "--no-edit", "main")
        self.headsha = git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.commits.append(commit(self.headsha, "Merge branch 'main' into feature", "me", 1))
        out = self.poll()
        self.assertEqual((out["settled"], out["settled_reasons"], out["head"]),
                         (False, ["new_head: a commit arrived since the last poll"], self.headsha))
        self.assertTrue(self.poll()["settled"])

    def test_claim_thread_and_check(self):
        self.extra = [{"match": ["api graphql", "-f id=T1"],
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
        self.threads = [thread("T1", comment("alice", "Off by one", 1, cid="C1", eyes="me", db=77))]
        self.reactions = [{"content": "eyes", "user": {"login": "me"}, "created_at": at(9)}]
        out = self.settle()
        self.assertTrue(out["settled"])
        self.assertEqual((self.states(out)["thread:T1"], out["actionable"]), (("claimed", "babysit", True), ["thread:T1"]))

    def test_the_earlier_claim_wins(self):
        self.extra = [{"match": ["api graphql", "-f id=T1"],
                       "stdout": {"data": {"node": {"comments": {"nodes": [{"id": "C1", "databaseId": 77}]}}}}},
                      {"match": ["-X POST"], "stdout": "{}"}]
        self.assertEqual(self.cmd("claim", "--pr", 42, "--item", "thread:T1")[0], 0)
        first = comment("alice", "Off by one", 0, cid="C1", db=77)
        first["createdAt"] = "2000-01-01T00:00:00Z"
        for when, claimant, lost in (("2999-01-01T00:00:00Z", "babysit", []),
                                     ("2000-01-02T00:00:00Z", "bob", ["thread:T1"])):
            with self.subTest(when):
                first["eyes"] = [[reaction("bob", when)]]
                self.threads = [thread("T1", first, path="src/c.py")]
                out = self.poll()
                self.assertEqual((self.states(out)["thread:T1"][:2], out["lost_claims"]), (("claimed", claimant), lost))

    def test_remove_claim_after_losing_a_tie(self):
        rx = {"match": ["pulls/comments/77/reactions?content=eyes"],
              "stdout": [[reaction("bob", "2000-01-02T00:00:00Z", 9000)], [reaction("me", at(1), 9001)]]}
        self.extra = [{"match": ["api graphql", "-f id=T1"],
                       "stdout": {"data": {"node": {"comments": {"nodes": [{"id": "C1", "databaseId": 77}]}}}}},
                      {"match": ["-X POST"], "stdout": "{}"}, {"match": ["-X DELETE"], "stdout": ""},
                      {"match": ["api --hostname github.com user"], "stdout": {"login": "me"}}, rx]
        self.assertEqual(self.cmd("claim", "--pr", 42, "--item", "thread:T1")[0], 0)
        first = comment("alice", "Off by one", 0, cid="C1", db=77)
        first["createdAt"] = "2000-01-01T00:00:00Z"
        self.threads = [thread("T1", first, path="src/c.py")]
        out = self.poll()
        self.assertEqual((self.states(out)["thread:T1"], out["lost_claims"]), (("claimed", "bob", False), ["thread:T1"]))
        before = len(self.gh.calls())
        rc, out, err = self.cmd("remove-claim", "--pr", 42, "--item", "thread:T1")
        self.assertEqual((rc, out), (0, {"removed": "thread:T1", "endpoint": "repos/o/r/pulls/comments/77/reactions",
                                         "deleted_reactions": [9001], "kept_for": []}), err)
        calls = self.gh.calls()[before:]
        self.assertEqual(calls[1][:6], ["api", "graphql", "--hostname", "github.com", "-f", "id=T1"])
        self.assertEqual([calls[0]] + calls[2:], [
            ["pr", "view", "42", "--json", "url"],
            ["api", "--hostname", "github.com", "user"],
            ["api", "--hostname", "github.com", "--paginate", "--slurp",
             "repos/o/r/pulls/comments/77/reactions?content=eyes&per_page=100"],
            ["api", "--hostname", "github.com", "-X", "DELETE", "repos/o/r/pulls/comments/77/reactions/9001"]])
        st = self.state_file()
        self.assertEqual((st["claims"], st["self_subjects"]), ({}, []))
        rx["stdout"] = [[reaction("bob", "2000-01-02T00:00:00Z", 9000)]]
        out = self.poll()
        self.assertEqual((self.states(out)["thread:T1"], out["lost_claims"]), (("claimed", "bob", False), []))
        writes = [a for a in self.gh.calls() if "-X" in a]
        self.assertEqual([a[a.index("-X") + 1] for a in writes], ["POST", "DELETE"])

    def test_remove_claim_keeps_the_prs_eyes_while_another_check_holds_it(self):
        self.extra = [{"match": ["-X POST"], "stdout": "{}"}, {"match": ["-X DELETE"], "stdout": ""},
                      {"match": ["api --hostname github.com user"], "stdout": {"login": "me"}},
                      {"match": ["issues/42/reactions?content=eyes"],
                       "stdout": [[reaction("bob", at(1), 5)], [reaction("me", at(1), 6)]]}]
        for item in ("check:a", "check:b"):
            self.assertEqual(self.cmd("claim", "--pr", 42, "--item", item)[0], 0)
        rc, out, err = self.cmd("remove-claim", "--pr", 42, "--item", "check:a")
        self.assertEqual((rc, out["deleted_reactions"], out["kept_for"]), (0, [], ["check:b"]), err)
        self.assertEqual(self.state_file()["self_subjects"], ["pr"])
        rc, out, err = self.cmd("remove-claim", "--pr", 42, "--item", "check:b")
        self.assertEqual((rc, out["deleted_reactions"], out["kept_for"]), (0, [6], []), err)
        writes = [a for a in self.gh.calls() if "-X" in a]
        self.assertEqual(writes[-1], ["api", "--hostname", "github.com", "-X", "DELETE", "repos/o/r/issues/42/reactions/6"])
        self.assertEqual([a[a.index("-X") + 1] for a in writes], ["POST", "POST", "DELETE"])
        st = self.state_file()
        self.assertEqual((st["claims"], st["self_subjects"]), ({}, []))

    def test_markers(self):
        rc, out, _ = self.cmd("markers", "--item", "check:lint")
        self.assertEqual((rc, out), (0, {"item": "check:lint", "comment": "<!-- vstack:babysit:check:lint -->",
                                         "commit": "vstack:babysit:check:lint"}))

    def test_input_mode_reads_no_gh(self):
        bundle = {"view": {"headRefOid": self.head, "commits": self.commits, "statusCheckRollup": [check("test")],
                           "comments": [], "reviews": []}, "threads": [], "pr_reactions": [], "comment_reactions": {},
                  "viewer": "me"}
        for _ in range(2):
            rc, out, err = self.cmd("poll", "--pr", 42, "--input", "-", stdin=json.dumps(bundle))
        self.assertEqual((rc, out["settled"]), (0, True), err)
        self.assertEqual(self.gh.calls(), [])
        self.assertEqual(self.cmd("poll", "--pr", 42, "--input", "-", stdin="[]")[0], 2)
        bad = {**bundle, "comment_reactions": {"1": {}}}
        self.assertEqual(self.cmd("poll", "--pr", 42, "--input", "-", stdin=json.dumps(bad))[0], 2)

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
        os.symlink(tmpdir(self), vstack)
        rc, _, err = self.cmd("poll", "--pr", 42)
        self.assertEqual(rc, 2)
        self.assertIn("symlink", err)
        os.unlink(vstack)
        os.makedirs(os.path.join(vstack, "42"))
        os.symlink(tmpdir(self) / "x.json", os.path.join(vstack, "42", "coordination.json"))
        self.assertEqual(self.cmd("poll", "--pr", 42)[0], 2)

    def test_usage(self):
        for args in ([], ["-h"], ["poll", "-h"], ["poll"], ["poll", "--pr", "0"], ["claim", "--pr", "4", "--item", "x"],
                     ["remove-claim", "--pr", "4", "--item", "x"],
                     ["markers", "--item", "thread:a b"], ["markers", "--item", "check:a-->b"],
                     ["poll", "--pr", "4", "-R", "o"], ["poll", "--pr", "4", "--bogus"]):
            with self.subTest(args):
                self.assertEqual(self.cmd(*args)[0], 2)
        self.assertEqual(self.cmd("poll", "--pr", 42, cwd=tmpdir(self))[0], 2)
        self.assertEqual(self.gh.calls(), [])

    def test_gh_failure_is_internal_with_an_unknown_handback(self):
        self.extra = [{"match": ["pr view"], "code": 1, "stderr": f"HTTP 401: Bad credentials (token {TOKEN})"}]
        rc, out, err = self.cmd("poll", "--pr", 42)
        self.assertEqual((rc, out["handback"]["reason"], out["settled"]), (4, "unknown", False))
        self.assertIn("gh exited 1", out["handback"]["detail"])




class QueryBudget(unittest.TestCase):
    def test_threads_query_within_github_node_limit(self):
        import importlib.util, os, re
        spec = importlib.util.spec_from_file_location(
            "coord", os.path.join(os.path.dirname(__file__), "..", "coordination.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        total = 1
        for n in re.findall(r"first:(\d+)", mod.THREADS_QUERY):
            total *= int(n)
        self.assertLessEqual(total, 500_000)


if __name__ == "__main__":
    unittest.main()
