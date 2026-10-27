"""list: which files and comments are in scope for the diff, --all and --paths
modes, skipped files, and refusals (paths outside the repo, symlinks, --out
inside the repo, refs that look like options).

Run from the repo root: python3 -m unittest discover -s skills/remove-comments/scripts/tests
"""
import os
import unittest

from helpers import commit_all, git, listing, new_repo, run, tmpdir


def in_scope(doc):
    return sorted((c["file"], c["text"]) for c in doc["comments"] if c["in_scope"])


def statuses(doc):
    return {f["path"]: (f["status"], f.get("reason")) for f in doc["files"]}


class DiffScope(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        (self.repo / "app.py").write_text("# pre-existing comment\nx = 1\n")
        commit_all(self.repo, "base")

    def test_only_added_lines_and_untracked_files(self):
        (self.repo / "app.py").write_text("# pre-existing comment\nx = 1\n# added comment\ny = 2  # added trailing\n")
        (self.repo / "new file.ts").write_text("// whole file is new\nexport {};\n")
        r, doc, _ = listing(self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(in_scope(doc), [("app.py", "# added comment"), ("app.py", "# added trailing"),
                                         ("new file.ts", "// whole file is new")])
        out_of_scope = [c for c in doc["comments"] if not c["in_scope"]]
        self.assertEqual([c["text"] for c in out_of_scope], ["# pre-existing comment"])
        self.assertEqual(doc["pending"], [c["id"] for c in doc["comments"] if c["in_scope"]])

    def test_staged_changes_and_odd_file_names(self):
        names = ["sp ace.js", 'qu"ote.js', "-dash.js", "café.js", "dir with space/x.js"]
        for n in names:
            p = self.repo / n
            p.parent.mkdir(exist_ok=True)
            p.write_text("const a = 1; // c\n")
        git(self.repo, "add", "--", *names[:2])
        r, doc, _ = listing(self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(f for f, _ in in_scope(doc)), sorted(names))

    def test_all_covers_every_comment_in_the_diff_files(self):
        (self.repo / "app.py").write_text("# pre-existing comment\nx = 1\ny = 2  # added\n")
        (self.repo / "other.py").write_text("# untouched\n")
        r, doc, _ = listing(self.repo, "--all")
        self.assertEqual(in_scope(doc), [("app.py", "# added"), ("app.py", "# pre-existing comment"),
                                         ("other.py", "# untouched")])
        commit_all(self.repo, "more")
        (self.repo / "app.py").write_text("# pre-existing comment\nx = 1\ny = 3  # added\n")
        r, doc, _ = listing(self.repo, "--all")
        self.assertEqual(sorted({f for f, _ in in_scope(doc)}), ["app.py"])

    def test_clean_tree_uses_merge_base_with_default_branch(self):
        git(self.repo, "checkout", "-qb", "feature")
        (self.repo / "app.py").write_text("# pre-existing comment\nx = 1\nz = 3  # on the branch\n")
        commit_all(self.repo, "feature work")
        r, doc, _ = listing(self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merge base with main", doc["scope"]["label"])
        self.assertEqual(in_scope(doc), [("app.py", "# on the branch")])
        git(self.repo, "checkout", "-q", "main")
        r, doc, _ = listing(self.repo)
        self.assertEqual(doc["files"], [])

    def test_explicit_base_and_renames(self):
        base = git(self.repo, "rev-parse", "HEAD").decode().strip()
        git(self.repo, "checkout", "-qb", "feature")
        body = "".join(f"x{i} = {i}  # line {i}\n" for i in range(20))
        (self.repo / "big.py").write_text(body)
        commit_all(self.repo, "big")
        git(self.repo, "mv", "big.py", "moved.py")
        (self.repo / "moved.py").write_text(body + "y = 1  # new\n")
        commit_all(self.repo, "move")
        r, doc, _ = listing(self.repo, "--diff", "HEAD~1")
        self.assertEqual(in_scope(doc), [("moved.py", "# new")])
        r, doc, _ = listing(self.repo, "--diff", base)
        self.assertEqual(len(in_scope(doc)), 21)

    def test_refusals(self):
        r, doc, _ = listing(self.repo, "--diff=--output=/tmp/x")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"refusing ref", r.stderr)
        self.assertEqual(listing(self.repo, "--diff", "--output=/tmp/x")[0].returncode, 2)
        self.assertFalse(os.path.exists("/tmp/x"))
        r, doc, _ = listing(self.repo, "--diff", "no-such-ref")
        self.assertEqual(r.returncode, 2)
        r = run("list", "--out", self.repo / "comments.json", cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"outside the repository", r.stderr)
        self.assertFalse((self.repo / "comments.json").exists())


class Skips(unittest.TestCase):
    def test_unsupported_generated_vendored(self):
        repo = new_repo()
        (repo / "x").mkdir()
        (repo / "x/a.ts").write_text("export {};\n")
        commit_all(repo, "base")
        files = {"README.md": "<!-- c -->\n", "data.json": "{}\n", "node_modules/p/i.js": "// c\n",
                 "dist/app.js": "// c\n", "lib/app.min.js": "// c\n", "yarn.lock": "# c\n",
                 "gen.go": "// Code generated by x. DO NOT EDIT.\npackage x\n",
                 "ok.go": "package x // c\n"}
        for name, body in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        os.symlink("x/a.ts", repo / "link.ts")
        r, doc, _ = listing(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = statuses(doc)
        self.assertEqual(st["README.md"], ("skipped", "unsupported file type"))
        self.assertEqual(st["data.json"], ("skipped", "unsupported file type"))
        self.assertEqual(st["node_modules/p/i.js"][1], "generated or vendored path")
        self.assertEqual(st["dist/app.js"][1], "generated or vendored path")
        self.assertEqual(st["lib/app.min.js"][1], "minified")
        self.assertEqual(st["yarn.lock"][1], "lockfile")
        self.assertEqual(st["gen.go"][1], "generated (says so in its first lines)")
        self.assertEqual(st["link.ts"], ("skipped", "symlink"))
        self.assertEqual(st["ok.go"], ("ok", None))
        self.assertEqual(in_scope(doc), [("ok.go", "// c")])


class PathsScope(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        (self.repo / "src/sub").mkdir(parents=True)
        (self.repo / "src/a.py").write_text("x = 1  # a\n")
        (self.repo / "src/sub/b.ts").write_text("// b\nexport {};\n")
        (self.repo / "top.sh").write_text("echo hi # top\n")
        commit_all(self.repo, "base")
        (self.repo / "src/untracked.py").write_text("# nope\n")

    def test_directory_expands_to_tracked_files(self):
        r, doc, _ = listing(self.repo, "--paths", "src")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(in_scope(doc), [("src/a.py", "# a"), ("src/sub/b.ts", "// b")])

    def test_untracked_file_is_skipped(self):
        r, doc, _ = listing(self.repo, "--paths", "src/untracked.py", "top.sh")
        self.assertEqual(statuses(doc)["src/untracked.py"][0], "skipped")
        self.assertEqual(in_scope(doc), [("top.sh", "# top")])

    def test_leading_dash_after_double_dash(self):
        (self.repo / "-x.py").write_text("y = 2  # dash\n")
        commit_all(self.repo, "dash")
        r, doc, _ = listing(self.repo, "--paths", "--", "-x.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(in_scope(doc), [("-x.py", "# dash")])

    def test_outside_and_symlinks_refused(self):
        outside = tmpdir() / "o.py"
        outside.write_text("# o\n")
        r, _, _ = listing(self.repo, "--paths", outside)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"outside the repository", r.stderr)
        r, _, _ = listing(self.repo, "--paths", "../")
        self.assertEqual(r.returncode, 2)
        os.symlink("src", self.repo / "linkdir")
        os.symlink("top.sh", self.repo / "link.sh")
        for p in ("linkdir/a.py", "link.sh"):
            with self.subTest(p=p):
                r, _, _ = listing(self.repo, "--paths", p)
                self.assertEqual(r.returncode, 2)
                self.assertIn(b"symlink", r.stderr)


if __name__ == "__main__":
    unittest.main()
