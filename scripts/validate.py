#!/usr/bin/env python3
"""Validate the lean plugin. Bare Python 3, no dependencies.

    python3 scripts/validate.py                 plugin invariants (runs in CI)
    python3 scripts/validate.py --tasks FILE    one run's task graph (used by /lean:brainstorm)

The plugin checks are the invariants hand-editing breaks. Claude Code ignores a
misspelled frontmatter field, a non-literal `meta`, or an unknown agent type
without raising anything, so they are checked here instead.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROLES = ("worker", "checker", "reviewer")
SKILLS = ("brainstorm", "build")
STATUSES = {"PENDING", "CHECKED", "APPROVED", "FLAGGED", "BLOCKED", "CONFLICT", "ABORTED"}
# Statuses the workflow itself must be able to produce. PENDING and CONFLICT
# belong to the skills: one precedes the workflow, the other follows a merge.
WORKFLOW_STATUSES = {"CHECKED", "APPROVED", "FLAGGED", "BLOCKED", "ABORTED"}

# Frontmatter that plugin agents ignore, and the one the design forbids.
IGNORED_AGENT_FIELDS = ("hooks", "mcpServers", "permissionMode", "initialPrompt")
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
# Claude Code falls back to its default colour for any other value, without a warning.
AGENT_COLORS = {"blue", "cyan", "green", "orange", "pink", "purple", "red", "yellow"}
KNOWN_TOOLS = {
    "Agent", "AskUserQuestion", "Bash", "Edit", "EnterPlanMode", "EnterWorktree",
    "ExitPlanMode", "ExitWorktree", "Glob", "Grep", "ListMcpResourcesTool", "LSP",
    "Monitor", "NotebookEdit", "Read", "ReadMcpResourceTool", "SendMessage",
    "Skill", "Task", "TaskOutput", "TaskStop", "TodoWrite", "ToolSearch",
    "WebFetch", "WebSearch", "Workflow", "Write",
}

REQUIRED_FILES = (
    ".claude-plugin/plugin.json",
    "agents/worker.md",
    "agents/checker.md",
    "agents/reviewer.md",
    "skills/brainstorm/SKILL.md",
    "skills/build/SKILL.md",
    "workflows/build-pass.js",
    "presets.json",
    "scripts/validate.py",
    ".github/workflows/validate.yml",
    ".gitignore",
    "LICENSE",
    "NOTES.md",
    "README.md",
)
ALLOWED_DIRS = {".claude-plugin", "agents", "skills", "workflows", "scripts", ".github"}
# Local state that is never repository content.
LOCAL_DIRS = {".git", ".lean", "__pycache__"}

FORBIDDEN_IN_WORKFLOW = ("import(", "Date.now(", "Math.random(", "new Date()", "require(")
FULL_MODEL_ID = re.compile(r"\bclaude-(?:opus|sonnet|haiku|fable|mythos|instant|\d)[\w.-]*", re.I)
TASK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, label, problems):
        if problems:
            self.failures += 1
            print(f"FAIL  {label}")
            for problem in problems:
                print(f"        {problem}")
        else:
            print(f"ok    {label}")


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load_json(path, problems):
    try:
        return json.loads(read(path))
    except FileNotFoundError:
        problems.append(f"{path} is missing")
    except json.JSONDecodeError as e:
        problems.append(f"{path} does not parse: {e}")
    return None


def scalar(raw):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_frontmatter(text):
    """Parse the YAML subset agent and skill files use: one `key: value` per
    line, with scalars, inline lists, and block lists. Returns (fields, body).
    A nested mapping is recorded by key only, which is all the checks need."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("no frontmatter: the file must start with ---")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter is not closed with ---")

    fields = {}
    key = None
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if key is None:
                raise ValueError(f"line {number}: indented line with no key above it")
            item = line.strip()
            if item.startswith("- ") and isinstance(fields[key], list):
                fields[key].append(scalar(item[2:]))
            elif fields[key] == [] or isinstance(fields[key], dict):
                fields[key] = {}  # a nested mapping; its contents are not needed
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):(.*)$", line)
        if not match:
            raise ValueError(f"line {number}: not a `key: value` line: {line.strip()!r}")
        key, raw = match.group(1), match.group(2).strip()
        if key in fields:
            raise ValueError(f"line {number}: duplicate key {key!r}")
        if raw == "":
            fields[key] = []  # a block list or nested mapping follows
        elif raw.startswith("[") and raw.endswith("]"):
            fields[key] = [scalar(p) for p in raw[1:-1].split(",") if p.strip()]
        else:
            fields[key] = scalar(raw)
    return fields, "\n".join(lines[end + 1:])


def tool_names(value):
    items = value if isinstance(value, list) else str(value).split(",")
    return [re.sub(r"\(.*\)$", "", str(item).strip()) for item in items if str(item).strip()]


def strip_leading_comments(source):
    text = source
    while True:
        text = text.lstrip()
        if text.startswith("//"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
        elif text.startswith("/*"):
            text = text.split("*/", 1)[1] if "*/" in text else ""
        else:
            return text


def object_literal(text, start):
    """Return the `{...}` literal that opens at text[start], skipping strings."""
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


# ---------------------------------------------------------------- plugin checks

def check_layout():
    problems = [f"{path} is missing" for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    for entry in sorted(ROOT.iterdir()):
        if entry.is_dir() and entry.name not in ALLOWED_DIRS | LOCAL_DIRS:
            problems.append(f"directory {entry.name}/ is not in the layout")
    try:
        ignored = [line.strip() for line in read(".gitignore").split("\n")]
        if ".lean/" not in ignored:
            problems.append(".gitignore must contain .lean/")
    except FileNotFoundError:
        pass
    return problems


def check_manifest():
    problems = []
    manifest = load_json(".claude-plugin/plugin.json", problems)
    name = manifest.get("name") if isinstance(manifest, dict) else None
    if manifest is not None and name != ROOT.name:
        problems.append(f"plugin.json name is {name!r} but the directory is {ROOT.name!r}")
    return problems, name


def load_agents():
    """Returns ({file stem: fields}, problems)."""
    agents, problems = {}, []
    for path in sorted((ROOT / "agents").glob("*.md")):
        try:
            fields, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except ValueError as e:
            problems.append(f"agents/{path.name}: {e}")
            continue
        agents[path.stem] = fields
    return agents, problems


def check_agent_identity(agents, parse_problems):
    problems = list(parse_problems)
    for stem, fields in agents.items():
        where = f"agents/{stem}.md"
        for key in ("name", "description"):
            if not isinstance(fields.get(key), str) or not fields[key].strip():
                problems.append(f"{where}: {key} is required (a file without it is skipped silently)")
        name = str(fields.get("name", ""))
        if ":" in name or name.startswith("-"):
            problems.append(f"{where}: name {name!r} must not contain ':' or start with '-'")
        if name and name != stem:
            problems.append(f"{where}: name {name!r} does not match the file name")
    if set(agents) != set(ROLES):
        problems.append(f"agents/ must hold exactly {', '.join(ROLES)}; found {', '.join(sorted(agents)) or 'none'}")
    return problems


def check_agent_ignored_fields(agents):
    return [
        f"agents/{stem}.md: {key} is ignored for plugin agents; remove it"
        for stem, fields in agents.items()
        for key in IGNORED_AGENT_FIELDS
        if key in fields
    ]


def check_agent_isolation(agents):
    return [
        f"agents/{stem}.md: isolation must not be set; /lean:build creates the worktrees"
        for stem, fields in agents.items()
        if "isolation" in fields
    ]


def check_agent_tools(agents):
    problems = []
    for stem, fields in agents.items():
        where = f"agents/{stem}.md"
        if "tools" not in fields:
            problems.append(f"{where}: tools is required, so the agent does not inherit every tool")
            continue
        names = tool_names(fields["tools"])
        for name in names:
            if name not in KNOWN_TOOLS and not name.startswith("mcp__"):
                problems.append(f"{where}: {name!r} is not a real tool name")
        if stem == "checker" and EDIT_TOOLS & set(names):
            problems.append(f"{where}: the checker must not have edit tools; a verifier that can fix is not a verifier")
    return problems


def check_agent_settings(agents, presets):
    """Frontmatter defaults: aliases only, turn limits set, CLAUDE.md kept only by the worker."""
    problems = []
    ladder = (presets or {}).get("escalation", {})
    for stem, fields in agents.items():
        where = f"agents/{stem}.md"
        if fields.get("model") not in ladder.get("model_ladder", []):
            problems.append(f"{where}: model {fields.get('model')!r} is not an alias on the model ladder")
        if "effort" in fields and fields["effort"] not in ladder.get("effort_ladder", []):
            problems.append(f"{where}: effort {fields['effort']!r} is not on the effort ladder")
        if not isinstance(fields.get("maxTurns"), int) or isinstance(fields.get("maxTurns"), bool) or fields["maxTurns"] < 1:
            problems.append(f"{where}: maxTurns must be a positive integer")
        if "color" in fields and fields["color"] not in AGENT_COLORS:
            problems.append(f"{where}: color {fields['color']!r} is not one Claude Code knows ({', '.join(sorted(AGENT_COLORS))})")
    if "effort" in agents.get("checker", {}):
        problems.append("agents/checker.md: the checker sets no effort")
    for stem in ("checker", "reviewer"):
        if stem in agents and agents[stem].get("omitClaudeMd") is not True:
            problems.append(f"agents/{stem}.md: omitClaudeMd must be true; it works from the delegation prompt alone")
    if "omitClaudeMd" in agents.get("worker", {}):
        problems.append("agents/worker.md: the worker keeps CLAUDE.md; remove omitClaudeMd")
    return problems


def check_presets():
    problems = []
    data = load_json("presets.json", problems)
    if not isinstance(data, dict):
        return problems, None
    presets = data.get("presets")
    ladder = data.get("escalation")
    if not isinstance(presets, dict) or not presets:
        problems.append("presets.json: presets must be a non-empty object")
        return problems, data
    if not isinstance(ladder, dict):
        problems.append("presets.json: escalation is missing")
        return problems, data
    models = ladder.get("model_ladder")
    efforts = ladder.get("effort_ladder")
    if not isinstance(models, list) or not isinstance(efforts, list) or not models or not efforts:
        problems.append("presets.json: escalation needs non-empty model_ladder and effort_ladder")
        return problems, data
    attempts = ladder.get("max_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        problems.append("presets.json: escalation.max_attempts must be a positive integer")
    if data.get("default") not in presets:
        problems.append(f"presets.json: default {data.get('default')!r} is not a preset")

    for name, roles in presets.items():
        if not isinstance(roles, dict) or set(roles) != set(ROLES):
            problems.append(f"preset {name}: must name exactly {', '.join(ROLES)}")
            continue
        for role, settings in roles.items():
            if settings.get("model") not in models:
                problems.append(f"preset {name}.{role}: model {settings.get('model')!r} is not an alias on model_ladder")
            if "effort" in settings and settings["effort"] not in efforts:
                problems.append(f"preset {name}.{role}: effort {settings['effort']!r} is not on effort_ladder")
            if role != "checker" and "effort" not in settings:
                problems.append(f"preset {name}.{role}: effort is missing")
            for key in settings:
                if key not in ("model", "effort"):
                    problems.append(f"preset {name}.{role}: unknown key {key!r}")
        if "effort" in roles["checker"]:
            problems.append(f"preset {name}: the checker must have no effort key")
    checkers = {roles["checker"].get("model") for roles in presets.values() if isinstance(roles, dict) and "checker" in roles}
    if len(checkers) > 1:
        problems.append("the checker must be the same model in every preset")
    return problems, data


def load_workflow():
    path = "workflows/build-pass.js"
    try:
        source = read(path)
    except FileNotFoundError:
        return None, None, None, [f"{path} is missing"]
    head = strip_leading_comments(source)
    opening = re.match(r"export\s+const\s+meta\s*=\s*(?=\{)", head)
    if not opening:
        return source, None, None, ["`export const meta = {` must be the first statement"]
    meta = object_literal(head, opening.end())
    if meta is None:
        return source, None, None, ["meta is not a closed object literal"]
    return source, meta, head[opening.end() + len(meta):], []


def check_workflow(source, meta, body, load_problems, plugin, agents):
    problems = list(load_problems)
    if source is None or meta is None:
        return problems
    for marker in ("...", "${", "("):
        if marker in re.sub(r"(['\"]).*?\1", "''", meta):
            problems.append(f"meta must be a plain literal; found {marker!r} (the command would drop out of autocomplete)")
    name = re.search(r"\bname\s*:\s*(['\"])(.+?)\1", meta)
    if not name:
        problems.append("meta.name must be a string literal")
    elif name.group(2) != "build-pass":
        problems.append(f"meta.name is {name.group(2)!r}; the command is named from it and must be 'build-pass'")
    if not re.search(r"\bdescription\s*:\s*(['\"]).+?\1", meta):
        problems.append("meta.description must be a string literal")

    types = re.findall(r"\bagentType\s*:\s*([^,\n}]+)", body)
    if not types:
        problems.append("no agentType found; the script must use the plugin's agents")
    for raw in types:
        literal = re.fullmatch(r"(['\"])(.+)\1", raw.strip())
        if not literal:
            problems.append(f"agentType {raw.strip()} must be a string literal so it can be checked")
            continue
        prefix, _, agent = literal.group(2).partition(":")
        if prefix != plugin or agent not in agents:
            problems.append(f"agentType {literal.group(2)!r} does not name an agent in agents/ as '{plugin}:<name>'")

    for needle in FORBIDDEN_IN_WORKFLOW:
        if needle in source:
            problems.append(f"contains {needle!r}, which fails or throws in a workflow script")
    if re.search(r"^\s*import\s", source, re.M):
        problems.append("contains an import statement; workflow scripts cannot load modules")
    return problems


def check_phases(meta, body):
    if meta is None:
        return ["meta could not be read"]
    problems = []
    listed = re.search(r"\bphases\s*:\s*\[(.*?)\]", meta, re.S)
    declared = set()
    if listed:
        if re.search(r"(?:^|,)\s*['\"]", listed.group(1)):
            problems.append("meta.phases entries must be { title: '...' } objects; Claude Code drops bare strings")
        declared = {m.group(2) for m in re.finditer(r"\btitle\s*:\s*(['\"])(.+?)\1", listed.group(1))}
    # A phase is used by a phase('X') call or by an agent's `phase: 'X'` option,
    # which is what the runtime asks for inside pipeline() stages.
    used = {m.group(2) for m in re.finditer(r"\bphase\(\s*(['\"])(.+?)\1\s*\)", body)}
    used |= {m.group(2) for m in re.finditer(r"\bphase\s*:\s*(['\"])(.+?)\1", body)}
    for title in sorted(declared - used):
        problems.append(f"meta.phases lists {title!r} but nothing runs under it")
    for title in sorted(used - declared):
        problems.append(f"phase {title!r} is used but missing from meta.phases")
    if not declared:
        problems.append("meta.phases is empty")
    return problems


def check_statuses(body):
    if body is None:
        return ["workflow body could not be read"]
    used = {m.group(2) for m in re.finditer(r"(['\"])([A-Z][A-Z_]{2,})\1", body)}
    problems = [f"status {s!r} is not in the status set" for s in sorted(used - STATUSES)]
    problems += [f"status {s!r} is never produced" for s in sorted(WORKFLOW_STATUSES - used)]
    return problems


def readme_preset_table(text):
    """Rows of the table whose header starts `| Preset |`: {name: (is_default, [cells])}."""
    lines = text.split("\n")
    rows = {}
    for i, line in enumerate(lines):
        if re.match(r"^\|\s*Preset\s*\|", line):
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                name = re.search(r"`([^`]+)`", cells[0])
                if name:
                    rows[name.group(1)] = ("(default)" in cells[0], cells[1:])
            return rows
    return None


def check_readme(data):
    if not isinstance(data, dict) or not isinstance(data.get("presets"), dict):
        return ["presets.json could not be read"]
    try:
        text = read("README.md")
    except FileNotFoundError:
        return ["README.md is missing"]
    rows = readme_preset_table(text)
    if rows is None:
        return ["README.md has no preset table (a table whose header starts `| Preset |`)"]
    problems = []
    presets = data["presets"]
    for name in sorted(set(presets) - set(rows)):
        problems.append(f"README table is missing preset {name!r}")
    for name in sorted(set(rows) - set(presets)):
        problems.append(f"README table lists {name!r}, which is not in presets.json")
    for name in sorted(set(presets) & set(rows)):
        is_default, cells = rows[name]
        if is_default != (name == data.get("default")):
            problems.append(f"README table: only {data.get('default')!r} may be marked (default)")
        expected = []
        for role in ROLES:
            settings = presets[name].get(role, {})
            expected.append(" / ".join(str(settings[k]) for k in ("model", "effort") if k in settings))
        got = [re.sub(r"\s*/\s*", " / ", c) for c in cells]
        if got != expected:
            problems.append(f"README table row {name!r} is {got} but presets.json says {expected}")
    ladder = data.get("escalation", {})
    for key in ("effort_ladder", "model_ladder"):
        stated = " → ".join(ladder.get(key, []))
        if stated not in text:
            problems.append(f"README does not state the {key.replace('_', ' ')} as `{stated}`")
    return problems


def check_skills():
    problems, found = [], set()
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        where = f"skills/{path.parent.name}/SKILL.md"
        found.add(path.parent.name)
        try:
            fields, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        if fields.get("name") != path.parent.name:
            problems.append(f"{where}: name {fields.get('name')!r} does not match its directory")
        if not isinstance(fields.get("description"), str) or not fields["description"].strip():
            problems.append(f"{where}: description is required and must be one line")
    if found != set(SKILLS):
        problems.append(f"skills/ must hold exactly {', '.join(SKILLS)}; found {', '.join(sorted(found)) or 'none'}")
    if "plan" in found:
        problems.append("no skill may be named plan; it collides with Claude Code's own plan concept")
    return problems


def check_model_ids():
    problems = []
    shipped = [".claude-plugin/plugin.json", "presets.json", "workflows/build-pass.js"]
    shipped += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "agents").glob("*.md"))]
    shipped += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "skills").glob("*/SKILL.md"))]
    for path in shipped:
        try:
            for match in FULL_MODEL_ID.finditer(read(path)):
                problems.append(f"{path}: full model ID {match.group(0)!r}; use an alias")
        except FileNotFoundError:
            pass
    return problems


def check_corrections():
    """The spec corrections recorded in NOTES.md, kept from drifting back."""
    problems = []
    for stem in ROLES:
        try:
            body = read(f"agents/{stem}.md")
        except FileNotFoundError:
            continue
        if "The harness may show you" not in body or "Do your part only" not in body:
            problems.append(f"agents/{stem}.md: the scope paragraph about the relayed user request is missing")
    try:
        fields, _ = parse_frontmatter(read("skills/build/SKILL.md"))
        if fields.get("disable-model-invocation") is not True:
            problems.append("skills/build/SKILL.md: disable-model-invocation must be true")
    except (FileNotFoundError, ValueError):
        pass
    return problems


def validate_plugin():
    report = Report()
    manifest_problems, plugin = check_manifest()
    agents, agent_parse_problems = load_agents()
    preset_problems, presets = check_presets()
    source, meta, body, workflow_problems = load_workflow()

    report.check(" 1  plugin.json parses and its name matches the directory", manifest_problems)
    report.check(" 2  agent frontmatter parses, with name and description", check_agent_identity(agents, agent_parse_problems))
    report.check(" 3  no agent sets a field plugin agents ignore", check_agent_ignored_fields(agents))
    report.check(" 4  no agent sets isolation", check_agent_isolation(agents))
    report.check(" 5  every tool in a tools field is a real tool", check_agent_tools(agents))
    report.check(" 6  presets.json: roles, aliases, ladders, no checker effort", preset_problems)
    report.check(" 7  workflow: meta first, agent types exist, nothing forbidden",
                 check_workflow(source, meta, body, workflow_problems, plugin or ROOT.name, agents))
    report.check(" 8  meta.phases and the phases used agree", check_phases(meta, body or ""))
    report.check(" 9  workflow statuses match the status set", check_statuses(body))
    report.check("10  README preset table and ladders match presets.json", check_readme(presets))
    report.check("11  layout is exactly the specified one", check_layout())
    report.check("12  agent defaults: aliases, turn limits, CLAUDE.md only for the worker", check_agent_settings(agents, presets))
    report.check("13  skills are brainstorm and build, each with name and description", check_skills())
    report.check("14  no full model ID in any shipped file", check_model_ids())
    report.check("15  scope paragraph in every agent; build skill not model-invocable", check_corrections())

    print()
    print("validate.py: OK" if report.failures == 0 else f"validate.py: {report.failures} check(s) failed")
    return 0 if report.failures == 0 else 1


# ------------------------------------------------------------- task-graph check

def validate_tasks(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL  {path} does not exist")
        return 1
    except json.JSONDecodeError as e:
        print(f"FAIL  {path} does not parse: {e}")
        return 1

    problems = []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    contracts = data.get("contracts", {}) if isinstance(data, dict) else {}
    if not isinstance(data, dict) or not isinstance(data.get("run_id"), str):
        problems.append("run_id is missing")
    if not isinstance(tasks, list) or not tasks:
        print("FAIL  tasks must be a non-empty list")
        return 1
    if not isinstance(contracts, dict):
        problems.append("contracts must be an object of name to text")
        contracts = {}

    ids = [t.get("id") if isinstance(t, dict) else None for t in tasks]
    for task_id in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        problems.append(f"task id {task_id} is used more than once")

    owners_of_file, owners_of_contract = {}, {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            problems.append(f"task #{index + 1} is not an object")
            continue
        task_id = task.get("id")
        where = f"task {task_id}"
        if not isinstance(task_id, str) or not TASK_ID.match(task_id):
            problems.append(f"task #{index + 1}: id {task_id!r} must use only letters, digits, _ and -")
        for key in ("title", "description"):
            if not isinstance(task.get(key), str) or not task[key].strip():
                problems.append(f"{where}: {key} is missing")
        criteria = task.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            problems.append(f"{where}: acceptance_criteria must be a non-empty list")
        owns = task.get("owns")
        if not isinstance(owns, list) or not owns:
            problems.append(f"{where}: owns must list at least one file")
            owns = []
        for file in owns:
            owners_of_file.setdefault(file, []).append(task_id)
        if task.get("status") not in STATUSES:
            problems.append(f"{where}: status {task.get('status')!r} is not one of {', '.join(sorted(STATUSES))}")
        held = task.get("contracts")
        if not isinstance(held, dict) or not isinstance(held.get("owns"), list) or not isinstance(held.get("consumes"), list):
            problems.append(f"{where}: contracts must be {{\"owns\": [...], \"consumes\": [...]}}")
            held = {"owns": [], "consumes": []}
        for name in held["owns"]:
            owners_of_contract.setdefault(name, []).append(task_id)
        for name in held["owns"] + held["consumes"]:
            if not isinstance(contracts.get(name), str) or not contracts[name].strip():
                problems.append(f"{where}: contract {name!r} is referenced but not defined")
        deps = task.get("depends_on")
        if not isinstance(deps, list):
            problems.append(f"{where}: depends_on must be a list")
            continue
        for dep in deps:
            if dep == task_id:
                problems.append(f"{where}: depends on itself")
            elif dep not in ids:
                problems.append(f"{where}: depends on {dep!r}, which is not a task")

    for file, owners in sorted(owners_of_file.items()):
        if len(owners) > 1:
            problems.append(f"file {file} is owned by {', '.join(map(str, owners))}; a file has exactly one owner")
    for name in sorted(contracts):
        owners = owners_of_contract.get(name, [])
        if len(owners) != 1:
            problems.append(f"contract {name!r} has {len(owners)} owners; it needs exactly one")

    graph = {t["id"]: [d for d in t.get("depends_on", []) if d in ids]
             for t in tasks if isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("depends_on"), list)}
    state = {}

    def cyclic(node, trail):
        if state.get(node) == "done":
            return None
        if state.get(node) == "open":
            return trail[trail.index(node):] + [node]
        state[node] = "open"
        for dep in graph.get(node, []):
            found = cyclic(dep, trail + [node])
            if found:
                return found
        state[node] = "done"
        return None

    for node in graph:
        cycle = cyclic(node, [])
        if cycle:
            problems.append(f"depends_on has a cycle: {' -> '.join(cycle)}")
            break

    if problems:
        print(f"FAIL  {path}")
        for problem in problems:
            print(f"        {problem}")
        return 1
    print(f"ok    {path}: {len(tasks)} tasks, {len(contracts)} contracts")
    return 0


def main(argv):
    if len(argv) == 2 and argv[0] == "--tasks":
        return validate_tasks(argv[1])
    if argv:
        print(__doc__.strip())
        return 2
    return validate_plugin()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
