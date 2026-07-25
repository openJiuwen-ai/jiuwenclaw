# E2E: JiuwenBox Proxy Basic Auth (Neo4j HTTP)

End-to-end check that a credential-free sandbox script reaches a Basic-auth-enforcing
upstream through the JiuwenBox Proxy, which injects `Authorization: Basic ...` from a
server-side `password_file`.

## Files

- `upstream_basic.py` — Basic-auth-enforcing HTTP upstream. Emulates the Neo4j 5.x
  HTTP transaction endpoint `POST /db/{db}/query/v2` (returns `RETURN 1 AS value` →
  `{"keys":["value"],"records":[[1]]}`; 401 on missing/wrong Basic). The password is
  read from `E2E_UPSTREAM_PASSWORD` env, never from argv.
- `run_e2e.py` — orchestrator: writes `0600` password files, starts the upstream,
  creates two proxy routes via REST (correct + wrong `password_file`), creates a real
  JiuwenBox sandbox, runs credential-free `curl` scripts inside it, and proves the
  real password / full Basic base64 does not leak into proxy list/detail, proxy logs,
  sandbox audit, or process argv.

## Why a stand-in instead of real Neo4j

On the 205 test host real Neo4j could not be obtained:

- `docker pull neo4j:5.26-community` from Docker Hub fails with
  `tls: failed to verify certificate: x509: certificate signed by unknown authority`
  (the 205 network runs an intercepting security appliance).
- All configured registry mirrors (`docker.m.daocloud.io`, `docker.1panel.live`,
  `docker.1ms.run`) fail with `failed to copy: httpReadSeeker: ... unable to discard to offset`.
- `https://dist.neo4j.org/.../neo4j-community-4.4.41-unix.tar.gz` returns `403`.

`upstream_basic.py` faithfully reproduces the Neo4j HTTP Basic semantics and query
endpoint shape that exercise the proxy code path. To run against real Neo4j instead,
point the proxy route `target_endpoint` at a real Neo4j HTTP URL (`http://host:7474`)
and drop `upstream_basic.py`; the proxy + sandbox side is unchanged.

## Run (on 205)

```bash
# 1. start a jiuwenbox server with sandbox + proxy (listen 0.0.0.0 so sandboxes
#    can reach it via the host IP). Use a policy that has both sandbox config
#    and inference_privacy_proxies (listen_port=18342, plus a bootstrap route).
JIUWENBOX_POLICY_PATH=/path/to/e2e-policy.yaml \
  /opt/python3.11/bin/python3.11 -m jiuwenbox.server.launcher \
  --listen http://0.0.0.0:18341 --log-level warning &

# 2. run the E2E driver (PROXY_HOST = the host IP reachable from sandboxes)
cd jiuwenswarm/jiuwenbox/tests/manual/e2e_proxy_basic
E2E_API=http://127.0.0.1:18341 E2E_PROXY_HOST=7.221.52.205 E2E_PROXY_PORT=18342 \
  /opt/python3.11/bin/python3.11 run_e2e.py
```

The sandbox uses `curl --noproxy '*'` to bypass the 205 corporate `http_proxy` env
so the host proxy is reached directly; in a normal deployment the sandbox has no
such proxy env and plain `curl` suffices.

## Scenarios asserted

1. no `Authorization` → proxy injects Basic → 200, `records=[[1]]`.
2. wrong `Authorization: Bearer ...` → overwritten → 200.
3. wrong `Authorization: Basic ...` → overwritten → 200.
4. wrong proxy `password_file` → upstream 401.
5. no plaintext password / full Basic base64 in proxy list, detail, logs, sandbox
   audit, or `ps` argv; the scenario-1 sandbox script contains no credential.
