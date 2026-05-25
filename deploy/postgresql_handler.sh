#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRES_NAME"]}"

    render_config_template "${POSTGRES_TEMPLATE_FILE}" "${POSTGRES_FILE}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${POSTGRES_FILE}
    wait_k8s_resource_ready "statefulset" "${pg_name}"
}

uninstall_postgresql() {
    local pg_name="${DEPLOY_VARS["POSTGRES_NAME"]}"

    exec_cmd kubectl delete -f ${POSTGRES_FILE}
    wait_pod_terminated "${pg_name}"
}
