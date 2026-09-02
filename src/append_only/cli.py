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
import sys

from .core import Mode, PathNotTracked, Report, check_file
from .gitlog import GitError, repo_root, resolve

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


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
    parser.add_argument(
        "--header",
        type=int,
        default=0,
        metavar="N",
        help=(
            "exempt the first N lines from the check, so a title and preamble "
            "can be edited freely. Required for prepend mode on any file with "
            "a heading (default: 0)"
        ),
    )
    parser.add_argument(
        "--since",
        metavar="REV",
        help="only check commits after REV, e.g. --since origin/main",
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
        "header": report.header,
        "commits_checked": report.commits_checked,
        "ok": report.ok,
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
            }
            for v in report.violations
        ],
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _print_human(report: Report, out) -> None:
    count = len(report.violations)
    summary = "clean" if report.ok else _plural(count, "violation")
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
        for line in violation.excerpt.splitlines():
            print(f"    {line}", file=out)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = sys.stdout

    if args.header < 0:
        print("append-only: --header must be 0 or more", file=sys.stderr)
        return EXIT_ERROR

    directory = args.directory
    if not os.path.isdir(directory):
        print(f"append-only: no such directory: {directory}", file=sys.stderr)
        return EXIT_ERROR

    try:
        repo_root(directory)
        if args.since:
            resolve(args.since, directory)
    except GitError as exc:
        print(f"append-only: {exc}", file=sys.stderr)
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
                    follow=not args.no_follow,
                    since=args.since,
                )
            )
        except PathNotTracked:
            print(
                f"append-only: {path} has no history in this repository "
                "(wrong path, or never committed?)",
                file=sys.stderr,
            )
            return EXIT_ERROR
        except GitError as exc:
            print(f"append-only: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if args.json and not args.quiet:
        payload = {
            "ok": all(r.ok for r in reports),
            "reports": [_report_to_dict(r) for r in reports],
        }
        print(json.dumps(payload, indent=2), file=out)
    elif not args.quiet:
        for index, report in enumerate(reports):
            if index:
                print(file=out)
            _print_human(report, out)

    return EXIT_OK if all(r.ok for r in reports) else EXIT_VIOLATIONS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
