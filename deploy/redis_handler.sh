#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_redis() {
    local redis_name="${DEPLOY_VARS["REDIS_NAME"]}"
    local template_file="${CONFIG["REDIS_TEMPLATE_FILE"]}"
    local file="${CONFIG["REDIS_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f "${file}"
    wait_k8s_resource_ready "deployment" "${redis_name}" "default"
}

uninstall_redis() {
    local redis_name="${DEPLOY_VARS["REDIS_NAME"]}"
    local file="${CONFIG["REDIS_FILE"]}"

    exec_cmd kubectl delete -f "${file}"
    wait_pod_terminated "${redis_name}" "default"
}
