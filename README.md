# append-only

Check that a file has only ever been appended to, across its whole git history.

Some files are ledgers. A CHANGELOG, a decision log, an incident record, a
migrations index, an audit trail. The rule for all of them is the same and it is
never written down anywhere a machine can read: *add to the bottom, don't touch
what's already there.* Nothing enforces it. A line gets edited, a conflict gets
resolved by picking one side, someone tidies up — and the file still looks fine,
because the only evidence is in a diff nobody re-reads.

`append-only` reads the whole history of a file and tells you every commit that
changed something already written.

## Install

```
pip install git+https://github.com/committed-nightly/append-only
```

Python 3.10+. No dependencies — it shells out to `git`, which you already have.

## Usage

```
append-only [--mode append|prepend] [--after REGEX] [--since REV] PATH [PATH...]
```

Run it on a ledger:

```
$ append-only CHANGELOG.md --mode prepend --after '^## '
CHANGELOG.md — 61 commits checked, 1 violation

  a4f1c0e21  Sam Okafor  2026-07-14  tidy up old entries
    changed existing content at line 38
    --- before
    +++ after
    @@ -12,3 +12,3 @@
     ## 0.4.0
    -- Fixed a crash when the config file was missing
    +- Fixed a crash
```

Clean files say so and exit 0:

```
$ append-only docs/decisions.md
docs/decisions.md — 12 commits checked, clean
```

### Options

| | |
|---|---|
| `--mode append` | new content goes at the bottom. The default. |
| `--mode prepend` | new content goes at the top, as in [Keep a Changelog](https://keepachangelog.com). |
| `--after REGEX` | exempt every line above the first one matching `REGEX`, so a title and preamble can be edited freely. |
| `--header N` | exempt the first N lines instead. Simpler, and worse — see below. |
| `--since REV` | only check commits after `REV`. `--since origin/main` is the fast path for a PR check. |
| `--no-follow` | don't track the file across renames. |
| `-C DIR` | run as if started in `DIR`. |
| `--json` | machine-readable output. |
| `-q`, `--quiet` | print nothing; report through the exit code alone. |

### Telling the tool where the entries start

Almost every real ledger needs one of these. A file that starts with a title and
a paragraph of instructions will otherwise be reported the first time anyone
fixes a typo in the title, which is not what you meant. In `--mode prepend` it is
effectively mandatory, since new entries land *below* the heading rather than at
line 1.

**Use `--after`.** Give it a pattern that matches what an entry looks like —
`'^## '` for a changelog, `'^\d{4}-'` for a dated ledger — and everything above
the first match is exempt. It is recomputed for every version of the file, so
adding a line to the preamble simply moves the boundary.

`--header N` counts lines instead, and the count is a fact about the file today.
Add a line to the preamble and every entry shifts down by one; the exempt slice
no longer describes the same region in the old and new versions, and you get a
violation for exactly the edit the option exists to permit:

```
$ append-only --header 2 LEDGER.md
  0e57cae95  Ali Nasser  2026-03-02  docs: note the format
    changed existing content at line 4

$ append-only --after '^\d{4}-' LEDGER.md
LEDGER.md — 3 commits checked, clean
```

That does not go away when you fix `N`, because the commit stays in history
forever. `--header` is kept because it is obvious and it is enough for a file
whose preamble genuinely never changes.

Neither option can be used to switch the check off by accident. A `--header`
larger than the file, or an `--after` pattern that matches no line in any
version, exempts everything — and that is exit 2, not a clean run.

## In CI

The exit code is the whole interface:

| code | meaning |
|---|---|
| `0` | every path checked is clean |
| `1` | at least one path was changed in a way that wasn't an append |
| `2` | the check could not run |

`2` is deliberately not `1`. A typo in the path, an unknown revision, a directory
that isn't a git repo — none of those are a verdict about your ledger, and a gate
that reports "couldn't run" as either pass or fail is worse than no gate. If you
only branch on zero versus non-zero you get the safe behaviour anyway.

The same goes for the quieter ways a run can end up having read nothing. All of
these are `2`:

- the path is a directory, or a submodule, rather than a file
- `--header N` is bigger than the file in every version
- `--after` matches no line in any version
- `--since` names a revision that doesn't exist

The last one is the one you will meet, because it is what a shallow CI clone does
to `origin/main`, and it says so.

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0   # it needs the history; a shallow clone has nothing to check
- run: pipx run --spec git+https://github.com/committed-nightly/append-only append-only CHANGELOG.md --mode prepend --after '^## '
```

`fetch-depth: 0` is the part everyone forgets. Under the default shallow clone
there is almost no history, so the check passes on a file it never really looked
at. It will still be honest about how little it saw — `1 commit checked` in the
output is the tell.

## What counts as a violation

For an ordinary commit, the previous content must still be there and unchanged —
at the top of the new version in append mode, at the bottom in prepend mode.

For a **merge**, the rule has to be different, because two branches that each
appended an entry produce a result that extends neither side. So: *every* parent's
content must still be present in the result, in order, and nothing may have been
slipped in above the last line any parent needs. New content at the end is still
fine, including content the merge itself added.

Checking merges against only one parent is how a ledger actually loses entries.
Resolve a conflict by keeping your side, and the result is a perfect copy of one
parent while the other parent's entries are gone.

Violations are one of:

- **rewrote** — a line that was already there says something different now.
- **truncated** — the end of the file was cut off.
- **inserted** — a merge introduced content between entries that came from
  neither side.
- **deleted** — the file is gone.

Deliberately *not* violations: creating the file, renaming it, appending nothing,
and merges that keep everything every side had.

A file that doesn't end in a newline gets one before comparison. Appending to
such a file necessarily adds that newline, and without this every append to a
no-trailing-newline file would read as a rewrite of its last line.

## Known limits

**Rename detection is git's.** A commit that renames a file *and* rewrites most
of it is not recognised as a rename by git, so the file looks new and its earlier
history is not checked. Copies are never followed: if git reports `C100`, the new
file is genuinely new and does not inherit the source's history.

**Squash and rebase erase the evidence.** If your workflow squashes a branch
before merge, this tool sees the squashed commit. A rewrite followed by a
correction inside the same branch collapses into nothing to find. That is a
property of the history you kept, not of the check.

**It reads history, not blame.** It tells you which commit changed old content.
Whether that was vandalism, a legitimate correction, or a conflict resolved
carelessly at 4am is your call.

**Merges are judged by content, not by provenance.** If both sides of a merge
contain the same line, the tool cannot tell which one it came from, and does not
try. It asks only whether everything each parent had is still there.

**Speed.** Roughly 6ms per commit that touched the file — about 3 seconds for a
500-commit ledger. Merges that changed the file are included, which git's default
log hides; on a merge-heavy repo that is more commits than `git log` would show
you for the same path. Fine as a gate; use `--since origin/main` on pull requests
if your ledger is much older than that.

## A note on strictness

This tool is stricter than most ledgers really are. Ours says "never edit an old
line *except* to update its outcome later", and run against it the tool duly
reports those outcome updates. That is the correct result: they *are* edits to
old lines. If your ledger has a legitimate exception, the honest options are to
put the mutable part above the first entry, where `--after` exempts it, or to
keep it in a second file.
There is no `--allow` list and there is not going to be one, because a rule with
exceptions you can spell in a config file is a rule that erodes.

## Development

```
git clone https://github.com/committed-nightly/append-only
cd append-only
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The tests build real git repositories in temp directories and run real `git`
against them. The only interesting bugs in this tool are disagreements with git,
and you cannot find those against a mock.

## Licence

MIT.
