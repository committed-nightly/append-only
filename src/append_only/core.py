"""The actual check: was this file ever changed in a way that wasn't an append?"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum

from .gitlog import Commit, HistoryEntry, blob_at, file_history

# How many lines of unified diff to keep in a violation excerpt. Enough to see
# what happened, not so much that a CI log becomes the file itself.
_EXCERPT_LINES = 14


class Mode(str, Enum):
    """Which end of the file new content is allowed to arrive at."""

    APPEND = "append"
    PREPEND = "prepend"


class PathNotTracked(Exception):
    """The path has no history at all -- almost always a typo in the path."""


@dataclass(frozen=True)
class Violation:
    commit: Commit
    kind: str  # "rewrote" | "truncated" | "deleted"
    path: str
    line: int | None  # 1-based line in the previous version, where it diverged
    excerpt: str

    def describe(self) -> str:
        if self.kind == "deleted":
            return "deleted the file"
        where = f"line {self.line}" if self.line is not None else "the file"
        if self.kind == "truncated":
            return f"cut the file off at {where}"
        return f"changed existing content at {where}"


@dataclass
class Report:
    path: str
    mode: Mode
    header: int
    commits_checked: int = 0
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _lines(content: bytes) -> list[bytes]:
    """Split into lines, normalising a missing trailing newline.

    A file that does not end in a newline gets one before comparison. Appending
    to such a file necessarily adds that newline, and without this every single
    append to a no-trailing-newline file would read as a rewrite of its last
    line.
    """
    if content and not content.endswith(b"\n"):
        content += b"\n"
    return content.splitlines(keepends=True)


def _decode(lines: list[bytes]) -> list[str]:
    return [line.decode("utf-8", "replace") for line in lines]


def _excerpt(old_body: list[bytes], new_body: list[bytes]) -> str:
    diff = difflib.unified_diff(
        _decode(old_body),
        _decode(new_body),
        fromfile="before",
        tofile="after",
        n=1,
    )
    kept = [line.rstrip("\n") for line in list(diff)[:_EXCERPT_LINES]]
    return "\n".join(kept)


def _compare(
    old: bytes, new: bytes, mode: Mode, header: int
) -> tuple[str, int | None, str] | None:
    """Return (kind, line, excerpt) if this change broke the rule, else None."""
    old_lines, new_lines = _lines(old), _lines(new)
    old_body, new_body = old_lines[header:], new_lines[header:]

    if not old_body:
        # Nothing below the header yet, so anything is a legal first append.
        return None

    if mode is Mode.APPEND:
        if len(new_body) >= len(old_body) and new_body[: len(old_body)] == old_body:
            return None
        # Where did they stop agreeing?
        limit = min(len(old_body), len(new_body))
        index = next(
            (i for i in range(limit) if old_body[i] != new_body[i]),
            limit,
        )
        line = header + index + 1
    else:
        if len(new_body) >= len(old_body) and new_body[-len(old_body) :] == old_body:
            return None
        limit = min(len(old_body), len(new_body))
        index = next(
            (i for i in range(1, limit + 1) if old_body[-i] != new_body[-i]),
            limit + 1,
        )
        # Counting back from the end of the *previous* version.
        line = header + len(old_body) - index + 1

    truncated = len(new_body) < len(old_body) and index >= limit
    return ("truncated" if truncated else "rewrote", line, _excerpt(old_body, new_body))


def _parent_path(entry: HistoryEntry, parent: str) -> str:
    """What this file was called in ``parent``.

    Rename information describes the first-parent edge, so that is the only
    edge we apply it to.
    """
    if entry.commit.parents and parent == entry.commit.parents[0]:
        return entry.parent_path
    return entry.path


def check_file(
    path: str,
    cwd: str,
    *,
    mode: Mode = Mode.APPEND,
    header: int = 0,
    follow: bool = True,
    since: str | None = None,
) -> Report:
    """Walk the history of ``path`` and report every change that wasn't an append."""
    history = file_history(path, cwd, follow=follow, since=since)
    report = Report(path=path, mode=mode, header=header)

    if not history:
        if since is not None:
            # An empty range is a legitimate "nothing happened", not a typo.
            return report
        raise PathNotTracked(path)

    for entry in history:
        commit, path_here = entry.commit, entry.path
        new = blob_at(commit.sha, path_here, cwd)
        report.commits_checked += 1

        if new is None:
            # Gone in this commit. That is only news if a parent had it.
            had_it = any(
                blob_at(parent, _parent_path(entry, parent), cwd) is not None
                for parent in commit.parents
            )
            if had_it:
                report.violations.append(
                    Violation(
                        commit=commit,
                        kind="deleted",
                        path=path_here,
                        line=None,
                        excerpt="",
                    )
                )
            continue

        findings = []
        for parent in commit.parents:
            old = blob_at(parent, _parent_path(entry, parent), cwd)
            if old is None:
                # Created relative to this parent: no prior content to keep.
                findings = []
                break
            result = _compare(old, new, mode, header)
            if result is None:
                # Agrees with at least one parent. Good enough.
                findings = []
                break
            findings.append(result)

        if findings:
            kind, line, excerpt = findings[0]
            report.violations.append(
                Violation(
                    commit=commit,
                    kind=kind,
                    path=path_here,
                    line=line,
                    excerpt=excerpt,
                )
            )

    # Oldest first reads better in a report: it is the order things happened.
    report.violations.reverse()
    return report
