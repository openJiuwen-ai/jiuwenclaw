#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
    local template_file="${CONFIG["MANAGER_TEMPLATE_FILE"]}"
    local file="${CONFIG["MANAGER_FILE"]}"
    local manager_web_template_file="${CONFIG["MANAGER_WEB_TEMPLATE_FILE"]}"
    local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"

    ensure_available_port "MANAGER_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file}
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${manager_name}" "${namespace}"
    success "MANAGER_NODE_PORT: ${DEPLOY_VARS["MANAGER_NODE_PORT"]}"

    ensure_available_port "MANAGER_WEB_NODE_PORT"
    render_config_template "${manager_web_template_file}" "${manager_web_file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${manager_web_file}
    wait_k8s_resource_ready "deployment" "${manager_web_name}" "${namespace}"
    success "MANAGER_WEB_NODE_PORT: ${DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]}"
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
    local file="${CONFIG["MANAGER_FILE"]}"
    local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"

    exec_cmd kubectl delete -f ${manager_web_file} --ignore-not-found=true
    wait_pod_terminated "${manager_web_name}" "${namespace}"
    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${manager_name}" "${namespace}"
}
