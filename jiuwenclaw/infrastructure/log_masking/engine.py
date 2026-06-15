# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""日志脱敏引擎：内置规则、按 priority DESC 顺序应用已编译规则。"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, ClassVar

_logger = logging.getLogger(__name__)

DEFAULT_REPLACEMENT = "******"
MAX_PATTERN_LENGTH = 512
MAX_REPLACEMENT_LENGTH = 64
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ALLOWED_SOURCES = frozenset({"builtin", "custom"})
_MAX_SANITIZE_TEXT_LEN = 256 * 1024
_LOG_MASKING_RULE_TABLE = "log_masking_rule"
_SENSITIVE_KW = (
    r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|credential|private[_-]?key|user[_-]?id|userid"
)


@dataclass(frozen=True)
class CompiledMaskingRule:
    rule_id: str
    pattern: re.Pattern[str]
    replacement: str
    name: str = ""
    priority: int = 0


_BUILTIN_RULES: list[CompiledMaskingRule] = [
    CompiledMaskingRule(
        "builtin_email",
        re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
        DEFAULT_REPLACEMENT,
        name="邮箱",
        priority=40,
    ),
    CompiledMaskingRule(
        "builtin_cn_mobile",
        re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
        DEFAULT_REPLACEMENT,
        name="手机号",
        priority=30,
    ),
    CompiledMaskingRule(
        "builtin_cn_id_card",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        DEFAULT_REPLACEMENT,
        name="身份证号",
        priority=20,
    ),
    CompiledMaskingRule(
        "builtin_kv_sensitive",
        re.compile(
            rf'(?i)(?P<prefix>["\']?[\w.-]{{0,128}}(?:{_SENSITIVE_KW})[\w.-]{{0,128}}["\']?\s*[:=]\s*)'
            r'(?P<val>"[^"]*"|\'[^\']*\'|[^,\s"\'\}\]]+)'
        ),
        rf"\g<prefix>{DEFAULT_REPLACEMENT}",
        name="敏感KV",
        priority=10,
    ),
]

_KV_SENSITIVE_PATTERN = next(
    rule.pattern for rule in _BUILTIN_RULES if rule.rule_id == "builtin_kv_sensitive"
)


def normalize_rule_id(rule_id: str) -> str:
    normalized = str(rule_id or "").strip()
    if not normalized or not _RULE_ID_RE.fullmatch(normalized):
        raise ValueError(
            "rule_id must be 1-64 chars of [A-Za-z0-9_.-]"
        )
    return normalized


def normalize_replacement(value: str | None) -> str:
    text = str(value or "").strip() or DEFAULT_REPLACEMENT
    if "\n" in text or "\r" in text:
        raise ValueError("replacement must not contain newlines")
    if len(text) > MAX_REPLACEMENT_LENGTH:
        raise ValueError(f"replacement must be at most {MAX_REPLACEMENT_LENGTH} chars")
    return text


def validate_pattern(pattern: str) -> str:
    text = str(pattern or "").strip()
    if not text:
        raise ValueError("pattern is required")
    if len(text) > MAX_PATTERN_LENGTH:
        raise ValueError(f"pattern must be at most {MAX_PATTERN_LENGTH} chars")
    try:
        re.compile(text)
    except re.error as exc:
        raise ValueError(f"invalid regex pattern: {exc}") from exc
    return text


def normalize_source(source: str) -> str:
    normalized = str(source or "custom").strip().lower()
    if normalized not in _ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {sorted(_ALLOWED_SOURCES)}")
    return normalized


class LogMaskingEngine:
    """进程内单例脱敏引擎（仅 ``get_instance`` 创建/持有单例）。"""

    _instance: ClassVar[LogMaskingEngine | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def compiled_default_rules() -> list[CompiledMaskingRule]:
        """返回内置规则列表（priority 越大越先执行）。"""
        return list(_BUILTIN_RULES)

    def __init__(self, rules: list[CompiledMaskingRule] | None = None) -> None:
        if rules is None:
            rules = self.compiled_default_rules()
        self._rules: list[CompiledMaskingRule] = list(rules)

    @classmethod
    def get_instance(cls) -> LogMaskingEngine:
        """返回单例；首次调用时使用内置默认规则初始化。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(cls.compiled_default_rules())
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """单测用：重置单例。"""
        with cls._lock:
            cls._instance = None

    @property
    def rules(self) -> list[CompiledMaskingRule]:
        return list(self._rules)

    def set_rules(self, rules: list[CompiledMaskingRule]) -> None:
        self._rules = list(rules)

    @classmethod
    def reload_from_rows(cls, rows: list[dict[str, Any]] | None) -> None:
        """从 DB/WS 行刷新单例规则；无行或全部编译失败时回退内置 defaults。"""
        if not rows:
            new_rules = list(cls.compiled_default_rules())
        else:
            compiled = cls.compile_masking_rows(rows)
            new_rules = list(compiled if compiled else cls.compiled_default_rules())

        cls.get_instance().set_rules(new_rules)

    @classmethod
    async def reload_log_masking_from_gateway_db(cls) -> None:
        """从 Gateway 库加载 enabled 规则并刷新**本进程**单例引擎。"""
        try:
            from jiuwenclaw.infrastructure.module_importer import (
                import_manager_ws_client_module,
            )

            db_mod = import_manager_ws_client_module("infrastructure.db")
            handler = await db_mod.ensure_db_handler(log_prefix="log_masking")
            rows = await cls.list_enabled_log_masking_rule_rows(handler)
            cls.reload_from_rows(rows)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[log_masking_db] log_masking_rule read failed: %s",
                exc,
                exc_info=True,
            )
            cls.reload_from_rows(None)

        from .probes import maybe_emit_log_masking_probe_samples

        maybe_emit_log_masking_probe_samples(_logger)

    @classmethod
    async def list_enabled_log_masking_rule_rows(
        cls,
        handler: Any,
        *,
        table_name: str = _LOG_MASKING_RULE_TABLE,
        jiuwenclaw_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回 ``enabled=true`` 的规则行（priority DESC）。"""
        jid = (jiuwenclaw_id or os.getenv("JIUWENCLAW_ID") or "").strip()
        if not jid:
            return []
        all_rows = await handler.list_records(table_name, {"jiuwenclaw_id": jid})
        result: list[dict[str, Any]] = []
        for row in all_rows:
            record = cls._masking_rule_row_to_dict(row)
            if record.get("enabled"):
                result.append(record)
        result.sort(
            key=lambda r: (-int(r.get("priority") or 0), int(r.get("id") or 0)),
        )
        return result

    @staticmethod
    def _masking_rule_row_to_dict(obj: Any) -> dict[str, Any]:
        created_at = getattr(obj, "created_at", None)
        updated_at = getattr(obj, "updated_at", None)
        return {
            "id": getattr(obj, "id", None),
            "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id", None),
            "rule_id": getattr(obj, "rule_id", None),
            "rule_name": getattr(obj, "rule_name", None),
            "description": getattr(obj, "description", None),
            "pattern": getattr(obj, "pattern", None),
            "replacement": getattr(obj, "replacement", None),
            "priority": getattr(obj, "priority", 0),
            "source": getattr(obj, "source", None),
            "enabled": bool(getattr(obj, "enabled", True)),
            "data": getattr(obj, "data", None),
            "created_at": str(created_at) if created_at is not None else None,
            "updated_at": str(updated_at) if updated_at is not None else None,
        }

    @staticmethod
    def compile_masking_rows(
        rows: list[dict[str, Any]],
        *,
        order_desc: bool = True,
    ) -> list[CompiledMaskingRule]:
        """将 DB/WS 行编译为规则列表；编译失败的行跳过。"""
        sorted_rows = sorted(
            rows,
            key=lambda r: (
                -int(r.get("priority") or 0)
                if order_desc
                else int(r.get("priority") or 0),
                int(r.get("id") or 0),
            ),
        )
        compiled: list[CompiledMaskingRule] = []
        for row in sorted_rows:
            if not bool(row.get("enabled", True)):
                continue
            rule_id = str(row.get("rule_id") or "").strip()
            pattern_text = str(row.get("pattern") or "").strip()
            if not rule_id or not pattern_text:
                continue
            try:
                pattern = re.compile(validate_pattern(pattern_text))
                replacement = normalize_replacement(row.get("replacement"))
                compiled.append(
                    CompiledMaskingRule(
                        rule_id=rule_id,
                        pattern=pattern,
                        replacement=replacement,
                        name=str(row.get("rule_name") or "").strip(),
                        priority=int(row.get("priority") or 0),
                    )
                )
            except ValueError as exc:
                _logger.error(
                    "[LogMasking] skip rule %s: %s", rule_id or "?", exc
                )
            except re.error as exc:
                _logger.error(
                    "[LogMasking] skip rule %s compile failed: %s", rule_id, exc
                )
        return compiled

    def sanitize(self, text: str) -> str:
        if not text:
            return text
        if len(text) > _MAX_SANITIZE_TEXT_LEN:
            text = text[:_MAX_SANITIZE_TEXT_LEN]
        masked = text
        for rule in self._rules:
            try:
                masked = rule.pattern.sub(rule.replacement, masked)
            except Exception:
                _logger.debug(
                    "[LogMasking] rule %s sub failed", rule.rule_id, exc_info=True
                )
        return masked
