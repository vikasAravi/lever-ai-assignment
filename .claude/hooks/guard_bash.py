#!/usr/bin/env python3
"""PreToolUse guard for Bash commands (enforces CLAUDE.md rules R3, R4, R5, R1).

Blocks (exit 2, reason on stderr):
  - staging or committing everything at once: `git add -A/--all/./-u`, `git commit -a`  (R3: stage by path)
  - a test run piped through tail/head                                                   (R4: counts must be visible)
  - shell writes to the assignment pack files                                            (R1: pack is read-only)
Asks the user (permissionDecision "ask") before destructive operations                 (R5):
  `docker compose down -v`, `git push --force`, `git reset --hard`, `git clean -f`, `git branch -D`.

Standard library only; runs on the system python3 (3.9+).
"""
import json
import re
import shlex
import sys

PACK_PATHS = ("proxy/", "seed/", "init/", "docker-compose.yml", "workbook.xlsx", "inventory.csv", ".env.example")
TEST_RUNNERS = re.compile(r"\b(pytest|py\.test|make\s+(test|check)\b|python3?\s+-m\s+(pytest|unittest))")
WRITE_OPS = re.compile(r"(^|\s)(sed\s+-i|tee|mv|rm|cp|truncate|chmod|perl\s+-[a-z]*i)\b|>{1,2}")


def segments(command):
    """Split a command line into simple commands on ;, &&, || and newlines (pipes kept inside)."""
    return [s.strip() for s in re.split(r";|&&|\|\||\n", command) if s.strip()]


def tokens(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def block(reason):
    sys.stderr.write(f"Blocked by .claude/hooks/guard_bash.py: {reason}\n")
    sys.exit(2)


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""

    for seg in segments(command):
        # R4: a piped test run hides the executed/passed/failed counts.
        pipe_parts = [p.strip() for p in seg.split("|")]
        if len(pipe_parts) > 1 and TEST_RUNNERS.search(pipe_parts[0]):
            if any(re.match(r"(tail|head)\b", p) for p in pipe_parts[1:]):
                block("never verify a test suite through `| tail` or `| head`; run it whole so the counts show (CLAUDE.md R4).")

        t = tokens(seg)
        if len(t) >= 2 and t[0] == "git":
            sub, args = t[1], t[2:]
            # R3: stage by path.
            if sub == "add" and any(a in ("-A", "--all", ".", "-u", "--update", ":/") for a in args):
                block("stage files by explicit path; `git add -A/./-u` is not allowed (CLAUDE.md R3).")
            if sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", a) or a == "--all" for a in args):
                block("`git commit -a` stages everything; stage by path, then commit (CLAUDE.md R3).")
            # R5: destructive git operations need the user.
            if sub == "push" and any(a in ("-f", "--force", "--force-with-lease") or a.startswith("--force") for a in args):
                ask("Force push rewrites shared history (CLAUDE.md R5).")
            if sub == "reset" and "--hard" in args:
                ask("`git reset --hard` discards uncommitted work (CLAUDE.md R5).")
            if sub == "clean" and any(a.startswith("-") and "f" in a for a in args):
                ask("`git clean -f` deletes untracked files (CLAUDE.md R5).")
            if sub == "branch" and "-D" in args:
                ask("`git branch -D` deletes an unmerged branch (CLAUDE.md R5).")

        if "docker" in t and "down" in t and any(a in ("-v", "--volumes") for a in t):
            ask("`docker compose down -v` deletes the GLPI database volume; the seed reloads on next start (CLAUDE.md R5).")

        # R1: the pack files are read-only, through the shell too.
        if WRITE_OPS.search(seg) and any(p in seg for p in PACK_PATHS):
            block("the assignment pack files (proxy/, seed/, init/, docker-compose.yml, workbook.xlsx, inventory.csv, .env.example) are read-only (CLAUDE.md R1).")

    sys.exit(0)


if __name__ == "__main__":
    main()
