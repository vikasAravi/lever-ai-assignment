---
name: brain-lookup
description: How to find the GLPI fact or rule that governs a piece of behaviour, and cite it correctly in code. Use before writing any code that depends on how GLPI behaves or on an agreed rule (matching, refusals, readback, scope, faults).
---

# Finding and citing brain facts and rules

The design is the source of truth. Ids are stable; cite them.

| Kind | Ids | Where |
|---|---|---|
| Scope and visibility facts | V1-V9 | `docs/design/onboarding-brain-spec.md` §2.1 |
| Shared rules | P-CERTAINTY, P-IDENTITY, P-PATH, P-BLANK, P-PARENT, P-ORDER, P-UNKNOWN, P-APPROVAL, ... | same file, §2.3-2.5 |
| Record-type facts | E1-E10, F1-F12, L1-L11, G1-G7, S1-S5, C1-C4 | same file, §3 |
| Legacy API facts | A1-A20 | `docs/design/api-and-faults-spec.md` §2 |
| Proxy facts | X1-X7 | same file, §4 |
| Tier 1 decisions | T1-1 ... T1-6 | `docs/design/tier1-mcp-server-spec.md` |
| Agent A decisions | A1-A5 (Agent A sections) | `docs/design/agent-a-spec.md` |

Once `brain/` exists, the YAML there carries the same ids and is what the code loads.

1. Find the id that covers the behaviour. If none does, **stop and ask the lead**: a new GLPI fact needs a source (use the `glpi-source-reader` subagent) and a spec update (CLAUDE.md R10).
2. In code, put the citation beside the logic it explains:

   ```python
   # Decision: A4 — changeActiveEntities answers 200 `false` on failure, so the body must be exactly true.
   if body is not True:
       raise ScopeChangeRefused(entity_id)
   ```

3. Never restate a fact differently from the spec. If the code needs to disagree with the spec, the spec is wrong or the code is: raise it with the lead.
