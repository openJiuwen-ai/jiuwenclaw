#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local template_file="${CONFIG["WEB_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEB_FILE"]}"

    ensure_available_port "WEB_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${web_name}" "${namespace}"
    success "WEB_NODE_PORT: ${DEPLOY_VARS["WEB_NODE_PORT"]}"
}

uninstall_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${web_name}" "${namespace}"
}