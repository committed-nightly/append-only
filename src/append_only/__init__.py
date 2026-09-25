"""Check that a file has only ever been appended to, across its git history."""

from .core import Mode, PathNotTracked, Report, Violation, check_file
from .gitlog import GitError

__version__ = "0.2.0"

__all__ = [
    "GitError",
    "Mode",
    "PathNotTracked",
    "Report",
    "Violation",
    "check_file",
    "__version__",
]
