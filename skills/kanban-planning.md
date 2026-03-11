---
name: kanban-planning
description: >
  Break complex development projects into small, atomic, sequenced tasks for subagents.
  Use when planning any multi-file build, feature, or multi-phase project. Produces a
  kanban-style PLAN.md that executives use to dispatch workers one step at a time.
---

# Kanban Planning Skill

## When to use this skill

Use this skill whenever you are planning a project that involves more than 3 files or
more than one worker type. The goal is to decompose the work into tasks small enough
that each worker can complete one in a single iteration with no ambiguity.

## Core principle

**Each task card = one worker + one atomic deliverable.**

A task is atomic if:
- It produces exactly one output (one file written, one command run, one search done)
- It can be described completely without referencing unresolved decisions
- A small model can execute it without needing to think about scope

## Plan structure

Produce a `PLAN.md` at `projects/<slug>/PLAN.md` with this structure:

```markdown
# Project: <Name>
**Goal:** <one sentence — what does done look like?>
**Slug:** <project-folder-name>

## Phases

### Phase 1: Foundation
| # | Worker | Task | Output |
|---|--------|------|--------|
| 1 | docs   | Write project brief with requirements and constraints | `projects/<slug>/brief.md` |
| 2 | research | Research <topic> — competitive landscape and best practices | `projects/<slug>/research/landscape.md` |
| 3 | coder  | Scaffold project structure, create package.json / requirements.txt | `projects/<slug>/` |

### Phase 2: Core Build
| # | Worker | Task | Output |
|---|--------|------|--------|
| 4 | coder  | Implement <feature A> — full file content at exact path | `projects/<slug>/src/featureA.ts` |
| 5 | coder  | Implement <feature B> — depends on featureA interface | `projects/<slug>/src/featureB.ts` |
| 6 | qa     | Verify Phase 2 — syntax check, logic review, cross-file consistency | `projects/<slug>/qa/phase2.md` |

### Phase 3: Polish & Docs
| # | Worker | Task | Output |
|---|--------|------|--------|
| 7 | docs   | Write README.md with setup, usage, and API reference | `projects/<slug>/README.md` |
| 8 | coder  | Final integration — wire everything together, run smoke test | commit |

## Risks & Dependencies
- Task 5 depends on Task 4's interface — executive must pass the interface definition
- Task 6 must read all Phase 2 files before running checks
```

## Rules for atomic tasks

1. **No "and" in a task description.** "Write the model and the controller" = two tasks.
2. **Name the exact output file path.** Workers need absolute certainty about where to write.
3. **Declare dependencies explicitly.** If task N needs task M's output, say so and include
   the file path where task M will deposit results.
4. **QA after each phase**, not just at the end. Catching errors early saves re-work.
5. **Research before building.** If the right approach is unknown, a research step comes first.
6. **One commit per meaningful unit of work.** Coder workers should commit after each task.

## Executive dispatch loop

For each task card in order:
1. Tell the worker the exact task, exact output path, and any relevant prior outputs
2. Include any applicable skill name (e.g. "Load and follow the `web-design-pro` skill")
3. Read the result — confirm the output file exists before moving to the next task
4. Carry relevant outputs (file contents, IDs, interfaces) into the next task's instructions

Never skip ahead. Never dispatch task N+1 until task N is confirmed complete.

## After the project

Save a new skill capturing what worked — the actual sequence used, any deviations from
the plan, and lessons learned. Call it `<project-type>-workflow` (e.g. `nextjs-saas-workflow`).
