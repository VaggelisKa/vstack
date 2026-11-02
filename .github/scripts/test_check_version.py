"""Tests for check-version.py. Each test builds a throwaway repo in a temp dir.

Run from the repo root: python3 -m unittest discover -s .github/scripts
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_version", HERE / "check-version.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=root, env=ENV, check=True, capture_output=True)


class CheckVersion(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="vstack-test-version-")
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        git(self.root, "init", "-q")
        self.write(codex="1.2.3", gemini="1.2.3")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")

    def write(self, codex, gemini, claude=None):
        (self.root / ".codex-plugin").mkdir(exist_ok=True)
        (self.root / ".claude-plugin").mkdir(exist_ok=True)
        (self.root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "x", "version": codex}))
        (self.root / "gemini-extension.json").write_text(json.dumps({"name": "x", "version": gemini}))
        doc = {"name": "x", **({"version": claude} if claude else {})}
        (self.root / ".claude-plugin/plugin.json").write_text(json.dumps(doc))

    def problems(self, base="HEAD"):
        return cv.check(self.root, base)

    def test_bumped_versions_pass(self):
        for v in ("1.2.4", "1.3.0", "2.0.0", "1.10.0"):
            with self.subTest(v=v):
                self.write(codex=v, gemini=v)
                self.assertEqual(self.problems(), [])

    def test_unchanged_version_fails(self):
        [p] = self.problems()
        self.assertIn("not higher than 1.2.3", p)

    def test_lower_version_fails(self):
        self.write(codex="1.2.2", gemini="1.2.2")
        [p] = self.problems()
        self.assertIn("not higher", p)

    def test_mismatched_versions_fail(self):
        self.write(codex="1.2.4", gemini="1.2.3")
        [p] = self.problems()
        self.assertIn("versions differ", p)

    def test_bad_version_fails(self):
        for v in ("1.2", "v1.2.4", "01.2.4", "1.2.4-rc1", 124):
            with self.subTest(v=v):
                self.write(codex=v, gemini=v)
                self.assertTrue(any("not X.Y.Z" in p for p in self.problems()))

    def test_version_in_claude_manifest_fails(self):
        self.write(codex="1.2.4", gemini="1.2.4", claude="1.2.4")
        [p] = self.problems()
        self.assertIn(".claude-plugin/plugin.json: remove 'version'", p)

    def test_version_in_marketplace_entry_fails(self):
        (self.root / ".claude-plugin/marketplace.json").write_text(
            json.dumps({"name": "m", "plugins": [{"name": "x", "source": "./", "version": "1.2.4"}]}))
        self.write(codex="1.2.4", gemini="1.2.4")
        [p] = self.problems()
        self.assertIn("marketplace.json: remove 'version'", p)

    def test_without_base_only_checks_the_tree(self):
        self.assertEqual(self.problems(base=None), [])

    def test_main_exit_codes(self):
        self.assertEqual(cv.main(["--root", str(self.root), "--base", "HEAD"]), 1)
        self.write(codex="1.2.4", gemini="1.2.4")
        self.assertEqual(cv.main(["--root", str(self.root), "--base", "HEAD"]), 0)


if __name__ == "__main__":
    unittest.main()
