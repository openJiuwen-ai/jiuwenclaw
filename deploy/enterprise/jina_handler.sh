#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_jina_files() {
    local template_file="${CONFIG["JINA_TEMPLATE_FILE"]}"
    local file="${CONFIG["JINA_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
}

deploy_jina() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["JINA_NAME"]}"
    local file="${CONFIG["JINA_FILE"]}"

    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${name}-reader"
    wait_k8s_resource_ready "deployment" "${name}-cache-proxy"
}

uninstall_jina() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["JINA_NAME"]}"
    local file="${CONFIG["JINA_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${name}-reader"
    wait_pod_terminated "${name}-cache-proxy"
}