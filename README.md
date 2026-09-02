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
append-only [--mode append|prepend] [--header N] [--since REV] PATH [PATH...]
```

Run it on a ledger:

```
$ append-only CHANGELOG.md --mode prepend --header 4
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
| `--header N` | exempt the first N lines, so a title and preamble can be edited freely. |
| `--since REV` | only check commits after `REV`. `--since origin/main` is the fast path for a PR check. |
| `--no-follow` | don't track the file across renames. |
| `-C DIR` | run as if started in `DIR`. |
| `--json` | machine-readable output. |
| `-q`, `--quiet` | print nothing; report through the exit code alone. |

Almost every real ledger needs `--header`. A file that starts with a title and a
paragraph of instructions will otherwise be reported the first time anyone fixes
a typo in the title, which is not what you meant. Count the lines above the first
entry and pass that. In `--mode prepend` it is effectively mandatory, since new
entries land *below* the heading rather than at line 1.

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

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0   # it needs the history; a shallow clone has nothing to check
- run: pipx run --spec git+https://github.com/committed-nightly/append-only append-only CHANGELOG.md --mode prepend --header 4
```

`fetch-depth: 0` is the part everyone forgets. Under the default shallow clone
there is almost no history, so the check passes on a file it never really looked
at. It will still be honest about how little it saw — `1 commit checked` in the
output is the tell.

## What counts as a violation

Per commit, the file is compared against **each parent**, and the commit is fine
if the parent's content survives intact — at the top of the new version in append
mode, at the bottom in prepend mode. Anything else is one of:

- **rewrote** — a line that was already there says something different now.
- **truncated** — the end of the file was cut off.
- **deleted** — the file is gone.

Deliberately *not* violations: creating the file, renaming it, appending nothing,
and merges that keep at least one parent's content intact.

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

**Speed.** Roughly 6ms per commit that touched the file — about 3 seconds for a
500-commit ledger. Fine as a gate; use `--since origin/main` on pull requests if
your ledger is much older than that.

## A note on strictness

This tool is stricter than most ledgers really are. Ours says "never edit an old
line *except* to update its outcome later", and run against it the tool duly
reports those outcome updates. That is the correct result: they *are* edits to
old lines. If your ledger has a legitimate exception, the honest options are to
put the mutable part above the `--header` line, or to keep it in a second file.
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
