# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""INIT 阶段处理器.

职责：
1. 创建工作区目录（resources/ref-files/ resources/ref-skills/ resources/tool_specs/ skill/ evals/ output/）
2. 分类解析上传内容（base64 → 文件 → 按类型解压/写入）
3. 判断任务模式（CREATE / CREATE_WITH_RESOURCES / MODIFY）
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from jiuwenclaw.agentserver.skilldev.context import SkillDevContext
from jiuwenclaw.agentserver.skilldev.schema import (
    SkillDevEventType,
    SkillDevStage,
    SkillDevTaskMode,
    # determine_task_mode,
)
from jiuwenclaw.agentserver.skilldev.stages.base import StageHandler, StageResult
from jiuwenclaw.agentserver.skilldev.utils import safe_extract_zip
from jiuwenclaw.agentserver.skilldev.stages.validate_stage import (
    parse_skill_frontmatter,
)


logger = logging.getLogger(__name__)

_SKILL_NAME_SYSTEM_PROMPT = """你是命名助手。请基于用户需求与已上传资料生成一个简短的 skill 标识名。

要求：
1) 只输出 skill 名称本身，不要解释。
2) 使用 kebab-case，仅允许小写字母、数字、短横线。
3) 语义清晰，长度建议 2-6 个词。
4) 当用户指令信息不足时，必须优先查看上传资料的文件名与关键内容后再命名。
"""


class InitStageHandler(StageHandler):
    """INIT 阶段：分类写入参考资料与工具说明，推断任务模式。"""

    async def execute(self, ctx: SkillDevContext) -> StageResult:

        # 工作区已由 Pipeline 的 ensure_local 创建，此处直接使用
        ref_files_dir = ctx.workspace / "resources" / "ref-files"
        ref_skills_dir = ctx.workspace / "resources" / "ref-skills"
        tool_specs_dir = ctx.workspace / "resources" / "tool_specs"
        # 解析上传的资源文件（普通参考文件）
        ref_files = ctx.state.input.get("files") or []
        logger.info(f"[InitStage], {ctx.state.input}")
        if ref_files:
            ref_files_dir.mkdir(parents=True, exist_ok=True)
            await self._write_resources(ref_files, ref_files_dir, extract_zip_to_subdir=True)

        # 解析上传的参考 skill 压缩包
        skill_packages = ctx.state.input.get("skill_packages") or []
        if skill_packages:
            ref_skills_dir.mkdir(parents=True, exist_ok=True)
            await self._write_resources(
                skill_packages,
                ref_skills_dir,
                extract_zip_to_subdir=True,
                allowed_suffixes=(".zip", ".skill"),
            )

        # 解析上传的外部工具定义（list[dict]，每项为一个 tool）
        tool_specs = ctx.state.input.get("tool_spec_files") or []
        if tool_specs:
            tool_specs_dir.mkdir(parents=True, exist_ok=True)
            await self._write_resources(tool_specs, tool_specs_dir, extract_zip_to_subdir=False)
            #  校验工具格式并加载到state中，每个 tool dict 至少须包含 "name" 字段，否则跳过
            ctx.state.external_tools = self._parse_tools(tool_specs_dir)
            logger.info("[InitStage] 加载外部工具: %d 个", len(ctx.state.external_tools))


        # 更新目录空状态：skill和resources目录
        skill_dir = ctx.workspace / "skill"
        ctx.state.ref_files_dir_empty = not any(ref_files_dir.iterdir()) if ref_files_dir.exists() else True
        ctx.state.ref_skills_dir_empty = not any(ref_skills_dir.iterdir()) if ref_skills_dir.exists() else True
        ctx.state.skill_dir_empty = not any(skill_dir.iterdir()) if skill_dir.exists() else True
        ctx.state.tool_specs_dir_empty = not any(tool_specs_dir.iterdir()) if tool_specs_dir.exists() else True

        # 写完文件后，通过内容扫描推断任务模式
        logger.info(
            "[InitStage] task_id=%s mode=%s ref_files_dir_empty=%s "
            "ref_skills_dir_empty=%s skill_dir_empty=%s tool_specs_dir_empty=%s",
            ctx.task_id,
            ctx.state.mode.value,
            ctx.state.ref_files_dir_empty,
            ctx.state.ref_skills_dir_empty,
            ctx.state.skill_dir_empty,
            ctx.state.tool_specs_dir_empty,
        )

        if not ctx.state.skill_dir_empty:
            skill_md = skill_dir / "SKILL.md"
            skill_name, _, _ = parse_skill_frontmatter(skill_md)
            ctx.state.skill_name = skill_name
        else:
            skill_name = await self._generate_skill_name(ctx)
            logger.info("[InitStage] 基于指令与上传内容生成 skill_name: %s", skill_name)
            ctx.state.skill_name = skill_name
            await ctx.emit(
                SkillDevEventType.SKILL_NAME_READY,
                {"skill_name": skill_name},
            )
        return StageResult(next_stage=SkillDevStage.CLARIFY)

    async def _generate_skill_name(self, ctx: SkillDevContext) -> str:
        """根据用户输入生成规范化的 skill_name（kebab-case）."""
        user_query = str(ctx.state.input.get("query", "")).strip()
        agent = ctx.create_stage_agent(
            stage_name="init",
            system_prompt=_SKILL_NAME_SYSTEM_PROMPT,
            tools=["file_read", "file_glob", "file_listdir"],
            max_iterations=8,
        )
        query = self._build_skill_name_query(ctx, user_query=user_query)
        try:
            raw_name = await ctx.run_stage_agent_streaming(agent, stage_name="init", query=query)
        except Exception as exc:
            logger.warning("[InitStage] 生成 skill_name 失败，使用兜底规则: %s", exc)
            raw_name = user_query
        return self._normalize_skill_name(raw_name, fallback_text=user_query)

    def _build_skill_name_query(self, ctx: SkillDevContext, *, user_query: str) -> str:
        """构造命名 query，要求 Agent 结合用户指令与上传内容."""
        parts = [
            "请生成一个准确的 skill_name。",
            f"用户需求：{user_query or '（未提供有效需求文本）'}",
            "命名前请优先检查工作区上传内容（至少查看目录与关键文件名，必要时读取文件内容）：",
            "- resources/ref-files/",
            "- resources/ref-skills/",
            "- resources/tool_specs/",
            "- skill/（如果已有历史 SKILL.md）",
            "只输出最终 skill_name，不要解释。",
        ]
        return "\n".join(parts)

    def _normalize_skill_name(self, candidate: str, *, fallback_text: str = "") -> str:
        """清洗任意文本为 kebab-case skill 名称."""
        text = (candidate or "").strip().lower()
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"```$", "", text).strip()
        text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
        text = re.sub(r"[_\s]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")

        if not text:
            fallback = (fallback_text or "generated skill").lower()
            fallback = re.sub(r"[^\w\s-]", " ", fallback, flags=re.UNICODE)
            fallback = re.sub(r"[_\s]+", "-", fallback)
            text = re.sub(r"-{2,}", "-", fallback).strip("-")

        if not text:
            text = "generated-skill"
        return text[:64].strip("-") or "generated-skill"

    async def _write_resources(
        self,
        resources: list[dict],
        dest_dir: Path,
        *,
        extract_zip_to_subdir: bool,
        allowed_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        """将所有资源文件 base64 解码后写入 dest_dir。

        对 .zip/.skill 文件执行 Python zipfile 解压；
        """
        for res in resources:
            name = res.get("filename", "unknown")
            content_b64 = res.get("base64Data", "")
            try:
                raw = base64.b64decode(content_b64)
                file_path = dest_dir / name
                suffix = file_path.suffix.lower()
                if allowed_suffixes and suffix not in allowed_suffixes:
                    raise ValueError(f"不支持的文件类型: {name}")
                file_path.write_bytes(raw)
                logger.info("[InitStage] 写入资源: %s (%d bytes)", name, len(raw))

                if suffix in (".zip", ".skill"):
                    safe_extract_zip(file_path, dest_dir, extract_to_stem_dir=extract_zip_to_subdir)
                elif suffix == ".rar":
                    raise NotImplementedError(f"RAR format not supported, please use zip instead.")
            except Exception as exc:
                logger.warning(
                    "[InitStage] 资源文件写入失败: name=%s error=%s", name, exc
                )

    def _parse_tools(self, tools_dir: Path) -> list[dict]:
        """从工具目录解析工具定义，返回合法的 tool dict 列表。

        在 tools_dir 中读取全部 .json 文件并合并（内容应为 list[dict]）。
        每个 tool dict 至少须包含 "name" 字段，不合规项会被跳过。
        """
        if not tools_dir.exists() or not tools_dir.is_dir():
            logger.warning("[InitStage] 工具目录不存在或不是目录: %s", tools_dir)
            return []

        json_files = sorted(tools_dir.glob("*.json"))
        if not json_files:
            logger.warning("[InitStage] 工具目录中未找到 JSON 文件: %s", tools_dir)
            return []

        valid: list[dict] = []
        for json_file in json_files:
            try:
                parsed = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[InitStage] 读取工具文件失败: file=%s error=%s", json_file.name, exc)
                continue

            if not isinstance(parsed, list):
                logger.warning("[InitStage] 工具文件内容应为 list: file=%s", json_file.name)
                continue

            for i, item in enumerate(parsed):
                if not isinstance(item, dict):
                    logger.warning("[InitStage] %s[%d] 不是 dict，跳过", json_file.name, i)
                    continue
                if not item.get("name"):
                    logger.warning("[InitStage] %s[%d] 缺少 'name' 字段，跳过", json_file.name, i)
                    continue
                valid.append(item)

        return valid

