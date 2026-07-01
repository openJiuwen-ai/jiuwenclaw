#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# === Extracts and deduplicates <<variable>> placeholders from template ===
extract_placeholders() {
    local templatefile="$1"
    local -a placeholders=($(grep -oE '<<[^>]+>>' "${templatefile}" | sort -u))
    echo "${placeholders[@]}"
}

# ==== Replaces <<variable>> placeholder with its value ===
replace_placeholder() {
    local placeholder="$1"
    local destfile="$2"
    local vars_arr_name=$3
    local var_name=$(echo "${placeholder}" | sed -e 's/^<<//' -e 's/>>$//')
    local arr_key_ref="${vars_arr_name}[${var_name}]"
    local var_value="${!arr_key_ref:-}"
    local os_type=${DEPLOY_VARS["OS_TYPE"]}

    #info "  Replacing placeholder: ${placeholder} → ${var_value}"
    if [ "${os_type}" == "macos" ]; then
        # macOS sed requires backup extension with -i
        sed -i.bak "s|${placeholder}|${var_value}|g" "${destfile}"
        rm -f "${destfile}.bak"
    else
        # Linux/Windows: use awk
        awk -v ph="${placeholder}" -v val="${var_value}" '
            { gsub(ph, val); print }
        ' "${destfile}" > "${destfile}.tmp" && mv -f "${destfile}.tmp" "${destfile}"
    fi
}

# ==== Renders configuration from template and replaces variables ===
render_config_template(){
    local templatefile=$1
    local destfile=$2
    local var_name=$3
    # Verify template file exists
    if [ ! -f "${templatefile}" ]; then
        error "Template file does not exist: ${templatefile}"
    fi
    info "Using template file: ${templatefile}"

    # Extract all placeholders
    local -a placeholders=($(extract_placeholders "${templatefile}"))
    if [ ${#placeholders[@]} -eq 0 ]; then
        warning "No <<variable_name>> format placeholders found in template file"
    fi

    # Copy template as target file
    exec_cmd cp -f ${templatefile} ${destfile}

    # Loop to replace each placeholder
    info "Starting placeholder replacement..."
    for placeholder in "${placeholders[@]}"; do
        replace_placeholder "${placeholder}" "${destfile}" "${var_name}"
    done

    success "Generated config file: ${destfile}"
}

enable_dev_mode_if_needed() {
    if [ "${DEPLOY_VARS["MODE"]}" != "dev" ]; then
        return
    fi

    local file="$1"
    mount_claw_code_path "${file}"
    mount_runtime_code_path "${file}"

    # Force pod to be scheduled on current master node
    yq eval 'select(.kind == "Deployment").spec.template.spec.nodeName = "'"${DEPLOY_VARS["MASTER_NODE_NAME"]}"'"' -i "${file}"

    # Security context adaptation for root user
    yq eval 'select(.kind == "Deployment").spec.template.spec.securityContext.fsGroup = 0' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation = true' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsNonRoot = false' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsUser = 0' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsGroup = 0' -i "${file}"
}


# Mount claw source code from host into container
mount_claw_code_path() {
    if [ -z "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ]; then
        return
    fi

    local file="$1"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts += [{
            "name": "claw-code",
            "mountPath": "'"${DEPLOY_VARS["CLAW_CODE_POD_PATH"]}"'"
        }]
    ' -i "${file}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.volumes += [{
            "name": "claw-code",
            "hostPath": {
                "path": "'"${DEPLOY_VARS["CLAW_CODE_PATH"]}"'",
                "type": "Directory"
            }
        }]
    ' -i "${file}"
}

# Mount runtime source code from host into container
mount_runtime_code_path() {
    if [ -z "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ]; then
        return
    fi

    local file="$1"
    local code="${DEPLOY_VARS["RUNTIME_CODE_PATH"]}"
    local mpath="${code}/management/openjiuwen_runtime/management"
    local fpath="${code}/foundation/openjiuwen_runtime/foundation"
    local pod_code="${DEPLOY_VARS["RUNTIME_CODE_POD_PATH"]}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts += [{
            "name": "foundation-code",
            "mountPath": "'"${pod_code}/foundation"'"
        }]
    ' -i "${file}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.volumes += [{
            "name": "foundation-code",
            "hostPath": {
                "path": "'"${fpath}"'",
                "type": "Directory"
            }
        }]
    ' -i "${file}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts += [{
            "name": "management-code",
            "mountPath": "'"${pod_code}/management"'"
        }]
    ' -i "${file}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.volumes += [{
            "name": "management-code",
            "hostPath": {
                "path": "'"${mpath}"'",
                "type": "Directory"
            }
        }]
    ' -i "${file}"
}

add_resource_if_set() {
    local module=$1
    local file=$2
    local dep_name=${3:-}   # 可选:仅作用于指定名字的 Deployment(合并模板时按名区分);为空则作用于全部

    local sel='.kind == "Deployment"'
    if [ -n "${dep_name}" ]; then
        sel="${sel} and .metadata.name == \"${dep_name}\""
    fi

    if [ -n "${DEPLOY_VARS["${module}_CPU_REQUEST"]:-}" ]; then
        yq eval "select(${sel}).spec.template.spec.containers[0].resources.requests.cpu = \"${DEPLOY_VARS["${module}_CPU_REQUEST"]}\"" -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_MEMORY_REQUEST"]:-}" ]; then
        yq eval "select(${sel}).spec.template.spec.containers[0].resources.requests.memory = \"${DEPLOY_VARS["${module}_MEMORY_REQUEST"]}\"" -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_CPU_LIMIT"]:-}" ]; then
        yq eval "select(${sel}).spec.template.spec.containers[0].resources.limits.cpu = \"${DEPLOY_VARS["${module}_CPU_LIMIT"]}\"" -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_MEMORY_LIMIT"]:-}" ]; then
        yq eval "select(${sel}).spec.template.spec.containers[0].resources.limits.memory = \"${DEPLOY_VARS["${module}_MEMORY_LIMIT"]}\"" -i "${file}"
    fi
}