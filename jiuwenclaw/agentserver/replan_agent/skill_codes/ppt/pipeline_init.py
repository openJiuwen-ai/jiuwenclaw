from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode

logger = logging.getLogger(__name__)
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    BashResult as _BashResult,
    cli_path as _cli_path,
    combined_output as _combined_output,
    normalize_tool_text as _normalize_tool_text,
    parse_bash_payload as _parse_bash_payload,
    quote_path as _quote_path,
    run_bash as _run_bash,
)

_PLAYWRIGHT_INSTALL_TIMEOUT = 600

_NPM_INSTALL_MARKERS = (
    "npm 依赖未安装",
    "npm 依赖缺失",
    "node_modules 目录为空",
    "playwright 依赖缺失",
    "npm install",
)
_PLAYWRIGHT_INSTALL_MARKERS = (
    "Chromium 未安装",
    "Chromium Headless Shell 未安装",
    "chromium 未安装",
    "npx playwright install",
    "浏览器安装必须尝试",
)

# [TEMP-EXTERNAL-SKILL] 默认 skill 目录名，与原版 pptx-craft 区分。
_DEFAULT_SKILL_NAME = "pptx-craft-replan"


class PipelineInitError(RuntimeError):
    """P0 流水线初始化失败。"""


# [TEMP-EXTERNAL-SKILL] _BUILTIN_PPTX_ROOT 保留定义但不再作为 pptx_root fallback 或 workdir。
# 后续稳定版可删除此常量及 skill_codes/ppt 下的 scripts/styles/assets 拷贝文件。
_BUILTIN_PPTX_ROOT = str(Path(__file__).resolve().parent)


def _resolve_pptx_root(inputs: dict[str, Any]) -> str:
    """[TEMP-EXTERNAL-SKILL] 解析 pptx_root —— 只用外部 skill_root，不 fallback builtin。

    优先级：
    1. inputs["pptx_root"] — 显式指定（最高优先级）
    2. inputs["skill_root"] + inputs["skill_name"] — 外部 skill 目录拼接
    3. 找不到 → raise PipelineInitError
    """
    builtin = str(Path(__file__).resolve().parent)
    # 1. 显式指定 pptx_root
    pptx_root = inputs.get("pptx_root")
    if pptx_root:
        root = Path(str(pptx_root)).expanduser().resolve()
        if not root.is_dir():
            raise PipelineInitError(f"pptx_root 不存在: {root}")
        resolved = str(root)
        logger.info("[P0] pptx_root resolved (source=pptx_root): %s builtin=%s", resolved, builtin)
        return resolved

    # 2. skill_root + skill_name
    skill_root = inputs.get("skill_root")
    skill_name = inputs.get("skill_name") or _DEFAULT_SKILL_NAME
    if skill_root:
        skill_root_path = Path(str(skill_root)).expanduser().resolve()
        # skill_root 本身就是 skill 目录（目录名 == skill_name）
        if skill_root_path.name == skill_name and skill_root_path.is_dir():
            resolved = str(skill_root_path)
            logger.info("[P0] pptx_root resolved (source=skill_root_is_skill_dir): %s builtin=%s", resolved, builtin)
            return resolved
        # skill_root 是 skills 根目录，拼接 skill_name 子目录
        candidate = skill_root_path / skill_name
        if candidate.is_dir():
            resolved = str(candidate)
            logger.info("[P0] pptx_root resolved (source=skill_root+skill_name): %s builtin=%s", resolved, builtin)
            return resolved
        # 兜底尝试 pptx-craft（兼容旧配置）
        fallback = skill_root_path / "pptx-craft"
        if fallback.is_dir():
            resolved = str(fallback)
            logger.warning(
                "[P0] pptx_root resolved (source=fallback_pptx-craft): %s builtin=%s — "
                "skill_root 下未找到 %s 但存在 pptx-craft，使用旧目录",
                resolved, builtin, skill_name,
            )
            return resolved

    raise PipelineInitError(
        f"缺少 pptx_root 或 skill_root 配置，无法定位 {skill_name} 根目录。"
        f"请确保 JIUWENCLAW_SHARED_SKILLS_DIRS 环境变量指向包含 {skill_name} 子目录的路径。"
    )


_NPM_DEPS = {
    "commander": "^12.0.0",
    "express": "^4.21.0",
    "get-port": "^7.1.0",
    "playwright": "^1.52.0",
}

_PACKAGE_JSON_CONTENT = (
    '{"name":"ppt-scripts","version":"1.0.0","private":true,"type":"module",'
    f'"dependencies":{json.dumps(_NPM_DEPS)}}}\n'
)


async def _ensure_package_json(node: PlanNode, pptx_root: str) -> None:
    pkg_path = Path(pptx_root) / "package.json"
    if pkg_path.is_file():
        return
    if not node.has_tool("write_file"):
        raise PipelineInitError("write_file 工具不可用，无法生成 package.json")
    await node.call_tool(
        "write_file",
        file_path=str(pkg_path),
        content=_PACKAGE_JSON_CONTENT,
    )
    logger.info("[P0.1] 已生成 package.json: %s", pkg_path)


def _node_modules_ready(pptx_root: str) -> bool:
    nm = Path(pptx_root) / "node_modules"
    if not nm.is_dir():
        return False
    for dep in _NPM_DEPS:
        if not (nm / dep).is_dir():
            return False
    return True


async def _bash(
    node: PlanNode,
    command: str,
    *,
    timeout_seconds: int = 300,
    required: bool = True,
    workdir: str | None = None,
) -> _BashResult:
    try:
        return await _run_bash(
            node,
            command,
            timeout_seconds=timeout_seconds,
            required=required,
            workdir=workdir,
        )
    except BashExecError as exc:
        raise PipelineInitError(str(exc)) from exc


def _needs_npm_install(check_output: str) -> bool:
    if "→ 安装: cd " in check_output and "npm install" in check_output:
        return True
    return any(marker in check_output for marker in _NPM_INSTALL_MARKERS)


def _needs_playwright_install(check_output: str) -> bool:
    if "→ 安装: npx playwright install chromium" in check_output:
        return True
    if "环境就绪" in check_output:
        return False
    return any(marker in check_output for marker in _PLAYWRIGHT_INSTALL_MARKERS)


def _parse_cli_path(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("[ERROR]"):
            continue
        candidate = Path(line).expanduser()
        if candidate.is_absolute() or candidate.parts:
            return str(candidate.resolve()) if candidate.exists() else str(candidate)
    match = re.search(r"([A-Za-z]:\\[^\s\"']+|/[^\s\"']+)", output)
    if match:
        return str(Path(match.group(1)).expanduser().resolve())
    raise PipelineInitError(f"无法从命令输出解析路径:\n{output}")


def _resolve_explicit_output_dir(inputs: dict[str, Any]) -> str | None:
    """上游显式指定的最终产物目录（完整路径，不再追加时间戳）。"""
    explicit = inputs.get("output_dir")
    if not explicit:
        return None
    text = str(explicit).strip()
    if not text:
        return None
    return str(Path(text).expanduser().resolve())


def _resolve_timestamp_parent_dir(inputs: dict[str, Any]) -> str:
    """generate-timestamp-dir 的父目录，与 interface.prepare_files_for_agent 的 output 路径一致。"""
    project_dir = inputs.get("effective_project_dir")
    if project_dir:
        session = str(inputs.get("conversation_id") or inputs.get("session_id") or "default")
        user_id = str(inputs.get("user_id") or session)
        chat_id = str(inputs.get("chat_id") or session)
        return str(
            (Path(str(project_dir)) / "files" / user_id / chat_id / "output").resolve()
        )
    return _resolve_workspace_base(inputs)


def _resolve_workspace_base(inputs: dict[str, Any]) -> str:
    workspace_base = inputs.get("workspace_base")
    if workspace_base:
        return str(Path(str(workspace_base)).expanduser().resolve())
    workspace = inputs.get("workspace")
    if workspace:
        return str(Path(str(workspace)).expanduser().resolve())
    return str(Path("./workspace").expanduser().resolve())


class P01EnvDepsNode(PlanNode):
    """P0.1 — check-env、npm install、playwright install。

    预期输入（ctx / inputs）:
        必填（二选一）: pptx_root | skill_root + skill_name

    预期输出（写入同一 ctx）:
        pptx_root: str — pptx-craft-replan 根目录绝对路径
        env_ok: bool — 环境检测与 npm 依赖就绪
        playwright_ready: bool — Chromium 是否可用（安装失败时为 False，不阻塞后续节点）
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p0_1_env_deps",
            instruction=(
                "执行 pptx-craft-replan 环境检测：node cli.js check-env。"
                "缺失 npm 依赖时 cd pptx_root && npm install（必须成功）。"
                "缺失 Chromium 时 npx playwright install chromium（必须尝试；"
                "超时或失败不阻塞，标记 playwright_ready=False）。"
                "禁止读取 cli.js 源码。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pptx_root = _resolve_pptx_root(inputs)
        inputs["pptx_root"] = pptx_root

        # skill_checksum_ok 兜底：Executor 正常注入时已有值，未注入时默认 True。
        inputs.setdefault("skill_checksum_ok", True)

        await _ensure_package_json(self, pptx_root)

        if not _node_modules_ready(pptx_root):
            logger.info("[P0.1] npm install 开始: %s", pptx_root)
            await _bash(
                self, "npm install",
                timeout_seconds=600, required=True, workdir=pptx_root,
            )
            logger.info("[P0.1] npm install 完成")

        check_cmd = _cli_path("check-env", pptx_root)
        check_result = await _bash(
            self, check_cmd, required=False, workdir=pptx_root,
        )
        check_output = _combined_output(check_result)

        inputs["env_ok"] = True

        if _needs_playwright_install(check_output):
            logger.info("[P0.1] playwright install chromium 开始")
            try:
                await _bash(
                    self, "npx playwright install chromium",
                    timeout_seconds=_PLAYWRIGHT_INSTALL_TIMEOUT,
                    required=True, workdir=pptx_root,
                )
                inputs["playwright_ready"] = True
                logger.info("[P0.1] playwright install 完成")
            except PipelineInitError:
                inputs["playwright_ready"] = False
                logger.warning("[P0.1] playwright install 失败，继续执行")
        else:
            inputs["playwright_ready"] = True

        return inputs

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": "正在检测 pptx-craft-replan 环境依赖...",
        }
        result = await self._execute(inputs)
        yield {
            "node": self.plan_name,
            "status": "ok",
            "message": "pptx-craft-replan 环境依赖检测完成",
            **result,
        }


class P02WorkspaceInitNode(PlanNode):
    """P0.2 — 解析 pptx_root、创建 output_dir / pages_dir、初始化会话变量。

    预期输入（ctx / inputs）:
        必填（二选一）: pptx_root | skill_root（通常由 P0.1 写入）
        可选: output_dir — 用户指定输出目录绝对路径；有则跳过 generate-timestamp-dir
        可选: workspace_base | workspace — 自动生成时间戳目录时的父路径（默认 ./workspace）

    预期输出（写入同一 ctx）:
        pptx_root: str
        output_dir: str — 会话产物根目录绝对路径
        pages_dir: str — HTML 页面目录绝对路径（{output_dir}/pages）
        session_dir: str — 等于 output_dir
        output_dir_user_specified: bool — 是否使用了上游传入的 output_dir
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p0_2_workspace_init",
            instruction=(
                "解析 pptx_root；确定 output_dir（用户指定则沿用，否则 generate-timestamp-dir）；"
                "调用 ensure-output-dir 创建 pages 子目录；"
                "初始化 session_dir、pages_dir、pptx_root 到会话上下文。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pptx_root = _resolve_pptx_root(inputs)
        inputs["pptx_root"] = pptx_root

        output_dir = _resolve_explicit_output_dir(inputs)
        if output_dir:
            inputs["output_dir_user_specified"] = True
        else:
            workspace_base = _resolve_timestamp_parent_dir(inputs)
            gen_cmd = (
                f"{_cli_path('generate-timestamp-dir', pptx_root)} "
                f"{_quote_path(workspace_base)}"
            )
            gen_result = await _bash(
                self, gen_cmd, required=True, workdir=pptx_root,
            )
            output_dir = _parse_cli_path(_combined_output(gen_result))
            inputs["output_dir_user_specified"] = False

        output_path = Path(output_dir)
        output_dir = str(output_path.resolve())

        ensure_cmd = (
            f"{_cli_path('ensure-output-dir', pptx_root)} "
            f"{_quote_path(output_dir)}"
        )
        ensure_result = await _bash(
            self, ensure_cmd, required=True, workdir=pptx_root,
        )
        pages_dir = _parse_cli_path(_combined_output(ensure_result))

        inputs["output_dir"] = output_dir
        inputs["pages_dir"] = pages_dir
        inputs["session_dir"] = output_dir
        return inputs

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield {
            "node": self.plan_name,
            "status": "progress",
            "message": "正在初始化 PPT 输出工作区...",
        }
        result = await self._execute(inputs)
        yield {
            "node": self.plan_name,
            "status": "ok",
            "message": "PPT 输出工作区初始化完成",
            **result,
        }


class PipelineInitNode(PlanNode):
    """P0 — 流水线启动 / 环境预置（P0.1 → P0.2）。

    预期输入（ctx / inputs）:
        必填（二选一）: pptx_root | skill_root
        可选: output_dir — 用户指定输出目录（由 RePlanAgent 入口结构化传入）
        可选: workspace_base | workspace — 未指定 output_dir 时的时间戳目录父路径

    预期输出（写入同一 ctx，为 P0.1 + P0.2 并集）:
        pptx_root, env_ok, playwright_ready,
        output_dir, pages_dir, session_dir, output_dir_user_specified
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p0_pipeline_init",
            instruction=(
                "流水线启动与环境预置：P0.1 检测并安装 Node/npm/playwright；"
                "P0.2 解析 pptx_root、创建 output_dir 与 pages_dir、初始化会话变量。"
                "不调 LLM，仅通过 cli.js 与 bash 完成。"
            ),
            sub_plans=[
                P01EnvDepsNode(),
                P02WorkspaceInitNode(),
            ],
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        ctx = inputs
        await self.execute_subplan(self.sub_plans[0], ctx)
        await self.execute_subplan(self.sub_plans[1], ctx)
        return ctx

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        ctx = inputs
        for subplan in self.sub_plans:
            async for chunk in self.execute_subplan_stream(subplan, ctx):
                yield chunk
        yield {
            **ctx,
            "node": self.plan_name,
            "status": "ok",
            "message": "流水线启动与环境预置完成",
        }
