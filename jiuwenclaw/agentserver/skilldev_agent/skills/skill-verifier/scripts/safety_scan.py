import json
import os
import sys
import time
import warnings
from datetime import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None

try:
    from urllib3.exceptions import InsecureRequestWarning
except ImportError:
    InsecureRequestWarning = None

if InsecureRequestWarning is not None:
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)

SKILL_SCAN_URL = os.getenv("SKILL_SCAN_URL")

COMMON_HEADERS = {
    "x-api-key": "apikey",
    "x-request-from": "jiuwenclaw",
    "Content-Type": "application/json",
}
# 沙箱id标识和OBS认证的apikey
_SANDBOX_ID = None
_API_KEY = None


def get_sandbox_init_data():
    """
    获取沙箱创建时 gw 持久化的 apikey 和 sandboxId
    """
    global _SANDBOX_ID, _API_KEY
    if _SANDBOX_ID and _API_KEY:
        return
    init_path: str = os.getenv("SANDBOX_INIT_DATA_PATH", "").strip()
    if not os.path.exists(init_path):
        logger.warning("[SandboxInitDataPath] 沙箱中不存在初始化数据路径：%s", init_path)
        return
    try:
        with open(init_path, "r", encoding="utf-8") as file:
            init_data = json.load(file)
            _SANDBOX_ID = init_data.get("sandboxId")
            _API_KEY = init_data.get("apiKey")
    except Exception as e:
        logger.error("[SandboxInitData] 初始化数据获取失败：%s", e)


def get_api_key():
    get_sandbox_init_data()
    return _API_KEY


def get_sandbox_id():
    get_sandbox_init_data()
    return _SANDBOX_ID


def get_oa_auth_headers(
    retry_interval: float = 3.0, session_id: str = ""
) -> dict[str, str]:
    """从环境变量获取 OA 鉴权 headers，无限轮询直到获取全。

    注意：此函数会阻塞直到获取到所有必需的鉴权信息。

    支持的配置：
    - x-api-key
    - x-sandbox-id
    - x-request-from
    - x-hag-trace-id

    Args:
        retry_interval: 重试间隔秒数，默认3秒
        session_id: 默认 sandbox_id

    Returns:
        包含鉴权信息的 headers 字典
    """
    headers: dict[str, str] = {}
    attempt = 0

    while True:
        api_key = get_api_key()
        sandbox_id = get_sandbox_id()

        if api_key and sandbox_id:
            # 获取到所有必需的 headers
            headers["x-api-key"] = api_key
            headers["x-sandbox-id"] = sandbox_id
            headers["x-request-from"] = "jiuwenclaw"
            headers["x-hag-trace-id"] = session_id if session_id else sandbox_id
            if attempt > 0:
                logger.info(
                    "[AgentWebSocketServer] 成功获取 OpenAbility 鉴权 headers (等待 %d 秒)",
                    attempt * retry_interval
                )
            return headers

        # 记录缺少的配置
        missing = []
        if not api_key:
            missing.append("x-api-key")
        if not sandbox_id:
            missing.append("x-sandbox-id")

        if attempt == 0:
            logger.warning(
                "[AgentWebSocketServer] OpenAbility 鉴权信息不完整，开始轮询等待: %s",
                ", ".join(missing)
            )
        else:
            # 每10秒记录一次日志
            if attempt % 10 == 0:
                logger.info(
                    "[AgentWebSocketServer] 等待 OpenAbility 鉴权信息中 (%d 秒): %s",
                    attempt * retry_interval,
                    ", ".join(missing)
                )

        # 等待后重试
        time.sleep(retry_interval)
        attempt += 1


def get_skill_scan_url():
    """Return the safety scan service URL, or fail if it is not configured."""
    if not SKILL_SCAN_URL:
        raise RuntimeError("SKILL_SCAN_URL environment variable is not set")
    return SKILL_SCAN_URL


def get_requests():
    """Return the requests module, delaying dependency errors until runtime."""
    if requests is None:
        raise RuntimeError("requests package is not installed")
    return requests


def _post_request(skill_id, payload):
    req = get_requests()
    auth_headers = get_oa_auth_headers()
    headers = {
        **COMMON_HEADERS,
        "x-skill-id": skill_id,
    }
    headers.update(auth_headers)

    try:
        resp = req.post(
            get_skill_scan_url(),
            headers=headers,
            json=payload,
            timeout=30,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json()
    except req.exceptions.RequestException as e:
        if hasattr(e, "response") and e.response is not None:
            raise RuntimeError(
                f"{skill_id} request failed: "
                f"status={e.response.status_code}, body={e.response.text}"
            ) from e
        raise


def _get_conclusion(raw_result):
    if not isinstance(raw_result, dict):
        return None
    if "conclusion" in raw_result:
        return raw_result.get("conclusion")
    results = raw_result.get("data", {}).get("result", [])
    if results:
        return results[0].get("conclusion")
    return None


def _format_failure(raw_result):
    if not isinstance(raw_result, dict):
        return json.dumps(raw_result, indent=2, ensure_ascii=False)

    if "error" in raw_result and "last_response" in raw_result:
        last_resp = raw_result.get("last_response")
        last_resp_str = (
            last_resp
            if isinstance(last_resp, str)
            else json.dumps(last_resp, indent=2, ensure_ascii=False)
        )
        return "\n".join(
            [
                f"reason：{raw_result.get('error')}",
                f"maxAttempts：{raw_result.get('maxAttempts')}",
                f"intervalSeconds：{raw_result.get('intervalSeconds')}",
                f"lastResponse：{last_resp_str}",
            ]
        )

    scan = None
    if any(k in raw_result for k in ("conclusion", "modelResult", "items")):
        scan = raw_result
    else:
        inner = raw_result.get("data", {}).get("result", [])
        if inner:
            scan = inner[0]

    if scan:
        def _fmt(v):
            return v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False)

        return "\n".join(
            [
                f"conclusion：{_fmt(scan.get('conclusion'))}",
                f"modelResult：{_fmt(scan.get('modelResult'))}",
                f"items：{_fmt(scan.get('items'))}",
            ]
        )

    return json.dumps(raw_result, indent=2, ensure_ascii=False)


def scan_url(skill_name, url, max_attempts=20, interval=2):
    """Submit scan task, poll for status, return result dict."""
    req_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    try:
        submit_data = _post_request(
            "skill_scan",
            {
                "skillName": skill_name,
                "skillID": "skill_scan",
                "action": "scan",
                "url": url,
                "reqTime": req_time,
            },
        )
    except Exception as e:
        return {"error": str(e)}

    if submit_data.get("code") != "0":
        return submit_data

    task_id = submit_data.get("data", {}).get("data", {}).get("taskID")
    if not task_id:
        return submit_data

    last_data = None
    for attempt in range(1, max_attempts + 1):
        try:
            last_data = _post_request("skill_scan_query_task", {"taskID": task_id})
        except Exception:
            if attempt >= max_attempts:
                raise
            time.sleep(interval)
            continue

        results = last_data.get("data", {}).get("result", [])
        if results and results[0].get("reportStatus") == "COMPLETED":
            return last_data

        if attempt < max_attempts:
            time.sleep(interval)

    return {
        "error": "Safety scan did not complete before polling timed out.",
        "maxAttempts": max_attempts,
        "intervalSeconds": interval,
        "last_response": last_data,
    }


def main(argv):
    if len(argv) != 3:
        print("Safety scan failed:")
        print("Usage: python3 -m scripts.safety_scan <skill-name> <url>")
        return 2

    raw_result = scan_url(skill_name=argv[1], url=argv[2])
    conclusion = _get_conclusion(raw_result)

    if str(conclusion).upper() == "BENIGN":
        print("Safety scan passed.")
        return 0

    print("Safety scan failed:")
    print(_format_failure(raw_result))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
