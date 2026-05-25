#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_mysql() {
    local mysql_name="${DEPLOY_VARS["MYSQL_NAME"]}"

    render_config_template "${MYSQL_TEMPLATE_FILE}" "${MYSQL_FILE}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${MYSQL_FILE}
    wait_k8s_resource_ready "statefulset" "${mysql_name}"
}

uninstall_mysql() {
    local mysql_name="${DEPLOY_VARS["MYSQL_NAME"]}"

    exec_cmd kubectl delete -f ${MYSQL_FILE}
    wait_pod_terminated "${mysql_name}"
}


