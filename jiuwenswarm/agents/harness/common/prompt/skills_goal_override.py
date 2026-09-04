# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Monkey-patch openjiuwen's Skills and Goal prompt sections.

Mirrors ``safety_override.py``'s patch-at-import strategy so the change takes
effect for all three agent profiles (office / code / design) without editing any
openjiuwen source file.

What this patch does:

1. **Skills section** — replaces the dynamic ``# Skills`` header with a curated
   static catalogue (the xiaoyi work canonical skill list). Dynamic installed
   skills are rendered directly from their structured ``Skill`` objects, rather
   than reparsing their Markdown representation. Static-name collisions are
   de-duplicated. A bounded catalogue keeps whole descriptions first, then
   preserves later skill names in compact form for on-demand discovery.

2. **Goal section** — the static ``# Goal 模式工作规则`` / ``Goal 上下文规则``
   protocol block (``_GOAL_PROTOCOL``) is emptied, and
   ``TaskCompletionRail.before_model_call`` is wrapped so the
   ``GOAL_PROTOCOL`` section is removed from the builder right after openjiuwen
   injects it. The dynamic Goal pieces (``<goal_task>`` XML,
   ``submit_goal_report``, the transcript assessor) are untouched, so Goal mode
   still runs — it just no longer carries the static protocol guidance.

Imported (idempotently) from ``prompt_builder.py`` (office) and
``code_prompt_builder.py`` (code/design) right next to ``safety_override``.
"""

from __future__ import annotations

import os
from html import escape
from typing import Any, Dict, List, Sequence

from jiuwenswarm.common.utils import logger

# ---------------------------------------------------------------------------
# Static catalogue — the 15 canonical xiaoyi work skills
# ---------------------------------------------------------------------------

_STATIC_SKILL_NAMES = frozenset(
    {
        "xiaoyi-web-search",
        "find-skills",
        "xiaoyi-doc-convert",
        "xiaoyi-ppt",
        "aigc_marker",
        "execution-validator-skill",
        "secret-guardian",
        "skill-creator",
        "skill-scope",
        "swarmskill-creator",
        "xiaoyi-pdf",
        "seedream-image-gen",
        "seedance-video-gen",
        "music-generation",
        "xiaoyi-image-understanding",
    }
)

# Final system-prompt budget for the Skills section. This lives in the product
# override rather than agent-core so packaged/runtime dependency refreshes do
# not erase the policy. Set to 0 to disable the cap for troubleshooting.
_SKILLS_PROMPT_MAX_CHARS_ENV = "JIUWENSWARM_SKILLS_PROMPT_MAX_CHARS"
_DEFAULT_SKILLS_PROMPT_MAX_CHARS = 30_000

_STATIC_BLOCK_EN = """## Skills

The following skills provide specialized instructions for specific tasks. Use `skill_tool` to load a skill's full `SKILL.md` when the task matches its description; follow that file before executing the skill. The catalogue is metadata, not a replacement for the skill instructions.

<available_skills>
  <skill>
    <name>xiaoyi-web-search</name>
    <description>Default for real-time web retrieval and web-information queries. Use another search interface only when the user explicitly requests it.</description>
  </skill>
  <skill>
    <name>find-skills</name>
    <description>Default for skill discovery, retrieval, and installation unless the user explicitly requests another method.</description>
  </skill>
  <skill>
    <name>xiaoyi-doc-convert</name>
    <description>Convert among DOCX, PDF, XLSX, PPTX, Markdown, and other mainstream document formats. Use it before manual conversion scripts.</description>
  </skill>
  <skill>
    <name>xiaoyi-ppt</name>
    <description>Create, edit, generate, or beautify template-based presentations. Prefer it to manual python-pptx-style generation unless the user requests otherwise or it cannot meet the requirement.</description>
  </skill>
  <skill>
    <name>aigc_marker</name>
    <description>Add standard AIGC markers to generated documents, spreadsheets, presentations, Markdown, HTML, images, audio, video, and other supported files.</description>
  </skill>
  <skill>
    <name>execution-validator-skill</name>
    <description>Mandatory pre-safety validation for command execution, file access, and content transmission. It must not be bypassed or disabled.</description>
  </skill>
  <skill>
    <name>secret-guardian</name>
    <description>Privacy and secret protection for configurations, logs, prompts, reports, environment variables, and workspace content containing sensitive identifiers or keys.</description>
  </skill>
  <skill>
    <name>skill-creator</name>
    <description>Create, optimize, debug, and evaluate independent single-agent skills.</description>
  </skill>
  <skill>
    <name>skill-scope</name>
    <description>Mandatory security scan before every skill installation, from any source; no exceptions or bypasses.</description>
  </skill>
  <skill>
    <name>swarmskill-creator</name>
    <description>Create, convert, or refactor multi-agent team skills and orchestration workflows. Use skill-creator for ordinary single-skill work.</description>
  </skill>
  <skill>
    <name>xiaoyi-pdf</name>
    <description>Create, edit, lay out, convert, merge, split, extract, watermark, fill, protect, decrypt, or parse PDFs. Use it whenever the request involves PDF processing.</description>
  </skill>
  <skill>
    <name>seedream-image-gen</name>
    <description>Generate images. Deliver the image file, not only a prompt or script.</description>
  </skill>
  <skill>
    <name>seedance-video-gen</name>
    <description>Generate videos. Deliver the video file, not only storyboard Markdown.</description>
  </skill>
  <skill>
    <name>music-generation</name>
    <description>Generate music or audio and deliver the audio file.</description>
  </skill>
  <skill>
    <name>xiaoyi-image-understanding</name>
    <description>Analyze and understand images.</description>
  </skill>
</available_skills>
"""

_STATIC_BLOCK_CN = """## 技能

以下技能为特定任务提供专门指引。当任务符合某项技能描述时，使用 `skill_tool` 加载其完整 `SKILL.md`，并在执行前遵循该文件。此目录仅提供元数据，不能替代技能的完整说明。

<available_skills>
  <skill>
    <name>xiaoyi-web-search</name>
    <description>实时网页检索和网络信息查询的默认技能；仅当用户明确指定其他搜索接口时才切换。</description>
  </skill>
  <skill>
    <name>find-skills</name>
    <description>技能发现、检索和安装的默认技能；仅当用户明确要求其他方法时才例外。</description>
  </skill>
  <skill>
    <name>xiaoyi-doc-convert</name>
    <description>在 DOCX、PDF、XLSX、PPTX、Markdown 等主流文档格式间转换；应优先于手工转换脚本。</description>
  </skill>
  <skill>
    <name>xiaoyi-ppt</name>
    <description>创建、编辑、生成和美化基于模板的演示文稿；除非用户另有要求或技能无法满足需求，应优先于 python-pptx 等手工生成方式。</description>
  </skill>
  <skill>
    <name>aigc_marker</name>
    <description>为文档、表格、演示文稿、Markdown、HTML、图片、音频、视频及其他支持的生成文件添加标准 AIGC 标识。</description>
  </skill>
  <skill>
    <name>execution-validator-skill</name>
    <description>命令执行、文件访问和内容传输的强制前置安全校验；不得绕过或禁用。</description>
  </skill>
  <skill>
    <name>secret-guardian</name>
    <description>保护配置、日志、提示词、报告、环境变量及含密钥或敏感标识的工作区内容中的隐私和秘密。</description>
  </skill>
  <skill>
    <name>skill-creator</name>
    <description>创建、优化、调试和评估独立的单智能体技能。</description>
  </skill>
  <skill>
    <name>skill-scope</name>
    <description>所有来源的技能安装前必须进行安全扫描；不得例外或绕过。</description>
  </skill>
  <skill>
    <name>swarmskill-creator</name>
    <description>创建、转换和重构多智能体团队技能与编排工作流；普通单技能工作使用 skill-creator。</description>
  </skill>
  <skill>
    <name>xiaoyi-pdf</name>
    <description>创建、编辑、排版、转换、合并、拆分、提取、加水印、填写表单、加密、解密或解析 PDF；凡涉及 PDF 处理时使用。</description>
  </skill>
  <skill>
    <name>seedream-image-gen</name>
    <description>生成图像；应交付图像文件，而非仅交付提示词或脚本。</description>
  </skill>
  <skill>
    <name>seedance-video-gen</name>
    <description>生成视频；应交付视频文件，而非仅交付分镜 Markdown。</description>
  </skill>
  <skill>
    <name>music-generation</name>
    <description>生成音乐或音频并交付音频文件。</description>
  </skill>
  <skill>
    <name>xiaoyi-image-understanding</name>
    <description>分析和理解图像。</description>
  </skill>
</available_skills>
"""

_STATIC_BLOCK: Dict[str, str] = {"cn": _STATIC_BLOCK_CN, "en": _STATIC_BLOCK_EN}

_AVAILABLE_SKILLS_CLOSE_TAG = "</available_skills>"

def _skills_prompt_max_chars() -> int:
    """Return the configured final Skills-section character budget.

    Invalid and negative values retain the safe default. A value of ``0`` is
    an explicit opt-out for troubleshooting; this must not be the default
    because installed skill descriptions originate outside the static prompt.
    """
    raw = os.environ.get(_SKILLS_PROMPT_MAX_CHARS_ENV, "").strip()
    if not raw:
        return _DEFAULT_SKILLS_PROMPT_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; using default %d",
            _SKILLS_PROMPT_MAX_CHARS_ENV,
            raw,
            _DEFAULT_SKILLS_PROMPT_MAX_CHARS,
        )
        return _DEFAULT_SKILLS_PROMPT_MAX_CHARS
    if value < 0:
        logger.warning(
            "Negative %s=%r; using default %d",
            _SKILLS_PROMPT_MAX_CHARS_ENV,
            raw,
            _DEFAULT_SKILLS_PROMPT_MAX_CHARS,
        )
        return _DEFAULT_SKILLS_PROMPT_MAX_CHARS
    return value


def _visible_dynamic_skills(skills: Sequence[Any]) -> List[Any]:
    """Return de-duplicated non-static skills in their existing stable order."""
    visible: List[Any] = []
    seen_names = set(_STATIC_SKILL_NAMES)
    for skill in skills:
        name = str(getattr(skill, "name", "") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        visible.append(skill)
    return visible


def _dynamic_skill_entries_xml(skills: Sequence[Any], *, compact: bool = False) -> List[str]:
    """Render dynamic ``Skill`` objects directly, never reparsing Markdown.

    ``Skill.description`` can legitimately contain numbered examples. Rendering
    the objects at this boundary avoids mistaking those examples for skills.
    """
    rendered: List[str] = []
    for skill in skills:
        name = str(getattr(skill, "name", "") or "").strip()
        if not name:
            continue
        if compact:
            rendered.append(f"  <skill><name>{escape(name)}</name></skill>")
            continue
        description = str(getattr(skill, "description", "") or "").strip()
        rendered.append(
            "  <skill>\n"
            f"    <name>{escape(name)}</name>\n"
            f"    <description>{escape(description)}</description>\n"
            "  </skill>"
        )
    return rendered


def _append_until_budget(
    entries: Sequence[str],
    *,
    initial_length: int,
    max_chars: int,
) -> tuple[List[str], int]:
    """Append whole entries in stable order, returning entries and final size."""
    kept: List[str] = []
    current_length = initial_length
    for candidate in entries:
        separator_length = 1 if kept else 0
        if current_length + separator_length + len(candidate) > max_chars:
            break
        kept.append(candidate)
        current_length += separator_length + len(candidate)
    return kept, current_length


# ---------------------------------------------------------------------------
# Patched builders (same signatures as openjiuwen's originals)
# ---------------------------------------------------------------------------

def _build_all_mode_skill_prompt_from_skills(
    skills: Sequence[Any], language: str = "en"
) -> str:
    """Build a bounded XML catalogue from structured static and dynamic skills.

    Full descriptions are kept only as whole entries. Once the budget is hit,
    later dynamic skills downgrade to name-only entries, so the model can still
    discover and load them. A compact notice directs broad capability discovery
    to the already-installed ``find-skills`` skill.
    """
    lang = language or "en"
    static = _STATIC_BLOCK.get(lang, _STATIC_BLOCK_EN)
    if _AVAILABLE_SKILLS_CLOSE_TAG not in static:
        logger.warning("Static skills catalogue is missing its closing XML tag")
        return static
    prefix, _ = static.rsplit(_AVAILABLE_SKILLS_CLOSE_TAG, 1)
    prefix = prefix.rstrip() + "\n"
    suffix = _AVAILABLE_SKILLS_CLOSE_TAG + "\n"
    visible_skills = _visible_dynamic_skills(skills)
    full_entries = _dynamic_skill_entries_xml(visible_skills)
    if not full_entries:
        return prefix + suffix

    configured_max = _skills_prompt_max_chars()
    if configured_max == 0:
        return prefix + "\n".join(full_entries) + "\n" + suffix

    static_length = len(prefix) + len(suffix)
    max_chars = max(configured_max, static_length)
    if configured_max < static_length:
        logger.warning(
            "%s=%d is smaller than the required static catalogue (%d); using %d",
            _SKILLS_PROMPT_MAX_CHARS_ENV,
            configured_max,
            static_length,
            static_length,
        )

    full_kept, current_length = _append_until_budget(
        full_entries,
        initial_length=static_length,
        max_chars=max_chars,
    )
    if len(full_kept) == len(full_entries):
        return prefix + "\n".join(full_kept) + "\n" + suffix

    notice = (
        "  <catalog_notice>Some dynamic skill descriptions are omitted to stay within "
        "the prompt budget. Use `find-skills` when you need broader skill discovery."
        "</catalog_notice>"
    )
    # Keep the notice only when it does not displace a skill identity.
    notice_length = len(notice) + (1 if full_kept else 0)
    compact_entries = _dynamic_skill_entries_xml(
        visible_skills[len(full_kept) :], compact=True
    )
    compact_kept, _ = _append_until_budget(
        compact_entries,
        initial_length=current_length + notice_length,
        max_chars=max_chars,
    )
    parts = [*full_kept]
    if compact_kept or len(full_kept) < len(full_entries):
        if current_length + notice_length <= max_chars:
            parts.append(notice)
            current_length += notice_length
        compact_kept, _ = _append_until_budget(
            compact_entries,
            initial_length=current_length,
            max_chars=max_chars,
        )
        parts.extend(compact_kept)

    compact_omitted = len(compact_entries) - len(compact_kept)
    logger.warning(
        "Skills prompt reached %d-char budget; kept %d full, %d name-only, omitted %d "
        "dynamic skill(s)",
        max_chars,
        len(full_kept),
        len(compact_kept),
        compact_omitted,
    )
    return prefix + ("\n".join(parts) + "\n" if parts else "") + suffix


def _build_auto_list_mode_skill_prompt(language: str = "en") -> str:
    """Auto-list mode: just the static block (no dynamic skill_lines available)."""
    lang = language or "en"
    return _STATIC_BLOCK.get(lang, _STATIC_BLOCK_EN) + "\n"


# ---------------------------------------------------------------------------
# Goal protocol — empty the static block and remove the section post-injection
# ---------------------------------------------------------------------------

_EMPTY_PROTOCOL = {"cn": "", "en": ""}


def _apply_goal_patch() -> None:
    """Empty ``_GOAL_PROTOCOL`` and wrap ``TaskCompletionRail.before_model_call``.

    The wrapper lets openjiuwen run its original injection logic, then removes
    the ``GOAL_PROTOCOL`` section from the builder so neither the
    ``# Goal 模式工作规则`` heading nor the ``Goal 上下文规则`` sub-block
    appears in the final system prompt. The reminder variant
    (``build_goal_reminder_section``) is dead code in the current openjiuwen
    build (no caller wires it), so removing by section name is safe.
    """
    try:
        import openjiuwen.harness.prompts.sections.goal as _goal
        _goal._GOAL_PROTOCOL = dict(_EMPTY_PROTOCOL)
    except Exception:
        logger.debug("[skills_goal_override] patch goal._GOAL_PROTOCOL failed", exc_info=True)

    try:
        from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
        from openjiuwen.harness.prompts.sections import SectionName
    except Exception:
        logger.debug("[skills_goal_override] TaskCompletionRail import failed", exc_info=True)
        return

    if getattr(TaskCompletionRail.before_model_call, "__skills_goal_override_wrapped__", False):
        return

    _orig_before_model_call = TaskCompletionRail.before_model_call

    async def _patched_before_model_call(self, ctx) -> None:  # type: ignore[no-untyped-def]
        await _orig_before_model_call(self, ctx)
        builder = getattr(getattr(ctx, "agent", None), "system_prompt_builder", None)
        if builder is None:
            return
        try:
            builder.remove_section(SectionName.GOAL_PROTOCOL)
        except Exception:
            logger.debug(
                "[skills_goal_override] remove_section(GOAL_PROTOCOL) failed",
                exc_info=True,
            )

    _patched_before_model_call.__skills_goal_override_wrapped__ = True  # type: ignore[attr-defined]
    TaskCompletionRail.before_model_call = _patched_before_model_call  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Apply skills patch (structured SkillUseRail override + auto-list prompt)
# ---------------------------------------------------------------------------

_PATCHED = False


def apply_patch() -> None:
    """Patch openjiuwen's skills + goal prompt sections. Idempotent."""
    global _PATCHED
    if _PATCHED:
        return

    # 1. Auto-list has no dynamic entries, so a module-level builder patch is
    # sufficient. All-mode is patched below at the structured Skill boundary.
    try:
        import openjiuwen.harness.prompts.sections.skills as _skills
        _skills.build_auto_list_mode_skill_prompt = _build_auto_list_mode_skill_prompt
    except Exception:
        logger.warning("[skills_goal_override] patch skills module failed", exc_info=True)

    # 2. Preserve Skill objects until their XML rendering boundary. The upstream
    # rail first renders Markdown; parsing it again is ambiguous for multi-line
    # descriptions that contain numbered examples.
    try:
        import openjiuwen.harness.rails.skills.skill_use_rail as _sur
        from openjiuwen.harness.prompts.sections import SectionName
        from openjiuwen.harness.prompts.builder import PromptSection

        original_build = _sur.SkillUseRail._build_skills_section
        if not getattr(original_build, "__skills_goal_override_wrapped__", False):

            def _patched_build_skills_section(self, skills=None):  # type: ignore[no-untyped-def]
                if self.skill_mode != self.SKILL_MODE_ALL:
                    return original_build(self, skills)
                current_skills = self.skills if skills is None else skills
                builder = getattr(self, "system_prompt_builder", None)
                language = getattr(builder, "language", "en") or "en"
                return PromptSection(
                    name=SectionName.SKILLS,
                    content={
                        language: _build_all_mode_skill_prompt_from_skills(
                            current_skills, language
                        )
                    },
                    priority=40,
                )

            _patched_build_skills_section.__skills_goal_override_wrapped__ = True  # type: ignore[attr-defined]
            _sur.SkillUseRail._build_skills_section = _patched_build_skills_section
    except Exception:
        logger.warning("[skills_goal_override] patch SkillUseRail failed", exc_info=True)

    # 3. Goal section removal.
    _apply_goal_patch()
    _PATCHED = True


apply_patch()


__all__ = [
    "_STATIC_SKILL_NAMES",
    "_build_all_mode_skill_prompt_from_skills",
    "_build_auto_list_mode_skill_prompt",
    "apply_patch",
]
