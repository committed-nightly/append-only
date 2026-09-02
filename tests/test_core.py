"""Behaviour of the check itself.

These build real repositories and run real git, because the only interesting
bugs in this tool are disagreements with git.
"""

from __future__ import annotations

import pytest

from append_only.core import Mode, PathNotTracked, check_file


def test_pure_appends_are_clean(repo):
    repo.commit_file("LEDGER.md", "one\n", "add ledger")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "append two")
    repo.commit_file("LEDGER.md", "one\ntwo\nthree\n", "append three")

    report = check_file("LEDGER.md", repo.path)

    assert report.ok
    assert report.commits_checked == 3


def test_editing_an_existing_line_is_a_violation(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add ledger")
    repo.commit_file("LEDGER.md", "one\nTWO\nthree\n", "sneak an edit in")

    report = check_file("LEDGER.md", repo.path)

    assert not report.ok
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.kind == "rewrote"
    assert violation.line == 2
    assert violation.commit.subject == "sneak an edit in"
    assert "-two" in violation.excerpt


def test_removing_a_line_from_the_end_is_reported_as_truncation(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\nthree\n", "add ledger")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "quietly drop the last line")

    report = check_file("LEDGER.md", repo.path)

    assert [v.kind for v in report.violations] == ["truncated"]
    assert report.violations[0].line == 3


def test_deleting_the_file_is_a_violation(repo):
    repo.commit_file("LEDGER.md", "one\n", "add ledger")
    repo.git("rm", "-q", "LEDGER.md")
    repo.commit("remove the ledger")

    report = check_file("LEDGER.md", repo.path)

    assert [v.kind for v in report.violations] == ["deleted"]
    assert report.violations[0].describe() == "deleted the file"


def test_creating_the_file_is_never_a_violation(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\nthree\n", "add a fully formed ledger")

    report = check_file("LEDGER.md", repo.path)

    assert report.ok


def test_violations_are_reported_oldest_first(repo):
    repo.commit_file("LEDGER.md", "a\nb\n", "add")
    repo.commit_file("LEDGER.md", "a\nX\n", "first offence")
    repo.commit_file("LEDGER.md", "a\nY\n", "second offence")

    report = check_file("LEDGER.md", repo.path)

    assert [v.commit.subject for v in report.violations] == [
        "first offence",
        "second offence",
    ]


# --- the header exemption -------------------------------------------------


def test_header_lines_may_change_freely(repo):
    repo.commit_file("LEDGER.md", "# Title\n\nentry one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Better title\n\nentry one\nentry two\n", "retitle")

    assert check_file("LEDGER.md", repo.path, header=2).ok
    # Without the exemption the same change is a violation on line 1.
    assert not check_file("LEDGER.md", repo.path).ok


def test_header_does_not_excuse_edits_below_it(repo):
    repo.commit_file("LEDGER.md", "# Title\n\nentry one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n\nentry ONE\n", "edit below the header")

    report = check_file("LEDGER.md", repo.path, header=2)

    assert not report.ok
    assert report.violations[0].line == 3


def test_header_larger_than_the_file_is_not_a_crash(repo):
    repo.commit_file("LEDGER.md", "one\n", "add")
    repo.commit_file("LEDGER.md", "totally different\n", "replace")

    assert check_file("LEDGER.md", repo.path, header=50).ok


# --- prepend mode ---------------------------------------------------------


def test_prepend_mode_accepts_new_entries_at_the_top(repo):
    repo.commit_file("CHANGELOG.md", "# Changelog\n\n## 0.1\n", "add changelog")
    repo.commit_file("CHANGELOG.md", "# Changelog\n\n## 0.2\n## 0.1\n", "release 0.2")

    assert check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND, header=2).ok


def test_prepend_mode_catches_an_edit_to_an_old_entry(repo):
    repo.commit_file("CHANGELOG.md", "# Changelog\n\n## 0.1\nfixed a bug\n", "add")
    repo.commit_file(
        "CHANGELOG.md", "# Changelog\n\n## 0.2\n## 0.1\nfixed a typo\n", "rewrite 0.1"
    )

    report = check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND, header=2)

    assert not report.ok
    assert report.violations[0].line == 4


def test_appending_to_a_prepend_file_is_a_violation(repo):
    repo.commit_file("CHANGELOG.md", "## 0.2\n## 0.1\n", "add")
    repo.commit_file("CHANGELOG.md", "## 0.2\n## 0.1\n## 0.3\n", "append at the bottom")

    assert not check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND).ok


# --- awkward files --------------------------------------------------------


def test_file_without_a_trailing_newline_can_still_be_appended_to(repo):
    """The regression this tool would otherwise trip over on nearly every repo."""
    repo.commit_file("LEDGER.md", "one\ntwo", "add ledger, no trailing newline")
    repo.commit_file("LEDGER.md", "one\ntwo\nthree\n", "append three")

    assert check_file("LEDGER.md", repo.path).ok


def test_empty_file_then_content_is_clean(repo):
    repo.commit_file("LEDGER.md", "", "add empty ledger")
    repo.commit_file("LEDGER.md", "one\n", "first entry")

    assert check_file("LEDGER.md", repo.path).ok


def test_unknown_path_raises_rather_than_passing(repo):
    repo.commit_file("LEDGER.md", "one\n", "add")

    with pytest.raises(PathNotTracked):
        check_file("NOT-A-FILE.md", repo.path)


# --- renames, branches, merges --------------------------------------------


def test_a_rename_alone_is_not_a_violation(repo):
    repo.commit_file("OLD.md", "one\ntwo\n", "add ledger")
    repo.git("mv", "OLD.md", "NEW.md")
    repo.commit("rename the ledger")
    repo.commit_file("NEW.md", "one\ntwo\nthree\n", "append under the new name")

    assert check_file("NEW.md", repo.path).ok


def test_an_edit_made_during_a_rename_is_still_caught(repo):
    # The file has to stay similar enough for git to call it a rename at all;
    # git's rename detection is what we inherit here, threshold and all.
    before = "".join(f"entry {n}\n" for n in range(10))
    after = before.replace("entry 3\n", "entry THREE\n")
    repo.commit_file("OLD.md", before, "add ledger")
    repo.git("rm", "-q", "OLD.md")
    repo.write("NEW.md", after)
    repo.commit("rename and edit in one go")

    report = check_file("NEW.md", repo.path)

    assert not report.ok
    assert report.violations[0].line == 4


def test_no_follow_stops_at_the_rename(repo):
    repo.commit_file("OLD.md", "one\ntwo\n", "add ledger")
    repo.git("mv", "OLD.md", "NEW.md")
    repo.commit("rename")

    # Without --follow the file looks brand new at the rename, so there is no
    # earlier content to have violated.
    report = check_file("NEW.md", repo.path, follow=False)

    assert report.ok


def test_a_merge_that_appends_both_sides_is_clean(repo):
    repo.commit_file("LEDGER.md", "base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "base\nside\n", "side entry")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "base\nmain\n", "main entry")
    # Resolve by keeping both, in an order that extends the main line.
    repo.merge("side")
    repo.write("LEDGER.md", "base\nmain\nside\n")
    repo.commit("merge side")

    report = check_file("LEDGER.md", repo.path)

    assert report.ok, [v.describe() for v in report.violations]


def test_a_merge_that_drops_one_sides_entries_is_caught(repo):
    repo.commit_file("LEDGER.md", "base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "base\nside\n", "side entry")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "base\nmain\n", "main entry")
    repo.merge("side")
    # Resolve the conflict by rewriting history: neither parent survives.
    repo.write("LEDGER.md", "base\nsomething else entirely\n")
    repo.commit("merge side")

    report = check_file("LEDGER.md", repo.path)

    assert not report.ok


# --- --since --------------------------------------------------------------


def test_since_ignores_older_violations(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "old offence")
    marker = repo.git("rev-parse", "HEAD").strip()
    repo.commit_file("LEDGER.md", "one\nEDITED\nthree\n", "honest append")

    assert check_file("LEDGER.md", repo.path, since=marker).ok
    assert not check_file("LEDGER.md", repo.path).ok


def test_since_with_no_commits_in_range_is_clean_not_an_error(repo):
    repo.commit_file("LEDGER.md", "one\n", "add")
    head = repo.git("rev-parse", "HEAD").strip()

    report = check_file("LEDGER.md", repo.path, since=head)

    assert report.ok
    assert report.commits_checked == 0
