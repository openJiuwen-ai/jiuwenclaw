"""请求级扩展字段透传。

将一个 ``dict[str, Any]`` 类型的扩展字段从 Channel 入口写入
``Message.metadata[METADATA_KEY]``，由 AgentServer 入口抬升到
请求级 ContextVar，供 Agent rail 通过 :func:`get_ext` 读取。

要透传哪些字段由环境变量 ``JIUWENCLAW_REQUEST_EXT_FORWARD_HEADERS``
（逗号分隔的字段名清单）声明。运行时可调 :func:`set_forward_headers`
覆盖（留给管控面 / DB 监听器使用）。载体内的键名与值含义由集成方约定，
本模块不预设字段语义。
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Any, Mapping

ENV_FORWARD_HEADERS = "JIUWENCLAW_REQUEST_EXT_FORWARD_HEADERS"
METADATA_KEY = "ext"

_request_ext: ContextVar["dict[str, Any] | None"] = ContextVar(
    "jw_request_ext", default=None,
)

# 运行时覆盖。优先级：set_forward_headers > 环境变量 > 空（关闭）。
# 默认 None（未覆盖），管控面 / DB 监听器在拉到新值时调用 set_forward_headers。
_runtime_override: "list[str] | None" = None


def set_forward_headers(headers: "list[str] | None") -> None:
    """整体覆盖 forward_headers（管控面 / DB 监听器使用）。

    传 None 等价于撤销覆盖，回落到环境变量；传空 list 等价于关闭透传。
    本接口会替换运行时清单的全部内容，覆盖此前任何 register_forward_header
    的累积结果。
    """
    global _runtime_override
    if headers is None:
        _runtime_override = None
    else:
        _runtime_override = [str(x).strip() for x in headers if str(x).strip()]


def register_forward_header(name: str) -> None:
    """加法注册单个字段名（扩展加载时使用）。

    首次调用本接口（或 :func:`register_forward_headers`）时，会将环境变量
    中现有的字段名作为底，后续注册在底之上累加；去重，不覆盖已有。
    """
    global _runtime_override
    name = str(name).strip()
    if not name:
        return
    if _runtime_override is None:
        raw = os.environ.get(ENV_FORWARD_HEADERS, "")
        _runtime_override = [x.strip() for x in raw.split(",") if x.strip()] if raw else []
    if name not in _runtime_override:
        _runtime_override.append(name)


def register_forward_headers(names: "list[str]") -> None:
    """加法注册多个字段名（扩展加载时批量调用）。"""
    for n in names or []:
        register_forward_header(n)


def _read_forward_headers() -> "list[str]":
    if _runtime_override is not None:
        return list(_runtime_override)
    raw = os.environ.get(ENV_FORWARD_HEADERS, "")
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_ext_from_source(source: "Mapping[str, Any] | None") -> "dict[str, Any] | None":
    """从入站 mapping（HTTP header 或 WS query 字典）按配置抽取扩展字段。

    支持值为字符串或字符串列表（``parse_qs`` 输出）；列表取首元素。
    键名匹配大小写不敏感。

    返回值：抽到任意字段时返回 dict（键名按配置中的原样保留），否则 None。
    """
    names = _read_forward_headers()
    if not names or not source:
        return None
    lower_to_actual = {k.lower(): k for k in source.keys() if isinstance(k, str)}
    out: dict[str, Any] = {}
    for name in names:
        actual = lower_to_actual.get(name.lower())
        if actual is None:
            continue
        val = source[actual]
        if isinstance(val, (list, tuple)):
            val = val[0] if val else None
        if val is None or val == "":
            continue
        out[name] = val
    return out or None


def set_current(ext: "dict[str, Any] | None") -> "Token":
    """Channel 入口使用：将 ext 写入 ContextVar，返回还原 token。"""
    return _request_ext.set(dict(ext) if ext else None)


def attach_to_metadata(
    metadata: "dict[str, Any] | None",
    ext: "dict[str, Any] | None" = None,
) -> "dict[str, Any] | None":
    """构造 Message 时使用：将 ext 写入 metadata。

    ``ext`` 缺省时读取当前 ContextVar 中的 ext。无 ext 时返回原 metadata。
    """
    if ext is None:
        ext = _request_ext.get()
    if not ext:
        return metadata
    out = dict(metadata or {})
    out[METADATA_KEY] = dict(ext)
    return out


def lift_from_metadata(metadata: "Mapping[str, Any] | None") -> "Token | None":
    """AgentServer 入口使用：从 request.metadata 抬升 ext 到 ContextVar。

    返回 ContextVar token；调用方在请求结束时应调 :func:`reset_ext`
    将 ContextVar 还原。无 ext 时返回 None。
    """
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(METADATA_KEY)
    if not isinstance(raw, dict) or not raw:
        return None
    return _request_ext.set(dict(raw))


def reset_ext(token: "Token | None") -> None:
    """与 :func:`set_current` / :func:`lift_from_metadata` 配对，还原 ContextVar。"""
    if token is not None:
        _request_ext.reset(token)


def get_ext() -> "dict[str, Any]":
    """Agent rail 使用：读取当前请求的扩展字段。

    返回值始终为 dict；无字段时返回空 dict，调用方无需 None 检查。
    """
    cur = _request_ext.get()
    return dict(cur) if cur else {}
