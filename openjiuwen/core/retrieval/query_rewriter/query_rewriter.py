# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Query Rewriter: rewrites user query into a standalone query suitable for retrieval based on
conversation context, to improve retrieval effectiveness.

This module provides the QueryRewriter class, used with context_engine's get_messages/set_messages
interface. When history length reaches a threshold, dialogue is compressed via LLM and only a
bounded number of history messages are used for rewriting, so that the prompt does not grow
unbounded. Downstream retrieval can call retrieve(...) on the standalone_query returned by
rewrite() to obtain more relevant documents.
"""

import json
from pathlib import Path
from typing import Any, List, Optional, Union
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm import (
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
)
from openjiuwen.core.foundation.llm.schema.mode_info import ModelConfig
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser


def _fill_template(template: str, **kwargs: str) -> str:
    """
    Replace only explicit placeholders {key} in the template; other braces are not treated as
    placeholders. This allows JSON examples etc. in the prompt without triggering str.format()
    KeyError. Caller must pass str values for placeholders.
    """
    out = template
    for key, value in kwargs.items():
        out = out.replace("{" + key + "}", value)
    return out


def _extract_json(model_output: str) -> str:
    """
    Extract the JSON object between the first { and last } from LLM output, ignoring
    non-JSON text before and after.
    """
    start = model_output.find("{")
    end = model_output.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.debug("No JSON object found in model output")
        return ""
    logger.debug("JSON object from output extracted")
    return model_output[start:end + 1]


def _parse_llm_json(json_str: str) -> Optional[dict]:
    """
    Parse JSON string from LLM output. Tries json.loads first; on failure tries parsing
    after json_repair. Returns dict or None. Does not raise.
    """
    if not json_str or not json_str.strip():
        return None
    try:
        out = json.loads(json_str)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            repaired = repair_json(json_str)
            out = json.loads(repaired)
            logger.debug("JSON parsed after repair_json")
            return out if isinstance(out, dict) else None
        except Exception:
            return None


def _force_string(raw: Any) -> str:
    """Convert value to string; dict uses json.dumps, on failure uses str(raw)."""
    if isinstance(raw, dict):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except Exception:
            logger.debug("json.dumps failed when forcing value to string, using str(raw)")
            return str(raw)
    return str(raw)


def _force_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    return [raw]


def _force_json(key: str, raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            else:
                return {key: parsed}
        except Exception:
            return {key: raw}
    elif isinstance(raw, list):
        return {key: raw}
    else:
        return {key: raw}


def _schema_repair(output: dict, output_schema: dict) -> dict:
    """
    Validate and repair LLM output JSON against output_schema: coerce types when they do not
    match; raise when repair is not possible.

    Args:
        output: JSON object from LLM output.
        output_schema: Field type constraints {field_name: expected_type}, expected_type is
            str, list, or dict.

    Returns:
        Dict containing only schema fields with types matching the schema.

    Raises:
        BaseError: RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID when required field is missing
            or type cannot be repaired.
    """
    if not isinstance(output, dict):
        raise build_error(
            StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
            error_msg="output must be a dict",
        )

    repaired = {}

    for field, expected_type in output_schema.items():
        value = output.get(field)

        if value is None:
            if expected_type is str:
                value = ""
            elif expected_type is list:
                value = []
            elif expected_type is dict:
                value = {}
            else:
                raise build_error(
                    StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                    error_msg=f"Cannot auto-repair field '{field}' with type {expected_type}",
                )

        if field == "typo":
            if not isinstance(value, list):
                value = _force_list(value)
            repaired_typo = []
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    item = _force_json(field, item)

                repaired_item = {}
                for key in ["original", "corrected", "reason"]:
                    v = item.get(key)
                    if v is None:
                        v = ""
                    elif not isinstance(v, str):
                        v = _force_string(v)
                    repaired_item[key] = v
                repaired_typo.append(repaired_item)

            value = repaired_typo

        if not isinstance(value, expected_type):
            if expected_type is str:
                value = _force_string(value)
            elif expected_type is list:
                value = _force_list(value)
            elif expected_type is dict:
                value = _force_json(field, value)
            else:
                raise build_error(
                    StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                    error_msg=f"Field '{field}' expected type {expected_type.__name__}, got {type(value).__name__}",
                )
        repaired[field] = value
    logger.debug("Schema check and repair done")
    return repaired


class QueryRewriter:
    """
    Query Rewriter (QR): rewrites short user query into a standalone query suitable for
    retrieval based on conversation context, to improve retrieval effectiveness.

    Design goals:
        - Complete references and ellipsis in multi-turn dialogue into full, self-contained
          queries for vector/sparse retrieval matching.
        - When history message count reaches a threshold, compress long dialogue into one
          summary via LLM and replace the whole history with context_engine's
          set_messages(with_history=True), to control token size sent to rewrite.

    Core behavior:
        - rewrite(query): under the given session context, produces before (original query),
          standalone_query (rewritten for retrieval), intention, etc.; triggers history
          compression when needed and uses only the latest compress_range history messages
          in the rewrite prompt to avoid unbounded growth.
        - Aligned with context_engine: only uses get_messages(size=..., with_history=True),
          set_messages([...], with_history=True), no extra storage or sliding-window logic.

    Responsibility split with context:
        - QR does **not** write the current user query or rewrite result into context.
        - The **caller** should, each turn:
          1) When user sends a message, call rewrite(message) first to get standalone_query
             for retrieval;
          2) Then append UserMessage(message) to context (add_messages);
          3) After receiving agent reply, append AssistantMessage(reply) to context.
        - So one rewrite per turn; context updates (user -> assistant) are appended by the
          caller in the above order.
    """

    def __init__(
        self,
        cfg: ModelConfig,
        ctx: ModelContext,
        compress_range: int = 20,
        prompt_lang: str = "zh",
    ):
        """
        Initialize Query Rewriter: bind LLM config and current session context.

        Args:
            cfg: LLM config (model_provider + model_info) for compression and rewrite calls.
            ctx: Current session ModelContext for reading/writing history (get_messages,
                 set_messages), must match context_engine contract.
            compress_range: When history message count reaches this value, trigger one
                compression; also used as the upper bound of history messages sent to the
                rewrite prompt (get_messages(size=compress_range, ...)) to prevent
                unbounded prompt growth. Default 20.
            prompt_lang: Prompt template language, corresponding to _zh / _en suffix under
                prompts directory. Default "zh" (uses compression_zh.md, intention_completion_zh.md).
        """
        self.model_config: ModelConfig = cfg
        self.compress_range: int = max(compress_range, 1)
        self.context = ctx
        self.prompt_lang: str = prompt_lang or "zh"
        self._template_cache: dict[tuple[str, str], str] = {}
        mi = self.model_config.model_info
        ssl_cert = getattr(mi, "ssl_cert", None)
        verify_ssl = bool(getattr(mi, "verify_ssl", True))
        # BaseModelClient requires ssl_cert when verify_ssl is True; if no cert is configured, use verify_ssl=False
        if verify_ssl and ssl_cert is None:
            verify_ssl = False
        self.llm = Model(
            model_client_config=ModelClientConfig(
                client_provider=self.model_config.model_provider,
                api_key=mi.api_key,
                api_base=mi.api_base,
                timeout=float(getattr(mi, "timeout", 60)),
                verify_ssl=verify_ssl,
                ssl_cert=ssl_cert,
            ),
            model_config=ModelRequestConfig(
                model=self.model_config.model_info.model_name,
                temperature=0.0,
                top_p=float(getattr(self.model_config.model_info, "top_p", 0.1)),
            ),
        )
        self._json_output_parser = JsonOutputParser()
        self._compress_output_schema =\
            {
                "theme": list,
                "summary": str
            }
        self._rewrite_output_schema =\
            {
                "before": str,
                "intention": str,
                "standalone_query": str,
                "references": dict,
                "missing": list,
                "typo": list,
                "gibberish": list,
                "from_history": str
            }

    def load_template(self, prompt_base: str) -> str:
        """
        Load prompt template file with language suffix from prompts/ under this module's
        directory. File name format: {prompt_base}_{prompt_lang}.md (e.g. compression_zh.md),
        controlled by constructor argument prompt_lang.

        Args:
            prompt_base: Template base name (e.g. "compression", "intention_completion").

        Returns:
            Full text content of the template file (UTF-8).

        Raises:
            BaseError: RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND when the template file
                for the given language suffix does not exist.
        """
        cache_key = (prompt_base, self.prompt_lang)
        if cache_key in self._template_cache:
            return self._template_cache[cache_key]
        prompt_dir = Path(__file__).resolve().parent / "prompts"
        name = f"{prompt_base}_{self.prompt_lang}.md"
        prompt_path = prompt_dir / name
        if not prompt_path.exists():
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND,
                error_msg=f"prompt file not found: {prompt_path}",
            )
        try:
            content = prompt_path.read_text(encoding="utf-8")
        except OSError as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND,
                error_msg=f"prompt file read failed: {prompt_path}, reason: {e}",
                cause=e,
            ) from e
        self._template_cache[cache_key] = content
        return content

    def msg_2_text(self, messages: Optional[List[BaseMessage]] = None) -> str:
        """
        Format message list into plain text "role: content" for prompt or LLM input.

        Args:
            messages: Message list to format; if None, read full history from self.context.

        Returns:
            One "role: content" per line; content serialized as JSON string when list or dict.
        """
        if messages is None:
            messages = self.context.get_messages(with_history=True)
        lines: List[str] = []
        for m in messages:
            content = m.content
            if isinstance(content, (list, dict)):
                content = json.dumps(content, ensure_ascii=False)
            else:
                content = str(content) if content is not None else ""
            lines.append(f"{m.role}: {content}")
        return "\n".join(lines).strip()

    async def compress(self, raw: List[BaseMessage]) -> dict:
        """
        Use LLM to compress a segment of history messages into a structured summary
        (theme + summary text) for later replacing the whole history.

        Args:
            raw: Message list to compress (usually full context history or a segment).

        Returns:
            Dict with at least "theme" (list of themes) and "summary" (summary text),
            format defined by compression template; caller may serialize this dict as a
            SystemMessage and write to context (set_messages(..., with_history=True)).

        Raises:
            BaseError: RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID when LLM output cannot
                be parsed as valid JSON.
        """
        raw_text = self.msg_2_text(raw)
        compression_prompt = _fill_template(self.load_template("compression"), history=raw_text)
        try:
            compressed = await self.llm.invoke(
                model=self.model_config.model_info.model_name,
                messages=[SystemMessage(content=compression_prompt)],
                temperature=self.llm.model_config.temperature,
            )
        except Exception as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_LLM_INVOKE_FAILED,
                error_msg=str(e),
                cause=e,
            ) from e
        content_str = (compressed.content or "").strip()
        content_json = _extract_json(content_str)
        try:
            compressed_json = _parse_llm_json(content_json)
            if compressed_json is None:
                compressed_json = await self._json_output_parser.parse(content_json)
            if not isinstance(compressed_json, dict):
                raise build_error(
                    StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                    error_msg=(
                        f"LLM compress output is not valid JSON (parse returned non-dict); "
                        f"content: {content_json[:500]!r}"
                    ),
                )
            compressed_json_repaired = _schema_repair(compressed_json, self._compress_output_schema)
        except json.JSONDecodeError as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                error_msg=f"LLM compress output is not valid JSON: {e}; compressed: {content_json}",
                cause=e,
            ) from e
        except BaseError:
            raise
        except Exception as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                error_msg=f"LLM compress output parsing failed: {e}; content: {content_json[:500]!r}",
                cause=e,
            ) from e
        logger.info("Compress completed: %s", compressed_json_repaired)
        return compressed_json_repaired

    async def rewrite(self, query: str) -> dict:
        """
        Rewrite user query into a standalone query suitable for retrieval in the current
        session context, and trigger one compression when history reaches the threshold,
        to improve retrieval and control token size.

        Flow summary:
            1. If full history count >= compress_range, call compress() to get summary and
               replace whole history with set_messages([SystemMessage(summary)], with_history=True).
            2. Use only the latest compress_range history (get_messages(size=compress_range, ...))
               in the rewrite prompt to avoid unbounded growth.
            3. Call LLM to produce JSON with standalone_query, intention, etc., and return.

        Args:
            query: User's raw query for the current turn (may contain references or ellipsis).

        Returns:
            Dict with at least before (original query), standalone_query (rewritten for
            retrieval), intention, etc.; downstream retrieval can call retrieve(...) on
            standalone_query to get more relevant documents.

        Raises:
            BaseError: RETRIEVAL_QUERY_REWRITER_INPUT_INVALID when query is invalid;
                RETRIEVAL_QUERY_REWRITER_LLM_INVOKE_FAILED when LLM call fails;
                RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID when LLM output cannot be
                parsed as valid JSON.
        """
        if not isinstance(query, str) or not query.strip():
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_INPUT_INVALID,
                error_msg="query must be a non-empty string",
            )
        prompt_template = self.load_template("intention_completion")

        # Get full history to decide whether to trigger compression
        history_full = self.context.get_messages(with_history=True) or []

        if len(history_full) >= self.compress_range:
            try:
                compressed_json = await self.compress(history_full)
                history_text = json.dumps(compressed_json, ensure_ascii=False)
                self.context.set_messages(
                    [SystemMessage(content=history_text, name="compressed_history")],
                    with_history=True,
                )
            except BaseError as e:
                logger.warning(
                    "Query rewriter compress failed, falling back to original history: %s",
                    e,
                    exc_info=True,
                )
                history_text = self.msg_2_text(history_full)
                self.context.set_messages(
                    [SystemMessage(content=history_text, name="original_history")],
                    with_history=True,
                )

        # Use only the latest compress_range messages for rewrite prompt to avoid unbounded growth
        history_for_rewrite = self.context.get_messages(
            size=self.compress_range,
            with_history=True,
        )
        history_text = self.msg_2_text(history_for_rewrite)

        completion_prompt = _fill_template(
            prompt_template,
            history=history_text,
            query=query,
        )
        messages: List[BaseMessage] = [SystemMessage(content=completion_prompt)]

        try:
            rewrote = await self.llm.invoke(
                model=self.model_config.model_info.model_name,
                messages=messages,
                temperature=self.llm.model_config.temperature,
            )
        except Exception as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_LLM_INVOKE_FAILED,
                error_msg=str(e),
                cause=e,
            ) from e
        content_str = (rewrote.content or "").strip()
        content_json = _extract_json(content_str)
        try:
            rewrote_json = _parse_llm_json(content_json)
            if rewrote_json is None:
                rewrote_json = await self._json_output_parser.parse(content_json)
            if not isinstance(rewrote_json, dict):
                raise build_error(
                    StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                    error_msg=(
                        f"LLM rewrite output is not valid JSON (parse returned non-dict); "
                        f"content: {content_json[:500]!r}"
                    ),
                )
            rewrote_json_repaired = _schema_repair(rewrote_json, self._rewrite_output_schema)
        except json.JSONDecodeError as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                error_msg=f"LLM rewrite output is not valid JSON: {e}; content: {content_json}",
                cause=e,
            ) from e
        except BaseError:
            raise
        except Exception as e:
            raise build_error(
                StatusCode.RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID,
                error_msg=f"LLM rewrite output parsing failed: {e}; content: {content_json[:500]!r}",
                cause=e,
            ) from e

        return rewrote_json_repaired
