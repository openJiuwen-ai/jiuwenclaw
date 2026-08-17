#!/usr/bin/env python3
"""
Log Reporter — standalone Python implementation extracted from src/log-reporter/.
Periodically scans log files, uploads incremental content, and reports to the sync API.

Usage:
    python scripts/log_reporter.py [--dry-run] [--once]

Options:
    --dry-run   Scan and print content but don't upload or report.
    --once      Run one scan cycle and exit (no interval).
    --interval  Scan interval in seconds (default: 300 = 5 minutes).

Environment overrides (optional, defaults read from ENV_FILE_PATH):
    LOG_REPORTER_ENV_FILE   Path to .xiaoyienv file
    LOG_REPORTER_CURSOR     Path to cursor store JSON
    LOG_REPORTER_BAK_DIR    Directory for temporary .bak files before upload
    LOG_REPORTER_INTERVAL   Scan interval in seconds (alternative to --interval)

Required env file format (key=value, one per line, # comments):
    SERVICE_URL=https://...
    PERSONAL-API-KEY=...
    PERSONAL-UID=...
"""

import os
import re
import sys
import json
import time
import random
import hashlib
import logging
import tempfile
import argparse
from pathlib import Path
from typing import Optional, Dict, List, Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [log-reporter] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("log-reporter")

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_SCAN_INTERVAL = 300  # 5 minutes
SCAN_JITTER = 30  # ±30 seconds random jitter to avoid thundering herd
DEFAULT_CURSOR_PATH = "/home/sandbox/.openclaw/.xiaoyilogging/.log-reporter-cursor.json"
DEFAULT_BAK_DIR = "/tmp/openclaw"
DEFAULT_ENV_FILE = "/home/sandbox/.jiuwenswarm/.xiaoyienv"

# Retry delays for upload and report (seconds)
RETRY_DELAYS = [10, 20, 30]
MAX_RETRIES = len(RETRY_DELAYS)

# Monitors — OpenClaw list plus JiuwenSwarm sandbox log paths.
# Sidecar logs live at /sidecar/proxyservice/ in the Claw test sandbox.
MONITORS = [
    {
        "path": "/home/sandbox/.jiuwenswarm/agent/.logs/full.log",
        "business_type": "jiuwenswarm-full",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.jiuwenswarm/agent/.logs/gateway.log",
        "business_type": "jiuwenswarm-gateway",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.jiuwenswarm/agent/.logs/agent_server.log",
        "business_type": "jiuwenswarm-agent-server",
        "json_parse": False,
    },
    {
        "path": "/tmp/openclaw/openclaw-{year-month-day}.log",
        "business_type": "openclaw-gateway",
        "json_parse": True,
    },
    {
        "path": "/tmp/openclaw/xiaoyi-channel-{year}{month}{day}.log",
        "business_type": "xiaoyi-channel",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.openclaw/workspace/logs/init_{year}{month}{day}_{hour}{minute}{second}.log",
        "business_type": "openclaw-init",
        "json_parse": False,
        "latest_only": True,
    },
    {
        "path": "/tmp/openclaw/skills-{year}{month}{day}.log",
        "business_type": "openclaw-skill",
        "json_parse": False,
    },
    {
        "path": "/tmp/openclaw/skill-toggle.log",
        "business_type": "skill-dynamization",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/full_backup_upload.log",
        "business_type": "openclaw-full-backup",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/selective_recover.log",
        "business_type": "selective-recover",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/updateUserKey.log",
        "business_type": "update-userkey",
        "json_parse": False,
    },
    {
        "path": "/sidecar/proxyservice/proxy_policy_fault.log",
        "business_type": "proxy-policy-fault",
        "json_parse": False,
    },
    {
        "path": "/sidecar/proxyservice/proxy_runtime_error.log",
        "business_type": "proxy-runtime-error",
        "json_parse": False,
    },
    {
        "path": "/sidecar/proxyservice/watchdog_alarm.dat",
        "business_type": "proxy-watchdog",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.openclaw/logs/celia_memory/celia_memory.log",
        "business_type": "celia-memory",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.openclaw/logs/Celia_memory.log",
        "business_type": "celia-memory",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.openclaw/logs/supervisord.log",
        "business_type": "supervisord",
        "json_parse": False,
    },
    {
        "path": "/home/sandbox/.openclaw/logs/stability/openclaw-stability-{year}-{month}-{day}T{hour}-{minute}-{second}-{any}.json",
        "business_type": "stability",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/delete_cloud_file.log",
        "business_type": "delete-cloud-file",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/read_file.log",
        "business_type": "read-file",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/jiuwen_swarm_delete_cloud_file.log",
        "business_type": "delete-cloud-file",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/jiuwen_swarm_full_backup_upload.log",
        "business_type": "openclaw-full-backup",
        "json_parse": False,
    },
    {
        "path": "/tmp/logs/restart.log",
        "business_type": "restart",
        "json_parse": False,
    },
]


# Remote monitors config URL — set to a JSON file URL to override built-in MONITORS
# Can also be set via LOG_REPORTER_REMOTE_CONFIG_URL environment variable.
REMOTE_MONITORS_CONFIG_URL = os.environ.get("LOG_REPORTER_REMOTE_CONFIG_URL", "")


def fetch_remote_monitors(config_url: str) -> Optional[List[Dict[str, Any]]]:
    """
    Download a remote JSON file that contains the monitors configuration.
    Returns the parsed monitors list, or None if the URL is empty or fetch fails.
    The JSON format should match MONITORS:
        [{"path": "...", "business_type": "...", "json_parse": true/false}, ...]
    """
    if not config_url or not config_url.strip():
        return None

    logger.info(f"Fetching remote monitors config from {config_url}")
    try:
        req = Request(config_url.strip(), method="GET")
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        monitors = json.loads(raw)
        if not isinstance(monitors, list):
            logger.warning("Remote monitors config is not a list, ignoring")
            return None
        for m in monitors:
            if not isinstance(m, dict) or "path" not in m or "business_type" not in m:
                logger.warning(f"Invalid monitor entry in remote config: {m}")
                return None
        logger.info(f"Loaded {len(monitors)} monitor(s) from remote config")
        return monitors
    except Exception as e:
        logger.warning(f"Failed to fetch remote monitors config: {e}")
        return None


# ── Env file reader ──────────────────────────────────────────────────────────

def read_env_file(env_path: str) -> Dict[str, str]:
    """Read key=value env file. Raises if required keys missing."""
    try:
        with open(env_path, "r") as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Environment file not found: {env_path}") from e

    env: Dict[str, str] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        idx = line.find("=")
        if idx == -1:
            continue
        key = line[:idx].strip()
        value = line[idx + 1:].strip()
        env[key] = value

    for key in ("SERVICE_URL", "PERSONAL-API-KEY", "PERSONAL-UID"):
        if key not in env or not env[key]:
            raise ValueError(f"Missing required env variable: {key}")

    return {
        "service_url": env["SERVICE_URL"],
        "api_key": env["PERSONAL-API-KEY"],
        "uid": env["PERSONAL-UID"],
    }


# ── Path Resolver ────────────────────────────────────────────────────────────

# Replace longer tokens first to avoid partial matches (matches TS order)
WILDCARD_TOKENS = [
    ("{year-month-day}", r"\d{4}-\d{2}-\d{2}"),
    ("{year}{month}{day}", r"\d{8}"),
    ("{hour}{minute}{second}", r"\d{6}"),
    ("{year}", r"\d{4}"),
    ("{month}", r"\d{2}"),
    ("{day}", r"\d{2}"),
    ("{hour}", r"\d{2}"),
    ("{minute}", r"\d{2}"),
    ("{second}", r"\d{2}"),
    ("{any}", ".*"),
]


def resolve_log_files(template_path: str, latest_only: bool = False) -> List[str]:
    """
    Convert a path template with date wildcards to actual files on disk.
    Scans the directory and returns all files matching the pattern, sorted.
    If latest_only is True, returns only the last (most recent) file.
    """
    directory = os.path.dirname(template_path)
    basename_pattern = os.path.basename(template_path)

    # Build regex from template
    regex_str = re.escape(basename_pattern)
    for token, replacement in WILDCARD_TOKENS:
        regex_str = regex_str.replace(re.escape(token), replacement)
    regex = re.compile(f"^{regex_str}$")

    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return []

    matching = [f for f in entries if regex.match(f)]
    matching.sort()
    if latest_only and matching:
        matching = [matching[-1]]
    return [os.path.join(directory, f) for f in matching]


# ── Cursor Store ─────────────────────────────────────────────────────────────

def _normalize_cursor(cursor: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize cursor keys: accept both camelCase (TS) and snake_case (Python)."""
    return {
        "last_size": cursor.get("last_size", cursor.get("lastSize", 0)),
        "last_line": cursor.get("last_line", cursor.get("lastLine", 0)),
        "last_modified": cursor.get("last_modified", cursor.get("lastModified", 0)),
    }


def load_cursor_store(store_path: str) -> Dict[str, Any]:
    try:
        with open(store_path, "r") as f:
            data = json.load(f)
        raw_files = data.get("files", {})
        normalized = {fp: _normalize_cursor(c) for fp, c in raw_files.items()}
        return {"files": normalized}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"files": {}}


def save_cursor_store(store_path: str, store: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        # Atomic write: write to temp file first, then rename
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(store_path),
            prefix=".log-reporter-cursor-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(store, f, indent=2)
            os.replace(tmp_path, store_path)  # atomic on Linux
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning(f"Cannot save cursor store: {e}")


def get_cursor(store: Dict[str, Any], file_path: str) -> Optional[Dict[str, Any]]:
    return store["files"].get(file_path)


def set_cursor(store: Dict[str, Any], file_path: str, cursor: Dict[str, Any]) -> None:
    store["files"][file_path] = cursor


# ── Scanner ──────────────────────────────────────────────────────────────────

def scan_file(file_path: str, cursor_store: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Read incremental content from a log file using byte-offset cursor.
    Reads in binary mode to avoid text-mode seek issues and encoding drift.
    Returns {file_path, content, new_cursor} or None if no new content.
    """
    try:
        stat = os.stat(file_path)
    except FileNotFoundError:
        return None

    current_size = stat.st_size
    current_mtime_ms = int(stat.st_mtime * 1000)
    cursor = get_cursor(cursor_store, file_path)

    # Determine start byte
    last_size = cursor.get("last_size", 0) if cursor else 0
    last_modified = cursor.get("last_modified", 0) if cursor else 0

    if cursor is None:
        start_byte = 0  # New file, read from beginning
    elif current_size > last_size:
        start_byte = last_size  # File grew, read from where we left off
    elif current_size < last_size and current_mtime_ms >= last_modified:
        start_byte = 0  # File rotated (truncated + rewritten)
    else:
        return None  # No change

    # Read in binary mode — byte offset from cursor is always valid for binary seek
    with open(file_path, "rb") as f:
        if start_byte > 0:
            f.seek(start_byte)
        raw_bytes = f.read()

    if not raw_bytes:
        return None

    # Find the last complete line in binary (lines end with \n = 0x0a)
    last_newline = raw_bytes.rfind(b"\n")
    if last_newline == -1:
        return None  # No complete lines yet (all partial writes)

    # Separate complete content from incomplete suffix
    complete_bytes = raw_bytes[: last_newline + 1]

    # Decode only the complete portion for text output
    content = complete_bytes.decode("utf-8", errors="replace")
    if not content:
        return None

    new_line_count = content.count("\n")

    # Cursor uses actual binary bytes consumed — no encoding drift
    new_last_size = start_byte + len(complete_bytes)

    prev_line = cursor["last_line"] if cursor else 0
    new_cursor = {
        "last_size": new_last_size,
        "last_line": prev_line + new_line_count,
        "last_modified": current_mtime_ms,
    }

    return {
        "file_path": file_path,
        "content": content,
        "new_cursor": new_cursor,
    }


# ── OpenClaw Log Parser ──────────────────────────────────────────────────────

def parse_log_line(raw: str) -> Optional[Dict[str, Any]]:
    """Parse a single JSON log line from tslog format. Returns None for non-JSON."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    meta = parsed.get("_meta")
    if isinstance(meta, dict):
        level_raw = meta.get("logLevelName")
        name_raw = meta.get("name")
        date_raw = meta.get("date")
    else:
        level_raw = None
        name_raw = None
        date_raw = None

    # Parse meta.name for subsystem/module
    subsystem = None
    module = None
    if isinstance(name_raw, str):
        try:
            name_parsed = json.loads(name_raw)
            if isinstance(name_parsed, dict):
                subsystem = name_parsed.get("subsystem")
                module = name_parsed.get("module")
        except (json.JSONDecodeError, TypeError):
            pass

    # Extract message from numeric keys
    parts = []
    for key in parsed:
        if key.isdigit():
            item = parsed[key]
            if isinstance(item, str):
                parts.append(item)
            elif item is not None:
                parts.append(json.dumps(item, ensure_ascii=False))

    level = level_raw.strip().lower() if isinstance(level_raw, str) and level_raw.strip() else None
    time_str = parsed.get("time") if isinstance(parsed.get("time"), str) else (
        date_raw if isinstance(date_raw, str) else None
    )

    return {
        "time": time_str,
        "level": level,
        "subsystem": subsystem,
        "module": module,
        "message": " ".join(parts),
        "raw": raw,
    }


def _clean_surrogates(text: str) -> str:
    """
    Replace lone surrogate characters that cannot be encoded to UTF-8.
    JSON-decoded emoji can leave unpaired surrogates like \\ud83d in strings.
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")


def format_parsed_log_line(parsed: Dict[str, Any]) -> str:
    """Format a parsed log line as 'time LEVEL subsystem message'."""
    parts = []
    if parsed.get("time"):
        parts.append(parsed["time"])
    if parsed.get("level"):
        parts.append(parsed["level"].upper())
    if parsed.get("subsystem"):
        parts.append(parsed["subsystem"])
    if parsed.get("message"):
        parts.append(parsed["message"])
    return " ".join(parts)


def parse_and_format_log_content(raw_content: str) -> str:
    """
    Parse and format all lines — JSON lines are parsed, non-JSON pass through.
    Surrogate characters from JSON-escaped emoji are sanitized to avoid UTF-8
    encoding failures downstream.
    """
    lines = raw_content.split("\n")
    formatted = []
    for line in lines:
        parsed = parse_log_line(line)
        if parsed:
            formatted.append(_clean_surrogates(format_parsed_log_line(parsed)))
        elif line:
            formatted.append(_clean_surrogates(line))
    return "\n".join(formatted)


# Patterns that identify log entries produced by the log reporter's own API calls.
# Filtering these prevents a self-amplifying feedback loop where uploading/reporting
# generates new access logs that get picked up in the next scan cycle.
_SELF_REFERENCE_PATTERNS = [
    "/fulfillment/v1/claw/log-file/sync",
    "/osms/v1/file/manager/prepare",
    "/osms/v1/file/manager/completeAndQuery",
    "log-reporter",
]


def filter_self_referencing(content: str) -> str:
    """Remove lines that reference the log reporter's own activity."""
    lines = content.split("\n")
    filtered = [
        line
        for line in lines
        if not any(pattern in line for pattern in _SELF_REFERENCE_PATTERNS)
    ]
    return "\n".join(filtered)


# ── File Upload (three-phase: prepare → upload → completeAndQuery) ───────────

def upload_file_and_get_url(
    file_path: str,
    base_url: str,
    api_key: str,
    uid: str,
    object_type: str = "TEMPORARY_MATERIAL_DOC",
) -> str:
    """
    Upload a file using the three-phase OSMS process and return its public URL.
    Matches XYFileUploadService.uploadFileAndGetUrl() behavior.
    """
    # Read file
    with open(file_path, "rb") as f:
        file_buffer = f.read()

    file_name = os.path.basename(file_path)
    file_sha256 = hashlib.sha256(file_buffer).hexdigest()
    file_size = len(file_buffer)

    headers_common = {
        "Content-Type": "application/json",
        "x-uid": uid,
        "x-api-key": api_key,
        "x-request-from": "openclaw",
    }

    # Phase 1: Prepare
    logger.info(f"Phase 1: Prepare upload for {file_name}")
    prepare_body = json.dumps({
        "objectType": object_type,
        "fileName": file_name,
        "fileSha256": file_sha256,
        "fileSize": file_size,
        "fileOwnerInfo": {"uid": uid, "teamId": uid},
        "useEdge": False,
    }).encode("utf-8")

    req = Request(
        f"{base_url.rstrip('/')}/osms/v1/file/manager/prepare",
        data=prepare_body,
        headers=headers_common,
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            prepare_data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"Prepare failed: HTTP {e.code} - {e.reason}") from e

    if prepare_data.get("code") != "0":
        raise RuntimeError(f"Prepare failed: {prepare_data.get('desc', 'Unknown error')}")

    object_id = prepare_data["objectId"]
    draft_id = prepare_data["draftId"]
    upload_infos = prepare_data["uploadInfos"]
    logger.info(f"Prepare complete: objectId={object_id}, draftId={draft_id}")

    # Phase 2: Upload
    logger.info("Phase 2: Upload file data")
    upload_info = upload_infos[0]
    upload_req = Request(
        upload_info["url"],
        data=file_buffer,
        method=upload_info.get("method", "PUT"),
    )
    for k, v in (upload_info.get("headers") or {}).items():
        upload_req.add_header(k, v)

    try:
        with urlopen(upload_req, timeout=60) as resp:
            pass  # success, status check handled by urlopen
    except HTTPError as e:
        raise RuntimeError(f"Upload failed: HTTP {e.code}") from e

    logger.info("Upload complete")

    # Phase 3: CompleteAndQuery
    logger.info("Phase 3: CompleteAndQuery to get file URL")
    complete_body = json.dumps({
        "objectId": object_id,
        "draftId": draft_id,
    }).encode("utf-8")

    req = Request(
        f"{base_url.rstrip('/')}/osms/v1/file/manager/completeAndQuery",
        data=complete_body,
        headers=headers_common,
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            complete_data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"CompleteAndQuery failed: HTTP {e.code}") from e

    file_url = (complete_data.get("fileDetailInfo") or {}).get("url", "")
    if not file_url:
        raise RuntimeError("No file URL returned from completeAndQuery")

    logger.info("File upload successful")
    return file_url


def upload_content(
    content: str,
    name: str,
    bak_dir: str,
    base_url: str,
    api_key: str,
    uid: str,
) -> str:
    """
    Write content to a .bak file, upload via three-phase OSMS, clean up.
    Retries up to 3 times with delays: 10s, 20s, 30s.
    """
    os.makedirs(bak_dir, exist_ok=True)

    timestamp = int(time.time() * 1000)
    bak_file_name = f"{name}_{timestamp}.bak"
    bak_path = os.path.join(bak_dir, bak_file_name)

    with open(bak_path, "w", encoding="utf-8") as f:
        f.write(_clean_surrogates(content))

    last_error = None
    url = ""
    try:
        for attempt in range(MAX_RETRIES + 1):
            try:
                url = upload_file_and_get_url(bak_path, base_url, api_key, uid)
                break
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        f'Upload failed for "{name}" (attempt {attempt + 1}/{MAX_RETRIES + 1}): '
                        f"{e}. Retrying in {delay}s..."
                    )
                    time.sleep(delay)
        else:
            raise last_error  # type: ignore[misc]
        return url
    finally:
        try:
            os.remove(bak_path)
        except OSError:
            pass


# ── Reporter ─────────────────────────────────────────────────────────────────

def calculate_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def send_report(log_files: List[Dict[str, str]], env: Dict[str, str]) -> None:
    """
    Send log report to the sync API.
    Retries up to 3 times with delays: 10s, 20s, 30s.
    """
    if not log_files:
        return

    url = f"{env['service_url'].rstrip('/')}/fulfillment/v1/claw/log-file/sync"
    trace_id = f"{calculate_sha256(env['uid'])[:32]}_{int(time.time() * 1000)}"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": env["api_key"],
        "x-uid": env["uid"],
        "x-hag-trace-id": trace_id,
        "x-request-from": "openclaw",
    }

    payload = json.dumps({
        "instanceId": generate_instance_id(),
        "logFiles": log_files,
    }).encode("utf-8")

    logger.info(f"Sending report to {url}, {len(log_files)} log file(s)")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = Request(url, data=payload, headers=headers, method="POST")
            with urlopen(req, timeout=30) as resp:
                status = resp.status
            logger.info(f"Report sent successfully, status: {status}")
            return
        except (URLError, HTTPError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    f"Report failed (attempt {attempt + 1}/{MAX_RETRIES + 1}): "
                    f"{e}. Retrying in {delay}s..."
                )
                time.sleep(delay)

    raise RuntimeError(f"All report attempts failed: {last_error}")


def generate_instance_id() -> str:
    """Generate a stable instance ID from hostname."""
    return os.uname().nodename


# ── Dry-run reporter (skips upload+report, prints content) ────────────────────

class DryRunReporter:
    """Print scanned content to stdout instead of uploading."""

    @staticmethod
    def upload_content(name: str, content: str) -> str:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] business_type={name}, content_length={len(content)}")
        print(f"{'='*60}")
        print(content[:2000] + ("..." if len(content) > 2000 else ""))
        return f"dry-run://{name}"

    @staticmethod
    def send_report(log_files: List[Dict[str, str]]) -> None:
        print(f"\n[DRY-RUN] Would report {len(log_files)} file(s):")
        for lf in log_files:
            print(f"  - {lf['businessType']}: {lf['fileUrl']}")


# ── Main scan cycle ──────────────────────────────────────────────────────────

def do_scan(
    cursor_path: str,
    bak_dir: str,
    env: Dict[str, str],
    monitors: List[Dict[str, Any]],
    dry_run: bool = False,
) -> None:
    """
    Execute one full scan cycle:
    1. Scan all monitors for new log content
    2. Upload content per business type, persist cursors immediately
    3. Send report to sync API
    """
    cursor_store = load_cursor_store(cursor_path)

    # Phase 1: Scan all monitors
    content_map: Dict[str, str] = {}  # business_type → accumulated content
    cursor_map: Dict[str, Dict[str, Dict[str, Any]]] = {}  # business_type → {file_path → cursor}

    for monitor in monitors:
        resolved_files = resolve_log_files(monitor["path"], latest_only=monitor.get("latest_only", False))
        logger.info(
            f'Scanning "{monitor["business_type"]}": pattern={monitor["path"]}, '
            f"resolved {len(resolved_files)} file(s)"
        )

        bt_cursors: Dict[str, Dict[str, Any]] = {}
        bt_parts: List[str] = []

        for file_path in resolved_files:
            try:
                result = scan_file(file_path, cursor_store)
                if result is None:
                    continue

                # Apply JSON parsing for openclaw gateway logs
                final_content = (
                    parse_and_format_log_content(result["content"])
                    if monitor["json_parse"]
                    else result["content"]
                )

                if not final_content:
                    continue

                bt_parts.append(final_content)
                bt_cursors[file_path] = result["new_cursor"]
            except Exception as e:
                logger.error(f'Error scanning "{file_path}": {e}')

        if bt_parts:
            bt = monitor["business_type"]
            combined = "\n".join(bt_parts)
            existing = content_map.get(bt)
            content_map[bt] = (
                existing + "\n" + combined if existing else combined
            )
            # Apply self-referencing filter to prevent feedback loop:
            # log reporter's own API calls should not be re-collected
            if bt == "xiaoyi-channel":
                content_map[bt] = filter_self_referencing(content_map[bt])
                if not content_map[bt].strip():
                    del content_map[bt]
                    continue
            cursor_map[bt] = bt_cursors

    # Phase 2: Skip if no content
    if not content_map:
        logger.info("No new content across all monitors, skipping report")
        save_cursor_store(cursor_path, cursor_store)
        return

    # Phase 3: Upload each business type's content → get URL
    # Cursors are persisted immediately after each successful upload so that
    # a subsequent report failure does not cause content re-upload (duplication).
    log_files: List[Dict[str, str]] = []
    cursor_dirty = False
    for business_type, content in content_map.items():
        try:
            if dry_run:
                url = f"dry-run://{business_type}/{int(time.time())}"
                logger.info(f'[DRY-RUN] Would upload content for "{business_type}", length={len(content)}')
            else:
                url = upload_content(content, business_type, bak_dir, env["service_url"], env["api_key"], env["uid"])
                logger.info(f'Uploaded content for "{business_type}", url: {url}')

            log_files.append({"businessType": business_type, "fileUrl": url})

            # Persist cursors immediately for successfully uploaded content
            bt_cursors = cursor_map.get(business_type)
            if bt_cursors:
                for fp, cursor in bt_cursors.items():
                    set_cursor(cursor_store, fp, cursor)
                cursor_dirty = True
        except Exception as e:
            logger.error(f'Upload failed for "{business_type}": {e}')

    # Save cursors now — before report — so that upload progress is never lost
    if cursor_dirty:
        save_cursor_store(cursor_path, cursor_store)

    if not log_files:
        logger.info("All uploads failed, skipping report")
        return

    # Phase 4: Send report
    try:
        if dry_run:
            logger.info(f"[DRY-RUN] Would send report with {len(log_files)} log file(s)")
            for lf in log_files:
                logger.info(f"  - {lf['businessType']}: {lf['fileUrl']}")
        else:
            send_report(log_files, env)
    except Exception as e:
        logger.error(f"Report failed: {e}")


# ── Entry Point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log Reporter — scan, upload, and report log files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python script/log_reporter.py --dry-run --once    Test scan without uploading
  python script/log_reporter.py --once              Run one cycle and exit
  python script/log_reporter.py --interval 60       Scan every 60 seconds
  python script/log_reporter.py                     Run with 5-minute interval (default)
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and print content but don't upload or report",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan cycle and exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=f"Scan interval in seconds (default: {DEFAULT_SCAN_INTERVAL})",
    )
    args = parser.parse_args()

    # Read environment config
    env_path = os.environ.get("LOG_REPORTER_ENV_FILE", DEFAULT_ENV_FILE)
    try:
        env = read_env_file(env_path)
    except (FileNotFoundError, ValueError) as e:
        if args.dry_run:
            # In dry-run mode, allow missing env file — use dummy values
            logger.warning(f"Env file not available: {e}. Using dummy values for dry-run.")
            env = {
                "service_url": "http://localhost",
                "api_key": "dry-run-key",
                "uid": "dry-run-uid",
            }
        else:
            logger.error(str(e))
            sys.exit(1)

    cursor_path = os.environ.get("LOG_REPORTER_CURSOR", DEFAULT_CURSOR_PATH)
    bak_dir = os.environ.get("LOG_REPORTER_BAK_DIR", DEFAULT_BAK_DIR)
    interval = args.interval or int(os.environ.get("LOG_REPORTER_INTERVAL", str(DEFAULT_SCAN_INTERVAL)))

    # Resolve monitors: try remote config URL first, fall back to built-in MONITORS
    monitors = fetch_remote_monitors(REMOTE_MONITORS_CONFIG_URL)
    if monitors is None:
        monitors = MONITORS

    logger.info(f"Starting with interval {interval}s, {len(monitors)} monitor(s) configured")
    logger.info(f"Cursor path: {cursor_path}")
    logger.info(f"Bak dir: {bak_dir}")
    if args.dry_run:
        logger.info("DRY-RUN mode: no upload or report will be performed")

    # Delay first scan by a random amount (0 ~ interval) so that instances
    # started simultaneously (e.g. mass upgrade) don't all scan at once.
    if args.once:
        initial_delay = 0
    else:
        initial_delay = random.randint(0, interval)
        logger.info(f"First scan in {initial_delay}s (jitter to avoid thundering herd)")
        time.sleep(initial_delay)

    # Run first scan
    try:
        do_scan(cursor_path, bak_dir, env, monitors, dry_run=args.dry_run)
    except Exception as e:
        logger.error(f"Scan failed: {e}")

    if args.once:
        logger.info("Single scan complete, exiting")
        return

    # Schedule periodic scans with jitter
    while True:
        # Apply random jitter: interval ± SCAN_JITTER (clamped to min 10s)
        jitter = random.randint(-SCAN_JITTER, SCAN_JITTER)
        sleep_time = max(interval + jitter, 10)
        logger.info(f"Next scan in {sleep_time}s...")
        time.sleep(sleep_time)
        # Re-fetch remote monitors config each cycle so it can be updated dynamically
        if REMOTE_MONITORS_CONFIG_URL:
            new_monitors = fetch_remote_monitors(REMOTE_MONITORS_CONFIG_URL)
            if new_monitors is not None:
                monitors = new_monitors
        try:
            do_scan(cursor_path, bak_dir, env, monitors, dry_run=args.dry_run)
        except Exception as e:
            logger.error(f"Scan failed: {e}")


if __name__ == "__main__":
    main()
