#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ========== Parses command-line arguments ========== 
parse_args() {
    local i=0
    local args=("$@")

    while [ $i -lt ${#args[@]} ]; do
        case "${args[$i]}" in
            up|down)
                CMD="${args[$i]}"
                i=$((i+1))
                ;;
            claw|nfs)
                # treat as modules
                local module="${args[$i]^^}"
                MODULES+=("${module}")
                i=$((i+1))
                ;;
            all)
                MODULES=("${ALL_MODULES[@]}")
                i=$((i+1))
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

    # If no modules are specified, deploy only CLAW by default
    if [ ${#MODULES[@]} -eq 0 ]; then
        echo "chenhui: no args."
        MODULES=(CLAW)
    fi

    info "Executing command: $*"
    info "CMD=${CMD}"
    info "MODULES=${MODULES[@]}"
}

# Print help info and exit
print_help() {
    cat << EOF
Usage: ./$(basename "$0") [COMMAND] [MODULES]

Commands:
  up        Start openyuanrong
  down      Shutdown openyuanrong

Modules:
  claw      Deploy CLAW module (default)
  nfs       Deploy NFS module
  all       Deploy all modules

Options:
  -h,--help Show this help message
EOF
    exit 0
}