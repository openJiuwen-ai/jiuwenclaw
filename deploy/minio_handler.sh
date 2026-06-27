#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_minio_files() {
    local template_file="${CONFIG["MINIO_TEMPLATE_FILE"]}"
    local file="${CONFIG["MINIO_FILE"]}"

    ensure_available_port "MINIO_API_NODE_PORT" "MINIO_CONSOLE_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
}

deploy_minio() {
    local minio_name="${DEPLOY_VARS["MINIO_NAME"]}"
    local file="${CONFIG["MINIO_FILE"]}"

    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "statefulset" "${minio_name}"
    success "MINIO_API_NODE_PORT: ${DEPLOY_VARS["MINIO_API_NODE_PORT"]}"
    success "MINIO_CONSOLE_NODE_PORT: ${DEPLOY_VARS["MINIO_CONSOLE_NODE_PORT"]}"
}

uninstall_minio() {
    local minio_name="${DEPLOY_VARS["MINIO_NAME"]}"
    local file="${CONFIG["MINIO_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${minio_name}"
}
