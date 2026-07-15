#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_web_file() {
    local template_file="${CONFIG["WEB_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEB_FILE"]}"
    local obs_type="${DEPLOY_VARS["OBS_TYPE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    if [ "${obs_type}" == "minio" ]; then
        local minio_url="${DEPLOY_VARS["MINIO_NAME"]}-headless.default:9000"

        if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_MINIO"]}" == "true" ]; then
            minio_url="${DEPLOY_VARS["MINIO_URL"]}"
        fi
        yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].env += [
            {
                "name": "JIUWENCLAW_MINIO_ENDPOINT",
                "value": "'"${minio_url}"'"
            },
            {
                "name": "JIUWENCLAW_MINIO_ACCESS_KEY",
                "value": "'"${DEPLOY_VARS["MINIO_ROOT_USER"]}"'"
            },
            {
                "name": "JIUWENCLAW_MINIO_BUCKET",
                "value": "'"${DEPLOY_VARS["OBS_BUCKET"]}"'"
            },
            {
                "name": "JIUWENCLAW_MINIO_SECURE",
                "value": "'"${DEPLOY_VARS["MINIO_SECURE"]}"'"
            },
            {
                "name": "JIUWENCLAW_MINIO_PUBLIC_BASE_URL",
                "value": "'"${DEPLOY_VARS["OBS_PUBLIC_BASE_URL"]}"'"
            },
            {
                "name": "JIUWENCLAW_MINIO_REGION",
                "value": "'"${DEPLOY_VARS["MINIO_REGION"]}"'"
            }
        ]' -i "${file}"
    fi

    add_resource_if_set "WEB" "${file}"
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