#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_gateway_env_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local env_template_file="${CONFIG["GATEWAY_ENV_TEMPLATE_FILE"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local env_file="${CONFIG["GATEWAY_ENV_FILE"]}"
    local deploy_mode="${DEPLOY_VARS["DEPLOYMENT_MODE"]}"

    if [ "${client_type}" != "jiuwen" ]; then
        return
    fi

    if [ "${deploy_mode}" == "active-standby" ]; then
         DEPLOY_VARS["GATEWAY_INSTANCE_ID"]="gateway-${namespace}"
    fi

    render_config_template "${env_template_file}" "${env_file}" "DEPLOY_VARS"

    echo "CLAW_MOUNT_TYPE=${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" >> "${env_file}"
    if [ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "nfs" ]; then
        echo "CLAW_NFS_SERVER=${DEPLOY_VARS["NFS_SERVER_ADDR"]}" >> "${env_file}"
        echo "CLAW_NFS_PATH=${DEPLOY_VARS["NFS_SHARE_PATH"]}/jiuwenclaw" >> "${env_file}" 
    else
        echo "CLAW_PVC=${DEPLOY_VARS["CLAW_PVC"]}" >> "${env_file}"
    fi

    # 移除所有注释行、过滤空值行 KEY=、按变量名排序
    # 注意：不能 sort > 同一个文件，shell 会在管道启动前就截断输出文件，
    # 导致左侧 grep 读到空。先写临时文件再 mv 覆盖。
    grep -v '^[[:space:]]*#' "${env_file}" \
        | grep '=' \
        | awk -F'=' '$2 != ""' \
        | sort > "${env_file}.tmp" && mv -f "${env_file}.tmp" "${env_file}"

    kubectl create configmap -n ${namespace} ${env_name} --from-env-file=${env_file} --dry-run=client -o yaml | yq eval 'del(.metadata.creationTimestamp)' > ${CONFIG_DIR}/gateway-env.configmap.yaml
}

gen_gateway_config_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local field_name="feishu"
    local template_file="${TEMPLATE_DIR}/gateway-config-${client_type}.template.yaml"
    local file="${CONFIG["GATEWAY_CONFIG_FILE"]}"
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local conf_file="${CONFIG["GATEWAY_CONFIG_FILE"]}"
    
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
    kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${conf_file}  --dry-run=client -o yaml | yq eval 'del(.metadata.creationTimestamp)' > ${CONFIG_DIR}/gateway-config.configmap.yaml
}

gen_gateway_file() {
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"
    local mode="${DEPLOY_VARS["MODE"]}"
    local template_file="${CONFIG["GATEWAY_TEMPLATE_FILE"]}"
    local file="${CONFIG["GATEWAY_FILE"]}"
    local enable_gw_lable="${DEPLOY_VARS["GATEWAY_SCHED_LABEL_ENABLED"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    if [ "${client_type}" != "jiuwen" ]; then
        success "Gateway file generation completed: ${file}"
        return
    fi

    enable_dev_mode_if_needed ${file}

    # No need to install packages
    if [[ "${mode}" == "dev" && -n "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ]]; then
        local claw_code="${DEPLOY_VARS["CLAW_CODE_PATH"]}"
        yq eval '.dependencies = {}' -i ${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/runtime_management_extension/extension.yaml
        yq eval '.dependencies = {}' -i ${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/manager_ws_client/extension.yaml
    fi

    add_resource_if_set "GATEWAY" "${file}"

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

    if [[ "${mode}" != "dev" &&  "${enable_gw_lable}" == "true" ]]; then
        # Automatically create nodeSelector and set gateway=enable
        yq eval 'select(.kind == "Deployment").spec.template.spec.nodeSelector |= {"gateway": "enable"}' -i "${file}"
    fi

    success "Gateway file generation completed: ${file}"
}

render_gateway_files() {
    local pvc_template_file="${CONFIG["CLAW_PVC_TEMPLATE_FILE"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"
    local mount_type="${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}"
    local is_external_pvc="${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}"
    local mode="${DEPLOY_VARS["MODE"]}"

    if [ "${mode}" == "dev" ]; then
        DEPLOY_VARS["AGENT_SERVER_HOME"]="/root"
    else
        DEPLOY_VARS["AGENT_SERVER_HOME"]="/home/app"
    fi

    render_secret_configmap
    gen_gateway_env_file
    gen_gateway_config_file
    if [ "${DEPLOY_VARS["DEPLOYMENT_MODE"]}" == "active-standby" ]; then
        DEPLOY_VARS["GATEWAY_REPLICAS"]="2"
    fi

    if [[ "${mount_type}" == "pvc" && "${is_external_pvc}" == "false" ]]; then
        render_config_template "${pvc_template_file}" "${pvc_file}" "DEPLOY_VARS"
    fi

    gen_gateway_file
}

deploy_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local env_file="${CONFIG["GATEWAY_ENV_FILE"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local conf_file="${CONFIG["GATEWAY_CONFIG_FILE"]}"
    local name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"
    local mount_type="${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}"
    local is_external_pvc="${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}"

    ensure_secret_configmap
    exec_cmd kubectl create configmap -n ${namespace} ${env_name} --from-env-file=${env_file}
    exec_cmd kubectl create configmap -n ${namespace} ${conf_name} --from-file=config.yaml=${conf_file}

    if [[ "${mount_type}" == "pvc" && "${is_external_pvc}" == "false" ]]; then
        exec_cmd kubectl apply -f ${pvc_file}
    fi

    exec_cmd kubectl apply -f ${gateway_file}
    wait_k8s_resource_ready "deployment" "${name}" "${namespace}"
}

uninstall_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local gateway_name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"
    local env_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_NAME"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"
    local mount_type="${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"

    # 先优雅停 Pod，再清理周边资源
    # gateway.yaml 含 ServiceAccount / Role / Deployment 等同文件资源。
    # 不可 kubectl delete -f 一次性删掉：SA 被删后 Pod 仍在 Terminating 窗口内，
    # in-cluster token 立即失效 → Gateway shutdown 删 Agent Pod 会 401。
    info "Deleting Gateway Deployment first (keep ServiceAccount for graceful shutdown)"
    exec_cmd kubectl delete deployment "${gateway_name}" -n "${namespace}" --ignore-not-found=true
    wait_pod_terminated "${gateway_name}" "${namespace}"

    # 兜底清理 gateway 动态创建的 agentserver pod
    # 正常情况下 gateway 在 graceful shutdown 时会由 ServiceManager.stop()
    # -> cleanup_all_agentserver_pods 自行删除这些 pod；但 gateway 一旦在
    # terminationGracePeriodSeconds 内被 SIGKILL（例如 busy-loop 阻塞了 shutdown），
    # 这些 pod 会变成孤儿残留：agentserver pod 无 ownerReference，且不在本脚本的
    # 清理范围内（MODULES 不含 agentserver）。此处按 gateway 创建 pod 时打下的
    # 固定 label 兜底删除，保证 down 后无残留。
    local orphan_pods
    orphan_pods=$(kubectl get pods -n "${namespace}" -l jiuwenclaw-component=agentserver -o name 2>/dev/null || true)
    if [ -n "${orphan_pods}" ]; then
        info "Cleaning up orphan agentserver pods created by gateway (label: jiuwenclaw-component=agentserver)"
        exec_cmd kubectl delete pod -n "${namespace}" -l jiuwenclaw-component=agentserver --ignore-not-found=true
    else
        info "No orphan agentserver pods to clean up."
    fi

    info "Deleting remaining Gateway resources (ServiceAccount, Role, Service, ...)"
    exec_cmd kubectl delete -f "${gateway_file}" --ignore-not-found=true
    delete_k8s_resource "configmap" "${conf_name}" "${namespace}"

    if [ "${DEPLOY_VARS["AGENT_RUNTIME"]}" == "jiuwen" ]; then
        delete_k8s_resource "configmap" "${env_name}" "${namespace}"
    fi

    if [[ "${mount_type}" == "pvc" && -z "${DEPLOY_VARS["CLAW_PVC"]:-}" ]]; then
        exec_cmd kubectl delete -f ${pvc_file}  --ignore-not-found=true
    fi
}
