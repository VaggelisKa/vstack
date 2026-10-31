"""Shared code for autonomous-merge's scripts: the handback format and exit
codes, finding the forge from a PR URL or the git remote, the live GitHub and
Bitbucket Cloud access checks, and redaction. Loaded by the other scripts with
importlib (they run under python3 -I, so the script's folder isn't on sys.path).

Nothing here prompts for or prints a credential: gh keeps its own token, and a
Bitbucket token is read from the environment and handed to curl on stdin as a
config file, never in argv. Bitbucket is only ever read (GET): autonomous-merge
never merges there.
"""
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

OK, USAGE, INTERNAL, HANDBACK = 0, 2, 4, 5

REASONS = (
    "no_cli", "auth_missing", "auth_expired", "insufficient_permission", "host_unsupported",
    "branch_protection", "checks_failing", "unresolved_threads", "changes_requested", "not_approved",
    "conflicts", "draft", "hold_label", "not_open", "needs_review", "escalated", "cap_reached", "oscillation",
    "no_progress", "security_blocker", "ci_unreproducible", "conflict_judgement", "design_change", "scope_change",
    "merge_unsupported_host", "merge_failed", "not_settled", "claimed_items", "conflicting_fixer", "stale_claim",
    "unknown",
)

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_READER = os.path.join(HERE, "..", "..", "review", "scripts", "read-result.py")
COORDINATION = os.path.join(HERE, "..", "..", "babysit", "scripts", "coordination.py")

NAME = re.compile(r"[A-Za-z0-9_.-]+")
PR_NUM = re.compile(r"#?([1-9][0-9]{0,9})")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
COLOR_OFF = ("FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "GH_FORCE_TTY")
MAX_PR = 2**31 - 1
BB_API = "https://api.bitbucket.org/2.0"
TOKEN_ENV = ("BITBUCKET_TOKEN", "BITBUCKET_APP_PASSWORD", "GH_TOKEN", "GITHUB_TOKEN",
             "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
TOKEN_PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*)\S+(\s+\S+)?"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "[redacted]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"), "[redacted]"),
    (re.compile(r"\bAT(BB|CTT|ATT)[A-Za-z0-9_=-]{8,}"), "[redacted]"),
    (re.compile(r"(://)[^/@\s]+@"), r"\1[redacted]@"),
]


class Usage(Exception):
    pass


class Handback(Exception):
    def __init__(self, reason, detail, action):
        assert reason in REASONS, reason
        super().__init__(detail)
        self.reason, self.detail, self.action = reason, detail, action


def redact(text):
    text = str(text)
    for name in TOKEN_ENV:
        v = os.environ.get(name)
        if v and len(v) >= 4:
            text = text.replace(v, "[redacted]")
    for pat, sub in TOKEN_PATTERNS:
        text = pat.sub(sub, text)
    return text


def one_line(text, n=400):
    text = " ".join(redact(text).split())
    return text if len(text) <= n else text[:n] + "..."


def emit(obj):
    print(json.dumps(obj, sort_keys=False))


def handback(prog, h):
    emit({"outcome": "handback", "reason": h.reason, "detail": one_line(h.detail, 1000),
          "action": one_line(h.action, 600)})
    print(f"{prog}: handed back ({h.reason}): {one_line(h.detail)}. Next: {one_line(h.action)}", file=sys.stderr)
    return HANDBACK


def main_wrapper(prog, main):
    try:
        code = main()
    except Handback as h:
        code = handback(prog, h)
    except Usage as e:
        print(f"{prog}: {one_line(e)}", file=sys.stderr)
        code = USAGE
    except Exception as e:
        emit({"outcome": "handback", "reason": "unknown", "detail": f"{prog}: internal error: {type(e).__name__}",
              "action": "Rerun; if it happens again, report it with the stderr line."})
        print(f"{prog}: internal error: {type(e).__name__}", file=sys.stderr)
        code = INTERNAL
    sys.exit(code)


def run(argv, stdin=None, env=None, timeout=120):
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(argv, **feed, capture_output=True, text=True, timeout=timeout,
                              env=env or os.environ)
    except subprocess.TimeoutExpired:
        raise Handback("unknown", f"{argv[0]} timed out after {timeout}s", "Check the network and rerun.")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(hostname):
    h = hostname.lower()
    if h == "github.com" or h.endswith(".ghe.com"):
        return "github"
    if h == "bitbucket.org":
        return "bitbucket"
    return None


def unsupported(hostname):
    return Handback("host_unsupported", f"host not supported: {hostname}",
                    "Merge this PR yourself; autonomous-merge supports github.com and bitbucket.org only.")


def name_ok(*names):
    return all(NAME.fullmatch(n) and n not in (".", "..") for n in names)


def parse_pr_url(arg):
    u = urllib.parse.urlsplit(arg)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise Usage("the PR must be a number or an https URL")
    host = u.hostname.lower()
    parts = [p for p in u.path.split("/") if p]
    kind = classify(host)
    if kind is None:
        raise unsupported(host)
    want = "pull" if kind == "github" else "pull-requests"
    if len(parts) < 4 or parts[2] != want or not PR_NUM.fullmatch(parts[3]) or not name_ok(parts[0], parts[1]):
        raise Usage(f"not a {kind} pull request URL")
    return {"host": kind, "hostname": host, "owner": parts[0], "repo": parts[1].removesuffix(".git"),
            "pr": int(parts[3])}


def parse_remote(url):
    """(hostname, owner, repo) from an https, ssh:// or scp-style git remote."""
    m = re.fullmatch(r"[^@/\s]+@([^:/\s]+):/?([^/\s]+)/([^/\s]+?)(\.git)?/?", url)
    if m:
        return m.group(1).lower(), m.group(2), m.group(3)
    u = urllib.parse.urlsplit(url)
    parts = [p for p in u.path.split("/") if p]
    if u.scheme in ("https", "http", "ssh", "git") and u.hostname and len(parts) >= 2:
        return u.hostname.lower(), parts[-2], parts[-1].removesuffix(".git")
    return None


def remote_url():
    for args in (["remote", "get-url", "origin"], ["remote"]):
        r = run(["git", *args])
        if r.returncode != 0 or not r.stdout.strip():
            continue
        if args[-1] == "origin":
            return r.stdout.strip()
        first = r.stdout.split()[0]
        r = run(["git", "remote", "get-url", "--", first])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def parse_target(arg):
    if not isinstance(arg, str) or not arg or arg.startswith("-"):
        raise Usage("give a PR number or URL (not starting with -)")
    m = PR_NUM.fullmatch(arg)
    if not m:
        return parse_pr_url(arg)
    pr = int(m.group(1))
    if pr > MAX_PR:
        raise Usage("PR number out of range")
    url = remote_url()
    parsed = url and parse_remote(url)
    if not parsed:
        raise Handback("unknown", "can't tell the host: no usable git remote here",
                       "Pass the full PR URL instead of a number.")
    hostname, owner, repo = parsed
    kind = classify(hostname)
    if kind is None:
        raise unsupported(hostname)
    if not name_ok(owner, repo):
        raise Usage("the git remote's owner or repo name has unexpected characters")
    return {"host": kind, "hostname": hostname, "owner": owner, "repo": repo, "pr": pr}


def gh_env():
    """The environment for gh. Color-forcing variables are dropped: with them set, gh
    colors its JSON even when NO_COLOR is set and stdout is a pipe."""
    env = {k: v for k, v in os.environ.items() if k not in COLOR_OFF}
    env.update({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1",
                "GH_PAGER": "cat", "PAGER": "cat", "GH_SPINNER_DISABLED": "1", "LC_ALL": "C"})
    return env


def loads(text):
    """JSON from gh. ANSI color, and any note printed before the value, is ignored."""
    text = ANSI.sub("", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        return value
    raise ValueError("not JSON")


DEADLINE = None  # a time.monotonic() every gh call must end by; the gate sets it while polling a merge


def gh(*args, timeout=120):
    if DEADLINE is not None:
        timeout = max(1, min(timeout, math.ceil(DEADLINE - time.monotonic())))
    return run(["gh", *args], env=gh_env(), timeout=timeout)


def gh_json(r, what):
    try:
        return loads(r.stdout)
    except ValueError:
        raise Handback("unknown", f"{what}: gh printed something that isn't JSON",
                       "Check `gh --version` (2.42 or newer) and rerun.")


def gh_repo(t):
    return f"{t['hostname']}/{t['owner']}/{t['repo']}"


def refresh_action(t):
    return (f"Run `gh auth refresh --hostname {t['hostname']} -s repo` yourself (or log in as an account "
            f"with write access to {t['owner']}/{t['repo']}), then rerun.")


def gh_api_error(r, t, what):
    err = r.stderr + r.stdout
    if "HTTP 401" in err or "Bad credentials" in err:
        return Handback("auth_expired", f"{what}: GitHub rejected the token (401)",
                        f"Run `gh auth login --hostname {t['hostname']}` yourself, then rerun.")
    if any(s in err for s in ("HTTP 403", "HTTP 404", "Not Found", "Resource not accessible",
                              "Could not resolve to")):
        return Handback("insufficient_permission", f"{what}: {one_line(err, 300)}", refresh_action(t))
    return Handback("unknown", f"{what}: gh exited {r.returncode}: {one_line(err, 300)}",
                    "Check the error above and rerun.")


def github_access(t):
    """Checks gh, its login and the token's access. Returns the PR's view JSON."""
    if not shutil.which("gh"):
        raise Handback("no_cli", "gh is not on PATH",
                       "Install the GitHub CLI (https://cli.github.com), run `gh auth login` yourself, then rerun.")
    r = gh("auth", "status", "--hostname", t["hostname"])
    if r.returncode != 0:
        text = (r.stdout + r.stderr).lower()
        if any(s in text for s in ("invalid", "expired", "failed to log in", "bad credentials", "revoked")):
            raise Handback("auth_expired", f"gh's login for {t['hostname']} is no longer valid",
                           f"Run `gh auth login --hostname {t['hostname']}` yourself, then rerun.")
        raise Handback("auth_missing", f"gh is not logged in to {t['hostname']}",
                       f"Run `gh auth login --hostname {t['hostname']}` yourself, then rerun.")
    r = gh("api", "--hostname", t["hostname"], f"repos/{t['owner']}/{t['repo']}")
    if r.returncode != 0:
        raise gh_api_error(r, t, f"reading {t['owner']}/{t['repo']}")
    repo = gh_json(r, "gh api repos")
    perms = repo.get("permissions") if isinstance(repo, dict) else None
    if isinstance(perms, dict) and "push" in perms:
        can_push = perms["push"] is True
    else:
        r = gh("repo", "view", gh_repo(t), "--json", "viewerPermission")
        if r.returncode != 0:
            raise gh_api_error(r, t, "reading viewerPermission")
        can_push = gh_json(r, "gh repo view").get("viewerPermission") in ("ADMIN", "MAINTAIN", "WRITE")
    if not can_push:
        raise Handback("insufficient_permission",
                       f"the token can read {t['owner']}/{t['repo']} but has no push or merge permission",
                       refresh_action(t))
    r = gh("pr", "view", str(t["pr"]), "-R", gh_repo(t), "--json",
           "number,url,state,isDraft,headRefName,baseRefName,headRefOid,baseRefOid,isCrossRepository")
    if r.returncode != 0:
        raise gh_api_error(r, t, f"reading PR {t['pr']}")
    return repo, gh_json(r, "gh pr view")


def bb_auth():
    token = os.environ.get("BITBUCKET_TOKEN")
    user, pw = os.environ.get("BITBUCKET_USERNAME"), os.environ.get("BITBUCKET_APP_PASSWORD")
    for name, value in (("BITBUCKET_TOKEN", token), ("BITBUCKET_USERNAME", user), ("BITBUCKET_APP_PASSWORD", pw)):
        if value and ("\r" in value or "\n" in value):
            raise Handback("auth_missing", f"{name} contains a line break",
                           f"Set {name} again without the line break, in your environment yourself, then rerun.")
    if token:
        return 'header = "Authorization: Bearer %s"\n' % token.replace("\\", "\\\\").replace('"', '\\"')
    if user and pw:
        cred = f"{user}:{pw}".replace("\\", "\\\\").replace('"', '\\"')
        return f'user = "{cred}"\n'
    raise Handback("auth_missing", "no Bitbucket credentials in the environment",
                   "Set BITBUCKET_TOKEN (an access token with pull request and repository read scope), or "
                   "BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD, in your environment yourself, then rerun.")


def bb_action():
    return "Give the token pull request and repository read access yourself, then rerun."


def bb_request(path):
    """(status, parsed JSON or None) for a GET. path is under /2.0 or a full api.bitbucket.org URL."""
    url = path if path.startswith(BB_API + "/") else f"{BB_API}/{path}"
    if not shutil.which("curl"):
        raise Handback("no_cli", "curl is not on PATH", "Install curl, then rerun.")
    config = bb_auth()
    with tempfile.TemporaryDirectory(prefix="am-bb-") as d:
        out = os.path.join(d, "body")
        argv = ["curl", "-q", "-sS", "--max-time", "60", "--config", "-", "-o", out, "-w", "%{http_code}",
                "-H", "Accept: application/json"]
        r = run([*argv, url], stdin=config)
        if r.returncode != 0:
            raise Handback("unknown", f"curl exited {r.returncode}: {one_line(r.stderr, 300)}",
                           "Check the network and rerun.")
        try:
            status = int(r.stdout.strip()[-3:])
        except ValueError:
            raise Handback("unknown", "curl gave no HTTP status", "Rerun.")
        try:
            with open(out, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
    try:
        return status, json.loads(text) if text.strip() else None
    except ValueError:
        return status, None


def bb_error(data):
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return one_line(data["error"].get("message") or "", 300)
    return ""


def bb_get(path, what):
    status, data = bb_request(path)
    if status == 200 and isinstance(data, dict):
        return data
    if status == 401:
        raise Handback("auth_expired", f"{what}: Bitbucket rejected the credentials (401)",
                       "Create a new token or app password, set it in your environment yourself, then rerun.")
    if status in (403, 404):
        raise Handback("insufficient_permission", f"{what}: HTTP {status} {bb_error(data)}".strip(), bb_action())
    raise Handback("unknown", f"{what}: HTTP {status} {bb_error(data)}".strip(), "Check Bitbucket's status and rerun.")


def bb_pages(path, what, limit=20):
    values, url = [], path
    for _ in range(limit):
        page = bb_get(url, what)
        values += page.get("values") or []
        url = page.get("next")
        if not url:
            return values
        if not url.startswith(BB_API + "/"):
            raise Handback("unknown", f"{what}: unexpected next-page URL", "Rerun.")
    raise Handback("unknown", f"{what}: more than {limit} pages", "Check the PR by hand.")


def bb_base(t):
    return f"repositories/{t['owner']}/{t['repo']}"


def bitbucket_access(t):
    """Checks curl, the credentials and PR read access. Returns (repo, pr) JSON."""
    if not shutil.which("curl"):
        raise Handback("no_cli", "curl is not on PATH", "Install curl, then rerun.")
    bb_auth()
    repo = bb_get(bb_base(t), f"reading {t['owner']}/{t['repo']}")
    pr = bb_get(f"{bb_base(t)}/pullrequests/{t['pr']}", f"reading PR {t['pr']}")
    return repo, pr


def review_ok(pr, head, base):
    """None if review.json exists for this head and base with merge_ok, else why not."""
    if not os.path.exists(REVIEW_READER):
        return "the review skill's read-result.py isn't installed next to this skill"
    try:
        reader = load_module("vstack_review_read_result", REVIEW_READER)
    except Exception as e:
        return f"can't load review's read-result.py: {type(e).__name__}"
    try:
        r = reader.read_result(pr, head, base)
    except reader.Stale:
        return "review.json is for another head or base"
    except reader.Unusable as e:
        return f"no usable review.json: {one_line(e, 200)}"
    except OSError as e:
        return f"can't read review.json: {e.strerror or type(e).__name__}"
    if r.get("merge_ok") is not True:
        return f"the last review on this head isn't a clean Ship (status {r.get('status')})"
    return None
