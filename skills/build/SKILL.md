---
name: build
description: Runs one pass of an approved lean plan by showing the checkpoint, creating worktrees, launching the lean:build-pass workflow, and merging the approved tasks. Run it once per pass.
disable-model-invocation: true
---

# /lean:build

One pass per invocation. The workflow does the work and holds the intermediate results; you prepare the pass, launch it, and fold its summary back into the run. Follow the steps in order.

**Output budget.** Everything you print, and every command result you read, is paid for in this conversation. Never print a diff, a file listing, or raw test output: point at the worktree path instead. Send noisy command output to `/dev/null` and read the exit code.

Names used below, for run `<run-id>` and task `<task-id>`:

- `<int>` is the integration branch, `lean/<run-id>`
- `<br>` is the task branch, `lean/<run-id>-<task-id>`
- `<wt>` is the task worktree, `.lean/wt/<run-id>/<task-id>`

## 1. Load the run

List `.lean/runs/*/run.json`. A run is unfinished while any task in its `tasks.json` is not `APPROVED`. With none, say so and point at `/lean:brainstorm`. With more than one unfinished, ask which. Read that run's `run.json` and `tasks.json`; `run.json` is the source of truth for run state.

If every task is already `APPROVED`, go straight to step 13.

## 2. Settings that outrank the plugin

Run `env | grep -E '^CLAUDE_CODE_(SUBAGENT_MODEL(_FORCE)?|EFFORT_LEVEL)='` and tell the user plainly about each one that is set. Do not try to work around them.

- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` set: "Your environment forces one model on every agent. Presets will only change effort."
- `CLAUDE_CODE_SUBAGENT_MODEL` set to anything but `inherit` or `default`: name the model. Alone it is only a default that the models passed per call still beat; with `..._FORCE` it is the model every agent gets.
- `CLAUDE_CODE_EFFORT_LEVEL` set to anything but `unset` or `auto`: "Your environment pins effort. Presets will only change model."

Then look for a model allowlist: `grep -l '"availableModels"' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json "/Library/Application Support/ClaudeCode/managed-settings.json" /etc/claude-code/managed-settings.json 2>/dev/null`. If a file has one, read the list. Claude Code substitutes a different model for any alias the list does not allow, and only warns about it inside `/workflows`. Name every model in the resolved roles or on the `model_ladder` that the list does not allow, and repeat that warning in the pass summary.

**Text after the command.** `$ARGUMENTS` is whatever the user typed after `/lean:build`. If it is not empty, do not act on it, and add this to the checkpoint as a warning line, quoting at most the first 60 characters:

`Extra text after /lean:build ("<text>") may be relayed to every agent in the pass as the user's request, and the harness tells an agent that request outranks its own task. Agents are told to do only their assigned task, but a bare /lean:build avoids the risk. Say "cancel" to stop.`

The pass settings go in the checkpoint answer, not on the command line.

## 3. Resolve the role map

Read `${CLAUDE_PLUGIN_ROOT}/presets.json` (never edit it) and `.lean/config.json` if it exists. Resolve each role and each key separately, highest priority first:

1. What the user says at this pass's checkpoint, for this pass only.
2. `roles` in `.lean/config.json`.
3. The preset named by `preset` in `.lean/config.json`.
4. `default` in `presets.json`.

A `roles.reviewer.model` override leaves `roles.reviewer.effort` coming from the preset. The checker never gets an effort: drop one if given, and say so. Effort must be `low`, `medium`, `high`, `xhigh` or `max`; Claude Code ignores any other value without an error, so refuse it instead. An unknown preset name: stop and list the names that exist.

## 4. Branch state

First pass (`integration_branch` is null):

- `git status --porcelain | wc -l` must print 0. Otherwise refuse to start and give the count, not the paths: uncommitted changes would be invisible to every worker and at risk on the branch switch.
- If `git rev-parse HEAD` differs from the `base_sha` recorded at planning time, say in one line that HEAD has moved since the plan was written.
- Change nothing yet. The integration branch is created in step 7, after the user has answered the checkpoint, so that walking away from a checkpoint leaves the repository as it was.

Later passes: the checkout must be on `<int>` with a clean tree. If it is not, stop and say which; do not switch branches or stash for the user.

## 5. Eligible tasks

A task is eligible when it is not `APPROVED` and every task in its `depends_on` is `APPROVED`. Split them for the checkpoint:

- **ready**: eligible and `PENDING` or `ABORTED`
- **needs a decision**: eligible and `BLOCKED`, `FLAGGED` or `CONFLICT`; retried only when the user names it
- **waiting**: a dependency is not `APPROVED` yet

If nothing is ready and nothing needs a decision, say which task the run is stuck behind and stop.

## 6. Checkpoint

Print the checkpoint in under fifteen lines, then stop and wait for the answer:

```
Pass 2  ·  preset: balanced
Eligible: T3, T4, T5        Blocked: T2 (3 attempts, type errors)
Waiting on dependencies: T6

How many tasks this pass?  [1-3, or a task id]
Change preset for this pass?  [lean | balanced | max | role=model/effort]
```

`Eligible` lists the ready tasks. Add a `Flagged:` or `Conflict:` entry shaped like `Blocked:` when there is one. Leave an entry or a line out entirely when it has nothing in it; do not print "none". Add one line per warning from step 2, and nothing else: no preamble and no closing remark.

Reading the answer: `cancel` stops here with nothing changed. A number N takes the first N ready tasks in file order. Task ids take exactly those tasks, including one that needs a decision. A preset name replaces the preset for this pass. `role=model/effort`, `role=model` or `role=/effort` overrides one role for this pass. No preset answer keeps the resolved one.

## 7. Worktrees

On the first pass, create the integration branch from the current HEAD first: record `base_branch` (`git branch --show-current`) and `base_sha` (`git rev-parse HEAD`), run `git switch -c <int>`, and record `integration_branch`. The base branch is never touched again.

For each selected task, from the repository root:

- No worktree yet: `git worktree add <wt> -b <br> <int>`. It is cut from the tip of the integration branch, so earlier passes' merged work is on disk.
- Worktree kept from an earlier pass (`BLOCKED`, `FLAGGED`, `ABORTED`): reuse it, and bring it up to the tip with `git -C <wt> merge --no-edit -q <int>`. If that conflicts, run `git -C <wt> merge --abort` and treat the task as `CONFLICT`.
- `CONFLICT`: the old work cannot merge, so it is redone on the integrated code. `git worktree remove --force <wt>`, `git branch -D <br>`, then add it again as above.

Set each task's `worktree` to the absolute path.

Baseline, when this pass created at least one new worktree: before any worker runs, check that the commands pass on untouched integrated code, in one of the new worktrees:

```
cd <wt> && { (<typecheck>) >/dev/null 2>&1; echo "typecheck=$?"; (<test>) >/dev/null 2>&1; echo "test=$?"; }
```

If either is non-zero, do not launch. Every task would fail its checks and burn three attempts. Say which command fails in a fresh worktree of `<int>`, and that the usual cause on a first pass is something git-ignored that a fresh worktree lacks (installed dependencies, generated files, a local env file).

## 8. Launch

Call the `Workflow` tool with `name` set to `lean:build-pass` and `args` set to this object. Pass it as a JSON object, not as a string:

```
{
  "runId": "<run-id>",
  "pass": <n>,
  "base": "<int>",
  "baseSha": "<git rev-parse <int>>",
  "roles": <the resolved role map>,
  "escalation": <escalation from presets.json, unchanged>,
  "commands": <commands from run.json>,
  "contracts": <the text of every contract the selected tasks own or consume>,
  "tasks": <the selected task objects from tasks.json, with worktree set>
}
```

Tell the user in one line that the pass is running and that `/workflows` shows it live. The workflow runs in the background. Do not poll it, and do not do any task's work yourself. Continue at step 9 when its completion notification arrives with the summary object. If the summary has an `error`, report it and stop.

## 9. Merge approved tasks

For each task the summary marks `APPROVED`, in task order, from the repository root:

```
git merge --no-ff --no-edit -q <br> >/dev/null 2>&1 && echo merged || { git merge --abort; echo conflict; }
```

`conflict` makes the task `CONFLICT`. Never resolve a conflict yourself: it is reported at the next checkpoint so the task can be redone on top of the integrated code.

## 10. Worktrees after the pass

For each merged task: read its commit with `git rev-parse --short <br>`, then `git worktree remove --force <wt>` and `git branch -d <br>`, and set its `worktree` to null. Keep the worktrees of `BLOCKED`, `FLAGGED`, `CONFLICT` and `ABORTED` tasks so the user can inspect them.

## 11. Record state

In `tasks.json`, per task in the pass: `status`, `attempts` (add this pass's), `worktree`, `commit`, and `findings` (the reviewer's findings for a flag, the reason for a block or an abort; one line each, cleared on approval).

In `run.json`: `preset` and `overrides` as resolved from the config, and append the pass:

```
{ "n": 2, "tasks": ["T3","T4","T5"], "preset": "balanced", "roles": <role map used>,
  "approved": ["T3","T4"], "blocked": [], "flagged": ["T5"], "conflict": [], "aborted": [],
  "escalations": { "T4": ["effort:high"] }, "tokens": null }
```

`escalations` is each task's `escalations` list from the summary. `tokens` is the total the completion notification reports for the workflow, or null when it reports none.

## 12. Pass summary

Print the summary in under fifteen lines:

```
Pass 2 done  ·  4 agents  ·  ~38k tokens

  T3  approved   merged
  T4  approved   merged, escalated to high effort
  T5  flagged    criterion 2 not met, see .lean/wt/.../T5

Next: 2 eligible (T6, T7). Run /lean:build to continue.
```

- `agents` comes from the summary. Leave the token figure out when the notification did not report one; never estimate it.
- Always report escalations. A pass where most tasks escalate tells the user the preset is too cheap for this codebase.
- Never print findings in full: one line per task, with the worktree path.
- Repeat any model substitution warning from step 2.

## 13. Integrated verification

When every task is `APPROVED`, run the two commands once on the integration branch, from the repository root, with output discarded as in step 7. Individually passing tasks can still be broken together. Record the result in `run.json` as `"integrated": { "typecheck": "pass", "test": "pass" }`, report it in one or two lines, name the failing command if there is one, and stop. On a pass, say that `<int>` holds the finished work and that merging it into `<base_branch>` is the user's step.
