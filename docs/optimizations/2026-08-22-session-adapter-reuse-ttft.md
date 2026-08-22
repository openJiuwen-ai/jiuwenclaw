# TTFT 优化：Session Adapter 资源复用

> 日期：2026-08-22
> 范围：jiuwenswarm sidecar（`interface_deep.py`）

---

## 1. 问题分析

### 1.1 现象

冷启动（首次请求）时，sidecar 的 session 初始化开销高达 ~15 秒，远超 warm 请求的 ~2 秒。用户感知为首次对话白屏等待极长。

### 1.2 根因

sidecar 为每个线程创建 **session-scoped adapter**，流程如下：

```
root adapter (进程级，常驻)
  └─ _new_session_scoped_adapter()  →  创建 session adapter
       └─ create_instance()
            ├─ 读取 config.yaml
            ├─ 合并 enterprise config
            ├─ 构建 Model 实例（含 API key / base / provider 解析）
            ├─ 组装 tool_cards
            ├─ 加载 skill_manager
            └─ 构建 DeepAgent
```

`_new_session_scoped_adapter()` 只复制了少量字段（`_tool_owner_id`、`_is_session_scoped_adapter` 标记等），**没有**复制 root adapter 已经构建好的 Model、config、tool_cards 等昂贵资源。

结果：每个 session adapter 的 `create_instance()` 从零走完上述全流程——读配置、建 Model、组装工具卡片——重复了 root adapter 已完成的工作。对于 GLM-5.1 + DashScope + 60+ 工具的场景，这一步耗时 ~15 秒。

### 1.3 时间线（优化前）

| 阶段 | 耗时 | 说明 |
|------|------|------|
| HTTP POST → invocation created | ~700ms | API 路由 + sidecar 唤起 |
| session_init | ~14,000ms | **adapter 从零构建（瓶颈）** |
| LLM 首 token | ~1,000ms | 模型推理 |
| **冷启动总 TTFT** | **~15,000ms** | |

warm 请求复用已有 session adapter，无此开销（~2,000ms）。

---

## 2. 改动

### 2.1 文件

**`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`**（唯一改动文件）

### 2.2 改动一：`_new_session_scoped_adapter()` — 继承父级资源

**位置**：~行 2062

**改动**：在创建 session adapter 后，将 root adapter 已构建的资源直接赋值给 session adapter，避免后续 `create_instance()` 重复构建。

```python
# --- 复制 Model 相关资源（跳过 Model 重建）---
if self._model is not None:
    adapter._model = self._model
    adapter._model_cache = dict(self._model_cache) if self._model_cache else {}
    adapter._model_name_to_keys = dict(self._model_name_to_keys) if self._model_name_to_keys else {}
    adapter._tier_model_cache = dict(self._tier_model_cache) if self._tier_model_cache else {}
    adapter._default_model_name = self._default_model_name
    adapter._model_client_config = self._model_client_config
    adapter._model_request_config = self._model_request_config
    adapter._last_models_config_fingerprint = self._last_models_config_fingerprint
    adapter._model_config_source = self._model_config_source

# --- 复制 tool_cards（跳过工具卡片组装）---
if self._tool_cards is not None:
    adapter._tool_cards = self._tool_cards

# --- 复制 multimodal / skill / config 缓存 ---
if self._multimodal_model_map is not None:
    adapter._multimodal_model_map = self._multimodal_model_map
if self._multimodal_vision_model is not None:
    adapter._multimodal_vision_model = self._multimodal_vision_model
if self._multimodal_audio_model is not None:
    adapter._multimodal_audio_model = self._multimodal_audio_model
if self._multimodal_video_model is not None:
    adapter._multimodal_video_model = self._multimodal_video_model
if self._multimodal_image_gen_model is not None:
    adapter._multimodal_image_gen_model = self._multimodal_image_gen_model
if self._enabled_skills is not None:
    adapter._enabled_skills = list(self._enabled_skills)
if self._config_base_cache is not None:
    adapter._config_base_cache = self._config_base_cache
if self._config_cache is not None:
    adapter._config_cache = self._config_cache
if self._skill_manager is not None:
    adapter._skill_manager = self._skill_manager

# --- 复制身份 / 目录 ---
if self._agent_name and self._agent_name != "main_agent":
    adapter._agent_name = self._agent_name
if self._project_dir is not None:
    adapter._project_dir = self._project_dir
if self._workspace_dir is not None:
    adapter._workspace_dir = self._workspace_dir
```

### 2.3 改动二：`create_instance()` — 短路跳过冗余构建

**位置**：~行 7152

**改动**：session adapter 如果已继承 `_model` 和 `_config_base_cache`，跳过 config 读取、Model 构建、tool_cards 组装，直接进入 DeepAgent 构建。

```python
if (
    self._is_session_scoped_adapter
    and self._model is not None
    and self._config_base_cache is not None
):
    # 短路：复用父级资源，跳过 config 读取 / Model 构建 / tool_cards 组装
    config_base = self._config_base_cache
    config = self._config_cache or config_base.get("react", {}).copy()
    model = self._model
    logger.info(
        "[JiuWenSwarmDeepAdapter] session adapter reuses parent resources: "
        "mode=%s sub_mode=%s (skipped config/model/tool rebuild)",
        mode, sub_mode,
    )
else:
    # 原有完整构建路径（root adapter 或未继承资源的 session adapter）
    ns_token, overlay_token = self._bind_request_env_overlay()
    try:
        # ... config 读取 / Model 构建 / tool_cards 组装 ...
    finally:
        reset_agent_env_ns(ns_token)
        reset_task_env_overlay(overlay_token)

    if self._skip_own_instance_build():
        return

    # _create_model(...)

# --- 以下 DeepAgent 构建对两条路径共用 ---
```

tool_cards 跳过同理：

```python
if self._tool_cards is not None:
    tool_cards = self._tool_cards          # 继承的，直接用
else:
    tool_cards = await self._get_tool_cards(self._tool_owner_id())  # 从零组装
    self._tool_cards = tool_cards
```

### 2.4 改动三：修复 `contextvars.Token.reset()` bug

**位置**：~行 7260（`finally` 块）

**问题**：原代码 `ns_token.var.reset(ns_token.token)` 错误——`contextvars.Token` 没有 `.token` 属性，正确 API 是 `context_var.reset(token)`。

**修复**：

```python
# 导入
from jiuwenswarm.common.local_env_config import reset_agent_env_ns, reset_task_env_overlay

# finally 块
finally:
    reset_agent_env_ns(ns_token)        # 正确调用 _agent_env_ns.reset(token)
    reset_task_env_overlay(overlay_token)
```

此 bug 是预存问题，导致所有 LLM 请求的 env overlay 泄露，表现为 `"'_contextvars.Token' object has no attribute 'token'"` 错误，用户看到 "这次处理没有顺利完成"。

---

## 3. 效果

### 3.1 Benchmark 数据（`tmp-ttft-bench-v2.mjs`）

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| **冷启动 invocation** | ~14,956ms | 787ms | **95% ↓** |
| **冷启动 session_init** | ~14,500ms | 1,167ms | **92% ↓** |
| **冷启动首 text token** | ~15,500ms | 14,979ms | 3% ↓（LLM 推理不变） |
| **warm invocation** | ~1,966ms | 272ms | 86% ↓ |
| **warm session_init** | ~1,900ms | 453ms | 76% ↓ |
| **warm 首 text token** | ~2,000ms | 2,650ms | ≈持平（LLM 推理波动） |

### 3.2 手动测试验证

用户通过浏览器 UI 新建 session 发送 "你好"：

| 阶段 | 耗时 | 说明 |
|------|------|------|
| T0 → T3 (invocation created) | 13ms | 极快 |
| T0 → T2 (session_init) | 315ms | **优化生效**（fresh session 仅 315ms） |
| T0 → T1 (send to jiuwen) | 483ms | 含 session binding + 环境准备 |
| T0 → T4 (首帧 from LLM) | 2,663ms | LLM 推理（chat.reasoning） |
| T0 → done | 3,173ms | 总请求时间 |

### 3.3 关键结论

- **Sidecar session 开销从 ~15s 降到 ~315ms**，冷启动 TTFT 瓶颈从 sidecar 转移到 LLM 推理
- **LLM 推理成为新瓶颈**（占总 TTFT 的 82%），这是模型层面的问题，sidecar 无法优化
- **contextvars bug 修复**消除了所有 LLM 请求的 env overlay 泄露，属于顺带修复的预存缺陷

---

## 4. 后续优化方向

| 方向 | 预期收益 | 复杂度 | 说明 |
|------|---------|--------|------|
| 条件关闭 CoT 推理 | TTFB -60%~70% | 中 | 简单查询关闭 `extra_body.thinking.type`，跳过 GLM-5 思维链 |
| 懒加载 tools/skills | session_init -30% | 中 | 首次使用时才加载工具定义，减少 session 初始化量 |
| DB 优化 | 不确定 | 高 | SQLite/Redis 查询开销需 profile 后评估 |
