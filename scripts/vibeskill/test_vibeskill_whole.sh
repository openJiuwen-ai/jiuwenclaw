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
echo "========== 测试完成 =========="
