"""Builds one eval run's workspace from a case's fixture.json. Run by the case's scaffold.sh, which
`claude plugin eval --scaffold` starts with bash in the run's working directory, with HOME set to the
run's sandbox home (shared with the agent).

It makes the working directory a git repo shaped like a checkout of a GitHub PR:

- the base branch with the fixture's `files`, and the PR branch with its `commits` on top,
- `origin` set to git@<host>:<owner>/<repo>.git, served by a fake ssh from the local bare repo
  .vstack-eval/origin.git, so fetch and push work offline,
- the fixture's `uncommitted` edits left in the working tree, on `checkout` (head or base),
- $HOME/bin first on PATH (through the shell rc files the agent's shell reads), holding the fake gh,
  the fake ssh, links to real git and python3, and the fixture's `bin` scripts,
- .vstack-eval/github.json: the fixture's `github` section with @HEAD, @HEAD~N and @BASE replaced by
  the commit SHAs, plus .vstack-eval/gh-calls.log, which the fake gh appends every call to and the
  fake ssh a `# push` line per push,
- `github.race`, if set: a commit by someone else, kept at refs/eval/race in the bare repo, that the
  fake gh moves onto the PR branch after `after_views` reads of the PR (a push mid-run).

.vstack-eval/ is in .git/info/exclude, so it never shows in git status.

Usage: python3 scaffold.py <fixture.json>
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HARNESS = pathlib.Path(__file__).resolve().parent
ROOT = HARNESS.parent.parent
WHO = {"alice": ("Alice Example", "alice@example.com"), "bob": ("Bob Example", "bob@example.com")}
DATE = "2026-10-28T09:00:00Z"


def run(*args, cwd=None, env=None, input=None):
    r = subprocess.run(args, cwd=cwd, env=env, input=input, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"scaffold: {' '.join(map(str, args))} failed: {r.stderr}")
    return r.stdout.strip()


def real(name):
    """A real binary for name, not the macOS /usr/bin stub that needs xcrun (which the sandbox blocks)."""
    found = shutil.which(name)
    if not found:
        sys.exit(f"scaffold: {name} is not on PATH")
    if sys.platform == "darwin" and found.startswith("/usr/bin/"):
        r = subprocess.run(["xcrun", "--find", name], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return os.path.realpath(found)


def usable_python(path):
    """True for a Python 3.10+ that the eval sandbox can run: not under the home directory, which the
    sandbox hides, unless it is inside this repo (the plugin under test, which it can read)."""
    path = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if path.startswith(home + os.sep) and not path.startswith(str(ROOT) + os.sep):
        return False
    r = subprocess.run([path, "-c", "import sys; sys.exit(sys.version_info < (3, 10))"], capture_output=True)
    return r.returncode == 0


def find_python():
    """The Python the skills' scripts run on in the sandbox: $VSTACK_EVAL_PYTHON, a python3.1x on PATH,
    or one that evals/run.sh installed into evals/.python/."""
    if os.environ.get("VSTACK_EVAL_PYTHON"):
        return os.environ["VSTACK_EVAL_PYTHON"]
    names = [f"python3.{m}" for m in range(20, 9, -1)] + ["python3"]
    found = [shutil.which(n) for n in names]
    local = sorted(HARNESS.parent.glob(".python/*/bin/python3"), reverse=True)
    for cand in [f for f in found if f] + [str(p) for p in local]:
        if sys.platform == "darwin" and cand.startswith("/usr/bin/"):
            cand = real("python3")
        if usable_python(cand):
            return os.path.realpath(cand)
    return None


def content(v):
    """A file's text: a string, or {"from": <path from the repo root>, "until"/"after": <marker line>}."""
    if isinstance(v, str):
        return v
    text = (ROOT / v["from"]).read_text()
    lines = text.splitlines(keepends=True)
    if "until" in v:
        lines = lines[:next(i for i, l in enumerate(lines) if v["until"] in l)]
    return "".join(lines)


def write_files(repo, files):
    for path, v in files.items():
        p = repo / path
        if v is None:
            if p.exists():
                p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content(v))
        if path.endswith(".sh"):
            p.chmod(0o755)


def commit(repo, message, author, env):
    name, email = WHO.get(author, (author, f"{author}@example.com"))
    e = {**env, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
         "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE}
    run("git", "add", "-A", cwd=repo, env=e)
    run("git", "commit", "-q", "--allow-empty", "-m", message, cwd=repo, env=e)
    return run("git", "rev-parse", "HEAD", cwd=repo, env=e)


def resolve(v, shas):
    if isinstance(v, dict):
        return {k: resolve(x, shas) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve(x, shas) for x in v]
    if isinstance(v, str):
        return re.sub(r"@(HEAD(?:~\d+)?|BASE)\b", lambda m: shas[m.group(1)], v)
    return v


def install_bin(home, state):
    b = home / "bin"
    b.mkdir(exist_ok=True)
    py = find_python()
    if not py:
        sys.exit("scaffold: no Python 3.10+ the eval sandbox can run (it hides your home directory). "
                 "Run evals/run.sh, which installs one into evals/.python with uv, or set VSTACK_EVAL_PYTHON.")
    local = HARNESS.parent / ".python"
    if py.startswith(str(local) + os.sep):
        # The sandbox reads the repo but won't load libraries from it: run a copy from the run's home
        # (a clone on APFS, so it's instant).
        src = local / pathlib.Path(py).relative_to(local).parts[0]
        dst = home / ".python" / src.name
        dst.parent.mkdir(exist_ok=True)
        run(*(["cp", "-cR"] if sys.platform == "darwin" else ["cp", "-R"]), str(src), str(dst))
        py = str(dst / pathlib.Path(py).relative_to(src))
    # Wrappers, not symlinks: git finds its helpers relative to the path it was started from.
    for name, target in (("git", real("git")), ("python3", py), ("python", py)):
        (b / name).write_text(f"#!/bin/sh\nexec '{target}' \"$@\"\n")
        (b / name).chmod(0o755)
    gh = (HARNESS / "fakegh.py").read_text().replace("@STATE@", str(state)).replace("@GIT@", real("git"))
    (b / "gh").write_text(f"#!{py} -I\n" + gh)
    (b / "gh").chmod(0o755)
    # git runs `ssh <host> "git-upload-pack '<owner>/<repo>.git'"`: serve every repo from the bare one.
    # A push is logged only when it changed a ref, so dry runs and rejected pushes don't count.
    git_ = real("git")
    (b / "ssh").write_text(f"""#!/bin/sh
for last; do :; done
cmd=${{last%% *}}
origin='{state}/origin.git'
[ "$cmd" = git-receive-pack ] || exec '{git_}' "${{cmd#git-}}" "$origin"
before=$('{git_}' --git-dir "$origin" for-each-ref refs/heads)
'{git_}' receive-pack "$origin"
rc=$?
[ "$before" = "$('{git_}' --git-dir "$origin" for-each-ref refs/heads)" ] || echo '# push' >> '{state}/gh-calls.log'
exit $rc
""")
    (b / "ssh").chmod(0o755)
    # The agent's shell can keep `git` pinned to /usr/bin/git, the macOS stub that needs xcrun (blocked in
    # the sandbox); a function wins over that.
    line = 'export PATH="$HOME/bin:$PATH"\ngit() { "$HOME/bin/git" "$@"; }\n'
    for rc in (".zshenv", ".zshrc", ".bashrc", ".bash_profile", ".profile"):
        p = home / rc
        if line not in (p.read_text() if p.exists() else ""):
            with open(p, "a") as f:
                f.write(line)


def main(fixture_path):
    fx = json.loads(pathlib.Path(fixture_path).read_text())
    repo = pathlib.Path.cwd()
    home = pathlib.Path(os.environ["HOME"])
    state = repo / ".vstack-eval"
    state.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    r = fx.get("repo", {})
    gh = fx.get("github")
    base_name = (gh or {}).get("pr", {}).get("baseRefName", r.get("base", "main"))
    head_name = (gh or {}).get("pr", {}).get("headRefName", r.get("head", "feature"))

    run("git", "init", "-q", "-b", base_name, cwd=repo, env=env)
    with open(repo / ".git/info/exclude", "a") as f:
        f.write(".vstack-eval/\n")
    write_files(repo, r.get("files", {}))
    shas = {"BASE": commit(repo, r.get("base_message", "Initial commit"), r.get("base_author", "alice"), env)}
    logins = {shas["BASE"]: r.get("base_author", "alice")}
    pr_commits = r.get("commits", [])
    if pr_commits:
        run("git", "checkout", "-q", "-b", head_name, cwd=repo, env=env)
        heads = []
        for c in pr_commits:
            write_files(repo, c.get("files", {}))
            heads.append(commit(repo, c["message"], c.get("author", "alice"), env))
            logins[heads[-1]] = c.get("author", "alice")
        for i, sha in enumerate(reversed(heads)):
            shas["HEAD" if i == 0 else f"HEAD~{i}"] = sha
    else:
        shas["HEAD"] = shas["BASE"]
    race = (gh or {}).get("race")
    if race:  # a commit someone else pushes mid-run: made now, kept off the branch until the fake gh moves it there
        write_files(repo, race["commit"].get("files", {}))
        race_sha = commit(repo, race["commit"]["message"], race["commit"].get("author", "alice"), env)
        logins[race_sha] = race["commit"].get("author", "alice")
        run("git", "reset", "-q", "--hard", shas["HEAD"], cwd=repo, env=env)

    install_bin(home, state)
    for name, script in fx.get("bin", {}).items():  # extra commands the case needs on PATH
        (home / "bin" / name).write_text(script)
        (home / "bin" / name).chmod(0o755)
    if gh:
        run("git", "init", "-q", "--bare", str(state / "origin.git"), env=env)
        run("git", "push", "-q", str(state / "origin.git"), f"{base_name}:{base_name}",
            *([f"{head_name}:{head_name}"] if pr_commits else []),
            *([f"{race_sha}:refs/eval/race"] if race else []), cwd=repo, env=env)
        run("git", "remote", "add", "origin", f"git@{gh.get('host', 'github.com')}:{gh['owner']}/{gh['repo']}.git",
            cwd=repo, env=env)
        run("git", "fetch", "-q", "origin", cwd=repo, env={**env, "PATH": f"{home / 'bin'}:{env['PATH']}"})
        if pr_commits:
            run("git", "branch", "-q", f"--set-upstream-to=origin/{head_name}", head_name, cwd=repo, env=env)
        run("git", "remote", "set-head", "origin", base_name, cwd=repo, env=env)
    if r.get("checkout", "head") == "base" and pr_commits:
        run("git", "checkout", "-q", base_name, cwd=repo, env=env)
    write_files(repo, r.get("uncommitted", {}))

    if gh:
        g = resolve(gh, shas)
        g.setdefault("host", "github.com")
        g.setdefault("viewer", {"login": "vstack-bot", "id": 9001})
        g.setdefault("repo_settings", {})
        g.setdefault("reviews", [])
        g.setdefault("threads", [])
        g.setdefault("issue_comments", [])
        g["initial_head"] = shas["HEAD"]
        checks = g.pop("checks", [])
        g["checks"] = checks if isinstance(checks, dict) else {shas["HEAD"]: checks}
        g["commit_logins"] = {sha: {"author": who, "committer": who} for sha, who in logins.items()}
        if race:
            g["race"] = {"after_views": race.get("after_views", 2), "sha": race_sha}
    else:
        g = {"github": False}
    (state / "github.json").write_text(json.dumps(g, indent=1))
    (state / "gh-calls.log").write_text("")


if __name__ == "__main__":
    if sys.argv[1:] == ["--find-python"]:
        py = find_python()
        print(py or "")
        sys.exit(0 if py else 1)
    main(sys.argv[1])
