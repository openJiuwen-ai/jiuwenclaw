#!/bin/bash
set -euo pipefail

API_BASE="http://localhost:19002"
WS_BASE="ws://127.0.0.1:19003"

echo "========== 0. 预检查 =========="
if ! curl -sS --max-time 2 "$API_BASE/api/v1/session" -o /dev/null; then
  echo "ERROR: 无法访问 $API_BASE ，需要先启动 HTTP 服务"
  exit 1
fi
if ! nc -z 127.0.0.1 19003 >/dev/null 2>&1; then
  echo "ERROR: 127.0.0.1:19003 未监听，请先启动 WebSocket 服务"
  exit 1
fi

echo "========== 1. 创建 Session =========="
HTTP_STATUS=$(curl -sS -o /tmp/vibeskill_session_resp.json -w "%{http_code}" \
  -X POST "$API_BASE/api/v1/session" \
  -H "Content-Type: application/json" \
  -d '{}')
RESPONSE=$(cat /tmp/vibeskill_session_resp.json)
echo "HTTP: $HTTP_STATUS"
echo "Response: $RESPONSE"

if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "201" ]]; then
  echo "ERROR: 创建 session 失败"
  exit 1
fi

SESSION_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin)['sessionID'])" <<< "$RESPONSE" 2>/dev/null || true)
if [[ -z "${SESSION_ID:-}" ]]; then
  echo "ERROR: 返回体里没有 sessionID，无法继续"
  exit 1
fi
echo "Session ID: $SESSION_ID"

echo ""
echo "========== 2. WebSocket message.send 测试 =========="
echo "运行: uv run python scripts/vibeskill/test_ws_whole.py $SESSION_ID"
uv run python scripts/vibeskill/test_ws_whole.py $SESSION_ID

echo ""
echo "========== 3. skill导出 =========="
curl -sS -X POST "$API_BASE/api/v1/session/$SESSION_ID/export" \
  -H "Content-Type: application/json" \
  -d '{}'
echo ""

echo ""
echo "========== 4. 列出文件和读取文件 =========="
FILE_LIST_HTTP_STATUS=$(curl -sS -o /tmp/vibeskill_file_list_resp.json -w "%{http_code}" \
  -X GET "$API_BASE/api/v1/session/$SESSION_ID/file")
FILE_LIST_RESPONSE=$(cat /tmp/vibeskill_file_list_resp.json)
echo "file list HTTP: $FILE_LIST_HTTP_STATUS"
echo "file list response: $FILE_LIST_RESPONSE"

if [[ "$FILE_LIST_HTTP_STATUS" != "200" ]]; then
  echo "ERROR: 列出文件失败"
  exit 1
fi

FIRST_FILE_PATH=$(python3 -c "
import json, sys
def first_file_path(nodes):
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        if n.get('type') == 'file' and n.get('path'):
            return n['path']
        got = first_file_path(n.get('children') or [])
        if got:
            return got
    return ''
data = json.loads(sys.stdin.read())
roots = data if isinstance(data, list) else (data.get('tree') or [])
print(first_file_path(roots))
" <<< "$FILE_LIST_RESPONSE" 2>/dev/null || true)
if [[ -z "${FIRST_FILE_PATH:-}" ]]; then
  echo "ERROR: 未找到可读取的文件 path"
  exit 1
fi

echo "读取文件: $FIRST_FILE_PATH"
FILE_READ_HTTP_STATUS=$(curl -sS -o /tmp/vibeskill_file_read_resp.json -w "%{http_code}" \
  -G "$API_BASE/api/v1/session/$SESSION_ID/file/content" \
  --data-urlencode "path=$FIRST_FILE_PATH")
FILE_READ_RESPONSE=$(cat /tmp/vibeskill_file_read_resp.json)
echo "file read HTTP: $FILE_READ_HTTP_STATUS"
echo "file read response: $FILE_READ_RESPONSE"

if [[ "$FILE_READ_HTTP_STATUS" != "200" ]]; then
  echo "ERROR: 读取文件失败"
  exit 1
fi

echo ""
echo ""
echo "========== 测试完成 =========="
