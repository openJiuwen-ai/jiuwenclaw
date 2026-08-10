#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_redis_files() {
    local template_file="${CONFIG["REDIS_TEMPLATE_FILE"]}"
    local file="${CONFIG["REDIS_FILE"]}"

    ensure_available_port "REDIS_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
}

deploy_redis() {
    local redis_name="${DEPLOY_VARS["REDIS_NAME"]}"
    local file="${CONFIG["REDIS_FILE"]}"

    exec_cmd kubectl apply -f "${file}"
    wait_k8s_resource_ready "deployment" "${redis_name}" "default"
}

uninstall_redis() {
    local redis_name="${DEPLOY_VARS["REDIS_NAME"]}"
    local file="${CONFIG["REDIS_FILE"]}"

    exec_cmd kubectl delete -f "${file}" --ignore-not-found=true
    wait_pod_terminated "${redis_name}" "default"
}
