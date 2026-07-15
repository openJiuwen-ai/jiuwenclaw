#!/usr/bin/env bash
set -euo >/dev/null 2>&1


render_secret_configmap() {
    local template_file="${CONFIG["SECRET_CM_TEMPLATE_FILE"]}"
    local file="${CONFIG["SECRET_CM_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
}

ensure_secret_configmap() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["SECRET_CM_NAME"]}"
    local file="${CONFIG["SECRET_CM_FILE"]}"

    if check_k8s_resource_exists "configmap" "${name}" "${namespace}"; then
        info "ConfigMap ${namespace}/${name} exists, skip creating."
        return
    fi

    exec_cmd kubectl apply -f ${file}
}