#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/global_vars.sh"
source "${SCRIPT_DIR}/args_handler.sh"
source "${SCRIPT_DIR}/cmd_handler.sh"
source "${SCRIPT_DIR}/envfile_handler.sh"
source "${SCRIPT_DIR}/template_handler.sh"
source "${SCRIPT_DIR}/check_handler.sh"
source "${SCRIPT_DIR}/web_handler.sh"
source "${SCRIPT_DIR}/manager_handler.sh"

TEST_DIR=""

cleanup() {
    if [ -n "${TEST_DIR}" ] && [ -d "${TEST_DIR}" ]; then
        rm -rf -- "${TEST_DIR}"
    fi
}

assert_equal() {
    local expected="$1"
    local actual="$2"
    local message="$3"
    if [ "${actual}" != "${expected}" ]; then
        echo "FAIL: ${message}: expected=${expected}, actual=${actual}" >&2
        exit 1
    fi
}

main() {
    check_cmd "yq"

    MODULES=()
    process_modules
    assert_equal "GATEWAY WEB MANAGER RUNTIME" "${MODULES[*]}" "enterprise default must include Manager"
    MODULES=()
    DEPLOY_VARS["JIUWENSWARM_EDITION"]="personal"
    process_modules
    assert_equal "GATEWAY WEB RUNTIME" "${MODULES[*]}" "personal default must exclude Manager"
    MODULES=()
    DEPLOY_VARS["JIUWENSWARM_EDITION"]="enterprise"

    TEST_DIR="$(mktemp -d)"
    local test_dir="${TEST_DIR}"
    trap cleanup EXIT

    DEPLOY_VARS["OS_TYPE"]="linux"
    DEPLOY_VARS["MODE"]="product"
    DEPLOY_VARS["WEB_NODE_PORT"]="31001"
    DEPLOY_VARS["MANAGER_SERVER_NODE_PORT"]="31002"
    DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]="31003"

    assert_equal \
        "enterprise" \
        "${DEPLOY_VARS["JIUWENSWARM_EDITION"]}" \
        "JIUWENSWARM_EDITION must default to enterprise"
    printf '%s\n' 'JIUWENSWARM_EDITION=personal' > "${test_dir}/.env.custom"
    read_env_from_file "${test_dir}/.env.custom" "DEPLOY_VARS"
    assert_equal \
        "personal" \
        "${DEPLOY_VARS["JIUWENSWARM_EDITION"]}" \
        ".env.custom edition value must override the default"

    CONFIG["WEB_FILE"]="${test_dir}/web.yaml"

    DEPLOY_VARS["JIUWENSWARM_EDITION"]="personal"
    gen_web_file
    assert_equal \
        "NodePort" \
        "$(yq eval-all 'select(.metadata.name == "jiuwenclaw-web-nodeport") | .spec.type' "${CONFIG["WEB_FILE"]}")" \
        "standalone mode must expose the User Web NodePort"
    assert_equal \
        "0.0.0.0" \
        "$(yq eval-all 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "FRONTEND_HOST").value' "${CONFIG["WEB_FILE"]}")" \
        "User Web frontend must listen on all interfaces"
    assert_equal \
        "personal" \
        "$(yq eval-all 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "JIUWENSWARM_EDITION").value' "${CONFIG["WEB_FILE"]}")" \
        "personal mode must inject JIUWENSWARM_EDITION"

    DEPLOY_VARS["JIUWENSWARM_EDITION"]="enterprise"
    gen_web_file
    assert_equal \
        "NodePort" \
        "$(yq eval-all 'select(.metadata.name == "jiuwenclaw-web-nodeport") | .spec.type' "${CONFIG["WEB_FILE"]}")" \
        "enterprise mode must expose the independent User Web NodePort"
    assert_equal \
        "enterprise" \
        "$(yq eval-all 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "JIUWENSWARM_EDITION").value' "${CONFIG["WEB_FILE"]}")" \
        "enterprise mode must inject JIUWENSWARM_EDITION"
    assert_equal \
        "enterprise" \
        "$(yq eval-all 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "VITE_JIUWENSWARM_EDITION").value' "${CONFIG["WEB_FILE"]}")" \
        "enterprise mode must inject VITE_JIUWENSWARM_EDITION"

    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="false"
    DEPLOY_VARS["USER_WEB_IDP_TARGET"]="http://jiuwenclaw-identity:8770"
    DEPLOY_VARS["USER_WEB_MANAGER_TARGET"]="http://jiuwenclaw-manager-server:8765"
    check_jiuwenswarm_edition_config

    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="true"
    DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]="true"
    check_jiuwenswarm_edition_config

    DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]="invalid"
    if check_jiuwenswarm_edition_config >/dev/null 2>&1; then
        echo "FAIL: invalid LOGIN_AUTH_SIMULATE value must be rejected" >&2
        exit 1
    fi
    DEPLOY_VARS["LOGIN_AUTH_SIMULATE"]="false"
    check_jiuwenswarm_edition_config

    DEPLOY_VARS["JIUWENSWARM_EDITION"]="invalid"
    if (check_jiuwenswarm_edition_config) >/dev/null 2>&1; then
        echo "FAIL: invalid edition value must be rejected" >&2
        exit 1
    fi

    CONFIG["MANAGER_SERVER_FILE"]="${test_dir}/manager-server.yaml"
    render_config_template \
        "${CONFIG["MANAGER_SERVER_TEMPLATE_FILE"]}" \
        "${CONFIG["MANAGER_SERVER_FILE"]}" \
        "DEPLOY_VARS"
    assert_equal \
        "http://jiuwenclaw-identity:8770/v1/auth/public_key" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_PUBLIC_KEY_URL").value' "${CONFIG["MANAGER_SERVER_FILE"]}")" \
        "Manager Server must receive the Identity public-key URL"

    CONFIG["IDENTITY_FILE"]="${test_dir}/identity.yaml"
    render_config_template \
        "${CONFIG["IDENTITY_TEMPLATE_FILE"]}" \
        "${CONFIG["IDENTITY_FILE"]}" \
        "DEPLOY_VARS"
    assert_equal \
        "false" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_FEDERATION_DEMO_ENABLED").value' "${CONFIG["IDENTITY_FILE"]}")" \
        "Identity federation Demo must remain disabled by default"
    assert_equal \
        "/idp" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_FEDERATION_PUBLIC_PATH_PREFIX").value' "${CONFIG["IDENTITY_FILE"]}")" \
        "Identity federation browser URLs must use the Manager Web IDP prefix"
    assert_equal \
        "300" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_FEDERATION_REQUEST_TTL").value' "${CONFIG["IDENTITY_FILE"]}")" \
        "Identity federation request TTL must be rendered"
    assert_equal \
        "60" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_FEDERATION_CODE_TTL").value' "${CONFIG["IDENTITY_FILE"]}")" \
        "Identity federation code TTL must be rendered"
    assert_equal \
        "enterprise-admins" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_FEDERATION_DEMO_ADMIN_GROUP").value' "${CONFIG["IDENTITY_FILE"]}")" \
        "Identity federation admin group must be rendered"

    CONFIG["MANAGER_WEB_FILE"]="${test_dir}/manager-web.yaml"
    render_config_template \
        "${CONFIG["MANAGER_WEB_TEMPLATE_FILE"]}" \
        "${CONFIG["MANAGER_WEB_FILE"]}" \
        "DEPLOY_VARS"
    assert_equal \
        "0" \
        "$(yq eval-all '[select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_PUBLIC_KEY_URL")] | length' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "Manager Web must not receive the unused public-key URL"
    assert_equal \
        "http://jiuwenclaw-web:5173" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "MANAGER_WEB_USER_WEB_TARGET").value' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "Manager Web must route /chat to the User Web HTTP service"
    assert_equal \
        "http://jiuwenclaw-gateway:19002" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "MANAGER_WEB_GATEWAY_HTTP_TARGET").value' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "Manager Web must route HTTP/SSE to the Gateway Web HTTP service"
    assert_equal \
        "http://jiuwenclaw-gateway:19000" \
        "$(yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "MANAGER_WEB_GATEWAY_WS_TARGET").value' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "Manager Web must route WebSocket to the Gateway WebSocket service"
    assert_equal \
        "0" \
        "$(yq eval-all '[select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "MANAGER_WEB_GATEWAY_SSE" or .name == "MANAGER_WEB_USER_SERVER_TARGET")] | length' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "obsolete Manager Web proxy variables must not be rendered"
    assert_equal \
        "5173" \
        "$(yq eval-all 'select(.kind == "Service" and .metadata.name == "jiuwenclaw-web").spec.ports[] | select(.name == "http").port' "${CONFIG["WEB_FILE"]}")" \
        "User Web ClusterIP service must expose its HTTP port"

    CONFIG["GATEWAY_FILE"]="${test_dir}/gateway.yaml"
    render_config_template \
        "${CONFIG["GATEWAY_TEMPLATE_FILE"]}" \
        "${CONFIG["GATEWAY_FILE"]}" \
        "DEPLOY_VARS"
    assert_equal \
        "1" \
        "$(yq eval-all '[select(.kind == "Service" and .metadata.name == "jiuwenclaw-gateway").spec.ports[] | select(.port == 19001)] | length' "${CONFIG["GATEWAY_FILE"]}")" \
        "Gateway Service must expose its shared HTTP and ACP listener only once"
    assert_equal \
        "http" \
        "$(yq eval-all 'select(.kind == "Service" and .metadata.name == "jiuwenclaw-gateway").spec.ports[] | select(.port == 19001).name' "${CONFIG["GATEWAY_FILE"]}")" \
        "Gateway shared listener must use the HTTP service port"

    ensure_secret_configmap() { :; }
    delete_k8s_resource() { :; }
    exec_cmd() { :; }
    wait_k8s_resource_ready() { :; }
    success() { :; }

    DEPLOY_VARS["JIUWENSWARM_EDITION"]="enterprise"
    deploy_web
    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="true"
    deploy_manager

    echo "PASS: identity and User Web deployment checks"
}

main "$@"
