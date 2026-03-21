# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ToolMetadataProvider ABC and validation utilities.

Defines the standard interface that all deepagent built-in tools must
implement, ensuring complete bilingual descriptions and parameter schemas.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Tuple


# Lazy import to avoid circular dependency at module level.
def _supported_languages() -> Tuple[str, ...]:
    from openjiuwen.deepagents.prompts.builder import SUPPORTED_LANGUAGES
    return SUPPORTED_LANGUAGES


class ToolMetadataProvider(ABC):
    """工具元数据的标准接口。

    所有 deepagent 内置工具必须实现此接口，
    确保提供完整的双语描述和参数 schema。
    """

    @abstractmethod
    def get_name(self) -> str:
        """工具在 registry 中的唯一名称。"""

    @abstractmethod
    def get_description(self, language: str = "cn") -> str:
        """返回指定语言的工具描述。"""

    @abstractmethod
    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        """返回指定语言的 JSON Schema 参数定义。"""

    def validate(self) -> None:
        """校验双语元数据完整性，失败抛 ValueError。"""
        validate_provider(self)


def validate_provider(provider: ToolMetadataProvider) -> None:
    """校验 provider 的双语元数据完整性。

    检查项：
    - cn/en description 都非空
    - cn/en schema 都是合法 object schema（有 type, properties, required）
    - 每个参数都有 description
    - cn/en schema 结构一致（properties key 集合相同）
    - 递归检查嵌套 properties/items
    """
    name = provider.get_name()

    languages = _supported_languages()
    for lang in languages:
        desc = provider.get_description(lang)
        if not desc or not desc.strip():
            raise ValueError(f"[{name}] {lang} description is empty")

    schemas = {
        lang: provider.get_input_params(lang)
        for lang in languages
    }
    ref_lang = languages[0]
    ref_schema = schemas[ref_lang]
    for lang in languages[1:]:
        ctx = _SchemaPairContext(
            name=name,
            ref_schema=ref_schema,
            other_schema=schemas[lang],
            ref_lang=ref_lang,
            other_lang=lang,
        )
        _validate_schema_pair(ctx)


@dataclass
class _SchemaPairContext:
    """校验两种语言 schema 一致性所需的上下文。"""

    name: str
    ref_schema: Dict[str, Any]
    other_schema: Dict[str, Any]
    ref_lang: str = "cn"
    other_lang: str = "en"
    path: str = ""


def _validate_schema_pair(ctx: _SchemaPairContext) -> None:
    """递归校验两种语言 schema 的结构一致性。"""
    prefix = f"[{ctx.name}]{ctx.path}"

    if ctx.ref_schema.get("type") != "object":
        raise ValueError(
            f"{prefix} {ctx.ref_lang} schema type != 'object'"
        )
    if ctx.other_schema.get("type") != "object":
        raise ValueError(
            f"{prefix} {ctx.other_lang} schema type != 'object'"
        )
    if "properties" not in ctx.ref_schema:
        raise ValueError(
            f"{prefix} {ctx.ref_lang} schema missing 'properties'"
        )
    if "required" not in ctx.ref_schema:
        raise ValueError(
            f"{prefix} {ctx.ref_lang} schema missing 'required'"
        )

    ref_props = ctx.ref_schema.get("properties", {})
    other_props = ctx.other_schema.get("properties", {})

    if set(ref_props.keys()) != set(other_props.keys()):
        raise ValueError(
            f"{prefix} property keys differ: "
            f"{ctx.ref_lang}={sorted(ref_props.keys())}, "
            f"{ctx.other_lang}={sorted(other_props.keys())}"
        )

    for key in ref_props:
        for lang, props in (
            (ctx.ref_lang, ref_props),
            (ctx.other_lang, other_props),
        ):
            prop = props[key]
            if "description" not in prop:
                raise ValueError(
                    f"{prefix}.{key} {lang} missing description"
                )
            if prop.get("type") == "object" and "properties" in prop:
                other = (
                    other_props if lang == ctx.ref_lang
                    else ref_props
                )[key]
                _validate_schema_pair(_SchemaPairContext(
                    name=ctx.name,
                    ref_schema=prop,
                    other_schema=other,
                    ref_lang=ctx.ref_lang,
                    other_lang=ctx.other_lang,
                    path=f"{ctx.path}.{key}",
                ))
            if prop.get("type") == "array" and "items" in prop:
                items = prop["items"]
                other_items = (
                    other_props if lang == ctx.ref_lang
                    else ref_props
                )[key].get("items", {})
                if (
                    isinstance(items, dict)
                    and items.get("type") == "object"
                    and "properties" in items
                ):
                    _validate_schema_pair(_SchemaPairContext(
                        name=ctx.name,
                        ref_schema=items,
                        other_schema=other_items,
                        ref_lang=ctx.ref_lang,
                        other_lang=ctx.other_lang,
                        path=f"{ctx.path}.{key}[]",
                    ))
