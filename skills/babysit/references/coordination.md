# Fixer coordination

`scripts/coordination.py` decides, each poll, whether babysit may act and on
what. It needs no knowledge of which tool the other fixer is: it reads PR
state and events only.

```
coordination.py poll --pr N [-R [HOST/]OWNER/REPO] [--input FILE|-] [--take-over]
coordination.py claim --pr N --item ID [-R ...]
coordination.py remove-claim --pr N --item ID [-R ...]
coordination.py markers --item ID
coordination.py pushed --pr N --sha SHA
```

`poll` prints:

```json
{"settled": true, "settled_reasons": [],
 "items": [{"id": "check:lint", "kind": "check", "state": "claimed", "claimant": "copilot-swe-agent", "own": false,
            "escalate": false}],
 "claimed_count": 1, "actionable": [], "lost_claims": [], "own_fix_rounds": 1, "handback": null}
```

Exit 0 ok, 5 handback (`handback` is `{reason, detail, action}`), 2 usage
(also `-h`, a state file that fails its checks), 4 internal (prints an
`unknown` handback first). `--input` takes `{"view": <gh pr view JSON>,
"threads": [...], "pr_reactions": [...], "comment_reactions": {"<comment
databaseId>": [...]}, "viewer": "login"}` and calls no gh.

Threads come from GraphQL (with each comment's `databaseId`, no reactions),
up to 50 comments per thread. A thread whose comments run past that page
(`comments.pageInfo.hasNextPage`) makes the threads unreadable
(`threads_unreadable`, not settled).
👀 reactions come from REST, every page, with no cap:
`gh api --paginate --slurp repos/O/R/issues/N/reactions?content=eyes&per_page=100`
for the PR, and `repos/O/R/pulls/comments/<databaseId>/reactions?content=eyes&per_page=100`
for every comment of each unresolved thread.

## Settled

All of:

- no check queued or running on the head;
- the head is the one the previous poll saw. **The first poll is never
  settled**: it records the head. A `gh pr update-branch` merge from base is
  a new head like any other push, so the poll after it isn't settled either;
- no other actor is working: a comment saying so ("on it", "working on
  this", "WIP", 👀...) with no later "done" from them and no later commit, a
  👀 on the PR newer than the head commit, an `@copilot`/`@codex`/`@claude`
  mention (in a PR comment, or any comment of an unresolved thread) not yet
  answered there by that tool, or a draft (`PENDING`) review by someone other
  than the viewer. A requested reviewer who hasn't started doesn't count. A
  bot's comment (`__typename` `Bot`, or a login ending in `[bot]`) is never a
  "working" signal, on a thread or a check either, except from these coding
  agents (case-insensitive), whose "working" comment or reply is a claim:
  `copilot`, `copilot-swe-agent`, `copilot-swe-agent[bot]`,
  `chatgpt-codex-connector`, `chatgpt-codex-connector[bot]`, `codex`,
  `claude`, `claude[bot]`. The
  viewer's comments carrying an own marker
  (`<!-- vstack:babysit:… -->`, `<!-- vstack:autonomous-merge:… -->`) are
  never working signals, mentions in them included;
- the threads and reactions could all be read.

`settled_reasons` says what's missing. Not settled means `actionable` is empty.

## Items and states

Ids: `check:<name>` (a failing check, or one tracked before) and
`thread:<threadId>`.

| State | Check | Thread |
|---|---|---|
| `open` | Failing, nobody on it | Last word is the reviewer's (or a third party's), nobody on it |
| `claimed` | A foreign commit since it started failing names it, or a "working" comment names it | A 👀 on it since the last comment, a "working" reply, or a foreign commit since the last comment that touches its file or names its id |
| `addressed` | Passes on the head (or gone after CI finished) | Resolved, or answered with no newer reviewer reply. Only the PR author, the viewer, or whoever claimed the thread (a 👀, an earlier "working" reply, or the claimant the last poll saw) answers; other bots' replies don't. A 👀 by anyone but the viewer newer than the PR author's or viewer's answer keeps it `claimed` |
| `stale-claim` | Claimed, and 2 CI cycles completed on newer heads, or 3 polls with CI idle and nothing new | Same |

`claimed` with `own: true` is babysit's own claim (from `claim`); it's
actionable. If both sides claim, the earlier claim wins. When the other fixer
wins, the item is listed in `lost_claims`: run `remove-claim` for it, which
deletes the viewer's own 👀 on the item's subject (`gh api -X DELETE
…/reactions/<id>`) and forgets the claim, then leave the item alone. A
check's 👀 is the PR's, shared by every check claim, so it is kept (only the
record is dropped) while another check claim remains. `claimed_count`
counts `claimed` and `stale-claim`, own or not; autonomous-merge's gate needs
it to be 0.

An item with `escalate: true` is never actionable (see `escalated` below);
the flag clears once the thread is addressed again.

## Markers

- Reply: `<!-- vstack:babysit:<id> -->` anywhere in the body.
- Commit: a line that is exactly `vstack:babysit:<id>`, one per item (not a
  `Co-Authored-By`-style trailer). autonomous-merge's review fixes use
  `vstack:autonomous-merge:<fingerprint>`; both count as own.
- `claim` adds a 👀 (`content=eyes`) to the thread's first comment, or to the
  PR for a check, and records it so babysit's own 👀 isn't read as someone
  else's. `claim` and `remove-claim` are the only GitHub writes.

A marker counts only on the viewer's own work: a comment the viewer wrote
(and on a thread, a babysit marker only for that thread's id), or a commit
whose first author or committer login is the viewer, or whose author has no
linked account (an unlinked email) and that is tied to this clone: its SHA was
recorded with `pushed`, or this clone's reflog (`git reflog show --all`: HEAD
and every ref) has an entry for it whose subject begins `commit:` or
`commit (amend):`. The author email and the local object store don't count: a
fetch brings in another clone's commits under the same `user.email`. If git
fails, nothing is tied. Any other unlinked marked commit is foreign (another
clone's or user's fixer can't pass as own).
Anyone else's marker is foreign (a forged reply doesn't address a thread,
and a 👀 or foreign claim on it still counts).
Commits without a marker are foreign too, even from the same account.

`pushed --pr N --sha SHA` (a full 40 or 64 hex SHA) adds the SHA of an own fix
commit just pushed to the state file's `pushed` list (last 500 kept; atomic
write, nothing on GitHub). Run it from the clone that pushed, right after each
push of your own marked fix commit. It exits 2 and writes nothing unless the
SHA is a commit in this clone, its author email (`%ae`) equals this clone's
`git config user.email` (case-insensitive; unset fails), and it is `HEAD` or
`@{push}`.

`gh pr view --json commits` has no committer. `poll` reads it with
`gh api repos/O/R/commits/<sha>` only where it decides: a marked commit whose
first author is someone else or has no linked account (so a commit the viewer
committed counts as own). If that read fails the commit stays foreign. An
`--input` bundle may put `"committer": {"login": ...}` on a commit itself.

`own_fix_rounds` counts the own commits with a babysit marker.
autonomous-merge's fix-round cap counts babysit-marked commits by its own `gh api` identity rule (author
or committer login, or an author or committer email that is the viewer's
noreply address or in `gh api user/emails`; a commit with no linked author or
committer counts too), plus a backstop on every such commit, marked or not.

## Handbacks

| Reason | When | Action |
|---|---|---|
| `stale_claim` | An item is `stale-claim`, or a working signal stayed unchanged for 3 polls, and no `--take-over` | "the other fixer seems stuck on `<id>`; fix it yourself or rerun with 'take over'" |
| `conflicting_fixer` | A check fixed by one side fails again after the other side's commit, or fails again after foreign commits twice | Pick one fixer and pause the other |
| `escalated` | A resolved thread is reopened after another fixer (not babysit) replied in it; the item gets `escalate: true` | A maintainer decides who handles it; babysit leaves it alone |

`--take-over` turns stale items actionable (and ignores a stale working
signal). Pass it only when the user's own words in this run's request say to
take over.

## State

`<git-common-dir>/vstack/<pr>/coordination.json`, written atomically, never
through a symlink: last head, poll count, babysit's claims, the `pushed` SHAs
(optional in older files, read as empty), and per item its
state, `claimed_since_head`, `ci_cycles_completed_since_claim`,
`polls_without_progress` and transitions (with whether the commits behind each
were own or foreign). Delete it to start over.

The state is per clone. `claims`, `self_subjects` (the subjects of
babysit's own 👀, so they aren't read as someone else's) and `pushed` live
under the git common dir, so run every `poll`, `claim`, `remove-claim` and
`pushed` for a PR from the same clone, or a worktree of it (worktrees share the
common dir). From another clone, babysit's own 👀 looks like another fixer's
claim, and its unlinked marked commits look like another fixer's commits.
