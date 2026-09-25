"""Command line entry point.

Exit codes matter here, because the main use for this tool is as a CI gate:

    0  every path checked is clean
    1  at least one path was changed in a way that wasn't an append
    2  the tool could not run the check at all

2 is deliberately not 1. A missing file, a bad revision or "this isn't a git
repo" must not look like a passing... or a failing... ledger. If you can't tell
"the check failed" from "the check didn't happen", the gate is worthless.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from .core import Mode, NothingChecked, PathNotTracked, Report, check_file
from .gitlog import GitError, repo_root, resolve

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

# An exception has to name one commit that cannot turn into a different commit
# later. Seven is git's usual floor for an abbreviation you'd write down.
_SHA = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")


class AllowFileError(ValueError):
    """A line of the allow file isn't a sha and a reason."""

    def __init__(self, path: str, lineno: int, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.lineno = lineno
        self.message = message


def _parse_allow_file(path: str) -> list[tuple[str, str | None]]:
    """Read ``sha [reason]`` lines. Blank lines and whole-line # are comments.

    The reason is the rest of the line, with a leading ``#`` stripped so that
    both of the ways people naturally write it mean the same thing::

        2dca89db4 cosmetic quoting fix, logbook#27
        2dca89db4 # cosmetic quoting fix, logbook#27
    """
    entries: list[tuple[str, str | None]] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            token = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            reason = rest.strip().lstrip("#").strip() or None
            if not _SHA.match(token):
                raise AllowFileError(
                    path, lineno, f"{token!r} is not a commit sha"
                )
            entries.append((token, reason))
    return entries


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="append-only",
        description=(
            "Check that a file has only ever been appended to, "
            "across its whole git history."
        ),
        epilog=(
            "Exit codes: 0 clean, 1 violations found, 2 the check could not run."
        ),
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="file(s) to check")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in Mode],
        default=Mode.APPEND.value,
        help=(
            "append: new content must go at the bottom (default). "
            "prepend: new content must go at the top, as in Keep a Changelog."
        ),
    )
    exempt = parser.add_mutually_exclusive_group()
    exempt.add_argument(
        "--after",
        metavar="REGEX",
        help=(
            "exempt every line above the first one matching REGEX, recomputed "
            "for each version of the file. Match what an entry looks like, e.g. "
            "'^## '. Prefer this over --header: it survives preamble edits"
        ),
    )
    exempt.add_argument(
        "--header",
        type=int,
        default=0,
        metavar="N",
        help=(
            "exempt the first N lines from the check. Simpler than --after and "
            "worse: adding a line to the preamble shifts every entry down and "
            "the check reports a violation for a legal edit (default: 0)"
        ),
    )
    parser.add_argument(
        "--since",
        metavar="REV",
        help="only check commits after REV, e.g. --since origin/main",
    )
    parser.add_argument(
        "--allow",
        action="append",
        metavar="SHA",
        help=(
            "accept the violation in commit SHA: keep checking everything "
            "else, and keep reporting this one. Repeatable. A commit sha "
            "only — not a branch or a revision expression"
        ),
    )
    parser.add_argument(
        "--allow-from",
        metavar="FILE",
        help=(
            "read allowed commits from FILE, one sha per line, with the "
            "reason for each on the rest of its line. Blank lines and lines "
            "starting with # are ignored"
        ),
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="don't track the file across renames",
    )
    parser.add_argument(
        "-C",
        dest="directory",
        default=".",
        metavar="DIR",
        help="run as if started in DIR (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print nothing; report only through the exit code",
    )
    return parser


def _report_to_dict(report: Report) -> dict:
    return {
        "path": report.path,
        "mode": report.mode.value,
        "header": report.preamble.lines,
        "after": report.preamble.after.pattern if report.preamble.after else None,
        "commits_checked": report.commits_checked,
        "ok": report.ok,
        "allowed": len(report.excused),
        "violations": [
            {
                "commit": v.commit.sha,
                "author": v.commit.author,
                "date": v.commit.date,
                "subject": v.commit.subject,
                "path": v.path,
                "kind": v.kind,
                "line": v.line,
                "description": v.describe(),
                "excerpt": v.excerpt,
                "allowed": v.allowed,
                "reason": v.reason,
            }
            for v in report.violations
        ],
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _print_human(report: Report, out) -> None:
    excused = report.excused
    summary = "clean" if report.ok else _plural(len(report.failures), "violation")
    if excused:
        summary += f" ({len(excused)} allowed)"
    print(
        f"{report.path} — {_plural(report.commits_checked, 'commit')} checked, "
        f"{summary}",
        file=out,
    )
    for violation in report.violations:
        commit = violation.commit
        print(file=out)
        print(
            f"  {commit.short}  {commit.author}  {commit.date[:10]}  "
            f"{commit.subject}",
            file=out,
        )
        print(f"    {violation.describe()}", file=out)
        if violation.allowed:
            # No excerpt. An accepted violation is one you have already read;
            # printing the diff for all nine of them buries the one that isn't.
            print(f"    allowed: {violation.reason or 'no reason given'}", file=out)
            continue
        for line in violation.excerpt.splitlines():
            print(f"    {line}", file=out)


def _dead_allow_notes(
    allow: dict[str, str | None], reports: list[Report], since: str | None
) -> list[str]:
    """Complain about exceptions that excused nothing anywhere.

    Judged across every path in the run, not per report: with two paths and one
    allow, the path the commit does not touch would otherwise nag about an
    exception that is doing its job on the other one.

    An allowed commit missing from the history entirely is only worth
    mentioning when the whole history was on the table. Under --since it is the
    ordinary case -- an exception older than the baseline, still written down
    so it survives the baseline moving.
    """
    used = {v.commit.sha for r in reports for v in r.violations if v.allowed}
    walked = {sha for r in reports for sha in r.allow_unused}
    notes = []
    for sha in allow:
        if sha in used:
            continue
        if sha in walked:
            notes.append(
                f"append-only: --allow {sha[:9]} is not needed — that commit "
                "was checked and kept the rule. Drop it."
            )
        elif since is None:
            notes.append(
                f"append-only: --allow {sha[:9]} never touched "
                f"{', '.join(r.path for r in reports)}."
            )
    return notes


def _nothing_checked(exc: NothingChecked, args) -> list[str]:
    """Explain a run that inspected nothing, and say what to do about it.

    All of these end in exit 2 rather than 0. Reporting "clean" for a file the
    tool never actually looked at is the exact failure a gate is supposed to
    prevent, and it is worse than having no gate, because it comes with a tick
    next to it.
    """
    commits = _plural(exc.commits, "commit")
    if exc.reason == "not-a-file":
        return [
            f"append-only: {exc.path} is not a file in this repository.",
            f"  {commits} touched that path, but no version of it is a regular "
            "file — a directory, or a submodule? Nothing was checked.",
        ]
    if args.after is not None:
        return [
            f"append-only: no line of {exc.path} matched --after {args.after!r}, "
            "in any version.",
            f"  Every one of {commits} was exempt in full. Nothing was checked — "
            "does the pattern match what an entry actually looks like?",
        ]
    return [
        f"append-only: --header {args.header} exempts the whole of {exc.path} "
        "in every version.",
        f"  {commits} touched it and none of them were checked. Count the "
        "preamble again, or use --after to stop counting lines by hand.",
    ]


def _resolve_allow(
    args, directory: str
) -> tuple[dict[str, str | None], list[str] | None]:
    """Turn --allow / --allow-from into {full sha: reason}, or explain why not.

    Everything is resolved against the repository up front. A sha nobody can
    resolve is exit 2 rather than a silently inert exception, for the same
    reason an unknown --since is: the run would otherwise report a verdict
    about a configuration it never applied.
    """
    specs: list[tuple[str, str | None]] = []
    if args.allow_from:
        try:
            specs += _parse_allow_file(args.allow_from)
        except OSError as exc:
            return {}, [f"append-only: --allow-from {args.allow_from}: {exc.strerror}."]
        except AllowFileError as exc:
            return {}, [
                f"append-only: {exc.path}:{exc.lineno}: {exc.message}.",
                "  Each line is a commit sha, then the reason it is accepted. "
                "Use # for a comment.",
            ]
    specs += [(sha, None) for sha in args.allow or []]

    allow: dict[str, str | None] = {}
    for token, reason in specs:
        if not _SHA.match(token):
            return {}, [
                f"append-only: --allow {token!r} is not a commit sha.",
                "  An exception has to name one immutable commit. A branch or "
                "a revision expression would quietly come to mean a different "
                "commit later, which is not an exception, it is a hole.",
            ]
        try:
            full = resolve(token, directory)
        except GitError:
            return {}, [
                f"append-only: --allow {token}: no such commit in this "
                "repository.",
                "  On CI this is usually a shallow clone — actions/checkout "
                "needs fetch-depth: 0 before an old sha resolves.",
            ]
        # First mention wins the reason, so a file entry keeps its reason even
        # if the same sha turns up again bare on the command line.
        if full not in allow or allow[full] is None:
            allow[full] = reason
    return allow, None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = sys.stdout

    if args.header < 0:
        print("append-only: --header must be 0 or more", file=sys.stderr)
        return EXIT_ERROR

    if args.after is not None:
        try:
            re.compile(args.after)
        except re.error as exc:
            print(
                f"append-only: --after {args.after!r} is not a valid regular "
                f"expression: {exc}",
                file=sys.stderr,
            )
            return EXIT_ERROR

    directory = args.directory
    if not os.path.isdir(directory):
        print(f"append-only: no such directory: {directory}", file=sys.stderr)
        return EXIT_ERROR

    try:
        repo_root(directory)
    except GitError as exc:
        print(f"append-only: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.since:
        try:
            resolve(args.since, directory)
        except GitError:
            print(
                f"append-only: --since {args.since}: no such revision in this "
                "repository.",
                file=sys.stderr,
            )
            print(
                "  On CI this is usually a shallow clone — actions/checkout "
                "needs fetch-depth: 0 before origin/main resolves.",
                file=sys.stderr,
            )
            return EXIT_ERROR

    allow, failure = _resolve_allow(args, directory)
    if failure is not None:
        for line in failure:
            print(line, file=sys.stderr)
        return EXIT_ERROR

    reports: list[Report] = []
    for path in args.paths:
        try:
            reports.append(
                check_file(
                    path,
                    directory,
                    mode=Mode(args.mode),
                    header=args.header,
                    after=args.after,
                    follow=not args.no_follow,
                    since=args.since,
                    allow=allow,
                )
            )
        except PathNotTracked:
            print(
                f"append-only: {path} has no history in this repository "
                "(wrong path, or never committed?)",
                file=sys.stderr,
            )
            return EXIT_ERROR
        except NothingChecked as exc:
            for line in _nothing_checked(exc, args):
                print(line, file=sys.stderr)
            return EXIT_ERROR
        except GitError as exc:
            print(f"append-only: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if args.json and not args.quiet:
        used = {v.commit.sha for r in reports for v in r.violations if v.allowed}
        walked = {sha for r in reports for sha in r.allow_unused}
        payload = {
            "ok": all(r.ok for r in reports),
            # Three states, kept apart rather than merged into "dead": whether
            # an absent exception is a problem depends on --since, and that is
            # the caller's call to make, not ours.
            "allow_used": sorted(used),
            "allow_unused": sorted(walked - used),
            "allow_absent": sorted(set(allow) - used - walked),
            "reports": [_report_to_dict(r) for r in reports],
        }
        print(json.dumps(payload, indent=2), file=out)
    elif not args.quiet:
        for index, report in enumerate(reports):
            if index:
                print(file=out)
            _print_human(report, out)
        for note in _dead_allow_notes(allow, reports, args.since):
            print(note, file=out)

    return EXIT_OK if all(r.ok for r in reports) else EXIT_VIOLATIONS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
