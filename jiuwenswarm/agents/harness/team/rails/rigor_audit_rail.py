# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rigor-audit rail: deterministic fabrication forensics on agent-written research text.

Ships the deterministic layer of SciRigorBench as an in-harness guardrail. Where
GovernanceReviewRail governs how scholarly objects are *produced*, this rail audits what was
actually written: numeric feasibility, cross-surface agreement, and construction residue.

Design constraints taken from the benchmark:

* Zero additional model calls. Every check is pure Python over the emitted text, so the rail
  costs nothing on the token budget and cannot itself hallucinate a finding.
* Report, never block. The benchmark's own result is that deterministic surface forensics
  saturate near zero on final-draft-quality agent papers: what they catch is a floor, not a
  certificate. Findings are logged for the writing loop; acceptance is not this rail's call.
* Every finding carries the fragment that triggered it, so a downstream reviewer can confirm
  or dismiss it without rerunning the agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)

# --- numeric feasibility -------------------------------------------------------------------
_MEAN_N_RE = re.compile(
    r"(?:mean|average|M)\s*[=:]?\s*(\d+\.\d+).{0,60}?\bn\s*[=:]\s*(\d{1,4})\b",
    re.IGNORECASE | re.DOTALL,
)
_PVALUE_RE = re.compile(r"\bp\s*[=<]\s*(0?\.\d+|\d\.\d+e-\d+|0\.0+)\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SD_ZERO_RE = re.compile(r"(?:SD|std|s\.d\.)\s*[=:]?\s*0\.0+\b", re.IGNORECASE)
_CI_RE = re.compile(r"\[\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\]")

# --- construction residue ------------------------------------------------------------------
_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|FIXME|TBD|XXX|placeholder|lorem ipsum|Author [A-E]\b|\[dream[^\]]*\])",
    re.IGNORECASE,
)
_INTERNAL_MARKER_RE = re.compile(
    r"\b(writing_mode\s*[:=]\s*dream|hypothetical_until_validated|target_result|idealized_claim|"
    r"claim boundary note|white-?box release)\b",
    re.IGNORECASE,
)
_ROUND_NUMBER_RE = re.compile(r"\b\d+\.(?:00|000|0000)\b")


@dataclass(frozen=True)
class RigorFinding:
    """One deterministic finding, bound to the fragment that produced it."""

    code: str
    message: str
    evidence: str

    def render(self) -> str:
        return f"{self.code}: {self.message} | evidence: {self.evidence[:120]}"


def _grim_infeasible(mean_str: str, n: int) -> bool:
    """GRIM: a mean of n integer responses must be a multiple of 1/n at its stated precision."""
    if not (0 < n <= 1000):
        return False
    decimals = len(mean_str.split(".")[1])
    if decimals == 0 or decimals > 4:
        return False
    mean = float(mean_str)
    scale = 10**decimals
    nearest = round(mean * n) / n
    return round(abs(nearest - mean) * scale) >= 1


def audit_text(text: str) -> list[RigorFinding]:
    """Run every deterministic check over one block of generated text.

    Pure function: no I/O, no model calls, no global state. Exposed at module level so the
    checks can be unit-tested and reused outside the harness.
    """
    findings: list[RigorFinding] = []

    for match in _MEAN_N_RE.finditer(text):
        mean_str, n_str = match.group(1), match.group(2)
        if _grim_infeasible(mean_str, int(n_str)):
            findings.append(RigorFinding(
                "D10_stat_feasibility",
                f"mean {mean_str} is not attainable from n={n_str} integer responses (GRIM)",
                match.group(0),
            ))

    for match in _PVALUE_RE.finditer(text):
        raw = match.group(1)
        if float(raw) == 0.0:
            findings.append(RigorFinding(
                "D11_pvalue_exact_zero",
                "p-value reported as exactly zero; report a bound such as p < 0.001 instead",
                match.group(0),
            ))

    percents = [float(p) for p in _PERCENT_RE.findall(text)]
    impossible = [p for p in percents if p > 100.0]
    if impossible:
        findings.append(RigorFinding(
            "D9_percent_out_of_range",
            f"percentage(s) above 100 reported: {impossible[:3]}",
            ", ".join(f"{p}%" for p in impossible[:3]),
        ))

    sd_zeros = _SD_ZERO_RE.findall(text)
    if len(sd_zeros) >= 2:
        findings.append(RigorFinding(
            "D2_missing_variance",
            f"{len(sd_zeros)} cells report exactly zero dispersion; collapsed variance is a "
            "construction signature unless the quantity is deterministic",
            sd_zeros[0],
        ))

    for match in _CI_RE.finditer(text):
        lo, hi = float(match.group(1)), float(match.group(2))
        if lo > hi:
            findings.append(RigorFinding(
                "D20_ci_inverted",
                f"confidence interval has lower bound {lo} above upper bound {hi}",
                match.group(0),
            ))
        elif lo == hi:
            findings.append(RigorFinding(
                "D27_ci_zero_width",
                "confidence interval collapses to a point at the printed precision",
                match.group(0),
            ))

    for match in _PLACEHOLDER_RE.finditer(text):
        findings.append(RigorFinding(
            "D34_submission_completeness",
            "placeholder text left in submission-facing content",
            match.group(0),
        ))

    for match in _INTERNAL_MARKER_RE.finditer(text):
        findings.append(RigorFinding(
            "D28_internal_marker_leak",
            "internal generation marker present on the public surface",
            match.group(0),
        ))

    round_numbers = _ROUND_NUMBER_RE.findall(text)
    if len(round_numbers) >= 4:
        findings.append(RigorFinding(
            "D1_result_number_grid",
            f"{len(round_numbers)} values sit on an exact rounding grid; check they were "
            "measured rather than filled in",
            ", ".join(round_numbers[:4]),
        ))

    return findings


class RigorAuditRail(DeepAgentRail):
    """Audit agent-written research text with the deterministic SciRigorBench detector layer."""

    SECTION_NAME = "rigor_audit"
    SECTION_PRIORITY = 44

    _PROMPT_CN = """\
# 严谨性自检规则（Rigor Self-Check）

在写出任何数值结果前，逐条自检：

1. **数值可行性**：均值必须能由样本量产生（如 n 个整数打分的均值必是 1/n 的倍数）；
   百分比不得超过 100；p 值不写成精确的 0，改写为 p < 0.001 之类的界。
2. **不确定度**：不要输出恒为零的标准差或宽度为零的置信区间；若量本身是确定性的，明说。
3. **跨表一致**：正文、表格、图注中同一个量必须一致；改动一处就同步另外两处。
4. **不留痕迹**：交付面不得出现 TODO/占位符/作者代号/内部生成标记。
5. **避免整齐**：不要把结果凑到整齐的小数网格上；报告实际计算出的位数。
"""

    _PROMPT_EN = """\
# Rigor Self-Check

Before writing any numeric result, verify each of the following:

1. **Numeric feasibility.** A mean must be attainable from the stated sample size (the mean of
   n integer responses is a multiple of 1/n); percentages must not exceed 100; never report a
   p-value as exactly zero -- report a bound such as p < 0.001.
2. **Uncertainty.** Do not emit standard deviations that are identically zero or intervals of
   zero width; if a quantity really is deterministic, say so explicitly.
3. **Cross-surface agreement.** The same quantity must match across prose, tables, and captions;
   changing one requires changing the others.
4. **No residue.** Submission-facing text must contain no TODO markers, placeholders, author
   codenames, or internal generation markers.
5. **No tidy grids.** Do not round results onto an even decimal grid; report the precision you
   actually computed.
"""

    def __init__(
        self,
        *,
        language: str = "cn",
        inject_prompt: bool = True,
        log_findings: bool = True,
    ) -> None:
        super().__init__()
        self._language = language
        self._inject_prompt = inject_prompt
        self._log_findings = log_findings
        self.system_prompt_builder = None
        self._agent_id: str = ""
        self._findings: list[RigorFinding] = []

    @property
    def findings(self) -> list[RigorFinding]:
        """Findings accumulated over this agent's lifetime, for downstream reporting."""
        return list(self._findings)

    def init(self, agent: "DeepAgent") -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._agent_id = str(
            getattr(agent.card, "id", None) or getattr(agent.card, "name", "unknown")
        )
        logger.info("[RigorAuditRail] Initialized for agent_id=%s", self._agent_id)

    def uninit(self, agent: "DeepAgent") -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None
        if self._findings:
            codes = sorted({f.code for f in self._findings})
            logger.info(
                "[RigorAuditRail] agent_id=%s emitted %d rigor finding(s) across %s",
                self._agent_id,
                len(self._findings),
                ", ".join(codes),
            )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        if not self._inject_prompt or self.system_prompt_builder is None:
            return
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={
                    self._language: (
                        self._PROMPT_CN if self._language == "cn" else self._PROMPT_EN
                    )
                },
                priority=self.SECTION_PRIORITY,
            )
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._log_findings:
            return

        response_text = ""
        if ctx and getattr(ctx, "response", None):
            response = ctx.response
            if isinstance(getattr(response, "content", None), str):
                response_text = response.content
            elif hasattr(response, "text"):
                response_text = str(response.text or "")
        if not response_text:
            return

        for finding in audit_text(response_text):
            self._findings.append(finding)
            logger.warning(
                "[RigorAuditRail] RIGOR FINDING agent_id=%s %s",
                self._agent_id,
                finding.render(),
            )


__all__ = ["RigorAuditRail", "RigorFinding", "audit_text"]
