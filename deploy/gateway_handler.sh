#!/usr/bin/env bash
set -euo >/dev/null 2>&1


gen_gateway_config_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local field_name="feishu"
    local template_file="${SCRIPT_DIR}/conf/gateway-config-${client_type}.template.yaml"
    local file="${CONFIG["GATEWAY_CONFIG_FILE"]}"

    info "AGENT_RUNTIME: ${client_type}"
    if [ "${client_type}" == "yuanrong" ]; then
        collect_oyr_info
        field_name="feishu_enterprise"
    fi
    info "GATEWAY_CONFIG_TEMPLATE_FILE: ${template_file}"

    render_config_template ${template_file} ${file} "DEPLOY_VARS"

    # Clear configuration
    yq eval ".channels.${field_name} = {}" -i "${file}"

    echo "${DEPLOY_VARS["FEISHU_BOTS"]}" | while read -r line; do
        # Skip empty lines
        [ -z "${line}" ] && continue

        # Split by colon
        IFS=':' read -r bot_name app_id app_secret <<< "${line}"

        # info "Adding bot: ${bot_name}"
        yq eval ".channels.${field_name}.${bot_name}.app_id = \"${app_id}\"" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.app_secret = \"${app_secret}\"" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.encrypt_key = \"\"" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.verification_token = \"\"" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.allow_from = []" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.enable_streaming = true" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.chat_id = \"\"" -i "${file}"
        yq eval ".channels.${field_name}.${bot_name}.enabled = true" -i "${file}"
    done

    success "ConfigMap file generation completed: ${file}"
}

gen_gateway_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local mode="${DEPLOY_VARS["MODE"]}"
    local template_file="${CONFIG["GATEWAY_TEMPLATE_FILE"]}"
    local file="${CONFIG["GATEWAY_FILE"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    if [ "${client_type}" != "jiuwen" ]; then
        success "Gateway file generation completed: ${file}"
        return
    fi

    enable_dev_mode_if_needed ${file}

    if [ -n "${DEPLOY_VARS["GATEWAY_CPU_REQUEST"]:-}" ]; then
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].resources.requests.cpu = "'"${DEPLOY_VARS["GATEWAY_CPU_REQUEST"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["GATEWAY_MEMORY_REQUEST"]:-}" ]; then
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].resources.requests.memory = "'"${DEPLOY_VARS["GATEWAY_MEMORY_REQUEST"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["GATEWAY_CPU_LIMIT"]:-}" ]; then
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].resources.limits.cpu = "'"${DEPLOY_VARS["GATEWAY_CPU_LIMIT"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["GATEWAY_MEMORY_LIMIT"]:-}" ]; then
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].resources.limits.memory = "'"${DEPLOY_VARS["GATEWAY_MEMORY_LIMIT"]}"'"' -i "${file}"
    fi

    # Bind dedicated ServiceAccount to grant pod creation privileges
    yq eval 'select(.kind == "Deployment").spec.template.spec.serviceAccountName = "'"${DEPLOY_VARS["GATEWAY_SERVICE_ACCOUNT"]}"'"' -i "${file}"

    # Inject environment variables via ConfigMap binding
    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].envFrom += [{
            "configMapRef": {
                "name": "'"${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"'"
            }
        }]
    ' -i "${file}"

    if [[ "${mode}" != "dev" ]] && if_any_nodes_gateway_label; then
        # Automatically create nodeSelector and set gateway=enable
        yq eval 'select(.kind == "Deployment").spec.template.spec.nodeSelector |= {"gateway": "enable"}' -i "${file}"
    fi
    success "Gateway file generation completed: ${file}"
}

create_gateway_env_configmap() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local mode="${DEPLOY_VARS["MODE"]}"
    local env_template_file="${CONFIG["GATEWAY_ENV_TEMPLATE_FILE"]}"
    local env_file="${CONFIG["GATEWAY_ENV_FILE"]}"

    if [ "${client_type}" != "jiuwen" ]; then
        return
    fi

    render_config_template "${env_template_file}" "${env_file}" "DEPLOY_VARS"
    exec_cmd kubectl create configmap -n ${namespace} ${env_name} --from-env-file=${env_file}
}

deploy_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local conf_file="${CONFIG["GATEWAY_CONFIG_FILE"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"

    # create configMap from config.yaml
    gen_gateway_config_file
    info "Executing: kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${conf_file} --dry-run=client -o yaml | kubectl apply -f -"
    kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${conf_file} --dry-run=client -o yaml | kubectl apply -f -

    # create configMap from gateway.env
    create_gateway_env_configmap

    # start gateway
    find_available_port "GATEWAY_NODE_PORT"
    gen_gateway_file
    exec_cmd kubectl apply -f ${gateway_file}
    wait_k8s_resource_ready "deployment" "${name}" "${namespace}"
    success "GATEWAY_NODE_PORT: ${DEPLOY_VARS["GATEWAY_NODE_PORT"]}"
}

uninstall_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local gateway_name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"

    exec_cmd kubectl delete -f ${gateway_file}
    wait_pod_terminated "${gateway_name}" "${namespace}"
    delete_k8s_resource "configmap" "${conf_name}" "${namespace}"

    if [ "${DEPLOY_VARS["AGENT_RUNTIME"]}" == "jiuwen" ]; then
        delete_k8s_resource "configmap" "${env_name}" "${namespace}"
    fi
}
