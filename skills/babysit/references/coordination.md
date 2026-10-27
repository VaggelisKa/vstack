# Fixer coordination

`scripts/coordination.py` decides, each poll, whether babysit may act and on
what. It needs no knowledge of which tool the other fixer is: it reads PR
state and events only.

```
coordination.py poll --pr N [-R [HOST/]OWNER/REPO] [--input FILE|-] [--take-over]
coordination.py claim --pr N --item ID [-R ...]
coordination.py markers --item ID
```

`poll` prints:

```json
{"settled": true, "settled_reasons": [],
 "items": [{"id": "check:lint", "kind": "check", "state": "claimed", "claimant": "copilot-swe-agent", "own": false}],
 "claimed_count": 1, "actionable": [], "own_fix_rounds": 1, "handback": null}
```

Exit 0 ok, 5 handback (`handback` is `{reason, detail, action}`), 2 usage
(also `-h`, a state file that fails its checks), 4 internal (prints an
`unknown` handback first). `--input` takes `{"view": <gh pr view JSON>,
"threads": [...], "pr_reactions": [...], "viewer": "login"}` and calls no gh.

## Settled

All of:

- no check queued or running on the head;
- the head is the one the previous poll saw. **The first poll is never
  settled**: it records the head;
- no other actor is working: a comment saying so ("on it", "working on
  this", "WIP", 👀...) with no later "done" from them and no later commit, a
  👀 on the PR newer than the head commit, an `@copilot`/`@codex`/`@claude`
  mention not yet answered by that tool, or a pending review;
- the threads and reactions could all be read.

`settled_reasons` says what's missing. Not settled means `actionable` is empty.

## Items and states

Ids: `check:<name>` (a failing check, or one tracked before) and
`thread:<threadId>`.

| State | Check | Thread |
|---|---|---|
| `open` | Failing, nobody on it | Last word is the reviewer's (or a third party's), nobody on it |
| `claimed` | A foreign commit since it started failing names it, or a "working" comment names it | A 👀 on it since the last comment, a "working" reply, or a foreign commit since the last comment that touches its file or names its id |
| `addressed` | Passes on the head (or gone after CI finished) | Resolved, or answered (PR author, a bot or agent, the viewer, or whoever claimed it) with no newer reviewer reply |
| `stale-claim` | Claimed, and 2 CI cycles completed on newer heads, or 3 polls with CI idle and nothing new | Same |

`claimed` with `own: true` is babysit's own claim (from `claim`); it's
actionable. If both sides claim, the earlier claim wins. `claimed_count`
counts `claimed` and `stale-claim`, own or not; autonomous-merge's gate needs
it to be 0.

## Markers

- Reply: `<!-- vstack:babysit:<id> -->` anywhere in the body.
- Commit: a line that is exactly `vstack:babysit:<id>`, one per item (not a
  `Co-Authored-By`-style trailer). autonomous-merge's review fixes use
  `vstack:autonomous-merge:<fingerprint>`; both count as own.
- `claim` adds a 👀 (`content=eyes`) to the thread's first comment, or to the
  PR for a check, and records it so babysit's own 👀 isn't read as someone
  else's.

Commits without a marker are foreign, even from the same account.
`own_fix_rounds` counts commits with a babysit marker; autonomous-merge's
fix-round cap counts only those.

## Handbacks

| Reason | When | Action |
|---|---|---|
| `stale_claim` | An item is `stale-claim`, or a working signal stayed unchanged for 3 polls, and no `--take-over` | "the other fixer seems stuck on `<id>`; fix it yourself or rerun with 'take over'" |
| `conflicting_fixer` | A check fixed by one side fails again after the other side's commit, or fails again after foreign commits twice | Pick one fixer and pause the other |

`--take-over` turns stale items actionable (and ignores a stale working
signal). Pass it only when the user's own words in this run's request say to
take over.

## State

`<git-common-dir>/vstack/<pr>/coordination.json`, written atomically, never
through a symlink: last head, poll count, babysit's claims, and per item its
state, `claimed_since_head`, `ci_cycles_completed_since_claim`,
`polls_without_progress` and transitions (with whether the commits behind each
were own or foreign). Delete it to start over.
