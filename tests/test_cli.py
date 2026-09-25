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


# --- accepted exceptions --------------------------------------------------
#
# The property that justifies --allow existing at all is the one --since does
# not have: history *behind* the excused commit is still checked. Most of these
# tests are that property, approached from different sides.


def test_allow_turns_a_violation_into_a_pass(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md") == EXIT_VIOLATIONS
    assert run(repo, "LEDGER.md", "--allow", bad) == EXIT_OK


def test_an_allowed_commit_is_still_reported(repo, capsys):
    """Excused, not hidden. An exception nobody sees is a deleted check."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    run(repo, "LEDGER.md", "--allow", bad)
    out = capsys.readouterr().out

    assert "clean (1 allowed)" in out
    assert bad[:9] in out
    assert "allowed: no reason given" in out


def test_allow_does_not_stop_checking_earlier_commits(repo, capsys):
    """The whole point, and the thing --since cannot do."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nBAD\n", "an earlier rewrite")
    second = repo.commit_file("LEDGER.md", "one\nWORSE\n", "a later rewrite")

    # Excuse the later one; the earlier one must still fail the run.
    assert run(repo, "LEDGER.md", "--allow", second) == EXIT_VIOLATIONS

    # Moving the baseline past it instead is the remedy --allow replaces: the
    # run passes, having looked at nothing at all.
    capsys.readouterr()
    assert run(repo, "LEDGER.md", "--since", second) == EXIT_OK
    assert "0 commits checked, clean" in capsys.readouterr().out


def test_allow_does_not_stop_checking_later_commits(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    repo.commit_file("LEDGER.md", "one\nEDITED AGAIN\n", "rewrite again")

    assert run(repo, "LEDGER.md", "--allow", bad) == EXIT_VIOLATIONS


def test_allowing_every_violation_still_counts_the_commits(repo, capsys):
    """Unlike a baseline bump, coverage does not go to zero."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    repo.commit_file("LEDGER.md", "one\nEDITED\nthree\n", "append")

    assert run(repo, "LEDGER.md", "--allow", bad) == EXIT_OK
    assert "3 commits checked" in capsys.readouterr().out


def test_allow_accepts_a_short_sha(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md", "--allow", bad[:9]) == EXIT_OK


def test_allow_is_repeatable(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    first = repo.commit_file("LEDGER.md", "one\nBAD\n", "rewrite")
    second = repo.commit_file("LEDGER.md", "one\nWORSE\n", "rewrite again")

    assert run(repo, "LEDGER.md", "--allow", first, "--allow", second) == EXIT_OK


def test_allow_excuses_a_deletion(repo):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.git("rm", "-q", "LEDGER.md")
    gone = repo.commit("delete it")

    assert run(repo, "LEDGER.md") == EXIT_VIOLATIONS
    assert run(repo, "LEDGER.md", "--allow", gone) == EXIT_OK


# --- exceptions that have to be real --------------------------------------


def test_allow_refuses_a_revision_expression(repo, capsys):
    """An exception that can come to mean a different commit is a hole."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md", "--allow", "HEAD") == EXIT_ERROR
    assert "not a commit sha" in capsys.readouterr().err


def test_allow_refuses_a_branch_name(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md", "--allow", "main") == EXIT_ERROR
    assert "not a commit sha" in capsys.readouterr().err


def test_an_unresolvable_allow_exits_2_not_0(repo, capsys):
    """A typo'd exception must not pass as a silently inert one."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md", "--allow", "deadbeefdead") == EXIT_ERROR
    assert "no such commit" in capsys.readouterr().err


def test_a_dead_allow_is_called_out(repo, capsys):
    """The commit was checked and kept the rule, so the exception is rubbish."""
    repo.commit_file("LEDGER.md", "one\n", "add")
    fine = repo.commit_file("LEDGER.md", "one\ntwo\n", "a perfectly good append")

    assert run(repo, "LEDGER.md", "--allow", fine) == EXIT_OK
    assert "is not needed" in capsys.readouterr().out


def test_an_allow_for_an_unrelated_commit_is_called_out(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    elsewhere = repo.commit_file("OTHER.md", "hello\n", "an unrelated commit")

    run(repo, "LEDGER.md", "--allow", elsewhere)

    assert "never touched LEDGER.md" in capsys.readouterr().out


def test_since_makes_an_out_of_range_allow_unremarkable(repo, capsys):
    """An exception older than the baseline is the point, not a mistake."""
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    old = repo.commit_file("LEDGER.md", "one\nEDITED\n", "an old rewrite")
    repo.commit_file("LEDGER.md", "one\nEDITED\nthree\n", "append")

    assert run(repo, "LEDGER.md", "--since", old, "--allow", old) == EXIT_OK
    assert "never touched" not in capsys.readouterr().out


def test_an_allow_used_by_one_path_is_not_nagged_about_by_another(repo, capsys):
    """Judged across the run, not per report."""
    repo.commit_file("A.md", "one\ntwo\n", "add a")
    repo.commit_file("B.md", "one\ntwo\n", "add b")
    bad = repo.commit_file("A.md", "one\nEDITED\n", "rewrite a")

    assert run(repo, "A.md", "B.md", "--allow", bad) == EXIT_OK
    assert "is not needed" not in capsys.readouterr().out


# --- the allow file -------------------------------------------------------


def test_allow_from_file_excuses_and_prints_the_reason(repo, capsys, tmp_path):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    allow = tmp_path / "allow.txt"
    allow.write_text(
        "# exceptions\n"
        "\n"
        f"{bad[:9]}  agreed in review, see issue 27\n",
        encoding="utf-8",
    )

    assert run(repo, "LEDGER.md", "--allow-from", str(allow)) == EXIT_OK
    assert "allowed: agreed in review, see issue 27" in capsys.readouterr().out


def test_a_hash_before_the_reason_is_optional(repo, capsys, tmp_path):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    allow = tmp_path / "allow.txt"
    allow.write_text(f"{bad} # cosmetic\n", encoding="utf-8")

    assert run(repo, "LEDGER.md", "--allow-from", str(allow)) == EXIT_OK
    assert "allowed: cosmetic" in capsys.readouterr().out


def test_a_junk_line_in_the_allow_file_exits_2_with_a_line_number(
    repo, capsys, tmp_path
):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    allow = tmp_path / "allow.txt"
    allow.write_text(f"# fine\n{bad}\nnot-a-sha whoops\n", encoding="utf-8")

    assert run(repo, "LEDGER.md", "--allow-from", str(allow)) == EXIT_ERROR
    assert "allow.txt:3" in capsys.readouterr().err


def test_a_missing_allow_file_exits_2(repo, capsys):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")

    assert run(repo, "LEDGER.md", "--allow-from", "/no/such/allow.txt") == EXIT_ERROR
    assert "allow" in capsys.readouterr().err


def test_the_file_keeps_its_reason_when_the_flag_repeats_the_sha(
    repo, capsys, tmp_path
):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    allow = tmp_path / "allow.txt"
    allow.write_text(f"{bad} the written-down reason\n", encoding="utf-8")

    run(repo, "LEDGER.md", "--allow-from", str(allow), "--allow", bad)

    assert "allowed: the written-down reason" in capsys.readouterr().out


# --- machine-readable -----------------------------------------------------


def test_json_marks_allowed_violations_and_sorts_the_exceptions(
    repo, capsys, tmp_path
):
    repo.commit_file("LEDGER.md", "one\ntwo\n", "add")
    bad = repo.commit_file("LEDGER.md", "one\nEDITED\n", "rewrite")
    elsewhere = repo.commit_file("OTHER.md", "hello\n", "unrelated")
    allow = tmp_path / "allow.txt"
    allow.write_text(f"{bad} why\n{elsewhere} stale\n", encoding="utf-8")

    code = run(repo, "--json", "LEDGER.md", "--allow-from", str(allow))
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["ok"] is True
    assert payload["allow_used"] == [bad]
    assert payload["allow_absent"] == [elsewhere]
    assert payload["allow_unused"] == []
    report = payload["reports"][0]
    assert report["allowed"] == 1
    assert report["violations"][0]["allowed"] is True
    assert report["violations"][0]["reason"] == "why"


def test_quiet_says_nothing_about_dead_exceptions_either(repo, capsys):
    repo.commit_file("LEDGER.md", "one\n", "add")
    fine = repo.commit_file("LEDGER.md", "one\ntwo\n", "append")

    assert run(repo, "--quiet", "LEDGER.md", "--allow", fine) == EXIT_OK
    assert capsys.readouterr().out == ""
