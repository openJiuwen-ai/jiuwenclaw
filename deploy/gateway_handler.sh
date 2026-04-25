#!/usr/bin/env bash
set -euo >/dev/null 2>&1


gen_gateway_configmap() {
    local client_type="${DEPLOY_VARS["AGENT_CLIENT_TYPE"]}"

    info "AGENT_CLIENT_TYPE: ${client_type}"
    if [ "${client_type}" == "yuanrong_frontend" ]; then
        GATEWAY_CONFIG_TEMPLATE_FILE="${SCRIPT_DIR}/conf/config-yr.template.yaml"
    elif [ "${client_type}" == "runtime_orchestrator" ]; then
        GATEWAY_CONFIG_TEMPLATE_FILE="${SCRIPT_DIR}/conf/config-rt.template.yaml"
    else
        error "Unsupported AGENT_CLIENT_TYPE: ${client_type}"
    fi
    info "GATEWAY_CONFIG_TEMPLATE_FILE: ${GATEWAY_CONFIG_TEMPLATE_FILE}"

    render_config_template ${GATEWAY_CONFIG_TEMPLATE_FILE} ${GATEWAY_CONFIG_FILE} "DEPLOY_VARS"

    # Clear configuration
    yq eval '.channels.feishu_enterprise = {}' -i "${GATEWAY_CONFIG_FILE}"

    echo "${DEPLOY_VARS["FEISHU_BOTS"]}" | while read -r line; do
        # Skip empty lines
        [ -z "${line}" ] && continue

        # Split by colon
        IFS=':' read -r bot_name app_id app_secret <<< "${line}"

        # info "Adding bot: ${bot_name}"
        yq eval ".channels.feishu_enterprise.${bot_name}.app_id = \"${app_id}\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.app_secret = \"${app_secret}\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.encrypt_key = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.verification_token = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.allow_from = []" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.enable_streaming = true" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.chat_id = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.feishu_enterprise.${bot_name}.enabled = true" -i "${GATEWAY_CONFIG_FILE}"
    done

    success "Configuration generation completed: ${GATEWAY_CONFIG_FILE}"

    local file_name=$(basename "${GATEWAY_CONFIG_FILE}")
    info "Executing: kubectl create configmap ${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]} --from-file=${file_name}=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -"
    kubectl create configmap ${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]} --from-file=${file_name}=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -
}

deploy_gateway(){
    local client_type="${DEPLOY_VARS["AGENT_CLIENT_TYPE"]}"
    local deploy_template_file="${SCRIPT_DIR}/conf/deployment-${DEPLOY_VARS["MODE"]}.template.yaml"

    if [ "${client_type}" == "yuanrong_frontend" ]; then
        collect_oyr_info
    elif [ "${client_type}" == "runtime_orchestrator" ]; then
        deploy_template_file="${SCRIPT_DIR}/conf/deployment-rt.template.yaml"

        # create gateway rbac
        render_config_template "${GATEWAY_RBAC_TEMPLATE_FILE}" "${GATEWAY_RBAC_FILE}" "DEPLOY_VARS"
        exec_cmd kubectl apply -f ${GATEWAY_RBAC_FILE}
    fi

    gen_gateway_configmap

    # start gateway
    render_config_template ${deploy_template_file} ${GATEWAY_DEPLOYMENT_FILE} "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${GATEWAY_DEPLOYMENT_FILE}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"
}


uninstall_gateway() {
    # Clean up standalone gateway deployment if it exists
    delete_k8s_resource "deployment" "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"
    wait_pod_terminated "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"

    delete_k8s_resource "configmap" "${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"

    if [ "${DEPLOY_VARS["AGENT_CLIENT_TYPE"]}" == "runtime_orchestrator" ]; then
        exec_cmd kubectl delete -f ${GATEWAY_RBAC_FILE}
    fi
}
