#!/usr/bin/env bash
# Stop the brainstorm server and clean up
# Usage: stop-server.sh <session_dir>
#
# Kills the server process. Only deletes session directory if it's
# under /tmp (ephemeral). Persistent directories (.brainstorm/) are
# kept so mockups can be reviewed later.

SESSION_DIR="$1"

if [[ -z "$SESSION_DIR" ]]; then
  echo '{"error": "Usage: stop-server.sh <session_dir>"}'
  exit 1
fi

STATE_DIR="${SESSION_DIR}/state"
PID_FILE="${STATE_DIR}/server.pid"

resolve_active_file() {
  if [[ "$SESSION_DIR" == */.brainstorm/* ]]; then
    echo "${SESSION_DIR%%/.brainstorm/*}/.brainstorm/active.json"
  elif [[ "$SESSION_DIR" == /tmp/brainstorm-* ]]; then
    echo "/tmp/brainstorm-active.json"
  fi
}

clear_active_file() {
  local active_file
  active_file="$(resolve_active_file)"
  [[ -n "$active_file" && -f "$active_file" ]] || return 0
  node -e "
    const fs = require('fs');
    try {
      const current = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
      if (current.session_dir === process.argv[2]) fs.unlinkSync(process.argv[1]);
    } catch (e) {
      fs.unlinkSync(process.argv[1]);
    }
  " "$active_file" "$SESSION_DIR"
}

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"

  kill "$pid" 2>/dev/null || true

  for i in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done

  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 0.1
  fi

  if kill -0 "$pid" 2>/dev/null; then
    echo '{"status": "failed", "error": "process still running"}'
    exit 1
  fi

  rm -f "$PID_FILE" "${STATE_DIR}/server.log"
  clear_active_file

  if [[ "$SESSION_DIR" == /tmp/* ]]; then
    rm -rf "$SESSION_DIR"
  fi

  echo '{"status": "stopped"}'
else
  echo '{"status": "not_running"}'
fi
