#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_manager_files() {
    local template_file="${CONFIG["MANAGER_TEMPLATE_FILE"]}"
    local file="${CONFIG["MANAGER_FILE"]}"

    ensure_available_port "MANAGER_NODE_PORT"
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

    add_resource_if_set "MANAGER" "${file}"
}

deploy_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_server_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_FILE"]}"

    
    exec_cmd kubectl apply -f ${manager_server_file}
    wait_k8s_resource_ready "deployment" "${manager_server_name}" "${namespace}"
    success "MANAGER_NODE_PORT: ${DEPLOY_VARS["MANAGER_NODE_PORT"]}"
}

uninstall_manager() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local manager_server_name="${DEPLOY_VARS["MANAGER_NAME"]}"
    local manager_server_file="${CONFIG["MANAGER_FILE"]}"

    exec_cmd kubectl delete -f ${manager_server_file} --ignore-not-found=true
    wait_pod_terminated "${manager_server_name}" "${namespace}"
}
