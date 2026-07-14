# Project Maintainer Docs

Status: partial
Last updated: 2026-07-07
Sync status: partial

## Project Brief

- Name: JiuwenSwarm
- Purpose: Multi-agent collaboration runtime with web, TUI, CLI, IM, ACP, A2A, distributed team, skill, memory, and sandbox capabilities.
- Primary users: Developers and teams using agent orchestration, multi-channel assistants, and task automation.
- Main runtime: Python package with AgentServer and Gateway processes, plus React/TypeScript frontends.
- Tech stack: Python 3.11+, websockets, FastAPI, OpenJiuwen, pytest, TypeScript/React, package and installer scripts.

## How To Read

Start with `INDEX.md`, then `manifest.yaml`, then the module, directory, flow, or code symbol relevant to the task.

The first build prioritizes `agentserver` runtime analysis. Project-wide source inventory exists, but complete per-symbol documentation and trusted health audit remain pending because this repository has 1236 inventoried source files and 14746 discovered symbols.

## Maintenance Rules

- Keep files within the size budgets in the Project Maintainer skill.
- Use `project/build-plan.md` as the operational queue for pending slices.
- Keep `project/coverage-map.json` and `project/symbol-audit-map.json` summaries current after inventory refreshes.
- For very large ledgers, store full generated JSON in compressed machine files and keep the project JSON files as navigable summaries.
- Do not mark this artifact `current` until stable tracked paths are mapped or out of scope, every required source symbol has an entry doc with `Actual Role` and health, required flows are documented or out of scope, and requested-scope audit symbols are audited or out of scope.
- Treat `audit.status: unaudited` as pending even when a symbol card has a useful first-pass behavior summary.
