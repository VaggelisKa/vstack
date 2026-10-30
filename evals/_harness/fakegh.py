"""A fake `gh` for the plugin evals: a stateful stand-in for one GitHub repo and one pull request.

scaffold.py copies this file to $HOME/bin/gh in each eval run, with STATE pointing at the run's
.vstack-eval/ folder. It answers the commands the skills use from .vstack-eval/github.json,
reads branches and commits from the local bare repo .vstack-eval/origin.git (pushes land there through
the fake ssh), and appends every call to .vstack-eval/gh-calls.log as one line: `gh <args>`. Side
effects GitHub would have are also logged as `# <event> key=value...` lines, which the graders match:

    # merge-armed method=squash head=<sha>     # merged method=squash head=<sha>
    # auto-merge-disabled                      # comment target=pr body=<first line>
    # reply thread=<id> body=<first line>      # resolve thread=<id>
    # reaction add|remove target=<endpoint>    # rerun run=<id>
    # update-branch head=<sha>                 # refused <why>

Anything it doesn't model fails with exit 1 and a message, the way gh fails on an unknown endpoint.
"""
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

STATE = os.environ.get("VSTACK_EVAL_STATE") or "@STATE@"
GIT = os.environ.get("VSTACK_EVAL_GIT") or ("@GIT@" if not "@GIT@".startswith("@") else "git")
NOW = "2026-10-29T12:00:00Z"
FAIL = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}


class Exit(Exception):
    def __init__(self, code, out="", err=""):
        self.code, self.out, self.err = code, out, err


def fail(msg, code=1):
    raise Exit(code, err=msg.rstrip("\n") + "\n")


def http(status, msg):
    fail(f"gh: {msg} (HTTP {status})")


# ---- state -----------------------------------------------------------------

class State:
    def __init__(self, path):
        self.path = path
        with open(path) as f:
            self.d = json.load(f)
        self.dirty = False

    def __getitem__(self, k):
        return self.d[k]

    def get(self, k, default=None):
        return self.d.get(k, default)

    def save(self):
        if self.dirty:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.d, f, indent=1)
            os.replace(tmp, self.path)


def log(line):
    with open(os.path.join(STATE, "gh-calls.log"), "a") as f:
        f.write(line.replace("\n", " ") + "\n")


def event(name, **kv):
    log("# " + name + "".join(f" {k}={str(v).splitlines()[0] if str(v) else ''}" for k, v in kv.items()))


def refused(why):
    """A call GitHub (or the skill's rules) would refuse: `# refused <why>`."""
    log(f"# refused {why}")


def git(*args, check=True):
    r = subprocess.run([GIT, "--git-dir", os.path.join(STATE, "origin.git"), *args],
                       capture_output=True, text=True)
    if check and r.returncode:
        fail(f"fake gh: git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip(" \n")


def ref(name):
    return git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}", check=False) or None


# ---- the PR ----------------------------------------------------------------

def pr(s):
    return s["pr"]


def head(s):
    return ref(pr(s)["headRefName"]) or s["initial_head"]


def base(s):
    return ref(pr(s)["baseRefName"])


def race(s, argv):
    """The fixture's mid-run push: after `after_views` reads of the PR, its commit lands on the branch."""
    r = s.get("race")
    if not r or r.get("done") or argv[:2] != ["pr", "view"]:
        return
    r["views"] = r.get("views", 0) + 1
    s.dirty = True
    if r["views"] > r["after_views"]:
        git("update-ref", f"refs/heads/{pr(s)['headRefName']}", r["sha"])
        r["done"] = True
        event("race-push", head=r["sha"])


def sync_push(s):
    """Apply the fixture's on_push once the head moves past the scaffolded one."""
    h = head(s)
    if h != s["initial_head"] and not s.get("pushed"):
        s.d["pushed"] = True
        on = s.get("on_push") or {}
        for rv in on.get("reviews", []):
            s.d["reviews"].append({**rv, "commit_id": h})
        for th in on.get("resolve_threads", []):
            for t in s["threads"]:
                if t["id"] == th:
                    t["isResolved"] = True
        s.dirty = True


def checks(s, sha=None):
    sha = sha or head(s)
    by = s["checks"]
    if sha in by:
        return by[sha]
    after = s.get("checks_after_push")
    if after is None:
        after = [{**c, "conclusion": "SUCCESS", "log": ""} for c in by.get(s["initial_head"], [])]
    by[sha] = [dict(c, run_id=c["run_id"] + 1000) for c in after]
    s.dirty = True
    return by[sha]


def check_state(c):
    if c.get("conclusion") is None:
        return "PENDING"
    return c["conclusion"]


def bucket(c):
    st = check_state(c)
    return {"SUCCESS": "pass", "PENDING": "pending", "SKIPPED": "skipping", "NEUTRAL": "pass",
            "CANCELLED": "cancel"}.get(st, "fail")


def url(s, tail=""):
    return f"https://{s['host']}/{s['owner']}/{s['repo']}{tail}"


def pr_url(s):
    return url(s, f"/pull/{pr(s)['number']}")


def actor(login, bot=False):
    return {"login": login, "__typename": "Bot" if bot else "User", "is_bot": bot}


def commits(s):
    b, h = base(s), head(s)
    out = []
    for line in git("log", "--reverse", "--format=%H%x1f%an%x1f%ae%x1f%cI%x1f%s%x1f%b%x1e", f"{b}..{h}").split("\x1e"):
        if not line.strip():
            continue
        sha, an, ae, date, subj, body = line.strip("\n").split("\x1f")
        login = meta(s, sha)["author"]
        out.append({"oid": sha, "messageHeadline": subj, "messageBody": body.strip(), "committedDate": date,
                    "authoredDate": date, "authors": [{"login": login, "name": an, "email": ae}]})
    return out


def meta(s, sha):
    """GitHub logins for a commit: the fixture's, else the viewer's (the agent's own commits)."""
    m = (s.get("commit_logins") or {}).get(sha)
    return m or {"author": s["viewer"]["login"], "committer": s["viewer"]["login"]}


def reviews(s):
    return s["reviews"]


def review_required(s):
    """Whether a review rule applies to the base, as GitHub knows it even when the caller can't read
    protection or rulesets: review_required in the fixture, else from branch_protection and rulesets."""
    if s.get("review_required") is not None:
        return s["review_required"]
    bp, rules = s.get("branch_protection"), s.get("rulesets")
    if "error" in (bp, rules):
        return True
    return bool((isinstance(bp, dict) and bp.get("required_approvals", 1) > 0) or any(
        r.get("type") == "pull_request" and (r.get("parameters") or {}).get("required_approving_review_count", 0) > 0
        for r in rules or []))


def review_decision(s):
    """GitHub's reviewDecision: empty when no review rule applies to the base."""
    if not review_required(s):
        return ""
    latest = {}
    for rv in reviews(s):
        if rv["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[rv["user"]] = rv["state"]
    if "CHANGES_REQUESTED" in latest.values():
        return "CHANGES_REQUESTED"
    if "APPROVED" in latest.values():
        return "APPROVED"
    return "REVIEW_REQUIRED"


def merge_state(s):
    p = pr(s)
    if p.get("mergeStateStatus"):
        return p["mergeStateStatus"]
    cs = checks(s)
    if p.get("isDraft"):
        return "DRAFT"
    if any(bucket(c) == "fail" for c in cs):
        return "BLOCKED" if any(c.get("required", True) and bucket(c) == "fail" for c in cs) else "UNSTABLE"
    if any(bucket(c) == "pending" for c in cs):
        return "BLOCKED"
    return "CLEAN" if review_decision(s) in ("APPROVED", "") else "BLOCKED"


def view(s, fields):
    p, h, b = pr(s), head(s), base(s)
    cs = checks(s)
    all_fields = {
        "id": lambda: "PR_kwDOeval" + str(p["number"]),
        "number": lambda: p["number"],
        "title": lambda: p["title"],
        "body": lambda: p.get("body", ""),
        "url": lambda: pr_url(s),
        "state": lambda: p.get("state", "OPEN"),
        "closed": lambda: p.get("state", "OPEN") != "OPEN",
        "isDraft": lambda: p.get("isDraft", False),
        "isCrossRepository": lambda: p.get("isCrossRepository", False),
        "author": lambda: {"login": p["author"], "is_bot": False, "name": p["author"].title()},
        "headRefName": lambda: p["headRefName"],
        "baseRefName": lambda: p["baseRefName"],
        "headRefOid": lambda: h,
        "baseRefOid": lambda: b,
        "headRepository": lambda: {"name": s["repo"]},
        "headRepositoryOwner": lambda: {"login": s["owner"]},
        "mergeable": lambda: p.get("mergeable", "MERGEABLE"),
        "mergeStateStatus": lambda: merge_state(s),
        "reviewDecision": lambda: p.get("reviewDecision") or review_decision(s),
        "labels": lambda: [{"name": n, "id": "LA_" + n, "color": "ededed", "description": ""} for n in p.get("labels", [])],
        "reviews": lambda: [{"id": f"PRR_{rv['id']}", "author": {"login": rv["user"]}, "authorAssociation": "MEMBER",
                             "body": rv.get("body", ""), "submittedAt": rv.get("submitted_at", NOW),
                             "includesCreatedEdit": False, "reactionGroups": [], "state": rv["state"],
                             "commit": {"oid": rv["commit_id"]}} for rv in reviews(s)],
        "latestReviews": lambda: list({rv["author"]["login"]: rv for rv in all_fields["reviews"]()}.values()),
        "reviewRequests": lambda: [],
        "comments": lambda: [{"id": f"IC_{c['id']}", "author": {"login": c["user"]}, "authorAssociation": "MEMBER",
                              "body": c["body"], "createdAt": c.get("created_at", NOW), "url": pr_url(s) + f"#issuecomment-{c['id']}",
                              "viewerDidAuthor": c["user"] == s["viewer"]["login"]} for c in s["issue_comments"]],
        "statusCheckRollup": lambda: [{"__typename": "CheckRun", "name": c["name"], "workflowName": c.get("workflow", "CI"),
                                       "status": "COMPLETED" if c.get("conclusion") else "IN_PROGRESS",
                                       "conclusion": c.get("conclusion") or "", "startedAt": NOW,
                                       "completedAt": NOW if c.get("conclusion") else "0001-01-01T00:00:00Z",
                                       "detailsUrl": url(s, f"/actions/runs/{c['run_id']}/job/{c['run_id']}1")} for c in cs],
        "commits": lambda: commits(s),
        "files": lambda: [{"path": f, "additions": 1, "deletions": 0} for f in git("diff", "--name-only", f"{b}...{h}").splitlines()],
        "changedFiles": lambda: len(git("diff", "--name-only", f"{b}...{h}").splitlines()),
        "additions": lambda: 1,
        "deletions": lambda: 0,
        "autoMergeRequest": lambda: p.get("autoMergeRequest"),
        "mergedAt": lambda: p.get("mergedAt"),
        "mergedBy": lambda: {"login": s["viewer"]["login"]} if p.get("mergedAt") else None,
        "mergeCommit": lambda: {"oid": p["mergeCommit"]} if p.get("mergeCommit") else None,
        "assignees": lambda: [],
        "milestone": lambda: None,
        "projectCards": lambda: [],
        "createdAt": lambda: p.get("created_at", "2026-10-28T09:00:00Z"),
        "updatedAt": lambda: NOW,
    }
    if fields is None:
        return all_fields
    out = {}
    for f in fields.split(","):
        f = f.strip()
        if f not in all_fields:
            fail(f'Unknown JSON field: "{f}"\nAvailable fields:\n  ' + "\n  ".join(sorted(all_fields)))
        out[f] = all_fields[f]()
    return out


# ---- output helpers --------------------------------------------------------

def jq(data, expr):
    exe = shutil.which("jq")
    if exe:
        r = subprocess.run([exe, "-r", expr], input=json.dumps(data), capture_output=True, text=True)
        if r.returncode:
            fail(r.stderr)
        return r.stdout
    cur = data
    for part in re.findall(r"\.([A-Za-z_][\w]*)|\[(\d+)\]", expr):
        cur = cur[part[0]] if part[0] else cur[int(part[1])]
    return (cur if isinstance(cur, str) else json.dumps(cur)) + "\n"


def emit(data, opts):
    if opts.get("jq"):
        raise Exit(0, jq(data, opts["jq"]))
    raise Exit(0, json.dumps(data, indent=2) + "\n")


# ---- argument parsing ------------------------------------------------------

VALUED = {"-R", "--repo", "--json", "-q", "--jq", "-t", "--template", "--hostname", "-f", "-F", "--field",
          "--raw-field", "-X", "--method", "-b", "--body", "--body-file", "-H", "--header", "--input",
          "--branch", "-b", "--workflow", "-w", "--limit", "-L", "--state", "-s", "--head", "--base",
          "--match-head-commit", "--subject", "--author", "-j", "--job", "-c", "--commit", "--interval", "-i",
          "--search", "-S", "--label", "-l", "--add-label", "--remove-label", "--title", "-T", "-r",
          "--reviewer", "--event", "-e", "--attempt", "-a", "--exit-status", "-A", "--app"}


def parse(args):
    pos, opts, fields = [], {}, []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            a, v = a.split("=", 1)
            args = args[:i] + [a, v] + args[i + 1:]
        if a in VALUED and i + 1 < len(args):
            v = args[i + 1]
            key = {"-R": "repo", "-q": "jq", "--jq": "jq", "-X": "method", "-b": "body", "-L": "limit",
                   "-s": "state", "-H": "header", "-t": "template", "-w": "workflow", "-j": "job"}.get(a, a.lstrip("-"))
            if a in ("-f", "-F", "--field", "--raw-field"):
                k, _, val = v.partition("=")
                if a in ("-F", "--field"):
                    if val.startswith("@"):
                        val = sys.stdin.read() if val == "@-" else open(val[1:]).read()
                    elif re.fullmatch(r"-?\d+", val):
                        val = int(val)
                    elif val in ("true", "false", "null"):
                        val = json.loads(val)
                fields.append((k, val))
            else:
                opts[key] = v
            i += 2
            continue
        if a.startswith("-") and a != "-":
            opts[a.lstrip("-")] = True
        else:
            pos.append(a)
        i += 1
    return pos, opts, dict(fields)


def check_repo(s, opts):
    want = opts.get("repo")
    if not want:
        return
    parts = re.sub(r"^https?://", "", want).rstrip("/").removesuffix(".git").split("/")
    if parts[-2:] != [s["owner"], s["repo"]] or (len(parts) == 3 and parts[0] != s["host"]):
        fail(f"GraphQL: Could not resolve to a Repository with the name '{'/'.join(parts[-2:])}'. (repository)")


def check_pr(s, sel):
    """The PR selected by a number, URL or branch name (or the current branch)."""
    p = pr(s)
    if sel is None:
        cur = subprocess.run([GIT, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
        if cur != p["headRefName"]:
            fail(f'no pull requests found for branch "{cur}"')
        return
    m = re.search(r"/pull/(\d+)", sel)
    if m:
        if not sel.startswith(url(s) + "/"):
            fail(f"GraphQL: Could not resolve to a Repository with the name '{sel.split('/pull/')[0].split('/', 3)[-1]}'. (repository)")
        sel = m.group(1)
    sel = sel.lstrip("#")
    if sel.isdigit():
        if int(sel) != p["number"]:
            fail(f"GraphQL: Could not resolve to a PullRequest with the number of {sel}. (repository.pullRequest)")
    elif sel != p["headRefName"]:
        fail(f'no pull requests found for branch "{sel}"')


def body_of(opts):
    if "body" in opts:
        return opts["body"]
    if "body-file" in opts:
        return sys.stdin.read() if opts["body-file"] == "-" else open(opts["body-file"]).read()
    return ""


# ---- gh pr / run / repo / auth ---------------------------------------------

def cmd_auth(s, pos, opts):
    a = s.get("auth") or {"ok": True}
    if pos[:1] == ["status"]:
        if a.get("ok", True):
            raise Exit(0, f"{s['host']}\n  ✓ Logged in to {s['host']} account {s['viewer']['login']} (keyring)\n"
                          "  - Active account: true\n  - Git operations protocol: ssh\n"
                          "  - Token: gho_************************************\n  - Token scopes: 'repo', 'workflow'\n")
        fail(a.get("message") or f"{s['host']}\n  X Failed to log in to {s['host']} using token (GH_TOKEN)\n"
                                  "  - Active account: true\n  - The token in GH_TOKEN is invalid.")
    if pos[:1] == ["token"]:
        fail("fake gh: printing tokens is not available in this evaluation")
    fail(f"fake gh: `gh auth {' '.join(pos)}` is not available in this evaluation")


def cmd_repo(s, pos, opts):
    if pos[:1] != ["view"]:
        fail(f"fake gh: `gh repo {' '.join(pos[:1])}` is not available in this evaluation")
    if len(pos) > 1:
        check_repo(s, {"repo": pos[1]})
    rs = s["repo_settings"]
    data = {"name": s["repo"], "owner": {"login": s["owner"]}, "nameWithOwner": f"{s['owner']}/{s['repo']}",
            "url": url(s), "defaultBranchRef": {"name": rs.get("default_branch", "main")},
            "viewerPermission": s.get("permission", "WRITE"), "isPrivate": True, "isFork": False,
            "mergeCommitAllowed": rs.get("allow_merge_commit", True), "squashMergeAllowed": rs.get("allow_squash_merge", True),
            "rebaseMergeAllowed": rs.get("allow_rebase_merge", True), "deleteBranchOnMerge": False,
            "description": "Acme API"}
    if "json" in opts:
        emit({f: data.get(f) for f in opts["json"].split(",")}, opts)
    raise Exit(0, f"{s['owner']}/{s['repo']}\nAcme API\n")


def cmd_pr(s, pos, opts, fields):
    sub, rest = pos[0], pos[1:]
    check_repo(s, opts)
    if sub == "list":
        p = pr(s)
        hit = (opts.get("head") in (None, p["headRefName"]) and opts.get("state", "open").upper() in (p.get("state", "OPEN"), "ALL")
               and opts.get("base") in (None, p["baseRefName"]))
        data = [view(s, opts.get("json") or "number,title,headRefName,url")] if hit else []
        if "json" in opts:
            emit(data, opts)
        raise Exit(0, "".join(f"{d['number']}\t{d['title']}\t{d['headRefName']}\tOPEN\n" for d in data))
    sel = rest[0] if rest else None
    check_pr(s, sel)
    p = pr(s)
    if sub == "view":
        if "json" in opts:
            emit(view(s, opts["json"]), opts)
        v = view(s, "title,state,author,body,url,reviewDecision,headRefName,baseRefName,labels")
        raise Exit(0, f"title:\t{v['title']}\nstate:\t{v['state']}\nauthor:\t{v['author']['login']}\n"
                      f"labels:\t{', '.join(l['name'] for l in v['labels'])}\nnumber:\t{p['number']}\n"
                      f"url:\t{v['url']}\n--\n{v['body']}\n")
    if sub == "diff":
        names = git("diff", "--name-only", f"{base(s)}...{head(s)}") if "name-only" in opts else None
        raise Exit(0, (names + "\n") if names is not None else git("diff", f"{base(s)}...{head(s)}") + "\n")
    if sub == "checks":
        return pr_checks(s, opts)
    if sub == "checkout":
        b = p["headRefName"]
        for args in (["fetch", "-q", "origin", f"+refs/heads/{b}:refs/remotes/origin/{b}"],
                     ["checkout", "-q", "-B", b, f"origin/{b}"]):
            r = subprocess.run([GIT, *args], capture_output=True, text=True)
            if r.returncode:
                fail(r.stderr or f"git {args[0]} failed")
        # The scaffold set the upstream; the sandbox may refuse writes to .git/config, so don't redo it.
        raise Exit(0, err=f"Switched to branch '{b}'\n")
    if p.get("state", "OPEN") != "OPEN" and sub in ("merge", "comment", "update-branch", "ready", "review", "edit"):
        if not (sub == "merge" and "disable-auto" in opts):
            fail(f"GraphQL: Pull request is {p['state'].lower()} (mergePullRequest)" if sub == "merge"
                 else f"pull request #{p['number']} is {p['state'].lower()}")
    if sub == "merge":
        return pr_merge(s, opts)
    if sub == "comment":
        body = body_of(opts)
        s["issue_comments"].append({"id": 900 + len(s["issue_comments"]), "user": s["viewer"]["login"], "body": body})
        s.dirty = True
        event("comment", target="pr", body=body.strip())
        raise Exit(0, pr_url(s) + f"#issuecomment-{900 + len(s['issue_comments'])}\n")
    if sub == "review":
        event("review", kind=next((k for k in ("approve", "request-changes", "comment") if k in opts), "comment"),
              body=body_of(opts).strip())
        raise Exit(0)
    if sub == "update-branch":
        if "rebase" in opts:
            refused("update-branch --rebase")
        return update_branch(s)
    if sub == "ready":
        p["isDraft"] = False
        s.dirty = True
        event("ready")
        raise Exit(0, err=f"✓ Pull request {s['owner']}/{s['repo']}#{p['number']} is marked as \"ready for review\"\n")
    if sub == "edit":
        for k in ("add-label", "remove-label", "title", "body"):
            if k in opts:
                event("edit", **{k: opts[k]})
        raise Exit(0, pr_url(s) + "\n")
    fail(f"fake gh: `gh pr {sub}` is not available in this evaluation")


def pr_checks(s, opts):
    cs = checks(s)
    if "required" in opts:
        req = s.get("required_checks")
        if req == "error":
            fail("failed to get required checks: HTTP 403: Resource not accessible by integration")
        if not req:
            fail(f"no required checks reported on the '{pr(s)['headRefName']}' branch")
        cs = [c for c in cs if c["name"] in req]
    rows = [{"name": c["name"], "state": check_state(c), "bucket": bucket(c), "workflow": c.get("workflow", "CI"),
             "link": url(s, f"/actions/runs/{c['run_id']}/job/{c['run_id']}1"), "description": "",
             "event": "pull_request", "startedAt": NOW, "completedAt": NOW if c.get("conclusion") else "0001-01-01T00:00:00Z"}
            for c in cs]
    code = 1 if any(r["bucket"] == "fail" for r in rows) else 8 if any(r["bucket"] == "pending" for r in rows) else 0
    if "json" in opts:
        out = [{k: r[k] for k in opts["json"].split(",") if k in r} for r in rows]
        raise Exit(code, jq(out, opts["jq"]) if opts.get("jq") else json.dumps(out, indent=2) + "\n")
    raise Exit(code, "".join(f"{r['name']}\t{r['bucket']}\t0s\t{r['link']}\n" for r in rows))


def pr_merge(s, opts):
    p, h = pr(s), head(s)
    if "admin" in opts:
        refused("--admin")
    if "disable-auto" in opts:
        if not p.get("autoMergeRequest"):
            fail("GraphQL: Can't disable auto-merge for this pull request. (disablePullRequestAutoMerge)")
        p["autoMergeRequest"] = None
        s.dirty = True
        event("auto-merge-disabled")
        raise Exit(0, err=f"✓ Auto-merge disabled for pull request {s['owner']}/{s['repo']}#{p['number']}\n")
    methods = [m for m in ("merge", "squash", "rebase") if m in opts]
    rs = s["repo_settings"]
    allowed = [m for m, k in (("merge", "allow_merge_commit"), ("squash", "allow_squash_merge"), ("rebase", "allow_rebase_merge"))
               if rs.get(k, True)]
    if not methods:  # gh only prompts for one in a terminal, and the agent's shell isn't one
        fail("--merge, --rebase, or --squash required when not running interactively")
    method = methods[0]
    if method not in allowed:
        fail(f"GraphQL: {method.capitalize()} merges are not allowed on this repository. (mergePullRequest)")
    want = opts.get("match-head-commit")
    if want and want != h:
        refused(f"match-head-commit {want[:12]} is not the head {h[:12]}")
        fail(f"GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)")
    blocked = merge_state(s) not in ("CLEAN", "HAS_HOOKS", "UNSTABLE") or p.get("labels_block_merge")
    if "auto" in opts:
        p["autoMergeRequest"] = {"mergeMethod": method.upper(), "enabledAt": NOW,
                                 "enabledBy": {"login": s["viewer"]["login"]}}
        event("merge-armed", method=method, head=h)
        s.dirty = True
        if not blocked and (s.get("merge") or {}).get("when_armed", "merge") == "merge":
            do_merge(s, method, h)
        raise Exit(0, err=f"✓ Pull request {s['owner']}/{s['repo']}#{p['number']} will be automatically merged via "
                          f"{method} when all requirements are met\n")
    if blocked:
        fail("X Pull request {}/{}#{} is not mergeable: the base branch policy prohibits the merge.\n"
             "To have the pull request merged after all the requirements have been met, add the `--auto` flag.\n"
             "To use administrator privileges to immediately merge the pull request, add the `--admin` flag."
             .format(s["owner"], s["repo"], p["number"]))
    do_merge(s, method, h)
    raise Exit(0, err=f"✓ Merged pull request {s['owner']}/{s['repo']}#{p['number']}\n")


def do_merge(s, method, h):
    p = pr(s)
    p.update(state="MERGED", mergedAt=NOW, mergeCommit=h, autoMergeRequest=None)
    s.dirty = True
    event("merged", method=method, head=h)


def update_branch(s):
    p, b, h = pr(s), base(s), head(s)
    if subprocess.run([GIT, "--git-dir", os.path.join(STATE, "origin.git"), "merge-base", "--is-ancestor", b, h]).returncode == 0:
        fail("! PR branch already up-to-date")
    tree = git("merge-tree", "--write-tree", h, b).splitlines()[0]
    env = {**os.environ, "GIT_AUTHOR_NAME": "GitHub", "GIT_AUTHOR_EMAIL": "noreply@github.com",
           "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com"}
    new = subprocess.run([GIT, "--git-dir", os.path.join(STATE, "origin.git"), "commit-tree", tree, "-p", h, "-p", b,
                          "-m", f"Merge branch '{p['baseRefName']}' into {p['headRefName']}"],
                         capture_output=True, text=True, env=env, check=True).stdout.strip()
    git("update-ref", f"refs/heads/{p['headRefName']}", new, h)
    s.d.setdefault("commit_logins", {})[new] = {"author": s["viewer"]["login"], "committer": "web-flow"}
    s.dirty = True
    event("update-branch", head=new)
    raise Exit(0, err="✓ PR branch updated\n")


def find_run(s, rid):
    for sha, cs in s["checks"].items():
        for c in cs:
            if str(c["run_id"]) == str(rid):
                return sha, c
    fail(f"HTTP 404: Not Found (https://api.github.com/repos/{s['owner']}/{s['repo']}/actions/runs/{rid})")


def cmd_run(s, pos, opts):
    check_repo(s, opts)
    sub, rest = pos[0], pos[1:]
    if sub == "list":
        wf, br = opts.get("workflow"), opts.get("branch")
        runs = []
        for r in s.get("runs", []):
            if (wf in (None, r["workflow"]) or wf.endswith(r.get("file", "\0"))) and br in (None, r["branch"]):
                runs.append({"databaseId": r["id"], "workflowName": r["workflow"], "headBranch": r["branch"],
                             "status": "completed", "conclusion": r["conclusion"].lower(), "event": "push",
                             "createdAt": r.get("created_at", NOW), "displayTitle": r.get("title", "CI"),
                             "url": url(s, f"/actions/runs/{r['id']}"), "headSha": r.get("sha", "")})
        if "json" in opts:
            emit([{k: r.get(k) for k in opts["json"].split(",")} for r in runs], opts)
        raise Exit(0, "".join(f"completed\t{r['conclusion']}\t{r['displayTitle']}\t{r['workflowName']}\t{r['headBranch']}"
                              f"\tpush\t{r['databaseId']}\n" for r in runs))
    if not rest:
        fail("run ID required when not running interactively")
    rid = rest[0]
    sha, c = find_run(s, rid)
    if sub == "view":
        if "log-failed" in opts or "log" in opts:
            raise Exit(0, c.get("log") or "")
        data = {"databaseId": c["run_id"], "name": c.get("workflow", "CI"), "workflowName": c.get("workflow", "CI"),
                "status": "completed" if c.get("conclusion") else "in_progress", "conclusion": (c.get("conclusion") or "").lower(),
                "headSha": sha, "headBranch": pr(s)["headRefName"], "event": "pull_request", "attempt": c.get("attempt", 1),
                "url": url(s, f"/actions/runs/{c['run_id']}"),
                "jobs": [{"databaseId": int(f"{c['run_id']}1"), "name": c["name"], "status": "completed",
                          "conclusion": (c.get("conclusion") or "").lower(), "steps": []}]}
        if "json" in opts:
            emit({k: data.get(k) for k in opts["json"].split(",")}, opts)
        raise Exit(0, f"{'X' if bucket(c) == 'fail' else '✓'} {pr(s)['headRefName']} {data['workflowName']} · {rid}\n"
                      f"JOBS\n{'X' if bucket(c) == 'fail' else '✓'} {c['name']} in 1m2s (ID {rid}1)\n")
    if sub == "rerun":
        c["attempt"] = c.get("attempt", 1) + 1
        c["conclusion"] = c.get("rerun_conclusion", c.get("conclusion"))
        c["log"] = c.get("rerun_log", c.get("log", ""))
        s.dirty = True
        event("rerun", run=rid, check=c["name"])
        raise Exit(0, err=f"✓ Requested rerun of run {rid}\n")
    if sub == "watch":
        raise Exit(1 if bucket(c) == "fail" else 0, f"Run {rid} completed with '{(c.get('conclusion') or '').lower()}'\n")
    fail(f"fake gh: `gh run {sub}` is not available in this evaluation")


# ---- gh api ----------------------------------------------------------------

def threads_payload(s):
    return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "totalCount": len(s["threads"]),
            "nodes": [thread_node(s, t) for t in s["threads"]]}


def thread_node(s, t):
    return {"id": t["id"], "isResolved": t.get("isResolved", False), "isOutdated": t.get("isOutdated", False),
            "path": t.get("path"), "line": t.get("line"), "originalLine": t.get("line"), "diffSide": "RIGHT",
            "resolvedBy": None, "viewerCanResolve": True, "viewerCanReply": True,
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "totalCount": len(t["comments"]),
                         "nodes": [{"id": c["id"], "databaseId": c["databaseId"], "body": c["body"],
                                    "author": {"__typename": "Bot" if c.get("bot") else "User", "login": c["user"]},
                                    "createdAt": c.get("created_at", NOW), "url": pr_url(s) + f"#discussion_r{c['databaseId']}",
                                    "path": t.get("path"), "line": t.get("line"), "reactionGroups": []}
                                   for c in t["comments"]]}}


def gql_pull(s):
    v = view(s, None)
    rv = v["reviews"]()
    return {"id": v["id"](), "number": v["number"](), "title": v["title"](), "body": v["body"](), "url": v["url"](),
            "state": v["state"](), "isDraft": v["isDraft"](), "headRefName": v["headRefName"](), "baseRefName": v["baseRefName"](),
            "headRefOid": v["headRefOid"](), "baseRefOid": v["baseRefOid"](), "mergeable": v["mergeable"](),
            "mergeStateStatus": v["mergeStateStatus"](), "reviewDecision": v["reviewDecision"]() or None,
            "author": actor(pr(s)["author"]), "isCrossRepository": v["isCrossRepository"](),
            "autoMergeRequest": v["autoMergeRequest"](), "reviewThreads": threads_payload(s),
            "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
                {**r, "author": actor(r["author"]["login"])} for r in rv]},
            "latestReviews": {"nodes": [{**r, "author": actor(r["author"]["login"])} for r in v["latestReviews"]()]},
            "labels": {"nodes": v["labels"]()},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
                {**c, "author": actor(c["author"]["login"])} for c in v["comments"]()]},
            "commits": {"nodes": [{"commit": {"oid": c["oid"], "message": c["messageHeadline"],
                                              "statusCheckRollup": {"state": "SUCCESS"}}} for c in v["commits"]()]}}


def graphql(s, fields):
    q = fields.get("query", "")
    tid = fields.get("id") or fields.get("threadId") or fields.get("thread")
    m = re.search(r'threadId\s*:\s*"([^"]+)"|pullRequestReviewThreadId\s*:\s*"([^"]+)"', q)
    if m:
        tid = m.group(1) or m.group(2)
    thread = next((t for t in s["threads"] if t["id"] == tid), None)
    if re.search(r"\b(un)?resolveReviewThread\b", q):
        if not thread:
            fail(f"GraphQL: Could not resolve to a node with the global id of '{tid}' (resolveReviewThread)")
        resolve = "unresolveReviewThread" not in q
        thread["isResolved"] = resolve
        s.dirty = True
        event("resolve" if resolve else "unresolve", thread=tid)
        key = "resolveReviewThread" if resolve else "unresolveReviewThread"
        return {"data": {key: {"thread": {"id": tid, "isResolved": resolve}}}}
    if re.search(r"\baddPullRequestReviewThreadReply\b|\baddPullRequestReviewComment\b", q):
        body = fields.get("body") or (re.search(r'body\s*:\s*"((?:[^"\\]|\\.)*)"', q) or [None, ""])[1]
        if not thread:
            fail(f"GraphQL: Could not resolve to a node with the global id of '{tid}'")
        thread["comments"].append({"id": f"PRRC_new{len(thread['comments'])}", "databaseId": 7000 + len(thread["comments"]),
                                   "user": s["viewer"]["login"], "body": body})
        s.dirty = True
        event("reply", thread=tid, body=body.strip())
        return {"data": {"addPullRequestReviewThreadReply": {"comment": {"id": "PRRC_new", "url": pr_url(s)}}}}
    if re.search(r"\baddComment\b", q):
        body = fields.get("body", "")
        s["issue_comments"].append({"id": 900 + len(s["issue_comments"]), "user": s["viewer"]["login"], "body": body})
        s.dirty = True
        event("comment", target="pr", body=body.strip())
        return {"data": {"addComment": {"commentEdge": {"node": {"id": "IC_new", "url": pr_url(s)}}}}}
    if re.search(r"\bmutation\b", q):
        refused("unsupported graphql mutation")
        fail("fake gh: this GraphQL mutation is not available in this evaluation")
    if re.search(r"\bnode\s*\(", q):
        if not thread:
            return {"data": {"node": None}}
        return {"data": {"node": thread_node(s, thread)}}
    data = {"viewer": {"login": s["viewer"]["login"], "id": "U_eval"}}
    if "repository" in q:
        rs = s["repo_settings"]
        data["repository"] = {"pullRequest": gql_pull(s), "defaultBranchRef": {"name": rs.get("default_branch", "main")},
                              "viewerPermission": s.get("permission", "WRITE"), "nameWithOwner": f"{s['owner']}/{s['repo']}",
                              "mergeCommitAllowed": rs.get("allow_merge_commit", True),
                              "squashMergeAllowed": rs.get("allow_squash_merge", True),
                              "rebaseMergeAllowed": rs.get("allow_rebase_merge", True)}
    return {"data": data}


def rest_review(s, rv):
    return {"id": rv["id"], "node_id": f"PRR_{rv['id']}", "user": {"login": rv["user"], "type": "Bot" if rv.get("bot") else "User"},
            "body": rv.get("body", ""), "state": rv["state"], "commit_id": rv["commit_id"],
            "submitted_at": rv.get("submitted_at", NOW), "html_url": pr_url(s) + f"#pullrequestreview-{rv['id']}"}


def rest(s, method, path, fields):
    o, r, n = s["owner"], s["repo"], str(pr(s)["number"])
    base_ = f"repos/{o}/{r}"
    rs = s["repo_settings"]
    p = path.split("?")[0].strip("/")
    reactions = s.d.setdefault("reactions", {})

    if p == "user":
        return {"login": s["viewer"]["login"], "id": s["viewer"].get("id", 9001), "type": "User"}
    if p == "user/emails":
        return [{"email": s["viewer"].get("email", "eval@example.invalid"), "primary": True, "verified": True}]
    if p.startswith("repos/") and not (p == base_ or p.startswith(base_ + "/")):
        http(404, "Not Found")
    if p == base_:
        return {"full_name": f"{o}/{r}", "name": r, "owner": {"login": o}, "private": True,
                "default_branch": rs.get("default_branch", "main"), "allow_merge_commit": rs.get("allow_merge_commit", True),
                "allow_squash_merge": rs.get("allow_squash_merge", True), "allow_rebase_merge": rs.get("allow_rebase_merge", True),
                "permissions": {"admin": False, "maintain": False, "push": s.get("permission", "WRITE") in ("WRITE", "MAINTAIN", "ADMIN"),
                                "triage": True, "pull": True}}
    m = re.fullmatch(re.escape(base_) + r"/(?P<rest>.*)", p)
    rp = m.group("rest")
    if rp == f"pulls/{n}":
        v = view(s, "state,isDraft,headRefOid,baseRefOid,headRefName,baseRefName,mergeable,title,body,author")
        return {"number": int(n), "state": "open" if v["state"] == "OPEN" else "closed", "merged": v["state"] == "MERGED",
                "draft": v["isDraft"], "title": v["title"], "body": v["body"], "user": {"login": v["author"]["login"]},
                "head": {"sha": v["headRefOid"], "ref": v["headRefName"]}, "base": {"sha": v["baseRefOid"], "ref": v["baseRefName"]},
                "mergeable": v["mergeable"] == "MERGEABLE", "html_url": pr_url(s)}
    if rp == f"pulls/{n}/reviews":
        return [rest_review(s, rv) for rv in reviews(s)]
    if rp == f"pulls/{n}/comments" and method == "GET":
        return [{"id": c["databaseId"], "node_id": c["id"], "user": {"login": c["user"], "type": "Bot" if c.get("bot") else "User"},
                 "body": c["body"], "path": t.get("path"), "line": t.get("line"), "created_at": c.get("created_at", NOW),
                 "html_url": pr_url(s) + f"#discussion_r{c['databaseId']}"} for t in s["threads"] for c in t["comments"]]
    m2 = re.fullmatch(rf"pulls/{n}/comments/(\d+)/replies|pulls/{n}/comments", rp)
    if m2 and method == "POST":
        cid = m2.group(1) or str(fields.get("in_reply_to", ""))
        thread = next((t for t in s["threads"] for c in t["comments"] if str(c["databaseId"]) == cid), None)
        if not thread:
            http(404, "Not Found")
        body = fields.get("body", "")
        thread["comments"].append({"id": f"PRRC_new{len(thread['comments'])}", "databaseId": 7000 + len(thread["comments"]),
                                   "user": s["viewer"]["login"], "body": body})
        s.dirty = True
        event("reply", thread=thread["id"], body=body.strip())
        return {"id": 7000, "body": body, "html_url": pr_url(s)}
    m3 = re.fullmatch(rf"(issues/{n}|pulls/comments/(\d+)|issues/comments/(\d+))/reactions(?:/(\d+))?", rp)
    if m3:
        key = m3.group(1)
        lst = reactions.setdefault(key, list((s.get("initial_reactions") or {}).get(key, [])))
        if method == "GET":
            return [x for x in lst if fields.get("content") in (None, x["content"])]
        if method == "POST":
            rx = {"id": 5000 + sum(map(len, reactions.values())), "content": fields.get("content", "eyes"),
                  "user": {"login": s["viewer"]["login"], "type": "User"}, "created_at": NOW}
            lst.append(rx)
            s.dirty = True
            event("reaction", op="add", target=key, content=rx["content"])
            return rx
        if method == "DELETE":
            lst[:] = [x for x in lst if str(x["id"]) != m3.group(4)]
            s.dirty = True
            event("reaction", op="remove", target=key)
            return None
    if rp == f"issues/{n}/comments":
        if method == "POST":
            body = fields.get("body", "")
            s["issue_comments"].append({"id": 900 + len(s["issue_comments"]), "user": s["viewer"]["login"], "body": body})
            s.dirty = True
            event("comment", target="pr", body=body.strip())
            return {"id": 900, "html_url": pr_url(s)}
        return [{"id": c["id"], "user": {"login": c["user"], "type": "Bot" if c.get("bot") else "User"}, "body": c["body"],
                 "created_at": c.get("created_at", NOW)} for c in s["issue_comments"]]
    if rp == f"issues/{n}/labels":
        return [{"name": x} for x in pr(s).get("labels", [])]
    m4 = re.fullmatch(r"commits/([0-9a-f]{7,64})(/check-runs|/status)?", rp)
    if m4:
        sha = git("rev-parse", "--verify", "--quiet", m4.group(1) + "^{commit}", check=False)
        if not sha:
            http(422, f"No commit found for SHA: {m4.group(1)}")
        if m4.group(2) == "/check-runs":
            cs = checks(s, sha)
            return {"total_count": len(cs), "check_runs": [{"id": c["run_id"], "name": c["name"],
                                                            "status": "completed" if c.get("conclusion") else "in_progress",
                                                            "conclusion": (c.get("conclusion") or "").lower() or None} for c in cs]}
        if m4.group(2) == "/status":
            return {"state": "success", "statuses": [], "sha": sha}
        an, ae, cn, ce, date, msg = git("show", "-s", "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1f%cI%x1f%B", sha).split("\x1f")
        who = meta(s, sha)
        files = git("diff-tree", "--no-commit-id", "-r", "--name-only", "-m", "--first-parent", sha).splitlines()
        return {"sha": sha, "author": {"login": who["author"], "type": "User"} if who.get("author") else None,
                "committer": {"login": who["committer"], "type": "User"} if who.get("committer") else None,
                "commit": {"message": msg.strip(), "author": {"name": an, "email": ae, "date": date},
                           "committer": {"name": cn, "email": ce, "date": date}},
                "files": [{"filename": f} for f in files], "html_url": url(s, f"/commit/{sha}")}
    m5 = re.fullmatch(r"rules/branches/(.+)", rp)
    if m5:
        rules = s.get("rulesets")
        if rules == "error":
            http(403, "Resource not accessible by integration")
        return rules or []
    m6 = re.fullmatch(r"branches/([^/]+)/protection(/.*)?", rp)
    if m6:
        bp = s.get("branch_protection")
        if bp == "error":
            http(403, "Resource not accessible by integration")
        if not bp:
            http(404, "Branch not protected")
        if m6.group(2) in ("/required_status_checks", "/required_status_checks/contexts"):
            ctx = bp.get("required_status_checks", [])
            return ctx if m6.group(2).endswith("contexts") else {"strict": False, "contexts": ctx}
        return {"required_status_checks": {"contexts": bp.get("required_status_checks", [])},
                "required_pull_request_reviews": {"required_approving_review_count": bp.get("required_approvals", 1),
                                                  "dismiss_stale_reviews": bp.get("dismiss_stale_reviews", False)}}
    m8 = re.fullmatch(r"branches/([^/]+)", rp)
    if m8:
        if m8.group(1) != pr(s)["baseRefName"]:
            http(404, "Branch not found")
        return {"name": m8.group(1), "protected": bool(s.get("branch_protection")),
                "commit": {"sha": base(s)}}
    m7 = re.fullmatch(r"actions/runs/(\d+)(/jobs|/rerun-failed-jobs|/rerun)?", rp)
    if m7:
        sha, c = find_run(s, m7.group(1))
        if m7.group(2) in ("/rerun-failed-jobs", "/rerun"):
            c["conclusion"] = c.get("rerun_conclusion", c.get("conclusion"))
            s.dirty = True
            event("rerun", run=m7.group(1), check=c["name"])
            return None
        if m7.group(2) == "/jobs":
            return {"total_count": 1, "jobs": [{"id": int(f"{c['run_id']}1"), "name": c["name"], "status": "completed",
                                                "conclusion": (c.get("conclusion") or "").lower()}]}
        return {"id": c["run_id"], "name": c.get("workflow", "CI"), "head_sha": sha, "status": "completed",
                "conclusion": (c.get("conclusion") or "").lower(), "run_attempt": c.get("attempt", 1)}
    if rp == f"pulls/{n}/merge" and method == "PUT":
        return pr_merge(s, {"match-head-commit": fields.get("sha"), fields.get("merge_method", "merge"): True})
    http(404, "Not Found")


def cmd_api(s, pos, opts, fields):
    if not pos:
        fail("accepts 1 arg(s), received 0")
    path = pos[0].replace("{owner}", s["owner"]).replace("{repo}", s["repo"])
    if opts.get("hostname") not in (None, s["host"]):
        fail(f"fake gh: not logged in to {opts['hostname']}")
    if "input" in opts:
        raw = sys.stdin.read() if opts["input"] == "-" else open(opts["input"]).read()
        with contextlib.suppress(ValueError):
            fields = {**json.loads(raw), **fields}
    method = (opts.get("method") or ("POST" if fields and path != "graphql" else "GET")).upper()
    if path == "graphql":
        data = graphql(s, fields)
    else:
        if method == "GET" and fields:
            path += ("&" if "?" in path else "?") + "&".join(f"{k}={v}" for k, v in fields.items())
        for k, v in re.findall(r"[?&]([^=&]+)=([^&]*)", path):
            fields.setdefault(k, v)
        data = rest(s, method, path, fields)
    if data is None:
        raise Exit(0)
    if "slurp" in opts:
        data = [data]
    emit(data, opts)


# ---- main ------------------------------------------------------------------

def main(argv):
    log("gh " + shlex.join(argv))
    if not argv or argv[0] in ("--version", "version"):
        raise Exit(0, "gh version 2.89.0 (2026-03-26)\nhttps://github.com/cli/cli/releases/tag/v2.89.0\n")
    if "--help" in argv or "-h" in argv:
        raise Exit(0, f"(fake gh in an evaluation: no help text for `gh {' '.join(a for a in argv if not a.startswith('-'))}`;"
                      " the real gh 2.89 flags apply)\n")
    pos, opts, fields = parse(argv)
    with open(os.path.join(STATE, "lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        s = State(os.path.join(STATE, "github.json"))
        try:
            if not s.get("github", True):
                fail("fake gh: this evaluation has no GitHub repository (none of the remotes are on github.com)")
            race(s, argv)
            sync_push(s)
            group = pos[0] if pos else ""
            if group == "auth":
                cmd_auth(s, pos[1:], opts)
            if not (s.get("auth") or {}).get("ok", True) and group in ("pr", "api", "run", "repo"):
                fail("HTTP 401: Bad credentials (https://api.github.com/graphql)\nTry authenticating with:  gh auth login")
            if group == "pr" and len(pos) > 1:
                cmd_pr(s, pos[1:], opts, fields)
            if group == "run" and len(pos) > 1:
                cmd_run(s, pos[1:], opts)
            if group == "repo" and len(pos) > 1:
                cmd_repo(s, pos[1:], opts)
            if group == "api":
                cmd_api(s, pos[1:], opts, fields)
            fail(f"fake gh: `gh {' '.join(pos[:2])}` is not available in this evaluation")
        finally:
            s.save()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exit as e:
        sys.stdout.write(e.out)
        sys.stderr.write(e.err)
        sys.exit(e.code)
