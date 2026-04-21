#!/usr/bin/env bash
set -euo >/dev/null 2>&1

source "./global_vars.sh"
source "./common.sh"
source "./cmd_handler.sh"
source "args_handler.sh"
source "oyr_handler.sh"    
source "check_handler.sh"
source "envfile_handler.sh"
source "k8s_handler.sh"
source "template_handler.sh"


# ==================== Main function ====================
main() {
    parse_args "$@"

    case "${CMD}" in
        up)
            check_dependency
            read_env_from_file "${CUSTOM_ENV_FILE}" "DEPLOY_VARS"
            #print_array DEPLOY_VARS
            collect_k8s_cluster_info
            deploy_oyr
            ;;
    
        down)
            check_if_master
            uninstall_oyr
            ;;
    esac
}

# Execute main function
main "$@"
