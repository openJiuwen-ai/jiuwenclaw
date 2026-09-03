# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Monkey-patch openjiuwen's Skills and Goal prompt sections.

Mirrors ``safety_override.py``'s patch-at-import strategy so the change takes
effect for all three agent profiles (office / code / design) without editing any
openjiuwen source file.

What this patch does:

1. **Skills section** — replaces the dynamic ``# Skills`` header with a curated
   static catalogue (the xiaoyi work canonical skill list). The
   ``SkillUseRail`` still appends dynamically discovered installed skills after
   the static block, but any installed skill whose name collides with one of the
   10 static entries is de-duplicated and the remainder is renumbered so the
   final list reads as a single continuous ``1..N`` catalogue with no gaps.

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
import re
from html import escape
from typing import Dict, List, Optional

from jiuwenswarm.common.utils import logger

# ---------------------------------------------------------------------------
# Static catalogue — the 10 canonical xiaoyi work skills
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

_DYNAMIC_START_INDEX = len(_STATIC_SKILL_NAMES) + 1  # static catalogue then dynamic entries

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

_STATIC_BLOCK_CN_LEGACY = """## 技能

优先使用以下技能与工具；使用技能前调用 `skill_tool` 获取该技能的完整 `SKILL.md`。

1. 网页搜索（`xiaoyi-web-search`）
   - 默认工具：小艺网页搜索技能（`xiaoyi-web-search`）
   - 使用规则：所有实时网页检索与网络信息查询任务默认使用此技能；仅当用户明确指定其他搜索接口时才切换。

2. 技能发现与安装（`find-skills`）
   - 默认工具：`find-skills` 技能
   - 使用规则：所有技能的发现、检索与安装任务默认必须通过此技能完成；仅当用户明确要求其他安装/发现方式时才例外。

3. 文档格式转换（`xiaoyi-doc-convert`）
   - 默认工具：`xiaoyi-doc-convert` 文档格式转换技能
   - 能力：支持 Docx、PDF、Xlsx、Pptx 与 Markdown 等主流文档格式之间的双向转换；专用专业文档转换工具。
   - 优先规则：所有文档格式转换请求必须优先使用此技能；禁止手工编写脚本生成或转换文档。

4. PPT — 模板创建（`xiaoyi-ppt`）
   - 默认工具：`xiaoyi-ppt` 技能
   - 适用场景：基于模板的 PPT 创建、编辑、生成与美化。
   - 优先规则：除非用户另有指定，基于模板的 PPT 任务优先使用此技能。
   - 禁止：禁止用 python-pptx 等手工编写脚本生成 PPT；仅当用户明确要求或本技能无法满足需求时才例外。

5. AIGC 内容标记（`aigc_marker`）
   - 用于为各类生成文件添加标准 AIGC 标记；支持文件类型包括 DOCX、PDF、Excel、PPT、Markdown、HTML、图片、音频、视频及所有主流格式文件。

6. 执行安全校验（`execution-validator-skill`）
   - 核心系统安全校验技能，对所有命令执行、文件访问与内容传输操作进行前置检查；拦截高风险操作、防止敏感数据泄露与非法执行；全局强制前置安全机制，不可绕过或禁用。

7. 隐私安全守护（`secret-guardian`）
   - 全局隐私保护技能，专用于处理配置文件、系统日志、提示词、报告、模型配置、通道配置、浏览器配置、环境变量及所有含隐私、密钥或敏感标识的工作区内容；可自动审计输出内容、拦截机密信息、脱敏敏感数据，并严格限制文件与网络访问权限，以最小化安全风险。

8. 单技能创建与优化（`skill-creator`）
   - 用于独立技能的全生命周期管理；支持从零创建新技能、编辑与优化已有技能、调试与评估技能性能、做方差基准测试、优化技能触发文案以提升技能调用准确率；仅适用于单智能体独立技能场景。

9. 技能安全审计（`skill-scope`）
   - 技能安装前强制安全扫描工具；对所有来源的技能安装行为做恶意检测；适用于所有安装场景：官方仓库安装、命令行安装、网络下载、find-skills 检索安装、手动导入技能目录、推荐技能安装等；所有技能安装必须先通过此工具的安全检查，无一例外、不可绕过。

10. 多角色团队技能编排（`swarmskill-creator`）
     - 专用的多智能体团队技能创建、转换与重构工具；支持编写团队工作流、编排脚本、构建多角色协同智能体架构、将单技能升级为团队协作技能；仅用于多角色团队场景；普通单技能创建应使用 `skill-creator`。

11. PDF 处理（`xiaoyi-pdf`）
     - PDF 综合处理技能，处理文档生成、编辑、安全与解析。 适用情形： 1. 创建与排版：从零生成报告、提案、简历等 PDF，或对现有文档重新排版美化； 2. 表单与水印：自动填写 PDF 表单字段，或添加文字/图片水印（如打水印、标机密）； 3. 页面管理：合并多个 PDF，或拆分、提取指定页码； 4. 安全控制：为 PDF 添加密码（加密）或移除密码（解密）； 5. 内容提取：从 PDF 中提取纯文本或导出表格数据。 只要用户诉求涉及生成、排版、美化、转换、拼接、拆分 PDF，或处理水印、表单、密码，必须触发本技能。

12. 图像生成（`seedream-image-gen`）
     - 使用规则：先 `skill_tool` 加载 `seedream-image-gen` 并严格按其 SKILL.md 填写。交付图像文件，不要只写 prompt 或脚本就停下。

13. 视频生成（`seedance-video-gen`）
     - 使用规则：先 `skill_tool` 加载 `seedance-video-gen` 并严格按其 SKILL.md 填写。交付视频文件，绝非分镜 markdown。

14. 音乐生成（`music-generation`）
     - 使用规则：先 `skill_tool` 加载 `music-generation` 并严格按其 SKILL.md 填写。交付音频文件。

15. 图像理解（`xiaoyi-image-understanding`）
     - 使用规则：先 `skill_tool` 加载 `xiaoyi-image-understanding` 并严格按其 SKILL.md 填写。
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

# ---------------------------------------------------------------------------
# De-duplication + renumbering for the dynamically rendered skill lines
# ---------------------------------------------------------------------------

# A rendered all-mode skill line looks like:
#   "{index}. `{skill_name}`{sep}{description}"  (+ optional "\n   Path: ..." continuation)
_MAIN_LINE_RE = re.compile(r"^\s*(\d+)\.\s+`([^`]+)`")
_LEADING_NUM_RE = re.compile(r"^\s*(\d+)(?=\.\s)")


def _parse_skill_entries(skill_lines: str) -> List[List[str]]:
    """Split a rendered skill_lines blob into per-entry line groups.

    Each entry is a list of lines: the first is the main numbered line, any
    following continuation lines (e.g. ``   Path: ...``) attach to it.
    """
    text = (skill_lines or "").strip()
    if not text:
        return []
    entries: List[List[str]] = []
    current: Optional[List[str]] = None
    for line in text.split("\n"):
        if _MAIN_LINE_RE.match(line):
            if current is not None:
                entries.append(current)
            current = [line]
        else:
            if current is not None:
                current.append(line)
            # orphan continuation lines (no preceding main line) are ignored
    if current is not None:
        entries.append(current)
    return entries


def _entry_name(entry: List[str]) -> str:
    """Extract the backticked skill name from an entry's main line."""
    m = _MAIN_LINE_RE.match(entry[0]) if entry else None
    return m.group(2) if m else ""


def _dedupe_and_renumber(skill_lines: str, start_index: int) -> str:
    """Drop entries whose name is in the static set; renumber the rest.

    The kept entries keep their original descriptions; only the leading
    ``N.`` is rewritten with a sequential counter starting at *start_index*
    so there are no gaps after dropping the static-named duplicates.
    """
    entries = _parse_skill_entries(skill_lines)
    out: List[str] = []
    idx = start_index
    for entry in entries:
        if _entry_name(entry) in _STATIC_SKILL_NAMES:
            continue
        main_line = entry[0]
        rest = entry[1:]
        main_line = _LEADING_NUM_RE.sub(lambda _: str(idx), main_line, count=1)
        out.append("\n".join([main_line, *rest]))
        idx += 1
    return "\n".join(out)


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


def _dynamic_skill_entries_xml(dynamic: str) -> List[str]:
    """Render inherited Markdown entries as XML without truncating descriptions."""
    rendered: List[str] = []
    for entry in _parse_skill_entries(dynamic):
        name = _entry_name(entry).strip()
        if not name:
            continue
        # Keep both the first-line description and any continuation lines
        # (for example a path) as one description value.
        main_description = re.sub(r"^\s*\d+\.\s+`[^`]+`\s*[:：]?\s*", "", entry[0]).strip()
        description = "\n".join([main_description, *entry[1:]]).strip()
        rendered.append(
            "  <skill>\n"
            f"    <name>{escape(name)}</name>\n"
            f"    <description>{escape(description)}</description>\n"
            "  </skill>"
        )
    return rendered


def _fit_skills_to_budget(
    dynamic_entries: List[str],
    *,
    prefix: str,
    suffix: str,
) -> List[str]:
    """Keep complete static/dynamic catalogue output within its total budget."""
    max_chars = _skills_prompt_max_chars()
    if max_chars == 0:
        return dynamic_entries
    kept: List[str] = []
    current_length = len(prefix) + len(suffix)
    for candidate in dynamic_entries:
        separator_length = 1 if kept else 0
        if current_length + separator_length + len(candidate) > max_chars:
            break
        kept.append(candidate)
        current_length += separator_length + len(candidate)
    if len(kept) < len(dynamic_entries):
        logger.warning(
            "Skills prompt reached %d-char budget; omitted %d dynamic skill(s)",
            max_chars,
            len(dynamic_entries) - len(kept),
        )
    return kept


# ---------------------------------------------------------------------------
# Patched builders (same signatures as openjiuwen's originals)
# ---------------------------------------------------------------------------

def _build_all_mode_skill_prompt(skill_lines: str, language: str = "en") -> str:
    """Build one bounded XML catalogue from static and dynamic skills."""
    lang = language or "en"
    static = _STATIC_BLOCK.get(lang, _STATIC_BLOCK_EN)
    dynamic = _dedupe_and_renumber(skill_lines or "", _DYNAMIC_START_INDEX).strip()
    if _AVAILABLE_SKILLS_CLOSE_TAG not in static:
        logger.warning("Static skills catalogue is missing its closing XML tag")
        return static
    prefix, _ = static.rsplit(_AVAILABLE_SKILLS_CLOSE_TAG, 1)
    prefix = prefix.rstrip() + "\n"
    suffix = _AVAILABLE_SKILLS_CLOSE_TAG + "\n"
    entries = _fit_skills_to_budget(
        _dynamic_skill_entries_xml(dynamic),
        prefix=prefix,
        suffix=suffix,
    )
    return prefix + ("\n".join(entries) + "\n" if entries else "") + suffix


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
# Apply skills patch (module attrs + defensive skill_use_rail namespace patch)
# ---------------------------------------------------------------------------

_PATCHED = False


def apply_patch() -> None:
    """Patch openjiuwen's skills + goal prompt sections. Idempotent."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    # 1. Patch the skills module's public builders.
    try:
        import openjiuwen.harness.prompts.sections.skills as _skills
        _skills.build_all_mode_skill_prompt = _build_all_mode_skill_prompt
        _skills.build_auto_list_mode_skill_prompt = _build_auto_list_mode_skill_prompt
        # Keep the no-skill fallback consistent: it now returns the static block.
        _skills.SKILL_RAIL_NO_SKILL_PROMPT = {
            "cn": _build_auto_list_mode_skill_prompt("cn"),
            "en": _build_auto_list_mode_skill_prompt("en"),
        }
    except Exception:
        logger.debug("[skills_goal_override] patch skills module failed", exc_info=True)

    # 2. Defensive: if SkillUseRail already captured the originals via a
    #    top-level ``from ... import``, rebind those names in its namespace too.
    try:
        import openjiuwen.harness.rails.skills.skill_use_rail as _sur
        _sur.build_all_mode_skill_prompt = _build_all_mode_skill_prompt
        _sur.build_auto_list_mode_skill_prompt = _build_auto_list_mode_skill_prompt
    except Exception:
        # Not imported yet — the skills-module patch above will be picked up by
        # SkillUseRail's own ``from ... import`` whenever it loads later.
        pass

    # 3. Goal section removal.
    _apply_goal_patch()


apply_patch()


__all__ = [
    "_STATIC_SKILL_NAMES",
    "_build_all_mode_skill_prompt",
    "_build_auto_list_mode_skill_prompt",
    "apply_patch",
]
