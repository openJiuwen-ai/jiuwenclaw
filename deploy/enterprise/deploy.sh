#!/usr/bin/env bash
set -euo >/dev/null 2>&1

source "common.sh"
source "global_vars.sh"
source "args_handler.sh"
source "check_handler.sh"
source "cmd_handler.sh"
source "envfile_handler.sh"
source "template_handler.sh"
source "k8s_handler.sh"
source "ports_handler.sh"
source "nfs_handler.sh"
source "mysql_handler.sh"
source "postgresql_handler.sh"
source "minio_handler.sh"
source "rabbitmq_handler.sh"
source "redis_handler.sh"
source "log_handler.sh"
source "jina_handler.sh"
source "configmap_secret_handler.sh"
source "gateway_handler.sh"
source "manager_handler.sh"
source "web_handler.sh"
source "runtime_handler.sh"
source "post_deploy_handler.sh"

process_up() {
    # MODULES是ALL_MODULES的子集，启动顺序正着来
    local sorted_modules=()
    for m in "${ALL_MODULES[@]}"; do
        if [[ " ${MODULES[@]} " =~ " ${m} " ]]; then
            sorted_modules+=("$m")
        fi
    done
    info "sorted_modules=${sorted_modules[@]}"

    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" != "true" ]; then
        local namespace="${DEPLOY_VARS["NAMESPACE"]}"
        if [ ${namespace} != "default" ]; then
            create_k8s_resource "ns" ${namespace}
        fi
    fi

    # collect_k8s_cluster_info 是只读 kubectl 查询。dev 模式渲染
    # (enable_dev_mode_if_needed) 依赖 CURRENT_NODE_NAME 给 Deployment pin nodeName,
    # render-only 也必须执行, 否则渲染半途 unbound variable 崩溃,
    # 产物缺 nodeName/securityContext/fsGroup/OPENJIUWEN_SERVICE_PG_SCHEMA。
    collect_k8s_cluster_info

    exec_cmd mkdir -p ${CONFIG_DIR}

    for module in "${sorted_modules[@]}"; do
        local lmodule=${module,,}
        local fname=${lmodule//-/_}

        if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
            check_${fname}_up_dependency
            render_${fname}_files
        else
            check_${fname}_up_dependency
            render_${fname}_files
            deploy_${fname}
        fi
    done

    # 部署完成后收敛业务侧基线（PG 服务模板行 / Redis scope config 派生副本 /
    # 节点 hostPath 目录），详见 post_deploy_handler.sh 头注释
    post_deploy_init_hook
}


process_down() {
    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    local namespace="${DEPLOY_VARS["NAMESPACE"]}"

    # MODULES是ALL_MODULES的子集，卸载顺序倒着来
    local reversed_modules=()
    for ((i=${#ALL_MODULES[@]}-1; i>=0; i--)); do
        m="${ALL_MODULES[$i]}"
        if [[ " ${MODULES[@]} " =~ " ${m} " ]]; then
            reversed_modules+=("$m")
        fi
    done
    info "reversed_modules=${reversed_modules[@]}"

    for module in "${reversed_modules[@]}"; do
        local lmodule=${module,,}
        local fname=${lmodule//-/_}
        uninstall_${fname}
    done
}

process_restart() {
    process_down
    process_up
}

# ==================== Main function ====================
main() {
    read_env_from_file "${CUSTOM_ENV_FILE}" "DEPLOY_VARS"

    # JIUWENSWARM_EDITION 是产品形态的唯一开关；USER_WEB_MODE / ENABLE_USER_WEB_EMBEDDING 仅作兼容输入。
    if [[ -z "${DEPLOY_VARS["JIUWENSWARM_EDITION"]:-}" ]]; then
        if [[ -n "${DEPLOY_VARS["USER_WEB_MODE"]:-}" ]]; then
            DEPLOY_VARS["JIUWENSWARM_EDITION"]="${DEPLOY_VARS["USER_WEB_MODE"]}"
            warning "USER_WEB_MODE is deprecated; use JIUWENSWARM_EDITION=${DEPLOY_VARS["JIUWENSWARM_EDITION"]}"
        elif [[ "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]:-false}" == "true" ]]; then
            DEPLOY_VARS["JIUWENSWARM_EDITION"]="enterprise"
            warning "ENABLE_USER_WEB_EMBEDDING is deprecated; use JIUWENSWARM_EDITION=enterprise"
        else
            DEPLOY_VARS["JIUWENSWARM_EDITION"]="enterprise"
        fi
    fi
    DEPLOY_VARS["USER_WEB_MODE"]="${DEPLOY_VARS["JIUWENSWARM_EDITION"]}"
    DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]=$(
        [[ "${DEPLOY_VARS["JIUWENSWARM_EDITION"]}" == "enterprise" ]] && printf true || printf false
    )
    if [[ -z "${DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]:-}" ]]; then
        DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]="true"
    fi
    local user_web_idp_defaulted="false"
    local user_web_manager_defaulted="false"
    if [[ -z "${DEPLOY_VARS["USER_WEB_IDP_TARGET"]:-}" ]]; then
        DEPLOY_VARS["USER_WEB_IDP_TARGET"]="http://${DEPLOY_VARS["IDENTITY_NAME"]}:${DEPLOY_VARS["IDENTITY_REST_PORT"]}"
        user_web_idp_defaulted="true"
    fi
    if [[ -z "${DEPLOY_VARS["USER_WEB_MANAGER_TARGET"]:-}" ]]; then
        DEPLOY_VARS["USER_WEB_MANAGER_TARGET"]="http://${DEPLOY_VARS["MANAGER_SERVER_NAME"]}:${DEPLOY_VARS["MANAGER_REST_PORT"]}"
        user_web_manager_defaulted="true"
    fi
    if [[ "${DEPLOY_VARS["JIUWENSWARM_EDITION"]}" == "enterprise" ]]; then
        if [[ "${DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]}" == "true" ]]; then
            info "【登录认证模拟调试模式已开启】"
        elif [[ "${DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]}" == "false" ]]; then
            info "【正式身份认证模式，依赖manager ID认证服务】"
            if [[ "${user_web_idp_defaulted}" == "true" ]]; then
                info "USER_WEB_IDP_TARGET 未配置，暂使用当前集群 Identity：${DEPLOY_VARS["USER_WEB_IDP_TARGET"]}"
            fi
            if [[ "${user_web_manager_defaulted}" == "true" ]]; then
                info "USER_WEB_MANAGER_TARGET 未配置，暂使用当前集群 Manager：${DEPLOY_VARS["USER_WEB_MANAGER_TARGET"]}"
            fi
        fi
    elif [[ "${DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]}" == "false" ]]; then
        warning "配置冲突：personal 模式仍将跳过企业登录认证"
    fi

    parse_args "$@"
    detect_os
    check_dependency
    process_${CMD}
}


# Execute main function
main "$@"
