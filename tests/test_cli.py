"""Command line behaviour, with particular attention to exit codes.

If you cannot tell "the ledger was rewritten" from "the check never ran", the
CI gate is decorative. So each of 0, 1 and 2 gets its own test.
"""

from __future__ import annotations

import json

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
