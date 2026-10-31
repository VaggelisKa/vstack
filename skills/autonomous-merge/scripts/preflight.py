"""Check, before any work, that autonomous-merge can reach this PR's forge with
enough access to push and merge.

Usage: python3 -I preflight.py <pr-number-or-url> [--allow-fork]

The host comes from the URL, or for a bare number from the repo's git remote
(origin, else the first remote). github.com (and *.ghe.com) needs `gh` on PATH,
`gh auth status --hostname <host>` passing, a token that can read the repo and
the PR, and push permission (`permissions.push` from `gh api repos/{o}/{r}`, or
`viewerPermission` WRITE, MAINTAIN or ADMIN), and a PR whose head isn't a
fork's: `isCrossRepository` true, missing or not a boolean hands back
`escalated`, since the run would execute and push the fork's code. Only
--allow-fork lets a fork through; pass it only when the user's own words in
this request say the fork's code may be run. bitbucket.org needs `curl` and
BITBUCKET_TOKEN (sent as a Bearer token) or BITBUCKET_USERNAME plus
BITBUCKET_APP_PASSWORD, and checks the repo and PR can be read with them;
autonomous-merge never merges there (the gate hands a passing PR back as
merge_unsupported_host), so merge permission isn't needed. Any other host is
handed back as host_unsupported.

Never prompts and never prints a token. Prints one JSON object on stdout:
{"outcome": "ok", ...} (exit 0) or a handback (exit 5, see
references/handbacks.md). Exit 2 usage (also -h), 4 internal error.
"""
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "vstack_am_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c)


def github(t, allow_fork=False):
    _, pr = c.github_access(t)
    if pr.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(pr.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    fork = pr.get("isCrossRepository")
    if fork is not False and not allow_fork:
        unread = "" if fork is True else " (isCrossRepository couldn't be read, so treating it as one)"
        raise c.Handback("escalated", "fork PR: the head is another repository's code; autonomous-merge doesn't run "
                                      f"or push it{unread}",
                         "Review the fork's code yourself, or rerun saying explicitly that the fork's code may be run.")
    return {"fork": fork is not False, "head_sha": pr.get("headRefOid"), "base_sha": pr.get("baseRefOid"),
            "url": pr.get("url"), "head_ref": pr.get("headRefName"), "base_ref": pr.get("baseRefName"),
            "draft": pr.get("isDraft"), "merge_permission": "yes"}


def bitbucket(t):
    _, pr = c.bitbucket_access(t)
    if pr.get("state") != "OPEN":
        raise c.Handback("not_open", f"PR {t['pr']} is {str(pr.get('state')).lower()}",
                         "Nothing to merge; reopen the PR if that's wrong.")
    src, dst = pr.get("source") or {}, pr.get("destination") or {}
    return {"head_ref": (src.get("branch") or {}).get("name"), "base_ref": (dst.get("branch") or {}).get("name"),
            "url": ((pr.get("links") or {}).get("html") or {}).get("href"), "draft": pr.get("draft") is True,
            "merge_permission": "not needed: you merge Bitbucket PRs yourself"}


def main():
    args = sys.argv[1:]
    allow_fork = "--allow-fork" in args
    args = [a for a in args if a != "--allow-fork"]
    if len(args) != 1:
        raise c.Usage("usage: preflight.py <pr-number-or-url> [--allow-fork]")
    t = c.parse_target(args[0])
    info = github(t, allow_fork) if t["host"] == "github" else bitbucket(t)
    c.emit({"outcome": "ok", **t, **info})
    return c.OK


if __name__ == "__main__":
    c.main_wrapper("preflight", main)
