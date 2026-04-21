# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""INIT 阶段处理器.

职责：
1. 创建工作区目录（resources/ref-files/ resources/ref-skills/ resources/tool_specs/ skill/ evals/ output/）
2. 分类解析上传内容（base64 → 文件 → 按类型解压/写入）
3. 判断任务模式（CREATE / CREATE_WITH_RESOURCES / MODIFY）
"""

from __future__ import annotations

import base64
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
from jiuwenclaw.agentserver.skilldev.zip_extract import safe_extract_zip

logger = logging.getLogger(__name__)


class InitStageHandler(StageHandler):
    """INIT 阶段：分类写入参考资料与工具说明，推断任务模式。"""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        await ctx.emit(SkillDevEventType.PROGRESS, {"message": "正在初始化工作区..."})

        # 工作区已由 Pipeline 的 ensure_local 创建，此处直接使用
        ref_files_dir = ctx.workspace / "resources" / "ref-files"
        ref_skills_dir = ctx.workspace / "resources" / "ref-skills"
        tool_specs_dir = ctx.workspace / "resources" / "tool_specs"
        # 解析上传的资源文件（普通参考文件）
        ref_files = ctx.state.input.get("files") or []
        logger.info(f"[InitStage], {ctx.state.input}")
        if ref_files:
            ref_files_dir.mkdir(parents=True, exist_ok=True)
            await ctx.emit(
                SkillDevEventType.PROGRESS,
                {"message": f"正在处理 {len(ref_files)} 个资源文件..."},
            )
            await self._write_resources(ref_files, ref_files_dir, extract_zip_to_subdir=True)

        # 解析上传的参考 skill 压缩包
        skill_packages = ctx.state.input.get("skill_packages") or []
        if skill_packages:
            ref_skills_dir.mkdir(parents=True, exist_ok=True)
            await ctx.emit(
                SkillDevEventType.PROGRESS,
                {"message": f"正在处理 {len(skill_packages)} 个参考 Skill 包..."},
            )
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
            # 保存工具
            await ctx.emit(
                SkillDevEventType.PROGRESS,
                {"message": f"正在处理 {len(tool_specs)} 个工具说明文件..."},
            )
            await self._write_resources(tool_specs, tool_specs_dir, extract_zip_to_subdir=False)

        # 更新目录空状态：skill和resources目录
        skill_dir = ctx.workspace / "skill"
        ctx.state.ref_files_dir_empty = not any(ref_files_dir.iterdir()) if ref_files_dir.exists() else True
        ctx.state.ref_skills_dir_empty = not any(ref_skills_dir.iterdir()) if ref_skills_dir.exists() else True
        ctx.state.skill_dir_empty = not any(skill_dir.iterdir()) if skill_dir.exists() else True
        ctx.state.tool_specs_dir_empty = not any(tool_specs_dir.iterdir()) if tool_specs_dir.exists() else True

        #  校验工具格式并加载到state中，每个 tool dict 至少须包含 "name" 字段，否则跳过
        if not ctx.state.tool_specs_dir_empty:
            ctx.state.external_tools = self._parse_tools(tool_specs)
            logger.info("[InitStage] 加载外部工具: %d 个", len(ctx.state.external_tools))

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
        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": "初始化完成，准备生成开发计划"}
        )
        return StageResult(next_stage=SkillDevStage.CLARIFY)

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

    def _parse_tools(self, tools_input: list | str) -> list[dict]:
        """解析工具定义输入，返回合法的 tool dict 列表。

        接受两种形式：
        - list[dict]：直接传入的工具列表
        - str：JSON 字符串，解析后应为 list[dict]

        每个 tool dict 至少须包含 "name" 字段，不合规的项目会被跳过并记录警告。
        """
        import json as _json

        if isinstance(tools_input, str):
            try:
                tools_input = _json.loads(tools_input)
            except Exception as exc:
                logger.warning("[InitStage] tools 字段 JSON 解析失败: %s", exc)
                return []

        if not isinstance(tools_input, list):
            logger.warning("[InitStage] tools 字段应为 list，实际类型: %s", type(tools_input).__name__)
            return []

        valid = []
        for i, item in enumerate(tools_input):
            if not isinstance(item, dict):
                logger.warning("[InitStage] tools[%d] 不是 dict，跳过", i)
                continue
            if not item.get("name"):
                logger.warning("[InitStage] tools[%d] 缺少 'name' 字段，跳过", i)
                continue
            valid.append(item)

        return valid

