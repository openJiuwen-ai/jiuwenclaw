#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/global_vars.sh"
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

    TEST_DIR="$(mktemp -d)"
    local test_dir="${TEST_DIR}"
    trap cleanup EXIT

    DEPLOY_VARS["OS_TYPE"]="linux"
    DEPLOY_VARS["MODE"]="product"
    DEPLOY_VARS["WEB_NODE_PORT"]="31001"
    DEPLOY_VARS["MANAGER_SERVER_NODE_PORT"]="31002"
    DEPLOY_VARS["MANAGER_WEB_NODE_PORT"]="31003"

    assert_equal \
        "false" \
        "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]}" \
        "User Web embedding must be disabled by default"
    printf '%s\n' 'ENABLE_USER_WEB_EMBEDDING=true' > "${test_dir}/.env.custom"
    read_env_from_file "${test_dir}/.env.custom" "DEPLOY_VARS"
    assert_equal \
        "true" \
        "${DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]}" \
        ".env.custom must override the embedding default"

    CONFIG["WEB_FILE"]="${test_dir}/web.yaml"

    DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]="false"
    gen_web_file
    assert_equal \
        "NodePort" \
        "$(yq eval-all 'select(.metadata.name == "jiuwenclaw-web-nodeport") | .spec.type' "${CONFIG["WEB_FILE"]}")" \
        "standalone mode must expose the User Web NodePort"
    assert_equal \
        "0.0.0.0" \
        "$(yq eval-all 'select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "FRONTEND_HOST").value' "${CONFIG["WEB_FILE"]}")" \
        "User Web frontend must listen on all interfaces"

    DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]="true"
    gen_web_file
    assert_equal \
        "0" \
        "$(yq eval-all '[select(.metadata.name == "jiuwenclaw-web-nodeport")] | length' "${CONFIG["WEB_FILE"]}")" \
        "embedded mode must remove the User Web NodePort"
    assert_equal \
        "ClusterIP" \
        "$(yq eval-all 'select(.kind == "Service") | .spec.type' "${CONFIG["WEB_FILE"]}")" \
        "embedded mode must retain the internal User Web service"

    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="false"
    if (check_user_web_embedding_config) >/dev/null 2>&1; then
        echo "FAIL: embedded mode must require Manager Web" >&2
        exit 1
    fi

    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="true"
    check_user_web_embedding_config

    DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]="invalid"
    if (check_user_web_embedding_config) >/dev/null 2>&1; then
        echo "FAIL: invalid boolean value must be rejected" >&2
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

    CONFIG["MANAGER_WEB_FILE"]="${test_dir}/manager-web.yaml"
    render_config_template \
        "${CONFIG["MANAGER_WEB_TEMPLATE_FILE"]}" \
        "${CONFIG["MANAGER_WEB_FILE"]}" \
        "DEPLOY_VARS"
    assert_equal \
        "0" \
        "$(yq eval-all '[select(.kind == "Deployment").spec.template.spec.containers[0].env[] | select(.name == "IDENTITY_PUBLIC_KEY_URL")] | length' "${CONFIG["MANAGER_WEB_FILE"]}")" \
        "Manager Web must not receive the unused public-key URL"

    local deleted_service=""
    ensure_secret_configmap() { :; }
    exec_cmd() { :; }
    wait_k8s_resource_ready() { :; }
    success() { :; }
    delete_k8s_resource() { deleted_service="$1/$2/$3"; }

    DEPLOY_VARS["ENABLE_USER_WEB_EMBEDDING"]="true"
    deploy_web
    assert_equal \
        "service/jiuwenclaw-web-nodeport/default" \
        "${deleted_service}" \
        "embedded deployment must remove a stale User Web NodePort"

    deleted_service=""
    DEPLOY_VARS["IS_UP_MANAGER_WEB"]="true"
    deploy_manager
    assert_equal \
        "service/jiuwenclaw-web-nodeport/default" \
        "${deleted_service}" \
        "Manager deployment must also remove a stale User Web NodePort"

    echo "PASS: identity and User Web deployment checks"
}

main "$@"
