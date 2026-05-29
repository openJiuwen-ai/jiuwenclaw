#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# =============================================================================
# CORE DATA STRUCTURE
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OYL_REPO_NAME="charts_dev"
OYL_REPO_URL="http://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/charts_dev"
OYL_CHART_NAME="openyuanrong"
OYL_CHART_VERSION="1.0.202603301703"
OYL_REPO_DIR="${SCRIPT_DIR}/${OYL_CHART_NAME}"

CUSTOM_ENV_FILE="${SCRIPT_DIR}/.env.custom"
ENV_FILE="${SCRIPT_DIR}/.env"

POOL_TEMPLATE_FILE="${SCRIPT_DIR}/conf/pool.template.json"
POOL_FILE="${SCRIPT_DIR}/conf/pool.json"

CLAW_META_TEMPLATE_FILE="${SCRIPT_DIR}/conf/claw_meta.template.json"
CLAW_META_FILE="${SCRIPT_DIR}/conf/claw_meta.json"

GATEWAY_CONFIG_TEMPLATE_FILE=""
GATEWAY_CONFIG_FILE="${SCRIPT_DIR}/conf/gateway-config.yaml"

GATEWAY_ENV_TEMPLATE_FILE="${SCRIPT_DIR}/conf/gateway.template.env"
GATEWAY_ENV_FILE="${SCRIPT_DIR}/conf/gateway.env"

GATEWAY_TEMPLATE_FILE="${SCRIPT_DIR}/conf/gateway.template.yaml"
GATEWAY_FILE="${SCRIPT_DIR}/conf/gateway.yaml"

NFS_TEMPLATE_FILE="${SCRIPT_DIR}/conf/nfs.template.yaml"
NFS_FILE="${SCRIPT_DIR}/conf/nfs.yaml"

RABBITMQ_TEMPLATE_FILE="${SCRIPT_DIR}/conf/rabbitmq.template.yaml"
RABBITMQ_FILE="${SCRIPT_DIR}/conf/rabbitmq.yaml"

MYSQL_TEMPLATE_FILE="${SCRIPT_DIR}/conf/mysql.template.yaml"
MYSQL_FILE="${SCRIPT_DIR}/conf/mysql.yaml"

POSTGRES_TEMPLATE_FILE="${SCRIPT_DIR}/conf/postgresql.template.yaml"
POSTGRES_FILE="${SCRIPT_DIR}/conf/postgresql.yaml"

WEB_TEMPLATE_FILE="${SCRIPT_DIR}/conf/web.template.yaml"
WEB_FILE="${SCRIPT_DIR}/conf/web.yaml"

MANAGER_TEMPLATE_FILE="${SCRIPT_DIR}/conf/manager.template.yaml"
MANAGER_FILE="${SCRIPT_DIR}/conf/manager.yaml"

PV_TEMPLATE_FILE="${SCRIPT_DIR}/conf/pv-nfs.template.yaml"
PV_FILE="${SCRIPT_DIR}/conf/pv-nfs.yaml"

PVC_TEMPLATE_FILE="${SCRIPT_DIR}/conf/pvc-nfs.template.yaml"
PVC_FILE="${SCRIPT_DIR}/conf/pvc-nfs.yaml"

REG_FUNC_FILE="${SCRIPT_DIR}/func/clawee.py"

META_PORT=""
CMD=""


# ==== All available modules ====
# NOTE: ALL_MODULES is set dynamically by _init_db_type() based on DB_TYPE.
#       Do not set it here.
declare -ga ALL_MODULES=()

declare -ga MODULES=()

declare -ga WORKER_NODE_IPS=()

declare -ga OTHER_MASTER_IPS=()

declare -ga OTHER_NODE_IPS=()

declare -A DEPLOY_VARS=(
    ["NAMESPACE"]="default"
    ["POOL_ID"]="claw"
    ["FUNC_SVC_NAME"]="0@jiuwen@clawtest"
    ["GATEWAY_NAME"]="jiuwenclaw-gateway"
    ["GATEWAY_SERVICE_ACCOUNT"]="jiuwenclaw-gateway-sa"
    ["GATEWAY_CONFIG_MAP_NAME"]="jiuwenclaw-gateway-config"
    ["GATEWAY_ENV_FILE_NAME"]="jiuwenclaw-gateway-env"
    ["GATEWAY_WEBSOCKET_PORT"]="19000"
    ["GATEWAY_DB_TYPE"]=""         # set by _init_db_type()
    ["GATEWAY_SQLITE_PATH"]="openjiuwen_gateway.db"
    ["GATEWAY_DB_HOST"]=""         # set by _init_db_type()
    ["GATEWAY_DB_PORT"]=""         # set by _init_db_type()
    ["GATEWAY_DB_USER"]=""         # set by _init_db_type()
    ["GATEWAY_DB_PASSWORD"]="Root@123456"
    ["GATEWAY_DB_NAME"]="openjiuwen_gateway"
    ["NFS_NAME"]="nfs-server"
    ["NFS_IMAGE"]="itsthenetwork/nfs-server-alpine:12"
    ["NFS_HOST_PATH"]="/data/nfs"
    ["NFS_SHARE_PATH"]="/"
    ["PVC_NAME"]="pvc-nfs-shared"
    ["PV_NAME"]="pv-nfs-shared"
    ["AGENT_SERVER_POD_NAME"]="jiuwenclaw-agentserver"
    ["WEB_NAME"]="jiuwenclaw-web"
    ["RABBITMQ_NAME"]="rabbitmq"
    ["RABBITMQ_IMAGE"]="rabbitmq:3.9.22-management"
    ["RABBITMQ_PATH"]="/exports/rabbitmq"
    ["RABBITMQ_USER"]="admin"
    ["RABBITMQ_PASSWORD"]="Rabbitmq@123"
    ["MYSQL_NAME"]="mysql"
    ["MYSQL_IMAGE"]="mysql:8.0"
    ["MYSQL_PATH"]="/exports/mysql"
    ["MYSQL_ROOT_PASSWORD"]="Root@123456"
    ["POSTGRES_NAME"]="postgresql"
    ["POSTGRES_IMAGE"]="postgres:16"
    ["POSTGRES_PATH"]="/exports/postgresql"
    ["POSTGRES_PASSWORD"]="Root@123456"
    ["MANAGER_NAME"]="jiuwenclaw-manager"
    ["MANAGER_REST_PORT"]="8765"
    ["MANAGER_WS_PORT"]="8766"
    ["MANAGER_DB_TYPE"]=""         # set by _init_db_type()
    ["MANAGER_SQLITE_PATH"]="claw_manager.db"
    ["MANAGER_DB_HOST"]=""         # set by _init_db_type()
    ["MANAGER_DB_PORT"]=""         # set by _init_db_type()
    ["MANAGER_DB_USER"]=""         # set by _init_db_type()
    ["MANAGER_DB_PASSWORD"]="Root@123456"
    ["MANAGER_DB_NAME"]="claw_manager"
    ["DB_TYPE"]="mysql"
    ["MYSQL_NODE_PORT"]="30036"
    ["POSTGRES_NODE_PORT"]="30432"
    ["RABBITMQ_AMQ_NODE_PORT"]="30073"
    ["RABBITMQ_MGR_NODE_PORT"]="30072"
    ["MANAGER_NODE_PORT"]="30086"
    ["WEB_NODE_PORT"]="30080"
    ["GATEWAY_NODE_PORT"]="30019"
)

# ==== Initialize DB_TYPE and derived variables ====
init_db_type() {
    local db_type="${DEPLOY_VARS["DB_TYPE"]:-mysql}"

    DEPLOY_VARS["MANAGER_DB_TYPE"]="${db_type}"
    DEPLOY_VARS["GATEWAY_DB_TYPE"]="${db_type}"

    # Set default DB connection params based on DB_TYPE
    if [[ "${db_type}" == "mysql" ]]; then
        DEPLOY_VARS["MANAGER_DB_HOST"]="mysql-headless"
        DEPLOY_VARS["MANAGER_DB_PORT"]="3306"
        DEPLOY_VARS["MANAGER_DB_USER"]="root"
        DEPLOY_VARS["GATEWAY_DB_HOST"]="mysql-headless"
        DEPLOY_VARS["GATEWAY_DB_PORT"]="3306"
        DEPLOY_VARS["GATEWAY_DB_USER"]="root"
        ALL_MODULES=("NFS" "RABBITMQ" "MYSQL" "GATEWAY" "WEB" "MANAGER")
    elif [[ "${db_type}" == "postgresql" ]]; then
        DEPLOY_VARS["MANAGER_DB_HOST"]="postgresql-headless"
        DEPLOY_VARS["MANAGER_DB_PORT"]="5432"
        DEPLOY_VARS["MANAGER_DB_USER"]="postgres"
        DEPLOY_VARS["GATEWAY_DB_HOST"]="postgresql-headless"
        DEPLOY_VARS["GATEWAY_DB_PORT"]="5432"
        DEPLOY_VARS["GATEWAY_DB_USER"]="postgres"
        ALL_MODULES=("NFS" "RABBITMQ" "POSTGRESQL" "GATEWAY" "WEB" "MANAGER")
    else
        # sqlite or unknown: no DB module needed
        ALL_MODULES=("NFS" "RABBITMQ" "GATEWAY" "WEB" "MANAGER")
    fi

    info "DB_TYPE=${db_type}, ALL_MODULES=${ALL_MODULES[*]}"
}

declare -A OYR_COMPONENTS=(
        ["frontend"]="deployment"
        ["function-scheduler"]="deployment"
        ["function-manager"]="deployment"
        ["function-master"]="deployment"
        ["meta-service"]="deployment"
        ["iam-adaptor"]="deployment"
        ["function-proxy"]="daemonset"
        ["ds-worker"]="daemonset"
        ["etcd"]="statefulset"
        ["minio"]="statefulset"
    )