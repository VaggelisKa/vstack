"""Shared helpers for the review skill's script tests. Everything runs in temp dirs.

The helpers themselves are shared by every skill's tests and live in
lib/testing.py at the plugin root, loaded by path; this file configures them
for review and re-exports what the tests import."""
import importlib.util
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
SKILL = SCRIPTS.parent

_spec = importlib.util.spec_from_file_location("vstack_testing", SKILL.parent.parent / "lib" / "testing.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
T.configure(SCRIPTS, "review-test-")

GIT_ENV = T.GIT_ENV
run, git, rmtree, tmpdir, scratch = T.run, T.git, T.rmtree, T.tmpdir, T.scratch
new_repo, commit_all, pr_repo, sha256_supported = T.new_repo, T.commit_all, T.pr_repo, T.sha256_supported
unrelated_repo, criss_cross_repo = T.unrelated_repo, T.criss_cross_repo
