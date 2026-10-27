"""Tests for preflight.py against fake gh and curl on PATH: the ok paths for
GitHub and Bitbucket Cloud, one test per handback reason, host detection from
the URL and the git remote, and no token ever in output or argv.

Run from the repo root: python3 -m unittest discover -s skills/autonomous-merge/scripts/tests
"""
import json
import subprocess
import sys
import unittest

from helpers import (BB, SCRIPTS, Stubs, bb_pr, bb_rule, bitbucket_rules, gh_rule, git, github_rules, pr_repo, run,
                     tmpdir)

TOKEN = "bbtok_s3cr3t_VALUE_1234567890"
BB_URL = "https://bitbucket.org/ws/r/pull-requests/42"


class Preflight(unittest.TestCase):
    def setUp(self):
        self.repo, self.base, self.head = pr_repo()
        self.stubs = Stubs()

    def go(self, *rules, arg="42", env=None, cwd=None, **over):
        self.stubs.set(list(rules) or github_rules(self.head, self.base, **over))
        return run("preflight.py", arg, cwd=cwd or self.repo, env=env or self.stubs.env())

    def handback(self, reason, *rules, **kw):
        code, data, out, err = self.go(*rules, **kw)
        self.assertEqual(code, 5, (out, err))
        self.assertEqual(data["outcome"], "handback")
        self.assertEqual(data["reason"], reason, data)
        self.assertEqual(sorted(data), ["action", "detail", "outcome", "reason"])
        self.assertIn(reason, err)
        return data

    def test_github_ok(self):
        code, data, out, err = self.go()
        self.assertEqual(code, 0, err)
        self.assertEqual((data["outcome"], data["host"], data["owner"], data["repo"], data["pr"], data["head_sha"]),
                         ("ok", "github", "o", "r", 42, self.head))
        self.assertTrue(all(c["prompt_disabled"] == "1" for c in self.stubs.calls("gh")))

    def test_github_url_and_https_remote(self):
        self.assertEqual(self.go(arg="https://github.com/o/r/pull/42/files")[0], 0)
        git(self.repo, "remote", "set-url", "origin", "https://x-access-token:ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/o/r.git")
        code, data, out, err = self.go()
        self.assertEqual(code, 0, err)
        self.assertNotIn("ghp_", out + err)

    def test_viewer_permission_fallback(self):
        repo = gh_rule(["api --hostname github.com repos/o/r"], {"name": "r"})
        self.assertEqual(self.go(repo=repo)[0], 0)
        viewer = gh_rule(["repo view"], {"viewerPermission": "READ"})
        self.handback("insufficient_permission", repo=repo, viewer=viewer)

    def test_no_cli(self):
        stubs = Stubs(tools=())
        stubs.set([])
        data = self.handback("no_cli", env=stubs.env())
        self.assertIn("gh auth login", data["action"])

    def test_auth_missing(self):
        auth = gh_rule(["auth status"], "", 1, "You are not logged into any GitHub hosts. To log in, run: gh auth login")
        data = self.handback("auth_missing", auth=auth)
        self.assertIn("gh auth login --hostname github.com", data["action"])
        self.assertEqual([c["argv"][:2] for c in self.stubs.calls("gh")], [["auth", "status"]])

    def test_auth_expired(self):
        auth = gh_rule(["auth status"], "", 1, "X Failed to log in to github.com using token (GH_TOKEN)\n"
                                               "- The token in GH_TOKEN is invalid.")
        self.handback("auth_expired", auth=auth)
        repo = gh_rule(["api --hostname github.com repos/o/r"], "", 1, "gh: Bad credentials (HTTP 401)")
        self.handback("auth_expired", repo=repo)

    def test_insufficient_permission(self):
        repo = gh_rule(["api --hostname github.com repos/o/r"], {"permissions": {"push": False, "pull": True}})
        data = self.handback("insufficient_permission", repo=repo)
        self.assertIn("gh auth refresh --hostname github.com -s repo", data["action"])
        repo = gh_rule(["api --hostname github.com repos/o/r"], "", 1, "gh: Not Found (HTTP 404)")
        self.handback("insufficient_permission", repo=repo)
        pr = gh_rule(["pr view 42"], "", 1, "GraphQL: Could not resolve to a PullRequest with the number of 42.")
        self.handback("insufficient_permission", pr=pr)

    def test_not_open(self):
        pr = gh_rule(["pr view 42"], {"number": 42, "state": "MERGED"})
        self.assertIn("merged", self.handback("not_open", pr=pr)["detail"])

    def test_host_unsupported(self):
        for arg in ["https://gitlab.com/o/r/-/merge_requests/42", "https://git.example.com/o/r/pull/42"]:
            with self.subTest(arg):
                self.assertIn("not supported", self.handback("host_unsupported", arg=arg)["detail"])
        git(self.repo, "remote", "set-url", "origin", "git@gitlab.com:o/r.git")
        self.handback("host_unsupported")
        self.assertEqual(self.stubs.calls(), [])

    def test_unknown_without_a_remote(self):
        repo, _, _ = pr_repo(remote=None)
        self.handback("unknown", cwd=repo)

    def test_internal_error_prints_an_unknown_handback_first(self):
        code = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('c', sys.argv[1]); "
                "c = importlib.util.module_from_spec(s); s.loader.exec_module(c); "
                "c.main_wrapper('x', lambda: {}['boom'])")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(SCRIPTS / "common.py")], capture_output=True)
        self.assertEqual(r.returncode, 4)
        first = r.stdout.decode().splitlines()[0]
        self.assertEqual(json.loads(first), {"outcome": "handback", "reason": "unknown",
                                             "detail": "x: internal error: KeyError",
                                             "action": "Rerun; if it happens again, report it with the stderr line."})
        self.assertIn("internal error", r.stderr.decode())

    def test_usage(self):
        for arg in ["--admin", "abc", "https://github.com/o/r/issues/42", "ftp://github.com/o/r/pull/1", "-h",
                    "--help"]:
            with self.subTest(arg):
                code, _, out, err = self.go(arg=arg)
                self.assertEqual(code, 2, (out, err))
        self.assertEqual(self.stubs.calls(), [])

    def bb(self, *rules, env=None):
        return self.go(*(rules or bitbucket_rules(self.head, self.base)), arg=BB_URL,
                       env=env or self.stubs.env(BITBUCKET_TOKEN=TOKEN))

    def test_bitbucket_ok(self):
        code, data, out, err = self.bb()
        self.assertEqual(code, 0, err)
        self.assertEqual((data["host"], data["owner"], data["repo"], data["pr"]), ("bitbucket", "ws", "r", 42))
        calls = self.stubs.calls("curl")
        self.assertTrue(calls)
        for c in calls:
            self.assertNotIn(TOKEN, " ".join(c["argv"]))
            self.assertIn(f"Authorization: Bearer {TOKEN}", c["stdin"])
            self.assertEqual(c["argv"][0], "-q")

    def test_bitbucket_app_password_and_remote(self):
        git(self.repo, "remote", "set-url", "origin", "git@bitbucket.org:ws/r.git")
        env = self.stubs.env(BITBUCKET_USERNAME="me", BITBUCKET_APP_PASSWORD="ATBBapppassword123456")
        self.stubs.set(bitbucket_rules(self.head, self.base))
        code, data, out, err = run("preflight.py", "42", cwd=self.repo, env=env)
        self.assertEqual(code, 0, err)
        self.assertIn('user = "me:ATBBapppassword123456"', self.stubs.calls("curl")[0]["stdin"])

    def test_bitbucket_handbacks(self):
        rules = bitbucket_rules(self.head, self.base)
        cases = {
            "auth_missing": (rules, self.stubs.env()),
            "auth_expired": ([bb_rule(BB, {"error": {"message": "Unauthorized"}}, 401)], None),
            "insufficient_permission": (bitbucket_rules(self.head, self.base, pr=bb_rule(f"{BB}/pullrequests/42", {}, 404)),
                                        None),
            "not_open": (bitbucket_rules(self.head, self.base,
                                         pr=bb_rule(f"{BB}/pullrequests/42", bb_pr(self.head, self.base, state="MERGED"))),
                         None),
        }
        for reason, (rules, env) in cases.items():
            with self.subTest(reason):
                self.stubs.set(rules)
                code, data, out, err = run("preflight.py", BB_URL, cwd=self.repo,
                                           env=env or self.stubs.env(BITBUCKET_TOKEN=TOKEN))
                self.assertEqual((code, data["reason"]), (5, reason), (out, err))
                if reason == "auth_missing":
                    self.assertIn("BITBUCKET_TOKEN", data["action"])
        stubs = Stubs(tools=("gh",))
        stubs.set([])
        code, data, _, _ = run("preflight.py", BB_URL, cwd=self.repo, env=stubs.env(BITBUCKET_TOKEN=TOKEN))
        self.assertEqual((code, data["reason"]), (5, "no_cli"))

    def test_token_never_printed(self):
        leaky = bb_rule(BB, {"error": {"message": f"token {TOKEN} rejected"}}, 403, stderr=f"Authorization: Bearer {TOKEN}")
        for rules in ([leaky], [bb_rule(BB, None, 0, 6, f"curl: (6) Bearer {TOKEN} host")]):
            with self.subTest(rules[0]["http"]):
                code, data, out, err = self.bb(*rules)
                self.assertEqual(code, 5)
                self.assertNotIn(TOKEN, out + err)
        gh_token = "ghp_ZYXWVUTSRQPONMLKJIHGFEDCBA0123456789"
        auth = gh_rule(["auth status"], "", 1, f"The token in GH_TOKEN ({gh_token}) is invalid")
        self.stubs.set(github_rules(self.head, self.base, auth=auth))
        code, data, out, err = run("preflight.py", "42", cwd=self.repo, env=self.stubs.env(GH_TOKEN=gh_token))
        self.assertEqual(code, 5)
        self.assertNotIn(gh_token, out + err)
        self.assertNotIn(gh_token, " ".join(" ".join(c["argv"]) for c in self.stubs.calls()))

    def test_outside_a_repo_with_a_url(self):
        cwd = tmpdir()
        self.assertEqual(self.go(arg="https://github.com/o/r/pull/42", cwd=cwd)[0], 0)


if __name__ == "__main__":
    unittest.main()
