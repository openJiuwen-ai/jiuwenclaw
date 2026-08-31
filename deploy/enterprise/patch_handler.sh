#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_patch_file() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local json_template="${CONFIG["AS_JSON_TEMPLATE_FILE"]}"
    local json_file="${CONFIG["AS_JSON_FILE"]}"
    local env_template="${CONFIG["AS_ENV_TEMPLATE_FILE"]}"
    local env_file="${CONFIG["AS_ENV_FILE"]}"
    local envfile_name="${DEPLOY_VARS["AGENT_SERVER_ENV_CM_NAME"]}"

    info "Rendering AgentServer templates"
    render_config_template "${json_template}" "${json_file}" "DEPLOY_VARS"
    render_config_template "${env_template}"  "${env_file}"  "DEPLOY_VARS"

    if ! jq . "${json_file}" >/dev/null 2>&1; then
        error "AgentServer JSON rendering failed, invalid JSON format: ${json_file}"
    fi

    # product 模式：镜像内置代码，不需要 hostPath 挂载 / root 权限
    # 三段式契约下：删 hostPath volumes + 主容器 securityContext + 引用已删卷的 volumeMounts
    if [[ "${DEPLOY_VARS["MODE"]}" == "product" ]]; then
        jq '.rawdata.templates[].volumes |= map(select(has("hostPath") | not))
            | .rawdata.containers |= map(if .container_id == "c-agentserver" then del(.securityContext) else . end)' \
            "${json_file}" > "${json_file}.tmp"
        jq '(.rawdata.templates[0].volumes | map(.name)) as $keep
            | .rawdata.containers[].volumeMounts |= map(select(.name as $n | $keep | map(. == $n) | any))' \
            "${json_file}.tmp" > "${json_file}"
        rm -f "${json_file}.tmp"
    fi

    # 移除所有注释行、过滤空值行 KEY=、按变量名排序
    # 注意：不能 sort > 同一个文件，shell 会在管道启动前就截断输出文件，
    # 导致左侧 grep 读到空。先写临时文件再 mv 覆盖。
    grep -v '^[[:space:]]*#' "${env_file}" \
        | grep '=' \
        | awk -F'=' '$2 != ""' \
        | sort > "${env_file}.tmp" && mv -f "${env_file}.tmp" "${env_file}"

    kubectl create configmap -n "${namespace}" "${envfile_name}" \
        --from-env-file="${env_file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${CONFIG["AS_ENV_YAML_FILE"]}"
    success "AgentServer configuration rendered"
}

install_agentserver_patch() {
    if [[ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" || "${DEPLOY_VARS["APPLY_PATCH"]}" == "false" ]]; then
        return
    fi

    local runtime_port="${DEPLOY_VARS["AGENT_RUNTIME_NODE_PORT"]}"
    local yaml_file="${CONFIG["AS_ENV_YAML_FILE"]}"
    local json_file="${CONFIG["AS_JSON_FILE"]}"

    ensure_secret_configmap
    exec_cmd kubectl apply -f "${yaml_file}"

    # 下发agentserver服务配置
    info "Pushing AgentServer template"
    set +e
    local response=$(curl -s --max-time 20 -X POST "http://127.0.0.1:${runtime_port}/api/session/config_sync" \
        -H "Content-Type: application/json" \
        -d @"${json_file}" 2>&1)
    local rc=$?
    set -e

    if [ ${rc} -ne 0 ]; then
        error "Send AgentServer config failed (curl rc=${rc}): ${response}"
    fi
    success "AgentServer template pushed."
}

install_model_patch() {
    if [[ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" || "${DEPLOY_VARS["APPLY_PATCH"]}" == "false" ]]; then
        return
    fi

    local gw_node_port="${DEPLOY_VARS["GATEWAY_CONFIG_HTTP_NODE_PORT"]:-}"
    local bot_id="${MODEL_BOT_ID:-default}"
    local agent_template_id="${AGENT_TEMPLATE_ID:-default-agent}"
    local model_template_id="default-model"

    # 1. 下发 model_template（数据一：模型凭据）
    info "Pushing model template (template_id=${model_template_id})"
    set +e
    local response=$(curl -s --max-time 20 -X POST "http://127.0.0.1:${gw_node_port}/api/v1/model-templates" \
        -H "Content-Type: application/json" \
        -d "{
            \"template_id\":\"${model_template_id}\",
            \"template_name\":\"${DEPLOY_VARS["MODEL_NAME"]} default\",
            \"description\":\"auto-pushed\",
            \"model_type\":[\"default\"],
            \"api_base\":\"${DEPLOY_VARS["API_BASE"]}\",
            \"api_key\":\"${DEPLOY_VARS["API_KEY"]}\",
            \"model_id\":\"${DEPLOY_VARS["MODEL_NAME"]}\",
            \"model_provider\":\"${DEPLOY_VARS["MODEL_PROVIDER"]}\",
            \"timeout\":1800,
            \"retry_count\":3,
            \"enable_streaming\":true,
            \"enable_function_calling\":true,
            \"verify_ssl\":false,
            \"enabled\":true
        }" 2>&1)
    local rc=$?
    set -e
    if [ ${rc} -ne 0 ]; then
        error "Push model template failed (curl rc=${rc}): ${response}"
    fi
    success "Model template pushed (template_id=${model_template_id})"

    # 2. 下发 agent_template（数据二：template_ref 指向 model_template）
    info "Pushing agent template (template_id=${agent_template_id}, ref→${model_template_id})"
    set +e
    response=$(curl -s --max-time 20 -X POST "http://127.0.0.1:${gw_node_port}/api/v1/agent-templates" \
        -H "Content-Type: application/json" \
        -d "{\"template_id\":\"${agent_template_id}\",\"template_name\":\"default agent\",\"template_ref\":{\"default_model\":[\"${model_template_id}\"]},\"enabled\":true}" 2>&1)
    rc=$?
    set -e
    if [ ${rc} -ne 0 ]; then
        error "Push agent template failed (curl rc=${rc}): ${response}"
    fi
    success "Agent template pushed (template_id=${agent_template_id})"

    # 3. 下发 instance_agent_resource（数据三：resource_id=bot_id, ref_template_id 指向 agent_template）
    info "Pushing instance agent resource (resource_id=${bot_id}, ref→${agent_template_id})"
    set +e
    response=$(curl -s --max-time 20 -X POST "http://127.0.0.1:${gw_node_port}/api/v1/instance-agent-resources" \
        -H "Content-Type: application/json" \
        -d "{\"resource_id\":\"${bot_id}\",\"ref_template_id\":\"${agent_template_id}\",\"resource_name\":\"default agent\",\"match_expr\":[],\"enabled\":true}" 2>&1)
    rc=$?
    set -e
    if [ ${rc} -ne 0 ]; then
        error "Push instance agent resource failed (curl rc=${rc}): ${response}"
    fi
    success "Instance agent resource pushed (resource_id=${bot_id})"
}

install_patch()
{
    install_agentserver_patch
    install_model_patch
}

uninstall_patch() {
    if [[ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" || "${DEPLOY_VARS["APPLY_PATCH"]}" == "false" ]]; then
        return
    fi

    local yaml_file="${CONFIG["AS_ENV_YAML_FILE"]}"
    exec_cmd kubectl delete -f "${yaml_file}"  --ignore-not-found=true
}