# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentOS 备份模型的输入侧上下文窗口字段单测。

覆盖两类行为：
1. 配置加载：models.agentos 块进入缓存（带 _source 标记、is_default=False、仅 model_name 非空时追加）。
2. 输入侧 max_tokens 别名（用户可读的上下文窗口上限）：
   - 不进 core 的 ModelRequestConfig（绝不作为输出 token 上限发往厂商）：
     build_model_from_entry 的 agentos 分支必须把它从 kwargs 里 pop 掉。
   - 只进 core 的 ContextEngineConfig.context_window_tokens（压缩阈值，不发厂商）：
     _deep_agent_context_engine_config 的 per-model 覆盖，仅当请求 model_name 匹配
     agentos 时生效；defaults 不受影响。

注意：不再有 max_output_tokens（输出侧用户自定义已移除）。输出 token 上限完全由
core 默认行为决定，agentos 不参与。
"""

import pytest

from jiuwenswarm.common.config import get_default_models
from jiuwenswarm.common.reasoning_injector import build_reasoning_model_request_kwargs
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    _deep_agent_context_engine_config,
    build_model_from_entry,
)


def _defaults_entry(name: str = "gpt-main") -> dict:
    return {
        "model_client_config": {
            "api_base": "http://x",
            "api_key": "sk-x",
            "model_name": name,
            "client_provider": "OpenAI",
            "timeout": 360,
            "verify_ssl": True,
            "custom_headers": {},
        },
        "model_config_obj": {"temperature": 0.95},
        "is_default": True,
    }


def _agentos_block(name: str = "agentos-pro", *, with_max_tokens: bool = True) -> dict:
    mco: dict = {"temperature": 0.95}
    if with_max_tokens:
        mco["max_tokens"] = 131072
    return {
        "model_client_config": {
            "api_base": "http://y",
            "api_key": "sk-y",
            "model_name": name,
            "client_provider": "OpenAI",
            "verify_ssl": True,
            "timeout": 1800,
        },
        "model_config_obj": mco,
    }


def _config(*, agentos_name: str | None = "agentos-pro", with_max_tokens: bool = True,
            react_cw: int | None = 65536) -> dict:
    cfg: dict = {"models": {"defaults": [_defaults_entry()]}}
    if agentos_name is not None:
        cfg["models"]["agentos"] = _agentos_block(agentos_name, with_max_tokens=with_max_tokens)
    react: dict = {}
    if react_cw is not None:
        react = {"context_engine_config": {"context_window_tokens": react_cw}}
    cfg["react"] = react
    return cfg


class TestAgentosConfigLoading:
    """get_default_models 对 agentos 块的加载行为。"""

    @staticmethod
    def test_agentos_appended_with_source_and_no_default():
        entries = get_default_models(_config())
        agentos = [e for e in entries if e.get("model_config_obj", {}).get("_source") == "agentos"]
        assert len(agentos) == 1
        assert agentos[0]["is_default"] is False
        mcc = agentos[0]["model_client_config"]
        assert mcc["model_name"] == "agentos-pro"
        mco = agentos[0]["model_config_obj"]
        assert mco["max_tokens"] == 131072

    @staticmethod
    def test_agentos_not_appended_when_model_name_empty():
        # model_name 为空 = 未配置，不应追加进缓存
        cfg = _config(agentos_name="")  # 空 model_name
        entries = get_default_models(cfg)
        assert all(
            e.get("model_config_obj", {}).get("_source") != "agentos" for e in entries
        )

    @staticmethod
    def test_agentos_absent_does_not_affect_defaults():
        entries = get_default_models(_config(agentos_name=None))
        assert len(entries) == 1
        assert entries[0]["model_client_config"]["model_name"] == "gpt-main"
        assert entries[0]["is_default"] is True

    @staticmethod
    def test_agentos_never_competes_for_default_flag():
        # agentos 始终 is_default=False，即便它与 defaults 同名也不抢默认（_infer_is_default
        # 的"组内唯一=默认"推断已被 get_default_models 显式置 False 覆盖）
        block = _agentos_block(name="gpt-main")  # 与 defaults 同名，制造组内多条目
        cfg = {"models": {"defaults": [_defaults_entry()], "agentos": block}, "react": {}}
        entries = get_default_models(cfg)
        agentos = [e for e in entries if e.get("model_config_obj", {}).get("_source") == "agentos"]
        assert len(agentos) == 1
        assert agentos[0]["is_default"] is False
        # defaults 仍应是默认
        defaults = [e for e in entries if e.get("model_config_obj", {}).get("_source") != "agentos"]
        assert defaults and defaults[0]["is_default"] is True


class TestAgentosMaxTokensNotInModelRequestConfig:
    """agentos 的 max_tokens 是输入侧别名，绝不进 core 的 ModelRequestConfig（否则会被
    误当成输出 token 上限发往厂商）。build_model_from_entry 必须把它 pop 掉。"""

    @staticmethod
    def test_agentos_max_tokens_not_set_on_model_request_config():
        entries = get_default_models(_config())
        agentos = next(e for e in entries
                       if e.get("model_config_obj", {}).get("_source") == "agentos")
        model = build_model_from_entry(agentos["model_client_config"], agentos["model_config_obj"])
        # ModelRequestConfig.max_tokens（输出上限）必须保持 None：agentos 的 max_tokens
        # 是输入侧别名，已 pop，不应落到这个字段上。
        assert model.model_config.max_tokens is None

    @staticmethod
    def test_agentos_max_tokens_not_in_model_dump():
        entries = get_default_models(_config())
        agentos = next(e for e in entries
                       if e.get("model_config_obj", {}).get("_source") == "agentos")
        model = build_model_from_entry(agentos["model_client_config"], agentos["model_config_obj"])
        dump = model.model_config.model_dump()
        # max_tokens 是 ModelRequestConfig 的声明字段，dump 里必有此键；关键是它的值
        # 必须为 None（agentos 的输入侧 max_tokens 已被 pop，未落到输出侧字段）。
        assert dump.get("max_tokens") is None
        # _source 是 extra，不应残留
        assert "_source" not in dump

    @staticmethod
    def test_defaults_max_tokens_remains_none():
        entries = get_default_models(_config())
        defaults = next(e for e in entries
                        if e.get("model_config_obj", {}).get("_source") != "agentos")
        model = build_model_from_entry(defaults["model_client_config"], defaults["model_config_obj"])
        assert model.model_config.max_tokens is None

    @staticmethod
    def test_source_marker_stripped_by_reasoning_injector():
        # web 端 config.validate_model 路径不走 build_model_from_entry，而是直接调
        # build_reasoning_model_request_kwargs 组装 kwargs。_source 标记必须在此层被
        # 统一 pop，否则会作为 extra 进 ModelRequestConfig，再随 model_dump 流到 SDK 的
        # AsyncCompletions.create(**params) 触发 "unexpected keyword argument '_source'"。
        entries = get_default_models(_config())
        agentos = next(e for e in entries
                       if e.get("model_config_obj", {}).get("_source") == "agentos")
        kwargs = build_reasoning_model_request_kwargs(
            model_client_config={"client_provider": "OpenAI", "api_base": "http://y"},
            model_config_obj=agentos["model_config_obj"],
            model_name="agentos-pro",
        )
        assert "_source" not in kwargs
        # 构造 ModelRequestConfig 也不应残留 _source
        from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
        mrc = ModelRequestConfig(**kwargs)
        assert getattr(mrc, "_source", None) is None
        assert "_source" not in mrc.model_dump()


class TestContextWindowTokensOverride:
    """_deep_agent_context_engine_config 的 per-model context_window_tokens 覆盖（仅 agentos）。

    agentos 块的 max_tokens（输入侧别名）覆盖到 core 的 ContextEngineConfig.context_window_tokens。
    """

    @staticmethod
    def test_legacy_call_unchanged_without_full_config():
        cfg = _config(react_cw=65536)
        cec = _deep_agent_context_engine_config(cfg["react"])
        assert cec.context_window_tokens == 65536

    @staticmethod
    def test_agentos_match_overrides_context_window():
        cfg = _config(react_cw=65536)
        cec = _deep_agent_context_engine_config(
            cfg["react"], full_config=cfg, model_name="agentos-pro"
        )
        # agentos 的 max_tokens(131072) 覆盖了全局 react 基础值 65536
        assert cec.context_window_tokens == 131072

    @staticmethod
    def test_defaults_name_not_overridden():
        # 请求 defaults 的 model_name -> 不匹配 agentos -> 用 react 基础值
        cfg = _config(react_cw=65536)
        cec = _deep_agent_context_engine_config(
            cfg["react"], full_config=cfg, model_name="gpt-main"
        )
        assert cec.context_window_tokens == 65536

    @staticmethod
    def test_no_agentos_block_falls_back_to_react():
        cfg = _config(agentos_name=None, react_cw=65536)
        cec = _deep_agent_context_engine_config(
            cfg["react"], full_config=cfg, model_name="anything"
        )
        assert cec.context_window_tokens == 65536

    @staticmethod
    def test_agentos_without_max_tokens_not_overriding():
        # agentos 块没配 max_tokens -> 不覆盖，回退基础值
        cfg = _config(with_max_tokens=False, react_cw=65536)
        cec = _deep_agent_context_engine_config(
            cfg["react"], full_config=cfg, model_name="agentos-pro"
        )
        assert cec.context_window_tokens == 65536
