"""Cross-checks never run code from the repo: a committed node_modules/typescript
symlink, repo directories on PATH, NODE_OPTIONS and repo headers are all ignored,
RC_TS_ROOT inside the repo is refused, and RC_TS_ROOT outside it is used.

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import GIT_ENV, SCRIPTS, commit_all, decide, git, new_repo, tmpdir

sys.path.insert(0, str(SCRIPTS))
import comments  # noqa: E402 - must follow the sys.path line

NODE = shutil.which("node")
EVIL_TS = """require('fs').writeFileSync(%s, 'typescript from the repo ran');
module.exports = {transpileModule() { return {outputText: '', diagnostics: []}; },
                  ScriptTarget: {}, JsxEmit: {}, ModuleKind: {}};
"""
SHIM = "#!/bin/sh\necho \"$0 ran\" >> %s\nexit 0\n"
NO_TS = "typescript: skipped (typescript isn't available outside the repo; set RC_TS_ROOT to a trusted install)"


def hostile_repo(marker):
    """A repo whose node_modules/typescript is a committed symlink to repo code that
    writes marker when loaded, plus bin/ shims and a NODE_OPTIONS hook that do the same."""
    repo = new_repo()
    (repo / "evil" / "lib").mkdir(parents=True)
    (repo / "evil" / "package.json").write_text(json.dumps({"name": "typescript", "main": "lib/typescript.js"}))
    (repo / "evil" / "lib" / "typescript.js").write_text(EVIL_TS % json.dumps(str(marker)))
    (repo / "node_modules").mkdir()
    os.symlink("../evil", repo / "node_modules" / "typescript")
    (repo / "bin").mkdir()
    for name in ("node", "npm", "gcc", "cc", "clang", "gofmt"):
        (repo / "bin" / name).write_text(SHIM % json.dumps(str(marker)))
        (repo / "bin" / name).chmod(0o755)
    (repo / "hook.js").write_text(EVIL_TS % json.dumps(str(marker)))
    (repo / "a.ts").write_text("export const a = 1;\n")
    (repo / "b.js").write_text("f(a);\n")
    (repo / "c.c").write_text("int x;\n")
    (repo / "d.go").write_text("package d\n")
    commit_all(repo, "base")
    return repo


def clean_env(**extra):
    """The test run's environment minus RC_TS_ROOT, with an empty global npm root."""
    prefix = tmpdir()
    env = {k: v for k, v in GIT_ENV.items() if k != "RC_TS_ROOT"}
    env.update(NPM_CONFIG_PREFIX=str(prefix), npm_config_prefix=str(prefix), **extra)
    return env


def cli(*args, cwd, env):
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "comments.py"), *map(str, args)],
                          capture_output=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL, timeout=120)


def edit_and_verify(repo, env):
    """Add a comment to every file, list, delete them all, verify; returns verify's result."""
    for name, line in (("a.ts", "export const b = 2; // two\n"), ("b.js", "g(b); // two\n"),
                       ("c.c", "int y; // two\n"), ("d.go", "var y = 2 // two\n")):
        with open(repo / name, "a") as f:
            f.write(line)
    out = tmpdir() / "comments.json"
    r = cli("list", "--out", out, cwd=repo, env=env)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    r = cli("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}), "--list", out,
            cwd=repo, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return cli("verify", "--list", out, cwd=repo, env=env)


class RepoCodeNeverRuns(unittest.TestCase):
    def setUp(self):
        self.marker = tmpdir() / "marker"
        self.repo = hostile_repo(self.marker)

    def assert_no_repo_code(self, r):
        self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")
        self.assertNotIn(b"typescript: ok", r.stdout)

    def test_committed_typescript_symlink_is_never_loaded(self):
        self.assertEqual(git(self.repo, "ls-files", "-s", "node_modules/typescript").decode()[:6], "120000")
        r = edit_and_verify(self.repo, clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_repo_code(r)
        if NODE:
            self.assertIn(NO_TS.encode(), r.stdout)

    def test_repo_bin_on_path_and_node_options_are_ignored(self):
        env = clean_env(PATH=f"{self.repo / 'bin'}{os.pathsep}node_modules/.bin{os.pathsep}{os.environ['PATH']}",
                        NODE_OPTIONS=f"--require {self.repo / 'hook.js'}", CPATH=str(self.repo))
        r = edit_and_verify(self.repo, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_repo_code(r)

    def test_rc_ts_root_inside_the_repo_is_refused(self):
        r = edit_and_verify(self.repo, clean_env(RC_TS_ROOT=str(self.repo)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_repo_code(r)
        if NODE:
            self.assertIn(b"typescript: skipped (RC_TS_ROOT is inside the repo)", r.stdout)

    def test_rc_ts_root_that_resolves_into_the_repo_is_refused(self):
        outside = tmpdir()
        (outside / "node_modules").mkdir()
        os.symlink(self.repo / "evil", outside / "node_modules" / "typescript")
        r = edit_and_verify(self.repo, clean_env(RC_TS_ROOT=str(outside)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_repo_code(r)
        if NODE:
            self.assertIn(b"typescript: skipped (RC_TS_ROOT's typescript resolves into the repo)", r.stdout)

    def test_find_typescript_never_returns_a_path_in_the_repo(self):
        if not NODE:
            self.skipTest("node isn't on PATH")
        roots = [str(self.repo)]
        env = comments.tool_env(roots)
        old = os.environ.pop("RC_TS_ROOT", None)
        try:
            path, why = comments.find_typescript(NODE, roots, str(tmpdir()), env)
        finally:
            if old is not None:
                os.environ["RC_TS_ROOT"] = old
        self.assertFalse(path and comments.inside(path, self.repo), path)
        self.assertFalse(self.marker.exists())

    @unittest.skipUnless(os.environ.get("RC_TS_ROOT") and NODE,
                         "set RC_TS_ROOT to a directory whose node_modules has typescript")
    def test_rc_ts_root_outside_the_repo_is_used(self):
        r = edit_and_verify(self.repo, clean_env(RC_TS_ROOT=os.environ["RC_TS_ROOT"]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn(b"cross-check typescript: ok", r.stdout)


class ToolEnvironment(unittest.TestCase):
    def test_scrubbed_variables_and_path(self):
        repo = tmpdir()
        saved = dict(os.environ)
        try:
            os.environ.update(NODE_OPTIONS="--require x", NODE_PATH="/x", LD_PRELOAD="/x.so", GCC_EXEC_PREFIX="/x",
                              CPATH="/x", C_INCLUDE_PATH="/x", CPLUS_INCLUDE_PATH="/x", GOFLAGS="-x",
                              GOROOT="/x", TS_NODE_PROJECT="/x", KEEP_ME="1",
                              PATH=os.pathsep.join([str(repo / "bin"), "rel/bin", "/usr/bin"]))
            env = comments.tool_env([str(repo)])
        finally:
            os.environ.clear()
            os.environ.update(saved)
        for k in ("NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "GCC_EXEC_PREFIX", "CPATH", "C_INCLUDE_PATH",
                  "CPLUS_INCLUDE_PATH", "GOFLAGS", "GOROOT", "TS_NODE_PROJECT"):
            self.assertNotIn(k, env)
        self.assertEqual(env["KEEP_ME"], "1")
        self.assertEqual(env["PATH"], "/usr/bin")

    @unittest.skipUnless(comments.c_preprocessor(str(tmpdir())), "no working gcc, cc or clang")
    def test_c_cross_check_never_reads_repo_headers(self):
        repo = new_repo()
        (repo / "local.h").write_text("#define TWO 2\n")
        (repo / "a.c").write_text('#include "local.h"\nint x = TWO;\n')
        commit_all(repo, "base")
        (repo / "a.c").write_text('#include "local.h"\nint x = TWO; // two\n')
        out = tmpdir() / "comments.json"
        self.assertEqual(cli("list", "--out", out, cwd=repo, env=clean_env()).returncode, 0)
        doc = json.loads(out.read_text())
        cli("apply", decide(out, {c: {"action": "delete"} for c in doc["pending"]}), "--list", out,
            cwd=repo, env=clean_env())
        r = cli("verify", "--list", out, cwd=repo, env=clean_env())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout.decode(), r"cross-check \S+: skipped \(the before file doesn't preprocess")


if __name__ == "__main__":
    unittest.main()
