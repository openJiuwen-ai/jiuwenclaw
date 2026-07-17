#!/usr/bin/env bash
set -euo >/dev/null 2>&1


render_secret_configmap() {
    local template_file="${CONFIG["SECRET_CM_TEMPLATE_FILE"]}"
    local file="${CONFIG["SECRET_CM_FILE"]}"
    local secret_keys=(
        "GATEWAY_DB_PASSWORD"
        "MANAGER_DB_PASSWORD"
        "REDIS_PASSWORD"
        "OBS_SECRET_KEY"
        "API_KEY"
    )
    for key in "${secret_keys[@]}"; do
        # 空值直接跳过，无需编码
        if [ -z "${DEPLOY_VARS[$key]:-}" ]; then
            continue
        fi

        # 已编码，跳过
        if echo -n "${DEPLOY_VARS[$key]}" | base64 -d >/dev/null 2>&1; then
            continue
        fi

        # 明文才编码
        DEPLOY_VARS[$key]=$(echo -n "${DEPLOY_VARS[$key]}" | base64 -w 0)
    done
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
}

ensure_secret_configmap() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["SECRET_CM_NAME"]}"
    local file="${CONFIG["SECRET_CM_FILE"]}"

    if check_k8s_resource_exists "secret" "${name}" "${namespace}"; then
        info "Secret ${namespace}/${name} exists, skip creating."
        return
    fi

    exec_cmd kubectl apply -f ${file}
}