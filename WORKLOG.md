# WORKLOG

The shared record of how this repo is built with Claude Code: the agent map, who owns what, the gates the lead holds, the messages between sessions, and where the split failed. Newest entries go at the bottom of each section.

## 1. Agent map (Agent A build)

One area per session, each in its own git worktree and branch. The lead (the original design session) owns the shared contracts and is the only one that merges into `master` (CLAUDE.md R8).

| Area | Session | Branch | Owns (paths) | Delivers |
|---|---|---|---|---|
| **Lead** | design session | `master` | `CLAUDE.md`, `.claude/`, `scripts/check_*.py`, `scripts/secret_scan.py`, `scripts/git-hooks/`, `Makefile`, `pyproject.toml`, `src/glpi_agents/contracts/`, `tests/live/`, `golden/`, `WORKLOG.md`, `docs/design/` | contracts first; then merges, live runs, golden tasks, the recorded agent turn |
| **Brain** | to start | `area/brain` | `brain/`, `src/glpi_agents/brain/`, `scripts/check_decisions.py`, `tests/unit/brain/` | the YAML brain (GLPI 11.0.12 facts, rules, codes, roles, agent manifest) and its validating loader |
| **GLPI client** | to start | `area/glpi-client` | `src/glpi_agents/glpi/`, `tests/fake_glpi/`, `scripts/check_layering.py`, `tests/unit/glpi/` | the legacy-API client (sessions, scope, search, writes, fault handling) and a fake GLPI that mimics its quirks, tested behind the real proxy |
| **MCP server** | to start | `area/mcp-server` | `src/glpi_agents/server/`, `scripts/gen_tool_catalog.py`, `docs/tool-catalog.md`, `tests/unit/server/`, `tests/registration/` | the 15 tools, answer envelope, access map, start-up checks, `/version`, `/health` |
| **Agent A** | to start | `area/agent-a` | `src/glpi_agents/agent_a/`, `approvals/`, `tests/unit/agent_a/` | intake, plan, runtime with conformance checks, journal, report, CLI |

Order:

1. **Lead:** process setup (done), then `src/glpi_agents/contracts/`: the interfaces every area codes against (answer envelope and codes, GLPI client protocol, brain access, tool specs, plan step).
2. **Areas in parallel**, each against the contracts and its own fakes.
3. **Lead:** merge each area through the gates, then live tests against GLPI behind the proxy, golden tasks, and the recorded agent turn.

## 2. Gates (lead)

A branch merges into `master` only when the lead has, in that branch's worktree:

1. run `make check` (pack integrity, secret scan, plus area checks as they land): **ok**;
2. run the area's test command from its handoff, whole (R4), and the counts meet the handoff's pass criterion;
3. confirmed the matching spec in `docs/design/` was updated for any behaviour change (R10).

The gate-verifier subagent runs steps 1-2 independently; the lead records the result below.

| Date | Branch | Commit | `make check` | Tests (executed / passed / failed / skipped) | Merged |
|---|---|---|---|---|---|

## 3. Messages between sessions

Every handoff (format in `.claude/skills/handoff/SKILL.md`) is copied here with its time.

## 4. Where it failed

Honest record of what went wrong in the process and what changed because of it.

- **2026-11-02, line endings.** The global git setting `core.autocrlf=input` silently rewrote `inventory.csv` (CRLF) to LF in the first import commit, which made "byte-for-byte as received" false. Caught by comparing checksums before pushing; fixed with `.gitattributes` (`inventory.csv -text`) and the local commits were redone. `scripts/check_pack.py` now guards it.
- **2026-11-02, hook lockout.** The first version of the hooks resolved their scripts through `$CLAUDE_PROJECT_DIR`, which in the lead session still pointed at the folder the session started in (`assignment-pack`). Because the hooks fail closed, every Bash and Edit call was blocked, including the fix. Unblocked by a symlink the user created in the old folder; the hook command now finds the scripts in whichever checkout it runs in (`git rev-parse --show-toplevel`), which also makes them work inside worktrees.

## 5. Decision log

The design decisions are recorded in `docs/design/` (brain, APIs and faults, Tier 1 server, Agent A), each with its rule ids. This log points at them rather than repeating them.
