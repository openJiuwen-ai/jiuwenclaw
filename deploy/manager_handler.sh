#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_manager_server_file() {
    local template_file="${CONFIG["MANAGER_SERVER_TEMPLATE_FILE"]}"
    local file="${CONFIG["MANAGER_SERVER_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file}

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

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_server_name="${DEPLOY_VARS["MANAGER_SERVER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_SERVER_FILE"]}"
    local is_up_web="${DEPLOY_VARS["IS_UP_MANAGER_WEB"]}"

    ensure_available_port "MANAGER_SERVER_NODE_PORT"
    gen_manager_server_file
    exec_cmd kubectl apply -f ${manager_server_file}
    wait_k8s_resource_ready "deployment" "${manager_server_name}" "${namespace}"
    success "MANAGER_SERVER_NODE_PORT: ${DEPLOY_VARS["MANAGER_SERVER_NODE_PORT"]}"

    if [ "${is_up_web}" == "true" ]; then
        local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
        local manager_web_template_file="${CONFIG["MANAGER_WEB_TEMPLATE_FILE"]}"
        local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"

        ensure_available_port "MANAGER_WEB_NODE_PORT"
        render_config_template "${manager_web_template_file}" "${manager_web_file}" "DEPLOY_VARS"
        add_resource_if_set "MANAGER_WEB" "${manager_web_file}"
        exec_cmd kubectl apply -f ${manager_web_file}
        wait_k8s_resource_ready "deployment" "${manager_web_name}" "${namespace}"
        success "MANAGER_WEB_NODE_PORT: ${DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]}"
    fi
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_server_name="${DEPLOY_VARS["MANAGER_SERVER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_SERVER_FILE"]}"
    local is_up_web="${DEPLOY_VARS["IS_UP_MANAGER_WEB"]}"

    if [ "${is_up_web}" == "true" ]; then
        local manager_web_name="${DEPLOY_VARS["MANAGER_WEB_NAME"]}"
        local manager_web_file="${CONFIG["MANAGER_WEB_FILE"]}"
        exec_cmd kubectl delete -f ${manager_web_file} --ignore-not-found=true
        wait_pod_terminated "${manager_web_name}" "${namespace}"
    fi
    exec_cmd kubectl delete -f ${manager_server_file} --ignore-not-found=true
    wait_pod_terminated "${manager_server_name}" "${namespace}"
}
