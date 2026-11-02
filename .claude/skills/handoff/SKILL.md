---
name: handoff
description: How to hand work to another session or ask the lead to merge, so the receiver can verify it without trusting you. Use whenever you message another session about finished work, a blocker, or an interface change.
---

# Handoffs that carry the check (CLAUDE.md R9)

Every handoff message has these parts:

```
From: <your area>  To: <session or lead>
Branch: <branch>   Commit: <short sha>
What: <one or two sentences on what is done or what changed>
Verify with: <exact command, from the repo root of that branch>
Pass criterion: <e.g. "0 failed, >= 42 passed, make check ok">
Depends on / affects: <interfaces, files, other areas>
Known gaps: <anything not done or not verified>
```

Rules:

1. The command must run as written in a fresh checkout of the branch. No "run the tests".
2. The pass criterion is numeric where possible.
3. An interface change (anything under `src/glpi_agents/contracts/`) goes to the lead first; never change a shared contract on your own branch.
4. Append a copy of every handoff to the `Messages` section of `WORKLOG.md` on your branch, with the time.
5. A receiver who can't reproduce the pass criterion replies with what they ran and what they saw, and the work isn't merged (R8).
