#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_postgresql_files() {
    local template_file="${CONFIG["POSTGRESQL_TEMPLATE_FILE"]}"
    local file="${CONFIG["POSTGRESQL_FILE"]}"

    ensure_available_port "POSTGRESQL_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    add_resource_if_set "POSTGRESQL" "${file}"
}

deploy_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRESQL_NAME"]}"
    local file="${CONFIG["POSTGRESQL_FILE"]}"
    
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "statefulset" "${pg_name}"
    success "POSTGRESQL_NODE_PORT: ${DEPLOY_VARS["POSTGRESQL_NODE_PORT"]}"
}

uninstall_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRESQL_NAME"]}"
    local file="${CONFIG["POSTGRESQL_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${pg_name}"
}
