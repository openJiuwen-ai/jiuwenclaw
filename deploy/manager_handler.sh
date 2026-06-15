#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local template_file="${CONFIG["MANAGER_TEMPLATE_FILE"]}"
    local file="${CONFIG["MANAGER_FILE"]}"

    ensure_available_port "MANAGER_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file}
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${manager_name}" "${namespace}"
    success "MANAGER_NODE_PORT: ${DEPLOY_VARS["MANAGER_NODE_PORT"]}"
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local file="${CONFIG["MANAGER_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${manager_name}" "${namespace}"
}
