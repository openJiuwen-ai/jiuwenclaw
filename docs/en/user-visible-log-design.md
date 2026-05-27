# User Visible Log Design

## 1. Background and Problem

### 1.1 Problem Description

In streaming response scenarios, JiuwenClaw's logging system cannot distinguish between user-visible business logs and technical internal logs. All logs are mixed together, causing the following issues:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Log Noise Analysis (Streaming Response Scenario)          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Actual log statistics (1000 user-visible log samples):                     │
│  ─────────────────────────────────────────────────────────────────           │
│  • Message dispatch logs (repetitive noise): ~980 entries (98%)             │
│  • Critical user operation logs: ~20 entries (2%)                           │
│                                                                             │
│  Dispatch log example (noise):                                              │
│  2026-03-27 11:51:27.050 INFO jiuwenclaw.gateway.channel_manager:           │
│  Retrieved from robot_messages, preparing to dispatch: id=msg123 channel_id=feishu │
│  2026-03-27 11:51:27.100 INFO jiuwenclaw.gateway.channel_manager:           │
│  Dispatched to Channel: channel_id=feishu id=msg123                         │
│  (Hundreds of similar logs are generated during streaming responses)        │
│                                                                             │
│  Logs users actually care about:                                            │
│  2026-03-27 11:51:26.142 INFO jiuwenclaw.tools.note:                        │
│  [NOTE_TOOL] Creating note - title: Meeting Record                          │
│  2026-03-27 11:51:26.890 INFO jiuwenclaw.tools.note:                        │
│  [NOTE_TOOL] Note created: title=Meeting Record, id=12345                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Core Pain Points

| Pain Point | Description |
|------------|-------------|
| Poor user experience | Log noise is too high during streaming responses, users cannot focus on critical operations |
| Limited observability | Cannot display only operation-related logs to users |
| Difficult troubleshooting | Cannot quickly determine if an issue is caused by user operations or internal system problems |

## 2. Design Goals

| Goal | Description | Priority |
|------|-------------|----------|
| Tiered display | Distinguish critical operations from progress information to reduce noise | 🔴 High |
| Automatic output | Tag automatically appears in all output locations (console, log files) | 🔴 High |
| Zero disruption | Does not affect existing logging system functionality | 🔴 High |
| Observability integration | Support filtering by level for display | 🟡 Medium |
| Unified API | Use only string values, eliminate boolean/string mixing | 🟡 Medium |

## 3. Tiered Tag System

### 3.1 Tag Definition

A two-tier Tag system is adopted to categorize user-visible logs into critical operations and progress information:

| Tag | user_visible value | Purpose | Expected proportion |
|-----|-------------------|---------|---------------------|
| `[USER]` | `'critical'` | Critical user operations (create note, query results, operation success/failure) | ~2% |
| `[USER_PROGRESS]` | `'progress'` | Progress information (message dispatch, data flow) | ~98% |
| No Tag | Not set | Technical internal logs (system startup, internal loops, technical errors) | - |

### 3.2 Tag Position in Logs

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Log Format Example                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Critical user operation ([USER] Tag):                                      │
│  2026-05-26 11:51:26.142 [123] INFO user_id=u001 domain_id=d001 app_id=a001 │
│  [USER] jiuwenclaw.tools.note:89: [NOTE_TOOL] Creating note                 │
│        │      │      │        │            │                                │
│     ProcessID Level Identity Fields  Tag    logger:lineno                    │
│                                                                             │
│  Progress information ([USER_PROGRESS] Tag):                                 │
│  2026-05-26 11:51:27.050 [123] INFO user_id=u001 domain_id=d001 app_id=a001 │
│  [USER_PROGRESS] jiuwenclaw.gateway:42: Retrieved from robot_messages...    │
│                                                                             │
│  Technical internal log (No Tag):                                           │
│  2026-05-26 11:51:25.335 [123] INFO user_id=null domain_id=null app_id=null │
│  jiuwenclaw.gateway.agent_client:15: Connecting to AgentServer              │
│                          ↑                                                  │
│                    No Tag, logger name directly                             │
│                                                                             │
│  Note: Identity fields (user_id/domain_id/app_id) are always output for     │
│        log aggregation analysis. When identity info is not set, null values │
│        are output.                                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Design Principles

1. **API Consistency** — Only use string values `'critical'` or `'progress'`, boolean values are no longer supported
2. **Precise marking** — Only mark logs that users actually care about, technical logs have no Tag by default
3. **Metadata-style Tag** — Tag position is after log level and before logger name, does not break timestamp continuity
4. **Business error separation** — Only mark business errors that users can understand, do not mark technical errors

## 4. Technical Implementation Architecture

### 4.1 Log Processing Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Log Processing Flow                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Code Layer: Business code adds marker                               │   │
│  │                                                                     │   │
│  │  logger.info(                                                       │   │
│  │      "Creating note: %s", title,                                    │   │
│  │      extra={'user_visible': 'critical'}  ← Add marker               │   │
│  │  )                                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          ↓                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Filter Layer 1: IdentityFieldFilter adds identity fields            │   │
│  │                                                                     │   │
│  │  • Read current identity info from IdentityStore singleton           │   │
│  │  • Set record.user_id, record.domain_id, record.app_id               │   │
│  │  • Set to None when no identity info                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          ↓                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Filter Layer 2: UserVisibleTagFilter adds Tag (text format only)    │   │
│  │                                                                     │   │
│  │  • Read record.user_visible attribute                                │   │
│  │  • Set record.user_tag field based on value                         │   │
│  │  • 'critical' → record.user_tag = "[USER] "                         │   │
│  │  • 'progress' → record.user_tag = "[USER_PROGRESS] "                │   │
│  │  • Other values → record.user_tag = ""                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          ↓                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Formatter Layer: Choose formatter based on format config            │   │
│  │                                                                     │   │
│  │  text mode: IdentityTextFormatter                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────┐ │   │
│  │  │ Format template:                                               │ │   │
│  │  │ %(asctime)s [%(process)d] %(levelname)s %(identity)s%(user_tag)s│ │   │
│  │  │ %(name)s:%(lineno)d: %(message)s                               │ │   │
│  │  │         ↑               ↑            ↑                          │ │   │
│  │  │      ProcessID     Identity Fields   User Tag                   │ │   │
│  │  └───────────────────────────────────────────────────────────────┘ │   │
│  │                                                                     │   │
│  │  json mode: JsonUserVisibleFormatter                                │   │
│  │  ┌───────────────────────────────────────────────────────────────┐ │   │
│  │  │ • Output complete JSON structure                               │ │   │
│  │  │ • Field order: timestamp→process→level→user_tag→identity→logger→│ │   │
│  │  │                lineno→message→component→user_visible           │ │   │
│  │  │ • Automatic sensitive data sanitization                        │ │   │
│  │  └───────────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          ↓                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Output Layer: Output to different targets based on format config    │   │
│  │                                                                     │   │
│  │  text mode:                                                          │   │
│  │    stdout ├──→ gateway.log ├──→ channel.log ├──→ agent_server.log   │   │
│  │           └──→ full.log                                             │   │
│  │                                                                     │   │
│  │  json mode:                                                          │   │
│  │    stdout ├──→ gateway.json ├──→ channel.json ├──→ agent_server.json│   │
│  │           └──→ full.json                                            │   │
│  │                                                                     │   │
│  │  dual mode: Output both .log and .json files                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Complete Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│         Complete Data Flow from User Input to Response Return (with Tag Marking)         │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────┐    ┌──────────┐    ┌────────────────┐    ┌──────────────┐               │
│  │User Input│───▶│ Channel  │───▶│ ChannelManager │───▶│MessageHandler│               │
│  └─────────┘    └──────────┘    └────────────────┘    └──────────────┘               │
│                     ↓                  ↓                     ↓                        │
│              Message receive       Message forward        Message enqueue              │
│              [USER] Tag            [USER] Tag             [USER] Tag                  │
│                                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────┐    ┌──────────┐           │
│  │ AgentServer  │───▶│MessageHandler│───▶│ ChannelManager │───▶│ Channel  │───▶ User  │
│  └──────────────┘    └──────────────┘    └────────────────┘    └──────────┘           │
│       ↓                   ↓                    ↓                    ↓                  │
│  Tool execution       Processing complete    Dispatch logs        Send logs           │
│  [USER] Tag           [USER] Tag          [USER_PROGRESS] Tag    [USER] Tag           │
│                                                                                         │
│  Legend:                                                                                │
│  ─────────                                                                              │
│  [USER] Tag            = Critical user operation logs (create note, query results,     │
│                          operation success/failure)                                     │
│  [USER_PROGRESS] Tag   = Progress information logs (message dispatch, data flow)       │
│  No Tag                = Technical internal logs (system startup, internal loops,       │
│                          technical errors)                                              │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

## 5. API Usage

### 5.1 Log Recording API

Pass the marker through Python logging's `extra` parameter:

```python
# Critical user operation (produces [USER] Tag)
logger.info(
    "Creating note: %s",
    title,
    extra={'user_visible': 'critical'}
)

# Progress information (produces [USER_PROGRESS] Tag)
logger.info(
    "Sending reply to user...",
    extra={'user_visible': 'progress'}
)

# Technical internal log (No Tag)
logger.info("Connecting to AgentServer")  # No extra parameter
```

### 5.2 Log Parsing API (for observability systems)

```python
def get_log_visibility(log_line: str) -> str:
    """Determine the user visibility level of a log line.

    Returns:
        - 'critical': Contains [USER] Tag, represents critical user operation
        - 'progress': Contains [USER_PROGRESS] Tag, represents progress information
        - 'technical': No Tag, represents technical internal log
    """
    if "[USER_PROGRESS]" in log_line:
        return 'progress'
    elif "[USER]" in log_line:
        return 'critical'
    else:
        return 'technical'
```

### 5.3 JSON Format Output

When log format is set to `json` or `dual`, logs are output in JSON format. JSON format includes the following fields:

```json
{
  "timestamp": "2026-05-26 11:51:26.142",
  "process": 123,
  "level": "INFO",
  "user_tag": "[USER] ",
  "user_id": "user_001",
  "domain_id": "domain_001",
  "app_id": "app_001",
  "logger": "jiuwenclaw.tools.note",
  "lineno": 89,
  "message": "[NOTE_TOOL] Creating note - title: Meeting Record",
  "component": "agent_server",
  "user_visible": "critical"
}
```

**Field Description**:

| Field | Description |
|-------|-------------|
| `timestamp` | Timestamp (text format `"2026-05-26 11:51:26.142"` or ISO 8601) |
| `process` | Process ID |
| `level` | Log level |
| `user_tag` | User visible Tag (only output when has value) |
| `user_id` / `domain_id` / `app_id` | Identity fields (always output, null values facilitate log aggregation) |
| `logger` | Logger name |
| `lineno` | Source code line number |
| `message` | Log message |
| `component` | Component classification (gateway/channel/agent_server) |
| `user_visible` | Visibility level (only output when `critical` or `progress`) |

For unmarked logs, `user_tag` and `user_visible` fields do not appear in JSON output.

## 6. Configuration Options

### 6.1 Log Format Configuration

The system supports three log output formats:

| Format | Console Output | File Output | Use Case |
|--------|----------------|-------------|----------|
| `text` | Text format | `.log` files (text) | Development environment |
| `json` | JSON format | `.json` files (JSON) | Production environment |
| `dual` | Text format | `.log` + `.json` files | Mixed environment |

```yaml
logging:
  format: json  # Default: text (development environment)
```

```bash
# Set via environment variable
JIUWENCLAW_LOG_FORMAT=json jiuwenclaw-app
```

### 6.2 Environment Variable Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `JIUWENCLAW_LOG_FORMAT` | `text` | Log output format (text/json/dual) |
| `JIUWENCLAW_LOG_USER_VISIBLE` | `true` | Enable `[USER]` Tag |
| `JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE` | `true` | Enable `[USER_PROGRESS]` Tag |

```bash
# Disable [USER] Tag (show only progress information)
JIUWENCLAW_LOG_USER_VISIBLE=false jiuwenclaw-app

# Disable [USER_PROGRESS] Tag (show only critical operations, recommended for user-facing)
JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE=false jiuwenclaw-app

# Disable all user visible Tags
JIUWENCLAW_LOG_USER_VISIBLE=false JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE=false jiuwenclaw-app
```

### 6.3 config.yaml Configuration

```yaml
logging:
  # Unified output format control
  format: json  # text/json/dual

  # Log output switches
  console_enabled: true   # Console log switch
  file_enabled: true      # File log switch

  # JSON format detailed configuration
  json:
    timestamp_format: text        # Timestamp format: text or iso8601
    include_component: true       # Auto-add component classification field
    sanitize_sensitive_data: true # Sensitive data sanitization
    exc_info_style: simple        # Exception info style: simple or full

  # User visible Tag configuration
  tags:
    user_visible: true           # Enable [USER] Tag (critical user operations)
    user_progress_visible: true  # Enable [USER_PROGRESS] Tag (progress information)
```

Configuration priority: Environment variables > config.yaml > Default values

## 7. Log Classification Standards

### 7.1 Critical Operation Logs (`user_visible='critical'`)

| Category | Content | Example |
|----------|---------|---------|
| Operation in progress | User-initiated operation is executing | "Creating note..." |
| Operation success | Operation completed successfully | "Note created successfully, id=12345" |
| Operation failure | User-understandable failure reason | "Failed to create note: title too long" |
| Query results | Results of user-requested queries | "Found 3 memos" |
| Permission approval | Permission request and approval results | "Tool xxx requires authorization, please confirm" |

### 7.2 Progress Information Logs (`user_visible='progress'`)

| Category | Content | Example |
|----------|---------|---------|
| Message dispatch | Message being dispatched to channel | "Sending reply to user..." |
| Data flow | Message flowing within system | "Retrieved from robot_messages, preparing to dispatch" |
| Intermediate state | Intermediate states during processing | "Dispatched to Channel: feishu" |

### 7.3 Technical Internal Logs (No marker)

| Category | Content | Example |
|----------|---------|---------|
| System startup | Service startup, connection establishment | "Connecting to AgentServer" |
| Internal loops | ReAct iterations, task loops | "ReAct iteration 1/10" |
| Technical errors | Underlying technical errors | "Connection timeout" |
| Debug information | All DEBUG level logs | Any logger.debug() call |

## 8. Module Integration Relationships

### 8.1 Core Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Core Component Structure                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  jiuwenclaw/utils.py                                                        │
│  ─────────────────────────────────────────────────────────────────────────  │
│  ├── LoggingTagConfig           ← Tag configuration management (env + yaml) │
│  ├── UserVisibleTagFilter       ← Tag filter (adds user_tag field)          │
│  ├── IdentityFieldFilter        ← Identity filter (adds user_id/domain_id/app_id) │
│  ├── JsonUserVisibleFormatter   ← JSON formatter (outputs complete JSON)    │
│  └── IdentityTextFormatter      ← Text formatter (uses %(user_tag)s placeholder) │
│                                                                             │
│  jiuwenclaw/utils.py::setup_logger()                                        │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • Add IdentityFieldFilter to all Handlers (auto-add identity fields)       │
│  • Add UserVisibleTagFilter to text format Handlers                         │
│  • Use IdentityTextFormatter as text formatter                              │
│  • Use JsonUserVisibleFormatter for JSON format                             │
│                                                                             │
│  Log format template:                                                        │
│  "%(asctime)s [%(process)d] %(levelname)s %(identity)s%(user_tag)s%(name)s:%(lineno)d: %(message)s" │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Text Format Field Order

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Text Log Field Order                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  2026-05-26 11:51:26.142 [123] INFO user_id=u001 [USER] jiuwenclaw.note:89: │
│  │                         │      │        │            │           │       │
│  │                         │      │        │            │           │       │
│  ▼                         ▼      ▼        ▼            ▼           ▼       │
│  Timestamp               ProcessID Level Identity    user_tag   logger:lineno │
│                                                                             │
│  %(asctime)s            %(process)d  %(levelname)s  %(identity)s  %(user_tag)s  %(name)s:%(lineno)d │
│                                                                             │
│  Note: %(identity)s format is "user_id=xxx domain_id=xxx app_id=xxx "       │
│        %(user_tag)s format is "[USER] " or "[USER_PROGRESS] " or ""         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 Marked Module List

| Module Layer | File | Mark Locations |
|--------------|------|----------------|
| **Infrastructure** | `utils.py` | Core implementation |
| **Business Tools** | `note_tools.py` | ~6 locations |
| | `alarm_tools.py` | ~4 locations |
| | `memory_tools.py` | ~2 locations |
| | `task_tools.py` | ~11 locations |
| | `image_tools.py` | ~2 locations |
| | `audio_tools.py` | ~1 location |
| | `multi_session_toolkits.py` | ~4 locations |
| | `react_agent.py` | ~4 locations |
| **Gateway Layer** | `message_handler.py` | ~8 locations |
| | `channel_manager.py` | ~3 locations |
| **Channel Layer** | `feishu.py` | ~4 locations |
| | `xiaoyi_channel.py` | ~3 locations |
| | `wecom_channel.py` | ~3 locations |
| | `web_channel.py` | ~2 locations |
| | `dingding.py` | ~2 locations |
| | `telegram_channel.py` | ~2 locations |

## 9. Test Coverage

### 9.1 Unit Test Files

| Test File | Test Content |
|------------|--------------|
| `test_user_visible_tag_filter.py` | Tag filter logic tests |
| `test_json_user_visible_formatter.py` | JSON formatter tests |
| `test_user_visible_formatter_format.py` | Format position validation |
| `test_multi_handler_no_duplication.py` | Multi-handler no duplication tests |

### 9.2 Tag Filter Test Cases

| Test Case | Test Content |
|-----------|--------------|
| `test_filter_sets_user_tag_field_for_critical` | `'critical'` produces `[USER]` Tag |
| `test_filter_sets_user_tag_field_for_progress` | `'progress'` produces `[USER_PROGRESS]` Tag |
| `test_filter_sets_empty_tag_for_no_user_visible` | No Tag when unmarked |
| `test_filter_sets_empty_tag_for_unknown_value` | No Tag for unknown values |
| `test_filter_respects_config_disabled_user_visible` | Config disable takes effect |
| `test_filter_respects_config_disabled_user_progress_visible` | Config disable takes effect |
| `test_filter_does_not_modify_record_msg` | Does not modify message content |

## 10. Output Effect Comparison

### 10.1 Before Modification (No Tag)

```bash
2026-03-27 11:51:26.142 INFO jiuwenclaw.tools.note: [NOTE_TOOL] Creating note - title: Meeting Record
2026-03-27 11:51:26.890 INFO jiuwenclaw.tools.note: [NOTE_TOOL] Note created: title=Meeting Record
2026-03-27 11:51:27.050 INFO jiuwenclaw.gateway.channel_manager: Retrieved from robot_messages, preparing to dispatch...
2026-03-27 11:51:27.100 INFO jiuwenclaw.gateway.channel_manager: Dispatched to Channel...
# All logs mixed together, cannot distinguish user-visible logs from technical logs
```

### 10.2 After Modification (With Tiered Tag)

**Text Format (format=text or .log files in dual mode)**:

```bash
# ============ System Startup Phase (No Tag, no identity fields) ============
2026-05-26 11:51:25.335 [123] INFO user_id=null domain_id=null app_id=null jiuwenclaw.gateway.agent_client:15: Connecting to AgentServer
2026-05-26 11:51:25.339 [123] INFO user_id=null domain_id=null app_id=null jiuwenclaw.gateway.agent_client:22: Connected to AgentServer

# ============ User Operation Phase ([USER] Tag, with identity fields) ============
2026-05-26 11:51:26.100 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER] jiuwenclaw.gateway.message_handler:42: Received user message, processing
2026-05-26 11:51:26.142 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER] jiuwenclaw.tools.note:89: [NOTE_TOOL] Creating note - title: Meeting Record
2026-05-26 11:51:26.890 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER] jiuwenclaw.tools.note:102: [NOTE_TOOL] Note created: title=Meeting Record, id=12345
2026-05-26 11:51:27.000 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER] jiuwenclaw.gateway.message_handler:208: Request processing complete

# ============ Response Dispatch Phase ([USER_PROGRESS] Tag) ============
2026-05-26 11:51:27.050 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER_PROGRESS] jiuwenclaw.gateway.channel_manager:35: Retrieved from robot_messages, preparing to dispatch...
2026-05-26 11:51:27.100 [123] INFO user_id=u001 domain_id=d001 app_id=a001 [USER_PROGRESS] jiuwenclaw.gateway.channel_manager:42: Dispatched to Channel...
```

**JSON Format (format=json or .json files in dual mode)**:

```json
{
  "timestamp": "2026-05-26 11:51:26.142",
  "process": 123,
  "level": "INFO",
  "user_tag": "[USER] ",
  "user_id": "u001",
  "domain_id": "d001",
  "app_id": "a001",
  "logger": "jiuwenclaw.tools.note",
  "lineno": 89,
  "message": "[NOTE_TOOL] Creating note - title: Meeting Record",
  "component": "agent_server",
  "user_visible": "critical"
}
```

### 10.3 Observability System Integration Effect

```python
# Simple mode (default): Show only [USER] Tag
>>> get_user_logs(mode='simple')
2026-03-27 11:51:26.142 [USER] jiuwenclaw.tools.note: [NOTE_TOOL] Creating note
2026-03-27 11:51:26.890 [USER] jiuwenclaw.tools.note: [NOTE_TOOL] Note created
2026-03-27 11:51:27.000 [USER] jiuwenclaw.gateway.message_handler: Request processing complete

# Detailed mode: Show [USER] and [USER_PROGRESS]
>>> get_user_logs(mode='detailed')
2026-03-27 11:51:26.142 [USER] jiuwenclaw.tools.note: [NOTE_TOOL] Creating note
2026-03-27 11:51:26.890 [USER] jiuwenclaw.tools.note: [NOTE_TOOL] Note created
2026-03-27 11:51:27.050 [USER_PROGRESS] jiuwenclaw.gateway.channel_manager: Preparing to dispatch...
2026-03-27 11:51:27.100 [USER_PROGRESS] jiuwenclaw.gateway.channel_manager: Dispatched...

# Debug mode: Show all logs (including technical internal logs)
>>> get_user_logs(mode='debug')
(All logs, no filtering)
```

## 11. Version History

| Version | Date | Change Description |
|---------|------|---------------------|
| 1.0 | 2026-03-27 | Initial version, covering tool layer log marking |
| 1.1 | 2026-03-27 | Added data flow path analysis |
| 1.2 | 2026-03-28 | Updated to actual implementation (Tag at metadata position) |
| 1.3 | 2026-03-30 | Added tiered Tag system: `[USER]` vs `[USER_PROGRESS]` |
| 1.4 | 2026-05-26 | Updated based on actual code implementation: JSON output structure, config path, log format modes |

---

**Document Created**: 2026-05-26
**Document Maintained**: JiuwenClaw Team