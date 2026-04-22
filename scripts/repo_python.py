#!/usr/bin/env python3
"""Run a command with the repo virtualenv python, regardless of platform."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def find_repo_python(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / "venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
        repo_root / "venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    repo_python = find_repo_python(repo_root)

    if repo_python is None:
        print("Repo virtualenv not found. Run ./scripts/bootstrap_dev.ps1 first.", file=sys.stderr)
        return 1

    result = subprocess.run([str(repo_python), *sys.argv[1:]], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
