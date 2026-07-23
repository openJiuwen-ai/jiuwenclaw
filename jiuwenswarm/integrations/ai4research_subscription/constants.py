"""Stable identifiers and safety limits for the Codex subscription provider."""

CODEX_PROVIDER_NAME = "AI4ResearchCodex"
CODEX_MODEL_ALIAS = "codex-subscription"
SUPPORTED_CODEX_VERSION = "0.144.5"

MAX_MESSAGES = 256
MAX_TOOLS = 128
MAX_PROMPT_BYTES = 512 * 1024
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 128 * 1024
MAX_JSONL_LINE_BYTES = 512 * 1024
MAX_TOOL_CALLS = 32

DEFAULT_TURN_TIMEOUT_SECONDS = 180.0
PROCESS_TERMINATE_GRACE_SECONDS = 5.0
PROFILE_LOCK_TIMEOUT_SECONDS = 0.25
PROFILE_LOCK_POLL_SECONDS = 0.01
VERSION_VERIFY_TIMEOUT_SECONDS = 10.0
MAX_VERSION_OUTPUT_BYTES = 4096

# Turn directories are provider-owned and normally contain only the response
# schema plus a small amount of Codex runtime scratch data.  These ceilings keep
# synchronous cleanup bounded on the AgentServer event loop.
MAX_TURN_CLEANUP_ENTRIES = 256
MAX_TURN_CLEANUP_BYTES = 16 * 1024 * 1024
MAX_TURN_CLEANUP_DEPTH = 8
