#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_mysql() {
    local mysql_name="${DEPLOY_VARS["MYSQL_NAME"]}"
    local template_file="${CONFIG["MYSQL_TEMPLATE_FILE"]}"
    local file="${CONFIG["MYSQL_FILE"]}"

    ensure_available_port "MYSQL_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "statefulset" "${mysql_name}"
    success "MYSQL_NODE_PORT: ${DEPLOY_VARS["MYSQL_NODE_PORT"]}"
}

uninstall_mysql() {
    local mysql_name="${DEPLOY_VARS["MYSQL_NAME"]}"
    local file="${CONFIG["MYSQL_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${mysql_name}"
}


