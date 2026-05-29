import json
import os
import sys
import time
import warnings
from datetime import datetime

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


def generate_req_time():
    """Generate the millisecond timestamp required by the scan request."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def extract_task_id(response_data):
    """Extract the taskID from the initial skill_scan response."""
    if response_data.get("code") != "0":
        return None
    return response_data.get("data", {}).get("data", {}).get("taskID")


def execute_skill_scan(skill_name, url, sandbox_id="rytest", trace_id="rytest001"):
    """Submit a safety scan task and return the HTTP response plus taskID."""
    requests_module = get_requests()
    headers = {
        **COMMON_HEADERS,
        "x-skill-id": "skill_scan",
        "x-hag-trace-id": trace_id,
        "x-sandbox-id": sandbox_id,
    }

    payload = {
        "skillName": skill_name,
        "skillID": "skill_scan",
        "action": "scan",
        "url": url,
        "reqTime": generate_req_time(),
    }

    try:
        response = requests_module.post(
            get_skill_scan_url(),
            headers=headers,
            json=payload,
            timeout=30,
            verify=False,
        )
        response.raise_for_status()
        return response, extract_task_id(response.json())
    except requests_module.exceptions.RequestException as e:
        if hasattr(e, "response") and e.response is not None:
            raise RuntimeError(
                f"skill_scan request failed: status={e.response.status_code}, body={e.response.text}"
            ) from e
        raise


def execute_skill_scan_query_task(task_id, sandbox_id="rytest", trace_id="rytest001"):
    """Query the safety scan task status by taskID."""
    requests_module = get_requests()
    headers = {
        **COMMON_HEADERS,
        "x-skill-id": "skill_scan_query_task",
        "x-hag-trace-id": trace_id,
        "x-sandbox-id": sandbox_id,
    }

    payload = {"taskID": task_id}

    try:
        response = requests_module.post(
            get_skill_scan_url(),
            headers=headers,
            json=payload,
            timeout=30,
            verify=False,
        )
        response.raise_for_status()
        return response
    except requests_module.exceptions.RequestException as e:
        if hasattr(e, "response") and e.response is not None:
            raise RuntimeError(
                "skill_scan_query_task request failed: "
                f"status={e.response.status_code}, body={e.response.text}"
            ) from e
        raise


def poll_task_status(task_id, max_attempts=10, interval=2):
    """Poll the task status until completion or the retry limit is reached."""
    last_response_data = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = execute_skill_scan_query_task(task_id)
            response_data = response.json()
            last_response_data = response_data

            if response_data.get("code") == "0":
                inner_data = response_data.get("data", {}).get("result", [])
                if inner_data:
                    result = inner_data[0]
                    if result.get("reportStatus") == "COMPLETED":
                        return response_data

            if attempt < max_attempts:
                time.sleep(interval)
        except Exception:
            if attempt < max_attempts:
                time.sleep(interval)
                continue
            raise

    return {
        "error": "Safety scan did not complete before polling timed out.",
        "maxAttempts": max_attempts,
        "intervalSeconds": interval,
        "last_response": last_response_data,
    }


def extract_scan_result(raw_result):
    """Find the result object containing conclusion, modelResult, and items."""
    if not raw_result or not isinstance(raw_result, dict):
        return None

    if any(key in raw_result for key in ("conclusion", "modelResult", "items")):
        return raw_result

    inner_data = raw_result.get("data", {}).get("result", [])
    if inner_data:
        return inner_data[0]

    return None


def build_scan_result(raw_result):
    """Build the public scan summary from the raw scan response."""
    scan_result = extract_scan_result(raw_result)
    if not scan_result:
        return None

    return {
        "conclusion": scan_result.get("conclusion"),
        "modelResult": scan_result.get("modelResult"),
        "items": scan_result.get("items", []),
    }


def format_result(raw_result):
    """Format a raw result as text suitable for stdout."""
    if isinstance(raw_result, str):
        return raw_result
    return json.dumps(raw_result, indent=2, ensure_ascii=False)


def format_field_value(value):
    """Format a field value, preserving strings and JSON-encoding structures."""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def format_failed_scan_result(raw_result):
    """Format a non-BENIGN scan result with the required key fields."""
    result = build_scan_result(raw_result)
    if not result:
        return None

    return "\n".join(
        [
            f"conclusion：{format_field_value(result.get('conclusion'))}",
            f"modelResult：{format_field_value(result.get('modelResult'))}",
            f"items：{format_field_value(result.get('items'))}",
        ]
    )


def format_timeout_result(raw_result):
    """Format a polling timeout with retry details and the last response."""
    if not isinstance(raw_result, dict) or "last_response" not in raw_result:
        return None

    return "\n".join(
        [
            f"reason：{raw_result.get('error')}",
            f"maxAttempts：{raw_result.get('maxAttempts')}",
            f"intervalSeconds：{raw_result.get('intervalSeconds')}",
            f"lastResponse：{format_field_value(raw_result.get('last_response'))}",
        ]
    )


def format_failure_message(raw_result):
    """Build the normalized failure message for the result type."""
    return (
        format_timeout_result(raw_result)
        or format_failed_scan_result(raw_result)
        or format_result(raw_result)
    )


def evaluate_scan_result(raw_result):
    """Return True only when the scan conclusion is BENIGN."""
    result = build_scan_result(raw_result)
    if not result:
        return False

    missing_fields = [key for key, value in result.items() if value is None]
    if missing_fields:
        return False

    return str(result["conclusion"]).upper() == "BENIGN"


def print_scan_result(raw_result):
    """Print the required scan verdict and return the process exit code."""
    if evaluate_scan_result(raw_result):
        print("Safety scan passed.")
        return 0

    print("Safety scan failed:")
    print(format_failure_message(raw_result))
    return 1


def scan_url(skill_name, url):
    """Run the full scan flow and return the raw scan output."""
    try:
        response, task_id = execute_skill_scan(skill_name=skill_name, url=url)
        response_data = response.json()

        if not task_id:
            return response_data

        return poll_task_status(task_id)
    except Exception as e:
        return {"error": str(e)}


def main(argv):
    """Parse positional arguments and run the safety scan command."""
    if len(argv) != 3:
        print("Safety scan failed:")
        print("Usage: python3 -m scripts.safety_scan <skill-name> <url>")
        return 2

    raw_result = scan_url(skill_name=argv[1], url=argv[2])
    return print_scan_result(raw_result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
