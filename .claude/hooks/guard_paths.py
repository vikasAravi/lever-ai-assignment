#!/usr/bin/env python3
"""PreToolUse guard for file-writing tools (enforces CLAUDE.md rules R1 and R2).

Blocks (exit 2) any Edit/Write/MultiEdit/NotebookEdit whose target is
  - an assignment pack file: proxy/, seed/, init/, docker-compose.yml, workbook.xlsx,
    inventory.csv, .env.example (R1: the pack is read-only), or
  - a .env file (R2: secrets are written by the user, never by an agent).
Paths are judged relative to the git checkout that contains them, so the rule holds
in every worktree as well as the main checkout.

Standard library only; runs on the system python3 (3.9+).
"""
import json
import os
import sys

PACK_DIRS = ("proxy", "seed", "init")
PACK_FILES = ("docker-compose.yml", "workbook.xlsx", "inventory.csv", ".env.example")


def repo_root(path):
    d = os.path.dirname(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main():
    payload = json.load(sys.stdin)
    ti = payload.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not target:
        sys.exit(0)
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    path = os.path.realpath(target if os.path.isabs(target) else os.path.join(base, target))

    name = os.path.basename(path)
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        sys.stderr.write("Blocked by .claude/hooks/guard_paths.py: .env holds secrets and is written by the user only (CLAUDE.md R2).\n")
        sys.exit(2)

    root = repo_root(path)
    if root is None:
        sys.exit(0)
    rel = os.path.relpath(path, root)
    first = rel.split(os.sep, 1)[0]
    if first in PACK_DIRS or rel in PACK_FILES:
        sys.stderr.write(f"Blocked by .claude/hooks/guard_paths.py: {rel} is an assignment pack file and is read-only (CLAUDE.md R1).\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
