#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# == read key-value pairs from file into array (for first start-up) ==
read_env_from_file() {
    local env_file="$1"
    local -n target_array="$2"

    if [ ! -f "${env_file}" ]; then
        error "Config file not found: ${env_file}"
    fi

    info "Loading config: ${env_file}"

    local key=""
    local value=""
    local buffer=""
    local in_quote=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        trimmed_line="${line#"${line%%[![:space:]]*}"}"
        if [[ $in_quote -eq 0 && ($trimmed_line == \#* || -z "${trimmed_line}") ]]; then
            continue
        fi

        if (( in_quote )); then
            buffer+=$'\n'"$line"
        else
            buffer="$line"
        fi

        if [[ $in_quote -eq 0 && $buffer =~ ^([A-Za-z0-9_]+)=\"(.*) ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            
            if [[ $value =~ (.*)\"[[:space:]]*$ ]]; then
                target_array["$key"]="${BASH_REMATCH[1]}"
                in_quote=0
            else
                buffer="$value"
                in_quote=1
            fi
            continue
        fi

        if (( in_quote )); then
            if [[ $buffer =~ (.*)\"[[:space:]]*$ ]]; then
                target_array["$key"]="${BASH_REMATCH[1]}"
                in_quote=0
                key=""
                buffer=""
            fi
            continue
        fi

        if [[ $buffer =~ ^([A-Za-z0-9_]+)=(.*) ]]; then
            local k="${BASH_REMATCH[1]}"
            local v="${BASH_REMATCH[2]}"
            v="${v#\"}"
            v="${v%\"}"
            target_array["$k"]="$v"
        fi

        buffer=""
    done < "$env_file"

    success "Loaded config: ${env_file}"
}

# ===== Writes sorted key-value pairs to .env.<Instance ID> file =====
write_env_to_file() {
    local env_file=$1
    local -n source_array=$2

    info "Writing $2 to config file: ${env_file}"
    > "${env_file}"
    printf "%s\n" "${!source_array[@]}" | sort | while read -r key; do
        if [ -n "${key}" ]; then
            echo "${key}=${source_array[${key}]}" >> "${env_file}"
        fi
    done
    success "Generated config file : ${env_file}"
}

# 生成 uuid4 格式实例 ID（小写，带连字符）；失败时返回非零，由调用方在主 shell 中处理
gen_uuid4() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import uuid; print(uuid.uuid4())'
        return 0
    fi
    return 1
}

# 若 .env.custom 未配置 JIUWENCLAW_ID，则生成 uuid4 并写回（供 Gateway 启动使用）
ensure_jiuwenclaw_id_in_custom_env() {
    local env_file="${CUSTOM_ENV_FILE}"
    local auto_gen="${DEPLOY_VARS["AUTO_JIUWENCLAW_ID"]:-true}"

    if [ "${auto_gen}" != "true" ]; then
        info "AUTO_JIUWENCLAW_ID=false, skip JIUWENCLAW_ID auto-generation"
        return 0
    fi

    local current="${DEPLOY_VARS["JIUWENCLAW_ID"]:-}"
    current="${current#\"}"
    current="${current%\"}"
    current="${current//[[:space:]]/}"

    if [ -n "${current}" ]; then
        info "JIUWENCLAW_ID already set in ${env_file}, skip auto-generation"
        return 0
    fi

    local new_id
    new_id="$(gen_uuid4)" || true
    new_id="${new_id//[[:space:]]/}"
    if [ -z "${new_id}" ]; then
        error "Cannot generate JIUWENCLAW_ID: uuidgen or python3 is required"
    fi

    if grep -qE '^[[:space:]]*JIUWENCLAW_ID=' "${env_file}"; then
        sed -i "s/^[[:space:]]*JIUWENCLAW_ID=.*/JIUWENCLAW_ID=\"${new_id}\"/" "${env_file}"
    else
        printf '\nJIUWENCLAW_ID="%s"\n' "${new_id}" >> "${env_file}"
    fi

    DEPLOY_VARS["JIUWENCLAW_ID"]="${new_id}"
    success "Auto-generated JIUWENCLAW_ID=${new_id} in ${env_file}"
}
