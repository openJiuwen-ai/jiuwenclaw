from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError, PlanNode
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.ppt_common import PptCommon
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    quote_path,
    run_bash,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_INTERMEDIATE_FILES = (
    "temp_image_map.json",
    "local_image_info.json",
    "page_image_info.json",
    "ai_plan.json",
)

_VQA_QUESTION = (
    "请描述这张图片的内容，包括场景、物体、人物、颜色等关键信息。"
)

_STEP0_SYSTEM = "你是 PPT 图片需求分析助手。根据大纲和研究内容，逐页分析图片需求，输出 JSON。"

_A2_SYSTEM = "你是图片实体提取助手。从图片描述中提取专有名词（地名、建筑名、品牌名、产品名等），输出 JSON。"

_A3_SYSTEM = "你是图片语义匹配助手。根据页面需求和图片描述，智能匹配图片，输出 JSON。"


def _build_step0_prompt(outline: str, research: str) -> str:
    return (
        "根据以下 PPT 大纲和研究内容，逐页分析图片需求。\n\n"
        "## 大纲\n"
        f"{outline}\n\n"
        "## 研究内容\n"
        f"{research or '（无研究内容）'}\n\n"
        "## 规则\n"
        "1. 封面/章节页 → needCount=1（背景图）\n"
        "2. 数据/图表页 → needCount=0（不需要图片）\n"
        "3. 案例/产品页 → 从要点中提取具体实体，实体数量=图片数量\n"
        "4. 关键词必须是专有名词，禁止泛词（如'美食''景点''技术'）\n"
        "5. 跳过不需要图片的页\n"
        "6. imageSize 必须大于最低分辨率：cover ≥ 1920×1080，content ≥ 800×600\n"
        "   格式为 \"宽*高\"，如 \"1920*1080\" 或 \"1024*1024\"\n\n"
        '输出 JSON：{"pages":[{"page":1,"type":"cover","title":"...","keywords":["实体1"],'
        '"needCount":1,"visualStrategy":"大图背景","imageSize":"1920*1080"}],"totalNeed":N}'
    )


def _build_a2_prompt(images: list[dict], topic: str = "") -> str:
    lines = []
    for i, img in enumerate(images):
        lines.append(f"图片{i + 1}：路径={img['path']}，描述={img['description']}")
    topic_hint = f"\n\n## 用户主题上下文\n{topic}" if topic else ""
    return (
        "从以下图片描述中提取专有名词作为实体。\n\n"
        "## 图片列表\n"
        + "\n".join(lines)
        + topic_hint
        + "\n\n"
        "实体类型：人名、地名、机构名、品牌名、产品名、事件名、作品名等。\n"
        "排除泛词（类别词、属性词、抽象词、动词）。\n"
        "如果图片描述中的人物/场景与主题上下文相关，请结合主题推断实体。\n"
        '输出 JSON：{"images":[{"path":"...","description":"...","entities":["实体1"]]}'
    )


def _build_a3_prompt(page_info: str, local_info: str) -> str:
    return (
        "根据页面需求和图片描述，智能匹配图片。\n\n"
        "## 页面需求\n"
        f"{page_info}\n\n"
        "## 本地图片\n"
        f"{local_info}\n\n"
        "## 规则\n"
        "1. 匹配分数 ≥ 70 才接受\n"
        "2. 每页不超过 needCount 张图片\n"
        "3. 不能重复使用同一张图片\n"
        "4. 优先满足高优先级页面\n"
        '输出 JSON：{"1":[{"originalPath":"...","description":"...","entities":[],'
        '"score":85,"matchedKeywords":["关键词"],"type":"local"}]}'
        "（key 是页码字符串，value 是图片数组；无匹配的页不要包含）"
    )


def _build_ai_prompt(keywords: list, topic: str, style_id: str, usage: str) -> str:
    kw = " ".join(keywords) if keywords else topic
    style_hint = f"，风格：{style_id}" if style_id else ""
    if usage == "cover":
        return f"{kw}，{topic}，background full-bleed 16:9 no text{style_hint}"
    return f"{kw}，{topic}，illustration conceptual no text{style_hint}"


def _extract_tool_text(result: Any, keys: tuple[str, ...]) -> str:
    """从 VQA/OCR 工具返回值中提取文本，兼容 dict/str/object 格式。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for k in keys:
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        data = result.get("data")
        if isinstance(data, dict):
            for k in keys:
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""
    if hasattr(result, "data"):
        data = result.data
        if isinstance(data, dict):
            for k in keys:
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if isinstance(data, str) and data.strip():
            return data.strip()
    return str(result).strip()


def _extract_vqa_answer(result: Any) -> str:
    return _extract_tool_text(result, ("answer", "text", "content"))


def _extract_ocr_text(result: Any) -> str:
    text = _extract_tool_text(result, ("ocr_text", "text", "content"))
    if text and text.lower() == "no text found.":
        return ""
    return text


def _parse_image_paths(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and len(line) > 2:
            paths.append(line[2:].strip())
    if not paths:
        logger.warning("[P6.5] _parse_image_paths 未解析到图片路径，raw=%s", text[:200])
    return paths


class ImagePrepareNode(PlanNode):
    """P6.5 图片准备节点（Diana）：按 image_sources 级联分配图片，产出 image_map.json。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p6_5_image_prepare",
            instruction=(
                "## P6.5 图片准备（Diana）\n"
                "\n"
                "### 职责\n"
                "按 `{image_sources}` 有序列表驱动的级联分配图片，产出 `image_map.json`。\n"
                "\n"
                "### 前置条件\n"
                "- `output_dir` / `pptx_root` / `outline_path` / `research_paths` 已由上游写入\n"
                "- `image_paths` / `image_sources` 由 P1/P2 写入\n"
                "\n"
                "### 输入\n"
                "- `image_paths`: 本地图片路径列表（local 源）\n"
                "- `image_sources`: 来源有序列表，默认 [\"local\"]\n"
                "- `outline_path` / `research_paths`: PPT 大纲与研究产物\n"
                "- `total_pages` / `topic` / `style_id`: 辅助参数\n"
                "\n"
                "### 输出\n"
                "```json\n"
                '{"image_map_path": "{output_dir}/image_map.json"}\n'
                "}\n"
                "失败/跳过时 `image_map_path` 为空字符串，下游 P8 走无图布局。\n"
                "\n"
                "### 门控\n"
                "- local 可用 ← image_paths 非空\n"
                "- network 恒不可用（禁用）\n"
                "- ai 可用 ← has_tool(text_to_image)\n"
                "- 全不可用 → 跳过\n"
                "\n"
                "### 失败兜底\n"
                "- 整流程最多重试 2 次，仍失败不阻塞 pipeline\n"
                "- 任何工具缺失自动降级（文件名降级 / 跳过 ai 源）\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = str(inputs.get("output_dir", "")).strip()
        pptx_root = str(inputs.get("pptx_root", "")).strip()
        outline_path = str(inputs.get("outline_path", "")).strip()
        research_paths = inputs.get("research_paths", {})
        image_paths = inputs.get("image_paths", [])
        image_sources = inputs.get("image_sources", ["local"])
        total_pages = inputs.get("total_pages") or inputs.get("page_count") or 0
        topic = str(inputs.get("topic", "")).strip()
        style_id = str(inputs.get("style_id", "")).strip()

        if not output_dir:
            logger.error("[P6.5] output_dir 为空，跳过图片准备")
            return {"image_map_path": ""}

        # 生图能力检测：读 P2 写的 imagegen_status.json
        ai_supported = await self._read_imagegen_status(self, output_dir)
        if ai_supported and "ai" not in image_sources:
            image_sources.append("ai")
            logger.info("[P6.5] imagegen_status.supported=true，已添加 ai 源")

        # 门控
        local_ok = bool(image_paths)
        ai_ok = "ai" in image_sources and ai_supported
        if not local_ok and not ai_ok:
            logger.info("[P6.5] 无可用图片来源（local=%s, ai=%s），跳过", local_ok, ai_ok)
            return {"image_map_path": ""}

        image_map_path = f"{output_dir}/image_map.json"
        for attempt in range(_MAX_RETRIES):
            try:
                ok = await self._step0_page_needs(output_dir, outline_path, research_paths)
                if not ok:
                    await self._cleanup(output_dir)
                    continue

                await self._step_a_local(output_dir, image_paths, topic)

                if ai_ok:
                    await self._ai_source(output_dir, pptx_root, image_sources, topic, style_id)

                ok = await self._step_d_finalize(output_dir, pptx_root, total_pages)
                if not ok:
                    await self._cleanup(output_dir)
                    continue

                if self._validate(output_dir):
                    logger.info("[P6.5] 图片准备成功: %s", image_map_path)
                    return {"image_map_path": image_map_path}

                logger.warning("[P6.5] image_map.json 校验失败 (attempt %d)", attempt + 1)
                await self._cleanup(output_dir)
            except Exception as e:
                if isinstance(e, AbortError):
                    raise
                logger.warning("[P6.5] 图片准备异常 (attempt %d): %s", attempt + 1, e)
                await self._cleanup(output_dir)

        logger.warning("[P6.5] 图片准备最终失败，降级为无图布局")
        return {"image_map_path": ""}

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield {"node": self.plan_name, "status": "progress", "message": "正在准备图片..."}
        result = await self._execute(inputs)
        path = result.get("image_map_path", "")
        yield {
            **result,
            "node": self.plan_name,
            "status": "ok",
            "message": f"图片准备完成：{path}" if path else "图片准备跳过（无可用来源）",
        }

    # ── Step 0: 页面需求分析 ──────────────────────────────

    async def _step0_page_needs(
        self, output_dir: str, outline_path: str, research_paths: Any,
    ) -> bool:
        outline = await PptCommon.read_file(
            self, outline_path, label="outline", max_chars=8000,
        )
        research = await self._collect_research(research_paths)

        if not outline and not research:
            logger.warning("[P6.5] outline 和 research 均为空")
            await self._write_json(
                f"{output_dir}/page_image_info.json",
                {"pages": [], "totalNeed": 0},
            )
            return True

        prompt = _build_step0_prompt(outline, research)
        try:
            resp = await self.stream_llm_collect(prompt=prompt, system_prompt=_STEP0_SYSTEM)
            data = PptCommon.parse_json_payload(resp)
            if not isinstance(data, dict) or "pages" not in data:
                logger.warning("[P6.5] Step 0 LLM 返回格式错误")
                return False
            await self._write_json(f"{output_dir}/page_image_info.json", data)
            return True
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] Step 0 LLM 失败: %s", e)
            return False

    async def _collect_research(self, research_paths: Any) -> str:
        if not research_paths:
            return ""
        parts: list[str] = []
        if isinstance(research_paths, dict):
            keys = sorted(research_paths.keys())
        else:
            keys = list(range(len(research_paths)))
        for k in keys:
            path = research_paths[k]
            text = await PptCommon.read_file(
                self, path, label=f"research-P{k}", max_chars=2000,
            )
            if text:
                parts.append(text)
        return ("\n\n---\n\n".join(parts))[:12000]

    # ── Step A: 本地图片处理 ──────────────────────────────

    async def _step_a_local(self, output_dir: str, image_paths: list[str], topic: str = "") -> None:
        if not image_paths:
            return

        images = await self._describe_images(image_paths)

        has_real_desc = any(
            img["description"] and img["description"] != Path(img["path"]).stem
            for img in images
        )
        if has_real_desc:
            images = await self._extract_entities(images, topic)

        local_info = {"images": images, "total": len(images)}
        await self._write_json(f"{output_dir}/local_image_info.json", local_info)

        page_info_raw = await PptCommon.read_file(
            self, f"{output_dir}/page_image_info.json", label="page_image_info",
        )
        if not page_info_raw:
            return

        await self._match_images(output_dir, page_info_raw, local_info)

    async def _describe_images(self, image_paths: list[str]) -> list[dict]:
        images: list[dict] = []
        has_vqa = self.has_tool("visual_question_answering")
        has_ocr = self.has_tool("image_ocr")

        for path in image_paths:
            desc = ""
            if has_vqa:
                try:
                    raw = await self.call_tool(
                        "visual_question_answering",
                        image_path_or_url=path,
                        question=_VQA_QUESTION,
                    )
                    desc = _extract_vqa_answer(raw)
                except Exception as e:
                    if isinstance(e, AbortError):
                        raise
                    logger.warning("[P6.5] VQA 失败 %s: %s", path, e)
            if not desc and has_ocr:
                try:
                    raw = await self.call_tool("image_ocr", image_path_or_url=path)
                    desc = _extract_ocr_text(raw)
                except Exception as e:
                    if isinstance(e, AbortError):
                        raise
                    logger.warning("[P6.5] OCR 失败 %s: %s", path, e)
            if not desc:
                desc = Path(path).stem
            images.append({"path": path, "description": desc, "entities": []})
        return images

    async def _extract_entities(self, images: list[dict], topic: str = "") -> list[dict]:
        prompt = _build_a2_prompt(images, topic)
        try:
            resp = await self.stream_llm_collect(prompt=prompt, system_prompt=_A2_SYSTEM)
            data = PptCommon.parse_json_payload(resp)
            if isinstance(data, dict) and isinstance(data.get("images"), list):
                result = data["images"]
                for i, img in enumerate(images):
                    if i < len(result) and isinstance(result[i], dict):
                        entities = result[i].get("entities", [])
                        if isinstance(entities, list):
                            img["entities"] = entities
                        desc = result[i].get("description")
                        if isinstance(desc, str) and desc.strip():
                            img["description"] = desc
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] 实体提取 LLM 失败: %s", e)
        return images

    async def _match_images(
        self, output_dir: str, page_info: str, local_info: dict,
    ) -> None:
        prompt = _build_a3_prompt(page_info, json.dumps(local_info, ensure_ascii=False))
        try:
            resp = await self.stream_llm_collect(prompt=prompt, system_prompt=_A3_SYSTEM)
            data = PptCommon.parse_json_payload(resp)
            if isinstance(data, dict):
                for key, imgs in data.items():
                    if key == "metadata" or not isinstance(imgs, list):
                        continue
                    for img in imgs:
                        if isinstance(img, dict):
                            img.setdefault("type", "local")
                            img.setdefault("score", 0.5)
                await self._write_json(f"{output_dir}/temp_image_map.json", data)
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] 本地匹配 LLM 失败: %s", e)

    # ── AI 源 ──────────────────────────────────────────────

    async def _ai_source(
        self, output_dir: str, pptx_root: str,
        image_sources: list, topic: str, style_id: str,
    ) -> None:
        # 最终确认：text_to_image 工具是否可用
        if not self.has_tool("text_to_image"):
            logger.warning("[P6.5] text_to_image 工具不可用，跳过 ai 源")
            return

        # prod: 改用 cli stage-ai-image 替代旧 ai-plan.js（含 SHA-256 精确复制）
        from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import cli_path as _cli_path
        sources_csv = ",".join(image_sources)
        cmd = (
            f"{_cli_path('stage-ai-image', pptx_root)} "
            f"--output-dir {quote_path(output_dir)} "
            f"--sources {sources_csv}"
        )
        try:
            result = await run_bash(self, cmd, workdir=pptx_root, required=False)
            logger.info("[P6.5] stage-ai-image: %s", (result.stdout or "")[:300])
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] stage-ai-image 失败，降级跳过 ai 源: %s", e)
            return

        ai_plan_raw = await PptCommon.read_file(
            self, f"{output_dir}/ai_plan.json", label="ai_plan",
        )
        if not ai_plan_raw:
            return
        ai_plan = PptCommon.parse_json_payload(ai_plan_raw)
        if not isinstance(ai_plan, list) or not ai_plan:
            return

        images_dir = Path(output_dir) / "images"
        # 循环外创建 images 目录（Windows mkdir 不支持 -p，循环内重复调用会失败）
        await run_bash(
            self,
            f"mkdir {quote_path(str(images_dir))}",
            required=False, workdir=output_dir,
        )
        temp_map = await self._read_temp_map(output_dir)

        # 从 page_image_info.json 读取 LLM 动态返回的图片分辨率
        page_size_map: dict[str, str] = {}
        page_info_raw = await PptCommon.read_file(
            self, f"{output_dir}/page_image_info.json", label="page_image_info",
        )
        if page_info_raw:
            page_info = PptCommon.parse_json_payload(page_info_raw)
            if isinstance(page_info, dict):
                for p in page_info.get("pages", []):
                    if isinstance(p, dict) and p.get("page"):
                        page_size_map[str(p["page"])] = p.get("imageSize", "")

        for item in ai_plan:
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            count = item.get("count", 0)
            keywords = item.get("keywords", [])
            usage = item.get("usage", "content")
            if not page or not count:
                continue
            # 优先用 LLM 返回的 imageSize，兜底按 usage 取默认值
            size = page_size_map.get(str(page), "")
            if not size:
                size = "1920*1080" if usage == "cover" else "1024*1024"
            for i in range(count):
                prompt = _build_ai_prompt(keywords, topic, style_id, usage)
                try:
                    raw = await self.call_tool(
                        "text_to_image", inputs={"prompt": prompt, "size": size, "n": 1},
                    )
                    paths = _parse_image_paths(str(raw))
                    if not paths:
                        continue
                    src = paths[0]
                    ext = Path(src).suffix or ".png"
                    dest = images_dir / f"page_{page}_ai_{i + 1}{ext}"
                    # prod: 用 stage-ai-image 的精确复制（含 SHA-256），但当前固化代码用 cp 兜底
                    cp_result = await run_bash(
                        self,
                        f"cp {quote_path(src)} {quote_path(str(dest))}",
                        required=False, workdir=output_dir,
                    )
                    if cp_result.exit_code != 0:
                        logger.warning(
                            "[P6.5] AI 生图复制失败 page=%s i=%d exit=%d: %s",
                            page, i + 1, cp_result.exit_code,
                            cp_result.stderr or cp_result.stdout,
                        )
                        continue
                    page_key = str(page)
                    if page_key not in temp_map:
                        temp_map[page_key] = []
                    temp_map[page_key].append({
                        "path": str(dest),
                        "type": "ai",
                        "description": " ".join(keywords),
                        "entities": keywords,
                        "matchedKeywords": keywords,
                        "score": 0.9,
                        "usage": usage,
                    })
                except Exception as e:
                    if isinstance(e, AbortError):
                        raise
                    logger.warning("[P6.5] AI 生图失败 page=%s i=%d: %s", page, i + 1, e)

        await self._write_json(f"{output_dir}/temp_image_map.json", temp_map)

    async def _read_temp_map(self, output_dir: str) -> dict:
        raw = await PptCommon.read_file(
            self, f"{output_dir}/temp_image_map.json", label="temp_image_map",
        )
        if not raw:
            return {}
        data = PptCommon.parse_json_payload(raw)
        if isinstance(data, dict):
            return data.get("pageImageMap", data)
        return {}

    # ── Step D: 汇总 ───────────────────────────────────────

    async def _step_d_finalize(
        self, output_dir: str, pptx_root: str, total_pages: int,
    ) -> bool:
        script = Path(pptx_root) / "image-insert" / "scripts" / "stepD-finalize.js"
        if not script.is_file():
            logger.warning("[P6.5] stepD-finalize.js 不存在: %s", script)
            return False

        cmd = f"node {quote_path(str(script))} {quote_path(output_dir)} {total_pages}"
        try:
            result = await run_bash(self, cmd, workdir=pptx_root, required=False)
            if result.exit_code != 0:
                logger.warning(
                    "[P6.5] stepD-finalize.js exit=%d: %s",
                    result.exit_code, result.stderr or result.stdout or "",
                )
                return False
            logger.info("[P6.5] stepD-finalize.js: %s", (result.stdout or "")[:300])
            return True
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] stepD-finalize.js 失败: %s", e)
            return False

    # ── 校验与清理 ─────────────────────────────────────────

    def _validate(self, output_dir: str) -> bool:
        path = Path(output_dir) / "image_map.json"
        return path.is_file()

    @staticmethod
    async def _read_imagegen_status(node: "ImagePrepareNode", output_dir: str) -> bool:
        """读 P2 写的 imagegen_status.json，返回 supported 字段。"""
        status_path = f"{output_dir}/imagegen_status.json"
        raw = await PptCommon.read_file(node, status_path, label="imagegen_status")
        if not raw:
            return False
        try:
            data = json.loads(raw)
            return bool(data.get("supported", False))
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] 解析 imagegen_status.json 失败: %s", e)
            return False

    async def _cleanup(self, output_dir: str) -> None:
        targets = [Path(output_dir) / f for f in _INTERMEDIATE_FILES]
        paths_str = " ".join(quote_path(str(t)) for t in targets if t.is_file())
        if not paths_str:
            return
        try:
            await run_bash(self, f"rm -f {paths_str}", required=False, workdir=output_dir)
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            pass

    async def _write_json(self, file_path: str, data: Any) -> None:
        try:
            await PptCommon.write_file(self, file_path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6.5] 写 %s 失败: %s", file_path, e)
