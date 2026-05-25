#!/usr/bin/env bash
set -euo >/dev/null 2>&1


gen_gateway_config_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local field_name="feishu"
    GATEWAY_CONFIG_TEMPLATE_FILE="${SCRIPT_DIR}/conf/gateway-config-${client_type}.template.yaml"

    info "AGENT_RUNTIME: ${client_type}"
    if [ "${client_type}" == "yuanrong" ]; then
        collect_oyr_info
        field_name="feishu_enterprise"
    fi
    info "GATEWAY_CONFIG_TEMPLATE_FILE: ${GATEWAY_CONFIG_TEMPLATE_FILE}"

    render_config_template ${GATEWAY_CONFIG_TEMPLATE_FILE} ${GATEWAY_CONFIG_FILE} "DEPLOY_VARS"

    # Clear configuration
    yq eval ".channels.${field_name} = {}" -i "${GATEWAY_CONFIG_FILE}"

    echo "${DEPLOY_VARS["FEISHU_BOTS"]}" | while read -r line; do
        # Skip empty lines
        [ -z "${line}" ] && continue

        # Split by colon
        IFS=':' read -r bot_name app_id app_secret <<< "${line}"

        # info "Adding bot: ${bot_name}"
        yq eval ".channels.${field_name}.${bot_name}.app_id = \"${app_id}\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.app_secret = \"${app_secret}\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.encrypt_key = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.verification_token = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.allow_from = []" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.enable_streaming = true" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.chat_id = \"\"" -i "${GATEWAY_CONFIG_FILE}"
        yq eval ".channels.${field_name}.${bot_name}.enabled = true" -i "${GATEWAY_CONFIG_FILE}"
    done

    success "ConfigMap file generation completed: ${GATEWAY_CONFIG_FILE}"
}

gen_gateway_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local mode="${DEPLOY_VARS["MODE"]}"
    local master_label=$(kubectl get node ${DEPLOY_VARS["MASTER_NODE_NAME"]} -o jsonpath='{.metadata.labels}' | grep -Eo 'node-role\.kubernetes\.io/(master|control-plane)' | head -1)

    # Render base template: adapt for yuanrong product mode
    render_config_template "${GATEWAY_TEMPLATE_FILE}" "${GATEWAY_FILE}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${GATEWAY_FILE} ${DEPLOY_VARS["GATEWAY_DEV_IMAGE"]}

    if [ "${client_type}" == "jiuwen" ]; then
        # Bind dedicated ServiceAccount to grant pod creation privileges
        yq eval 'select(.kind == "Deployment").spec.template.spec.serviceAccountName = "'"${DEPLOY_VARS["GATEWAY_SERVICE_ACCOUNT"]}"'"' -i "${GATEWAY_FILE}"

        # Override image with rt image
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].image = "'"${DEPLOY_VARS["GATEWAY_RT_IMAGE"]}"'"' -i "${GATEWAY_FILE}"

        # Inject environment variables via ConfigMap binding
        yq eval '
            select(.kind == "Deployment").spec.template.spec.containers[0].envFrom += [{
                "configMapRef": {
                    "name": "'"${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"'"
                }
            }]
        ' -i "${GATEWAY_FILE}"

        if [[ "${mode}" != "dev" ]] && if_any_nodes_gateway_label; then
            # Automatically create nodeSelector and set gateway=enable
            yq eval 'select(.kind == "Deployment").spec.template.spec.nodeSelector |= {"gateway": "enable"}' -i "${GATEWAY_FILE}"
        fi
    fi

    if [ "${mode}" == "dev" ] || [ "${client_type}" == "jiuwen" ]; then
         # Security context adaptation for root user
        yq eval 'select(.kind == "Deployment").spec.template.spec.securityContext.fsGroup = 0' -i "${GATEWAY_FILE}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation = true' -i "${GATEWAY_FILE}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsNonRoot = false' -i "${GATEWAY_FILE}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsUser = 0' -i "${GATEWAY_FILE}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsGroup = 0' -i "${GATEWAY_FILE}"

        # configuration file adaptation for root user
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts[0].mountPath = "/root/.jiuwenclaw/config/config.yaml"' -i "${GATEWAY_FILE}"
    fi

    success "Gateway file generation completed: ${GATEWAY_FILE}"
}

deploy_gateway() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"

    # create configMap from config.yaml
    gen_gateway_config_file

    info "Executing: kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -"
    kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -

    # create configMap from gateway.env
    if [ "${client_type}" == "jiuwen" ]; then
        render_config_template "${GATEWAY_ENV_TEMPLATE_FILE}" "${GATEWAY_ENV_FILE}" "DEPLOY_VARS"
        exec_cmd kubectl create configmap -n ${namespace} ${env_name} --from-env-file=${GATEWAY_ENV_FILE}
    fi

    # start gateway
    gen_gateway_file
    exec_cmd kubectl apply -f ${GATEWAY_FILE}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["GATEWAY_NAME"]}" "${namespace}"
}


uninstall_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local gateway_name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"

    exec_cmd kubectl delete -f ${GATEWAY_FILE}
    wait_pod_terminated "${gateway_name}" "${namespace}"
    delete_k8s_resource "configmap" "${conf_name}" "${namespace}"

    if [ "${DEPLOY_VARS["AGENT_RUNTIME"]}" == "jiuwen" ]; then
        delete_k8s_resource "configmap" "${env_name}" "${namespace}"
    fi
}
