#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_web_file() {
    local template_file="${CONFIG["WEB_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEB_FILE"]}"
    local enable_external_obs="${DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]}"
    local obs_url="${DEPLOY_VARS["MINIO_NAME"]}-headless.default:9000"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"

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

    if [ "${DEPLOY_VARS["IS_MOUNT_WEB_CODE"]}" == "true" ]; then
        enable_dev_mode_if_needed ${file}
    fi
}

render_web_files() {
    ensure_secret_configmap
    ensure_available_port "WEB_NODE_PORT"
    gen_web_file
}

deploy_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    ensure_secret_configmap
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${web_name}" "${namespace}"
    success "WEB_NODE_PORT: ${DEPLOY_VARS["WEB_NODE_PORT"]}"
}

uninstall_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${web_name}" "${namespace}"
}