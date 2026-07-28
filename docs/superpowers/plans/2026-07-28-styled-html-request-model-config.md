# Styled HTML Request Model Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DeepResearch styled HTML use the current request/tenant model configuration and immediately fall back offline when that configuration is invalid.

**Architecture:** Keep model credentials out of tool arguments. Resolve the report-style model through JiuwenClaw's overlay-aware `load_deepresearch_config()` path, validate it before constructing the SDK client, and use the overlay-aware TLS reader while preserving the existing offline conversion fallback and subprocess bridge.

**Tech Stack:** Python 3.12, pytest, ContextVar-backed JiuwenClaw configuration, openjiuwen-deepsearch report-style SDK.

---

### Task 1: Reproduce the request-overlay configuration bug

**Files:**
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Write the failing overlay precedence test**

Add imports for `bind_task_env_overlay` and `reset_task_env_overlay`, then add:

```python
def test_styled_export_llm_config_uses_request_overlay_instead_of_process_env(
    monkeypatch,
):
    monkeypatch.setenv("MODEL_NAME", "static-model")
    monkeypatch.setenv("MODEL_PROVIDER", "OpenAI")
    monkeypatch.setenv("API_BASE", "https://example.com/compatible-mode/v1")
    monkeypatch.setenv("API_KEY", "static-key")
    token = bind_task_env_overlay({
        "MODEL_NAME": "glm-5.2",
        "MODEL_PROVIDER": "OpenAI",
        "API_BASE": "https://client-claw.example/v2",
        "API_KEY": "request-key",
    })
    try:
        config = dt._build_styled_export_llm_config()
    finally:
        reset_task_env_overlay(token)

    assert config["general"]["model_name"] == "glm-5.2"
    assert config["general"]["model_type"] == "openai"
    assert config["general"]["base_url"] == "https://client-claw.example/v2"
    assert config["general"]["api_key"] == bytearray(b"request-key")
```

- [ ] **Step 2: Run the test and verify the current implementation fails**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_styled_export_llm_config_uses_request_overlay_instead_of_process_env -q
```

Expected: FAIL because the current implementation reads the static `os.environ` values and either selects `static-model` or reaches the `example.com` configuration.

- [ ] **Step 3: Write failing validation tests**

Add:

```python
@pytest.mark.parametrize(
    "overlay",
    [
        {"MODEL_PROVIDER": "OpenAI", "API_BASE": "https://llm.example/v1", "API_KEY": "key"},
        {"MODEL_NAME": "model", "MODEL_PROVIDER": "OpenAI", "API_KEY": "key"},
        {"MODEL_NAME": "model", "MODEL_PROVIDER": "OpenAI", "API_BASE": "https://llm.example/v1"},
        {
            "MODEL_NAME": "model",
            "MODEL_PROVIDER": "OpenAI",
            "API_BASE": "https://example.com/compatible-mode/v1",
            "API_KEY": "key",
        },
    ],
)
def test_styled_export_llm_config_rejects_invalid_config_before_client_creation(
    overlay,
):
    token = bind_task_env_overlay(overlay)
    try:
        with pytest.raises(ValueError, match="styled HTML LLM configuration is invalid"):
            dt._build_styled_export_llm_config()
    finally:
        reset_task_env_overlay(token)
```

- [ ] **Step 4: Run the validation tests and verify they fail for the missing behavior**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_styled_export_llm_config_rejects_invalid_config_before_client_creation -q
```

Expected: FAIL because the current builder returns incomplete or placeholder configurations instead of rejecting them.

### Task 2: Resolve and validate the report-style model from the request overlay

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py:330-365`
- Modify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py:368-379`
- Modify: `tests/unit/agentserver/test_deepresearch_stream_tool.py:153-438`

- [ ] **Step 1: Replace the process snapshot with overlay-aware resolution**

Import `read_env` alongside `export_agent_environ`. Replace the body of `_build_styled_export_llm_config()` with:

```python
    resolved = load_deepresearch_config()
    api_key = resolved["LLM_API_KEY"].strip()
    model_name = resolved["LLM_MODEL_NAME"].strip()
    base_url = resolved["LLM_BASE_URL"].strip()
    model_type = _map_provider_to_type(resolved["LLM_MODEL_TYPE"]).lower()

    if (
        not api_key
        or not model_name
        or not base_url
        or "example.com" in base_url.lower()
    ):
        raise ValueError("styled HTML LLM configuration is invalid")

    if model_type not in ("openai", "siliconflow"):
        model_type = "openai"

    return {
        "general": {
            "model_name": model_name,
            "model_type": model_type,
            "base_url": base_url,
            "api_key": bytearray(api_key, encoding="utf-8"),
            "extension": {
                "extra_body": {
                    "thinking": {"type": "disabled"},
                },
            },
            "verify_ssl": False,
        },
    }
```

- [ ] **Step 2: Make report-style TLS resolution overlay-aware**

In `_scoped_report_style_llm_context()`, replace the `_build_bridge_env(os.environ)` TLS lookup with:

```python
        async with scoped_deepresearch_tls_env(
            lambda: {
                "LLM_SSL_VERIFY": read_env("LLM_SSL_VERIFY", "false")
            }
        ):
```

Update the existing TLS unit tests to patch `dt.read_env` instead of `_build_bridge_env`, while preserving their assertions that TLS is scoped only to SDK initialization and restored after entry.

- [ ] **Step 3: Run the focused builder and TLS tests**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py -q \
  -k 'styled_export_llm_config or styled_report_llm_context'
```

Expected: all selected tests PASS.

- [ ] **Step 4: Verify invalid configuration uses the existing offline fallback**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_generate_report_html_falls_back_to_offline_conversion -q
```

Expected: PASS, demonstrating that builder failure is caught before SDK model initialization and HTML is still produced.

### Task 3: Regression verification and delivery

**Files:**
- Verify: `jiuwenclaw/agentserver/tools/deepresearch/tools.py`
- Verify: `tests/unit/agentserver/test_deepresearch_stream_tool.py`
- Verify: `tests/unit/agentserver/test_deepresearch_rewrite_tools.py`

- [ ] **Step 1: Run the full DeepResearch stream and rewrite tool suites**

Run:

```bash
/Users/hualinge/vscodeproject/jiuwenclaw/.venv/bin/python -m pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py -q
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Check formatting and the exact diff**

Run:

```bash
git diff --check
git status --short
git diff -- jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
```

Expected: no whitespace errors; only the planned production and test files are modified after the plan commit.

- [ ] **Step 3: Commit the implementation**

Run:

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch/tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "fix(deepresearch): reuse request model for styled HTML"
```

Expected: one implementation commit containing only the model-resolution fix and its regression tests.
