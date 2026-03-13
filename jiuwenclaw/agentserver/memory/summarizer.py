# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""LLM-based memory compression and summarization for JiuWenClaw.

"""

import os
import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig

from jiuwenclaw.utils import logger
from jiuwenclaw.config import get_config


def format_messages_for_summary(
        messages: List[Dict[str, Any]],
        include_timestamp: bool = False,
        max_content_length: int = 1000
) -> str:
    """Format messages into conversation string for summarization.

    Args:
        messages: List of message dicts
        include_timestamp: Whether to include timestamps
        max_content_length: Max length for content truncation

    Returns:
        Formatted conversation string
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[Tool: {block.get('name', 'unknown')}]")
                    elif block.get("type") == "tool_result":
                        text_parts.append(f"[Tool Result: {str(block.get('output', ''))[:200]}]")
                else:
                    text_parts.append(str(block))
            content = " ".join(text_parts)

        if include_timestamp:
            timestamp = msg.get("time_created", "") or msg.get("timestamp", "")
            time_prefix = f"[{timestamp}] " if timestamp else ""
        else:
            time_prefix = ""

        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."

        if role == "user":
            lines.append(f"{time_prefix}User: {content}")
        elif role == "assistant":
            lines.append(f"{time_prefix}Assistant: {content}")
        elif role == "system":
            lines.append(f"{time_prefix}System: {content}")
        elif role == "tool":
            lines.append(f"{time_prefix}Tool Result: {content[:500]}")

    return "\n".join(lines)


class LLMClient:
    """LLM client using openjiuwen framework."""

    def __init__(self):
        self._load_config()

    def _load_config(self):
        """Load LLM configuration from config.yaml."""
        config = get_config()

        react_config = config.get("react", {})
        model_client_config = react_config.get("model_client_config", {})

        self.api_key = model_client_config.get("api_key", "")
        self.base_url = model_client_config.get("api_base", "")
        self.model_name = react_config.get("model_name", "")
        self.model_provider = model_client_config.get("client_provider", "OpenAI")
        self.verify_ssl = model_client_config.get("verify_ssl", False)

        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url.rsplit("/chat/completions", 1)[0]

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.3) -> str:
        """Call LLM with messages using openJiuwen's Model.

        Args:
            messages: List of message dicts with role and content
            temperature: Temperature for generation

        Returns:
            Generated text
        """

        if not self.api_key:
            raise ValueError("LLM API key not configured")

        model_config = ModelRequestConfig(
            model=self.model_name,
            temperature=temperature
        )

        model_client_config = ModelClientConfig(
            client_id="1",
            client_provider=self.model_provider,
            api_key=self.api_key,
            api_base=self.base_url,
            verify_ssl=self.verify_ssl
        )

        llm = Model(model_config=model_config, model_client_config=model_client_config)

        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        response = await llm.invoke(formatted_messages)

        if hasattr(response, 'content'):
            return response.content
        elif isinstance(response, dict):
            return response.get('content', str(response))
        else:
            return str(response)


class ConversationCompactor:
    """Compacts conversation messages into summaries."""

    COMPACT_PROMPT = """你是一个对话压缩助手。请将以下对话历史压缩为简洁的摘要。

要求：
1. 保留关键信息和决策
2. 保留用户的重要偏好和习惯
3. 保留待办事项和提醒
4. 省略不必要的闲聊和重复内容
5. 使用简洁的语言

对话历史：
{conversation}

请输出压缩后的摘要（不要输出其他内容）："""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    async def compact(
            self,
            messages: List[Dict[str, Any]],
            prior_summary: str = ""
    ) -> str:
        """Compact messages into a summary.

        Args:
            messages: List of messages to compact
            prior_summary: Prior summary to build upon

        Returns:
            Compacted summary
        """
        if not messages:
            return prior_summary

        conversation = format_messages_for_summary(messages, include_timestamp=False)

        if prior_summary:
            conversation = f"[Prior Summary]\n{prior_summary}\n\n[New Conversation]\n{conversation}"

        full_prompt = self.COMPACT_PROMPT.format(conversation=conversation)

        try:
            summary = await self.llm.chat([
                {"role": "user", "content": full_prompt}
            ])

            return f"""<context-summary>
{summary.strip()}
</context-summary>
这是之前对话的摘要，请将其作为上下文以保持对话的连续性。"""

        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            return prior_summary


class SessionSummarizer:
    """Generates daily session summaries."""

    SUMMARY_PROMPT = """你是一个会话摘要助手。请为以下日期生成结构化的摘要。

日期：{date}

对话内容：
{conversation}

请按以下格式输出摘要：

## 今日重要事项
- [列出重要事件、决策、约定等]

## 用户偏好更新
- [列出新增表达的偏好或习惯]

## 待办事项
- [列出需要跟进的事项]

## 其他备注
- [其他值得记录的信息]

请输出摘要（不要输出其他内容）："""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    async def summarize(
            self,
            messages: List[Dict[str, Any]],
            date: Optional[str] = None
    ) -> str:
        """Generate a summary of messages for a specific date.

        Args:
            messages: List of messages to summarize
            date: Date string (YYYY-MM-DD), defaults to today

        Returns:
            Generated summary
        """
        if not messages:
            return ""

        date = date or datetime.datetime.now().strftime("%Y-%m-%d")
        conversation = format_messages_for_summary(messages, include_timestamp=True)

        full_prompt = self.SUMMARY_PROMPT.format(date=date, conversation=conversation)

        try:
            summary = await self.llm.chat([
                {"role": "user", "content": full_prompt}
            ])
            return summary.strip()
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"# Session Summary - {date}\n\nSummary generation failed, please check the conversation log manually."


async def compact_memory(
        messages: List[Dict[str, Any]],
        prior_summary: str = ""
) -> str:
    """Compact messages into a summary.

    Args:
        messages: List of messages to compact
        prior_summary: Prior summary to build upon

    Returns:
        Compacted summary
    """
    compactor = ConversationCompactor()
    return await compactor.compact(messages, prior_summary)


async def summarize_session(
        messages: List[Dict[str, Any]],
        date: Optional[str] = None
) -> str:
    """Generate a session summary.

    Args:
        messages: List of messages to summarize
        date: Date string (YYYY-MM-DD)

    Returns:
        Session summary
    """
    summarizer = SessionSummarizer()
    return await summarizer.summarize(messages, date)
