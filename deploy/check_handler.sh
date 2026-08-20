#!/usr/bin/env bash
set -euo >/dev/null 2>&1

check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        success "$1 is OK."
    else
        error "$1 is not installed. Please install it first."
    fi
}

check_yq() {
    local YQ_VERSION=$(yq --version 2>&1)

    check_cmd "yq"
    if echo "$YQ_VERSION" | grep -q "mikefarah" && echo "$YQ_VERSION" | grep -qE "version v4\.|version v[5-9]\."; then
        success "yq is OK: $YQ_VERSION"
    else
        error "The detected yq is not mikefarah/yq v4+. Current version info: $YQ_VERSION"
    fi
}

check_cmds() {
    for cmd in jq mount.nfs base64
    do
        check_cmd ${cmd}
    done

    check_yq

    local os_type=${DEPLOY_VARS["OS_TYPE"]}
    if [ "${os_type}" == "macos" ]; then
        for cmd in jot lsof
        do
            check_cmd ${cmd}
        done
    fi
}

detect_os() {
    if [ "$(uname -s)" != "Linux" ]; then
        error "Unsupported OS: ${os_type}"
    fi
    DEPLOY_VARS["OS_TYPE"]="linux"
}

check_if_root() {
    if [[ ${EUID} -ne 0 ]]; then
        error "This script must be run as root (sudo)."
    fi
}

# ======== Check if the cluster has at least 2 nodes ======== 
check_cluster_has_enough_nodes() {
    if [ "${CMD}" == "down"  ]; then
        return
    fi
    info "===== Checking cluster node count ====="

    # Get ready node count (only Ready nodes)
    local node_count=$(kubectl get nodes --no-headers | grep -w "Ready" | wc -l)

    # Check if node count >= 2
    if [[ ${node_count} -lt 2 ]]; then
        error "Cluster only has ${node_count} Ready node(s), at least 2 required!"
    fi

    success "Cluster has ${node_count} Ready nodes, check passed!"
}

check_dependency(){
    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    check_cmds
    check_if_root
}

check_if_nfs_up() {
    # Check if external NFS server
    if [ -n "${DEPLOY_VARS["NFS_SERVER_ADDR"]:-}" ]; then
        info "Use external NFS server"
        DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]="true"
        return
    fi

    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    # No Build-In NFS server
    if ! check_k8s_resource_exists "deployment" "${DEPLOY_VARS["NFS_NAME"]}"; then
        error "NFS is not deployed. Please deploy it first with: ./$(basename "$0") up nfs"
    fi

    info "Use built-in NFS server"
    fetch_current_node_ip
    DEPLOY_VARS["NFS_SERVER_ADDR"]=${DEPLOY_VARS["CURRENT_NODE_IP"]}
}

check_if_nfs_sc_up() {
    # Check if external PVC
    if [ -n "${DEPLOY_VARS["CLAW_PVC"]:-}" ]; then
        info "Use external PVC"
        DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]="true"
        return
    fi

    # No Build-In NFS provider
    if ! check_k8s_resource_exists "deployment" "${DEPLOY_VARS["NFS_SC_DNAME"]}"; then
        error "NFS_SC is not deployed. Please deploy it first with: ./$(basename "$0") up nfs-sc"
    fi

    DEPLOY_VARS["CLAW_PVC"]="jiuwenclaw-pvc"
}

check_if_mysql_up() {
    local name="${DEPLOY_VARS["MYSQL_NAME"]}"

    if [ "${DEPLOY_VARS["DB_TYPE"]}" != "mysql" ]; then
       return
    fi

    # Check if external MySQL server
    if [ -n "${DEPLOY_VARS["DB_HOST"]:-}" ]; then
        info "Use external MySQL server"
        if [ -z "${DEPLOY_VARS["DB_PORT"]:-}" ]; then
            error "Please define DB_PORT in .env.custom"

        fi
        DEPLOY_VARS["ENABLE_EXTERNAL_MYSQL"]="true"
        return
    fi

    # No Build-In MySQL server
    if ! check_k8s_resource_exists "statefulset" "${name}"; then
        error "MySQL is not deployed. Please deploy it first with: ./$(basename "$0") up mysql"
    fi

    info "Use built-in MySQL server"
    DEPLOY_VARS["DB_HOST"]="${name}-headless.default"
    DEPLOY_VARS["DB_PORT"]="3306"
    for module in MANAGER GATEWAY OBSERVABILITY
    do
        DEPLOY_VARS["${module}_DB_USER"]="root"
        DEPLOY_VARS["${module}_DB_PASSWORD"]=${DEPLOY_VARS["MYSQL_ROOT_PASSWORD"]}
    done
}

check_if_postgresql_up() {
    local name="${DEPLOY_VARS["POSTGRES_NAME"]}"

    if [ "${DEPLOY_VARS["DB_TYPE"]}" != "postgresql" ]; then
        return
    fi

    # Check if external PostgreSQL server
    if [ -n "${DEPLOY_VARS["DB_HOST"]:-}" ]; then
        info "Use external PostgreSQL server"
        if [ -z "${DEPLOY_VARS["DB_PORT"]:-}" ]; then
            error "Please define DB_PORT in .env.custom"
        fi
        DEPLOY_VARS["ENABLE_EXTERNAL_POSTGRES"]="true"
        return
    fi

    # No Build-In PostgreSQL server
    if ! check_k8s_resource_exists "statefulset" "${name}"; then
        error "PostgreSQL is not deployed. Please deploy it first with: ./$(basename "$0") up postgresql"
    fi

    info "Use built-in PostgreSQL server"
    DEPLOY_VARS["DB_HOST"]="${name}-headless.default"
    DEPLOY_VARS["DB_PORT"]="5432"

    for module in MANAGER GATEWAY OBSERVABILITY
    do
        DEPLOY_VARS["${module}_DB_USER"]="postgres"
        DEPLOY_VARS["${module}_DB_PASSWORD"]=${DEPLOY_VARS["POSTGRES_PASSWORD"]}
    done
}


check_if_db_up() {
    local db_type="${DEPLOY_VARS["DB_TYPE"]}"
    info "DB_TYPE: ${db_type}"
    if [ "${db_type}" == "sqlite" ]; then
        return
    fi 
    check_if_${db_type}_up

    if [[ "${DEPLOY_VARS["ENABLE_EXTERNAL_MYSQL"]}" == "true" || "${DEPLOY_VARS["ENABLE_EXTERNAL_POSTGRES"]}" == "true" ]]; then
        for module in MANAGER GATEWAY OBSERVABILITY
        do
            if [ -z "${DEPLOY_VARS["${module}_DB_USER"]:-}" ]; then
                DEPLOY_VARS["${module}_DB_USER"]=${DEPLOY_VARS["DB_USER"]}
            fi

            if [ -z "${DEPLOY_VARS["${module}_DB_USER"]:-}" ]; then
                error "Please set up ${module}_DB_USER or DB_USER."
            fi

            if [ -z "${DEPLOY_VARS["${module}_DB_PASSWORD"]:-}" ]; then
                DEPLOY_VARS["${module}_DB_PASSWORD"]=${DEPLOY_VARS["DB_PASSWORD"]}
            fi

            if [ -z "${DEPLOY_VARS["${module}_DB_PASSWORD"]:-}" ]; then
                error "Please set up ${module}_DB_PASSWORD or DB_PASSWORD."
            fi
        done
    fi

    # 企业 Web 会话历史库（选型看 WEB_DB_TYPE / DB_TYPE，与 GATEWAY_DB_TYPE 对称；
    # 连接用 WEB_DB_*；未单独配账号时从 GATEWAY_DB_* / DB_* 回填）
    if [ -z "${DEPLOY_VARS["WEB_DB_USER"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_USER"]=${DEPLOY_VARS["GATEWAY_DB_USER"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_USER"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_USER"]=${DEPLOY_VARS["DB_USER"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_PASSWORD"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_PASSWORD"]=${DEPLOY_VARS["GATEWAY_DB_PASSWORD"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_PASSWORD"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_PASSWORD"]=${DEPLOY_VARS["DB_PASSWORD"]:-}
    fi

    # 企业 Web 会话历史库（选型看 WEB_DB_TYPE / DB_TYPE，与 GATEWAY_DB_TYPE 对称；
    # 连接用 WEB_DB_*；未单独配账号时从 GATEWAY_DB_* / DB_* 回填）
    if [ -z "${DEPLOY_VARS["WEB_DB_USER"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_USER"]=${DEPLOY_VARS["GATEWAY_DB_USER"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_USER"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_USER"]=${DEPLOY_VARS["DB_USER"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_PASSWORD"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_PASSWORD"]=${DEPLOY_VARS["GATEWAY_DB_PASSWORD"]:-}
    fi
    if [ -z "${DEPLOY_VARS["WEB_DB_PASSWORD"]:-}" ]; then
        DEPLOY_VARS["WEB_DB_PASSWORD"]=${DEPLOY_VARS["DB_PASSWORD"]:-}
    fi
}

check_if_obs_up() {
    local name="${DEPLOY_VARS["MINIO_NAME"]}"

    # Check if external OBS server
    if [ -n "${DEPLOY_VARS["OBS_URL"]:-}" ]; then
        info "Use external OBS server"
        DEPLOY_VARS["ENABLE_EXTERNAL_OBS"]="true"
        return
    fi

    # No Build-In Minio server
    if ! check_k8s_resource_exists "statefulset" "${name}"; then
        error "Minio is not deployed. Please deploy it first with: ./$(basename "$0") up minio"
    fi

    info "Use built-in Minio server"
}

check_if_redis_up() {
    local mode="${DEPLOY_VARS["DEPLOYMENT_MODE"]:-standalone}"
    local name="${DEPLOY_VARS["REDIS_NAME"]}"

    if [[ "${mode}" != "active-standby" ]]; then
        info "DEPLOYMENT_MODE=${mode}, skip Redis check"
        return
    fi

    if [ -n "${DEPLOY_VARS["REDIS_HOST"]:-}" ]; then
        info "Use external Redis server"
        DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]="true"
        return
    fi

    # No Build-In Redis server
    if ! check_k8s_resource_exists "deployment" "${name}"; then
        error "Redis is not deployed. Please deploy it first with: ./$(basename "$0") up redis"
    fi

    info "Use built-in Redis server"
    DEPLOY_VARS["REDIS_HOST"]="${name}.default.svc.cluster.local"
    DEPLOY_VARS["REDIS_PORT"]="6379"
}

check_if_jina_up() {
    local name="${DEPLOY_VARS["JINA_NAME"]}"
    local rname="${name}-reader"
    local cname="${name}-cache-proxy"

    if check_k8s_resource_exists "deployment" "${rname}" && check_k8s_resource_exists "deployment" "${cname}"; then
        DEPLOY_VARS["JINA_READER_ENDPOINT"]="http://${name}-cache-proxy-svc.default"
        info "Use built-in Jina server"
    fi
}

check_if_rabbitmq_up() {
    local name="${DEPLOY_VARS["RABBITMQ_NAME"]}"
    local user=${DEPLOY_VARS["RABBITMQ_USER"]}
    local password=${DEPLOY_VARS["RABBITMQ_PASSWORD"]}
    local url=""
    local encoded_password=$(urlencode "$password")

    # Check if external RABBITMQ server
    if [ -n "${DEPLOY_VARS["RABBITMQ_URL"]:-}" ]; then
        info "Use external RABBITMQ server"
        url="${DEPLOY_VARS["RABBITMQ_URL"]}"
        DEPLOY_VARS["MANAGER_RABBITMQ_URL"]="amqp://${user}:${encoded_password}@${url}"
        DEPLOY_VARS["ENABLE_EXTERNAL_RABBITMQ"]="true"
        return
    fi

    # No Build-In RABBITMQ server
    if ! check_k8s_resource_exists "statefulset" "${name}"; then
        error "RABBITMQ is not deployed. Please deploy it first with: ./$(basename "$0") up rabbitmq"
    fi

    info "Use built-in RABBITMQ server"
    url="${name}-headless.default:5672"
    DEPLOY_VARS["MANAGER_RABBITMQ_URL"]="amqp://${user}:${encoded_password}@${url}"
}

check_if_otel_up() {
    local name="${DEPLOY_VARS["OTEL_NAME"]}"

    # Check if external Opentelemetry Collector
    if [ -n "${DEPLOY_VARS["OTEL_EXPORTER_OTLP_ENDPOINT"]:-}" ]; then
        info "Use external Opentelemetry Collector"
        DEPLOY_VARS["ENABLE_EXTERNAL_OTEL"]="true"
        return
    fi

    info "Use built-in Opentelemetry Collector"
    DEPLOY_VARS["OTEL_EXPORTER_OTLP_ENDPOINT"]="http://${name}:4318"
}

check_if_prometheus_up() {
    local name="${DEPLOY_VARS["PROMETHEUS_NAME"]}"

    # Check if external Prometheus server
    if [ -n "${DEPLOY_VARS["PROMETHEUS_URL"]:-}" ]; then
        info "Use external Prometheus server"
        DEPLOY_VARS["ENABLE_EXTERNAL_PROMETHEUS"]="true"
        return
    fi
    
    info "Use built-in Prometheus server"
    DEPLOY_VARS["PROMETHEUS_URL"]="http://${name}:9090"
}

check_if_tempo_up() {
    local name="${DEPLOY_VARS["TEMPO_NAME"]}"

    # Check if external Tempo server
    if [ -n "${DEPLOY_VARS["TEMPO_URL"]:-}" ]; then
        info "Use external Tempo server"
        DEPLOY_VARS["ENABLE_EXTERNAL_TEMPO"]="true"
        return
    fi

    info "Use built-in Tempo server"
    DEPLOY_VARS["TEMPO_URL"]="http://${name}:3100"
}

check_if_loki_up() {
    local name="${DEPLOY_VARS["LOKI_NAME"]}"

    # Check if external Loki server
    if [ -n "${DEPLOY_VARS["LOKI_URL"]:-}" ]; then
        info "Use external Loki server"
        DEPLOY_VARS["ENABLE_EXTERNAL_LOKI"]="true"
        return
    fi

    info "Use built-in Loki server"
    DEPLOY_VARS["LOKI_URL"]="http://${name}:3100"
}

check_nfs_up_dependency(){
    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    local arch=$(uname -m)

    if [[ "$arch" =~ ^aarch64 || "$arch" =~ arm ]]; then
        error "ARM arch unsupported for NFS, abort deployment."
    fi
}

check_nfs_sc_up_dependency(){
    check_if_nfs_up
}

check_mysql_up_dependency(){
    local mysql_path="${DEPLOY_VARS["NFS_POD_PATH"]}/${DEPLOY_VARS["MYSQL_NAME"]}"
    local nfs_dname=${DEPLOY_VARS["NFS_NAME"]}

    check_if_nfs_up

    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]}" == "false" ]; then
        info "Preparing MySQL data directory: ${mysql_path}"
        local nfs_pod=$(kubectl get pods -n default -l app=${nfs_dname} -o jsonpath='{.items[0].metadata.name}')

        info "Executing: kubectl exec ${nfs_pod} -- sh -c \"mkdir -p ${mysql_path}\""
        kubectl exec ${nfs_pod} -- sh -c "mkdir -p ${mysql_path}"
        success "MySQL directory created successfully in NFS Pod!"
    fi
}


postgresql_up_dependency(){
    prepare_nfs_path "${DEPLOY_VARS["POSTGRES_NAME"]}" "root" "root"
}

check_minio_up_dependency(){
    prepare_nfs_path "${DEPLOY_VARS["MINIO_NAME"]}" "root" "root"
}

check_redis_up_dependency() {
    info "Redis module has no dependencies"
}

check_rabbitmq_up_dependency(){
    prepare_nfs_path "${DEPLOY_VARS["RABBITMQ_NAME"]}" "root" "root"
}

check_log_up_dependency(){
    if [ -z "${DEPLOY_VARS["CLAW_LOG_DIR"]:-}" ]; then
        DEPLOY_VARS["CLAW_LOG_DIR"]="${HOME}/claw_logs"
    fi

    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    local log_dir="${DEPLOY_VARS["CLAW_LOG_DIR"]}"
    exec_cmd mkdir -p "${log_dir}"
    exec_cmd chmod 755 "${log_dir}"
}

check_jina_up_dependency() {
    info "JINA module has no dependencies"
}

check_gateway_up_dependency(){

    if [ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "nfs" ]; then
        prepare_nfs_path "jiuwenclaw" "1000" "1000"
    elif [ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" ]; then
        check_if_nfs_sc_up
    fi

    check_if_db_up
    check_if_redis_up
    check_if_jina_up
    check_if_otel_up
}

check_web_up_dependency(){
    check_if_db_up
    check_if_obs_up

    if ! check_k8s_resource_exists "deployment" "${DEPLOY_VARS["GATEWAY_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"; then
        error "GATEWAY is not deployed. Please deploy it first with: ./$(basename "$0") up gateway"
    fi
}

check_manager_up_dependency(){
    #check_if_rabbitmq_up
    check_if_db_up
}

check_monitor_up_dependency(){
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi

    check_if_db_up
    check_if_otel_up
    check_if_prometheus_up
    check_if_tempo_up
    check_if_loki_up

    prepare_nfs_path "${DEPLOY_VARS["PROMETHEUS_NAME"]}" "root" "root"
    prepare_nfs_path "${DEPLOY_VARS["TEMPO_NAME"]}" "root" "root"
    prepare_nfs_path "${DEPLOY_VARS["LOKI_NAME"]}" "root" "root"
}



prepare_nfs_path() {
    local name="$1"
    local user="$2"
    local group="$3"
    local path="${DEPLOY_VARS["NFS_POD_PATH"]}/${name}"
    local nfs_dname=${DEPLOY_VARS["NFS_NAME"]}

    check_if_nfs_up

    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return
    fi

    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_NFS"]}" == "true" ]; then
        return
    fi

    info "Preparing ${module} data directory: ${path}"
    local nfs_pod=$(kubectl get pods -n default -l app=${nfs_dname} -o jsonpath='{.items[0].metadata.name}')
    info "Executing: kubectl exec ${nfs_pod} -- sh -c \"mkdir -p ${path} && chown -R ${user}:${group} ${path} && chmod -R 755 ${path}\""
    kubectl exec ${nfs_pod} -- sh -c "mkdir -p ${path} && chown -R ${user}:${group} ${path} && chmod -R 755 ${path}"
    success "${module} directory created successfully in NFS Pod!"
}
