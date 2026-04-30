#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ==================== Log functions ====================
info() { echo -e "\033[36m=== $@ ===\033[0m"; }
success() { echo -e "\033[32m✅ $@\033[0m"; }
warning() { echo -e "\033[33m⚠️  $@\033[0m"; }
error() { echo -e "\033[31m❌ $@\033[0m"; exit 1; }


# Print all key-value pairs of bash array
print_array() {
    local array_name="$1"
    local -n arr_ref="$1"
    
    echo -e "\033[33m$ ${array_name}\033[0m"
    
    if [[ ! "$(declare -p ${array_name})" =~ "declare -a" && ! "$(declare -p ${array_name})" =~ "declare -A" ]]; then
        echo -e "\033[31m[ERROR] ${array_name} is not a bash array variable!\033[0m"
        return 1
    fi

    for key in "${!arr_ref[@]}"; do
        echo -e "\033[36m  ├─ ${array_name}[${key}] = ${arr_ref[${key}]}\033[0m"
    done
    
    echo -e "\033[33m  └─ Total elements count: ${#arr_ref[@]}\033[0m\n"
}

# Execute command on all worker nodes
exec_on_other_nodes() {
    local cmd="$1"

    info "===== Execute command on other Nodes ====="
    info "Command: ${cmd}"

    for node_ip in "${OTHER_NODE_IPS[@]}"; do
        info "Executing command on node ${node_ip}"

        # Execute command via SSH
        if ssh -o ConnectTimeout=5 \
               -o StrictHostKeyChecking=no \
               "${node_ip}" "${cmd}"; then
            success "Node ${node_ip} - Execute command successfully"
        else
            error "Node ${node_ip} - Execute command failed! Check network/SSH auth/permissions"
        fi
    done

    success "Execute command on all other Nodes successfully!"
}

sync_file_to_other_nodes() {
    local file=$1
    local file_dir=$(dirname "${file}")

    exec_on_other_nodes "mkdir -p ${file_dir}
        rm -rf ${file}"

    info "===== Sync file from current node to all other nodes ====="
    info "File: ${file}"
    for node_ip in "${OTHER_NODE_IPS[@]}"; do
        info "Syncing file to Worker ${node_ip}..."
        if scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${file}" ${node_ip}:${file_dir}; then
            success "Node ${node_ip} - Sync file successfully"
        else
            error "Node ${node_ip} - Sync file failed! Check network/SSH auth/permissions"
        fi
    done

    success "Sync file from current node to all other nodes successfully!"
}

exec_on_all_nodes() {
    cmd="$1"
    bash -c "${cmd}"
    exec_on_other_nodes "${cmd}"
}
