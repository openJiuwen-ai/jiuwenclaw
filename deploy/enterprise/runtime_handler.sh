#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_runtime_file() {
    local template_file="${CONFIG["RUNTIME_TEMPLATE_FILE"]}"
    local file="${CONFIG["RUNTIME_FILE"]}"

    # 按 REDIS_MODE 计算 AgentServer checkpointer 的 Redis URL
    # cluster：redis+cluster://host:port（cluster 只有 db 0，不能带库号）
    # standalone：redis://host:port/db
    local redis_mode="${DEPLOY_VARS["REDIS_MODE"]:-standalone}"
    local redis_host="${DEPLOY_VARS["REDIS_HOST"]:-}"
    local redis_port="${DEPLOY_VARS["REDIS_PORT"]:-}"
    local redis_db="${DEPLOY_VARS["AGENT_RUNTIME_REDIS_DB"]:-0}"
    # 校验非空：host/port 缺失时不能拼出残缺的 Redis URL（如 redis://:6379/0），
    # 否则 AgentServer checkpointer 会因连接串非法而启动失败。此时跳过赋值并告警。
    if [[ -z "${redis_host}" || -z "${redis_port}" ]]; then
        warning "REDIS_HOST 或 REDIS_PORT 为空，跳过 OPENJIUWEN_SERVICE_REDIS_URL 赋值（AgentServer checkpointer Redis 连接串未设置）"
    elif [[ "${redis_mode}" == "cluster" ]]; then
        DEPLOY_VARS["OPENJIUWEN_SERVICE_REDIS_URL"]="redis+cluster://${redis_host}:${redis_port}"
    else
        DEPLOY_VARS["OPENJIUWEN_SERVICE_REDIS_URL"]="redis://${redis_host}:${redis_port}/${redis_db}"
    fi

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file} runtime

    if [ "${DEPLOY_VARS["DB_TYPE"]}" == "postgresql" ]; then
        yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].env += [
            {
                "name": "OPENJIUWEN_SERVICE_PG_SCHEMA",
                "value": "'"${DEPLOY_VARS["RUNTIME_PG_SCHEMA"]}"'"
            }
        ]' -i "${file}"
    fi

    add_resource_if_set "RUNTIME" "${file}"
}

render_runtime_files() {
    render_secret_configmap
    gen_runtime_file
}

deploy_runtime() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}"
    local file="${CONFIG["RUNTIME_FILE"]}"

    ensure_secret_configmap
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${name}" "${namespace}"
}

uninstall_runtime() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}"
    local file="${CONFIG["RUNTIME_FILE"]}"

    # 先删 Deployment（保持 ServiceAccount 不删），等 Pod 优雅退出
    # runtime.yaml 含 ServiceAccount / Role / Deployment 等同文件资源。
    # 不可 kubectl delete -f 一次性删：SA 被删后 Pod 仍在 Terminating 窗口内，
    # in-cluster token 立即失效 → runtime shutdown 删 AgentServer Pod 会 401。
    info "Deleting Runtime Deployment first (keep ServiceAccount for graceful shutdown)"
    exec_cmd kubectl delete deployment "${name}" -n "${namespace}" --ignore-not-found=true
    wait_pod_terminated "${name}" "${namespace}"

    # 兜底清理 runtime 动态创建的 agentserver pod（按 label）
    local orphan_pods
    orphan_pods=$(kubectl get pods -n "${namespace}" -l jiuwenclaw-component=agentserver -o name 2>/dev/null || true)
    if [ -n "${orphan_pods}" ]; then
        info "Cleaning up orphan agentserver pods created by runtime (label: jiuwenclaw-component=agentserver)"
        exec_cmd kubectl delete pod -n "${namespace}" -l jiuwenclaw-component=agentserver --ignore-not-found=true
    else
        info "No orphan agentserver pods to clean up."
    fi

    info "Deleting remaining Runtime resources (ServiceAccount, Role, Service, ...)"
    exec_cmd kubectl delete -f "${file}" --ignore-not-found=true
    uninstall_secret_configmap
    ensure_redis_down "gateway"
}
