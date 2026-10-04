---
name: brainstorm
description: Plans a coding change for the lean build pipeline by restating the request, finding the project's typecheck and test commands, and splitting the work into independent tasks to approve before /lean:build. Use when the user wants to start a lean run.
argument-hint: <what to build>
model: opus
effort: high
---

# /lean:brainstorm

Turn `$ARGUMENTS` into an approved plan that `/lean:build` can execute. This conversation is the only place the pipeline can ask the user anything, so settle what needs settling here. Every agent downstream inherits the quality of this plan.

Keep the conversation cheap: explore through the Explore agent, write the plan to disk, and print only the compact summary in step 7.

## 1. Restate

Restate the request in one short paragraph. State the assumptions you are making rather than asking about each one. Ask only when a decision is irreversible, expensive, or touches security or data.

## 2. Verification commands

The pipeline decides correctness by running commands, so it needs a real typecheck command and a real test command. Look at `package.json` scripts, `Makefile`, `composer.json`, `pyproject.toml`, `justfile`, and CI config. If you cannot find both, ask the user for them. Do not guess and move on: a wrong command blocks every task after three paid attempts.

The repository must be a git repository (`git rev-parse --is-inside-work-tree`). If it is not, say the pipeline needs git and stop.

## 3. Explore

Explore only as much of the codebase as the plan needs. Delegate bulk reading to the built-in Explore agent so search output does not land in this conversation.

## 4. Write the plan

Pick a run id, `<date>-<4 hex>`, for example with `echo "$(date +%F)-$(openssl rand -hex 2)"`.

Run state lives in `.lean/`, which must stay out of git or the build refuses a dirty tree. If `git check-ignore -q .lean/probe` fails, add `.lean/*` to the file `"$(git rev-parse --git-common-dir)/info/exclude"` (local to this clone, not committed) and say that you did.

Write `.lean/runs/<run-id>/plan.md`: the restated request, the stated assumptions, the approach, and the open concerns.

Write `.lean/runs/<run-id>/run.json`:

```json
{
  "run_id": "<run-id>",
  "created": "<date -u +%Y-%m-%dT%H:%M:%SZ>",
  "base_branch": "<git branch --show-current>",
  "base_sha": "<git rev-parse HEAD>",
  "integration_branch": null,
  "preset": null,
  "overrides": {},
  "commands": { "typecheck": "<command>", "test": "<command>" },
  "passes": []
}
```

## 5. Write the tasks

Write `.lean/runs/<run-id>/tasks.json`:

```json
{
  "run_id": "<run-id>",
  "contracts": {
    "session-store-api": "Exact interface text, given verbatim to every task that owns or consumes it."
  },
  "tasks": [
    {
      "id": "T1",
      "title": "Add Redis session store",
      "description": "What to build, in two or three sentences.",
      "acceptance_criteria": ["A criterion a reviewer can check against the diff", "Another one"],
      "owns": ["src/session/redis.ts", "src/session/index.ts"],
      "contracts": { "owns": ["session-store-api"], "consumes": [] },
      "depends_on": [],
      "status": "PENDING",
      "attempts": 0,
      "worktree": null,
      "commit": null,
      "findings": []
    }
  ]
}
```

Decomposition rules:

- **Independent first.** Where two tasks share a boundary, write the boundary down as a contract and give both sides the same text verbatim, so they build against it in parallel instead of queueing.
- A real `depends_on` edge is a last resort and must carry its reason in the task description.
- Every file is owned by exactly one task. This is what makes parallel worktrees safe to merge. Tests belong to the task that owns the code they test.
- Every contract has exactly one owner and zero or more consumers.
- Each task must leave the typecheck and the test suite passing on its own, because it is verified alone in its own worktree.
- Task ids use letters, digits, `_` and `-` only; they become branch and directory names.

## 6. Validate the task graph

Before showing anything, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate.py" --tasks .lean/runs/<run-id>/tasks.json
```

Fix what it reports and rerun until it passes. Without `python3`, check the same rules by reading the file: unique ids, acyclic `depends_on` that name real tasks, no file owned by two tasks, every referenced contract defined, and every contract with exactly one owner.

## 7. Summarise

Print a compact summary: the approach in a few lines, the task list as id plus title, the contracts by name, the two verification commands, and the open concerns. Do not print the full plan text; give the path to `plan.md` instead.

## 8. Approval

Ask for approval. If the user wants changes, edit the files, validate again, and summarise again. On approval, tell the user to run `/lean:build`. Do not start the build yourself.
