"""Shared helpers for the autonomous-merge skill's script tests. Everything runs
in temp dirs, with fake `gh` and `curl` on a PATH that holds nothing else but git."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILLS = SCRIPTS.parent.parent
REVIEW_WRITER = SKILLS / "review" / "scripts" / "write-result.py"
BABYSIT_WRITER = SKILLS / "babysit" / "scripts" / "write-result.py"

GIT_ENV = {k: v for k, v in os.environ.items()
           if not k.startswith(("GH_", "GITHUB_", "BITBUCKET_")) and k != "PATH"}
GIT_ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ.get("PATH", "")})

STUB = r'''#!%(python)s
import json, os, sys
d = os.environ["STUB_DIR"]
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
stdin = sys.stdin.read()
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps({"cmd": name, "argv": args, "stdin": stdin,
                        "prompt_disabled": os.environ.get("GH_PROMPT_DISABLED")}) + "\n")
joined = " ".join(args)
with open(os.path.join(d, "rules.json")) as f:
    rules = json.load(f)
used_path = os.path.join(d, "used.json")
used = json.load(open(used_path)) if os.path.exists(used_path) else []
for i, rule in enumerate(rules):
    if i not in used and rule["cmd"] == name and all(m in joined for m in rule["match"]):
        if rule.get("once"):
            with open(used_path, "w") as f:
                json.dump(used + [i], f)
        break
else:
    sys.stderr.write("stub: no rule for %%s %%s\n" %% (name, joined))
    sys.exit(99)
out = rule.get("stdout", "")
if name == "curl":
    body = rule.get("body")
    path = args[args.index("-o") + 1]
    with open(path, "w") as f:
        f.write(body if isinstance(body, str) else json.dumps(body) if body is not None else "")
    out = str(rule.get("http", 200))
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(rule.get("stderr", ""))
sys.exit(rule.get("code", 0))
'''


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="am-test-"))


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, env=GIT_ENV)
    if check and r.returncode:
        raise AssertionError(f"git {args} exited {r.returncode}: {r.stderr.decode()}")
    return r.stdout if check else r


def commit_all(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").decode().strip()


def pr_repo(remote="git@github.com:o/r.git"):
    """A repo on branch feature, one commit ahead of main. Returns (repo, base, head)."""
    repo = tmpdir() / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("x = 1\n")
    base = commit_all(repo, "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "src/a.py").write_text("x = 2\n")
    head = commit_all(repo, "head")
    if remote:
        git(repo, "remote", "add", "origin", remote)
    return repo, base, head


class Stubs:
    """A PATH holding fake gh and curl that answer from rules and log every call."""

    def __init__(self, tools=("gh", "curl")):
        self.dir = tmpdir() / "bin"
        self.dir.mkdir()
        os.symlink(shutil.which("git"), self.dir / "git")
        for tool in tools:
            p = self.dir / tool
            p.write_text(STUB % {"python": sys.executable})
            p.chmod(0o755)
        self.rules = []

    def set(self, rules):
        self.rules = rules
        (self.dir / "rules.json").write_text(json.dumps(rules))
        if (self.dir / "used.json").exists():
            os.unlink(self.dir / "used.json")

    def clear(self):
        for name in ("log.jsonl", "used.json"):
            if (self.dir / name).exists():
                os.unlink(self.dir / name)

    def calls(self, cmd=None):
        log = self.dir / "log.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in rows if cmd is None or r["cmd"] == cmd]

    def env(self, **extra):
        return {**GIT_ENV, "PATH": str(self.dir), "STUB_DIR": str(self.dir), "VSTACK_POLL_DELAYS": "0,0,0,0",
                "VSTACK_MERGE_POLL_DELAYS": "0,0,0", **extra}


def run(script, *args, cwd, env):
    r = subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *map(str, args)],
                       stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd, env=env)
    out = r.stdout.decode()
    try:
        data = json.loads(out) if out.strip() else None
    except ValueError:
        data = None
    return r.returncode, data, out, r.stderr.decode()


def write_result(writer, repo, pr, head, base, result):
    r = subprocess.run([sys.executable, "-I", str(writer), "--pr", str(pr), "--head-sha", head, "--base-sha", base],
                       input=json.dumps(result).encode(), capture_output=True, cwd=repo, env=GIT_ENV)
    if r.returncode:
        raise AssertionError(r.stderr.decode())


SHIP = {"status": "ship", "verdict": "Ship", "score": 9.0, "findings": [], "unreviewed_areas": [], "partial": False}


def finding(severity="major", file="src/a.py", rule="bug-hunt", summary="Off by one in loop at line 3", line=3,
            fid="C1"):
    return {"id": fid, "severity": severity, "file": file, "line": line, "rule": rule, "summary": summary,
            "verified": True}


def review(*findings):
    sev = {f["severity"] for f in findings}
    if "blocker" in sev:
        return {**SHIP, "status": "rethink", "verdict": "Rethink", "score": 3.0, "findings": list(findings)}
    if "major" in sev:
        return {**SHIP, "status": "fix", "verdict": "Ship with fixes", "score": 6.0, "findings": list(findings)}
    return {**SHIP, "findings": list(findings)}


def babysit(status="green", threads=(), checks=(("test", "passing"),)):
    req = [{"name": n, "state": s} for n, s in checks]
    states = {c["state"] for c in req}
    state = next((s for s in ("failing", "pending", "passing") if s in states), "none")
    green = status == "green"
    return {"status": status, "ci": {"state": state, "failing_checks": [c["name"] for c in req if c["state"] == "failing"]},
            "threads": list(threads), "last_action": "done", "mergeable": True if green else None,
            "approved_on_head": green, "changes_requested": False, "required_checks": req}


def gh_rule(match, stdout="", code=0, stderr=""):
    return {"cmd": "gh", "match": match, "stdout": stdout, "code": code, "stderr": stderr}


def threads_page(*threads, more=False):
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": more, "endCursor": "c"}, "nodes": list(threads)}}}}}


def thread(resolved=False, bot=False, url="https://github.com/o/r/pull/42#discussion_r1"):
    author = {"__typename": "Bot", "login": "copilot"} if bot else {"__typename": "User", "login": "alice"}
    return {"id": "PRRT_1", "isResolved": resolved,
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [{"databaseId": 1, "author": author, "url": url}]}}


def gh_review(state="APPROVED", login="bob", commit=None, bot=False):
    return {"user": {"login": login, "type": "Bot" if bot else "User"}, "state": state, "commit_id": commit}


def github_view(head, base, **over):
    v = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
         "reviewDecision": "APPROVED", "headRefOid": head, "baseRefOid": base, "baseRefName": "main",
         "author": {"login": "me"},
         "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                                "conclusion": "SUCCESS"}]}
    v.update(over)
    return v


VIEW_MATCH = ["pr view 42 -R github.com/o/r --json state,isDraft,mergeable"]
COORD_MATCH = ["pr view 42 -R github.com/o/r --json headRefOid,commits"]


def coord_view(head, **over):
    """gh pr view as babysit's coordination.py reads it: settled, nothing claimed."""
    v = {"headRefOid": head, "url": "https://github.com/o/r/pull/42", "author": {"login": "me"},
         "commits": [{"oid": head, "messageHeadline": "head", "messageBody": "", "committedDate": "2026-10-27T10:00:00Z",
                      "authors": [{"login": "me"}]}],
         "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                                "conclusion": "SUCCESS"}],
         "reviews": [], "comments": []}
    v.update(over)
    return v


def seed_coordination(repo, head, pr=42):
    """babysit's coordination state after a poll that saw head, so the gate's look is settled."""
    d = os.path.join(os.path.realpath(repo / ".git"), "vstack", str(pr))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "coordination.json"), "w") as f:
        json.dump({"schema_version": 1, "pr": pr, "last_head": head, "polls": 1, "items": {}, "claims": {},
                   "self_subjects": [], "working": {}, "files": {}}, f)


def github_rules(head, base, **over):
    """Rules for a ready PR. An override may be a list of rules, tried in order
    (give all but the last "once": True to answer a sequence of calls)."""
    rules = {
        "auth": gh_rule(["auth status --hostname github.com"], "github.com\n  Logged in to github.com account t\n"),
        "reviews": gh_rule(["repos/o/r/pulls/42/reviews"], [[gh_review(commit=head)]]),
        "rulesets": gh_rule(["repos/o/r/rules/branches/main"], []),
        "repo": gh_rule(["api --hostname github.com repos/o/r"],
                        {"permissions": {"push": True}, "allow_merge_commit": True, "allow_squash_merge": True,
                         "allow_rebase_merge": True}),
        "viewer": gh_rule(["repo view github.com/o/r --json viewerPermission"], {"viewerPermission": "WRITE"}),
        "pr": gh_rule(["pr view 42 -R github.com/o/r --json number,url"],
                      {"number": 42, "url": "https://github.com/o/r/pull/42", "state": "OPEN", "isDraft": False,
                       "headRefName": "feature", "baseRefName": "main", "headRefOid": head, "baseRefOid": base}),
        "view": gh_rule(VIEW_MATCH, github_view(head, base)),
        "checks": gh_rule(["pr checks 42 -R github.com/o/r --required"], [{"name": "test", "bucket": "pass"}]),
        "threads": gh_rule(["api graphql"], [threads_page(thread(resolved=True))]),
        "coord_view": gh_rule(COORD_MATCH, coord_view(head)),
        "pr_reactions": gh_rule(["issues/42/reactions"], [[]]),
        "viewer_login": gh_rule(["api --hostname github.com user"], {"login": "me"}),
        "comment_reactions": gh_rule(["pulls/comments/1/reactions"], [[]]),
        "disable": gh_rule(["pr merge 42 -R github.com/o/r --disable-auto"], ""),
        "merge": gh_rule(["pr merge 42"], ""),
    }
    rules.update(over)
    out = []
    for r in rules.values():
        out += r if isinstance(r, list) else [r] if r is not None else []
    return out


BB = "https://api.bitbucket.org/2.0/repositories/ws/r"


def bb_rule(match, body=None, http=200, code=0, stderr=""):
    return {"cmd": "curl", "match": [match] if isinstance(match, str) else match, "body": body, "http": http,
            "code": code, "stderr": stderr}


def bb_pr(head, base, **over):
    pr = {"id": 42, "state": "OPEN", "draft": False, "author": {"uuid": "{author}"},
          "source": {"branch": {"name": "feature"}, "commit": {"hash": head[:12]}},
          "destination": {"branch": {"name": "main"}, "commit": {"hash": base[:12]}},
          "participants": [{"user": {"uuid": "{rev}"}, "role": "REVIEWER", "approved": True, "state": "approved"}],
          "links": {"html": {"href": "https://bitbucket.org/ws/r/pull-requests/42"}}}
    pr.update(over)
    return pr


def bitbucket_rules(head, base, **over):
    rules = {
        "head": bb_rule(f"{BB}/commit/{head[:12]}", {"hash": head}),
        "base": bb_rule(f"{BB}/commit/{base[:12]}", {"hash": base}),
        "diffstat": bb_rule(f"{BB}/pullrequests/42/diffstat", {"values": [{"status": "modified"}]}),
        "statuses": bb_rule(f"{BB}/pullrequests/42/statuses", {"values": [{"name": "build", "state": "SUCCESSFUL"}]}),
        "tasks": bb_rule(f"{BB}/pullrequests/42/tasks", {"values": [{"state": "RESOLVED"}]}),
        "comments": bb_rule(f"{BB}/pullrequests/42/comments", {"values": [{"content": {"raw": "lgtm"}}]}),
        "pr": bb_rule(f"{BB}/pullrequests/42", bb_pr(head, base)),
        "repo": bb_rule(BB, {"full_name": "ws/r"}),
    }
    rules.update(over)
    return [r for r in rules.values() if r is not None]
