"""Shared helpers for the babysit skill's script tests. Everything runs in temp dirs.

The common helpers are shared by every skill's tests and live in
lib/testing.py at the plugin root, loaded by path; this file configures them
for babysit, re-exports what the tests import and adds the fake gh."""
import importlib.util
import json
import os
import pathlib
import shutil
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILL = SCRIPTS.parent

_spec = importlib.util.spec_from_file_location("vstack_testing", SKILL.parent.parent / "lib" / "testing.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
T.configure(SCRIPTS, "babysit-test-")

GIT_ENV = T.GIT_ENV
run, git, rmtree, tmpdir, scratch = T.run, T.git, T.rmtree, T.tmpdir, T.scratch
new_repo, commit_all, pr_repo, sha256_supported = T.new_repo, T.commit_all, T.pr_repo, T.sha256_supported
unrelated_repo, criss_cross_repo = T.unrelated_repo, T.criss_cross_repo


GH_STUB = r'''#!%(python)s
import json, os, sys
d = os.environ["STUB_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
joined = " ".join(args)
with open(os.path.join(d, "rules.json")) as f:
    rules = json.load(f)
for rule in rules:
    if all(m in joined for m in rule["match"]):
        break
else:
    sys.stderr.write("stub: no rule for gh %%s\n" %% joined)
    sys.exit(99)
out = rule.get("stdout", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(rule.get("stderr", ""))
sys.exit(rule.get("code", 0))
'''


class FakeGh:
    """A PATH holding git and a fake gh that answers from rules (first match wins) and logs its argv."""

    def __init__(self, test):
        self.dir = tmpdir(test) / "bin"
        self.dir.mkdir()
        os.symlink(shutil.which("git"), self.dir / "git")
        p = self.dir / "gh"
        p.write_text(GH_STUB % {"python": sys.executable})
        p.chmod(0o755)
        self.set([])

    def set(self, rules):
        (self.dir / "rules.json").write_text(json.dumps(rules))

    def calls(self):
        log = self.dir / "log.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def env(self):
        env = {k: v for k, v in GIT_ENV.items() if not k.startswith(("GH_", "GITHUB_"))}
        return {**env, "PATH": str(self.dir), "STUB_DIR": str(self.dir)}
