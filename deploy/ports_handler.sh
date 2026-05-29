#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ======== Check if a single port is occupied ===============
is_port_occupied() {
    local port="$1"
    local port_occupied=0
    local os_type=${DEPLOY_VARS["OS_TYPE"]}

    case "${os_type}" in
        macos)
            # macOS: use lsof which is more reliable
            if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
                port_occupied=1
            fi
            ;;
        linux)
            netstat_output=$(netstat -tuln 2>&1)
            if echo "${netstat_output}" | grep -q ":$port"; then
                port_occupied=1
            fi
            ;;
        windows)
            # Windows Git Bash/Cygwin: Match LISTENING state in netstat -an output
            if netstat -an | grep -qiE ":$port[^0-9].*LISTENING.*" 2>/dev/null; then
                port_occupied=1
            fi
            ;;
    esac

    # Return result: 0 = occupied, 1 = available
    if [ "$port_occupied" -eq 1 ]; then
        return 0
    else
        return 1
    fi
}

# =========== Allocate multiple available ports at once ==============
# Usage: find_available_port "PORT_NAME_1" ["PORT_NAME_2" ...]
# Finds and assigns an available port for each provided port name
find_available_port() {
    local start_port=${CONFIG["START_PORT"]}
    local end_port=${CONFIG["END_PORT"]}

    # Iterate over all passed port name arguments
    for port_name in "$@"; do
        for port in $(seq "$start_port" "$end_port"); do
            if ! is_port_occupied "$port"; then
                DEPLOY_VARS["${port_name}"]="$port"
                # Move start port forward to avoid reusing the same port
                start_port=$((port + 1))
                break
            fi
        done
    done
}
