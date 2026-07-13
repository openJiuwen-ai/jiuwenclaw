#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_web_files() {
    local template_file="${CONFIG["WEB_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEB_FILE"]}"
    local obs_type="${DEPLOY_VARS["OBS_TYPE"]}"

    ensure_available_port "WEB_NODE_PORT"

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
                "name": "JIUWENCLAW_MINIO_SECRET_KEY",
                "value": "'"${DEPLOY_VARS["MINIO_ROOT_PASSWORD"]}"'"
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
    add_resource_if_set "AUTH_SRV" "${file}"
    add_resource_if_set "MGR_SRV" "${file}"
    add_resource_if_set "USER_SRV" "${file}"
}

deploy_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local auth_name="${DEPLOY_VARS["AUTH_SRV_NAME"]}"
    local usr_srv_name="${DEPLOY_VARS["USER_SRV_NAME"]}"
    local mgr_srv_name="${DEPLOY_VARS["MGR_SRV_NAME"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${auth_name}" "${namespace}"
    wait_k8s_resource_ready "deployment" "${usr_srv_name}" "${namespace}"
    wait_k8s_resource_ready "deployment" "${mgr_srv_name}" "${namespace}"
    wait_k8s_resource_ready "deployment" "${web_name}" "${namespace}"
    success "WEB_NODE_PORT: ${DEPLOY_VARS["WEB_NODE_PORT"]}"
}


uninstall_web() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local auth_name="${DEPLOY_VARS["AUTH_SRV_NAME"]}"
    local usr_srv_name="${DEPLOY_VARS["USER_SRV_NAME"]}"
    local mgr_srv_name="${DEPLOY_VARS["MGR_SRV_NAME"]}"
    local web_name="${DEPLOY_VARS["WEB_NAME"]}"
    local file="${CONFIG["WEB_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${web_name}" "${namespace}"
    wait_pod_terminated "${mgr_srv_name}" "${namespace}"
    wait_pod_terminated "${usr_srv_name}" "${namespace}"
    wait_pod_terminated "${auth_name}" "${namespace}"
}
