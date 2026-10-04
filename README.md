# lean

A Claude Code plugin that runs an agentic coding pipeline for a fraction of the token cost of a full multi-agent orchestrator, and lets you switch models and effort levels per run. It is for developers who already have a project with a typecheck and a test suite and want Claude to build a planned change as a set of independent tasks, with commands deciding correctness instead of a model's opinion. You plan in conversation, then run one unattended pass at a time: a worker implements each task, a checker runs your typecheck and tests, a failed check retries with more effort and then a stronger model, and a reviewer approves before anything is merged.

## Install and test locally

The plugin ships without a marketplace manifest, so load it from a directory:

```
claude --plugin-dir /path/to/lean
claude plugin validate /path/to/lean --strict
python3 /path/to/lean/scripts/validate.py
```

`claude plugin validate` needs Claude Code v2.1.233 or later. This plugin was built and tested on 2.1.285. To install it permanently, add it to a marketplace of your own.

## The two-command workflow

```
/lean:brainstorm <request>     clarify, explore, write the plan and task list, ask for approval
/lean:build                    run one pass; run it again for the next pass
```

`/lean:brainstorm` runs in the conversation, finds your typecheck and test commands (and asks if it cannot), splits the work into tasks that own separate files, and asks you to approve. `/lean:build` shows a checkpoint, creates a worktree per task, launches the `/lean:build-pass` workflow, merges what was approved, and prints a summary. The workflow cannot take input while it runs, so each pass is its own launch and the checkpoint lives in the skill.

Here is a real run against the test fixture. The checkpoint:

```
Pass 1  ·  preset: balanced
Eligible: T1, T2, T3

How many tasks this pass?  [1-3, or a task id]
Change preset for this pass?  [lean | balanced | max | role=model/effort]
```

Answering `2, lean` runs T1 and T2 on the `lean` preset. When the workflow ends:

```
Pass 1 done  ·  6 agents  ·  ~56k tokens

  T1  approved   merged
  T2  approved   merged

Next: 1 eligible (T3). Run /lean:build to continue.
```

The next pass, from an earlier run of the same fixture, where T3 cannot pass by design:

```
Pass 2 done  ·  3 agents  ·  ~27k tokens

  T3  blocked   after 2 attempts, escalated to medium effort. test/limits.test.ts contradicts the acceptance criteria. See .lean/wt/2026-10-04-f1c7/T3.
```

The blocked task keeps its worktree so you can look at it. Nothing is ever printed into the conversation except the checkpoint and this summary: no diffs, no file lists, no test output.

When every task is approved, `/lean:build` runs your two commands once more on the whole integration branch, because tasks that pass alone can still break together. The finished work is on the branch `lean/<run-id>`. Merging it into your own branch is your step.

## Presets and overrides

Three presets ship in `presets.json`. The checker is the same model in every preset and has no effort setting: a preset changes how hard the thinking roles work, not how cheaply verification runs.

| Preset | Worker | Checker | Reviewer |
| --- | --- | --- | --- |
| `lean` | sonnet / low | haiku | sonnet / low |
| `balanced` (default) | sonnet / medium | haiku | sonnet / medium |
| `max` | opus / high | haiku | opus / high |

To set a default for a repository, create `.lean/config.json`:

```json
{
  "preset": "balanced",
  "roles": {
    "reviewer": { "model": "opus", "effort": "high" }
  }
}
```

Settings resolve per role and per key, highest priority first:

1. What you say at the checkpoint, for that pass only: a preset name, or `role=model/effort`, `role=model`, `role=/effort` (for example `reviewer=opus/high`).
2. `roles` in `.lean/config.json`.
3. The preset named in `.lean/config.json`.
4. `default` in `presets.json`.

So `roles.reviewer.model` alone leaves the reviewer's effort coming from the preset. Models are aliases (`haiku`, `sonnet`, `opus`), never pinned IDs. Effort is `low`, `medium`, `high`, `xhigh` or `max`.

To share a config with your team, commit `.lean/config.json` deliberately. Git cannot re-include a file inside an ignored directory, so ignore `.lean/*` instead of `.lean/` and add the rule `!.lean/config.json`.

## Escalation

When a task's checks fail, its worker is run again in the same worktree with the failures appended, up to three attempts:

- **Attempt 1:** the resolved role settings.
- **Attempt 2:** the same model, effort raised one step on `low → medium → high → xhigh`.
- **Attempt 3:** the model raised one step on `haiku → sonnet → opus`, with the effort from attempt 2.

Both dials stop at the top of their ladder, and a role already at the ceiling repeats there. An effort that is not on the ladder, such as `max`, is never raised. After three failed checks the task is `BLOCKED`: it is not reviewed or merged. The summary always lists escalations. A pass where most tasks escalate is telling you the preset is too cheap for this codebase, which is the cue to switch presets.

A checker result that is missing, malformed or contradictory counts as a failed check, never as a pass.

## Before you run this

- **It needs git and a clean tree, and it never touches your base branch.** The first pass refuses to start with uncommitted changes, then creates the integration branch `lean/<run-id>` from your current HEAD and works there. Task branches are named `lean/<run-id>-<task-id>`, not `lean/<run-id>/<task-id>`, because git cannot hold both a branch and a branch under it.
- **It needs a working typecheck and test suite.** The whole design assumes verification is a command, not an opinion. The commands also have to pass in a fresh worktree: before launching, `/lean:build` runs them once in a new one and refuses if they fail. The usual cause is something git-ignored that a fresh worktree lacks, such as installed dependencies or a local env file.
- **It spawns agents that edit files and run commands.** Choose a permission mode deliberately, or you get a wall of prompts, and add the tools the agents need to your allow rules before a long run. In the test runs the workflow launched with no prompts under `--permission-mode acceptEdits --allowedTools Bash Read Edit Write Grep Glob Workflow Skill`.
- **Type the bare command.** Claude Code may relay what you typed to every agent in the pass as your request, and tells an agent that request outranks its own task. Agents are told to do only their assigned task, but text after `/lean:build` is the one way to put a wider request in front of all of them. `/lean:build` warns if it sees any. Put pass settings in the checkpoint answer instead.

## Costs

A workflow run can use meaningfully more tokens than doing the same work in conversation, and runs count toward your plan's usage limits. On the small test fixture, a pass of two tasks took about 52k to 56k tokens and a pass of one blocked task about 27k. `/workflows` shows per-agent token usage while a pass runs, so you can stop one that is going badly. Try a narrow slice first: one or two tasks on the `lean` preset.

## Plans and settings

Dynamic workflows need a paid plan. On Pro they are off by default: switch them on with the Dynamic workflows row in `/config`. Without them `/lean:build-pass` cannot launch, and the plugin does not fall back to anything else.

Three environment variables outrank anything this plugin does. `/lean:build` detects them before the checkpoint and says so:

| Variable | What it does | What `/lean:build` tells you |
| --- | --- | --- |
| `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` | Claude Code ignores the `model` field in agent definitions, and a model passed at spawn time | Your environment forces one model on every agent. Presets will only change effort. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | A default model for subagents. A model passed per call still beats it, but it is forced when `..._FORCE` is also set | The model it is set to. |
| `CLAUDE_CODE_EFFORT_LEVEL` | Beats effort set in agent frontmatter | Your environment pins effort. Presets will only change model. |

If your organization's `availableModels` allowlist blocks a model that a preset or the escalation ladder asks for, Claude Code substitutes a different one and warns, and the warning is easy to miss in `/workflows`. `/lean:build` names the affected models at the checkpoint and again in the pass summary.

## Two entries named brainstorm

The author's other plugin, yolo-dag, also has a `brainstorm` entrypoint. Plugin skills are namespaced, so `/lean:brainstorm` and `/yolo-dag:brainstorm` do not collide, but with both installed you will see two `brainstorm` entries in `/` autocomplete.

## Known limits

Things recorded in `NOTES.md` that affect you:

- Workflow agents take no working-directory option, so the worktrees are created by `/lean:build` and agents work in them by absolute path. This was verified for worktrees under the project directory; worktrees outside it were not tried.
- An `omitClaudeMd` setting on the checker and reviewer, and the `maxTurns` partial-output marking, are set as the spec asks but were not observed taking effect.
- `/lean:brainstorm` has not been run end to end, and `claude plugin eval` has not been run on its description.
- Whether effort set only in an agent's frontmatter reaches background agents is unresolved. It does not matter here, because effort is passed on every call.
- Escalate-then-approved was not observed on the test fixture: in seven passes the worker passed its first attempt each time. The escalation logic is covered by tests against mock agents and by one real low-to-medium retry that ended `BLOCKED`.
