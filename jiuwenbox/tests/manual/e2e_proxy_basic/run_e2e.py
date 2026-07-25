#!/usr/bin/env python3
"""JiuwenBox Proxy Basic-auth E2E driver (P2 / P2-R).

Runs on the 205 host. Orchestrates a JiuwenBox server (editable source) with
sandbox + proxy (Basic route via password_file) and a real JiuwenBox sandbox
running a credential-free script through the proxy.

Two upstream modes (E2E_UPSTREAM env):
  - "real"    : a real Neo4j 2026.06.0 instance on 127.0.0.1:17474 (started
                outside this driver; the real password lives only in a 0600
                file pointed to by E2E_PASSWORD_FILE). This is the P2-R
                verification path.
  - "standin" : the bundled upstream_basic.py (faithfully emulates the Neo4j
                /db/{db}/tx/commit endpoint) for offline regression where real
                Neo4j / Java 21 is unavailable. The driver writes the 0600
                password files itself and starts the stand-in.

Scenarios (identical in both modes):
  1. no Authorization -> proxy injects Basic -> 200, RETURN 1 AS value == 1
  2. wrong Bearer -> overwritten -> 200
  3. wrong Basic -> overwritten -> 200
  4. wrong proxy password_file -> upstream 401
Then proves the real password / full Basic base64 does not appear in: proxy
list/detail, proxy logs, sandbox audit, or any process argv.

Real Neo4j 2026.06.0 HTTP interface used:
    POST /db/neo4j/tx/commit
    body : {"statements":[{"statement":"RETURN 1 AS value"}]}
    200  : {"results":[{"columns":["value"],
                        "data":[{"row":[1],"meta":[null]}]}], ...,"errors":[]}
    401  : {"errors":[{"code":"Neo.ClientError.Security.Unauthorized", ...}]}
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
PROXY_HOST = os.environ.get("E2E_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("E2E_PROXY_PORT", "18342"))
UPSTREAM_PORT = int(os.environ.get("E2E_UPSTREAM_PORT", "17474"))
UPSTREAM_MODE = os.environ.get("E2E_UPSTREAM", "real")
USERNAME = os.environ.get("E2E_USERNAME", "neo4j")
# standin-only test fixture (never a real env credential); real mode reads the
# password from E2E_PASSWORD_FILE instead.
STANDIN_PASSWORD = "e2e-real-pw-9f3a7c2b"
WRONG_PASSWORD = "e2e-wrong-pw-0000000"

WORKDIR = Path(os.environ.get("E2E_WORKDIR", "/bke/neo4j-basic-verify/e2e"))
# In real mode the correct-password file is the external 0600 secret created by
# the Neo4j setup (E2E_PASSWORD_FILE); in standin mode it defaults to a workdir
# path the driver writes itself.
PW_FILE = os.environ.get("E2E_PASSWORD_FILE", str(WORKDIR / "neo4j_password"))
PW_FILE_BAD = str(WORKDIR / "neo4j_password_bad")
UPSTREAM_LOG = str(WORKDIR / "upstream.log")
HERE = Path(__file__).resolve().parent

# Real Neo4j tx/commit endpoint. The proxy path_prefix /neo4j is stripped, so
# /neo4j/db/neo4j/tx/commit -> http://127.0.0.1:17474/db/neo4j/tx/commit.
QUERY_PATH = "/neo4j/db/neo4j/tx/commit"
QUERY_BAD_PATH = "/neo4jbad/db/neo4j/tx/commit"
QUERY_BODY = '{"statements":[{"statement":"RETURN 1 AS value"}]}'

REPORT: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    REPORT.append(msg)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    sys.exit(1)


def resolve_password() -> str:
    """Return the real password. Real mode reads it from the 0600 file; standin
    mode uses the test fixture (and writes that file itself)."""
    if UPSTREAM_MODE == "real":
        f = os.environ.get("E2E_PASSWORD_FILE", PW_FILE)
        try:
            return Path(f).read_text().strip()
        except OSError as e:
            fail(f"real mode: cannot read password file {f}: {e}")
    return STANDIN_PASSWORD


def ensure_dirs() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)


def write_password_files(password: str) -> None:
    """Write the 0600 secret files used by the proxy routes.

    In real mode the correct-password file already exists (created by the
    Neo4j setup); we only (re)write the bad file. In standin mode we write both
    (the stand-in uses the same fixture password)."""
    if UPSTREAM_MODE != "real":
        p = Path(PW_FILE)
        p.write_text(password + "\n")
        os.chmod(p, 0o600)
    pbad = Path(PW_FILE_BAD)
    pbad.write_text(WRONG_PASSWORD + "\n")
    os.chmod(pbad, 0o600)
    log(f"[setup] password files (0600): correct={PW_FILE} bad={PW_FILE_BAD}")


def start_upstream(password: str) -> subprocess.Popen | None:
    if UPSTREAM_MODE != "standin":
        log(f"[setup] real Neo4j upstream on 127.0.0.1:{UPSTREAM_PORT} (external)")
        return None
    env = os.environ.copy()
    env["E2E_UPSTREAM_USERNAME"] = USERNAME
    env["E2E_UPSTREAM_PASSWORD"] = password
    env["E2E_UPSTREAM_PORT"] = str(UPSTREAM_PORT)
    logf = open(UPSTREAM_LOG, "w")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "upstream_basic.py")],
        env=env, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    if proc.poll() is not None:
        fail("stand-in upstream exited early")
    log(f"[setup] stand-in upstream listening on 127.0.0.1:{UPSTREAM_PORT}")
    return proc


def api(client: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    return client.request(method, path, timeout=30.0, **kw)


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
        f"-d '{QUERY_BODY}' -w '\\nHTTP=%{{http_code}}\\n'"
    )


def main() -> None:
    ensure_dirs()
    password = resolve_password()
    write_password_files(password)
    upstream = start_upstream(password)
    sandbox_id = None
    client = httpx.Client(base_url=API, timeout=30.0)
    try:
        if api(client, "GET", "/health").status_code != 200:
            fail("E2E jiuwenbox server not healthy")
        create_routes(client)

        r = api(client, "POST", "/api/v1/sandboxes", json={})
        if r.status_code != 201:
            fail(f"create sandbox failed: {r.status_code} {r.text}")
        sandbox_id = r.json()["id"]
        st = {}
        for _ in range(20):
            st = api(client, "GET", f"/api/v1/sandboxes/{sandbox_id}").json()
            if st.get("phase") == "ready":
                break
            time.sleep(0.5)
        log(f"[sandbox] created {sandbox_id}, phase={st.get('phase')}")

        # Scenario 1: no Authorization -> 200, value=1
        code, out, err = sandbox_exec(client, sandbox_id, curl_in_sandbox(QUERY_PATH))
        log(f"[scenario 1] no-auth -> exit={code} out={out!r}")
        if "HTTP=200" not in out or '"row":[1]' not in out:
            fail(f"scenario 1 expected 200 with row:[1], got: {out!r} err={err!r}")

        # Scenario 2: wrong Bearer -> overwritten -> 200
        code, out, err = sandbox_exec(
            client, sandbox_id,
            curl_in_sandbox(QUERY_PATH, "-H 'Authorization: Bearer attacker-token-xyz'"),
        )
        log(f"[scenario 2] wrong-bearer -> exit={code} out={out!r}")
        if "HTTP=200" not in out or '"row":[1]' not in out:
            fail(f"scenario 2 expected 200 (Bearer overwritten), got: {out!r}")

        # Scenario 3: wrong Basic -> overwritten -> 200
        fake = base64.b64encode(b"attacker:bad").decode()
        code, out, err = sandbox_exec(
            client, sandbox_id,
            curl_in_sandbox(QUERY_PATH, f"-H 'Authorization: Basic {fake}'"),
        )
        log(f"[scenario 3] wrong-basic -> exit={code} out={out!r}")
        if "HTTP=200" not in out or '"row":[1]' not in out:
            fail(f"scenario 3 expected 200 (Basic overwritten), got: {out!r}")

        # Scenario 4: wrong proxy password_file -> upstream 401
        code, out, err = sandbox_exec(client, sandbox_id, curl_in_sandbox(QUERY_BAD_PATH))
        log(f"[scenario 4] wrong-proxy-pw -> exit={code} out={out!r}")
        if "HTTP=401" not in out:
            fail(f"scenario 4 expected 401 (wrong creds rejected), got: {out!r}")

        # --- no-secret evidence ---
        leaks: list[str] = []
        full_basic = base64.b64encode(f"{USERNAME}:{password}".encode()).decode()

        def check(label: str, text: str) -> None:
            if password in text:
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

        audit_r = api(client, "GET", f"/api/v1/sandboxes/{sandbox_id}/logs")
        check("sandbox audit", audit_r.text)

        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        check("process argv (ps)", ps)

        log(f"[redact] full Basic base64 that must NOT leak: {full_basic}")
        if leaks:
            fail("secret leakage: " + "; ".join(leaks))
        log("[redact] PASS: no plaintext password / full Basic base64 in list, detail, logs, audit, or ps")

        s1_script = curl_in_sandbox(QUERY_PATH)
        if password in s1_script or "Authorization" in s1_script:
            fail("scenario 1 sandbox script contains creds")
        log("[redact] PASS: scenario-1 sandbox script contains no password / no Authorization header")

        log("\n=== E2E RESULT: PASS ===")
    finally:
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
        if upstream is not None:
            upstream.terminate()
            try:
                upstream.wait(timeout=5)
            except subprocess.TimeoutExpired:
                upstream.kill()
            log("[cleanup] stand-in upstream stopped")
        # In standin mode we own both files; in real mode the correct file is
        # managed by the Neo4j setup, so only remove the bad file we created.
        for p in ((PW_FILE, PW_FILE_BAD) if UPSTREAM_MODE != "real" else (PW_FILE_BAD,)):
            try:
                Path(p).unlink()
                log(f"[cleanup] removed {p}")
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
