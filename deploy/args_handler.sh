#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ========== Parses command-line arguments ========== 
parse_args() {
    local i=0
    local args=("$@")

    while [ $i -lt ${#args[@]} ]; do
        case "${args[$i]}" in
            up|down|restart)
                CMD="${args[$i]}"
                i=$((i+1))
                ;;
            nfs|rabbitmq|yr_claw|gateway|web)
                MODULES+=("${args[$i]^^}")
                i=$((i+1))
                ;;
            -n)
                DEPLOY_VARS["NAMESPACE"]="${args[$((i+1))]}"
                i=$((i+2))
                ;;
            --web-port)
                DEPLOY_VARS["WEB_HOST_PORT"]="${args[$((i+1))]}"
                i=$((i+2))
                ;;
            -h|--help)
                print_help
                ;;
            *)
                error "Invalid Args: ${args[$i]}"
                ;;
        esac
    done

    # Verify that the command must exist
    if [ -z "${CMD:-}" ]; then
        error "Command not specified! Use 'up' or 'down'"
        exit 1
    fi

    # If no modules are specified, deploy all by default
    if [ ${#MODULES[@]} -eq 0 ]; then
        process_modules
    fi

    info "Executing command: $*"
    info "CMD=${CMD}"
    info "MODULES=${MODULES[@]}"
    info "NAMESPACE=${DEPLOY_VARS["NAMESPACE"]}"
}

process_modules() {
    MODULES=("GATEWAY")
    if [ "${DEPLOY_VARS["AGENT_RUNTIME"]}" == "yuanrong" ]; then
        MODULES+=("YR_CLAW")
    fi
}

# Print help info and exit
# Print help info and exit
print_help() {
    cat << EOF
Usage: ./$(basename "$0") [COMMAND] [MODULES...] [OPTIONS]

Commands (Required):
  up        Deploy and start specified modules
  down      Stop and uninstall specified modules
  restart   Restart specified modules

Modules (Optional):
  nfs       NFS service module (deploys to default namespace, ignores -n parameter)
  yr_claw   OpenYuanRong CLAW module
  gateway   Gateway service module
  web       Web frontend module
  rabbitmq  RabbitMQ module

Options:
  -n NAMESPACE       Specify Kubernetes namespace (defaults to default if unspecified)
  --web-port PORT    Set host port for web service (default: 8080)
  -h, --help         Display this help message and exit

Examples:
  ./$(basename "$0") up                                # Deploy default modules in default namespace
  ./$(basename "$0") up web --web-port 8000 -n myns    # Deploy web module with host port 8000 in myns namespace
  ./$(basename "$0") up nfs                            # Deploy NFS (always uses default namespace, ignores -n parameter)
  ./$(basename "$0") down                              # Uninstall default modules in default namespace
  ./$(basename "$0") restart                           # Restart default modules in default namespace
  ./$(basename "$0") restart gateway                   # Restart gateway module in default namespace
EOF
    exit 0
}

process_namespace() {
    local namespace=${DEPLOY_VARS["NAMESPACE"]}
    local client_type="${DEPLOY_VARS["AGENT_RUNTIME"]}"

    if [ "${client_type}" == "yuanrong" ]; then
        # Cluster-scoped resources cannot be isolated by namespace;
        # use name suffixing to achieve resource isolation
        DEPLOY_VARS["PV_NAME"]="${DEPLOY_VARS["PV_NAME"]}-${namespace}"
        DEPLOY_VARS["POOL_ID"]="${DEPLOY_VARS["POOL_ID"]}-${namespace}"
    fi
}
