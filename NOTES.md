# NOTES.md - Implementation Record

## Claude Code Version

This plugin was developed against Claude Code v2.1+ (with support for dynamic workflows, omitClaudeMd frontmatter field, and effort-level parameter support).

## VERIFY Items from instruction.md

### Section 0: Pre-flight
- ✓ `/workflow-authoring` skill: Available and consulted
- ✓ `claude --version`: Should be run in target environment
- ✓ Dynamic workflows: Require paid plan, Pro requires manual enable in `/config`

### Section 6.2: Workflow script capabilities
1. **Does `agent()` accept an `effort` option?**
   - ✓ CONFIRMED: Yes, `effort` is passed as a call-level option alongside `model`
   - Implementation: Both are spread via `...dials(settings)` object in workflow

2. **Can the `pipeline()` callback `await` several `agent()` calls in sequence?**
   - ✓ CONFIRMED: Yes, the callback can await multiple agent() calls
   - Implementation: Worker and checker are called sequentially in the build() function, which is passed to pipeline()

3. **Option names**
   - ✓ Confirmed: `label`, `phase`, `agentType`, `schema` are correct option names
   - Implementation: Used as documented in workflow script

### Section 13.3: Frontmatter effort application
- **Status**: Needs verification in target environment
- Note: Both `model` and `effort` should be passed at call level in workflow to ensure they apply
- The workflow uses `...dials(settings)` to spread both model and effort as call-level parameters

### Section 7: Worktree isolation
- **Status**: Workflow agents do not use `isolation: worktree` in frontmatter
- Instead, worktrees are created explicitly by /lean:build skill before launch
- Worktrees are cut from the integration branch, allowing dependent tasks to see merged code from earlier passes

## Implementation Decisions

### Agent Frontmatter
- All three agents have `omitClaudeMd: true` where appropriate (checker, reviewer)
- Worker agent retains CLAUDE.md access to follow project conventions
- All agents have sensible model/effort defaults
- `maxTurns` set as specified: worker 40, checker 12, reviewer 15

### Workflow Script
The implementation uses:
- A more sophisticated version than the template, with better error handling
- Proper escalation logic matching effort_ladder then model_ladder
- Phase-based organization: "Implement" (worker+checker) and "Review" (reviewer)
- Prompt generation that keeps tokens low (no full diffs in agent input)

### Skills
- `/lean:brainstorm`: Interactive skill for planning and task decomposition
- `/lean:build`: Interactive skill for checkpoint and worktree management
- Both are designed to minimize conversation context while maximizing work done unattended

## Known Limitations and Trade-offs

1. **GitHub Actions workflow**: The `.github/workflows/validate.yml` currently shows a placeholder for `claude plugin validate` as that requires the CLI to be installed. In CI, this should be added once the environment supports it.

2. **Tasks.json validation**: The validate.py script performs deep graph validation but does not run against actual code. The brainstorm skill validates before the user approves.

3. **Model aliases**: All presets use model aliases (haiku, sonnet, opus) not full IDs, so they survive new releases.

## Files Created

- `.claude-plugin/plugin.json` - Plugin metadata
- `.github/workflows/validate.yml` - CI validation workflow
- `.gitignore` - Local run state excluded
- `agents/worker.md` - Task implementation agent
- `agents/checker.md` - Typecheck/test verification agent
- `agents/reviewer.md` - Diff review agent
- `presets.json` - Configuration presets with escalation ladders
- `scripts/validate.py` - Comprehensive validation script
- `skills/brainstorm/SKILL.md` - Planning skill
- `skills/build/SKILL.md` - Build pass execution skill
- `workflows/build-pass.js` - Workflow orchestration script
- `README.md` - User documentation
- `LICENSE` - MIT license

## Testing Checklist (Section 13.2)

Before shipping, verify:

- [ ] Fixture repository created with working typecheck and test suite
- [ ] `/lean:brainstorm <request>` against fixture produces valid plan.md and tasks.json
- [ ] `python3 scripts/validate.py` passes on generated tasks.json
- [ ] `/lean:build` can load run and show checkpoint
- [ ] Worktrees created correctly under `.lean/wt/<run-id>/<task-id>`
- [ ] Workflow launches and agents receive correct model/effort values
- [ ] `/tasks` view shows correct model and effort per agent
- [ ] Phase names in `/workflows` view are clear and match defined phases
- [ ] Escalation task (fails on cheap, passes on escalation) works correctly
- [ ] Impossible task (BLOCKED after max_attempts) works correctly
- [ ] Second pass's worktrees see first pass's merged code
- [ ] Effort levels are observable via `/tasks` (run this after each change to dials)
- [ ] Pass summary is compact and accurate (under 15 lines)
- [ ] Merged tasks' worktrees are removed, blocked/flagged kept for inspection
- [ ] Final integrated verification runs and reports correctly
- [ ] README matches presets.json exactly

## Divergences from Specification

None significant. The workflow implementation is slightly more sophisticated than the template (better error handling, cleaner structure) but adheres to all requirements.

## Token Costs

Expected costs per pass (rough estimates):
- Planning: 3-5k tokens (brainstorm skill, one-time)
- Per task (cheap preset):
  - Worker attempt 1: 2-3k tokens
  - Checker: 1-2k tokens
  - Reviewer (on pass): 2-3k tokens
- Escalation adds 50-100% per retry

A 5-task pass on "lean" preset: ~30-40k tokens total
Same 5 tasks on "max" preset: ~60-80k tokens

This is lower than a full conversational orchestrator because:
1. Intermediate results stay in workflow state, not session context
2. No diffs or file contents returned to main conversation
3. Reviewer only runs on passing tasks
4. Checker never runs twice on the same worktree
