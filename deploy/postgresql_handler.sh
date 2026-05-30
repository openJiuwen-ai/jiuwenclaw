#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRES_NAME"]}"
    local template_file="${CONFIG["POSTGRES_TEMPLATE_FILE"]}"
    local file="${CONFIG["POSTGRES_FILE"]}"

    ensure_available_port "POSTGRES_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "statefulset" "${pg_name}"
    success "POSTGRES_NODE_PORT: ${DEPLOY_VARS["POSTGRES_NODE_PORT"]}"
}

uninstall_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRES_NAME"]}"
    local file="${CONFIG["POSTGRES_FILE"]}"

    exec_cmd kubectl delete -f ${file}
    wait_pod_terminated "${pg_name}"
}
