# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import ToolMetadataProvider


def _schema_free_search(lang: str) -> Dict[str, Any]:
    desc = {
        "cn": "免费搜索查询词。",
        "en": "Free search query text.",
    }[lang]
    max_desc = {
        "cn": "返回结果条数上限（1-20）。",
        "en": "Maximum number of results (1-20).",
    }[lang]
    timeout_desc = {
        "cn": "请求超时时间（秒，5-60）。",
        "en": "Request timeout in seconds (5-60).",
    }[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": desc},
            "max_results": {"type": "integer", "description": max_desc, "default": 8},
            "timeout_seconds": {"type": "integer", "description": timeout_desc, "default": 20},
        },
        "required": ["query"],
    }


def _schema_paid_search(lang: str) -> Dict[str, Any]:
    qd = {"cn": "付费搜索查询词。", "en": "Paid search query text."}[lang]
    pd = {
        "cn": "搜索服务提供方：auto|perplexity|serper|jina。",
        "en": "Provider: auto|perplexity|serper|jina.",
    }[lang]
    md = {"cn": "返回 URL 数量上限（1-20）。", "en": "Maximum number of URLs (1-20)."}[lang]
    td = {"cn": "请求超时时间（秒，10-120）。", "en": "Request timeout in seconds (10-120)."}[lang]
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": qd},
            "provider": {"type": "string", "description": pd, "default": "auto"},
            "max_results": {"type": "integer", "description": md, "default": 8},
            "timeout_seconds": {"type": "integer", "description": td, "default": 45},
        },
        "required": ["query"],
    }


def _schema_fetch_webpage(lang: str) -> Dict[str, Any]:
    ud = {"cn": "要抓取的网页 URL。", "en": "Webpage URL to fetch."}[lang]
    cd = {
        "cn": "正文最大字符数（500-50000），超出会截断。",
        "en": "Maximum content characters (500-50000). Truncates when exceeded.",
    }[lang]
    td = {"cn": "请求超时时间（秒，5-120）。", "en": "Request timeout in seconds (5-120)."}[lang]
    return {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": ud},
            "max_chars": {"type": "integer", "description": cd, "default": 12000},
            "timeout_seconds": {"type": "integer", "description": td, "default": 30},
        },
        "required": ["url"],
    }


class FreeSearchMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "free_search"

    def get_description(self, language: str = "cn") -> str:
        # Keep semantics aligned with legacy description; provide CN translation.
        return {
            "cn": "免费搜索（DuckDuckGo 等）。输入 query，返回按相关性排序的 URL 列表与摘要。",
            "en": "Free search via DuckDuckGo. Input query and return ranked URLs with snippets.",
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_free_search(language)


class PaidSearchMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "paid_search"

    def get_description(self, language: str = "cn") -> str:
        return {
            "cn": "付费搜索（Perplexity/SERPER/JINA）。支持 provider=auto|perplexity|serper|jina。",
            "en": "Paid search via Perplexity/SERPER/JINA. Support provider=auto|perplexity|serper|jina.",
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_paid_search(language)


class FetchWebpageMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "fetch_webpage"

    def get_description(self, language: str = "cn") -> str:
        return {
            "cn": "抓取网页文本内容。返回 URL、状态码、标题与已清洗的正文文本。",
            "en": "Fetch webpage text content from URL. Returns status/title/plain text content.",
        }[language]

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _schema_fetch_webpage(language)
