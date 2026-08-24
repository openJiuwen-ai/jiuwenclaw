#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_manager_server_file() {
    local template_file="${CONFIG["MANAGER_SERVER_TEMPLATE_FILE"]}"
    local file="${CONFIG["MANAGER_SERVER_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file} manager-server

    if [ "${DEPLOY_VARS["DB_TYPE"]}" == "postgresql" ]; then
        yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].env += [
            {
                "name": "MANAGER_PG_SCHEMA",
                "value": "'"${DEPLOY_VARS["MANAGER_PG_SCHEMA"]}"'"
            }
        ]' -i "${file}"
    fi

    add_resource_if_set "MANAGER_SERVER" "${file}"
}

gen_identity_file() {
    local template_file="${CONFIG["IDENTITY_TEMPLATE_FILE"]}"
    local file="${CONFIG["IDENTITY_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file} identity

    if [ "${DEPLOY_VARS["DB_TYPE"]}" == "postgresql" ]; then
        yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].env += [
            {
                "name": "IDENTITY_PG_SCHEMA",
                "value": "'"${DEPLOY_VARS["MANAGER_PG_SCHEMA"]}"'"
            }
        ]' -i "${file}"
    fi

    add_resource_if_set "IDENTITY" "${file}"
}

render_manager_files() {
    local is_up_web="${DEPLOY_VARS["IS_UP_MANAGER_WEB"]}"

    render_secret_configmap
    ensure_available_port "MANAGER_SERVER_NODE_PORT" "MANAGER_WEB_NODE_PORT"
    gen_manager_server_file
    gen_identity_file

    if [ "${is_up_web}" == "true" ]; then
        local manager_web_template_file="${CONFIG["MANAGER_WEB_TEMPLATE_FILE"]}"
        local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"

        render_config_template "${manager_web_template_file}" "${manager_web_file}" "DEPLOY_VARS"
        enable_dev_mode_if_needed "${manager_web_file}" manager-web
        add_resource_if_set "MANAGER_WEB" "${manager_web_file}"
    fi
}

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local is_up_web="${DEPLOY_VARS["IS_UP_MANAGER_WEB"]}"

    ensure_secret_configmap

    # manager-server
    local manager_server_name="${DEPLOY_VARS["MANAGER_SERVER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_SERVER_FILE"]}"
    exec_cmd kubectl apply -f ${manager_server_file}
    wait_k8s_resource_ready "deployment" "${manager_server_name}" "${namespace}"
    success "MANAGER_SERVER_NODE_PORT: ${DEPLOY_VARS["MANAGER_SERVER_NODE_PORT"]}"

    # identity（manager-web 的 /idp 反代到此）
    local identity_name="${DEPLOY_VARS["IDENTITY_NAME"]}"
    local identity_file="${CONFIG["IDENTITY_FILE"]}"
    exec_cmd kubectl apply -f ${identity_file}
    wait_k8s_resource_ready "deployment" "${identity_name}" "${namespace}"
    success "IDENTITY_REST_PORT: ${DEPLOY_VARS["IDENTITY_REST_PORT"]}"

    # manager-web
    if [ "${is_up_web}" == "true" ]; then
        local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
        local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"

        exec_cmd kubectl apply -f ${manager_web_file}
        wait_k8s_resource_ready "deployment" "${manager_web_name}" "${namespace}"
        success "MANAGER_WEB_NODE_PORT: ${DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]}"
    fi
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local is_up_web="${DEPLOY_VARS["IS_UP_MANAGER_WEB"]}"

    # 反序：manager-web → identity → manager-server
    if [ "${is_up_web}" == "true" ]; then
        local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
        local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"
        exec_cmd kubectl delete -f ${manager_web_file} --ignore-not-found=true
        wait_pod_terminated "${manager_web_name}" "${namespace}"
    fi

    local identity_name="${DEPLOY_VARS["IDENTITY_NAME"]}"
    local identity_file="${CONFIG["IDENTITY_FILE"]}"
    exec_cmd kubectl delete -f ${identity_file} --ignore-not-found=true
    wait_pod_terminated "${identity_name}" "${namespace}"

    local manager_server_name="${DEPLOY_VARS["MANAGER_SERVER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_SERVER_FILE"]}"
    exec_cmd kubectl delete -f ${manager_server_file} --ignore-not-found=true
    wait_pod_terminated "${manager_server_name}" "${namespace}"

    uninstall_secret_configmap
}
