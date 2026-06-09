#!/usr/bin/env python3
"""
validate_swarmskill.py — Compliance checker for Swarm Skills (v0.1 spec).

Usage:
    python validate_swarmskill.py <path/to/swarmskill-name/>

Exit codes:
    0  = PASS (no errors; warnings may still print)
    1  = FAIL (one or more errors)
    2  = USAGE error (bad arguments, missing PyYAML, etc.)

Requires: PyYAML  (pip install pyyaml)

What this script catches (deterministic):
    - 5-file structure (SKILL.md, roles/, workflow.md, bind.md, dependencies.yaml)
    - Script-only SwarmFlow structure (SKILL.md + scripts/workflow.py only;
      prompts must be inline in the script)
    - Frontmatter required fields + types
    - `name` field equals directory name
    - roles/<id>.md exists for every roles[].id; no orphan role files
    - Each role file has the 5 mandatory sections (## Identity / Success Criteria /
      Boundary / Output Schema / Inline Persona for Teammate)
    - Each `## Identity` body's first non-blank line matches motto pattern `> *"..."*`
    - Each `## Boundary` contains both **Forbidden** and **Mandatory** markers
    - SKILL.md body has ## Workflow / ## Roles / ## Files
    - workflow.md has ## Overview / ## Detailed Steps / ## Acceptance Criteria
      and at least one ```mermaid``` block
    - bind.md has ## Resource Constraints / ## Behavioral Constraints / ## Failure Handling
    - dependencies.yaml has both `skills:` and `tools:` keys (even if empty)
    - Every roles[].skills / roles[].tools in SKILL.md appears in dependencies.yaml
    - scripts/workflow.py, when present, is legal Python with literal META and
      async def run(args), explicit swarmflow primitive imports, inline prompts,
      no template placeholders, and no obvious dangerous calls or local absolute paths
    - scripts/workflow.py has no f-string referencing an undefined name (the
      literal-brace trap: `{N}` in prompt text parsed as an expression)

What this script does NOT catch (judgment calls — see reference/compliance-checklist.md):
    - Whether mottos are mutually antagonistic (anti-convergence quality)
    - Whether Boundary lists really cover all sibling territories
    - Whether bind.md numbers actually match the workflow's needs
    - Whether the Swarm Skill is even justified vs a single-agent skill (Stage 0)
"""

from __future__ import annotations

import ast
import builtins
import sys
import re
import logging
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    logger.info("[USAGE ERROR] PyYAML is required. Install with: pip install pyyaml")
    raise


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

class Report:

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, file: str, msg: str) -> None:
        self.errors.append(f"  [ERROR] {file}: {msg}")

    def warn(self, file: str, msg: str) -> None:
        self.warnings.append(f"  [WARN]  {file}: {msg}")

    def passed(self) -> bool:
        return not self.errors

    def emit(self) -> None:
        if self.errors:
            logger.info("\nERRORS:")
            for e in self.errors:
                logger.info(e)
        if self.warnings:
            logger.info("\nWARNINGS:")
            for w in self.warnings:
                logger.info(w)
        if self.passed():
            logger.info("\n[PASS] %s warning(s), 0 error(s).", len(self.warnings))
        else:
            logger.info(
                "\n[FAIL] %s error(s), %s warning(s).",
                len(self.errors),
                len(self.warnings),
            )


# ---------------------------------------------------------------------------
# Frontmatter + section parsing helpers
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a Markdown file into (frontmatter_dict, body_str)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"frontmatter YAML parse error: {e}") from e
    return fm, m.group(2)


def find_h2_sections(body: str) -> dict[str, int]:
    """Return mapping of `## Section Title` -> line number (1-indexed)."""
    sections: dict[str, int] = {}
    for i, line in enumerate(body.splitlines(), 1):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            sections[m.group(1).strip()] = i
    return sections


def section_body(body: str, header: str) -> str | None:
    """Return the body text under `## <header>` up to the next `## ` heading or EOF."""
    lines = body.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if re.match(rf"^##\s+{re.escape(header)}\s*$", line):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^##\s+\S", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Per-file validators
# ---------------------------------------------------------------------------

def validate_skill_md(path: Path, report: Report, script_only: bool = False) -> tuple[dict | None, list[str]]:
    """Validate <root>/SKILL.md. Returns (frontmatter, role_ids_declared)."""
    if not path.exists():
        report.err("SKILL.md", "missing — required for every Swarm Skill")
        return None, []

    text = path.read_text(encoding="utf-8")

    try:
        fm, body = split_frontmatter(text)
    except ValueError as e:
        report.err("SKILL.md", str(e))
        return None, []

    if fm is None:
        report.err("SKILL.md", "missing YAML frontmatter (--- ... ---)")
        return None, []

    # Required frontmatter fields. Script-only SwarmFlow skills do not need
    # roles[] because the script is the execution definition.
    required_fields = ("name", "description", "version", "kind")
    if not script_only:
        required_fields += ("roles",)
    for field in required_fields:
        if field not in fm:
            report.err("SKILL.md", f"frontmatter missing required field `{field}`")

    if fm.get("kind") != "swarm-skill":
        report.err("SKILL.md", f"frontmatter `kind` must be `swarm-skill` (got: {fm.get('kind')!r})")

    # name == directory name
    dir_name = path.parent.name
    if fm.get("name") != dir_name:
        report.err("SKILL.md", f"frontmatter `name` ({fm.get('name')!r}) must equal directory name ({dir_name!r})")

    # description discipline (the trigger blob the model reads BEFORE loading the body)
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        desc_chars = len(desc.strip())
        desc_lines = [ln for ln in desc.strip().splitlines() if ln.strip()]
        if desc_chars > 500:
            report.warn(
                "SKILL.md",
                f"description is {desc_chars} chars — HARD CAP is 500 (platform limit: 1024). "
                "Strip synonym enumeration / scope thresholds / Stage-0 rationale; see "
                "../swarmskill-creator/SKILL.md Stage 5 for the discipline rules.",
            )
        if len(desc_lines) > 4:
            report.warn(
                "SKILL.md",
                f"description has {len(desc_lines)} non-blank lines — HARD CAP is 4. "
                "Use 3-line structure: WHAT / WHEN / NOT.",
            )
        if re.search(r"^\s*Triggers\s*:", desc, re.MULTILINE | re.IGNORECASE):
            report.warn(
                "SKILL.md",
                "description contains a `Triggers:` line — DEPRECATED. Merge synonym phrases "
                "into one natural-language `Use when` sentence; semantic match handles variants.",
            )

    # roles[] structure
    role_ids: list[str] = []
    roles = fm.get("roles", [])
    if script_only:
        if "roles" in fm and roles not in (None, []):
            report.err("SKILL.md", "script-only SwarmFlow skills must omit `roles` or set `roles: []`")
        roles = []
    elif not isinstance(roles, list) or (not roles and not script_only):
        report.err("SKILL.md", "frontmatter `roles` must be a non-empty list")
    else:
        for i, role in enumerate(roles):
            if not isinstance(role, dict):
                report.err("SKILL.md", f"roles[{i}] must be an object")
                continue
            if "id" not in role:
                report.err("SKILL.md", f"roles[{i}] missing required field `id`")
            else:
                role_ids.append(role["id"])
            if "kind" not in role:
                report.err("SKILL.md", f"roles[{i}] (id={role.get('id')!r}) missing required field `kind`")
            elif role["kind"] not in ("ai_agent", "human_agent"):
                report.err(
                    "SKILL.md",
                    f"roles[{i}] (id={role.get('id')!r}) `kind` must be "
                    f"`ai_agent` or `human_agent` (got: {role['kind']!r})",
                )
            if "purpose" not in role:
                report.err("SKILL.md", f"roles[{i}] (id={role.get('id')!r}) missing required field `purpose`")
            else:
                # Per-role purpose is a 1-line summary; details go in roles/<id>.md
                purpose = role.get("purpose") or ""
                if isinstance(purpose, str) and len(purpose) > 150:
                    report.warn(
                        "SKILL.md",
                        f"roles[{i}] (id={role.get('id')!r}) `purpose` is {len(purpose)} "
                        f"chars — HARD CAP is 150. Move detail into roles/{role.get('id')}.md.",
                    )

    # Check if all AI roles have empty skills and tools (suggests auto-matching was skipped).
    # Human-agent roles are excluded — they legitimately have no skills/tools.
    if isinstance(roles, list) and roles:
        ai_roles = [r for r in roles if isinstance(r, dict) and r.get("kind", "ai_agent") != "human_agent"]
        all_empty = ai_roles and all(
            (not role.get("skills")) and (not role.get("tools"))
            for role in ai_roles
        )
        if all_empty and len(role_ids) > 0:
            report.warn(
                "SKILL.md",
                "all roles have empty `skills` and `tools` — was Stage 2 auto-matching run? "
                "Empty lists are valid only when the local scan confirms no matches. "
                "Run the auto-matching sub-step in Stage 2 before declaring no dependencies.",
            )

    # Body required sections
    sections = find_h2_sections(body)
    required_body_sections = ("Workflow", "Files") if script_only else ("Workflow", "Roles", "Files")
    for required in required_body_sections:
        if required not in sections:
            report.err("SKILL.md", f"body missing required section `## {required}`")

    # Anti-pattern: redundant sections that the description / workflow.md already cover
    for deprecated in ("When to Use", "Anti-Patterns", "Output Format"):
        if deprecated in sections:
            report.warn(
                "SKILL.md",
                f"body contains `## {deprecated}` which is already covered by `description` "
                "or `workflow.md` — remove to avoid duplication",
            )

    # Anti-pattern: mermaid in SKILL.md body (redundant with workflow.md).
    # Script-only skills have no workflow.md, so this warning does not apply.
    if not script_only and re.search(r"```mermaid", body):
        report.warn(
            "SKILL.md",
            "body contains a ```mermaid``` block — keep mermaid only in workflow.md "
            "to avoid duplication",
        )

    return fm, role_ids


def validate_role_file(path: Path, role_id: str, report: Report) -> dict[str, list[str]]:
    """Validate one roles/<id>.md.

    Returns extracted role-level skills/tools (currently unused — declared in
    SKILL.md frontmatter).
    """
    file_label = f"roles/{role_id}.md"
    if not path.exists():
        report.err(file_label, f"missing — required because SKILL.md frontmatter declares roles[].id = {role_id!r}")
        return {}

    text = path.read_text(encoding="utf-8")
    sections = find_h2_sections(text)

    required_sections = (
        "Identity",
        "Success Criteria",
        "Boundary",
        "Output Schema",
        "Inline Persona for Teammate",
    )
    for s in required_sections:
        if s not in sections:
            report.err(file_label, f"missing required section `## {s}`")

    # Identity must start with a 1-line motto: > *"..."*
    identity_body = section_body(text, "Identity")
    if identity_body is not None:
        first_nonblank = next((ln.strip() for ln in identity_body.splitlines() if ln.strip()), "")
        # Accept either > *"..."* or > *'...'*  (italic blockquote with quotes)
        motto_pattern = re.compile(r'^>\s*\*["\'].+["\']\*\s*$')
        if not motto_pattern.match(first_nonblank):
            report.err(
                file_label,
                "## Identity first non-blank line must be a 1-line motto in "
                f"`> *\"...\"*` format. Got: {first_nonblank[:80]!r}",
            )

    # Boundary must contain BOTH Forbidden and Mandatory markers
    # Accept common variations: **Forbidden**, **禁止**, **Forbidden actions**, etc.
    boundary_body = section_body(text, "Boundary")
    if boundary_body is not None:
        has_forbidden = re.search(r"\*\*(Forbidden|禁止)\*\*", boundary_body) is not None
        has_mandatory = re.search(r"\*\*(Mandatory|必须|必做)\*\*", boundary_body) is not None
        if not has_forbidden:
            report.err(file_label, "## Boundary missing `**Forbidden**` (or `**禁止**`) block")
        if not has_mandatory:
            report.err(file_label, "## Boundary missing `**Mandatory**` (or `**必须**` / `**必做**`) block")

    # Inline Persona must be non-trivially long (sanity check — empty section is the
    # most common authoring error)
    persona_body = section_body(text, "Inline Persona for Teammate")
    if persona_body is not None and len(persona_body.strip()) < 100:
        report.warn(
            file_label,
            f"## Inline Persona for Teammate is suspiciously short "
            f"({len(persona_body.strip())} chars). "
            "It must be a self-contained pasteable prompt — see reference/role-design.md.",
        )

    # Anti-pattern: section names that should be merged into the 5 mandatory sections
    for deprecated in ("Mindset", "Priorities", "Inspection Process", "MUST-Find Rule", "Inline Persona for Sub-Agent"):
        if deprecated in sections:
            if deprecated == "Inline Persona for Sub-Agent":
                report.err(
                    file_label,
                    "uses old `## Inline Persona for Sub-Agent` heading — rename to "
                    "`## Inline Persona for Teammate`",
                )
            else:
                report.warn(
                    file_label,
                    f"contains `## {deprecated}` — merge it into one of the 5 mandatory "
                    "sections (Identity / Success Criteria / Boundary / Output Schema / "
                    "Inline Persona for Teammate)",
                )

    return {}


def validate_workflow_md(path: Path, report: Report) -> None:
    if not path.exists():
        report.err("workflow.md", "missing — required by the 5-file structure")
        return

    text = path.read_text(encoding="utf-8")
    sections = find_h2_sections(text)

    for required in ("Overview", "Detailed Steps", "Acceptance Criteria"):
        if required not in sections:
            report.err("workflow.md", f"missing required section `## {required}`")

    # Mermaid is required in workflow.md — the primary expression difference from single-agent skills
    if not re.search(r"```mermaid", text):
        report.err(
            "workflow.md",
            "missing ```mermaid``` block — workflow.md must contain at least one "
            "mermaid diagram",
        )


def validate_bind_md(path: Path, report: Report) -> None:
    if not path.exists():
        report.err("bind.md", "missing — required by the 5-file structure")
        return

    text = path.read_text(encoding="utf-8")
    sections = find_h2_sections(text)

    for required in ("Resource Constraints", "Behavioral Constraints", "Failure Handling"):
        if required not in sections:
            report.err("bind.md", f"missing required section `## {required}`")

    # Resource Constraints should mention the 3 baseline items
    rc_body = section_body(text, "Resource Constraints")
    if rc_body is not None:
        for required_item in ("max_parallel_teammates", "total_wall_clock_budget", "total_token_budget"):
            if required_item not in rc_body:
                report.warn(
                    "bind.md",
                    f"## Resource Constraints does not mention `{required_item}` — "
                    "recommended as a baseline item",
                )


def validate_dependencies_yaml(path: Path, report: Report) -> dict[str, list[str]]:
    """Returns {'skills': [names], 'tools': [names]} for cross-file consistency check."""
    if not path.exists():
        report.err(
            "dependencies.yaml",
            "missing — required by the 5-file structure (write `skills: []` / "
            "`tools: []` if no deps)",
        )
        return {"skills": [], "tools": []}

    text = path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        report.err("dependencies.yaml", f"YAML parse error: {e}")
        return {"skills": [], "tools": []}

    if not isinstance(data, dict):
        report.err("dependencies.yaml", "root must be a mapping with `skills` and `tools` keys")
        return {"skills": [], "tools": []}

    declared = {"skills": [], "tools": []}

    for segment in ("skills", "tools"):
        if segment not in data:
            report.err("dependencies.yaml", f"missing required `{segment}:` segment (write `{segment}: []` if empty)")
            continue

        entries = data[segment]
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            report.err("dependencies.yaml", f"`{segment}:` must be a list (or `[]`)")
            continue

        required_fields = {
            "skills": ("name", "source", "required", "purpose"),
            "tools": ("name", "required", "purpose"),
        }[segment]

        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                report.err("dependencies.yaml", f"{segment}[{i}] must be an object")
                continue
            for field in required_fields:
                if field not in entry:
                    report.err(
                        "dependencies.yaml",
                        f"{segment}[{i}] (name={entry.get('name')!r}) missing required "
                        f"field `{field}`",
                    )
            if segment in declared and "name" in entry:
                declared[segment].append(entry["name"])

    return declared


# ---------------------------------------------------------------------------
# SwarmFlow script validator
# ---------------------------------------------------------------------------

def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError):
        return False


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _AgentCallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "agent":
            self.calls.append(node)
        self.generic_visit(node)


def _phase_call_value(stmt: ast.stmt) -> str | None:
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not isinstance(call.func, ast.Name) or call.func.id != "phase" or not call.args:
        return None
    return _string_literal(call.args[0])


def _agent_phase_mismatches(run_node: ast.AsyncFunctionDef) -> list[str]:
    mismatches: list[str] = []
    current_phase: str | None = None

    for stmt in run_node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        phase_value = _phase_call_value(stmt)
        if phase_value is not None:
            current_phase = phase_value

        collector = _AgentCallCollector()
        collector.visit(stmt)
        for call in collector.calls:
            phase_kw = next((kw for kw in call.keywords if kw.arg == "phase"), None)
            if phase_kw is None or current_phase is None:
                continue

            agent_phase = _string_literal(phase_kw.value)
            line = getattr(call, "lineno", "?")
            if agent_phase is None:
                mismatches.append(
                    f"line {line}: agent phase must be the literal current phase {current_phase!r}"
                )
            elif agent_phase != current_phase:
                mismatches.append(
                    f"line {line}: agent phase {agent_phase!r} does not match active phase {current_phase!r}"
                )

    return mismatches


def _collect_bound_names(tree: ast.Module) -> set[str]:
    """Over-approximate every name that could be bound anywhere in the module.

    Intentionally scope-agnostic: collecting names module-wide errs toward
    fewer false positives for the f-string check below.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                bound.add(arg.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                bound.add(arg.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            # Covers assignments, for/with targets, comprehension vars, walrus.
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _fstring_undefined_names(tree: ast.Module) -> list[tuple[int, str]]:
    """Find names referenced inside f-strings that are never bound anywhere.

    Catches the literal-brace trap: a prompt with `{N}` inside an f-string is
    parsed as the expression N and raises NameError at runtime (valid syntax,
    so ast.parse alone never flags it).
    """
    bound = _collect_bound_names(tree)
    builtin_names = set(dir(builtins))
    findings: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            for sub in ast.walk(value.value):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id in bound or sub.id in builtin_names:
                        continue
                    line = getattr(sub, "lineno", getattr(node, "lineno", 0))
                    key = (line, sub.id)
                    if key not in seen:
                        seen.add(key)
                        findings.append(key)
    return findings


def validate_swarmflow_script(path: Path, skill_name: str, report: Report) -> None:
    file_label = "scripts/workflow.py"
    if not path.exists():
        report.err(file_label, "missing — required for SwarmFlow script or script-only mode")
        return

    text = path.read_text(encoding="utf-8")

    if "TEMPLATE NOTES" in text:
        report.err(file_label, "contains template notes — delete before publishing")
    if re.search(r"<[^>\n]+>", text):
        report.err(file_label, "contains unresolved template placeholder(s) like `<...>`")
    if re.search(r"([A-Za-z]:\\|/Users/|/home/)", text):
        report.err(file_label, "contains an obvious local absolute path")

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        report.err(file_label, f"Python syntax error: {e.msg} at line {e.lineno}")
        return

    for line, name in _fstring_undefined_names(tree):
        report.err(
            file_label,
            f"line {line}: f-string references undefined name {name!r} — a literal "
            f"brace like {{{name}}} in prompt text is parsed as a Python expression "
            f"and crashes at runtime. Use string.Template ($-placeholders) or "
            f"concatenate the dynamic part instead.",
        )

    meta_node: ast.AST | None = None
    run_node: ast.AsyncFunctionDef | None = None
    has_swarmflow_import = False
    dangerous_imports: list[str] = []
    blocked_imports: list[str] = []
    dangerous_calls: list[str] = []
    non_inline_prompt_calls: list[int] = []

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "META":
                    meta_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "META"
        ):
            meta_node = node.value
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            run_node = node

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in {"os", "subprocess"}:
                    dangerous_imports.append(alias.name)
                if alias.name.split(".", 1)[0] == "skill_context":
                    blocked_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "swarmflow" or module.startswith("swarmflow."):
                has_swarmflow_import = True
            if module.split(".", 1)[0] in {"os", "subprocess"}:
                dangerous_imports.append(module)
            if module.split(".", 1)[0] == "skill_context":
                blocked_imports.append(module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                dangerous_calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                base = node.func.value
                if isinstance(base, ast.Name) and base.id in {"os", "subprocess"}:
                    dangerous_calls.append(f"{base.id}.{node.func.attr}")
                if node.func.attr == "build_prompt":
                    non_inline_prompt_calls.append(getattr(node, "lineno", 0))

    if meta_node is None:
        report.err(file_label, "missing top-level `META` assignment")
    elif not isinstance(meta_node, ast.Dict) or not _is_literal(meta_node):
        report.err(file_label, "`META` must be a pure literal dict")
    else:
        meta = ast.literal_eval(meta_node)
        if meta.get("name") != skill_name:
            report.warn(
                file_label,
                f"META.name ({meta.get('name')!r}) does not match "
                f"directory name ({skill_name!r})",
            )

    if run_node is None:
        report.err(file_label, "missing top-level `async def run(args)`")
    else:
        args = run_node.args.args
        arg_names = [arg.arg for arg in args]
        if arg_names != ["args"] or run_node.args.defaults:
            report.err(file_label, f"`run` parameters must be exactly `(args)` (got: {arg_names})")
        phase_mismatches = _agent_phase_mismatches(run_node)
        for mismatch in phase_mismatches:
            report.err(file_label, mismatch)

    if not has_swarmflow_import:
        report.err(file_label, "missing explicit `from swarmflow import ...` primitive import")
    if dangerous_imports:
        report.err(
            file_label,
            f"imports blocked module(s): {', '.join(sorted(set(dangerous_imports)))}",
        )
    if blocked_imports:
        report.err(
            file_label,
            "uses a non-standard SwarmFlow context import; "
            "use the standalone workflow format",
        )
    if dangerous_calls:
        report.err(file_label, f"uses blocked call(s): {', '.join(sorted(set(dangerous_calls)))}")
    if non_inline_prompt_calls:
        lines = ", ".join(str(line) for line in sorted(set(non_inline_prompt_calls)) if line)
        report.err(
            file_label,
            "SwarmFlow scripts must keep agent prompts inline in scripts/workflow.py "
            f"(non-inline prompt call at line(s): {lines})",
        )


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------

def cross_check_skills_tools(skill_fm: dict, declared: dict[str, list[str]], report: Report) -> None:
    """Every roles[].skills / roles[].tools in SKILL.md must appear in dependencies.yaml."""
    for role in skill_fm.get("roles", []):
        if not isinstance(role, dict):
            continue
        role_id = role.get("id", "?")
        for kind, segment in (("skills", "skills"), ("tools", "tools")):
            for item in role.get(kind, []) or []:
                if item not in declared[segment]:
                    report.err(
                        "dependencies.yaml",
                        f"`{item}` is declared in SKILL.md `roles[id={role_id!r}].{kind}` "
                        f"but missing from `{segment}:` segment",
                    )


def check_orphan_role_files(roles_dir: Path, declared_role_ids: list[str], report: Report) -> None:
    if not roles_dir.exists():
        return
    for f in roles_dir.glob("*.md"):
        role_id = f.stem
        if role_id not in declared_role_ids:
            report.warn(
                "roles/",
                f"orphan file `roles/{f.name}` — not declared in SKILL.md "
                "frontmatter `roles[]`",
            )


def detect_structure(root: Path, report: Report) -> tuple[bool, bool]:
    """Return (has_swarmflow_script, is_script_only)."""
    script_path = root / "scripts" / "workflow.py"
    has_script = script_path.exists()
    prompts_dir = root / "prompts"
    markdown_paths = [
        root / "roles",
        root / "workflow.md",
        root / "bind.md",
        root / "dependencies.yaml",
    ]
    present = [p for p in markdown_paths if p.exists()]
    has_partial_spec = 0 < len(present) < len(markdown_paths)

    if has_script and not present:
        if prompts_dir.exists():
            report.err(
                "prompts/",
                "script-only SwarmFlow skills must keep prompts inline in "
                "scripts/workflow.py; do not create prompts/",
            )
        return True, True

    if has_script and has_partial_spec:
        missing = [p.name for p in markdown_paths if not p.exists()]
        report.err(
            "structure",
            "partial Markdown spec with SwarmFlow script — either provide the full "
            "5-file spec or use script-only mode with only SKILL.md + "
            f"scripts/workflow.py. Missing: {', '.join(missing)}",
        )

    return has_script, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        logger.info("[USAGE ERROR] %s is not a directory", root)
        return 2

    logger.info("Validating Swarm Skill: %s\n", root.resolve())

    report = Report()

    # 0. Structure shape
    has_swarmflow_script, is_script_only = detect_structure(root, report)

    # 1. SKILL.md
    skill_fm, role_ids = validate_skill_md(root / "SKILL.md", report, script_only=is_script_only)

    if has_swarmflow_script:
        validate_swarmflow_script(root / "scripts" / "workflow.py", root.name, report)

    if is_script_only:
        report.emit()
        return 0 if report.passed() else 1

    # 2. roles/<id>.md
    roles_dir = root / "roles"
    if not roles_dir.exists():
        report.err("roles/", "directory missing — required by the 5-file structure")
    else:
        for role_id in role_ids:
            validate_role_file(roles_dir / f"{role_id}.md", role_id, report)
        check_orphan_role_files(roles_dir, role_ids, report)

    # 3. workflow.md
    validate_workflow_md(root / "workflow.md", report)

    # 4. bind.md
    validate_bind_md(root / "bind.md", report)

    # 5. dependencies.yaml + cross-check
    declared = validate_dependencies_yaml(root / "dependencies.yaml", report)
    if skill_fm is not None:
        cross_check_skills_tools(skill_fm, declared, report)

    report.emit()
    return 0 if report.passed() else 1


def main() -> int:
    if len(sys.argv) != 2:
        logger.info(__doc__)
        return 2
    return validate(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
