#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# =============================================================================
# CORE DATA STRUCTURE
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CUSTOM_ENV_FILE="${SCRIPT_DIR}/.env.custom"
TEMPLATE_DIR="${SCRIPT_DIR}/templates"
CONFIG_DIR="${SCRIPT_DIR}/conf"

# ===== Core project configuration (paths, ports, commands, OS info) =====
declare -A CONFIG=(
    ["GATEWAY_CONFIG_FILE"]="${CONFIG_DIR}/gateway-config.yaml"

    ["GATEWAY_ENV_TEMPLATE_FILE"]="${TEMPLATE_DIR}/gateway.template.env"
    ["GATEWAY_ENV_FILE"]="${CONFIG_DIR}/gateway.env"

    ["GATEWAY_TEMPLATE_FILE"]="${TEMPLATE_DIR}/gateway.template.yaml"
    ["GATEWAY_FILE"]="${CONFIG_DIR}/gateway.yaml"

    ["CLAW_PVC_TEMPLATE_FILE"]="${TEMPLATE_DIR}/claw-pvc.template.yaml"
    ["CLAW_PVC_FILE"]="${CONFIG_DIR}/claw-pvc.yaml"

    ["NFS_TEMPLATE_FILE"]="${TEMPLATE_DIR}/nfs.template.yaml"
    ["NFS_FILE"]="${CONFIG_DIR}/nfs.yaml"

    ["NFS_SC_TEMPLATE_FILE"]="${TEMPLATE_DIR}/nfs-sc.template.yaml"
    ["NFS_SC_FILE"]="${CONFIG_DIR}/nfs-sc.yaml"

    ["RABBITMQ_TEMPLATE_FILE"]="${TEMPLATE_DIR}/rabbitmq.template.yaml"
    ["RABBITMQ_FILE"]="${CONFIG_DIR}/rabbitmq.yaml"

    ["MYSQL_TEMPLATE_FILE"]="${TEMPLATE_DIR}/mysql.template.yaml"
    ["MYSQL_FILE"]="${CONFIG_DIR}/mysql.yaml"

    ["REDIS_TEMPLATE_FILE"]="${TEMPLATE_DIR}/redis.template.yaml"
    ["REDIS_FILE"]="${CONFIG_DIR}/redis.yaml"

    ["POSTGRES_TEMPLATE_FILE"]="${TEMPLATE_DIR}/postgresql.template.yaml"
    ["POSTGRES_FILE"]="${CONFIG_DIR}/postgresql.yaml"

    ["MINIO_TEMPLATE_FILE"]="${TEMPLATE_DIR}/minio.template.yaml"
    ["MINIO_FILE"]="${CONFIG_DIR}/minio.yaml"

    ["WEB_TEMPLATE_FILE"]="${TEMPLATE_DIR}/web.template.yaml"
    ["WEB_FILE"]="${CONFIG_DIR}/web.yaml"

    ["MANAGER_SERVER_TEMPLATE_FILE"]="${TEMPLATE_DIR}/manager-server.template.yaml"
    ["MANAGER_SERVER_FILE"]="${CONFIG_DIR}/manager-server.yaml"

    ["MANAGER_WEB_TEMPLATE_FILE"]="${TEMPLATE_DIR}/manager-web.template.yaml"
    ["MANAGER_WEB_FILE"]="${CONFIG_DIR}/manager-web.yaml"

    ["PV_TEMPLATE_FILE"]="${TEMPLATE_DIR}/pv-nfs.template.yaml"
    ["PV_FILE"]="${CONFIG_DIR}/pv-nfs.yaml"

    ["PVC_TEMPLATE_FILE"]="${TEMPLATE_DIR}/pvc-nfs.template.yaml"
    ["PVC_FILE"]="${CONFIG_DIR}/pvc-nfs.yaml"

    ["START_PORT"]="30000"
    ["END_PORT"]="32767"
)


# Parsed command-line arguments
declare -A ARGS=(
    ["CMD"]=""
)


# ==== All available modules ====
#declare -ga ALL_MODULES=("NFS" "RABBITMQ" "YR_CLAW" "GATEWAY" "WEB" "MANAGER")
declare -ga ALL_MODULES=("NFS" "NFS-SC" "RABBITMQ" "MYSQL" "REDIS" "POSTGRESQL" "MINIO" "GATEWAY" "WEB" "MANAGER")

declare -ga MODULES=()

declare -ga WORKER_NODE_IPS=()

declare -ga OTHER_MASTER_IPS=()

declare -ga OTHER_NODE_IPS=()

declare -A DEPLOY_VARS=(
    ["AGENT_SERVER_HOME"]="/home/app"
    ["AGENT_SERVER_POD_NAME"]="jiuwenclaw-agentserver"
    ["AGENT_SERVER_SERVICE_CONCURRENCY"]="10"
    ["AGENT_SERVER_SESSION_CONCURRENCY"]="10"
    ["AGENT_BOT_ID_GROUP_NUM"]="0"
    ["AGENT_SERVER_REACT_MAX_ITER"]="10"
    ["CLAW_CODE_PATH"]=""
    ["CLAW_CODE_POD_PATH"]="/app/jiuwenclaw"
    ["CLAW_MOUNT_TYPE"]="pvc"
    ["CLAW_STORAGE_SIZE"]="1Gi"
    ["DB_TYPE"]="sqlite"
    ["DEPLOYMENT_MODE"]="standalone"
    ["ENTERPRISE_WEB_WS_PORT"]="19000"
    ["ENABLE_EXTERNAL_MINIO"]="false"
    ["ENABLE_EXTERNAL_MYSQL"]="false"
    ["ENABLE_EXTERNAL_NFS"]="false"
    ["ENABLE_EXTERNAL_PVC"]="false"
    ["ENABLE_EXTERNAL_POSTGRES"]="false"
    ["ENABLE_EXTERNAL_RABBITMQ"]="false"
    ["ENABLE_EXTERNAL_REDIS"]="false"
    ["ENABLE_GATEWAY_SCHED_LABEL"]="false"
    ["FUNC_SVC_NAME"]="0@jiuwen@clawtest"
    ["GATEWAY_CLAW_WS_PING_INTERVAL"]="20.0"
    ["GATEWAY_CLAW_WS_PING_TIMEOUT"]="20.0"
    ["TOOL_RESULT_DISPLAY_MAX_CHARS"]="500"
    ["GATEWAY_CONFIG_MAP_NAME"]="jiuwenclaw-gateway-config"
    ["GATEWAY_DB_NAME"]="gateway"
    ["GATEWAY_DB_MAX_OVERFLOW"]="20"
    ["GATEWAY_DB_POOL_SIZE"]="2"
    ["GATEWAY_DB_POOL_TIMEOUT"]="30"
    ["GATEWAY_ENV_FILE_NAME"]="jiuwenclaw-gateway-env"
    ["GATEWAY_INSTANCE_ID"]=""
    ["GATEWAY_LOG_MASKING_ENABLED"]="true"
    ["GATEWAY_NAME"]="jiuwenclaw-gateway"
    ["GATEWAY_PG_SCHEMA"]="public"
    ["GATEWAY_REPLICAS"]="1"
    ["GATEWAY_SERVICE_ACCOUNT"]="jiuwenclaw-gateway-sa"
    ["GATEWAY_SQLITE_PATH"]="gateway.db"
    ["IS_UP_MANAGER_WEB"]="true"
    ["JIUWENBOX_CODE_POD_PATH"]="/usr/local/lib/python3.11/site-packages/jiuwenbox"
    ["JIUWENCLAW_ID"]=""
    ["MANAGER_DB_NAME"]="manager"
    ["MANAGER_PG_SCHEMA"]="public"
    ["MANAGER_REST_PORT"]="8765"
    ["MANAGER_SERVER_NAME"]="jiuwenclaw-manager-server"
    ["MANAGER_SQLITE_PATH"]="manager.db"
    ["MANAGER_WEB_NAME"]="jiuwenclaw-manager-web"
    ["MANAGER_WEB_PORT"]="5273"
    ["MANAGER_WS_PORT"]="8766"
    ["MINIO_IMAGE"]="minio/minio-arm64:RELEASE.2024-12-18T13-15-44Z"
    ["MINIO_NAME"]="minio"
    ["MINIO_REGION"]="default"
    ["MINIO_ROOT_PASSWORD"]="Minio@123456"
    ["MINIO_ROOT_USER"]="minioadmin"
    ["MINIO_SECURE"]="false"
    ["MINIO_STORAGE_SIZE"]="4Gi"
    ["MODE"]="product"
    ["MYSQL_IMAGE"]="mysql:8.0"
    ["MYSQL_NAME"]="mysql"
    ["MYSQL_ROOT_PASSWORD"]="Root@123456"
    ["MYSQL_MAX_CONNECTION"]="151"
    ["MYSQL_STORAGE_SIZE"]="4Gi"
    ["NAMESPACE"]="default"
    ["NFS_HOST_PATH"]="/data/nfs"
    ["NFS_IMAGE"]="ghcr.io/obeone/nfs-server:2.2.2"
    ["NFS_NAME"]="nfs-server"
    ["NFS_POD_PATH"]="/exports"
    ["NFS_SC_DNAME"]="nfs-provisioner"
    ["NFS_SC_IMAGE"]="registry.k8s.io/sig-storage/nfs-subdir-external-provisioner:v4.0.2"
    ["NFS_SC_NAME"]="nfs-storage"
    ["NFS_SHARE_PATH"]="/"
    ["NO_CHECK_PORTS"]="false"
    ["OBS_BUCKET"]="jiuwenclaw"
    ["OBS_PUBLIC_BASE_URL"]=""
    ["OBS_TYPE"]="minio"
    ["POOL_ID"]="claw"
    ["POSTGRES_IMAGE"]="postgres:16"
    ["POSTGRES_NAME"]="postgresql"
    ["POSTGRES_PASSWORD"]="Root@123456"
    ["POSTGRES_MAX_CONNECTION"]="100"
    ["POSTGRES_STORAGE_SIZE"]="4Gi"
    ["PVC_NAME"]="pvc-nfs-shared"
    ["PV_NAME"]="pv-nfs-shared"
    ["RABBITMQ_IMAGE"]="rabbitmq:3.9.22-management"
    ["RABBITMQ_NAME"]="rabbitmq"
    ["RABBITMQ_PASSWORD"]="Rabbitmq@123"
    ["RABBITMQ_STORAGE_SIZE"]="2Gi"
    ["RABBITMQ_USER"]="admin"
    ["REDIS_DB"]="0"
    ["REDIS_HOST"]=""
    ["REDIS_IMAGE"]="redis:7-alpine"
    ["REDIS_KEY_PREFIX"]="jiuwenclaw:"
    ["REDIS_MODE"]="standalone"
    ["REDIS_NAME"]="redis"
    ["REDIS_PASSWORD"]=""
    ["REDIS_PORT"]="6379"
    ["REDIS_MODE"]="standalone"
    ["RENDER_ONLY"]="false"
    ["RUNTIME_CODE_PATH"]=""
    ["RUNTIME_CODE_POD_PATH"]="/usr/local/lib/python3.11/site-packages/openjiuwen_runtime"
    ["TIMEZONE"]="Asia/Shanghai"
    ["WEB_NAME"]="jiuwenclaw-web"
    ["WS_ALLOWED_ORIGINS"]=""
    ["WS_ORIGIN_CHECK_ENABLED"]="false"
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
