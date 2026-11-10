"""Refuse to commit symlinks: a tracked symlink to local data can overwrite the real folder on merge.

Checks the git index (so it works in a pre-commit hook and in CI). Exit status 1 lists the offenders.
"""

import subprocess
import sys


def tracked_symlinks() -> list[str]:
    """Paths in the index stored with symlink mode 120000."""
    out = subprocess.run(
        ["git", "ls-files", "-s"], capture_output=True, text=True, check=True
    ).stdout
    return [line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("120000")]


def main() -> int:
    """Print every tracked symlink and return 1 if there are any."""
    bad = tracked_symlinks()
    for path in bad:
        print(f"symlink committed: {path}", file=sys.stderr)
    if bad:
        print(
            "Symlinks must not be committed (they can replace real data folders on merge). "
            "Run `git rm --cached <path>` and add the path to .gitignore.",
            file=sys.stderr,
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
