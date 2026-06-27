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



urlencode() {
    local string="${1}"
    local strlen=${#string}
    local encoded=""
    local pos c o

    for (( pos=0 ; pos<strlen ; pos++ )); do
        c=${string:$pos:1}
        case "$c" in
            [-_.~a-zA-Z0-9] ) o="${c}" ;;
            * ) printf -v o '%%%02x' "'$c'"
        esac
        encoded+="${o}"
    done
    echo "${encoded}"
}