"""The actual check: was this file ever changed in a way that wasn't an append?"""

from __future__ import annotations

import difflib
import re
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


class NothingChecked(Exception):
    """Commits touched the path, but no version of it could be checked.

    This is the failure this tool exists to avoid, turned inward: a run that
    inspected nothing must not be reported as a clean run. ``reason`` is
    ``"not-a-file"`` (a directory, a submodule) or ``"all-exempt"`` (the
    preamble swallowed the whole file in every version).
    """

    def __init__(self, path: str, reason: str, commits: int) -> None:
        super().__init__(path)
        self.path = path
        self.reason = reason
        self.commits = commits


@dataclass(frozen=True)
class Preamble:
    """The exempt region at the top of the file: a title, a legend, a preamble.

    Two ways to say where it ends, and they are not equally good.

    ``lines`` is a fixed count. It is wrong the moment anyone adds a line to the
    preamble: every body line shifts by one, the exempt slice stops describing
    the same region in the old and new versions, and you get a violation for a
    legal edit -- permanently, since that commit is in history forever.

    ``after`` is a regex matching the first *entry*, and everything above it is
    exempt. The region is recomputed for every version of the file, so editing
    the preamble moves the boundary instead of shifting the body underneath it.
    That self-maintaining property is the entire reason it exists. Naming the
    shape of an entry -- ``^## `` for a changelog, ``^\\d{4}-`` for a dated
    ledger -- is also a fact about the file's format rather than a line count
    that happens to be true today.
    """

    lines: int = 0
    after: re.Pattern[str] | None = None

    @property
    def configured(self) -> bool:
        return self.lines > 0 or self.after is not None

    def size(self, file_lines: list[bytes]) -> int:
        """How many lines at the top of *this version* are exempt."""
        if self.after is None:
            return self.lines
        for index, line in enumerate(file_lines):
            text = line.decode("utf-8", "replace").rstrip("\n")
            if self.after.search(text):
                return index
        # No entry in this version, so there is nothing here to protect yet --
        # the commits that built the preamble before the first entry arrived are
        # not violations. This cannot be used to hide anything: a version that
        # loses its entries is compared against a parent that had them and is
        # reported as a truncation, and a pattern that matches in no version at
        # all leaves the whole run with nothing checked, which is an error.
        return len(file_lines)


@dataclass(frozen=True)
class Violation:
    commit: Commit
    kind: str  # "rewrote" | "truncated" | "inserted" | "deleted"
    path: str
    # 1-based line where it diverged, in the previous version of the file --
    # except for "inserted", which is a line in the merge result, because that
    # is where the content that came from nowhere actually is.
    line: int | None
    excerpt: str

    def describe(self) -> str:
        if self.kind == "deleted":
            return "deleted the file"
        where = f"line {self.line}" if self.line is not None else "the file"
        if self.kind == "truncated":
            return f"cut the file off at {where}"
        if self.kind == "inserted":
            return f"merged in new content at {where}, above existing entries"
        return f"changed existing content at {where}"


@dataclass
class Report:
    path: str
    mode: Mode
    preamble: Preamble
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


def _body(content: bytes, preamble: Preamble) -> tuple[list[bytes], int]:
    """The checkable part of this version, and how many lines sit above it."""
    lines = _lines(content)
    exempt = preamble.size(lines)
    return lines[exempt:], exempt


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
    old: bytes, new: bytes, mode: Mode, preamble: Preamble
) -> tuple[str, int | None, str] | None:
    """Return (kind, line, excerpt) if this change broke the rule, else None.

    The exempt region is measured separately in each version, so a commit that
    edits the preamble does not shift the body out from under the comparison.
    """
    old_body, old_exempt = _body(old, preamble)
    new_body, _ = _body(new, preamble)

    if not old_body:
        # Nothing below the preamble yet, so anything is a legal first append.
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
        line = old_exempt + index + 1
    else:
        if len(new_body) >= len(old_body) and new_body[-len(old_body) :] == old_body:
            return None
        limit = min(len(old_body), len(new_body))
        index = next(
            (i for i in range(1, limit + 1) if old_body[-i] != new_body[-i]),
            limit + 1,
        )
        # Counting back from the end of the *previous* version.
        line = old_exempt + len(old_body) - index + 1

    # "Truncated" means the file was cut short at the end where the old content
    # lives -- what survives is a prefix of what was there. That test is the
    # same in both modes; deriving it from where the diff ran off instead is
    # what got the two labels swapped in prepend mode.
    truncated = len(new_body) < len(old_body) and new_body == old_body[: len(new_body)]
    return ("truncated" if truncated else "rewrote", line, _excerpt(old_body, new_body))


def _embed(needle: list[bytes], haystack: list[bytes]) -> tuple[list[int], int]:
    """Match ``needle`` into ``haystack`` as a subsequence, leftmost first.

    Returns the positions used and how many elements were placed. A count below
    ``len(needle)`` means no embedding exists at all: greedy leftmost matching
    is complete for subsequences, so if it fails, nothing else would have
    worked either.
    """
    positions: list[int] = []
    cursor = 0
    for item in needle:
        while cursor < len(haystack) and haystack[cursor] != item:
            cursor += 1
        if cursor == len(haystack):
            break
        positions.append(cursor)
        cursor += 1
    return positions, len(positions)


def _check_merge(
    olds: list[bytes], new: bytes, mode: Mode, preamble: Preamble
) -> tuple[str, int | None, str] | None:
    """Did this merge keep everything all of its parents had?

    A merge cannot be judged by the single-parent rule. Two branches that each
    appended an entry produce a result that is not a prefix-extension of either
    side, and that is a perfectly legal merge. The rule that fits: every
    parent's body must still be present in the result, in order, and nothing
    may have been slipped in above the last line any parent needs.

    Testing "does the result agree with *any* parent" instead is what let a
    merge resolved with "keep ours" throw away the other side's entries and
    still pass. Testing *every* parent for a prefix is the opposite mistake --
    it fails the concurrent-append merge above.

    For a single parent this is exactly the prefix rule: if a body embeds with
    no gaps below its own last line, its positions are 0..n and it is a prefix.
    """
    new_body, new_exempt = _body(new, preamble)
    # Both modes are the same problem read in opposite directions.
    result = new_body if mode is Mode.APPEND else new_body[::-1]

    covered: set[int] = set()
    furthest = -1
    for old in olds:
        old_body, _ = _body(old, preamble)
        if not old_body:
            continue
        kept = old_body if mode is Mode.APPEND else old_body[::-1]
        positions, matched = _embed(kept, result)
        if matched < len(kept):
            # This parent's entries did not survive. _compare tells the better
            # story -- it finds where the two stopped agreeing. It cannot come
            # back clean here: a body that is a prefix of the result also
            # embeds in it.
            return _compare(old, new, mode, preamble)
        covered.update(positions)
        if positions:
            furthest = max(furthest, positions[-1])

    for index in range(furthest):
        if index in covered:
            continue
        # A line no parent brought, sitting above content that a parent needs:
        # somebody wrote it during the merge, in the middle of the ledger.
        offset = index if mode is Mode.APPEND else len(new_body) - 1 - index
        first, _ = _body(olds[0], preamble)
        return ("inserted", new_exempt + offset + 1, _excerpt(first, new_body))

    return None


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
    after: str | None = None,
    follow: bool = True,
    since: str | None = None,
) -> Report:
    """Walk the history of ``path`` and report every change that wasn't an append."""
    preamble = Preamble(lines=header, after=re.compile(after) if after else None)
    history = file_history(path, cwd, follow=follow, since=since)
    report = Report(path=path, mode=mode, preamble=preamble)

    if not history:
        if since is not None:
            # An empty range is a legitimate "nothing happened", not a typo.
            return report
        raise PathNotTracked(path)

    # Whether this run ever had anything to look at. Both of these staying
    # false means the answer is "I don't know", which is a 2, not a 0.
    saw_blob = False
    saw_body = False

    for entry in history:
        commit, path_here = entry.commit, entry.path
        new = blob_at(commit.sha, path_here, cwd)
        report.commits_checked += 1

        olds = []
        for parent in commit.parents:
            old = blob_at(parent, _parent_path(entry, parent), cwd)
            if old is not None:
                olds.append(old)
                saw_blob = True
                saw_body = saw_body or bool(_body(old, preamble)[0])

        if new is None:
            # Gone in this commit. That is only news if a parent had it.
            if olds:
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

        saw_blob = True
        saw_body = saw_body or bool(_body(new, preamble)[0])

        if not olds:
            # Created here, as far as every parent is concerned: no prior
            # content that this commit could have failed to keep.
            continue

        # Parents that never had the file are not evidence of anything, so a
        # merge is judged only against the parents that did.
        if len(olds) > 1:
            result = _check_merge(olds, new, mode, preamble)
        else:
            result = _compare(olds[0], new, mode, preamble)

        if result is not None:
            kind, line, excerpt = result
            report.violations.append(
                Violation(
                    commit=commit,
                    kind=kind,
                    path=path_here,
                    line=line,
                    excerpt=excerpt,
                )
            )

    if not saw_blob:
        raise NothingChecked(path, "not-a-file", report.commits_checked)
    if preamble.configured and not saw_body:
        # A --header nobody can satisfy, or an --after that matches no line in
        # any version. Either way the preamble ate the file and the run has no
        # opinion about anything.
        raise NothingChecked(path, "all-exempt", report.commits_checked)

    # Oldest first reads better in a report: it is the order things happened.
    report.violations.reverse()
    return report
