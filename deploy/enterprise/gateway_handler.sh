#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_gateway_env_file() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local env_template_file="${CONFIG["GATEWAY_ENV_TEMPLATE_FILE"]}"
    local envfile_name="${DEPLOY_VARS["GATEWAY_ENV_FILE_CM_NAME"]}"
    local env_file="${CONFIG["GATEWAY_ENV_FILE"]}"
    local yaml_file="${CONFIG["GATEWAY_ENV_YAML_FILE"]}"

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

    kubectl create configmap -n "${namespace}" "${envfile_name}" \
        --from-file=.env="${env_file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${yaml_file}"
}

gen_gateway_config_file() {
    local field_name="feishu"
    local template_file="${CONFIG["GATEWAY_CONFIG_TEMPLATE_FILE"]}"
    local file="${CONFIG["GATEWAY_CONFIG_FILE"]}"
    local yaml_file="${CONFIG["GATEWAY_CONFIG_YAML_FILE"]}"
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local conf_name="${DEPLOY_VARS["GATEWAY_CONFIG_MAP_NAME"]}"

    info "GATEWAY_CONFIG_TEMPLATE_FILE: ${template_file}"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"

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

    success "Gateway config rendered: ${file}; ConfigMap yaml: ${yaml_file}"
    kubectl create configmap -n "${namespace}" "${conf_name}" \
        --from-file=config.yaml="${file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${yaml_file}"
}

gen_gateway_file() {
    local mode="${DEPLOY_VARS["MODE"]}"
    local template_file="${CONFIG["GATEWAY_TEMPLATE_FILE"]}"
    local file="${CONFIG["GATEWAY_FILE"]}"
    local enable_gw_lable="${DEPLOY_VARS["GATEWAY_SCHED_LABEL_ENABLED"]}"

    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed "${file}" gateway

    # No need to install packages
    if [[ "${mode}" == "dev" && -n "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ]]; then
        local claw_code="${DEPLOY_VARS["CLAW_CODE_PATH"]}"
        yq eval '.dependencies = {}' -i "${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/runtime_management_extension/extension.yaml"
        yq eval '.dependencies = {}' -i "${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/manager_config_receiver/extension.yaml"
    fi

    add_resource_if_set "GATEWAY" "${file}"

    if [[ "${mode}" != "dev" && "${enable_gw_lable}" == "true" ]]; then
        # Automatically create nodeSelector and set gateway=enable
        yq eval 'select(.kind == "Deployment").spec.template.spec.nodeSelector |= {"gateway": "enable"}' -i "${file}"
    fi

    if [[ "${DEPLOY_VARS["APPLY_PATCH"]}" != "true" ]]; then
        yq eval-all -i 'select(.kind != "Service" or .spec.type != "NodePort")' "${file}"
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

    if [[ "${mount_type}" == "pvc" && "${is_external_pvc}" == "false" ]]; then
        render_config_template "${pvc_template_file}" "${pvc_file}" "DEPLOY_VARS"
    fi

    ensure_available_port "GATEWAY_CONFIG_HTTP_NODE_PORT"
    gen_gateway_file
}

deploy_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local env_yaml_file="${CONFIG["GATEWAY_ENV_YAML_FILE"]}"
    local conf_yaml_file="${CONFIG["GATEWAY_CONFIG_YAML_FILE"]}"
    local name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"
    local mount_type="${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}"
    local is_external_pvc="${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}"

    ensure_secret_configmap
    # 使用 apply 保证重复部署幂等：ConfigMap 已存在时更新内容，不因 create 冲突失败。
    exec_cmd kubectl apply -f "${env_yaml_file}"
    exec_cmd kubectl apply -f "${conf_yaml_file}"

    if [[ "${mount_type}" == "pvc" && "${is_external_pvc}" == "false" ]]; then
        exec_cmd kubectl apply -f "${pvc_file}"
    fi

    exec_cmd kubectl apply -f "${gateway_file}"
    wait_k8s_resource_ready "deployment" "${name}" "${namespace}"
}

uninstall_gateway() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local gateway_name="${DEPLOY_VARS["GATEWAY_NAME"]}"
    local gateway_file="${CONFIG["GATEWAY_FILE"]}"
    local mount_type="${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}"
    local is_external_pvc="${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"

    local env_yaml_file="${CONFIG["GATEWAY_ENV_YAML_FILE"]}"
    local conf_yaml_file="${CONFIG["GATEWAY_CONFIG_YAML_FILE"]}"

    # 先优雅停 Pod，再清理周边资源
    # gateway.yaml 含 ServiceAccount / Role / Deployment 等同文件资源。
    # 不可 kubectl delete -f 一次性删掉：SA 被删后 Pod 仍在 Terminating 窗口内，
    # in-cluster token 立即失效 → Gateway shutdown 删 Agent Pod 会 401。
    info "Deleting Gateway Deployment first (keep ServiceAccount for graceful shutdown)"
    exec_cmd kubectl delete deployment "${gateway_name}" -n "${namespace}" --ignore-not-found=true
    wait_pod_terminated "${gateway_name}" "${namespace}"

    info "Deleting remaining Gateway resources (ServiceAccount, Role, Service, ...)"
    exec_cmd kubectl delete -f "${gateway_file}" --ignore-not-found=true
    exec_cmd kubectl delete -f "${env_yaml_file}" --ignore-not-found=true
    exec_cmd kubectl delete -f "${conf_yaml_file}" --ignore-not-found=true

    if [[ "${mount_type}" == "pvc" && "${is_external_pvc}" == "false" ]]; then
        exec_cmd kubectl delete -f "${pvc_file}" --ignore-not-found=true
    fi
    uninstall_secret_configmap
    ensure_redis_down
}
