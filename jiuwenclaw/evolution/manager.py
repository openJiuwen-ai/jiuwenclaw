# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillEvolutionManager - Core Manager for skill evolution pipeline."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jiuwenclaw.evolution.schema import (
    EvolutionChange,
    EvolutionEntry,
    EvolutionFile,
    EvolutionSignal,
    VALID_SECTIONS,
)
from jiuwenclaw.evolution.signal_detector import SignalDetector
from jiuwenclaw.utils import logger

_EVOLUTION_FILENAME = "evolutions.json"

_GENERATE_PROMPT = """你是一个 Skill 优化专家。
根据以下 Skill 内容和对话信号，生成一条演进记录。

## 当前 Skill 内容（摘要）
{skill_content}

## 演进信号
{signals_json}
## 对话片段（作为上下文）
{conversation_snippet}

## 要求
1. 语言必须一致（强制）：输出语言必须与 Skill 完全一致。若 Skill 是中文，输出中文；若 Skill 是英文，输出英文。禁止自行决定语言！
2. 标题层级：使用与 Skill 相同的标题层级（##、### 等）
3. 记录格式（强制）：只生成 1 条记录！禁止 2 条或更多！输出 JSON 的 content 字段只能有 1 个标题 + 2-3 个分点！
4. 聚焦信号：优先选择与当前任务直接相关的信号，忽略无关噪音
5. 提取通用规则：生成可复用的规则，非临时补丁
   - 好："遇到 X 类型错误时，先检查 Y 是否正确再执行 Z"
   - 差："某用户某次提到某问题"
6. 专注单一类型：一次只生成一个 section 类型的改进（Instructions/Examples/Troubleshooting 之一），不混合多类型
7. 分点格式：只使用无序列表（- 或 *），禁止层级（不能有子分点）
8. 精炼语言：内容简洁，避免冗余描述
9. 高质量增量：生成的演进内容必须是 Skill 中未提及的新知识，能指导后续使用并提升 Agent 执行效率。
10. 相关性判断（强制）：判断当前发现的问题是否与 Skill 本身相关
    - 相关：问题由 Skill 的指令、脚本、示例或排查逻辑导致
    - 不相关：问题由外部因素导致（如网络、环境、权限、第三方服务等）

只输出以下 JSON，不要其他内容：
{{
  "section": "Instructions | Examples | Troubleshooting",
  "action": "append",
  "relevant": true | false,
  "content": "Markdown 内容，1 个标题 + 2-3 个分点，无层级（只生成 1 条记录，禁止重复内容）"
}}"""


def build_conversation_snippet(
    messages: List[dict],
    max_messages: int = 30,
    content_preview_chars: int = 300,
) -> str:
    """Build conversation snippet from messages for LLM context."""
    if not messages:
        return ""

    def extract_text(m: dict) -> str:
        content = m.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    lines: List[str] = []

    for msg in messages[-max_messages:]:
        role = msg.get("role", "unknown")
        text = extract_text(msg).strip() or "(无文本)"

        if len(text) > content_preview_chars:
            text = text[:content_preview_chars] + "..."

        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [tc.get("name", "") for tc in tool_calls if isinstance(tc, dict)]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "

        line = prefix + text
        lines.append(line)

    return "\n".join(lines)


class SkillEvolutionManager:
    """Manages evolution lifecycle for a Skill directory."""

    def __init__(self, llm: Any, skills_base_dir: str, model: str) -> None:
        self._llm = llm
        self._base = Path(skills_base_dir)
        self._model = model

    def scan(
        self,
        messages: List[dict],
        skill_dir_map: Optional[Dict[str, str]] = None,
    ) -> List[EvolutionSignal]:
        """Stage 1: Extract evolution signals from conversation (no LLM).

        Args:
            messages: Session message list.
            skill_dir_map: { skill_name: SKILL.md path }, for attribution.

        Returns:
            Deduplicated EvolutionSignal list (only the last one).
        """
        signals = SignalDetector(skill_dir_map=skill_dir_map).detect(messages)

        if len(signals) > 1:
            signals = signals[-1:]
            logger.info("[EvolutionManager] scan: keeping only last signal")

        signals_json = json.dumps(
            [s.to_dict() for s in signals],
            ensure_ascii=False,
            indent=2,
        )
        logger.info(
            "[EvolutionManager] scan: found %d signals, signals_json=%s",
            len(signals),
            signals_json,
        )
        return signals

    async def generate(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        skill_content: Optional[str] = None,
        conversation_snippet: Optional[str] = None,
    ) -> Optional[EvolutionEntry]:
        """Stage 2: Generate evolution entry via LLM.

        Args:
            skill_name: Skill name.
            signals: Signal list from scan().
            skill_content: Current SKILL.md content (optional, auto-read if not provided).
            conversation_snippet: Summary of recent conversation rounds.

        Returns:
            Generated EvolutionEntry, or None if LLM thinks no changes needed.
        """
        if not signals:
            return None

        if skill_content is None:
            skill_dir = self._base / skill_name
            skill_content = self._read_skill_content(skill_dir)

        signals_json = json.dumps(
            [s.to_dict() for s in signals],
            ensure_ascii=False,
            indent=2,
        )
        if conversation_snippet and conversation_snippet.strip():
            conversation_snippet = conversation_snippet.strip()
        else:
            conversation_snippet = ""

        prompt = _GENERATE_PROMPT.format(
            skill_content=skill_content[:2000],
            signals_json=signals_json,
            conversation_snippet=conversation_snippet,
        )

        logger.info("[EvolutionManager] generate: calling LLM (skill=%s)", skill_name)
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[EvolutionManager] generate: LLM call failed: %s", exc)
            return None

        change = self._parse_llm_response(raw)
        if change is None or not change.content.strip() or not change.relevant:
            logger.info("[EvolutionManager] generate: LLM thinks no changes needed")
            return None

        source = signals[0].type if signals else "unknown"
        context = "; ".join(s.excerpt for s in signals)
        entry = EvolutionEntry.make(source=source, context=context, change=change)
        logger.info(
            "[EvolutionManager] generate entry %s -> [%s]",
            entry.id,
            change.section,
        )
        return entry

    def append_entry(self, skill_name: str, entry: EvolutionEntry) -> None:
        """Stage 3: Append entry to evolutions.json."""
        skill_dir = self._base / skill_name
        evo_file = self._load_evolution_file(skill_dir)
        evo_file.entries.append(entry)
        evo_file.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._save_evolution_file(skill_dir, evo_file)
        logger.info(
            "[EvolutionManager] append_entry: wrote %s/evolutions.json (id=%s)",
            skill_name,
            entry.id,
        )

    def solidify(self, skill_name: str) -> int:
        """Stage 4: Solidify pending entries into SKILL.md.

        Returns:
            Number of entries solidified.
        """
        skill_dir = self._base / skill_name
        evo_file = self._load_evolution_file(skill_dir)
        pending = evo_file.pending_entries
        if not pending:
            logger.info("[EvolutionManager] solidify: no pending entries (skill=%s)", skill_name)
            return 0

        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            logger.warning("[EvolutionManager] solidify: SKILL.md not found (skill=%s)", skill_name)
            return 0

        content = skill_md_path.read_text(encoding="utf-8")
        for entry in pending:
            content = self._inject_section(content, entry.change)
            entry.applied = True

        skill_md_path.write_text(content, encoding="utf-8")
        evo_file.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._save_evolution_file(skill_dir, evo_file)
        logger.info(
            "[EvolutionManager] solidified %d entries (skill=%s)",
            len(pending),
            skill_name,
        )
        return len(pending)

    def load_skill_with_evolution(
        self, skill_name: str, base_content: str
    ) -> str:
        """Merge pending entries in memory, return enhanced content."""
        skill_dir = self._base / skill_name
        evo_file = self._load_evolution_file(skill_dir)
        pending = evo_file.pending_entries
        if not pending:
            return base_content

        content = base_content
        for entry in pending:
            content = self._inject_section(content, entry.change)
        return content

    def get_evolution_summary(self, skill_name: str) -> str:
        """Return pending evolution summary as Markdown."""
        skill_dir = self._base / skill_name
        evo_file = self._load_evolution_file(skill_dir)
        pending = evo_file.pending_entries
        if not pending:
            return ""

        lines = [f"\n\n### Skill '{skill_name}' 演进经验（自动注入，待固化）\n"]
        for entry in pending:
            lines.append(
                f"- **[{entry.change.section}]** {entry.change.content}"
            )
        return "\n".join(lines)

    def list_pending_summary(self, skill_names: List[str]) -> str:
        """Return pending summary for multiple Skills."""
        lines = []
        count = 0
        for name in skill_names:
            skill_dir = self._base / name
            evo_file = self._load_evolution_file(skill_dir)
            pending = evo_file.pending_entries
            if pending:
                count += 1
                lines.append(f"{count}. **{name}** - 共 {len(pending)} 条 pending 经验")
                for e in pending:
                    content = e.change.content
                    title = content.split('\n')[0] if '\n' in content else content[:50]
                    lines.append(f"   - **{title}**: ")
                    if '\n' in content:
                        body_lines = content.split('\n')[1:]
                        if body_lines:
                            summary = ' '.join(l.strip().lstrip('- ') for l in body_lines if l.strip())
                            lines.append(f"    {summary[:100].replace('**', '')}")
                lines.append("")
        
        if not lines:
            return "当前所有 Skill 暂无演进信息。"
            
        return "\n".join(lines)

    def _load_evolution_file(self, skill_dir: Path) -> EvolutionFile:
        evo_path = skill_dir / _EVOLUTION_FILENAME
        if evo_path.exists():
            try:
                data = json.loads(evo_path.read_text(encoding="utf-8"))
                return EvolutionFile.from_dict(data)
            except Exception as exc:
                logger.warning("[EvolutionManager] read evolutions.json failed: %s", exc)
        return EvolutionFile.empty(skill_id=skill_dir.name)

    def _save_evolution_file(self, skill_dir: Path, evo_file: EvolutionFile) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        evo_path = skill_dir / _EVOLUTION_FILENAME
        try:
            evo_path.write_text(
                json.dumps(evo_file.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("[EvolutionManager] write evolutions.json failed: %s", exc)

    @staticmethod
    def _find_skill_md(skill_dir: Path) -> Optional[Path]:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md
        md_files = list(skill_dir.glob("*.md"))
        if md_files:
            return md_files[0]
        return None

    @staticmethod
    def _read_skill_content(skill_dir: Path) -> str:
        md = SkillEvolutionManager._find_skill_md(skill_dir)
        if md is None:
            return "(SKILL.md not found)"
        try:
            return md.read_text(encoding="utf-8")
        except Exception:
            return "(failed to read SKILL.md)"

    @staticmethod
    def _inject_section(content: str, change: EvolutionChange) -> str:
        """Append change.content to the corresponding section in SKILL.md."""
        section = change.section
        addition = f"\n{change.content}\n"

        header_pattern = re.compile(
            rf"(## {re.escape(section)}.*?)(\n## |\Z)", re.DOTALL
        )
        m = header_pattern.search(content)
        if m:
            insert_pos = m.start(2)
            content = content[:insert_pos] + addition + content[insert_pos:]
        else:
            content = content.rstrip() + f"\n\n## {section}\n{change.content}\n"
        return content

    @staticmethod
    def _parse_llm_response(raw: str) -> Optional[EvolutionChange]:
        """从 LLM 返回里解析出 EvolutionChange。"""
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                logger.warning("[EvolutionManager] cannot parse LLM response as JSON: %s", raw[:200])
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                logger.warning("[EvolutionManager] JSON parse failed")
                return None

        section = data.get("section", "Troubleshooting")
        if section not in VALID_SECTIONS:
            section = "Troubleshooting"

        relevant = data.get("relevant", True)
        return EvolutionChange(
            section=section,
            action=data.get("action", "append"),
            content=data.get("content", ""),
            relevant=relevant,
        )
