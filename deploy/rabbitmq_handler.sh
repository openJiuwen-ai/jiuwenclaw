#!/usr/bin/env bash
set -euo >/dev/null 2>&1

process_rabbitmq_vars() {
    DEPLOY_VARS["RABBITMQ_USER"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_USER"]}" | base64)
    DEPLOY_VARS["RABBITMQ_PASSWORD"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_PASSWORD"]}" | base64)
    ensure_available_port "RABBITMQ_AMQ_NODE_PORT" "RABBITMQ_MGR_NODE_PORT"
}

deploy_rabbitmq() {
    local rabbitmq_name="${DEPLOY_VARS["RABBITMQ_NAME"]}"
    local template_file="${CONFIG["RABBITMQ_TEMPLATE_FILE"]}"
    local file="${CONFIG["RABBITMQ_FILE"]}"

    process_rabbitmq_vars
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "statefulset" "${rabbitmq_name}"
    success "RABBITMQ_AMQ_NODE_PORT: ${DEPLOY_VARS["RABBITMQ_AMQ_NODE_PORT"]}"
    success "RABBITMQ_MGR_NODE_PORT: ${DEPLOY_VARS["RABBITMQ_MGR_NODE_PORT"]}"
}

uninstall_rabbitmq() {
    local rabbitmq_name="${DEPLOY_VARS["RABBITMQ_NAME"]}"
    local file="${CONFIG["RABBITMQ_FILE"]}"

    exec_cmd kubectl delete -f ${file}
    wait_pod_terminated "${rabbitmq_name}"
}


