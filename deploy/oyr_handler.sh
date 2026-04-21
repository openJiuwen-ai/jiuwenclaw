#!/usr/bin/env bash
set -euo >/dev/null 2>&1

config_value_file() {
    local oyr_values_file="${OYL_REPO_DIR}/values.yaml"
    if [ ! -f "${oyr_values_file}" ]; then
        error "${oyr_values_file} not found!"
    fi

    info "Starting to modify ${oyr_values_file}..."

    info "Configuring metaService resources..."
    sed -i '/^    metaService:/,/^    functionMaster:/ s/\(cpu:\s*"\)2\("\)/\1500m\2/' "${oyr_values_file}"
    sed -i '/^    metaService:/,/^    functionMaster:/ s/\(memory:\s*"\)6Gi\("\)/\11Gi\2/' "${oyr_values_file}"
    sed -i '/^    metaService:/,/^    functionMaster:/ s/\(cpu:\s*"\)1\("\)/\1500m\2/' "${oyr_values_file}"
    sed -i '/^    metaService:/,/^    functionMaster:/ s/\(memory:\s*"\)3Gi\("\)/\11Gi\2/' "${oyr_values_file}"

    info "Setting functionScheduler replicas..."
    sed -i 's/\(functionScheduler:\s*\)2/\11/' "${oyr_values_file}"

    info "Disabling rate limit..."
    sed -i '/^    invokeRateLimit:/,/^    msgSize:/ s/\(enable:\s*\)true/\1false/' "${oyr_values_file}"


    info "Updating storage configuration..."
    sed -i 's/\(s3AccessKey:\s*"\)root\("\)/\1minioadmin\2/' "${oyr_values_file}"
    sed -i 's/s3SecretKey: ""/s3SecretKey: "minioadmin"/' "${oyr_values_file}"
}

config_meta_cm_file(){
    local oyr_meta_cm_file="${OYL_REPO_DIR}/templates/meta-service/meta-service-configmap.yaml"

    if [ ! -f "${oyr_meta_cm_file}" ]; then
        error "${oyr_meta_cm_file} not found!"
    fi

    info "Adding disableSync: true to meta-service configmap..."
    sed -i '/"disableSync": true/d' "${oyr_meta_cm_file}"
    sed -i 's/\("authType": "{{ .Values.global.etcdManagement.authType }}"\)/\1,\n        "disableSync": true/' "${oyr_meta_cm_file}"
}

config_comp_cm_file(){
    local oyr_comp_cm_file="${OYL_REPO_DIR}/templates/common/components-toml-configmap.yaml"
    if [ ! -f "${oyr_comp_cm_file}" ]; then
        error "${oyr_comp_cm_file} not found!"
    fi

    info "Adding enableEvent=\"true\" for frontend driverMode=\"true\"..."
    sed -i '/enableEvent="true"/d' "${oyr_comp_cm_file}"
    sed -i '/faasfrontend/,/driverMode="true"/ s/\(driverMode="true"\)/\1\n    enableEvent="true"/' "${oyr_comp_cm_file}"
}

config_oyr() {
    config_value_file
    config_meta_cm_file
    config_comp_cm_file
}

configure_docker_insecure_registry_on_all_nodes() {
    local docker_daemon_file="/etc/docker/daemon.json"

    sync_file_to_workers "${SCRIPT_DIR}/update_docker_registry.py"
    exec_on_all_nodes '
        echo "Configuring insecure registries in '"${docker_daemon_file}"'"
        python3 '"${SCRIPT_DIR}"'/update_docker_registry.py '"${docker_daemon_file}"'
        EXIT_CODE=$?

        if [ ${EXIT_CODE} -eq 0 ]; then
            echo "Reloading systemd and restarting Docker..."
            systemctl daemon-reload
            systemctl restart docker

            if ! systemctl is-active --quiet docker; then
                echo "❌ Docker failed to start!"
                exit 1
            fi
            echo "✅ Docker restarted successfully"
            exit 0

        elif [ ${EXIT_CODE} -eq 1 ]; then
            echo "✅ Registries already configured, skip Docker restart"
            exit 0

        else
            echo "❌ Failed to update docker registry config"
            exit 1
        fi
    '
}

wait_oyr_ready() {
    info "Waiting for all openyuanrong resources to be ready..."
    for name in "${!OYR_COMPONENTS[@]}"; do
        wait_k8s_resource_ready "${OYR_COMPONENTS[${name}]}" "${name}"
    done
    success "All openyuanrong resources are ready"
}

collect_oyr_info() {
    META_PORT=$(kubectl get svc meta-service \
        -o jsonpath='{.spec.ports[0].nodePort}')
    info "META_PORT: ${META_PORT}"

    DEPLOY_VARS["FRONTEND_PORT"]=$(kubectl get svc frontend-lb \
        -o jsonpath='{.spec.ports[0].port}')
    info "FRONTEND_PORT: ${DEPLOY_VARS["FRONTEND_PORT"]}"
}


install_oyr() {
    # Configure the openYuanrong whitelist image repository address
    configure_docker_insecure_registry_on_all_nodes

    cd ${SCRIPT_DIR}
    # Add the openYuanrong Helm repository
    exec_cmd helm repo add ${OYL_REPO_NAME} ${OYL_REPO_URL}
    exec_cmd helm repo update

    # Pull the openyuanrong code
    exec_cmd rm -rf ${OYL_REPO_DIR}
    exec_cmd helm pull --untar ${OYL_REPO_NAME}/${OYL_CHART_NAME} --version ${OYL_CHART_VERSION}
    
    cd ${OYL_REPO_DIR}
    # Modify the openyuanrong configuration
    config_oyr

    # Deploy openYuanrong
    exec_cmd helm install ${OYL_CHART_NAME} .
    success "Install openYuanrong helm repository"

    # Wait for all openYuanrong Kubernetes resources to be ready
    wait_oyr_ready
    collect_oyr_info
}

# Create function Pod resource pool
create_func_pool() {
    local pool_id=${DEPLOY_VARS["POOL_ID"]}
    local master_ip=${DEPLOY_VARS["MASTER_NODE_IP"]}
    local claw_deployment_name="function-agent-${pool_id}"

    sync_file_to_workers ${REG_FUNC_FILE}

    # Create function Pod resource pool
    DEPLOY_VARS["REG_FUNC_DIR"]=$(dirname "${REG_FUNC_FILE}")
    render_config_template ${POOL_TEMPLATE_FILE} ${POOL_FILE} "DEPLOY_VARS"

    info "curl -X POST -H \"Content-Type: application/json\" http://${master_ip}:${META_PORT}/serverless/v1/podpools -d @${POOL_FILE}"
    res=$(curl -X POST -H "Content-Type: application/json" http://${master_ip}:${META_PORT}/serverless/v1/podpools -d @${POOL_FILE})
    info "Result: ${res}"

    # Parse response code
    code=$(echo "$res" | jq -r '.code' 2>/dev/null)
    if [[ $? -ne 0 || "${code}" != "0" ]]; then
        error "Failed to create function pod resource pool"
    fi
    success "Function pod resource pool created successfully!"

    wait_k8s_resource_ready "deployment" "${claw_deployment_name}"
}

# Register openyuanrong function
register_oyr_func() {
    local master_ip=${DEPLOY_VARS["MASTER_NODE_IP"]}

    # Render function metadata config from template
    render_config_template ${CLAW_META_TEMPLATE_FILE} ${CLAW_META_FILE} "DEPLOY_VARS"

    info "Executing: curl -X POST -H \"Content-Type: application/json\" -H \"x-storage-type: local\" http://${master_ip}:${META_PORT}/serverless/v1/functions -d @${CLAW_META_FILE}"
    res=$(curl -X POST -H "Content-Type: application/json" -H "x-storage-type: local" http://${master_ip}:${META_PORT}/serverless/v1/functions -d @${CLAW_META_FILE})
    info "Result: ${res}"

    # Parse response code with jq
    code=$(echo "$res" | jq -r '.code' 2>/dev/null)
    if [[ $? -ne 0 || "${code}" != "0" ]]; then
        error "Failed to create serverless function"
    fi

    # Extract function id from response
    DEPLOY_VARS["FUNCTION_ID"]=$(echo "${res}" | jq -r '.function.id')
    success "Serverless function created successfully! function_id: ${DEPLOY_VARS["FUNCTION_ID"]}"
}

gen_gateway_config_file() {
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

    info "Configuration generation completed: ${GATEWAY_CONFIG_FILE}"
}

deploy_claw_gateway(){
    local file_name=$(basename "${GATEWAY_CONFIG_FILE}")
    local deploy_template_file="${SCRIPT_DIR}/conf/deployment-${DEPLOY_VARS["MODE"]}.template.yaml"

    render_config_template ${deploy_template_file} ${GATEWAY_DEPLOYMENT_FILE} "DEPLOY_VARS"
    gen_gateway_config_file

    info "Executing: kubectl create configmap ${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]} --from-file=${file_name}=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -"
    kubectl create configmap ${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]} --from-file=${file_name}=${GATEWAY_CONFIG_FILE} --dry-run=client -o yaml | kubectl apply -f -

    # Start gateway
    exec_cmd kubectl apply -f ${GATEWAY_DEPLOYMENT_FILE}

    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"

}

deploy_oyr() {  
    install_oyr
    create_func_pool
    register_oyr_func
    deploy_claw_gateway
}


wait_oyr_terminated() {
    info "Waiting for all openyuanrong resources to be terminated..."
    for name in "${!OYR_COMPONENTS[@]}"; do
        wait_pod_terminated "${name}"
    done
}

uninstall_oyr() {
    info "Starting to uninstall openyuanrong..."

    # unistall if Helm release exists
    if helm list --filter "^${OYL_CHART_NAME}$" | grep -q "${OYL_CHART_NAME}"; then
        exec_cmd helm uninstall ${OYL_CHART_NAME}

        # Wait until all resources and associated pods are fully terminated
        wait_oyr_terminated
    else
        info "Helm release ${OYL_CHART_NAME} not found, skipping uninstall."
    fi

    # Clean up standalone gateway deployment if it exists
    delete_k8s_resource "deployment" "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"
    wait_pod_terminated "${DEPLOY_VARS["GATEWAY_DEPLOYMENT_NAME"]}"

    delete_k8s_resource "configmap" "${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"

    success "Uninstall ${OYL_CHART_NAME} completed successfully."
}