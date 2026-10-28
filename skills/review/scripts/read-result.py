"""Read the review's result for one PR from <git-common-dir>/vstack/<pr>/review.json.

Usage: python3 -I read-result.py --pr N --expect-head-sha SHA --expect-base-sha SHA

Pass the PR's current head and base (`gh pr view N --json headRefOid,baseRefOid`),
as full SHAs. Prints the result JSON on stdout. Exits 0 ok, 3 stale (the file's
head_sha or base_sha doesn't match; nothing on stdout), 2 usage error, not in a
git repo, missing file or unsupported file (newer schema_version, not JSON,
wrong skill or pr, fails validation), 4 internal error. Reasons go to stderr.
Any non-zero exit means don't use the file.

Readers follow references/result-schema.md: a schema_version newer than
SCHEMA_VERSION is refused, and a head_sha or base_sha that isn't exactly the
PR's current one means stale. merge_base is recomputed with
`git merge-base --all base_sha head_sha` and the file refused (exit 2) unless
that gives exactly one commit equal to it, so the commits must be local. The
whole file is also checked as
write-result.py checks it (it's loaded from this folder), so a missing field,
wrong type or contradiction such as a merge_ok the rest doesn't support is
refused (exit 2); only unknown fields are ignored. The folder is found as
write-result.py finds it, and a symlinked vstack, <pr> folder or file is
refused. The file is a cache, not an attestation: never merge on it alone.

To import it, load this file with importlib.util.spec_from_file_location and
call read_result(pr, expect_head_sha, expect_base_sha); it returns the result
dict or raises Stale (which carries .result), Missing or Unsupported.

The reading itself is shared with babysit's read-result.py: it is Reader in
lib/result_file.py at the plugin root, which write-result.py loads by path.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "vstack_review_write_result", os.path.join(os.path.dirname(os.path.abspath(__file__)), "write-result.py"))
writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writer)
rf = writer.rf

SKILL = writer.SKILL
SCHEMA_VERSION = writer.SCHEMA_VERSION
MAX_BYTES = writer.MAX_BYTES
MAX_ERRORS = writer.MAX_ERRORS
MAX_PR = writer.MAX_PR
PR_ARG = writer.PR_ARG
SHA = writer.SHA
DIR_FLAGS = writer.DIR_FLAGS
is_int = writer.is_int
no_duplicates = writer.no_duplicates
no_constants = writer.no_constants
Unusable, Missing, Unsupported, Stale = rf.Unusable, rf.Missing, rf.Unsupported, rf.Stale

_reader = rf.Reader(writer)
read_result = _reader.read_result
main = _reader.main

if __name__ == "__main__":
    rf.run(main, "read-result")
