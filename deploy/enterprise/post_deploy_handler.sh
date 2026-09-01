#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ============================================================================
# post_deploy_handler.sh — 部署完成后收敛业务侧基线
#
# 背景：部署工具只渲染 K8s 基础设施，业务侧的三类环境状态不随渲染流动：
#   1. PG service_config_template 模板行（AgentServer checkpoint 所需 env 键、
#      最小暖池、会话 TTL、logs 可写挂载）；
#   2. Redis scope config 派生副本（resource_manager:resource:scope:<id>:config，
#      由模板行生成，存量副本需同步修正）；
#   3. 各 K8s 节点 hostPath 目录（代码目录 + logs 子目录属主）。
# 本 hook 在 process_up 全模块部署完成后自动执行一次幂等收敛
# （同目录 post_deploy_init.sh，连接参数经 JCL_* 环境变量注入）。
# 连接参数以渲染后的 runtime.yaml 为权威值（.env.custom 缺项时以实际部署为准）。
# RENDER_ONLY / 非 postgresql 环境自动跳过。
# ============================================================================
post_deploy_init_hook() {
    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi
    if [ "${DEPLOY_VARS["DB_TYPE"]:-}" != "postgresql" ]; then
        warning "post_deploy_init 跳过：DB_TYPE=${DEPLOY_VARS["DB_TYPE"]:-} 非 postgresql，checkpoint 修复基线不适用"
        return
    fi

    local runtime_file="${CONFIG["RUNTIME_FILE"]}"
    local pdi_dir pdi_script
    pdi_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    pdi_script="${pdi_dir}/post_deploy_init.sh"
    if [ ! -f "${pdi_script}" ]; then
        warning "post_deploy_init.sh 不存在（应与本脚本同目录），跳过部署后初始化"
        return
    fi

    local pg_schema pg_dbname redis_url
    pg_schema="$(yq eval '.spec.template.spec.containers[0].env[] | select(.name=="OPENJIUWEN_SERVICE_PG_SCHEMA") | .value' "${runtime_file}" 2>/dev/null || true)"
    pg_dbname="$(yq eval '.spec.template.spec.containers[0].env[] | select(.name=="OPENJIUWEN_SERVICE_DB_NAME") | .value' "${runtime_file}" 2>/dev/null || true)"
    redis_url="$(yq eval '.spec.template.spec.containers[0].env[] | select(.name=="OPENJIUWEN_SERVICE_REDIS_URL") | .value' "${runtime_file}" 2>/dev/null || true)"
    [ -z "${pg_schema}" ] && pg_schema="${DEPLOY_VARS["RUNTIME_PG_SCHEMA"]:-}"
    [ -z "${pg_dbname}" ] && pg_dbname="${DEPLOY_VARS["RUNTIME_DB_NAME"]:-}"
    if [ -z "${pg_schema}" ]; then
        warning "RUNTIME_PG_SCHEMA 为空（runtime.yaml 与 .env.custom 均未配置），跳过部署后初始化"
        return
    fi

    info "部署后初始化：收敛 checkpoint 修复基线（ns=${DEPLOY_VARS["NAMESPACE"]} schema=${pg_schema} db=${pg_dbname}）"
    JCL_NS="${DEPLOY_VARS["NAMESPACE"]}" \
    JCL_RUNTIME_DEPLOY="${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}" \
    JCL_PG_HOST="${DEPLOY_VARS["DB_HOST"]}" \
    JCL_PG_PORT="${DEPLOY_VARS["DB_PORT"]}" \
    JCL_PG_USER="${DEPLOY_VARS["DB_USER"]}" \
    JCL_PG_PASSWORD="${DEPLOY_VARS["DB_PASSWORD"]}" \
    JCL_PG_NAME="${pg_dbname}" \
    JCL_PG_SCHEMA="${pg_schema}" \
    JCL_REDIS_URL="${redis_url}" \
    JCL_REDIS_PASSWORD="${DEPLOY_VARS["REDIS_PASSWORD"]:-}" \
    JCL_CODE_DIR="${DEPLOY_VARS["CLAW_CODE_PATH"]}" \
    JCL_AGENT_IMAGE="${DEPLOY_VARS["AGENT_SERVER_IMAGE"]}" \
    JCL_TEMPLATE_NAME="${DEPLOY_VARS["PDI_TEMPLATE_NAME"]:-default-template}" \
    JCL_SCOPE_ID="${DEPLOY_VARS["PDI_SCOPE_ID"]:-default-scope}" \
    JCL_GATEWAY_CM="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}" \
    JCL_API_BASE="${DEPLOY_VARS["API_BASE"]:-}" \
    JCL_API_KEY="${DEPLOY_VARS["API_KEY"]:-}" \
    JCL_MODEL_PROVIDER="${DEPLOY_VARS["MODEL_PROVIDER"]:-OpenAI}" \
    JCL_MODEL_NAME="${DEPLOY_VARS["MODEL_NAME"]:-}" \
    bash "${pdi_script}" --restart-if-changed
}
