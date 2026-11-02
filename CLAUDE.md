# CLAUDE.md

Lever AI founding-engineer assignment: an MCP server over GLPI 11.0.12 (behind a fault proxy), Agent A (onboarding from `workbook.xlsx`) and Agent B (inventory reconciliation). The design lives in `docs/design/`; read the spec for your area before changing code.

## Hard rules

Every rule names what enforces it. A rule whose check is still pending says so; don't treat it as optional.

| # | Rule | Enforced by |
|---|---|---|
| R1 | **The assignment pack is read-only**: `proxy/`, `seed/`, `init/`, `docker-compose.yml`, `workbook.xlsx`, `inventory.csv`, `.env.example`. Never modify, bypass or disable the proxy or GLPI. | `.claude/hooks/guard_paths.py` (edits), `.claude/hooks/guard_bash.py` (shell writes), `scripts/check_pack.py` (sha256 against the import commit; pre-commit and `make check`) |
| R2 | **No secrets anywhere but `.env`**: not in the repo, logs, command-line arguments, tool results or transcripts. Only the user writes `.env`. | `.gitignore`, `guard_paths.py` (blocks writing `.env`), `scripts/secret_scan.py` (pre-commit and `make check`); leak test over logs and outputs: *pending, lands with the server* |
| R3 | **Stage by path.** Never `git add -A`, `git add .`, `git add -u`, `git commit -a`. | `guard_bash.py` |
| R4 | **A test result counts only with its executed count in the output.** Never pipe a test run through `tail` or `head`; quote the executed / passed / failed / skipped numbers. | `guard_bash.py`; `make test` writes full output and `test-output/summary.md` (*pending, lands with the test harness*) |
| R5 | **Pause before destructive operations**: `docker compose down -v`, force push, `git reset --hard`, `git clean -f`, `git branch -D`. Routine work doesn't need a question. | `guard_bash.py` (asks the user) |
| R6 | **GLPI behaviour comes from the brain.** Code that depends on a GLPI fact or rule carries a `# Decision:` comment citing its id from `docs/design/` or `brain/` (e.g. `# Decision: A4 — changeActiveEntities answers 200 false on failure`). | `scripts/check_decisions.py`: every cited id must exist (*pending, lands with the brain*) |
| R7 | **Only the GLPI client talks to GLPI.** No HTTP calls to GLPI outside `src/glpi_agents/glpi/`. Tools and agents go through it. | `scripts/check_layering.py` (*pending, lands with the client*) |
| R8 | **The lead holds the gates.** Area sessions work in their own worktree and branch and never merge into `master`. The lead merges only after re-running `make check` and the area's tests itself, and records the counts in `WORKLOG.md`. | Process rule; evidence is the gate log in `WORKLOG.md` |
| R9 | **Handoffs carry the check.** A message asking another session to rely on your work names the exact command to run and the pass criterion (e.g. `make test-unit`, expect `0 failed`, `>= 40 passed`). | Process rule; evidence is the message log in `WORKLOG.md` |
| R10 | **Specs before behaviour changes.** A change to how a tool or agent behaves updates the matching spec in `docs/design/` in the same branch. | Lead review at merge (R8) |

## Working here

- Python 3.11+ (`/opt/homebrew/bin/python3.11` locally), virtualenv in `.venv/`. Run `make setup` once per checkout; it also installs the git pre-commit hook.
- `make check` runs every static check (pack integrity, secret scan, and the pending checks as they land).
- Commit messages: conventional prefix (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), a body that says why, and the `Co-Authored-By` trailer.
- Commits go to the `vikasAravi/lever-ai-assignment` remote through the `github-personal` SSH alias, as `vikasAravi`. Never use another identity.
- `WORKLOG.md` is the shared record: agent map, ownership, gate log, messages between sessions, and where the split failed.
