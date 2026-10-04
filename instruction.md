# instruction.md

Build a Claude Code plugin that runs an agentic coding pipeline at a
fraction of the token cost of a full multi-agent orchestrator, and lets
the user switch models and effort levels per run.

This document is the complete specification. Follow it top to bottom.
Where it says VERIFY, stop and check the named source before writing
that part.

---

## 0. Before you write anything

Run these first. They change what you are allowed to assume.

1. Run `/workflow-authoring`. This is the bundled skill that loads the
   dynamic-workflow script-writing reference. Section 6 of this document
   describes the workflow script from secondary sources; the skill is
   authoritative. Where they disagree, the skill wins, and you note the
   difference in `NOTES.md`.
2. Run `claude --version`. Record it in `NOTES.md`. Several features this
   plugin uses have minimum versions, listed in section 11.
3. Confirm dynamic workflows are available. They need a paid plan, and on
   Pro they must be switched on from the Dynamic workflows row in
   `/config`. If they are off, turn them on before testing.

If workflows turn out to be unavailable in the environment you are
building in, do not silently fall back to a skill-driven orchestrator.
Stop and report it.

---

## 1. Names and placeholders

The plugin name is `lean` throughout this document. It is a placeholder.
If the repository directory has a different name, use that instead and
apply it consistently: the `name` field in `plugin.json`, the `agentType`
strings in the workflow script, the namespaced command the user types,
and every example in the README.

Namespaced identifiers that follow from the plugin name:

| Thing | Identifier |
| --- | --- |
| Entry skill | `/lean:brainstorm` |
| Build skill | `/lean:build` |
| Workflow | `/lean:build-pass` |
| Worker agent | `lean:worker` |
| Checker agent | `lean:checker` |
| Reviewer agent | `lean:reviewer` |

Do not name the entry skill `plan`. Claude Code has its own plan concept
and the collision is confusing. `brainstorm` is the chosen name.

Note for the README: the author's existing plugin, yolo-dag, also has a
`brainstorm` entrypoint. Plugin skills are namespaced so there is no
functional collision, but a user with both installed sees two entries in
`/` autocomplete. Say so in the README rather than pretending otherwise.

---

## 2. What the plugin does

Two interactive skills in the main conversation, one workflow script that
runs unattended, and three small agents.

```
/lean:brainstorm <request>
    clarify, explore, write plan.md and tasks.json, ask for approval
        |
        v
/lean:build                          <-- user runs this once per pass
    resolve preset -> checkpoint -> create worktrees -> launch workflow
        |
        v
    workflow /lean:build-pass        <-- unattended, one pass
        worker -> checker -> (escalate -> worker) -> reviewer
        |
        v
    merge approved tasks, print pass summary, stop
        |
        +--> user runs /lean:build again for the next pass
```

The design rests on three facts:

- A workflow script holds the loop and the intermediate results, so the
  main conversation's context only receives the final summary. This is
  the main token saving versus running orchestration from a skill.
- A workflow cannot take user input mid-run. Therefore each pass is a
  separate workflow launch, and the checkpoint lives in the skill.
- Target repositories always have a typecheck and a test suite. Therefore
  correctness is decided by commands, not by a model's opinion, and the
  worker can start on a cheap setting and escalate only on failure.

---

## 3. Repository layout

Create exactly this. Do not add directories that are not listed.

```
lean/
├── .claude-plugin/
│   └── plugin.json
├── agents/
│   ├── worker.md
│   ├── checker.md
│   └── reviewer.md
├── skills/
│   ├── brainstorm/
│   │   └── SKILL.md
│   └── build/
│       └── SKILL.md
├── workflows/
│   └── build-pass.js
├── presets.json
├── scripts/
│   └── validate.py
├── .github/
│   └── workflows/
│       └── validate.yml
├── .gitignore
├── LICENSE
├── NOTES.md
└── README.md
```

`.gitignore` must contain `.lean/`. That directory is local run state,
not repository content.

---

## 4. Configuration: presets and overrides

### 4.1 presets.json

Ships with the plugin. Read-only at runtime; the user never edits this
file, they copy values out of it.

```json
{
  "default": "balanced",
  "presets": {
    "lean": {
      "worker":   { "model": "sonnet", "effort": "low" },
      "checker":  { "model": "haiku" },
      "reviewer": { "model": "sonnet", "effort": "low" }
    },
    "balanced": {
      "worker":   { "model": "sonnet", "effort": "medium" },
      "checker":  { "model": "haiku" },
      "reviewer": { "model": "sonnet", "effort": "medium" }
    },
    "max": {
      "worker":   { "model": "opus", "effort": "high" },
      "checker":  { "model": "haiku" },
      "reviewer": { "model": "opus", "effort": "high" }
    }
  },
  "escalation": {
    "effort_ladder": ["low", "medium", "high", "xhigh"],
    "model_ladder": ["haiku", "sonnet", "opus"],
    "max_attempts": 3
  }
}
```

Rules that must hold:

- Use model aliases (`haiku`, `sonnet`, `opus`), never full model IDs.
  Aliases survive new model releases; pinned IDs go stale.
- The checker has no `effort` key in any preset. It runs commands and
  reports structured output; reasoning effort buys nothing there, and
  effort levels available depend on the model.
- The checker is the same model in every preset. A preset is about how
  hard the thinking roles work, not about making verification cheaper.

### 4.2 User config

Optional, at `.lean/config.json` in the user's repository:

```json
{
  "preset": "balanced",
  "roles": {
    "reviewer": { "model": "opus", "effort": "high" }
  }
}
```

### 4.3 Resolution order

Highest priority first. `/lean:build` resolves this before launching the
workflow and passes the final, fully-resolved role map in `args`.

1. What the user says at the checkpoint, for that pass only.
2. `roles` overrides in `.lean/config.json`.
3. The preset named in `.lean/config.json`.
4. `presets.default`.

Merging is per role and per key. A `roles.reviewer.model` override leaves
`roles.reviewer.effort` coming from the preset.

### 4.4 Settings that sit above the plugin

Three environment variables outrank anything the plugin does. Detect them
in `/lean:build` before the checkpoint and tell the user plainly what is
happening. Do not try to work around them.

| Variable | Effect | What to say |
| --- | --- | --- |
| `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` | Claude Code ignores the `model` field in subagent definitions, and a model passed at spawn time | "Your environment forces one model on every agent. Presets will only change effort." |
| `CLAUDE_CODE_SUBAGENT_MODEL` | A default model for subagents, below a per-call model in the resolution order, but forced when `..._FORCE` is also set | Name the model it is set to. |
| `CLAUDE_CODE_EFFORT_LEVEL` | Beats frontmatter effort | "Your environment pins effort. Presets will only change model." |

Also check `availableModels`. If the organization's allowlist blocks a
model a preset asks for, Claude Code substitutes a different one and
warns. Surface that warning in the pass summary rather than letting it
scroll past in `/workflows`.

---

## 5. The agents

Three agent files, each small. The system prompt body should be tight;
the workflow passes the task-specific detail in the prompt.

Frontmatter rules that apply to all three:

- `name` and `description` are required. A file with no `description` is
  skipped and the reason only appears in the debug log.
- `name` must not contain `:` and must not start with `-`.
- Keep `description` to one short sentence. Agent descriptions consume
  context, and Claude Code warns at startup once they total more than
  15,000 tokens across all non-built-in agents.
- Plugin agents ignore `hooks`, `mcpServers`, `permissionMode`, and
  `initialPrompt`. Do not put them in these files.
- Set `model` and `effort` as sensible defaults. The workflow passes the
  resolved values per call, and a per-call `model` wins over frontmatter.
  The frontmatter matters only if someone invokes the agent directly.
- Set `omitClaudeMd: true` on the checker and the reviewer. They work
  entirely from the delegation prompt. VERIFY: this needs Claude Code
  v2.1.271 or later. Below that version, omit the field.
  The worker keeps CLAUDE.md, because project coding conventions are
  exactly what it must follow.
- Do not set `isolation: worktree` on any of them. See section 7.
- `maxTurns` guards against a runaway agent. Suggested: worker 40,
  checker 12, reviewer 15. Output is returned marked partial when the
  limit is hit, so the workflow must treat a partial result as a failure.

### 5.1 agents/worker.md

```yaml
---
name: worker
description: Implements one task in an assigned git worktree and reports the commit it made.
tools: Read, Edit, Write, Bash, Grep, Glob
model: sonnet
effort: medium
maxTurns: 40
color: blue
---
```

System prompt must state:

- You implement exactly one task. Do not touch files outside the `owns`
  list in the task you are given.
- You work inside the worktree path given in the prompt. `cd` does not
  persist between Bash calls, so every command must be written as
  `cd <worktree> && <command>`, or use `git -C <worktree>`. This is a
  real constraint, not a style preference.
- Follow the project's existing conventions and the contracts quoted in
  your task verbatim. A contract is a boundary another task is coding
  against at the same time; changing it breaks them silently.
- Commit your work in the worktree when you are done. One commit.
- Do not run the test suite or the typecheck. A separate agent does that.
  Running them yourself doubles the cost of the pass.
- Return only the structured output described by your schema. No prose
  summary, no diff in the response.

On a retry, the prompt will include the previous attempt's failures. The
system prompt must say: fix the reported failures, do not start over, do
not revert work that was not implicated.

### 5.2 agents/checker.md

```yaml
---
name: checker
description: Runs the project's typecheck and test suite in a given worktree and reports structured pass or fail.
tools: Bash, Read
model: haiku
maxTurns: 12
omitClaudeMd: true
color: gray
---
```

System prompt must state:

- You verify. You never edit, never fix, never explain away a failure.
  You have no edit tools; if you find yourself wanting one, report fail.
- Run the commands you are given, in the worktree path you are given,
  each as `cd <worktree> && <command>`.
- Report fail if any command exits non-zero.
- On failure, return at most the 20 most informative lines per failing
  command. Full output is what makes verification expensive. Prefer the
  assertion message and the file and line over the stack trace.
- Return only the structured output described by your schema.

### 5.3 agents/reviewer.md

```yaml
---
name: reviewer
description: Reviews a passing task's diff against its acceptance criteria and contracts.
tools: Read, Grep, Glob, Bash
model: sonnet
effort: medium
maxTurns: 15
omitClaudeMd: true
color: purple
---
```

System prompt must state:

- You run only after the typecheck and tests have passed. Correctness
  against the suite is already established. Do not re-litigate it.
- Your job is what the suite cannot see: does the diff do what the
  acceptance criteria asked, does it honour the contracts it was given
  verbatim, did it change files it does not own, did it add scope nobody
  asked for, did it leave a stub or a TODO where real behaviour was
  specified.
- Read the diff with `cd <worktree> && git diff <base>...HEAD`.
- Flag or approve. A flag must name the acceptance criterion or contract
  it violates. "Could be cleaner" is not a flag.
- Return only the structured output described by your schema.

---

## 6. The workflow script

File: `workflows/build-pass.js`.

A plugin ships workflows from a `workflows/` directory at the plugin
root, and they are namespaced by plugin name, so this runs as
`/lean:build-pass`.

### 6.1 Hard constraints of the runtime

These are not style guidance. Violating them breaks the run.

- `export const meta` must be the first statement, and a plain object
  literal with `name` and `description`. A variable, function call, or
  spread inside it drops the command from `/` autocomplete.
- If `meta.phases` is listed, each entry must be exactly the title passed
  to `phase()`.
- No filesystem or shell access from the script itself. Agents do that.
- No module loading. A script containing `import()` fails before the run
  starts.
- `Date.now()`, `Math.random()`, and a no-argument `new Date()` throw
  inside the script, so that a relaunched run repeats the same calls.
  If you need a timestamp or a run id, pass it in through `args`.
- `agent()` resolves to `null` if the user stops it or it hits an
  unrecoverable API error. Every call site must handle `null`.
- Limits: 16 concurrent agents (fewer on a CPU-limited machine), 4,096
  items in a single `parallel()` or `pipeline()` call, 1,000 agents total
  per run.
- A saved workflow reads input from a global named `args`. If no args are
  passed, `args` is `undefined`, not `{}`.

### 6.2 VERIFY before writing this file

Three things this script depends on must be checked against
`/workflow-authoring`, because this document's source for them is
secondary:

1. **Does `agent()` accept an `effort` option?** The model dial is
   documented at call level. Effort at call level is reported for
   workflow `agent()` and is the whole reason this plugin uses a
   workflow, but confirm the option name and accepted values.
   If it does not exist, fall back plan: define three agent variants per
   role in frontmatter (`worker-low`, `worker-medium`, `worker-high`) and
   switch `agentType` instead of passing `effort`. Record which path you
   took in `NOTES.md`, and keep the user-facing preset interface the same
   either way.
2. **Can the `pipeline()` callback `await` several `agent()` calls in
   sequence?** The per-task chain of worker, checker, retry, needs this.
   If the callback must be a single `agent()` call, restructure: run the
   worker stage with `pipeline()`, then the checker stage with
   `pipeline()`, then handle retries as a bounded loop of further
   `pipeline()` calls over the failed subset only.
3. **The exact option names** for `label`, `phase`, `agentType`, and
   `schema`.

### 6.3 Behaviour

```
args = {
  runId:    string,
  base:     string,            // integration branch name
  baseSha:  string,
  roles:    { worker: {...}, checker: {...}, reviewer: {...} },
  escalation: { effort_ladder, model_ladder, max_attempts },
  commands: { typecheck: string, test: string },
  tasks:    [ Task, ... ]      // only the tasks selected for this pass
}
```

Per task, in a fan-out bounded by the number of tasks the user chose:

1. Spawn the worker with the resolved worker role settings, the task, and
   its assigned worktree path. Attempt 1.
2. Spawn the checker in the same worktree with the typecheck and test
   commands.
3. If the checker passes, go to review.
4. If the checker fails and attempts remain: escalate, and spawn the
   worker again in the same worktree with the failure detail appended.
   - Attempt 2: same model, effort raised one step on `effort_ladder`.
   - Attempt 3: model raised one step on `model_ladder`, effort as it was
     after attempt 2's raise.
   - Cap effort at the top of the ladder; cap model at the top of the
     ladder. A role already at the ceiling repeats at the ceiling.
5. After `max_attempts` failed checks, the task is `BLOCKED`. Record the
   last failure. Do not review it, do not merge it.
6. On a pass, spawn the reviewer against the diff. Approve makes the task
   `APPROVED`. A flag makes it `FLAGGED`, with the findings recorded.
   A flagged task is not merged; it comes back at the checkpoint.
7. A `null` from any `agent()` call makes the task `ABORTED`.

The script returns a summary object only. It must not return diffs, file
contents, or test output. Everything the main conversation receives is a
token cost in the session context, which is what this plugin exists to
avoid.

### 6.4 Shape to write

Adapt it to whatever `/workflow-authoring` says. The structure, not the
exact API, is what this document is specifying.

```javascript
export const meta = {
  name: 'build-pass',
  description: 'Run one build pass: implement, verify, escalate on failure, review',
  phases: ['Implement', 'Review'],
}

const { roles, escalation, commands, tasks, base, baseSha } = args

const step = (ladder, current) => {
  const i = ladder.indexOf(current)
  if (i === -1) return current
  return ladder[Math.min(i + 1, ladder.length - 1)]
}

const escalate = (settings, attempt) =>
  attempt === 1
    ? { ...settings, effort: step(escalation.effort_ladder, settings.effort) }
    : { ...settings, model: step(escalation.model_ladder, settings.model) }

const workerSchema = {
  type: 'object',
  required: ['status', 'commit'],
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    commit: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    note: { type: 'string' },
  },
}

const checkSchema = {
  type: 'object',
  required: ['status'],
  properties: {
    status: { type: 'string', enum: ['pass', 'fail'] },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          command: { type: 'string' },
          detail: { type: 'string' },
        },
      },
    },
  },
}

const reviewSchema = {
  type: 'object',
  required: ['verdict'],
  properties: {
    verdict: { type: 'string', enum: ['approve', 'flag'] },
    findings: { type: 'array', items: { type: 'string' } },
  },
}

phase('Implement')

const built = await pipeline(tasks, async (task) => {
  let settings = roles.worker
  let failure = null

  for (let attempt = 1; attempt <= escalation.max_attempts; attempt++) {
    const work = await agent(buildPrompt(task, failure, attempt), {
      label: `${task.id} build`,
      agentType: 'lean:worker',
      schema: workerSchema,
      ...settings,
    })
    if (!work) return { id: task.id, status: 'ABORTED', stage: 'build' }
    if (work.status === 'blocked') {
      return { id: task.id, status: 'BLOCKED', reason: work.note, attempts: attempt }
    }

    const check = await agent(checkPrompt(task, work), {
      label: `${task.id} check`,
      agentType: 'lean:checker',
      schema: checkSchema,
      ...roles.checker,
    })
    if (!check) return { id: task.id, status: 'ABORTED', stage: 'check' }

    if (check.status === 'pass') {
      return { id: task.id, status: 'CHECKED', work, attempts: attempt }
    }

    failure = check.failures
    settings = escalate(settings, attempt)
  }

  return { id: task.id, status: 'BLOCKED', reason: failure, attempts: escalation.max_attempts }
})

phase('Review')

const checked = built.filter((r) => r && r.status === 'CHECKED')

const reviewed = await pipeline(checked, async (result) => {
  const task = tasks.find((t) => t.id === result.id)
  const review = await agent(reviewPrompt(task, result, base, baseSha), {
    label: `${result.id} review`,
    agentType: 'lean:reviewer',
    schema: reviewSchema,
    ...roles.reviewer,
  })
  if (!review) return { ...result, status: 'ABORTED', stage: 'review' }
  return {
    ...result,
    status: review.verdict === 'approve' ? 'APPROVED' : 'FLAGGED',
    findings: review.findings ?? [],
  }
})

const byId = new Map(reviewed.filter(Boolean).map((r) => [r.id, r]))

return {
  runId: args.runId,
  results: built.filter(Boolean).map((r) => byId.get(r.id) ?? r),
}
```

`buildPrompt`, `checkPrompt`, and `reviewPrompt` are plain functions
defined in the same file, below `meta`. Keep them terse. Every character
they emit is paid for in the agent's input.

---

## 7. Worktrees

The worker agents must not set `isolation: worktree` in frontmatter.
That option branches from the repository's default branch rather than the
parent session's HEAD, which would cut every worker off from work merged
by earlier passes. Dependent tasks would build against stale code.

Instead, `/lean:build` creates the worktrees itself with Bash, before
launching the workflow, and passes the paths in `args`:

```
git worktree add .lean/wt/<run-id>/<task-id> -b lean/<run-id>/<task-id> <integration-branch>
```

Each worktree is cut from the current tip of the integration branch, so a
task that depends on an earlier pass's work literally has that code on
disk. Retries reuse the same worktree. After the workflow returns,
`/lean:build` merges approved tasks into the integration branch and
removes their worktrees.

If `/workflow-authoring` says workflow agents cannot be given an
arbitrary working directory and must use `isolation: worktree`, then
check whether the base branch is configurable. If it is, set it to the
integration branch. If it is not, record the limitation in `NOTES.md`
and in the README: dependent tasks must then be scheduled in separate
passes rather than relying on a shared tip.

---

## 8. The skills

### 8.1 skills/brainstorm/SKILL.md

Runs in the main conversation, which is the only place the plugin can ask
the user anything.

Frontmatter: `name: brainstorm`, a one-line `description`, and a
`model`/`effort` pairing suited to planning. Planning is the one place
where a stronger setting pays for itself, because every downstream agent
inherits the plan's quality. Default it to the preset's strongest
reasonable setting.

Behaviour:

1. Restate the request in one short paragraph. State the assumptions you
   are making rather than asking about each one. Ask only when a decision
   is irreversible, expensive, or touches security or data.
2. Detect the project's verification commands and record them. Look at
   `package.json` scripts, `Makefile`, `composer.json`, `pyproject.toml`,
   `justfile`, CI config. You need a typecheck command and a test
   command. If you cannot find both, ask the user for them. The whole
   pipeline rests on these being real, so do not guess and move on.
3. Explore only as much of the codebase as the plan needs. Delegate bulk
   reading to the built-in Explore agent so search output does not land
   in the session context.
4. Write `.lean/runs/<run-id>/plan.md`: the restated request, the stated
   assumptions, the approach, and the open concerns.
5. Write `.lean/runs/<run-id>/tasks.json` using the schema in section 9.
6. Validate the task graph before showing it: unique ids, acyclic
   `depends_on`, no file owned by two tasks, every contract referenced is
   defined and has exactly one owner.
7. Print a compact plan summary: the approach in a few lines, the task
   list as id plus title, the contracts, and the open concerns. Not the
   full plan text, which is on disk for the user to open.
8. Ask for approval. On approval, tell the user to run `/lean:build`.

### 8.2 skills/build/SKILL.md

Runs one pass per invocation.

Behaviour, in order:

1. Load the run. If there is more than one unfinished run, ask which.
2. Detect the environment variables in section 4.4 and report any that
   override the plugin.
3. Resolve the role map by section 4.3.
4. On the first pass only: create the integration branch
   `lean/<run-id>` from the current HEAD, and refuse to start if the
   working tree is dirty. Uncommitted changes would be invisible to
   every worker and at risk on the branch switch.
5. Compute the eligible task set: tasks not yet `APPROVED`, whose
   `depends_on` are all `APPROVED`.
6. Show the checkpoint and ask. Format in section 10.
7. Create a worktree per selected task, per section 7.
8. Launch `/lean:build-pass` with `args`. Give it a `runId` and any
   timestamp it needs, since the script cannot generate them.
9. When the workflow returns, for each `APPROVED` task: merge its branch
   into the integration branch. If a merge conflicts, mark the task
   `CONFLICT` rather than resolving it silently, and report it at the
   checkpoint so it can be reworked on top of the integrated code.
10. Remove the worktrees of merged tasks. Keep the worktrees of
    `BLOCKED`, `FLAGGED`, and `CONFLICT` tasks so the user can inspect
    them.
11. Update `tasks.json` statuses and `run.json`.
12. Print the pass summary, per section 10.
13. If every task is `APPROVED`, run the integrated verification once
    against the whole integration branch. Individually passing tasks can
    still be collectively broken. Report the result and stop.

The skill must never print a diff, a file listing, or raw test output
into the conversation. Point at the worktree path instead.

---

## 9. Data formats

### 9.1 tasks.json

```json
{
  "run_id": "2026-10-04-a3f9",
  "contracts": {
    "session-store-api": "Exact interface text, given verbatim to every task that owns or consumes it."
  },
  "tasks": [
    {
      "id": "T1",
      "title": "Add Redis session store",
      "description": "What to build, in two or three sentences.",
      "acceptance_criteria": [
        "A criterion a reviewer can check against the diff",
        "Another one"
      ],
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

Decomposition rules for the brainstorm skill:

- **Independent first.** Where two tasks share a boundary, write the
  boundary down as a contract and give both sides the same text verbatim,
  so they build against it in parallel instead of queueing.
- A real `depends_on` edge is a last resort and must carry a reason in
  the task description.
- Every file is owned by exactly one task. This is what makes parallel
  worktrees safe to merge.
- Every contract has exactly one owner and zero or more consumers.

Statuses: `PENDING`, `CHECKED`, `APPROVED`, `FLAGGED`, `BLOCKED`,
`CONFLICT`, `ABORTED`.

### 9.2 run.json

```json
{
  "run_id": "2026-10-04-a3f9",
  "created": "2026-10-04T08:00:00Z",
  "base_branch": "main",
  "base_sha": "abc1234",
  "integration_branch": "lean/2026-10-04-a3f9",
  "preset": "balanced",
  "overrides": {},
  "commands": { "typecheck": "npm run typecheck", "test": "npm test" },
  "passes": [
    {
      "n": 1,
      "tasks": ["T1", "T2"],
      "preset": "balanced",
      "approved": ["T1"],
      "blocked": ["T2"],
      "escalations": { "T2": ["effort:high", "model:opus"] },
      "tokens": null
    }
  ]
}
```

`run.json` is the source of truth for state. The markdown files are for
the human.

### 9.3 Run state directory

```
.lean/
├── config.json                   # user config, committed if the team wants it
├── runs/<run-id>/
│   ├── run.json
│   ├── plan.md
│   └── tasks.json
└── wt/<run-id>/<task-id>/        # worktrees
```

`.lean/` is gitignored. If a team wants to share config, they commit
`.lean/config.json` deliberately with a negated ignore rule.

---

## 10. Checkpoint and summary format

Both are printed by `/lean:build` into the main conversation, so both
have a strict budget. Keep each under about fifteen lines.

Checkpoint, before a pass:

```
Pass 2  ·  preset: balanced
Eligible: T3, T4, T5        Blocked: T2 (3 attempts, type errors)
Waiting on dependencies: T6

How many tasks this pass?  [1-3, or a task id]
Change preset for this pass?  [lean | balanced | max | role=model/effort]
```

Summary, after a pass:

```
Pass 2 done  ·  4 agents  ·  ~38k tokens

  T3  approved   merged
  T4  approved   merged, escalated to high effort
  T5  flagged    criterion 2 not met, see .lean/wt/.../T5

Next: 2 eligible (T6, T7). Run /lean:build to continue.
```

Rules:

- Always report escalations. A pass where most tasks escalate is telling
  the user the preset is too cheap for this codebase, which is exactly
  the signal that makes the preset switch useful.
- Report the token figure from the `/workflows` view if it is available
  to the skill. If it is not, omit the line rather than estimating.
- Never print findings in full. One line each, with a path.

---

## 11. Version requirements

Record the installed version in `NOTES.md` and degrade gracefully, which
means omitting the field and saying so, never silently shipping a file
that Claude Code ignores.

| Feature | Minimum |
| --- | --- |
| Dynamic workflows | v2.1.154 |
| `workflowSizeGuideline` in settings | v2.1.219 |
| `/workflow-authoring` skill | v2.1.248 |
| `omitClaudeMd` frontmatter | v2.1.271 |
| `/tasks` showing model and effort | v2.1.242 |
| `claude plugin validate` on a directory | v2.1.233 |
| `maxTurns` partial-output marking | v2.1.246 |

Misspelled frontmatter fields are ignored without an error, so a field
written for a newer version simply does nothing. That is why they get
recorded rather than left in hopefully.

---

## 12. Validation

`scripts/validate.py`: bare Python 3, no dependencies, runs in CI on
every push. Checks the invariants that hand-editing breaks:

1. `plugin.json` parses and its `name` matches the directory.
2. Every agent file's frontmatter parses and has `name` and
   `description`; no `name` contains `:` or starts with `-`.
3. No agent file contains `hooks`, `mcpServers`, `permissionMode`, or
   `initialPrompt`, which plugin agents ignore.
4. No agent file contains `isolation`, per section 7.
5. Every tool named in a `tools` field is a real tool name.
6. `presets.json` parses; every preset names all three roles; every model
   value is an alias from `escalation.model_ladder`; every effort value is
   on `escalation.effort_ladder`; no checker has an `effort` key.
7. `workflows/build-pass.js`: `export const meta` is the first statement;
   every `agentType` string names an agent that exists in `agents/`,
   with the right plugin prefix; the file contains no `import(`,
   `Date.now(`, `Math.random(`, or `new Date()`.
8. Every `meta.phases` entry appears as a `phase()` argument, and vice
   versa.
9. The status values used in the workflow script match the set in
   section 9.1.
10. The preset table in `README.md` matches `presets.json`. A README that
    drifts from the config is worse than no README.

CI workflow runs `python3 scripts/validate.py` plus
`claude plugin validate .`.

---

## 13. Testing

### 13.1 Fixture

Build a small repository fixture with a real typecheck and a real test
suite, plus three deliberately shaped tasks:

- one that any model completes first try,
- one that fails the typecheck on a cheap setting and passes after
  escalation,
- one that is impossible, to exercise `BLOCKED` after three attempts.

### 13.2 Manual pass

```
cc --plugin-dir /path/to/lean
```

Then, in order:

1. `/lean:brainstorm <request>` against the fixture, approve the plan.
2. `/lean:build`, choose 2 tasks on preset `lean`.
3. While the pass runs, open `/tasks` and confirm each agent's row shows
   the model you expect, and the effort level where the agent sets one.
   This is the only direct observation of whether the dials are being
   applied. Do it every time you change how settings are passed.
4. Open `/workflows`, drill into a phase, confirm phase names and labels
   read well and token counts are visible.
5. Confirm the escalation task shows two attempts and ends approved.
6. Confirm the impossible task ends `BLOCKED` with its worktree kept.
7. Run `/lean:build` again and confirm the second pass's worktrees
   contain the first pass's merged code.

### 13.3 Known-dubious behaviour to confirm explicitly

There is a reported issue that frontmatter `effort` is not applied to
background-dispatched subagents even though `model` is. Workflow agents
run in the background. Before shipping, confirm with `/tasks` that the
effort you pass actually takes. If it does not, the escalation ladder
must escalate on the model dial only, the presets must be rewritten
around models, and the README must say so. Record the finding either way.

### 13.4 Delegation evals

`claude plugin eval` runs prompts with and without the plugin and scores
the results. Use it on the `brainstorm` description to check the skill is
picked up for realistic requests and not for unrelated ones. VERIFY
availability; it has been gated behind early access.

---

## 14. README requirements

The README is part of the deliverable, not an afterthought. It must
contain:

1. One paragraph on what the plugin does and who it is for.
2. Install and local-testing commands.
3. The two-command workflow, with a real example session.
4. The preset table, matching `presets.json` exactly, and the override
   syntax.
5. The escalation ladder, stated plainly.
6. A "before you run this" section with the three practical realities,
   stated up front because discovering them mid-run is worse:
   - It needs git and a clean tree. It never touches your base branch.
   - It needs a working typecheck and test suite. The whole design
     assumes verification is a command, not an opinion.
   - It spawns agents that edit files and run commands. Choose a
     permission mode deliberately, or you get a wall of prompts. Adding
     the tools the agents need to your allow rules before a long run
     avoids this.
7. Costs: a workflow run can use meaningfully more tokens than doing the
   same work in conversation, runs count toward plan usage limits, and
   `/workflows` shows per-agent token usage so a run can be stopped. Say
   to try a narrow slice first.
8. Workflows need a paid plan, and on Pro they must be switched on in
   `/config`.
9. The environment variables in section 4.4 and what they override.
10. The namespacing note about yolo-dag's `brainstorm`.
11. Anything recorded in `NOTES.md` that affects users.

---

## 15. NOTES.md

A running record for the author, not for users. It must end up
containing:

- The Claude Code version built against.
- Every VERIFY item in this document and what the answer turned out to be.
- Whether call-level `effort` works, and which path section 6.2 took.
- Whether frontmatter `effort` is observably applied to background
  agents, per section 13.3.
- Whether workflow agents can take an arbitrary working directory, per
  section 7.
- Any place the implementation diverges from this specification, and why.

---

## 16. Definition of done

- Every file in section 3 exists and `python3 scripts/validate.py` and
  `claude plugin validate .` both pass.
- The full manual pass in section 13.2 completes against the fixture,
  including the escalation and the blocked task.
- `/tasks` confirms the model, and the effort where set, on at least one
  agent of each role.
- A second pass's worktrees contain the first pass's merged code.
- The README matches the shipped `presets.json` and carries every warning
  in section 14.
- `NOTES.md` answers every VERIFY item.

---

## 17. Things not to do

- Do not add more agents. Three is the design. A fourth needs a reason
  that survives the question "what does this cost per pass?"
- Do not move orchestration into the skills. The workflow holding the
  loop and the intermediate results is the entire token argument.
- Do not let the checker edit anything, and do not give it edit tools "so
  it can fix small things". A verifier that can fix is not a verifier.
- Do not run the reviewer on a task whose checks failed.
- Do not print diffs, file contents, or raw test output into the main
  conversation.
- Do not pin full model IDs anywhere.
- Do not add a review loop with multiple critics per task. That is the
  other plugin. If this one needs deeper review, it gets it by the user
  switching to the `max` preset, not by spawning more agents.
