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

    ["REDIS_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/redis.template.yaml"
    ["REDIS_FILE"]="${SCRIPT_DIR}/conf/redis.yaml"

    ["POSTGRES_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/postgresql.template.yaml"
    ["POSTGRES_FILE"]="${SCRIPT_DIR}/conf/postgresql.yaml"

    ["MINIO_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/minio.template.yaml"
    ["MINIO_FILE"]="${SCRIPT_DIR}/conf/minio.yaml"

    ["WEB_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/web.template.yaml"
    ["WEB_FILE"]="${SCRIPT_DIR}/conf/web.yaml"

    ["MANAGER_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/manager.template.yaml"
    ["MANAGER_FILE"]="${SCRIPT_DIR}/conf/manager.yaml"

    ["MANAGER_WEB_TEMPLATE_FILE"]="${SCRIPT_DIR}/conf/manager_web.template.yaml"
    ["MANAGER_WEB_FILE"]="${SCRIPT_DIR}/conf/manager_web.yaml"

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
declare -ga ALL_MODULES=("NFS" "RABBITMQ" "MYSQL" "REDIS" "POSTGRESQL" "MINIO" "GATEWAY" "WEB" "MANAGER")

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
    ["GATEWAY_REPLICAS"]="1"
    ["ENTERPRISE_WEB_WS_PORT"]="19000"
    ["JIUWENCLAW_PATH"]="/exports/jiuwenclaw"
    ["JIUWENCLAW_NFS_PATH"]="/jiuwenclaw"
    ["NFS_NAME"]="nfs-server"
    ["NFS_IMAGE"]="ghcr.io/obeone/nfs-server:2.2.2"
    ["NFS_HOST_PATH"]="/data/nfs"
    ["NFS_SHARE_PATH"]="/"
    ["PVC_NAME"]="pvc-nfs-shared"
    ["PV_NAME"]="pv-nfs-shared"
    ["AGENT_SERVER_POD_NAME"]="jiuwenclaw-agentserver"
    ["WEB_NAME"]="jiuwenclaw-web"
    ["RABBITMQ_NAME"]="rabbitmq"
    ["RABBITMQ_IMAGE"]="rabbitmq:3.9.22-management"
    ["RABBITMQ_PATH"]="/exports/rabbitmq"
    ["RABBITMQ_NFS_PATH"]="/rabbitmq"
    ["RABBITMQ_USER"]="admin"
    ["RABBITMQ_PASSWORD"]="Rabbitmq@123"
    ["RABBITMQ_STORAGE_SIZE"]="2Gi"
    ["MYSQL_NAME"]="mysql"
    ["MYSQL_IMAGE"]="mysql:8.0"
    ["MYSQL_PATH"]="/exports/mysql"
    ["MYSQL_NFS_PATH"]="/mysql"
    ["MYSQL_ROOT_PASSWORD"]="Root@123456"
    ["MYSQL_STORAGE_SIZE"]="4Gi"
    ["POSTGRES_NAME"]="postgresql"
    ["POSTGRES_IMAGE"]="postgres:16"
    ["POSTGRES_PATH"]="/exports/postgresql"
    ["POSTGRES_NFS_PATH"]="/postgresql"
    ["POSTGRES_PASSWORD"]="Root@123456"
    ["POSTGRES_STORAGE_SIZE"]="4Gi"
    ["REDIS_NAME"]="redis"
    ["REDIS_IMAGE"]="redis:7-alpine"
    ["REDIS_PORT"]="6379"
    ["REDIS_PASSWORD"]=""
    ["REDIS_DB"]="0"
    ["REDIS_KEY_PREFIX"]="jiuwenclaw:"
    ["REDIS_HOST"]=""
    ["DEPLOYMENT_MODE"]="standalone"
    ["GATEWAY_INSTANCE_ID"]=""
    ["MINIO_NAME"]="minio"
    ["MINIO_IMAGE"]="minio/minio-arm64:RELEASE.2024-12-18T13-15-44Z"
    ["MINIO_ROOT_USER"]="minioadmin"
    ["MINIO_ROOT_PASSWORD"]="Minio@123456"
    ["MINIO_STORAGE_SIZE"]="4Gi"
    ["MINIO_PATH"]="/exports/minio"
    ["MINIO_NFS_PATH"]="/minio"
    ["MANAGER_NAME"]="jiuwenclaw-manager"
    ["MANAGER_WEB_NAME"]="jiuwenclaw-manager-web"
    ["MANAGER_REST_PORT"]="8765"
    ["MANAGER_WS_PORT"]="8766"
    ["MANAGER_WEB_PORT"]="5273"
    ["DB_TYPE"]="sqlite"
    ["MANAGER_DB_NAME"]="manager"
    ["GATEWAY_DB_NAME"]="gateway"
    ["CHECKPOINTER_DB_NAME"]="jiuwenclawee"
    ["MANAGER_SQLITE_PATH"]="manager.db"
    ["GATEWAY_SQLITE_PATH"]="gateway.db"
    ["ENABLE_EXTERNAL_NFS"]="false"
    ["ENABLE_EXTERNAL_RABBITMQ"]="false"
    ["ENABLE_EXTERNAL_MYSQL"]="false"
    ["ENABLE_EXTERNAL_POSTGRES"]="false"
    ["ENABLE_EXTERNAL_REDIS"]="false"
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
