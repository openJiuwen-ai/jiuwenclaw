# Browser tools

## 1. Conceptual Overview

Browser tools in JiuwenSwarm enable driving a real Chrome instance for form filling, clicks, uploads, and web tasks. Users first configure Chrome path in the web UI and start the browser service; the system launches an attachable Chrome instance. When needed, the agent connects to and controls this browser.

### 1.1 Key Capabilities

The browser tools currently support the following capabilities:

- Open web pages and wait for loading
- Continue operations on already logged-in websites
- Click buttons, input text, select page elements
- Execute multi-step web tasks
- Reuse the same browser session to reduce repeated logins
- Read page titles, URLs, and page content when needed
- Support complex tasks like file uploads, email composition, and web form submissions

### 1.2 Typical Use Cases

- **Web Information Extraction**: Extract structured information from news sites, document pages, etc.
- **Email Operations**: Log in to email to send messages, check inbox, download attachments
- **Form Filling**: Automatically fill out online registration, application forms
- **Online Shopping**: Browse products, compare prices, add to cart
- **Enterprise System Operations**: Complete approvals, queries, etc. in internal systems

## 2. Quick Start (Frontend Operations Only)

Install Chrome first.

### 2.1 Step 1: Chrome path and profile

1. Open Chrome.
2. Visit `chrome://version`.
3. Note:
   - **Executable path** → `CHROME_PATH`
   - **Profile path** → confirms user data for login debugging

Where:
- Executable path is generally the complete path to `chrome.exe`.
- Profile directory helps confirm the current browser account and data directory, facilitating troubleshooting of login state or authorization issues.

### 2.2 Step 2: Open the browser service panel

1. Open the JiuwenSwarm web UI.

   ![Browser panel](../assets/images/browser1.png)

2. Go to **Settings** → **Browser service**.
3. Find the Chrome path field.

   ![Chrome path](../assets/images/browser2.png)


### 2.3 Step 3: Set `CHROME_PATH`
1. Copy the executable path from `chrome://version`.

   ![Chrome version](../assets/images/browser3.png)

2. Paste into **CHROME_PATH** and **Save path**.

### 2.4 Step 4: Start the browser service
1. Click **Start browser service**.
2. A new Chrome window should open — that instance is controlled by the agent.

This popped-up Chrome is the browser instance that can be controlled by the agent later.

### 2.5 Step 5: Log in manually

For mail, SSO, or intranet, complete login **in that Chrome window** first.

If your tasks require website login, email authorization, enterprise system access, or using existing account states, complete the necessary manual operations in that Chrome, such as:

- Log in to Gmail / Outlook / corporate email
- Complete manual authentication via SMS, verification code, QR code scan, etc.
- Allow site access permissions
- Open the target page to be operated

### 2.6 Step 6: Use from chat

Ask the agent to open pages, fill forms, or continue logged-in flows. It uses the authorized Chrome, not a cold profile.

After completing authorization, you can ask the agent to perform browser tasks in the conversation, such as:

- Open a webpage and read information
- Continue clicking and filling forms after login
- Compose emails, upload attachments, wait for confirmation in email

When the task requires a browser, the agent will control this already authorized Chrome, not create a completely stateless browser again.

## 3. Usage Tips

To make the browser tools more stable, follow these recommendations:

- Prefer using the real Chrome installed locally rather than a temporary browser.
- After starting the browser service, prioritize completing login and authorization in the popped-up Chrome.
- For long-flow tasks, try to maintain the same session to avoid frequently changing browser states.
- For scenarios involving email, enterprise systems, online banking, etc., it is recommended to complete manual login first before letting the agent continue operations.
- If the browser state is abnormal, restart the browser service and test again.
- Ensure `PLAYWRIGHT_CDP_URL` matches the debugging address and port in `config.yaml`.
- Keep `BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE=0` to prevent the model from splitting browser tasks into too many short calls.

## 4. Practical Cases

### 4.1 Case 1: Web Information Extraction
**Task Description**: Extract news titles and summaries from a specific web page

**Operation Steps**:
1. Start the browser service and complete necessary authorization
2. Enter in the conversation: "Please extract today's headline news title and summary from https://example.com/news"
3. The agent will automatically call browser tools to access the specified web page
4. The browser will parse the page content and extract the required information
5. The agent will organize the extracted information and return it to the user

### 4.2 Case 2: Email Sending (with Attachment)
**Task Description**: Send an email with attachment using Gmail

**Operation Steps**:
1. Start the browser service
2. Log in to Gmail account in the popped-up Chrome
3. Enter in the conversation: "Please send an email to test@example.com, subject: Test Email, content: This is a test email, attachment: /path/to/file.pdf"
4. The agent will call browser tools to open Gmail compose page
5. The browser will automatically fill in recipient, subject, content, and upload the attachment
6. After sending is complete, the agent will notify the user that the email has been sent

> **Note**: Operation screenshots will be added after future frontend updates. Each case will display 1-2 images, mainly showing the key execution process and final results.

## 5. Backend Configuration

### 5.1 Configuration Files Overview

The browser tool configuration involves several core files that work together to configure and run the browser:

- **`config/config.yaml`**: Mainly configures Chrome startup parameters, such as Chrome executable path, remote debugging address and port. These configurations are the foundation for browser startup.
- **`.env`**: Configures browser runtime, MCP connection, Playwright parameters, timeout settings and other environment variables that affect the browser's runtime behavior.
- **`.env.template`**: Environment variable template file containing all available environment variables and their default values, which can be used as a configuration reference.

The relationship between these three files is: `config/config.yaml` provides the basic parameters for browser startup, and `.env` provides the runtime environment configuration. Together, they ensure the browser tools work properly.

### 5.2 Browser Configuration in config.yaml

| Configuration Item | Type | Default Value | Description |
|--------------------|------|---------------|-------------|
| `browser.chrome_path` | string/map | - | Chrome executable path, can be a single string or a map by OS |
| `browser.remote_debugging_address` | string | "127.0.0.1" | Chrome remote debugging listening address |
| `browser.remote_debugging_port` | integer | 9222 | Chrome remote debugging port |
| `browser.user_data_dir` | string | "" | Chrome user data directory, use system default when empty |
| `browser.profile_directory` | string | "Default" | Chrome Profile name to use |

**Example configuration by OS**:
```yaml
browser:
  chrome_path:
    windows: "C:\\Users\\YOUR_USER\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
    macos: "/Applications/Google Chrome.app"
    linux: "/usr/bin/google-chrome"
  remote_debugging_address: "127.0.0.1"
  remote_debugging_port: 9222
  user_data_dir: ""
  profile_directory: "Default"
```

### 5.2 Browser Configuration in .env

#### 5.2.1 Browser MCP Wrapper Configuration

| Environment Variable | Default Value | Description |
|----------------------|---------------|-------------|
| `BROWSER_RUNTIME_MCP_ENABLED` | 1 | Whether to enable browser MCP wrapper |
| `BROWSER_RUNTIME_MCP_CLIENT_TYPE` | streamable-http | MCP client type |
| `BROWSER_RUNTIME_MCP_SERVER_ID` | playwright_runtime_wrapper | Wrapper identifier information |
| `BROWSER_RUNTIME_MCP_SERVER_NAME` | playwright-runtime-wrapper | Wrapper name |
| `BROWSER_RUNTIME_MCP_SERVER_PATH` | http://127.0.0.1:8940/mcp | Wrapper access address |
| `BROWSER_RUNTIME_MCP_TIMEOUT_S` | 300 | MCP connection or call layer timeout control |
| `BROWSER_RUNTIME_MCP_HOST` | 127.0.0.1 | Host used when automatically starting wrapper locally |
| `BROWSER_RUNTIME_MCP_PORT` | 8940 | Port used when automatically starting wrapper locally |
| `BROWSER_RUNTIME_MCP_PATH` | /mcp | Path used when automatically starting wrapper locally |
| `BROWSER_RUNTIME_MCP_COMMAND` | - | Wrapper startup command override, usually left empty |
| `BROWSER_RUNTIME_MCP_ARGS` | - | Wrapper startup arguments override, usually left empty |
| `BROWSER_RUNTIME_MCP_AUTO_SSE_FALLBACK` | 1 | Whether to allow SSE fallback in certain modes |

#### 5.2.2 Official Playwright MCP Configuration

| Environment Variable | Default Value | Description |
|----------------------|---------------|-------------|
| `PLAYWRIGHT_MCP_COMMAND` | npx | Command to start official Playwright MCP |
| `PLAYWRIGHT_MCP_ARGS` | -y @playwright/mcp@latest | Arguments to start official Playwright MCP |
| `PLAYWRIGHT_CDP_URL` | http://127.0.0.1:9222 | CDP address to connect to the started Chrome, should match debugging address and port in config.yaml |

#### 5.2.3 Timeout and Execution Strategy Configuration

| Environment Variable | Default Value | Description |
|----------------------|---------------|-------------|
| `PLAYWRIGHT_TOOL_TIMEOUT_S` | 300 | Total watchdog timeout for browser tool execution |
| `BROWSER_TIMEOUT_S` | 300 | Default long timeout for browser tasks; if the model passes a smaller timeout_s, it will be clamped to at least this value |
| `BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE` | 0 | Whether to allow the model to shorten task timeouts, recommended to keep as 0 |

### 5.3 Recommended Minimal Configuration

To use the browser tools properly, ensure at least the following fields are correct:

**config/config.yaml**
```yaml
browser:
  chrome_path: "C:\\Users\\YOUR_USER\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
  remote_debugging_address: "127.0.0.1"
  remote_debugging_port: 9222
  user_data_dir: ""
  profile_directory: "Default"
```

**.env**
```dotenv
BROWSER_RUNTIME_MCP_ENABLED=1
BROWSER_RUNTIME_MCP_CLIENT_TYPE=streamable-http
BROWSER_RUNTIME_MCP_SERVER_PATH=http://127.0.0.1:8940/mcp
PLAYWRIGHT_MCP_COMMAND=npx
PLAYWRIGHT_MCP_ARGS=-y @playwright/mcp@latest
PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222
PLAYWRIGHT_TOOL_TIMEOUT_S=300
BROWSER_TIMEOUT_S=300
BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE=0
```

## 6. Principle and Code Architecture

### 6.1 Technical Architecture

The core flow of the current browser tools is as follows:

- **Web UI**: Provides a "Browser service" panel for configuring Chrome executable path and starting/stopping the browser service
- **Backend Application**: Starts local Chrome with remote debugging capabilities via `browser_start_client.py`
- **Browser Runtime**: Based on Playwright MCP encapsulation, browser tools communicate with the runtime via MCP client
- **Agent Call**: When the agent calls tools like `browser_run_task`, the runtime converts natural language tasks into browser operation steps
- **Session Management**: Browsers are reused by `session_id`, maintaining login state, page context, and authorization status within the same session

In simple terms:

### Frontend
- `jiuwenswarm/channels/web/frontend/src/components/BrowserPanel/index.tsx` — path, save, start service.

### Backend
- `app.py` — `path.get`, `path.set`, `browser.start`, etc.
- `jiuwenswarm/agents/harness/common/tools/browser_start_client.py` — Chrome launch from `config.yaml`.
- `jiuwenswarm/agents/harness/common/tools/browser_tools.py` — MCP client, auto-start wrapper.
- `jiuwenswarm/agents/harness/common/tools/browser-move/src/playwright_runtime_mcp_server.py` — MCP server.
- `.../playwright_runtime/runtime.py`, `service.py`, `agents.py`, `config.py` — runtime orchestration.

`UI config → start Chrome → runtime attaches → agent runs tasks`

### 6.2 Core Code

The core code of the browser tools is mainly distributed in the following modules:

- **Tool Management Module**: `jiuwenswarm/agents/harness/common/tools/` is the underlying module that manages all tools in the system. Browser-related tools are mainly implemented under this module.
- **Frontend Interface Module**: `jiuwenswarm/channels/web/frontend/` is responsible for user interface interaction.

Specific file descriptions:

| Module | File Path | Function Description |
|--------|-----------|----------------------|
| Frontend Browser Service Panel | `jiuwenswarm/channels/web/frontend/src/components/BrowserPanel/index.tsx` | Responsible for reading path, saving path, triggering "Start browser service" |
| Backend Application Entry | `app.py` | Provides frontend call entries like `path.get`, `path.set`, `browser.start` |
| Chrome Startup Script | `tools/browser_start_client.py` | Reads `browser.*` configuration from `config/config.yaml`, starts Chrome with remote debugging capabilities |
| Browser MCP Access | `tools/browser_tools.py` | Browser MCP wrapper access, automatic startup, client patch, configuration building |
| Browser Runtime MCP Server | `tools/browser-move/src/playwright_runtime_mcp_server.py` | Browser runtime MCP server entry |
| Browser Runtime Orchestration Layer | `tools/browser-move/src/playwright_runtime/runtime.py` | Browser runtime orchestration layer |
| Browser Task Execution | `tools/browser-move/src/playwright_runtime/service.py` | Browser task execution, session reuse, timeout guardrails |
| Browser Runtime Configuration | `tools/browser-move/src/playwright_runtime/config.py` | Playwright MCP and browser runtime configuration parsing |

## 7. Summary

The essence of browser tools is to allow the agent to execute web operations on your already authorized real Chrome; the frontend is responsible for configuration and startup, while the backend is responsible for takeover and automated execution.