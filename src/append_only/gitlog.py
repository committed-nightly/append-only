"""Thin wrappers over the ``git`` binary.

Everything here shells out. There is no libgit2, no GitPython, no vendored
object parser -- the whole point of this tool is that it agrees with what
``git log`` says, so it asks git.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

# Separators for our --format string. They are written as git's %xNN escapes
# rather than as literal control characters, because an argv element cannot
# contain a NUL byte -- passing "\x00" here fails before git even starts.
_REC = "\x01"
_FLD = "\x00"

_FORMAT = "%x01" + "%x00".join(["%H", "%P", "%an", "%aI", "%s"])


class GitError(RuntimeError):
    """git was missing, unhappy, or pointed at something that isn't a repo."""


@dataclass(frozen=True)
class Change:
    """One entry from ``--name-status`` for a commit."""

    status: str  # A, M, D, R100, ...
    path: str
    old_path: str | None = None  # set for renames and copies


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author: str
    date: str  # ISO 8601, as git formatted it
    subject: str
    changes: tuple[Change, ...] = field(default=())

    @property
    def short(self) -> str:
        return self.sha[:9]


@dataclass(frozen=True)
class HistoryEntry:
    """A commit that touched the file, plus what the file was called there."""

    commit: Commit
    path: str  # name at this commit
    parent_path: str  # name in the first parent; differs only across a rename


def run_git(args: list[str], cwd: str) -> str:
    """Run git and return stdout, or raise GitError."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the box
        raise GitError("git is not on PATH") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(stderr or f"git {' '.join(args)} failed")
    return proc.stdout.decode("utf-8", "replace")


def repo_root(cwd: str) -> str:
    """Absolute path of the working tree containing ``cwd``."""
    return run_git(["rev-parse", "--show-toplevel"], cwd).strip()


def resolve(rev: str, cwd: str) -> str:
    """Resolve a revision to a full sha, raising GitError if it is unknown."""
    return run_git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd).strip()


def blob_at(sha: str, path: str, cwd: str) -> bytes | None:
    """Contents of ``path`` at ``sha``, or None if it did not exist there.

    Returns None for a path that exists but is not a regular file (a directory,
    a submodule), since "append-only" is meaningless for those.
    """
    spec = f"{sha}:{path}"
    try:
        kind = run_git(["cat-file", "-t", spec], cwd).strip()
    except GitError:
        return None
    if kind != "blob":
        return None
    proc = subprocess.run(
        ["git", "cat-file", "blob", spec], cwd=cwd, capture_output=True, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_changes(lines: list[str]) -> tuple[Change, ...]:
    changes: list[Change] = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            changes.append(Change(status=status, path=parts[2], old_path=parts[1]))
        elif len(parts) >= 2:
            changes.append(Change(status=status, path=parts[1]))
    return tuple(changes)


def _log(path: str, cwd: str, *, follow: bool, since: str | None) -> list[Commit]:
    args = [
        # Without this git escapes non-ASCII paths into C-style quoted strings
        # and our tab split stops lining up with reality.
        "-c",
        "core.quotePath=false",
        "log",
        f"--format={_FORMAT}",
        "--name-status",
    ]
    if follow:
        args.append("--follow")
    if since:
        args.append(f"{since}..HEAD")
    args += ["--", path]

    out = run_git(args, cwd)
    commits: list[Commit] = []
    for record in out.split(_REC):
        if not record.strip():
            continue
        head, _, rest = record.partition("\n")
        fields = head.split(_FLD)
        if len(fields) < 5:
            continue
        sha, parents, author, date, subject = fields[:5]
        commits.append(
            Commit(
                sha=sha,
                parents=tuple(p for p in parents.split() if p),
                author=author,
                date=date,
                subject=subject,
                changes=_parse_changes(rest.splitlines()),
            )
        )
    return commits


def rename_chain(path: str, cwd: str, *, since: str | None = None) -> list[str]:
    """Every name this file has had, newest name first.

    Rename detection is git's own, similarity threshold and all: a commit that
    renames a file *and* rewrites most of it is not recognised as a rename by
    git, and so is not recognised as one here either.
    """
    names = [path]
    current = path
    for commit in _log(path, cwd, follow=True, since=since):
        for change in commit.changes:
            if change.path == current and change.old_path:
                current = change.old_path
                names.append(current)
                break
    return names


def file_history(
    path: str,
    cwd: str,
    *,
    follow: bool = True,
    since: str | None = None,
) -> list[HistoryEntry]:
    """Commits that touched ``path``, newest first, including merge commits.

    ``git log --follow`` is used only to work out the file's previous names. It
    is deliberately not used to enumerate the commits, because ``--follow``
    drops merge commits from its output entirely -- which would hide a merge
    that resolved a conflict by throwing away half the file. Each name in the
    chain gets its own plain ``git log``, which does report merges.
    """
    names = rename_chain(path, cwd, since=since) if follow else [path]
    # (sha, new name) -> previous name, for the commits that did the renaming.
    renamed_from: dict[tuple[str, str], str] = {}
    # The same commits, keyed by their *old* name. A rename shows up in the old
    # name's log as a plain deletion, and reporting that as "deleted the file"
    # would flag every rename; the new name's segment already covers it.
    rename_shas: set[tuple[str, str]] = set()
    if follow:
        for newer, older in zip(names, names[1:]):
            for commit in _log(newer, cwd, follow=True, since=since):
                for change in commit.changes:
                    if change.path == newer and change.old_path == older:
                        renamed_from[(commit.sha, newer)] = older
                        rename_shas.add((commit.sha, older))

    entries: list[HistoryEntry] = []
    seen: set[tuple[str, str]] = set()
    # Names are newest first and their histories do not overlap in time, so
    # concatenating the segments keeps the whole list newest first.
    for name in names:
        for commit in _log(name, cwd, follow=False, since=since):
            key = (commit.sha, name)
            if key in seen or key in rename_shas:
                continue
            seen.add(key)
            entries.append(
                HistoryEntry(
                    commit=commit,
                    path=name,
                    parent_path=renamed_from.get(key, name),
                )
            )
    return entries
