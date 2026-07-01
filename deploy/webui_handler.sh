#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# 统一前端模块 webui：认证中心 identity + 统一前端 webui 一起部署（同一模块，必启）。
#   - identity：python 服务，独立身份库，ClusterIP（对外由 webui 经 /idp 反代）。
#   - webui：nginx + SPA，对外唯一入口(NodePort，端口 ports_handler 自动分配)。
# 参照 manager 模块（一个 handler 管 server+web）：此处一个 handler 管 identity+webui。
# 两个资源合并在 webui.template.yaml 一个文件里（--- 分隔），部署顺序排在 ALL_MODULES 最后，
# 确保 webui 的 BACKEND_* 指向的后端 Service 已存在。

render_webui_files() {
    local template_file="${CONFIG["WEBUI_TEMPLATE_FILE"]}"
    local file="${CONFIG["WEBUI_FILE"]}"

    ensure_available_port "WEBUI_NODE_PORT"
    render_config_template "${template_file}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file}
    # 一个文件里两个 Deployment，资源按 deployment 名分别注入，避免张冠李戴
    add_resource_if_set "IDENTITY" "${file}" "${DEPLOY_VARS["IDENTITY_NAME"]}"
    add_resource_if_set "WEBUI" "${file}" "${DEPLOY_VARS["WEBUI_NAME"]}"
}

deploy_webui() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local file="${CONFIG["WEBUI_FILE"]}"

    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["IDENTITY_NAME"]}" "${namespace}"
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["WEBUI_NAME"]}" "${namespace}"
    success "IDENTITY deployed (ClusterIP): ${DEPLOY_VARS["IDENTITY_NAME"]}:${DEPLOY_VARS["IDENTITY_PORT"]}"
    success "WEBUI_NODE_PORT: ${DEPLOY_VARS["WEBUI_NODE_PORT"]}"
}

uninstall_webui() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local file="${CONFIG["WEBUI_FILE"]}"

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${DEPLOY_VARS["WEBUI_NAME"]}" "${namespace}"
    wait_pod_terminated "${DEPLOY_VARS["IDENTITY_NAME"]}" "${namespace}"
}
