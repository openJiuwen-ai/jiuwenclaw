# E2E: JiuwenBox Proxy Basic Auth (real Neo4j HTTP)

End-to-end check that a credential-free sandbox script reaches a Basic-auth-enforcing
upstream through the JiuwenBox Proxy, which injects `Authorization: Basic ...` from a
server-side `password_file`.

## Files

- `upstream_basic.py` — offline stand-in upstream. Faithfully emulates the real
  Neo4j 2026.06.0 HTTP transactional endpoint `POST /db/{db}/tx/commit`
  (request `{"statements":[{"statement":"RETURN 1 AS value"}]}`; 200 response
  `{"results":[{"columns":["value"],"data":[{"row":[1],"meta":[null]}]}],"errors":[]}`;
  401 `{"errors":[{"code":"Neo.ClientError.Security.Unauthorized",...}]}`).
  The password is read from `E2E_UPSTREAM_PASSWORD` env, never from argv.
- `run_e2e.py` — orchestrator. Two modes (see below). Writes `0600` password
  files (or reuses the real one), starts the stand-in if needed, creates two
  proxy routes via REST (correct + wrong `password_file`), creates a real
  JiuwenBox sandbox, runs credential-free `curl` scripts inside it, and proves
  the real password / full Basic base64 does not leak into proxy list/detail,
  proxy logs, sandbox audit, or process argv.

## Two upstream modes (`E2E_UPSTREAM`)

- `real` (default): a real Neo4j 2026.06.0 instance on `127.0.0.1:17474`,
  started outside the driver. The real password lives only in a `0600` file
  pointed to by `E2E_PASSWORD_FILE` (default `/bke/neo4j-basic-verify/neo4j_password`).
  This is the P2-R verification path.
- `standin`: the bundled `upstream_basic.py` for offline regression where real
  Neo4j / Java 21 is unavailable. The driver writes both `0600` password files
  itself (test-fixture password) and starts the stand-in.

Both modes exercise the identical proxy + sandbox + redaction code path; only
the upstream differs.

## Real Neo4j 2026.06.0 HTTP interface (verified)

```
POST http://127.0.0.1:17474/db/neo4j/tx/commit
Authorization: Basic <base64(neo4j:<password>)>
Content-Type: application/json

{"statements":[{"statement":"RETURN 1 AS value"}]}

200 -> {"results":[{"columns":["value"],"data":[{"row":[1],"meta":[null]]}],
         "notifications":[...],"errors":[],"lastBookmarks":[...]}
401 -> {"errors":[{"code":"Neo.ClientError.Security.Unauthorized",
         "message":"Invalid credential."}]}     # wrong Basic
       {"errors":[{"code":"Neo.ClientError.Security.Unauthorized",
         "message":"No authentication header supplied."}]}  # missing
```

Neo4j 2026.06.0 also exposes the newer Query API v2 (`POST /db/{db}/query/v2`,
body `{"statement":...}`, response `{"data":{"fields":[...],"values":[[1]]}}`),
but the HTTP transactional endpoint `/db/{db}/tx/commit` is used here because it
is the stable, documented REST query API and the one the policy example comments
reference. The stand-in emulates exactly this tx/commit shape (NOT the v2 shape).

## Java requirement (real mode)

Neo4j 2026.06.0 requires **Java 21 or 25** (`Unsupported Java 11.0.22 detected.
Please use Java(TM) 21 or Java(TM) 25 to run Neo4j Server`). The 205 host (Kylin,
glibc 2.28, aarch64) ships only Java 8/11; a portable OpenJDK 21.0.2 aarch64
tarball (from the huaweicloud openjdk mirror) is extracted to a temp dir and
used via `JAVA_HOME` without touching the system default Java.

## Run (on 205)

```bash
# 0. real mode only: start Neo4j 2026.06.0 with JDK 21 on 127.0.0.1:17474,
#    set the neo4j initial password, and write it to a 0600 file at
#    /bke/neo4j-basic-verify/neo4j_password (done by the P2-R setup steps).

# 1. start a jiuwenbox server with sandbox + proxy (listen 0.0.0.0 so sandboxes
#    can reach it). Policy must set inference_privacy_proxies.listen_port=18342
#    and listen_host=0.0.0.0.
JIUWENBOX_POLICY_PATH=/path/to/e2e-policy.yaml \
  PYTHONPATH=/bke/jiuwenbox-src/jiuwenbox/src \
  /opt/python3.11/bin/python3.11 -m jiuwenbox.server.launcher \
  --listen http://0.0.0.0:18341 --log-level warning &

# 2. real Neo4j E2E
cd jiuwenswarm/jiuwenbox/tests/manual/e2e_proxy_basic
E2E_API=http://127.0.0.1:18341 E2E_PROXY_HOST=7.221.52.205 E2E_PROXY_PORT=18342 \
  E2E_UPSTREAM=real E2E_PASSWORD_FILE=/bke/neo4j-basic-verify/neo4j_password \
  /opt/python3.11/bin/python3.11 run_e2e.py

# 2b. offline stand-in E2E (no Neo4j / no Java 21 needed)
E2E_API=http://127.0.0.1:18341 E2E_PROXY_HOST=127.0.0.1 E2E_PROXY_PORT=18342 \
  E2E_UPSTREAM=standin E2E_UPSTREAM_PORT=17474 \
  /opt/python3.11/bin/python3.11 run_e2e.py
```

The sandbox uses `curl --noproxy '*'` to bypass the 205 corporate `http_proxy`
env so the host proxy is reached directly; in a normal deployment the sandbox
has no such proxy env and plain `curl` suffices.

## Scenarios asserted

1. no `Authorization` → proxy injects Basic → 200, `"row":[1]` (value=1).
2. wrong `Authorization: Bearer ...` → overwritten → 200.
3. wrong `Authorization: Basic ...` → overwritten → 200.
4. wrong proxy `password_file` → upstream 401.
5. no plaintext password / full Basic base64 in proxy list, detail, logs, sandbox
   audit, or `ps` argv; the scenario-1 sandbox script contains no credential.
