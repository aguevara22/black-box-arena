"""The repo must stay account-agnostic: no tracked text file may carry a
home directory, an account name, or a build tree. Other people clone and
run this; a path from one machine is a defect in the deliverable.

Runs over `git ls-files` when git is available (the real tracked set), else
walks the tree with the usual exclusions.
"""

import os
from pathlib import Path
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".olean", ".ilean", ".pyc", ".gz", ".zip"}
FORBIDDEN_FRAGMENTS = ("/" + "Users/", "/" + "home/", "C:" + "\\Users\\", "/" + "root/")
FORBIDDEN_TRACKED_DIRS = ("/.lake/", "/__pycache__/", "/.venv/", "/state/")


def _tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout
        return [REPO_ROOT / p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "state", "__pycache__", ".lake", ".pytest_cache"}]
            files.extend(Path(dirpath) / f for f in filenames)
        return files


def test_no_home_directories_in_tracked_text():
    offenders = []
    home = str(Path.home())
    for path in _tracked_files():
        if path.resolve() == SELF or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for fragment in FORBIDDEN_FRAGMENTS + (home,):
            if fragment in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: contains {fragment!r}")
                break
    assert not offenders, "account-specific paths in tracked files:\n" + "\n".join(offenders)


def test_no_build_trees_tracked():
    bad = [
        str(p.relative_to(REPO_ROOT))
        for p in _tracked_files()
        if any(marker in "/" + p.relative_to(REPO_ROOT).as_posix() for marker in FORBIDDEN_TRACKED_DIRS)
    ]
    assert not bad, "build or state trees are tracked:\n" + "\n".join(bad)
