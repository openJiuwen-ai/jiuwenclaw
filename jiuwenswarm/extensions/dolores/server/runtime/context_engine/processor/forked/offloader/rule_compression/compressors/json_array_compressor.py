from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any

from json_repair import loads as repair_json_loads

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.common import (
    ERROR_RE,
    fits_budget_and_saves,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


class JsonArrayCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        compressed, lossy = self._compress(content, ctx)
        return RuleCompressionResult(
            content=compressed,
            content_type=ContentType.JSON_ARRAY,
            modified=compressed != content,
            lossy=lossy,
        )

    def _compress(self, content: str, ctx: RuleContext) -> tuple[str, bool]:
        rows = _load_json_array(content)
        if rows is None:
            return content, False
        if not rows:
            return content, False
        if all(isinstance(row, dict) for row in rows):
            return self._compress_object_array(content, rows, ctx)
        if all(isinstance(row, str) for row in rows):
            return self._compress_string_array(content, rows, ctx)
        if all(self._is_number(row) for row in rows):
            return self._compress_number_array(content, rows, ctx)
        return self._compress_mixed_array(content, rows, ctx)

    def _compress_object_array(
        self,
        content: str,
        rows: list[dict[str, Any]],
        ctx: RuleContext,
    ) -> tuple[str, bool]:
        keys = sorted({key for row in rows for key in row.keys()})
        if not keys:
            return content, False
        if self._schema_density(rows, keys) >= ctx.json_csv_min_density:
            candidate = self._to_csv(rows, keys)
        else:
            candidate = self._to_jsonl(rows)
        if fits_budget_and_saves(content, candidate, ctx):
            return candidate, False

        selected = self._select_salient_rows(rows)
        candidate = self._format_lossy_array(
            selected,
            len(rows) - len(selected),
            summary=self._build_object_summary(rows),
        )
        if fits_budget_and_saves(content, candidate, ctx):
            return candidate, len(selected) < len(rows)
        return content, False

    def _compress_string_array(
        self,
        content: str,
        rows: list[str],
        ctx: RuleContext,
    ) -> tuple[str, bool]:
        compact = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if fits_budget_and_saves(content, compact, ctx):
            return compact, False
        if len(rows) <= 6:
            return content, False

        selected_indexes = {0, len(rows) - 1}
        selected_indexes.update(index for index, value in enumerate(rows) if ERROR_RE.search(value))
        selected_indexes.add(max(range(len(rows)), key=lambda index: len(rows[index])))
        selected = [rows[index] for index in sorted(selected_indexes)]
        candidate = self._format_lossy_array(selected, len(rows) - len(selected))
        if fits_budget_and_saves(content, candidate, ctx):
            return candidate, True
        return content, False

    def _compress_number_array(
        self,
        content: str,
        rows: list[int | float],
        ctx: RuleContext,
    ) -> tuple[str, bool]:
        compact = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if fits_budget_and_saves(content, compact, ctx):
            return compact, False
        if len(rows) <= 6:
            return content, False

        selected_indexes = {
            0,
            len(rows) - 1,
            min(range(len(rows)), key=rows.__getitem__),
            max(range(len(rows)), key=rows.__getitem__),
        }
        change_index = max(
            range(1, len(rows)),
            key=lambda index: abs(rows[index] - rows[index - 1]),
        )
        selected_indexes.update({change_index - 1, change_index})
        selected = [rows[index] for index in sorted(selected_indexes)]
        candidate = self._format_lossy_array(selected, len(rows) - len(selected))
        if fits_budget_and_saves(content, candidate, ctx):
            return candidate, True
        return content, False

    def _compress_mixed_array(
        self,
        content: str,
        rows: list[Any],
        ctx: RuleContext,
    ) -> tuple[str, bool]:
        compact = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if fits_budget_and_saves(content, compact, ctx):
            return compact, False
        if len(rows) <= 6:
            return content, False

        selected_indexes = {0, len(rows) - 1}
        seen_types: set[str] = set()
        for index, value in enumerate(rows):
            type_name = self._json_value_type(value)
            if type_name not in seen_types:
                seen_types.add(type_name)
                selected_indexes.add(index)
            if ERROR_RE.search(json.dumps(value, ensure_ascii=False)):
                selected_indexes.add(index)
        selected = [rows[index] for index in sorted(selected_indexes)]
        candidate = self._format_lossy_array(selected, len(rows) - len(selected))
        if fits_budget_and_saves(content, candidate, ctx):
            return candidate, True
        return content, False

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _json_value_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        return type(value).__name__

    @staticmethod
    def _format_lossy_array(
        selected: list[Any],
        omitted: int,
        *,
        summary: dict[str, Any] | None = None,
    ) -> str:
        output = list(selected)
        if omitted > 0:
            marker: dict[str, Any] = {"_omitted": omitted}
            if summary:
                marker["_summary"] = summary
            output.append(marker)
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _build_object_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        value_counts: dict[str, dict[str, int]] = {}
        minimum_coverage = max(int(len(rows) * 0.8), 1)
        keys = sorted({key for row in rows for key in row})
        for key in keys:
            values: list[Any] = []
            for row in rows:
                value = row.get(key)
                if key in row and (value is None or isinstance(value, (str, int, float, bool))):
                    values.append(value)
            if len(values) < minimum_coverage:
                continue
            counts = Counter(value if isinstance(value, str) else json.dumps(value) for value in values)
            if len(counts) <= 10:
                value_counts[key] = dict(counts)
        summary: dict[str, Any] = {"total_rows": len(rows)}
        if value_counts:
            summary["value_counts"] = value_counts
        return summary

    @staticmethod
    def _schema_density(rows: list[dict[str, Any]], keys: list[str]) -> float:
        if not rows or not keys:
            return 0.0
        return sum(len(row) for row in rows) / (len(rows) * len(keys))

    @classmethod
    def _to_csv(cls, rows: list[dict[str, Any]], keys: list[str]) -> str:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: cls._serialize_csv_value(row.get(key)) for key in keys})
        return f"[JSON_ARRAY compressed to CSV]\n{out.getvalue().strip()}"

    @staticmethod
    def _serialize_csv_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value

    @staticmethod
    def _to_jsonl(rows: list[dict[str, Any]]) -> str:
        lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
        return "[JSON_ARRAY compressed to JSONL]\n" + "\n".join(lines)

    @staticmethod
    def _select_salient_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(rows) <= 12:
            return rows
        scored = []
        for index, row in enumerate(rows):
            text = json.dumps(row, ensure_ascii=False).lower()
            score = 0
            if index < 5 or index >= len(rows) - 5:
                score += 10
            if ERROR_RE.search(text):
                score += 20
            scored.append((score, index, row))
        selected = sorted(sorted(scored, reverse=True)[:12], key=lambda item: item[1])
        return [row for _, _, row in selected]


def _load_json_array(content: str) -> list[Any] | None:
    try:
        rows = json.loads(content)
    except (TypeError, ValueError):
        stripped = content.lstrip()
        if not stripped.startswith("["):
            return None
        try:
            rows = repair_json_loads(content)
        except (TypeError, ValueError):
            return None
    return rows if isinstance(rows, list) else None
