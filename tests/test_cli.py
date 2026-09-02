"""Command line behaviour, with particular attention to exit codes.

If you cannot tell "the ledger was rewritten" from "the check never ran", the
CI gate is decorative. So each of 0, 1 and 2 gets its own test.
"""

from __future__ import annotations

import json
import os

import pytest

from append_only.cli import EXIT_ERROR, EXIT_OK, EXIT_VIOLATIONS, main


def run(repo, *args):
    return main(["-C", repo.path, *args])


# --- exit codes -----------------------------------------------------------


def test_clean_history_exits_0(repo, capsys):
    repo.commit_file("LEDGER.md", "one\n", "add")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "append")

    assert run(repo, "LEDGER.md") == EXIT_OK
    assert "clean" in capsys.readouterr().out


def test_violation_exits_1(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md") == EXIT_VIOLATIONS


def test_missing_file_exits_2_not_0(repo, capsys):
    """The failure that would make the whole tool a liability."""
    repo.commit_file("LEDGER.md", "one\n", "add")

    assert run(repo, "TYPO.md") == EXIT_ERROR
    assert "no history" in capsys.readouterr().err


def test_not_a_git_repo_exits_2(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert main(["-C", str(plain), "LEDGER.md"]) == EXIT_ERROR
    assert capsys.readouterr().err.strip()


def test_missing_directory_exits_2(capsys):
    assert main(["-C", "/no/such/place", "LEDGER.md"]) == EXIT_ERROR
    assert "no such directory" in capsys.readouterr().err


def test_unknown_revision_exits_2(repo, capsys):
    repo.commit_file("LEDGER.md", "one\n", "add")

    assert run(repo, "--since", "nope-not-a-rev", "LEDGER.md") == EXIT_ERROR
    assert capsys.readouterr().err.strip()


def test_unknown_revision_is_explained_in_words(repo, capsys):
    """This fires when CI cloned shallow, so it has to say that."""
    repo.commit_file("LEDGER.md", "one\n", "add")

    run(repo, "--since", "origin/main", "LEDGER.md")
    err = capsys.readouterr().err

    assert "no such revision" in err
    assert "fetch-depth" in err
    assert "rev-parse" not in err, "this is a message for a person, not a shell log"


def test_since_with_an_empty_range_exits_0(repo):
    """A pull request that doesn't touch the ledger has to pass."""
    repo.commit_file("LEDGER.md", "one\n", "add")
    base = repo.git("rev-parse", "HEAD").strip()
    repo.commit_file("other.txt", "unrelated\n", "something else")

    assert run(repo, "--since", base, "LEDGER.md") == EXIT_OK


def test_since_does_not_turn_a_typo_into_a_clean_pass(repo, capsys):
    """--since used to launder a bad path from a 2 into a 0.

    Reachable from the line the README recommends for CI, which is what made it
    the worst of the "reported clean without checking anything" family.
    """
    repo.commit_file("LEDGER.md", "one\n", "add")
    repo.commit_file("LEDGER.md", "one\ntwo\n", "append")
    base = repo.git("rev-parse", "HEAD~1").strip()

    assert run(repo, "TYPO.md") == EXIT_ERROR
    capsys.readouterr()

    assert run(repo, "--since", base, "TYPO.md") == EXIT_ERROR
    assert "no history" in capsys.readouterr().err


def test_since_with_a_path_created_after_the_revision_still_checks_it(repo):
    """The file has no history *before* the range, which is not a typo."""
    repo.commit_file("other.txt", "unrelated\n", "first")
    base = repo.git("rev-parse", "HEAD").strip()
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add the ledger")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "--since", base, "LEDGER.md") == EXIT_VIOLATIONS


def test_header_over_run_exits_2_not_0(repo, capsys):
    """The likeliest misconfiguration there is, and it used to pass silently."""
    repo.commit_file("LEDGER.md", "# Title\n\none\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "# Title\n\none\nEDITED\n", "rewrite")

    assert run(repo, "--header", "2", "LEDGER.md") == EXIT_VIOLATIONS
    assert run(repo, "--header", "9999", "LEDGER.md") == EXIT_ERROR
    assert "exempts the whole of LEDGER.md" in capsys.readouterr().err


def test_an_after_pattern_that_matches_nothing_exits_2_not_0(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "--after", "^NOPE", "LEDGER.md") == EXIT_ERROR
    assert "matched --after" in capsys.readouterr().err


def test_a_directory_exits_2_not_0(repo, capsys):
    os.mkdir(f"{repo.path}/notes")
    repo.commit_file("notes/a.md", "one\n", "add a note")

    assert run(repo, "notes") == EXIT_ERROR
    assert "is not a file" in capsys.readouterr().err


def test_after_and_header_cannot_both_be_given(repo):
    repo.commit_file("LEDGER.md", "one\n", "add")

    with pytest.raises(SystemExit):
        run(repo, "--after", "^---$", "--header", "2", "LEDGER.md")


def test_a_broken_after_regex_exits_2(repo, capsys):
    repo.commit_file("LEDGER.md", "one\n", "add")

    assert run(repo, "--after", "^(unclosed", "LEDGER.md") == EXIT_ERROR
    assert "not a valid regular expression" in capsys.readouterr().err


def test_negative_header_exits_2(repo, capsys):
    repo.commit_file("LEDGER.md", "one\n", "add")

    assert run(repo, "--header", "-1", "LEDGER.md") == EXIT_ERROR
    assert "--header" in capsys.readouterr().err


# --- output ---------------------------------------------------------------


def test_human_output_names_the_commit_and_line(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "quietly fix a typo")

    run(repo, "LEDGER.md")
    out = capsys.readouterr().out

    assert "quietly fix a typo" in out
    assert "line 2" in out
    assert "Test Person" in out


def test_json_output_is_valid_and_complete(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    code = run(repo, "--json", "LEDGER.md")
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_VIOLATIONS
    assert payload["ok"] is False
    report = payload["reports"][0]
    assert report["path"] == "LEDGER.md"
    assert report["violations"][0]["kind"] == "rewrote"
    assert report["violations"][0]["line"] == 2


def test_json_records_which_preamble_rule_was_used(repo, capsys):
    repo.commit_file("LEDGER.md", "# Title\n---\none\n", "add")
    repo.commit_file("LEDGER.md", "# Title\n---\none\ntwo\n", "append")

    run(repo, "--json", "--after", "^---$", "LEDGER.md")
    report = json.loads(capsys.readouterr().out)["reports"][0]

    assert report["after"] == "^---$"
    assert report["header"] == 0


def test_quiet_prints_nothing_but_still_signals(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    code = run(repo, "--quiet", "LEDGER.md")

    assert code == EXIT_VIOLATIONS
    assert capsys.readouterr().out == ""


# --- several files at once ------------------------------------------------


def test_all_paths_are_reported_before_exiting(repo, capsys):
    repo.commit_file("A.md", "one\ntwo\n", "add a")
    repo.commit_file("B.md", "one\ntwo\n", "add b")
    repo.commit_file("A.md", "one\nEDITED\n", "rewrite a")
    repo.commit_file("B.md", "one\ntwo\nthree\n", "append to b")

    code = run(repo, "A.md", "B.md")
    out = capsys.readouterr().out

    assert code == EXIT_VIOLATIONS
    # B is clean, but it still gets reported rather than being skipped once A
    # has already failed.
    assert "A.md" in out and "B.md" in out
    assert "clean" in out


def test_one_bad_path_does_not_hide_behind_a_good_one(repo):
    repo.commit_file("A.md", "one\n", "add a")
    repo.commit_file("B.md", "one\ntwo\n", "add b")
    repo.commit_file("B.md", "one\nEDITED\n", "rewrite b")

    assert run(repo, "A.md", "B.md") == EXIT_VIOLATIONS


def test_a_typo_in_the_second_path_still_exits_2(repo):
    """Exit 2 must win over exit 1: a check that didn't run isn't a result."""
    repo.commit_file("A.md", "one\ntwo\n", "add a")
    repo.commit_file("A.md", "one\nEDITED\n", "rewrite a")

    assert run(repo, "A.md", "TYPO.md") == EXIT_ERROR
