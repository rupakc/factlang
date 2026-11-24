---
name: egm-handoff
description: Cut a plan task into a self-contained brief for a goldfish implementer (Elephant-Goldfish method).
---

# EGM handoff

1. Pick one task from the plan. It must be implementable from the spec plus the task text plus the repo; if not, amend the spec first and record it in `MEMORY.md`.
2. Write the brief: the task text verbatim, the exact files to create or change, the interfaces it produces and consumes, and the global constraints (`CLAUDE.md`).
3. Name only the spec sections that matter (for example 5.1, 11, 14, 15); do not hand over the plan file or prior chat.
4. State the done criteria: tests written first, negative controls present, full gate green, explicit-path commit, CI green after push.
5. Send it to a fresh `goldfish-implementer`. Tasks on the same layer run sequentially; independent tasks may run in parallel.
6. When it reports, send the diff and the spec to a separate fresh reviewer (`goldfish-spec-reviewer` for spec gaps, `compiler-reviewer` or `dialect-engineer` for domain review).
7. Any gap the goldfish reports is a spec defect: amend the spec, add a `MEMORY.md` entry, then re-issue the task.
