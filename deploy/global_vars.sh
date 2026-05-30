#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# =============================================================================
# CORE DATA STRUCTURE
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CUSTOM_ENV_FILE="${SCRIPT_DIR}/.env.custom"

# ===== Core project configuration (paths, ports, commands, OS info) =====
declare -A CONFIG=(
    ["GATEWAY_CONFIG_FILE"]="${SCRIPT_DIR}/conf/gateway-config.yaml"

    ["GATEWAY_ENV_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/gateway.template.env"
    ["GATEWAY_ENV_FILE"]="${SCRIPT_DIR}/conf/gateway.env"

    ["GATEWAY_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/gateway.template.yaml"
    ["GATEWAY_FILE"]="${SCRIPT_DIR}/conf/gateway.yaml"

    ["NFS_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/nfs.template.yaml"
    ["NFS_FILE"]="${SCRIPT_DIR}/conf/nfs.yaml"

    ["RABBITMQ_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/rabbitmq.template.yaml"
    ["RABBITMQ_FILE"]="${SCRIPT_DIR}/conf/rabbitmq.yaml"

    ["MYSQL_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/mysql.template.yaml"
    ["MYSQL_FILE"]="${SCRIPT_DIR}/conf/mysql.yaml"

    ["POSTGRES_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/postgresql.template.yaml"
    ["POSTGRES_FILE"]="${SCRIPT_DIR}/conf/postgresql.yaml"

    ["WEB_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/web.template.yaml"
    ["WEB_FILE"]="${SCRIPT_DIR}/conf/web.yaml"

    ["MANAGER_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/manager.template.yaml"
    ["MANAGER_FILE"]="${SCRIPT_DIR}/conf/manager.yaml"

    ["PV_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/pv-nfs.template.yaml"
    ["PV_FILE"]="${SCRIPT_DIR}/conf/pv-nfs.yaml"

    ["PVC_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/pvc-nfs.template.yaml"
    ["PVC_FILE"]="${SCRIPT_DIR}/conf/pvc-nfs.yaml"

    ["START_PORT"]="30000"
    ["END_PORT"]="32767"
)




# Parsed command-line arguments
declare -A ARGS=(
    ["CMD"]=""
)



# ==== All available modules ====
#declare -ga ALL_MODULES=("NFS" "RABBITMQ" "YR_CLAW" "GATEWAY" "WEB" "MANAGER")
declare -ga ALL_MODULES=("NFS" "RABBITMQ" "MYSQL" "POSTGRESQL" "GATEWAY" "WEB" "MANAGER")

declare -ga MODULES=()

declare -ga WORKER_NODE_IPS=()

declare -ga OTHER_MASTER_IPS=()

declare -ga OTHER_NODE_IPS=()

declare -A DEPLOY_VARS=(
    ["MODE"]=product
    ["HOST_CODE_PATH"]=""
    ["POD_CODE_PATH"]=""
    ["NAMESPACE"]="default"
    ["POOL_ID"]="claw"
    ["FUNC_SVC_NAME"]="0@jiuwen@clawtest"
    ["GATEWAY_NAME"]="jiuwenclaw-gateway"
    ["GATEWAY_SERVICE_ACCOUNT"]="jiuwenclaw-gateway-sa"
    ["GATEWAY_CONFIG_MAP_NAME"]="jiuwenclaw-gateway-config"
    ["GATEWAY_ENV_FILE_NAME"]="jiuwenclaw-gateway-env"
    ["GATEWAY_WEBSOCKET_PORT"]="19000"
    ["JIUWENCLAW_PATH"]="/exports/jiuwenclaw"
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
    ["DB_TYPE"]="sqlite"
    ["MANAGER_DB_NAME"]="claw_manager"
    ["GATEWAY_DB_NAME"]="openjiuwen_gateway"
    ["MANAGER_SQLITE_PATH"]="claw_manager.db"
    ["GATEWAY_SQLITE_PATH"]="openjiuwen_gateway.db"
    ["CHECKPOINTER_DB_NAME"]="jiuwenclawee"
)



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
