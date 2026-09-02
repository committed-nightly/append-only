"""Behaviour of the check itself.

These build real repositories and run real git, because the only interesting
bugs in this tool are disagreements with git.
"""

from __future__ import annotations

import os

import pytest

from append_only.core import Mode, NothingChecked, PathNotTracked, check_file


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


def test_header_larger_than_the_file_refuses_to_pass(repo):
    """An N nobody can satisfy exempts everything, and that is not "clean"."""
    repo.commit_file("LEDGER.md", "one\n", "add")
    repo.commit_file("LEDGER.md", "totally different\n", "replace")

    with pytest.raises(NothingChecked) as caught:
        check_file("LEDGER.md", repo.path, header=50)

    assert caught.value.reason == "all-exempt"


def test_a_header_that_fits_is_still_fine(repo):
    """The guard above must not fire on a preamble that leaves a real body."""
    repo.commit_file("LEDGER.md", "# Title\n\none\n", "add")
    repo.commit_file("LEDGER.md", "# Title\n\none\ntwo\n", "append")

    assert check_file("LEDGER.md", repo.path, header=2).ok


def test_an_empty_file_with_no_preamble_is_simply_clean(repo):
    """Nothing to check because the file is empty is not a misconfiguration."""
    repo.commit_file("LEDGER.md", "", "add an empty ledger")

    assert check_file("LEDGER.md", repo.path).ok


# --- the --after exemption ------------------------------------------------


def test_after_exempts_everything_above_the_first_entry(repo):
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n2026-01-02 two\n", "add")

    assert check_file("LEDGER.md", repo.path, after=r"^\d{4}-").ok


def test_after_still_checks_the_line_it_matched(repo):
    """The marker is the first entry, not the last preamble line."""
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 ONE\n", "edit the first entry")

    report = check_file("LEDGER.md", repo.path, after=r"^\d{4}-")

    assert not report.ok
    assert report.violations[0].line == 3


def test_after_survives_a_line_being_added_to_the_preamble(repo):
    """The case --header gets permanently wrong, and the reason --after exists.

    Adding a preamble line shifts every entry down by one. A fixed count then
    compares entry N against entry N-1 and reports a violation for an edit it
    was configured to permit -- forever, since the commit stays in history.
    """
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n2026-01-02 two\n", "add")
    repo.commit_file(
        "LEDGER.md",
        "# Title\nnow with a note\n\n2026-01-01 one\n2026-01-02 two\n",
        "document the format",
    )

    assert check_file("LEDGER.md", repo.path, after=r"^\d{4}-").ok
    # The same history under --header, which is what this argument is about.
    assert not check_file("LEDGER.md", repo.path, header=2).ok


def test_after_does_not_excuse_edits_below_the_marker(repo):
    repo.commit_file("LEDGER.md", "# Title\n2026-01-01 one\ntwo\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n2026-01-01 one\nTWO\n", "edit an entry")

    report = check_file("LEDGER.md", repo.path, after=r"^\d{4}-")

    assert not report.ok
    assert report.violations[0].line == 3


def test_after_reports_the_line_number_from_the_version_it_broke(repo):
    """Line numbers follow the old version's own preamble, not the new one's."""
    repo.commit_file("LEDGER.md", "# Title\n2026-01-01 one\ntwo\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\nnote\n2026-01-01 one\nTWO\n", "grow, edit")

    report = check_file("LEDGER.md", repo.path, after=r"^\d{4}-")

    # "two" was line 3 in the version that was damaged, even though the entry
    # sits on line 4 afterwards.
    assert report.violations[0].line == 3


def test_after_that_matches_nothing_anywhere_refuses_to_pass(repo):
    """A pattern that never matches exempts everything, so it must not say 0."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add ledger")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    with pytest.raises(NothingChecked) as caught:
        check_file("LEDGER.md", repo.path, after=r"^\d{4}-")

    assert caught.value.reason == "all-exempt"


def test_after_tolerates_the_days_before_the_first_entry(repo):
    """Building the preamble is not a violation, and never becomes one.

    --header gets this permanently wrong in the other direction: those early
    commits stay in history, so a stale N reports them forever.
    """
    repo.commit_file("LEDGER.md", "# Title\n\n", "start a ledger")
    repo.commit_file("LEDGER.md", "# Title\nnow with a note\n\n", "expand it")
    repo.commit_file("LEDGER.md", "# Title\nnow with a note\n\n2026-01-01 one\n", "1st")
    repo.commit_file(
        "LEDGER.md", "# Title\nnow with a note\n\n2026-01-01 one\n2026-01-02 two\n", "2nd"
    )

    assert check_file("LEDGER.md", repo.path, after=r"^\d{4}-").ok


def test_after_still_catches_a_ledger_emptied_back_to_its_preamble(repo):
    """Deleting every entry removes the marker; that must not exempt the file."""
    repo.commit_file("LEDGER.md", "# Title\n\n2026-01-01 one\n", "add ledger")
    repo.commit_file("LEDGER.md", "# Title\n\n", "wipe the entries")

    report = check_file("LEDGER.md", repo.path, after=r"^\d{4}-")

    assert not report.ok
    assert report.violations[0].kind == "truncated"


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


def test_prepend_losing_the_oldest_entries_is_a_truncation(repo):
    """The bottom of a prepend file is its far end: cutting it off is a cut."""
    repo.commit_file("CHANGELOG.md", "## 0.3\n## 0.2\n## 0.1\n", "add")
    repo.commit_file("CHANGELOG.md", "## 0.3\n## 0.2\n", "drop the oldest release")

    report = check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND)

    assert report.violations[0].kind == "truncated"
    assert report.violations[0].line == 3


def test_prepend_losing_the_newest_entry_is_not_a_truncation(repo):
    """Nothing was cut off the end here -- the head of the file was rewritten."""
    repo.commit_file("CHANGELOG.md", "## 0.3\n## 0.2\n## 0.1\n", "add")
    repo.commit_file("CHANGELOG.md", "## 0.2\n## 0.1\n", "unrelease 0.3")

    report = check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND)

    assert report.violations[0].kind == "rewrote"
    assert report.violations[0].line == 1


def test_append_truncation_keeps_its_label(repo):
    """The mirror of the two above, to pin the shared arithmetic down."""
    repo.commit_file("LEDGER.md", "one\ntwo\nthree\n", "add")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "drop the newest entry")

    assert check_file("LEDGER.md", repo.path).violations[0].kind == "truncated"


def test_prepend_merges_are_judged_from_the_top(repo):
    repo.commit_file("CHANGELOG.md", "## 0.1\n", "add")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("CHANGELOG.md", "## 0.2\n## 0.1\n", "release 0.2")
    repo.git("checkout", "-q", "main")
    repo.commit_file("CHANGELOG.md", "## 0.1.1\n## 0.1\n", "release 0.1.1")
    repo.merge("side")
    repo.write("CHANGELOG.md", "## 0.2\n## 0.1.1\n## 0.1\n")
    repo.commit("merge both releases")

    assert check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND).ok


def test_a_prepend_merge_that_loses_one_sides_release_is_caught(repo):
    repo.commit_file("CHANGELOG.md", "## 0.1\n", "add")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("CHANGELOG.md", "## 0.2\n## 0.1\n", "release 0.2")
    repo.git("checkout", "-q", "main")
    repo.commit_file("CHANGELOG.md", "## 0.1.1\n## 0.1\n", "release 0.1.1")
    repo.merge("side")
    # Resolved by taking the side branch whole. 0.1.1 is simply gone.
    repo.write("CHANGELOG.md", "## 0.2\n## 0.1\n")
    repo.commit("merge side")

    report = check_file("CHANGELOG.md", repo.path, mode=Mode.PREPEND)

    assert not report.ok
    assert report.violations[-1].commit.subject == "merge side"


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


def test_a_directory_raises_rather_than_passing(repo):
    """It has commits and no content, which looks exactly like a clean ledger."""
    os.mkdir(f"{repo.path}/notes")
    repo.commit_file("notes/a.md", "one\n", "add a note")
    repo.commit_file("notes/b.md", "two\n", "add another")

    with pytest.raises(NothingChecked) as caught:
        check_file("notes", repo.path)

    assert caught.value.reason == "not-a-file"
    assert caught.value.commits == 2


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


def test_a_file_copied_from_another_does_not_inherit_its_history(repo):
    """git reports an identical new file as a copy; a copy is not a rename."""
    repo.commit_file("A.md", "one\ntwo\n", "add a")
    repo.commit_file("B.md", "one\ntwo\n", "add b, identical to a")
    repo.commit_file("A.md", "one\nEDITED\n", "rewrite a")
    repo.commit_file("B.md", "one\ntwo\nthree\n", "honestly append to b")

    assert not check_file("A.md", repo.path).ok
    assert check_file("B.md", repo.path).ok


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


def test_a_merge_that_interleaves_both_sides_is_clean(repo):
    """Neither parent is a prefix of the result, and it is still a legal merge."""
    repo.commit_file("LEDGER.md", "base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "base\nside\n", "side entry")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "base\nmain\n", "main entry")
    repo.merge("side")
    repo.write("LEDGER.md", "base\nside\nmain\n")
    repo.commit("merge side, oldest entry first")

    report = check_file("LEDGER.md", repo.path)

    assert report.ok, [v.describe() for v in report.violations]


def test_a_merge_may_still_append_something_new_of_its_own(repo):
    repo.commit_file("LEDGER.md", "base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "base\nside\n", "side entry")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "base\nmain\n", "main entry")
    repo.merge("side")
    repo.write("LEDGER.md", "base\nmain\nside\nnoting the merge\n")
    repo.commit("merge side and record it")

    assert check_file("LEDGER.md", repo.path).ok


def test_a_merge_that_keeps_one_parent_wholesale_is_caught(repo):
    """The way a ledger actually loses entries: a conflict resolved "ours".

    Two things hid this. The merge is TREESAME to the side it kept, so git's
    default history simplification pruned it *and* the append it discarded --
    one commit reported out of three. And once the log was complete, accepting
    a result that matched any one parent passed it anyway, because the result
    was a verbatim copy of the parent that was kept.
    """
    repo.commit_file("LEDGER.md", "A\nB\n", "base")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("other.txt", "x\n", "unrelated work")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "A\nB\nC\n", "append C")
    repo.git("checkout", "-q", "side")
    repo.merge("main")
    repo.write("LEDGER.md", "A\nB\n")
    repo.commit("merge, kept ours")

    report = check_file("LEDGER.md", repo.path)

    assert not report.ok, "entry C is gone from the file and from the report"
    assert report.commits_checked == 3
    violation = report.violations[0]
    assert violation.kind == "truncated"
    assert violation.line == 3
    assert violation.commit.subject == "merge, kept ours"


def test_a_merge_cannot_slip_a_new_entry_between_existing_ones(repo):
    """Every parent survives, so the entries are all there -- but one is new."""
    repo.commit_file("LEDGER.md", "base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "base\nside\n", "side entry")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "base\nmain\n", "main entry")
    repo.merge("side")
    repo.write("LEDGER.md", "base\nbackdated\nmain\nside\n")
    repo.commit("merge side")

    report = check_file("LEDGER.md", repo.path)

    assert not report.ok
    assert report.violations[0].kind == "inserted"
    assert report.violations[0].line == 2


def test_a_merge_must_keep_a_file_only_one_side_ever_had(repo):
    """A parent without the file proves nothing; the parent with it still counts."""
    repo.commit_file("other.txt", "x\n", "unrelated base")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "start the ledger on a branch")
    repo.git("checkout", "-q", "main")
    repo.commit_file("other.txt", "x\ny\n", "carry on")
    repo.merge("side")
    repo.write("LEDGER.md", "one\nREWRITTEN\n")
    repo.commit("merge side")

    assert not check_file("LEDGER.md", repo.path).ok


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


def test_a_merge_of_duplicate_lines_can_report_a_false_inserted(repo):
    """A known limit, pinned so it can't change quietly.

    _embed places each parent at its leftmost positions, and a leftmost choice
    can leave a hole another placement would have filled. Here A is [a, b] and
    B is [a]; the result [a, a, b] accounts for both if B takes line 1 and A
    takes lines 2-3, but leftmost gives A lines 1 and 3 and calls line 2
    inserted. It needs genuinely duplicated lines, which is why real ledgers
    don't meet it -- see test_two_concurrent_appends_of_distinct_entries below.

    If someone fixes the algorithm this test fails, and the "Known limits"
    section of the README needs deleting in the same commit.
    """
    repo.commit_file("LEDGER.md", "a\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("other.txt", "unrelated\n", "side does something else")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "a\nb\n", "main appends b")
    repo.merge("side")
    repo.write("LEDGER.md", "a\na\nb\n")
    repo.commit("merge side")

    report = check_file("LEDGER.md", repo.path)

    assert [v.kind for v in report.violations] == ["inserted"]


def test_two_concurrent_appends_of_distinct_entries_are_clean(repo):
    """The shape the false positive above does *not* reach.

    Distinct entries -- dated, numbered, anything a real ledger has -- give
    _embed no ambiguity to resolve badly.
    """
    repo.commit_file("LEDGER.md", "2026-01-01 base\n", "add ledger")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit_file("LEDGER.md", "2026-01-01 base\n2026-01-02 side\n", "side")
    repo.git("checkout", "-q", "main")
    repo.commit_file("LEDGER.md", "2026-01-01 base\n2026-01-03 main\n", "main")
    repo.merge("side")
    repo.write("LEDGER.md", "2026-01-01 base\n2026-01-02 side\n2026-01-03 main\n")
    repo.commit("merge side")

    assert check_file("LEDGER.md", repo.path).ok


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


def test_since_with_an_untracked_path_is_an_error_not_a_clean_run(repo):
    """The two ways to get zero commits are not the same answer.

    "Nothing happened in this range" is clean. "I have never heard of this
    file" is an error, with or without --since.
    """
    repo.commit_file("LEDGER.md", "one\n", "add")
    head = repo.git("rev-parse", "HEAD").strip()

    with pytest.raises(PathNotTracked):
        check_file("TYPO.md", repo.path, since=head)


def test_since_with_an_untracked_path_is_an_error_without_follow(repo):
    """The existence check has to honour --no-follow like the real walk does."""
    repo.commit_file("LEDGER.md", "one\n", "add")
    head = repo.git("rev-parse", "HEAD").strip()

    with pytest.raises(PathNotTracked):
        check_file("TYPO.md", repo.path, follow=False, since=head)
