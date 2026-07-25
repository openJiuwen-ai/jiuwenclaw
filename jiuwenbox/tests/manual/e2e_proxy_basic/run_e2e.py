#!/usr/bin/env python3
"""JiuwenBox Proxy Basic-auth E2E driver (P2).

Runs on the 205 host. Orchestrates:
  - a Basic-auth-enforcing upstream (Neo4j-HTTP-API-faithful stand-in; real
    Neo4j could not be pulled on 205 - see report),
  - a JiuwenBox server (editable source) with sandbox + proxy (Basic route via
    password_file),
  - a real JiuwenBox sandbox running a credential-free script through the proxy.

Scenarios:
  1. no Authorization -> proxy injects Basic -> 200, RETURN 1 AS value == 1
  2. wrong Bearer -> overwritten -> 200
  3. wrong Basic -> overwritten -> 200
  4. wrong proxy password_file -> upstream 401
Then proves the real password / full Basic base64 does not appear in: proxy
list/detail, proxy logs, sandbox audit, or any process argv.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

API = os.environ.get("E2E_API", "http://127.0.0.1:18341")
PROXY_HOST = os.environ.get("E2E_PROXY_HOST", "7.221.52.205")
PROXY_PORT = int(os.environ.get("E2E_PROXY_PORT", "18342"))
UPSTREAM_PORT = int(os.environ.get("E2E_UPSTREAM_PORT", "17474"))
USERNAME = "neo4j"
PASSWORD = "e2e-real-pw-9f3a7c2b"  # test-only; never a real env credential
WRONG_PASSWORD = "e2e-wrong-pw-0000000"
PW_FILE = "/root/basic-proxy-verify/e2e/neo4j_password"
PW_FILE_BAD = "/root/basic-proxy-verify/e2e/neo4j_password_bad"
UPSTREAM_LOG = "/root/basic-proxy-verify/e2e/upstream.log"
QUERY_PATH = "/neo4j/db/neo4j/query/v2"
QUERY_BAD_PATH = "/neo4jbad/db/neo4j/query/v2"

REPORT: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    REPORT.append(msg)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    sys.exit(1)


def ensure_dirs() -> None:
    Path("/root/basic-proxy-verify/e2e").mkdir(parents=True, exist_ok=True)


def write_password_files() -> None:
    for path, pw in ((PW_FILE, PASSWORD), (PW_FILE_BAD, WRONG_PASSWORD)):
        p = Path(path)
        p.write_text(pw + "\n")
        os.chmod(p, 0o600)
    log(f"[setup] password files written (0600): {PW_FILE}, {PW_FILE_BAD}")


def start_upstream() -> subprocess.Popen:
    env = os.environ.copy()
    env["E2E_UPSTREAM_USERNAME"] = USERNAME
    env["E2E_UPSTREAM_PASSWORD"] = PASSWORD
    env["E2E_UPSTREAM_PORT"] = str(UPSTREAM_PORT)
    logf = open(UPSTREAM_LOG, "w")
    proc = subprocess.Popen(
        [sys.executable, "/root/basic-proxy-verify/e2e/upstream_basic.py"],
        env=env, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    if proc.poll() is not None:
        fail("upstream exited early")
    log(f"[setup] upstream listening on 127.0.0.1:{UPSTREAM_PORT}")
    return proc


def api(client: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    r = client.request(method, path, timeout=30.0, **kw)
    return r


def create_routes(client: httpx.Client) -> None:
    # /neo4j -> correct password_file
    r = api(client, "POST", "/api/v1/proxies", json={
        "path_prefix": "/neo4j",
        "target_endpoint": f"http://127.0.0.1:{UPSTREAM_PORT}",
        "basic_auth": {"username": USERNAME, "password_file": PW_FILE},
    })
    if r.status_code != 201:
        fail(f"create /neo4j route failed: {r.status_code} {r.text}")
    r = api(client, "POST", "/api/v1/proxies/neo4j/start")
    if r.status_code != 200:
        fail(f"start /neo4j failed: {r.status_code} {r.text}")
    # /neo4jbad -> WRONG password_file (exercises wrong-creds scenario)
    r = api(client, "POST", "/api/v1/proxies", json={
        "path_prefix": "/neo4jbad",
        "target_endpoint": f"http://127.0.0.1:{UPSTREAM_PORT}",
        "basic_auth": {"username": USERNAME, "password_file": PW_FILE_BAD},
    })
    if r.status_code != 201:
        fail(f"create /neo4jbad route failed: {r.status_code} {r.text}")
    r = api(client, "POST", "/api/v1/proxies/neo4jbad/start")
    if r.status_code != 200:
        fail(f"start /neo4jbad failed: {r.status_code} {r.text}")
    log("[setup] proxy routes /neo4j (correct) and /neo4jbad (wrong) created+started")


def sandbox_exec(client: httpx.Client, sandbox_id: str, script: str) -> tuple[int, str, str]:
    """Run a shell script inside the sandbox; script must contain NO creds."""
    r = api(client, "POST", f"/api/v1/sandboxes/{sandbox_id}/exec", json={
        "command": ["sh", "-c", script],
        "timeout_seconds": 30,
    })
    if r.status_code != 200:
        return -1, "", f"exec http {r.status_code}: {r.text}"
    d = r.json()
    return d.get("exit_code", -1), d.get("stdout", ""), d.get("stderr", "")


def curl_in_sandbox(path: str, extra_headers: str = "") -> str:
    """Credential-free curl from sandbox through the proxy. --noproxy bypasses
    the 205 corporate http_proxy env so the host proxy is reached directly."""
    hdrs = f"-H 'Content-Type: application/json' {extra_headers}".strip()
    return (
        f"curl -s --noproxy '*' --max-time 10 -X POST "
        f"'http://{PROXY_HOST}:{PROXY_PORT}{path}' {hdrs} "
        f"-d '{{\"statement\":\"RETURN 1 AS value\"}}' -w '\\nHTTP=%{{http_code}}\\n'"
    )


def main() -> None:
    ensure_dirs()
    write_password_files()
    upstream = start_upstream()
    sandbox_id = None
    client = httpx.Client(base_url=API, timeout=30.0)
    try:
        # health
        if api(client, "GET", "/health").status_code != 200:
            fail("E2E jiuwenbox server not healthy")
        create_routes(client)

        # create sandbox
        r = api(client, "POST", "/api/v1/sandboxes", json={})
        if r.status_code != 201:
            fail(f"create sandbox failed: {r.status_code} {r.text}")
        sandbox_id = r.json()["id"]
        for _ in range(20):
            st = api(client, "GET", f"/api/v1/sandboxes/{sandbox_id}").json()
            if st.get("phase") == "ready":
                break
            time.sleep(0.5)
        log(f"[sandbox] created {sandbox_id}, phase={st.get('phase')}")

        # Scenario 1: no Authorization -> 200
        code, out, err = sandbox_exec(client, sandbox_id, curl_in_sandbox(QUERY_PATH))
        log(f"[scenario 1] no-auth -> exit={code} out={out!r}")
        if "HTTP=200" not in out or '"value"' not in out or "[1]" not in out:
            fail(f"scenario 1 expected 200 with value=1, got: {out!r} err={err!r}")

        # Scenario 2: wrong Bearer -> overwritten -> 200
        code, out, err = sandbox_exec(
            client, sandbox_id,
            curl_in_sandbox(QUERY_PATH, "-H 'Authorization: Bearer attacker-token-xyz'"),
        )
        log(f"[scenario 2] wrong-bearer -> exit={code} out={out!r}")
        if "HTTP=200" not in out:
            fail(f"scenario 2 expected 200 (Bearer overwritten), got: {out!r}")

        # Scenario 3: wrong Basic -> overwritten -> 200
        fake = base64.b64encode(b"attacker:bad").decode()
        code, out, err = sandbox_exec(
            client, sandbox_id,
            curl_in_sandbox(QUERY_PATH, f"-H 'Authorization: Basic {fake}'"),
        )
        log(f"[scenario 3] wrong-basic -> exit={code} out={out!r}")
        if "HTTP=200" not in out:
            fail(f"scenario 3 expected 200 (Basic overwritten), got: {out!r}")

        # Scenario 4: wrong proxy password_file -> upstream 401
        code, out, err = sandbox_exec(client, sandbox_id, curl_in_sandbox(QUERY_BAD_PATH))
        log(f"[scenario 4] wrong-proxy-pw -> exit={code} out={out!r}")
        if "HTTP=401" not in out:
            fail(f"scenario 4 expected 401 (wrong creds rejected), got: {out!r}")

        # --- no-secret evidence ---
        leaks: list[str] = []
        full_basic = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

        def check(label: str, text: str) -> None:
            if PASSWORD in text:
                leaks.append(f"{label}: contains plaintext PASSWORD")
            if full_basic in text:
                leaks.append(f"{label}: contains full Basic base64")

        listing = api(client, "GET", "/api/v1/proxies").json()
        detail = api(client, "GET", "/api/v1/proxies/neo4j").json()
        check("proxy list", json.dumps(listing, ensure_ascii=False))
        check("proxy detail", json.dumps(detail, ensure_ascii=False))
        log(f"[redact] proxy detail basic_auth={detail['route']['basic_auth']}")
        if "password" in detail["route"]["basic_auth"]:
            leaks.append("proxy detail basic_auth contains 'password' key")

        logs_r = api(client, "GET", "/api/v1/proxies/neo4j/logs")
        check("proxy logs", logs_r.text)

        # sandbox audit log
        audit_r = api(client, "GET", f"/api/v1/sandboxes/{sandbox_id}/logs")
        check("sandbox audit", audit_r.text)

        # process argv sweep (the password must not be in any process cmdline)
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        check("process argv (ps)", ps)
        # the wrong password file path appears (that's a path, not the secret);
        # only the secret value matters.

        log(f"[redact] full Basic base64 that must NOT leak: {full_basic}")
        if leaks:
            fail("secret leakage: " + "; ".join(leaks))
        log("[redact] PASS: no plaintext password / full Basic base64 in list, detail, logs, audit, or ps")

        # prove the sandbox script itself has no creds: the curl command we ran
        # contains no Authorization (scenario 1) and no password.
        s1_script = curl_in_sandbox(QUERY_PATH)
        if PASSWORD in s1_script or "Authorization" in s1_script:
            fail("scenario 1 sandbox script contains creds")
        log("[redact] PASS: scenario-1 sandbox script contains no password / no Authorization header")

        log("\n=== E2E RESULT: PASS ===")
    finally:
        # cleanup
        if sandbox_id:
            try:
                client.delete(f"/api/v1/sandboxes/{sandbox_id}", timeout=10.0)
                log(f"[cleanup] deleted sandbox {sandbox_id}")
            except Exception as e:
                log(f"[cleanup] sandbox delete error: {e}")
        for name in ("neo4jbad", "neo4j"):
            try:
                client.post(f"/api/v1/proxies/{name}/stop", timeout=10.0)
                client.delete(f"/api/v1/proxies/{name}", timeout=10.0)
                log(f"[cleanup] deleted route {name}")
            except Exception as e:
                log(f"[cleanup] route {name} delete error: {e}")
        client.close()
        upstream.terminate()
        try:
            upstream.wait(timeout=5)
        except subprocess.TimeoutExpired:
            upstream.kill()
        log("[cleanup] upstream stopped")
        for p in (PW_FILE, PW_FILE_BAD):
            try:
                Path(p).unlink()
                log(f"[cleanup] removed {p}")
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
