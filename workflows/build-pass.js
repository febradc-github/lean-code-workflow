export const meta = {
  name: 'build-pass',
  description: 'Run one build pass: implement, verify, escalate on failure, review',
  whenToUse: 'Launched by /lean:build, which resolves the preset and creates the worktrees. Not meant to be started by hand.',
  phases: [
    { title: 'Implement', detail: 'worker then checker per task; a failed check escalates effort, then model' },
    { title: 'Review', detail: 'reviewer on each task whose checks passed' },
  ],
}

// One pass, launched by /lean:build with a fully resolved `args`. This script
// holds the loop and every intermediate result. The conversation receives only
// the summary object returned at the bottom, so nothing here may put a diff,
// file contents, or test output into that object.

const parse = (value) => {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value)
  } catch (e) {
    return null
  }
}

const input = parse(args)
const a = input && typeof input === 'object' ? input : {}
const roles = a.roles || {}
const ladder = a.escalation || {}
const commands = a.commands || {}
const contracts = a.contracts || {}
const tasks = Array.isArray(a.tasks) ? a.tasks : []
const maxAttempts = Number.isInteger(ladder.max_attempts) ? ladder.max_attempts : 0

const text = (value) => typeof value === 'string' && value.length > 0
const refs = (task) => [...((task.contracts || {}).owns || []), ...((task.contracts || {}).consumes || [])]

if (!input || typeof input !== 'object') {
  return { runId: null, error: 'no args: launch this through /lean:build', agents: 0, results: [] }
}

const problems = []
if (!text(a.runId)) problems.push('runId missing')
if (!text(a.base) || !text(a.baseSha)) problems.push('base or baseSha missing')
for (const role of ['worker', 'checker', 'reviewer']) {
  if (!text((roles[role] || {}).model)) problems.push(`roles.${role}.model missing`)
}
if (!Array.isArray(ladder.effort_ladder) || !Array.isArray(ladder.model_ladder) || maxAttempts < 1) {
  problems.push('escalation needs effort_ladder, model_ladder and max_attempts')
}
if (!text(commands.typecheck) || !text(commands.test)) problems.push('commands.typecheck or commands.test missing')
if (tasks.length === 0) problems.push('no tasks selected')
for (const task of tasks) {
  if (!text(task.id) || !text(task.worktree)) problems.push('a task has no id or no worktree path')
  for (const name of refs(task)) {
    if (!text(contracts[name])) problems.push(`contract ${name} has no text in args.contracts`)
  }
}
if (problems.length > 0) {
  return { runId: a.runId || null, error: problems.join('; '), agents: 0, results: [] }
}

// One line, bounded: the only free text allowed into the summary.
const LINE = 200
const line = (value) => {
  const first = String(value == null ? '' : value).split('\n').map((s) => s.trim()).filter(Boolean)[0] || ''
  return first.length > LINE ? `${first.slice(0, LINE - 1)}…` : first
}

const step = (rungs, current) => {
  const i = rungs.indexOf(current)
  if (i === -1) return current
  return rungs[Math.min(i + 1, rungs.length - 1)]
}

// After attempt 1: effort up one rung, same model. After any later attempt:
// model up one rung, effort as it stands. A dial already at the top stays there.
const escalate = (settings, attempt) =>
  attempt === 1
    ? { ...settings, effort: step(ladder.effort_ladder, settings.effort) }
    : { ...settings, model: step(ladder.model_ladder, settings.model) }

const dials = (settings) => {
  const opts = {}
  if (text(settings.model)) opts.model = settings.model
  if (text(settings.effort)) opts.effort = settings.effort
  return opts
}

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

// Prompts. Every character here is paid for in an agent's input.
const bullets = (items) => (items || []).map((item) => `- ${item}`).join('\n')
const numbered = (items) => (items || []).map((item, i) => `${i + 1}. ${item}`).join('\n')

const contractText = (task) => {
  const held = task.contracts || {}
  const parts = [
    ...(held.owns || []).map((name) => `[${name}] owned by this task\n${contracts[name]}`),
    ...(held.consumes || []).map((name) => `[${name}] owned by another task, consumed here\n${contracts[name]}`),
  ]
  return parts.length > 0 ? `Contracts (verbatim, not to be changed):\n${parts.join('\n')}` : ''
}

const buildPrompt = (task, failure, attempt) =>
  [
    `Task ${task.id}: ${task.title}`,
    `Worktree: ${task.worktree}`,
    task.description,
    `Acceptance criteria:\n${numbered(task.acceptance_criteria)}`,
    `Owns (edit nothing else):\n${bullets(task.owns)}`,
    contractText(task),
    failure.length > 0
      ? `${attempt > 1 ? 'The previous attempt failed' : 'An earlier pass left these open'}. Fix them and keep everything else:\n${bullets(failure)}`
      : '',
  ]
    .filter(Boolean)
    .join('\n\n')

const checkPrompt = (task) =>
  [
    `Worktree: ${task.worktree}`,
    'Run all three and name failures by label:',
    `committed: test -z "$(git status --porcelain)" && test -n "$(git rev-list ${a.baseSha}..HEAD)"`,
    `typecheck: ${commands.typecheck}`,
    `test: ${commands.test}`,
  ].join('\n')

const reviewPrompt = (task) =>
  [
    `Task ${task.id}: ${task.title}`,
    `Worktree: ${task.worktree}`,
    `Base for the diff: ${a.baseSha} (tip of ${a.base})`,
    `Acceptance criteria:\n${numbered(task.acceptance_criteria)}`,
    `Owns (a change to any other file is a flag):\n${bullets(task.owns)}`,
    contractText(task),
  ]
    .filter(Boolean)
    .join('\n\n')

// The `committed` command prints nothing when it fails, so say what it means.
const failureText = (f) => {
  const label = String((f && f.command) || 'check')
  if (label.includes('committed')) {
    return 'committed: the worktree has uncommitted changes or no new commit. Commit all of your work.'
  }
  return `${label}:\n${String((f && f.detail) || 'failed, no detail reported')}`
}

const has = (out, key, allowed) => Boolean(out) && typeof out === 'object' && allowed.includes(out[key])

// agent() resolves to null when the user skips it or the API fails for good,
// and throws when the agent stalls or cannot produce its structured output.
// Both end the task as ABORTED instead of dropping it from the summary.
let spawned = 0
const spawn = async (prompt, opts) => {
  spawned += 1
  try {
    const out = await agent(prompt, opts)
    return out == null ? { gone: 'agent stopped or hit an unrecoverable API error' } : { out }
  } catch (e) {
    return { gone: line(e && e.message ? e.message : e) }
  }
}

const build = async (task) => {
  let settings = { ...roles.worker }
  let failure = (Array.isArray(task.findings) ? task.findings : []).map(String)
  const escalations = []
  const row = (status, attempts, extra) => ({ id: task.id, status, attempts, escalations, ...extra })

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const suffix = attempt > 1 ? ` (attempt ${attempt})` : ''

    const work = await spawn(buildPrompt(task, failure, attempt), {
      label: `${task.id} build${suffix}`,
      phase: 'Implement',
      agentType: 'lean:worker',
      schema: workerSchema,
      ...dials(settings),
    })
    if (work.gone) return row('ABORTED', attempt, { stage: 'build', reason: work.gone })

    if (!has(work.out, 'status', ['done', 'blocked'])) {
      // A partial result (turn limit hit) is a failed attempt, not a pass.
      failure = ['worker: returned no complete result. Finish the task and commit it.']
    } else if (work.out.status === 'blocked') {
      return row('BLOCKED', attempt, { reason: line(work.out.note) || 'worker reported blocked' })
    } else {
      const check = await spawn(checkPrompt(task), {
        label: `${task.id} check${suffix}`,
        phase: 'Implement',
        agentType: 'lean:checker',
        schema: checkSchema,
        ...dials(roles.checker),
      })
      if (check.gone) return row('ABORTED', attempt, { stage: 'check', reason: check.gone })
      if (!has(check.out, 'status', ['pass', 'fail'])) {
        return row('ABORTED', attempt, { stage: 'check', reason: 'checker returned no complete result' })
      }
      if (check.out.status === 'pass') return row('CHECKED', attempt, {})

      const failures = Array.isArray(check.out.failures) ? check.out.failures : []
      failure = failures.length > 0 ? failures.map(failureText) : ['check: failed, no detail reported']
    }

    if (attempt < maxAttempts) {
      const next = escalate(settings, attempt)
      if (next.effort !== settings.effort) escalations.push(`effort:${next.effort}`)
      if (next.model !== settings.model) escalations.push(`model:${next.model}`)
      settings = next
      log(`${task.id}: attempt ${attempt} failed, retrying on ${settings.model}/${settings.effort}`)
    }
  }

  return row('BLOCKED', maxAttempts, { reason: failure.map(line).filter(Boolean).slice(0, 3).join(' | ') })
}

// Only a task whose checks passed is reviewed.
const review = async (built, task) => {
  if (!built || built.status !== 'CHECKED') return built

  const verdict = await spawn(reviewPrompt(task), {
    label: `${task.id} review`,
    phase: 'Review',
    agentType: 'lean:reviewer',
    schema: reviewSchema,
    ...dials(roles.reviewer),
  })
  if (verdict.gone) return { ...built, status: 'ABORTED', stage: 'review', reason: verdict.gone }
  if (!has(verdict.out, 'verdict', ['approve', 'flag'])) {
    return { ...built, status: 'ABORTED', stage: 'review', reason: 'reviewer returned no complete result' }
  }
  if (verdict.out.verdict === 'approve') return { ...built, status: 'APPROVED' }

  const findings = (Array.isArray(verdict.out.findings) ? verdict.out.findings : []).map(line).filter(Boolean)
  return {
    ...built,
    status: 'FLAGGED',
    findings: findings.length > 0 ? findings.slice(0, 10) : ['flagged without naming a criterion'],
  }
}

// No barrier between the stages: a task is reviewed as soon as its own checks
// pass, while slower tasks are still being built.
const rows = await pipeline(tasks, build, review)
const byId = new Map(rows.filter(Boolean).map((r) => [r.id, r]))

return {
  runId: a.runId,
  pass: a.pass == null ? null : a.pass,
  agents: spawned,
  results: tasks.map(
    (task) =>
      byId.get(task.id) || {
        id: task.id,
        status: 'ABORTED',
        attempts: 0,
        escalations: [],
        stage: 'script',
        reason: 'the task chain ended without a result',
      },
  ),
}
