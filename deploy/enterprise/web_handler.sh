#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_web_file() {
    local template_file="${CONFIG["WEB_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEB_FILE"]}"
    local enable_external_obs="${DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]}"
    local obs_url="${DEPLOY_VARS["MINIO_NAME"]}-headless.default:9000"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"

    if [ "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]}" == "true" ]; then
        local nodeport_name="${DEPLOY_VARS["WEB_NAME"]}-nodeport"
        yq eval 'select(.metadata.name != "'"${nodeport_name}"'")' -i "${file}"
    fi

    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]}" == "true" ]; then
        obs_url="${DEPLOY_VARS["OBS_URL"]}"
    fi

    yq eval '
    select(.kind == "Deployment").spec.template.spec.containers[0].env += [
        {
            "name": "JIUWENCLAW_MINIO_ENDPOINT",
            "value": "'"${obs_url}"'"
        },
        {
            "name": "JIUWENCLAW_MINIO_ACCESS_KEY",
            "value": "'"${DEPLOY_VARS["OBS_ACCESS_KEY"]}"'"
        },
        {
            "name": "JIUWENCLAW_MINIO_BUCKET",
            "value": "'"${DEPLOY_VARS["OBS_BUCKET"]}"'"
        },
        {
            "name": "JIUWENCLAW_MINIO_SECURE",
            "value": "'"${DEPLOY_VARS["OBS_SECURE"]}"'"
        },
        {
            "name": "JIUWENCLAW_MINIO_PUBLIC_BASE_URL",
            "value": "'"${DEPLOY_VARS["OBS_PUBLIC_BASE_URL"]}"'"
        },
        {
            "name": "JIUWENCLAW_MINIO_REGION",
            "value": "'"${DEPLOY_VARS["OBS_REGION"]}"'"
        }
    ]' -i "${file}"

    add_resource_if_set "WEB" "${file}"

    enable_dev_mode_if_needed ${file} web
}

render_web_files() {
    render_secret_configmap
    if [ "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]}" != "true" ]; then
        ensure_available_port "WEB_NODE_PORT"
    fi
    gen_web_file
}

deploy_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    ensure_secret_configmap
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${name}" "${namespace}"
    if [ "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]}" == "true" ]; then
        delete_k8s_resource "service" "${name}-nodeport" "${namespace}"
        success "User Web is available through Manager Web; standalone NodePort is disabled"
    else
        success "WEB_NODE_PORT: ${DEPLOY_VARS["WEB_NODE_PORT"]}"
    fi
}

uninstall_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    delete_k8s_resource "service" "${name}-nodeport" "${namespace}"
    wait_pod_terminated "${name}" "${namespace}"
    uninstall_secret_configmap
}
