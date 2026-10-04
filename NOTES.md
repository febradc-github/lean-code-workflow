# NOTES.md

A record for the author, not for users. It holds only what was observed while building. Anything taken from the Claude Code binary's string table instead of from a run or from the `/workflow-authoring` reference is marked **undocumented internals**: it describes version 2.1.285 and can change without notice.

Built against Claude Code **2.1.285** (`claude --version`), Pro plan (`claude auth status`), macOS, Python 3.13.14, Node 26.7.0, git 2.55.0. Every minimum in section 11 of the spec is at or below 2.1.285.

## Section 0

- `/workflow-authoring` returned "Unknown skill" until `"enableWorkflows": true` was added to `~/.claude/settings.json`, and loaded right after. The plan is Pro, where workflows are off by default. See the internals list for what the key is.
- With workflows on, `/lean:build` launches `lean:build-pass` through the Workflow tool by name, and it runs.

## VERIFY items

| Item | Answer | Evidence |
| --- | --- | --- |
| 6.2.1 Does `agent()` take call-level `effort`? | Yes, and it is applied. The path taken is call-level effort. The `worker-low` style fallback variants were not needed. | The reference lists `effort` (`low`, `medium`, `high`, `xhigh`, `max`) on `agent()`. Agent transcripts record an `effort` field per assistant turn. A probe agent called with `effort: low` recorded `low`. In pass 1 the workers and reviewers recorded `low` and the checkers, which get no effort, recorded none. In pass 2 the worker's escalated second attempt recorded `medium`. `/tasks` showed nothing: opened while pass 2 ran, it said "No tasks currently running". |
| 6.2.2 Can a `pipeline()` callback await several `agent()` calls in sequence? | Yes. | `build()` awaits worker, checker, then a second worker inside one pipeline stage. Pass 2's T3 ran exactly that sequence. |
| 6.2.3 Option names | `label`, `phase`, `agentType`, `schema`, `model`, `effort` are right. | Documented in the reference and used in real runs. `agentType: 'lean:worker'` resolved to the plugin agent. |
| 7 Can a workflow agent take an arbitrary working directory? | `agent()` has no working-directory option. `isolation: worktree` is not required. The fallback in section 7 is unnecessary. | The reference lists `label`, `phase`, `schema`, `model`, `effort`, `isolation` and `agentType`. A probe agent ran `cd <worktree outside the project> && pwd && git branch --show-current` and reported the right path and branch. In passes 1 and 2 the workers committed inside `.lean/wt/...` worktrees, and those commits merged into the integration branch. A worktree cut in pass 2 held pass 1's merged files. **Not tested:** the file tools (Read, Edit, Write) on paths outside the session's working directory. The worktrees here sit under the project directory. |
| 13.3 Is frontmatter `effort` applied to background agents? | **Open, and low priority.** | Effort is passed per call, and that was observed to apply. Effort set only in frontmatter, with no call-level value, was not tested. The agent files keep `effort` as a default for direct invocation. |
| 13.4 `claude plugin eval` | Not run. The command exists in 2.1.285 (`claude plugin eval --help`). No eval cases are written. | |

## Observed behaviour

- **Passes on the fixture.** Pass 1 ran T1 and T2 on preset `lean`: 6 agents, 52,188 tokens, both approved on the first attempt. Pass 2 ran T3: 3 agents, 27,124 tokens, `BLOCKED`. The token figures come from the workflow completion notification (`usage.total_tokens`). T3 ended because the worker reported `blocked` on attempt 2, not because three checks failed.
- **Model resolution.** Requested `sonnet` ran as `claude-sonnet-5-5` and `haiku` as `claude-haiku-4-5-20251001`.
- **Permissions.** The named workflow launched with no confirmation prompt in headless and interactive sessions that had `Workflow` in `--allowedTools` and `--permission-mode acceptEdits`.
- **Pass-2 worktree.** It was cut from the integration tip and contained the merged files of pass 1.
- **`claude plugin validate .`** checks the manifest. Fed deliberately broken agent files (an invalid colour, an invalid effort, an unknown field, a `:` in the name), it flagged only a missing `description`. That is why `scripts/validate.py` carries those checks.
- **The probe incident.** A probe workflow gave two agents identical one-command tasks. The harness put the session's original user request in front of both. The sonnet agent did its task. The haiku agent acted on the original request instead and wrote four files into the project before it was stopped. This is why the agents carry a scope paragraph (deviation 4).
- **The workflow script's mock tests** (escalation, caps, aborts, bounded summary) live in the build scratchpad, not in the repo, because the layout allows no extra directories.

## Spec corrections

Four places where the spec is corrected. `scripts/validate.py` check 15 keeps items 3 and 4 from drifting back.

1. **Checker colour is `cyan`.** Section 5.2 says `gray`, which Claude Code does not recognise (see the internals list for the accepted set).
2. **Branches are `lean/<run>-<task>`.** Sections 7 and 8 say `lean/<run>/<task>`. Git refuses to create `lean/<run>/<task>` while `lean/<run>` exists as a branch: `cannot lock ref 'refs/heads/lean/<run>/<task>': 'refs/heads/lean/<run>' exists`. Tested in a scratch repo.
3. **`disable-model-invocation: true` on the build skill.** It is intended: a run starts only when the user types `/lean:build`.
4. **Every agent's system prompt carries a scope paragraph.** The harness may show an agent the user's request to the session, in front of its task. The paragraph says that request is the user's and stands, that it was addressed to the session, which divided the work, and that the agent does only its own part. It is worded as scope anchoring and does not tell the agent to rank its task above the user. It exists because of the probe incident above, where one agent took the whole relayed request as its own job.

Other differences, not among the four:

- `meta.phases` entries are `{ title, detail }` objects. Section 6.4 shows bare strings, and the reference wins under section 0.1.
- `args` also carries `pass` and `contracts`. The summary also carries `agents` and per-task `escalations` and `reason`.
- Agent calls pass `phase` per call, as the reference advises inside `pipeline()` stages.
- `/lean:build` runs the two commands once in a fresh worktree before launching, and refuses to launch if they fail.
- `scripts/validate.py` has 14 checks instead of 10, and a `--tasks FILE` mode that `/lean:brainstorm` uses on a task graph.

## Undocumented internals (from the binary strings, 2.1.285)

- `enableWorkflows` is a key in the settings schema ("Enable or disable the Workflows feature for this user. Unset = default by plan"). The `/config` row "Dynamic workflows" writes it. On Pro the default is off. `disableWorkflows: true` and `CLAUDE_CODE_DISABLE_WORKFLOWS` still disable the feature.
- With `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` set, `agent()` logs "Workflow agent model ... ignored" and drops the call-level model.
- Agent colours accepted: `blue`, `cyan`, `green`, `orange`, `pink`, `purple`, `red`, `yellow`. Any other value falls back to the default colour with no warning.
- Plugin workflows load from `workflows/*.js` and are named `<plugin>:<meta.name>`. This one was also observed working.
- A `meta.phases` entry without a `title` property is dropped, so bare strings vanish.
- `agent()` has no `cwd` option. The separate Agent tool does have one.

## Still unverified

`/lean:brainstorm` has not been run. `/tasks` has not shown model and effort. `/workflows` was seen only as the one-line status bar, not drilled into. `omitClaudeMd` and `maxTurns` partial-output marking were not observed taking effect.
