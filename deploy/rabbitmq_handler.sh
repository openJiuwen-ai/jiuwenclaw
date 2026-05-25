#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_rabbitmq() {
    local rabbitmq_name="${DEPLOY_VARS["RABBITMQ_NAME"]}"
    local user="${DEPLOY_VARS["RABBITMQ_USER"]}"
    local password="${DEPLOY_VARS["RABBITMQ_PASSWORD"]}"

    DEPLOY_VARS["RABBITMQ_USER"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_USER"]}" | base64)
    DEPLOY_VARS["RABBITMQ_PASSWORD"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_PASSWORD"]}" | base64)
    render_config_template "${RABBITMQ_TEMPLATE_FILE}" "${RABBITMQ_FILE}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${RABBITMQ_FILE}
    wait_k8s_resource_ready "statefulset" "${rabbitmq_name}"
}

uninstall_rabbitmq() {
    local rabbitmq_name="${DEPLOY_VARS["RABBITMQ_NAME"]}"

    exec_cmd kubectl delete -f ${RABBITMQ_FILE}
    wait_pod_terminated "${rabbitmq_name}"
}


