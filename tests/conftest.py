from __future__ import annotations

import subprocess

import pytest


class Repo:
    """A throwaway git repo you can drive from a test."""

    def __init__(self, path):
        self.path = str(path)

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            check=check,
            text=True,
        )
        return proc.stdout

    def merge(self, branch: str) -> None:
        """Start a merge, tolerating conflicts -- the test resolves them."""
        self.git("merge", "--no-commit", "-q", branch, check=False)

    def write(self, name: str, content: str) -> None:
        with open(f"{self.path}/{name}", "w", encoding="utf-8") as handle:
            handle.write(content)

    def write_bytes(self, name: str, content: bytes) -> None:
        with open(f"{self.path}/{name}", "wb") as handle:
            handle.write(content)

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def commit_file(self, name: str, content: str, message: str) -> str:
        self.write(name, content)
        return self.commit(message)


@pytest.fixture
def repo(tmp_path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir()
    r = Repo(path)
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.name", "Test Person")
    r.git("config", "user.email", "test@example.invalid")
    r.git("config", "commit.gpgsign", "false")
    return r
