# Aidlc skills path resolution

Canonical reference for **where skills live**, how **Leader** resolves paths, and what **downstream workers** can read. Role agent files may still say `skills/<name>/`; treat that as **relative to `skills_root`** below until those files are updated.

## Variables

| Symbol | Meaning |
|--------|---------|
| `repo-root` | Business project root: source, tests, `doc/<module>/`, `.venv` |
| `skills_root` | Directory containing shared Aidlc skills (`dev-*`, `env-setup`, `dispatch-parallel`, …) |

Gate scripts always use `--repo-root` for **business** artifacts; script paths use **`skills_root`**.

## Resolve `skills_root` (Leader @ G0)

Apply in order; stop at first match:

1. **Task card** `skills_root` (absolute path) exists and contains `dev-analyzer/` or `dev-tester/`.
2. **`<repo-root>/skills/`** exists and contains `dev-analyzer/` or `dev-tester/`.
3. **Workspace / AidlcSkills root** `<workspace>/skills/` (same layout as this repo).
4. **BLOCK** — ask user via `user-interact`: follow [README](../../../README.md) installation section — full `skills/` + platform dir — or set `skills_root` on the task card.

**Default when co-located:** `skills_root` = `<repo-root>/skills`.

**Split layout (common):** `repo-root` = business clone; `skills_root` = AiDlcSkills `skills/` (absolute). Leader **must** put `skills_root` on every spawn task card.

## Platform: global vs repo

Host **repo** column = where the IDE loads project-level skills. **`skills_root`** for Aidlc `dev-*` / gate scripts follows §Resolve below — often `<repo-root>/skills`, not always the same directory as the host repo column.

| Platform | Repo (project) skills | Global (user) skills | Worker `Read` scope |
|----------|-------------------------|----------------------|---------------------|
| **Claude Code** | `<root>/.claude/skills/` | `~/.claude/skills/` | Sub-agent workspace ≈ **`repo-root`**; global skills are **Main-only** auto-inject |
| **Cursor** | `<root>/.cursor/skills/` | `~/.cursor/skills/` | Task/sub-agent sees **opened workspace**; mirror `skills/` under project or set `skills_root` |
| **Codex CLI** | `<root>/.codex/skills/` | `~/.codex/skills/` | Same as repo-root + task card `skills_root` |
| **OpenCode** | `<root>/.opencode/skills/` | `~/.config/opencode/skills/` | Same as repo-root + task card `skills_root` |
| **JiuwenSwarm** | No local skills | `~/.jiuwenswarm/agent/workspace/skills/` | Roles load `skills/dev-<role>/` under global `skills_root`; see `aidlc-dev-team` `roles/*.md` |

**JiuwenSwarm @ G0:** if steps 1–2 miss, use `~/.jiuwenswarm/agent/workspace/skills/` when it contains `dev-analyzer/` or `dev-tester/`.

**Rule:** After G0 locks `skills_root` on the task card, shorthand `skills/<name>/…` in Leader docs, gates, and spawn prompts means **`{skills_root}/<name>/…`**, not `~/.claude/skills/…`. Only the task card repeats the absolute `skills_root`.

## Leader task card (spawn)

Always include (absolute paths):

```text
repo-root: <business root>
skills_root: <directory containing dev-* skills>
python: <repo-root>/.venv/.../python.exe
```

When `skills_root` ≠ `repo-root`, add one line for workers:

```text
Load SKILL: {skills_root}/dev-<role>/SKILL.md (not under repo-root unless listed there).
```

Gate example (split layout):

```powershell
& $PYTHON "$skills_root/dev-tester/scripts/tester_plan_check.py" --module <module> --repo-root <repo-root> verify
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Worker: “skill file not found” at `skills/dev-*/SKILL.md` | Only global/user skills installed; business repo has no `skills/` | Copy or junction `skills/` into `repo-root`, or set `skills_root` on task card |
| Leader runs; worker fails gates | Main used `~/.claude/skills`; worker cwd is `repo-root` | Same as above |
| `check_*.py` not found | Gate ran with wrong cwd; `skills/...` not under `skills_root` | Run from `skills_root` or use absolute `skills_root/...` in the command |

## Related

- [layer-alignment.md](layer-alignment.md) — cross-stage rules  
- `skills/env-setup/references/python-env.md` §skills 仓与业务仓分离 — venv vs `repo-root`  
- `skills/bench-runner/SKILL.md` — explicit `paths.skills_root` for benchmarks
