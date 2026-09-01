#!/usr/bin/env bash
# ============================================================================
# post_deploy_init.sh — jiuwenswarm 部署后初始化（幂等，可重复执行）
#
# 背景：AgentServer checkpoint 修复所需的三类"环境状态"改动
#   不会随部署工具流动（业务模板/节点本地态/Redis 派生副本不在 K8s 渲染范围）。
#   本脚本在部署完成后执行一次，把环境收敛到 checkpoint 修复基线：
#     1. PG service_config_template 模板行：
#        - agent_env 合并 7 个 checkpoint 键（只增改这 7 键，不覆盖其他键）
#        - min_idle_services 提升到 >=1；session_ttl 提升到 >=3600（只升不降）
#        - agent_host_path_mounts 追加 logs 可写挂载（若缺）
#        - 模板表为空时 seed 一行完整默认模板（镜像/端口/挂载/env 齐全）
#     2. Redis scope config 派生副本（{resource_manager:<schema>}:...:config）：
#        - min_idle_pods >=1；pod_spec_json 内 agent_env/挂载同步修正
#        - scope 表为空且模板已 seed 时 seed 默认 scope
#     3. 全部 K8s 节点：hostPath 目录存在 + logs 目录 chown 1000:1000
#
# 用法：
#   post_deploy_init.sh                    # 仅收敛
#   post_deploy_init.sh --restart-if-changed   # 有实际变更时滚动重启 agent-runtime
#   post_deploy_init.sh --restart-runtime      # 无论是否变更都重启
#
# 参数化：全部通过 JCL_* 环境变量注入（部署工具 hook 会从 .env.custom/渲染产物
#   自动传入）；连接类参数必填（见下方校验），其余使用通用默认值。
#   JCL_NS/JCL_RUNTIME_DEPLOY      命名空间 / runtime deployment 名
#   JCL_PG_HOST/PORT/USER/PASSWORD/NAME/SCHEMA   checkpoint 与业务库所在 PG
#   JCL_REDIS_URL/JCL_REDIS_PASSWORD             Redis（cluster/standalone 均可）
#   JCL_CODE_DIR/JCL_AGENT_UID                   agentserver 代码 hostPath/运行 uid
#   JCL_AGENT_IMAGE/JCL_TEMPLATE_NAME/JCL_SCOPE_ID/JCL_GATEWAY_CM   seed 用
#   JCL_API_BASE/JCL_API_KEY/JCL_MODEL_PROVIDER/JCL_MODEL_NAME      seed 用
#   JCL_WAIT_TABLES                              等待 DB 建表秒数（默认 90）
#
# 依赖：kubectl；runtime pod 内 asyncpg/redis-py；到各节点 root 免密 ssh。
# ============================================================================
set -euo pipefail

# 连接类参数：必填（部署工具 hook 自动注入；手工执行需显式设置）
JCL_NS="${JCL_NS:-}"
JCL_PG_HOST="${JCL_PG_HOST:-}"
JCL_PG_PORT="${JCL_PG_PORT:-5432}"
JCL_PG_USER="${JCL_PG_USER:-}"
JCL_PG_PASSWORD="${JCL_PG_PASSWORD:-}"
JCL_PG_NAME="${JCL_PG_NAME:-}"
JCL_PG_SCHEMA="${JCL_PG_SCHEMA:-}"
JCL_REDIS_URL="${JCL_REDIS_URL:-}"
JCL_REDIS_PASSWORD="${JCL_REDIS_PASSWORD:-}"   # Redis 无密码环境允许为空
JCL_CODE_DIR="${JCL_CODE_DIR:-}"               # 为空时跳过 hostPath 相关收敛并 WARN
JCL_AGENT_IMAGE="${JCL_AGENT_IMAGE:-}"
JCL_RUNTIME_DEPLOY="${JCL_RUNTIME_DEPLOY:-jiuwenclaw-agent-runtime}"
JCL_AGENT_UID="${JCL_AGENT_UID:-1000}"
JCL_TEMPLATE_NAME="${JCL_TEMPLATE_NAME:-default-template}"
JCL_SCOPE_ID="${JCL_SCOPE_ID:-default-scope}"
JCL_GATEWAY_CM="${JCL_GATEWAY_CM:-jiuwenclaw-gateway-config}"
JCL_API_BASE="${JCL_API_BASE:-}"
JCL_API_KEY="${JCL_API_KEY:-}"
JCL_MODEL_PROVIDER="${JCL_MODEL_PROVIDER:-OpenAI}"
JCL_MODEL_NAME="${JCL_MODEL_NAME:-}"
JCL_WAIT_TABLES="${JCL_WAIT_TABLES:-90}"

MODE="none"
[[ "${1:-}" == "--restart-if-changed" ]] && MODE="if-changed"
[[ "${1:-}" == "--restart-runtime" ]] && MODE="always"
MARKER="${JCL_MARKER:-/tmp/jcl_pdi.changed}"
rm -f "$MARKER"

log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { log "  [OK]   $*"; }
fix()  { log "  [FIX]  $*"; }
warn() { log "  [WARN] $*"; }
die()  { log "  [ERR]  $*"; exit 1; }

# --- 0a. 必填参数校验（hook 自动注入；手工执行需显式设置）--------------------
_MISS=""
for _v in JCL_NS JCL_PG_HOST JCL_PG_USER JCL_PG_PASSWORD JCL_PG_NAME \
          JCL_PG_SCHEMA JCL_REDIS_URL; do
  if [[ -z "${!_v}" ]]; then _MISS+=" $_v"; fi
done
if [[ -n "$_MISS" ]]; then
  die "缺少必填环境变量:$_MISS（部署工具 hook 自动注入；手工执行请显式设置）"
fi

# --- 0. 预检 ----------------------------------------------------------------
command -v kubectl >/dev/null 2>&1 || die "kubectl 不可用"
kubectl -n "$JCL_NS" get ns "$JCL_NS" >/dev/null 2>&1 || die "命名空间 $JCL_NS 不可达：部署未完成？"
RT_POD=$(kubectl -n "$JCL_NS" get pods 2>/dev/null \
  | awk -v d="$JCL_RUNTIME_DEPLOY" '$1 ~ d"-" && $3 == "Running" {print $1; exit}')
[[ -n "$RT_POD" ]] || die "未发现 Running 的 ${JCL_RUNTIME_DEPLOY} pod：部署未完成？"
log "0. 预检通过（ns=$JCL_NS runtime pod: $RT_POD）"

# --- 1. PG 模板行 + Redis scope config 收敛/seed（借道 runtime pod 执行）------
log "1. 收敛 PG 模板行与 Redis scope config（schema=${JCL_PG_SCHEMA}）"

# 配置经 JSON argv 传入 pod（kubectl exec 不透传本地环境变量）
CFG="$(python3 - "$JCL_NS" "$JCL_PG_HOST" "$JCL_PG_PORT" "$JCL_PG_USER" "$JCL_PG_PASSWORD" \
  "$JCL_PG_NAME" "$JCL_PG_SCHEMA" "$JCL_REDIS_URL" "$JCL_REDIS_PASSWORD" \
  "$JCL_CODE_DIR" "$JCL_AGENT_IMAGE" "$JCL_TEMPLATE_NAME" "$JCL_SCOPE_ID" \
  "$JCL_GATEWAY_CM" "$JCL_API_BASE" "$JCL_API_KEY" "$JCL_MODEL_PROVIDER" \
  "$JCL_MODEL_NAME" <<'JSONEOF'
import json, sys
keys = ["ns","pg_host","pg_port","pg_user","pg_password","pg_name","pg_schema",
        "redis_url","redis_password","code_dir","agent_image","template_name",
        "scope_id","gateway_cm","api_base","api_key","model_provider","model_name"]
cfg = dict(zip(keys, sys.argv[1:]))
cfg["pg_port"] = int(cfg["pg_port"])
print(json.dumps(cfg, ensure_ascii=False))
JSONEOF
)"

PY="$(mktemp /tmp/jcl_pdi.XXXXXX.py)"
cat > "$PY" <<'PYEOF'
import asyncio, json, sys, time
import asyncpg

CFG = json.loads(sys.argv[1])
SCHEMA = CFG["pg_schema"]
LOGS_MOUNT_PATH = "/app/jiuwenswarm/logs"
CODE_MOUNT_PATH = "/app/jiuwenswarm"
CHECKPOINT_ENV = {
    "CHECKPOINT_DB_TYPE": "postgresql",
    "GATEWAY_DB_HOST": CFG["pg_host"],
    "GATEWAY_DB_PORT": str(CFG["pg_port"]),
    "GATEWAY_DB_USER": CFG["pg_user"],
    "GATEWAY_DB_PASSWORD": CFG["pg_password"],
    "GATEWAY_DB_NAME": CFG["pg_name"],
    "GATEWAY_PG_SCHEMA": SCHEMA,
}
MIN_IDLE = 1
SESSION_TTL_MIN = 3600


def pj(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    try:
        return json.loads(v)
    except Exception:
        return None


def merge_env(env):
    env = dict(env or {})
    changed = [k for k, v in CHECKPOINT_ENV.items() if env.get(k) != v]
    env.update(CHECKPOINT_ENV)
    return env, changed


def merge_mounts(mounts):
    mounts = list(mounts or [])
    changed = []
    if not CFG["code_dir"]:
        return mounts, changed, "unset"
    if not any(m.get("mount_path") == LOGS_MOUNT_PATH for m in mounts):
        mounts.append({"host_path": CFG["code_dir"] + "/logs",
                       "mount_path": LOGS_MOUNT_PATH,
                       "read_only": False, "host_path_type": "Directory"})
        changed.append("logs_mount")
    has_code = any(m.get("mount_path") == CODE_MOUNT_PATH for m in mounts)
    return mounts, changed, has_code


async def db():
    conn = await asyncpg.connect(
        host=CFG["pg_host"], port=CFG["pg_port"], user=CFG["pg_user"],
        password=CFG["pg_password"], database=CFG["pg_name"])
    try:
        # 等待 runtime 完成建表（全新部署时表由 runtime 启动创建）
        deadline = time.time() + CFG.get("wait_tables", 90)
        while True:
            ok_t = await conn.fetchval(
                "SELECT to_regclass($1)", '"%s".service_config_template' % SCHEMA)
            ok_s = await conn.fetchval(
                "SELECT to_regclass($1)", '"%s".routing_scope' % SCHEMA)
            if ok_t and ok_s:
                break
            if time.time() > deadline:
                print("  [ERR]  业务表不存在（runtime 尚未初始化 DB）："
                      f"{SCHEMA}.service_config_template/routing_scope")
                sys.exit(3)
            print("  [WAIT] 等待 runtime 建表 ...")
            await asyncio.sleep(3)

        rows = await conn.fetch(
            'SELECT id, template_name, agent_env, min_idle_services, '
            'session_ttl, agent_host_path_mounts '
            f'FROM "{SCHEMA}".service_config_template')
        if not rows:
            if not CFG["code_dir"]:
                print("  [ERR]  模板表为空需要 seed，但 JCL_CODE_DIR 未配置（代码 hostPath 目录）")
                sys.exit(3)
            # seed 完整默认模板行
            import uuid
            tid = str(uuid.uuid4())
            env = dict(CHECKPOINT_ENV)
            env.update({
                "OPENJIUWEN_SERVICE_DB_TYPE": "postgresql",
                "OPENJIUWEN_SERVICE_DB_HOST": CFG["pg_host"],
                "OPENJIUWEN_SERVICE_DB_PORT": str(CFG["pg_port"]),
                "OPENJIUWEN_SERVICE_DB_USER": CFG["pg_user"],
                "OPENJIUWEN_SERVICE_DB_PASSWORD": CFG["pg_password"],
                "OPENJIUWEN_SERVICE_DB_NAME": CFG["pg_name"],
                "OPENJIUWEN_SERVICE_PG_SCHEMA": SCHEMA,
                "OPENJIUWEN_SERVICE_REDIS_URL": CFG["redis_url"],
                "REDIS_PASSWORD": CFG["redis_password"],
                "AGENT_HTTP_ENABLED": "true",
                "AGENT_HTTP_HOST": "0.0.0.0",
                "AGENT_HTTP_PORT": "8766",
                "AGENT_SERVER_HOST": "0.0.0.0",
                "AGENT_SERVER_PORT": "18092",
                "JIUWENSWARM_EDITION": "enterprise",
                "JIUWENCLAW_SANDBOX_ENABLED": "false",
                "API_KEY": CFG["api_key"],
                "API_BASE": CFG["api_base"],
                "MODEL_PROVIDER": CFG["model_provider"],
                "MODEL_NAME": CFG["model_name"],
                "LLM_SSL_VERIFY": "False",
                "TZ": "Asia/Shanghai",
            })
            mounts = [
                {"host_path": CFG["code_dir"], "mount_path": CODE_MOUNT_PATH,
                 "read_only": True, "host_path_type": "Directory"},
                {"host_path": CFG["code_dir"] + "/logs",
                 "mount_path": LOGS_MOUNT_PATH,
                 "read_only": False, "host_path_type": "Directory"},
            ]
            cms = [{"config_map_name": CFG["gateway_cm"],
                    "mount_path": "/home/app/.jiuwenswarm/config/config.yaml",
                    "sub_path": "config.yaml", "items": None, "read_only": True}]
            await conn.execute(
                f'INSERT INTO "{SCHEMA}".service_config_template ('
                'jiuwenclaw_id, template_id, template_name, description, '
                'agent_image, namespace, pod_name, container_name, '
                'container_port, port_name, sse_port, sse_path, health_path, '
                'agent_env, image_pull_policy, readiness_initial_delay, '
                'readiness_period, ready_timeout, ready_poll_interval, '
                'agent_host_path_mounts, agent_configmap_mounts, '
                'min_idle_services, service_concurrency, service_ttl, '
                'session_concurrency, session_ttl, message_timeout, enabled, data, '
                'created_at, updated_at'
                ') VALUES ('
                "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,"
                "$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,"
                'now(), now())',
                "", tid, CFG["template_name"],
                "seeded by post_deploy_init", CFG["agent_image"], CFG["ns"],
                "agentserver-mnt", "agent", 8766, "http", 8766, "/api/v1",
                "/api/v1/health", json.dumps(env, ensure_ascii=False),
                "IfNotPresent", 10, 5, 300, 5,
                json.dumps(mounts, ensure_ascii=False),
                json.dumps(cms, ensure_ascii=False),
                MIN_IDLE, 2, 180, 3, SESSION_TTL_MIN, 60, True, "{}")
            print(f"  [SEED] 模板表为空，已 seed 默认模板 id={tid} name={CFG['template_name']}")
            rows = await conn.fetch(
                'SELECT id, template_name, agent_env, min_idle_services, '
                'session_ttl, agent_host_path_mounts '
                f'FROM "{SCHEMA}".service_config_template')

        for r in rows:
            rid, name = r["id"], r["template_name"]
            env, ch_env = merge_env(pj(r["agent_env"]))
            mounts, ch_mount, has_code = merge_mounts(pj(r["agent_host_path_mounts"]))
            mi = r["min_idle_services"] or 0
            st = r["session_ttl"] or 0
            need = bool(ch_env or ch_mount) or mi < MIN_IDLE or st < SESSION_TTL_MIN
            if need:
                await conn.execute(
                    f'UPDATE "{SCHEMA}".service_config_template '
                    'SET agent_env=$1, min_idle_services=$2, session_ttl=$3, '
                    'agent_host_path_mounts=$4, updated_at=now() WHERE id=$5',
                    json.dumps(env, ensure_ascii=False),
                    max(mi, MIN_IDLE), max(st, SESSION_TTL_MIN),
                    json.dumps(mounts, ensure_ascii=False), rid)
                print(f"  [FIX]  模板 id={rid}({name}): env+{len(ch_env)}键 "
                      f"挂载+{len(ch_mount)} min_idle={max(mi, MIN_IDLE)} "
                      f"session_ttl={max(st, SESSION_TTL_MIN)}")
            else:
                print(f"  [OK]   模板 id={rid}({name}): 已是修复基线，无需变更")
            if has_code == "unset":
                print(f"  [WARN] 模板 id={rid}: JCL_CODE_DIR 未配置，跳过 logs/代码目录挂载收敛")
            elif not has_code:
                print(f"  [WARN] 模板 id={rid} 缺少代码目录挂载 {CODE_MOUNT_PATH}"
                      "（部署自身配置问题，本脚本不代建）")
        rows2 = await conn.fetch(
            'SELECT id, template_name, agent_env, min_idle_services, session_ttl '
            f'FROM "{SCHEMA}".service_config_template')
        for r in rows2:
            env = pj(r["agent_env"]) or {}
            missing = sorted(set(CHECKPOINT_ENV) - set(env))
            state = "checkpoint键齐全" if not missing else f"checkpoint键仍缺失{missing}"
            print(f"  [VERIFY] 模板 id={r['id']}: {state} "
                  f"min_idle={r['min_idle_services']} session_ttl={r['session_ttl']}")

        scope_ids = [x["scope_id"] for x in
                     await conn.fetch(f'SELECT scope_id FROM "{SCHEMA}".routing_scope')]
        if not scope_ids:
            first = await conn.fetchrow(
                'SELECT template_id FROM "{0}".service_config_template '
                'ORDER BY id LIMIT 1'.format(SCHEMA))
            if first:
                await conn.execute(
                    f'INSERT INTO "{SCHEMA}".routing_scope '
                    '(jiuwenclaw_id, scope_id, match_index, template_id, routing_rules, '
                    'created_at, updated_at) '
                    'VALUES ($1,$2,$3,$4,$5, now(), now())',
                    "", CFG["scope_id"], 0, first["template_id"], json.dumps(""))
                print(f"  [SEED] scope 表为空，已 seed 默认 scope {CFG['scope_id']}"
                      f" -> template {first['template_id']}")
                scope_ids = [CFG["scope_id"]]
            else:
                print("  [WARN] scope 表为空且无模板可引用：跳过 scope seed")
        return scope_ids
    finally:
        await conn.close()


def redis_part(scope_ids):
    from redis.cluster import RedisCluster
    if not scope_ids:
        print("  [WARN] routing_scope 为空：跳过 Redis 收敛")
        return
    r = RedisCluster.from_url(
        CFG["redis_url"].replace("redis+cluster://", "redis://", 1),
        password=CFG["redis_password"], decode_responses=True)
    for sid in scope_ids:
        key = "{resource_manager:%s}:resource:scope:%s:config" % (SCHEMA, sid)
        if not r.exists(key):
            print(f"  [SKIP] Redis {key} 尚未 seed（首次会话时自动从模板生成）")
            continue
        cur = int(r.hget(key, "min_idle_pods") or 0)
        if cur < MIN_IDLE:
            r.hset(key, "min_idle_pods", MIN_IDLE)
            print(f"  [FIX]  Redis {sid}: min_idle_pods {cur} -> {MIN_IDLE}")
        else:
            print(f"  [OK]   Redis {sid}: min_idle_pods={cur}")
        spec = pj(r.hget(key, "pod_spec_json")) or {}
        env, ch_env = merge_env(spec.get("agent_env"))
        mounts, ch_mount, has_code = merge_mounts(spec.get("agent_host_path_mounts"))
        if ch_env or ch_mount:
            spec["agent_env"] = env
            spec["agent_host_path_mounts"] = mounts
            r.hset(key, "pod_spec_json", json.dumps(spec, ensure_ascii=False))
            print(f"  [FIX]  Redis {sid}: pod_spec_json 同步（env+{len(ch_env)}键 "
                  f"挂载+{len(ch_mount)}）；存量 pod 随轮换获得新 env")
        else:
            print(f"  [OK]   Redis {sid}: pod_spec_json 已含 checkpoint 配置")
        if has_code == "unset":
            print(f"  [WARN] Redis {sid}: JCL_CODE_DIR 未配置，跳过 logs/代码目录挂载收敛")
        elif not has_code:
            print(f"  [WARN] Redis {sid}: pod_spec_json 缺代码目录挂载 {CODE_MOUNT_PATH}")


CFG["wait_tables"] = int(sys.argv[2]) if len(sys.argv) > 2 else 90
scope_ids = asyncio.run(db())
redis_part(scope_ids)
PYEOF
OUT="$(kubectl -n "$JCL_NS" exec -i "$RT_POD" -- python - "$CFG" "$JCL_WAIT_TABLES" < "$PY" 2>&1)" \
  || { echo "$OUT"; rm -f "$PY"; die "DB/Redis 收敛失败"; }
echo "$OUT"
rm -f "$PY"
if echo "$OUT" | grep -qE '\[(FIX|SEED)\]'; then
  echo 1 > "$MARKER"
  log "  变更已应用（标记文件 $MARKER）"
fi

# --- 2. 节点 hostPath 目录 ---------------------------------------------------
log "2. 节点 hostPath 目录检查与收敛"
NODES=""
if [[ -n "$JCL_CODE_DIR" ]]; then
  NODES=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' | sort -u)
else
  warn "JCL_CODE_DIR 未配置：跳过节点 hostPath 目录收敛（非 dev 模式部署可不配置）"
fi
LOCAL_IPS=" $(hostname -I) "
FAILED_NODES=()
for ip in $NODES; do
  [[ -z "$ip" ]] && continue
  if [[ "$LOCAL_IPS" == *" $ip "* ]]; then
    mkdir -p "$JCL_CODE_DIR" "$JCL_CODE_DIR/logs"
    chown -R "$JCL_AGENT_UID:$JCL_AGENT_UID" "$JCL_CODE_DIR/logs"
    [[ -z "$(ls -A "$JCL_CODE_DIR" 2>/dev/null)" ]] \
      && warn "节点 $ip（本机）：代码目录 $JCL_CODE_DIR 为空，agentserver 代码挂载将不生效"
    ok "节点 $ip（本机）：目录就绪，logs 属主 ${JCL_AGENT_UID}:${JCL_AGENT_UID}"
  else
    if OUT2=$(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "root@$ip" \
        "mkdir -p '$JCL_CODE_DIR' '$JCL_CODE_DIR/logs' && chown -R $JCL_AGENT_UID:$JCL_AGENT_UID '$JCL_CODE_DIR/logs' && if [ -z \"\$(ls -A '$JCL_CODE_DIR' 2>/dev/null)\" ]; then echo __EMPTY_CODE__; fi" 2>&1); then
      [[ "$OUT2" == *__EMPTY_CODE__* ]] \
        && warn "节点 $ip：代码目录 $JCL_CODE_DIR 为空，agentserver 代码挂载将不生效"
      ok "节点 $ip：目录就绪，logs 属主 ${JCL_AGENT_UID}:${JCL_AGENT_UID}"
    else
      FAILED_NODES+=("$ip")
      warn "节点 $ip：ssh 失败，请手动执行：ssh root@$ip \"mkdir -p $JCL_CODE_DIR $JCL_CODE_DIR/logs && chown -R $JCL_AGENT_UID:$JCL_AGENT_UID $JCL_CODE_DIR/logs\""
    fi
  fi
done

# --- 3. 可选：滚动重启 runtime ------------------------------------------------
NEED_RESTART=0
[[ "$MODE" == "always" ]] && NEED_RESTART=1
[[ "$MODE" == "if-changed" && -f "$MARKER" ]] && NEED_RESTART=1
if [[ "$NEED_RESTART" == 1 ]]; then
  log "3. 滚动重启 ${JCL_RUNTIME_DEPLOY}（SM 内存快照从 DB 重新对齐）"
  kubectl -n "$JCL_NS" rollout restart "deployment/$JCL_RUNTIME_DEPLOY"
  kubectl -n "$JCL_NS" rollout status "deployment/$JCL_RUNTIME_DEPLOY" --timeout=300s
else
  log "3. 跳过 runtime 重启（无变更或未指定重启模式）"
fi

# --- 4. 汇总 ------------------------------------------------------------------
log "4. 汇总"
[[ ${#FAILED_NODES[@]} -gt 0 ]] && die "存在不可达节点：${FAILED_NODES[*]}（按 WARN 提示手动处理后重跑）"
log "完成：环境已收敛到 checkpoint 修复基线（幂等，可重复执行）"
