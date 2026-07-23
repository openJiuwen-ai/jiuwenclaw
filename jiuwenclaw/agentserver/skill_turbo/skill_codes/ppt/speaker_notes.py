"""Stage 8 — 演讲备注生成与注入（仅 need_speaker_notes=True 时执行）。

prod 契约：
1. 取语调规则（优先 tone-style skill，降级为内置默认）
2. cli notes extract-text 抽取每页可见纯文本
3. 按页并发 LLM 生成备注分片 speaker-notes-page-{N}.txt
4. 分片校验（缺失/空页重跑一次）
5. 单进程 cli notes inject 写回 .pptx

best-effort：任何失败都不阻塞 PPTX 交付。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    cli_path,
    quote_path,
    run_bash,
)

logger = logging.getLogger(__name__)

_DEFAULT_TONE = "简洁干练"


class SpeakerNotesNode(PlanNode):
    """Stage 8 — 演讲备注生成与注入。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p11_speaker_notes",
            instruction=(
                "## Stage 8 演讲备注生成与注入\n"
                "仅 need_speaker_notes=True 时执行，否则跳过。\n"
                "best-effort：任何失败都不阻塞 PPTX 交付。\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        need = bool(inputs.get("need_speaker_notes"))
        if not need:
            logger.info("[P11] need_speaker_notes=False，跳过演讲备注")
            return {"speaker_notes_status": "skipped", "speaker_notes_message": "未触发演讲备注"}

        pptx_path = str(inputs.get("pptx_path") or "").strip()
        pages_dir = str(inputs.get("pages_dir") or "").strip()
        output_dir = str(inputs.get("output_dir") or "").strip()
        pptx_root = str(inputs.get("pptx_root") or "").strip()
        total_pages = int(inputs.get("total_pages") or 0)
        topic = str(inputs.get("topic") or "").strip()
        audience = str(inputs.get("audience") or "").strip()
        presentation_purpose = str(inputs.get("presentation_purpose") or "").strip()

        if not pptx_path or not pages_dir or not pptx_root:
            logger.warning("[P11] 缺少必要路径，跳过演讲备注: pptx=%s pages=%s root=%s",
                           bool(pptx_path), bool(pages_dir), bool(pptx_root))
            return {"speaker_notes_status": "skipped", "speaker_notes_message": "缺少必要路径"}

        if total_pages <= 0:
            page_count = int(inputs.get("page_count") or 0)
            total_pages = page_count + 2

        # 1. 取语调规则
        tone_rules = await self._get_tone_rules(inputs)

        # 2. cli notes extract-text 抽取每页可见纯文本
        page_texts = await self._extract_page_texts(pptx_path, pptx_root)

        # 3. 按页并发生成备注分片
        await self._generate_notes_per_page(
            pages_dir, page_texts, total_pages,
            topic, audience, presentation_purpose, tone_rules,
        )

        # 4. 分片校验 + 缺失重跑
        await self._validate_and_retry(
            pages_dir, page_texts, total_pages,
            topic, audience, presentation_purpose, tone_rules,
        )

        # 5. 单进程注入
        inject_ok = await self._inject_notes(pptx_path, pages_dir, pptx_root)

        status = "ok" if inject_ok else "partial"
        msg = "演讲备注已注入" if inject_ok else "演讲备注注入失败（不阻塞交付）"
        logger.info("[P11] 演讲备注完成 status=%s", status)
        return {
            "speaker_notes_status": status,
            "speaker_notes_message": msg,
        }

    async def _get_tone_rules(self, inputs: dict[str, Any]) -> str:
        """取语调规则：优先 tone-style skill，降级为默认。"""
        # 尝试调用 tone-style skill
        if self.has_tool("Skill"):
            try:
                tone_req = str(inputs.get("tone_requirement") or "").strip()
                result = await self.call_tool("Skill", skill="tone-style", args=tone_req)
                if isinstance(result, str) and result.strip():
                    logger.info("[P11] tone-style skill 返回语调规则")
                    return result.strip()
                if isinstance(result, dict):
                    content = result.get("content") or result.get("result") or ""
                    if isinstance(content, str) and content.strip():
                        logger.info("[P11] tone-style skill 返回语调规则")
                        return content.strip()
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P11] tone-style skill 不可用，降级: %s", e)

        # 降级：用内置默认语调指引
        audience = str(inputs.get("audience") or "").strip()
        tone_hint = str(inputs.get("tone_requirement") or "").strip()
        if not tone_hint:
            tone_hint = _DEFAULT_TONE
        rule = f"语调：{tone_hint}。受众：{audience or '一般受众'}。"
        logger.info("[P11] 使用降级语调规则: %s", rule)
        return rule

    async def _extract_page_texts(self, pptx_path: str, pptx_root: str) -> dict[int, str]:
        """cli notes extract-text 抽取每页可见纯文本。"""
        try:
            cmd = f"{cli_path('notes', pptx_root)} extract-text --pptx {quote_path(pptx_path)}"
            result = await run_bash(self, cmd, timeout_seconds=60, required=False, workdir=pptx_root)
            if result.exit_code != 0:
                logger.warning("[P11] notes extract-text 失败 exit=%d: %s",
                               result.exit_code, (result.stderr or "")[:300])
                return {}
            raw = result.stdout or ""
            # 尝试解析 JSON {page: text}
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {int(k): str(v) for k, v in data.items()}
            except json.JSONDecodeError:
                pass
            logger.warning("[P11] notes extract-text 返回非 JSON，跳过")
            return {}
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P11] notes extract-text 异常: %s", e)
            return {}

    async def _generate_notes_per_page(
        self,
        pages_dir: str,
        page_texts: dict[int, str],
        total_pages: int,
        topic: str,
        audience: str,
        presentation_purpose: str,
        tone_rules: str,
    ) -> None:
        """按页并发生成备注分片。"""
        async def _gen_one(page_num: int) -> None:
            page_text = page_texts.get(page_num, "")
            # 判断页类型
            if page_num == 1:
                page_type = "cover"
            elif page_num >= total_pages:
                page_type = "ending"
            else:
                page_type = "content"

            prompt = (
                f"你是演讲备注撰写者。请为第 {page_num}/{total_pages} 页幻灯片生成口播备注。\n"
                f"页类型：{page_type}\n"
                f"页可见文本：{page_text[:2000]}\n"
                f"主题：{topic}\n"
                f"受众：{audience}\n"
                f"演讲目的：{presentation_purpose}\n"
                f"语调规则：{tone_rules}\n"
                f"要求：生成纯文本口播备注，50-200字，直接输出备注正文，不要解释。\n"
            )
            try:
                notes = await self.stream_llm_collect(
                    prompt=prompt,
                    system_prompt="你是演讲备注撰写专家，直接输出口播备注正文。",
                )
                if notes and notes.strip():
                    out_path = Path(pages_dir) / f"speaker-notes-page-{page_num}.txt"
                    await PptCommon.write_file(self, out_path, notes.strip())
                    logger.debug("[P11] 生成备注分片 page=%d", page_num)
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P11] 生成备注分片失败 page=%d: %s", page_num, e)

        tasks = [_gen_one(i) for i in range(1, total_pages + 1)]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _validate_and_retry(
        self,
        pages_dir: str,
        page_texts: dict[int, str],
        total_pages: int,
        topic: str,
        audience: str,
        presentation_purpose: str,
        tone_rules: str,
    ) -> None:
        """分片校验：缺失/空页重跑一次。"""
        for page_num in range(1, total_pages + 1):
            out_path = Path(pages_dir) / f"speaker-notes-page-{page_num}.txt"
            content = await PptCommon.read_file(self, str(out_path), label=f"notes-page-{page_num}")
            if content and content.strip():
                continue
            # 缺失，重跑一次
            logger.warning("[P11] 备注分片缺失 page=%d，重跑", page_num)
            page_text = page_texts.get(page_num, "")
            if page_num == 1:
                page_type = "cover"
            elif page_num >= total_pages:
                page_type = "ending"
            else:
                page_type = "content"
            prompt = (
                f"你是演讲备注撰写者。请为第 {page_num}/{total_pages} 页幻灯片生成口播备注。\n"
                f"页类型：{page_type}\n"
                f"页可见文本：{page_text[:2000]}\n"
                f"主题：{topic}\n"
                f"受众：{audience}\n"
                f"演讲目的：{presentation_purpose}\n"
                f"语调规则：{tone_rules}\n"
                f"要求：生成纯文本口播备注，50-200字，直接输出备注正文，不要解释。\n"
            )
            try:
                notes = await self.stream_llm_collect(
                    prompt=prompt,
                    system_prompt="你是演讲备注撰写专家，直接输出口播备注正文。",
                )
                if notes and notes.strip():
                    await PptCommon.write_file(self, out_path, notes.strip())
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P11] 重跑备注分片仍失败 page=%d: %s", page_num, e)

    async def _inject_notes(self, pptx_path: str, pages_dir: str, pptx_root: str) -> bool:
        """单进程 cli notes inject 写回 .pptx。"""
        try:
            cmd = (
                f"{cli_path('notes', pptx_root)} inject "
                f"--pptx {quote_path(pptx_path)} "
                f"--notes-dir {quote_path(pages_dir)}"
            )
            result = await run_bash(self, cmd, timeout_seconds=60, required=False, workdir=pptx_root)
            if result.exit_code != 0:
                logger.warning("[P11] notes inject 失败 exit=%d: %s",
                               result.exit_code, (result.stderr or "")[:300])
                return False
            logger.info("[P11] notes inject 成功")
            return True
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P11] notes inject 异常: %s", e)
            return False

    async def _execute_stream(self, inputs: dict[str, Any]):
        result = await self._execute(inputs)
        status_map = {"ok": "ok", "partial": "warning", "skipped": "ok"}
        yield {
            **result,
            "node": self.plan_name,
            "status": status_map.get(result.get("speaker_notes_status", ""), "ok"),
            "message": result.get("speaker_notes_message", ""),
        }
