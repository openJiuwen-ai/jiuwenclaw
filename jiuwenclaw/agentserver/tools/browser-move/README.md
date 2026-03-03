# browser-move

Runtime wrapper for using official Playwright MCP (`@playwright/mcp`) from openJiuwen.

## Current `.env` baseline (this repo)

These are the env vars currently used in your local `.env`:

```dotenv
# LLM
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=anthropic/claude-sonnet-4.5
MODEL_PROVIDER=openrouter

# Official Playwright MCP (spawned by this runtime)
PLAYWRIGHT_MCP_COMMAND=npx
PLAYWRIGHT_MCP_ARGS=-y @playwright/mcp@latest
PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222

# Runtime timeout guardrails
BROWSER_TIMEOUT_S=180
PLAYWRIGHT_TOOL_TIMEOUT_S=180
```

If you keep only the block above, this runtime will work with your current setup.

## Start the wrapper MCP server (streamable-http)

```powershell
uv run python src/playwright_runtime_mcp_server.py --transport streamable-http --host 127.0.0.1 --port 8940
```

Default streamable-http endpoint path is:
- `http://127.0.0.1:8940/mcp`

You can change path with `--path` or `PLAYWRIGHT_RUNTIME_MCP_PATH`.

## openJiuwen integration (streamable-http)

Use this MCP registration in your main agent:

```python
from openjiuwen.core.foundation.tool import McpServerConfig

mcp_cfg = McpServerConfig(
    server_id="playwright_runtime_wrapper_http",
    server_name="playwright-runtime-wrapper",
    server_path="http://127.0.0.1:8940/mcp",
    client_type="streamable-http"
)

await Runner.resource_mgr.add_mcp_server(mcp_cfg, tag="agent.main")
agent.ability_manager.add(mcp_cfg)
```

In this repo, `jiuwenclaw.agentserver.tools.browser_tools` now does this
automatically for the browser wrapper:

- monkeypatches current `openjiuwen` tool manager so `streamable-http` is accepted
- auto-starts `src/playwright_runtime_mcp_server.py --transport streamable-http`
- registers the wrapper into the agent as MCP tools

## Tools exposed by this wrapper

- `browser_run_task(task, session_id="", request_id="", timeout_s=0)`
  - prefer one comprehensive task instead of many tiny retries
  - omit `timeout_s` to use the configured long default
  - if `timeout_s` is lower than `BROWSER_TIMEOUT_S`, runtime clamps it back up by default
- `browser_cancel_task(session_id, request_id="")`
- `browser_clear_cancel(session_id, request_id="")`
- `browser_custom_action(action, session_id="", request_id="", params={})`
- `browser_list_custom_actions()`
- `browser_runtime_health()`

## Quick local checks

```powershell
python src/test_playwright_runtime.py
python src/test_playwright_runtime.py --live --query "Go to https://example.com and return page title."
```

## Notes

- Streamable HTTP is fully supported in `openjiuwen` tool manager and in this runtime's patched client layer.
- Browser sessions are sticky per `session_id`.
- `max_steps=20`, `max_failures=2`, and `retry_once=true` are hardcoded in runtime entrypoints.
- HTTP/streamable-http runs stateless by default unless you explicitly disable it.
