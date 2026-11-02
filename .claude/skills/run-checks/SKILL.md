---
name: run-checks
description: How to run this repo's checks and tests and report them truthfully (executed / passed / failed / skipped). Use whenever you are about to say that checks or tests pass, before a handoff, and before asking the lead to merge.
---

# Running checks and reporting them

1. `make check` runs the static checks: pack integrity (R1) and the secret scan (R2). More join as their areas land.
2. Run test suites **whole**. Never pipe a test run through `tail` or `head` (CLAUDE.md R4); a hook blocks it. If the output is long, write it to a file (`... > test-output/<name>.txt 2>&1`) and read the summary line from the file.
3. Report the counts exactly as the runner printed them: executed, passed, failed, skipped, errors. "Tests pass" without numbers is not a report.
4. A suite that collected 0 tests is a failure to report, not a pass.
5. If anything failed, name each failing test and its first error line. Don't summarise failures away.
6. When handing work to another session, use the `handoff` skill so the receiver gets the exact command and pass criterion.
