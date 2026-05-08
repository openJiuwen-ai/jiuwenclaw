#!/usr/bin/env bash
set -euo >/dev/null 2>&1

deploy_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"

    render_config_template "${WEB_DEPLOYMENT_TEMPLATE_FILE}" "${WEB_DEPLOYMENT_FILE}" "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${WEB_DEPLOYMENT_FILE}
    wait_k8s_resource_ready "deployment" "${web_name}"  "${namespace}"
}

uninstall_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"

    delete_k8s_resource "deployment" "${web_name}" "${namespace}"
    wait_pod_terminated "${web_name}" "${namespace}"
}