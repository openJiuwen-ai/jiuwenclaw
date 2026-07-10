"""LLM-based distillation of skill patterns from clustered traces."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .cluster import ClusteredQuery
from .models import DistilledPattern, TraceRecord

LOGGER = logging.getLogger(__name__)

_DISTILL_PATTERN_PROMPT = """\
You are an expert at generalizing query patterns. Given a cluster of semantically similar successful queries and the skills used to handle them, produce a generalized query template that captures their common intent.

Use bracketed placeholders like [Entity], [Attribute], [Player], etc. for variable parts. Keep the result close to the original queries — only replace specific entities/names/values that vary across the cluster. Do NOT over-generalize: preserve the original sentence structure and as much concrete wording as possible. For example, avoid patterns like "[Action] on my [Platform] feed [Details]" — keep it readable and natural.

For Chinese queries, use Chinese placeholders: [人名], [地点], [日期], [事物], etc., and keep the rest of the sentence natural.

---

CLUSTER INFO:
Cluster ID: {cluster_id}
Representative Query: {centroid_query}

SUCCESSFUL QUERIES:
{member_queries}

USED SKILLS IN THIS CLUSTER:
{used_skills}

---

Guidelines for generalization:
- For domain-specific skills (e.g., a weather skill for weather queries), keep domain-related entities concrete. Do NOT generalize them into placeholders like [City], [Date], etc. This preserves matching accuracy for future queries.
- Only replace variable entities that are truly unrelated to the skill's domain.
- The output should be a fluent, natural-sounding query template.

Produce a JSON object with a single key "pattern_description" whose value is the generalized query template.

Examples:
- Input queries: "Find Messi's goal record", "Find Tom Brady's career stats"
Used skills: web_search (search the web for information)
Output: {{"pattern_description": "Find [Player]'s career statistics/records"}}
- Input queries: "上海明天的天气", "北京下周的天气"
Used skills: weather_api (查询天气)
Output: {{"pattern_description": "[城市][时间]的天气"}}

Output ONLY valid JSON. No markdown, no explanation.
"""


class TraceDistiller:
    """Extract effective/ineffective skill patterns from clustered execution traces.

    Usage::

        distiller = TraceDistiller(
            llm_client=llm,
            llm_model="qwen3-32b",
            skills_info=[{"name": "web_search", "description": "..."}],
            max_workers=8,
        )
        patterns = distiller.run(clusters)
    """

    def __init__(
            self,
            llm_client: Any,
            llm_model: str,
            *,
            skills_info: list[dict[str, str]] | None = None,
            max_workers: int = 8,
            max_success_examples: int = 20,
    ) -> None:
        self._llm_model = llm_model
        if not llm_client or not llm_model:
            raise ValueError("TraceDistiller requires both llm_client and llm_model.")
        self._llm = llm_client
        self._max_workers = max_workers
        self._max_examples = max_success_examples

        # Build skill name → description map
        self._skills_by_name: dict[str, str] = {}
        if skills_info:
            for s in skills_info:
                self._skills_by_name[s["name"]] = s.get("description", "")

    def run(self, clusters: list[ClusteredQuery]) -> list[DistilledPattern]:
        """Run distillation in parallel across clusters."""
        results: list[DistilledPattern | None] = [None] * len(clusters)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_idx: dict[Any, int] = {}
            for i, cluster in enumerate(clusters):
                LOGGER.info("TraceDistiller: submitting cluster %d (id=%d)", i, cluster.cluster_id)
                future = executor.submit(self._distill_one, cluster)
                future_to_idx[future] = i

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                    LOGGER.info("TraceDistiller: finished cluster %d", idx)
                except Exception as exc:
                    LOGGER.error(
                        "TraceDistiller: failed cluster %d (id=%d): %s (type=%s, repr=%r)",
                        idx, clusters[idx].cluster_id, exc,
                        type(exc).__name__, exc,
                    )

        return [r for r in results if r is not None]

    def _distill_one(self, cluster: ClusteredQuery) -> DistilledPattern | None:
        metrics = self._compute_metrics(cluster.success_traces, cluster.failure_traces)

        success_queries = [t.query for t in cluster.success_traces]
        used_skill_names = {
            s for combo in metrics["effective_skills"] for s in combo
        }

        prompt = self._build_prompt(cluster, success_queries, used_skill_names)

        try:
            response = self._llm.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert at generalizing query patterns. "
                            "Output only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=256,
                stream=False,
                extra_body={"enable_thinking": False, "thinking": {"type": "disabled"}},
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError(
                    f"LLM returned empty content for cluster {cluster.cluster_id}"
                )
            pattern_description = self._parse_response(content, cluster.cluster_id)
        except Exception as exc:
            raise RuntimeError(
                f"LLM call failed for cluster {cluster.cluster_id}: {exc}"
            ) from exc

        if not pattern_description:
            return None

        return DistilledPattern(
            cluster_id=cluster.cluster_id,
            pattern_description=pattern_description,
            **metrics,
        )

    @staticmethod
    def _compute_metrics(
            success_traces: list[TraceRecord],
            failure_traces: list[TraceRecord],
    ) -> dict[str, Any]:
        effective_skills_map: dict[tuple[str, ...], int] = {}
        for t in success_traces:
            key = tuple(sorted(t.skills))
            effective_skills_map[key] = effective_skills_map.get(key, 0) + 1
        effective_skills = [
            list(k)
            for k, _ in sorted(effective_skills_map.items(), key=lambda x: -x[1])
        ]

        ineffective_skills: list[dict[str, str | list[str]]] = []
        seen: set[tuple[str, ...]] = set()
        for t in failure_traces:
            key = tuple(sorted(t.skills))
            if key not in seen:
                seen.add(key)
                ineffective_skills.append(
                    {
                        "skills": list(key),
                        "reason": t.error_type or "unknown",
                    }
                )

        total = len(success_traces) + len(failure_traces)
        success_rate = len(success_traces) / total if total > 0 else 0.0

        return {
            "effective_skills": effective_skills,
            "ineffective_skills": ineffective_skills,
            "success_rate": success_rate,
            "raw_trace_count": total,
        }

    def _build_prompt(
            self,
            cluster: ClusteredQuery,
            success_queries: list[str],
            used_skill_names: set[str] | None = None,
    ) -> str:
        queries_text = "\n".join(f"- {q!r}" for q in success_queries[: self._max_examples])
        if not queries_text:
            queries_text = "  (no successful traces)"

        skills_text = ""
        if self._skills_by_name and used_skill_names:
            lines = []
            for name in sorted(used_skill_names):
                desc = self._skills_by_name.get(name, "")
                if desc:
                    lines.append(f"- {name}: {desc}")
                else:
                    lines.append(f"- {name}")
            if lines:
                skills_text = "\n".join(lines)
        if not skills_text:
            skills_text = "  (no skill descriptions available)"

        return _DISTILL_PATTERN_PROMPT.format(
            cluster_id=cluster.cluster_id,
            centroid_query=cluster.centroid_query,
            member_queries=queries_text,
            used_skills=skills_text,
        )

    @staticmethod
    def _parse_response(response: str, cluster_id: int) -> str:
        raw = response.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOGGER.error(
                "TraceDistiller: failed to parse JSON for cluster %d", cluster_id
            )
            return ""

        return data.get("pattern_description", "")


__all__ = ["TraceDistiller"]