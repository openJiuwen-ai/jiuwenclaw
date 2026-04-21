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
            -h|--help)
                print_help
                ;;
            *)
                error "Invalid Args: ${args[$i]}"
                ;;
        esac
    done
    info "Executing command: $*"
    info "CMD=${CMD}"
}


# Print help info and exit
print_help() {
    cat << EOF
Usage: ./$(basename "$0") [COMMAND] [OPTIONS]

Commands:
  up        Start openyuanrong.
  down      Shutdown openyuanrong.

Options:
  -h,--help Show this help message.
EOF
    exit 0
}