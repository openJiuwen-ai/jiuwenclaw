#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"

    render_config_template "${MANAGER_TEMPLATE_FILE}" "${MANAGER_FILE}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${MANAGER_FILE}
    wait_k8s_resource_ready "deployment" "${manager_name}" "${namespace}"
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_name="${DEPLOY_VARS["MANAGER_NAME"]}"

    exec_cmd kubectl delete -f ${MANAGER_FILE}
    wait_pod_terminated "${manager_name}" "${namespace}"
}
