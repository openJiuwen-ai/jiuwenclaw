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
    local mode="${DEPLOY_VARS["MODE"]}"
    local file="$1"

    if [ "${mode}" != "dev" ]; then
        return
    fi

    # Force pod to be scheduled on current master node
    yq eval 'select(.kind == "Deployment").spec.template.spec.nodeName = "'"${DEPLOY_VARS["MASTER_NODE_NAME"]}"'"' -i "${file}"

    # Mount host source code directory into container
    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts += [{
            "name": "host-code",
            "mountPath": "'"${DEPLOY_VARS["POD_CODE_PATH"]}"'"
        }]
    ' -i "${file}"

    yq eval '
        select(.kind == "Deployment").spec.template.spec.volumes += [{
            "name": "host-code",
            "hostPath": {
                "path": "'"${DEPLOY_VARS["HOST_CODE_PATH"]}"'",
                "type": "Directory"
            }
        }]
    ' -i "${file}"

    # Security context adaptation for root user
    yq eval 'select(.kind == "Deployment").spec.template.spec.securityContext.fsGroup = 0' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation = true' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsNonRoot = false' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsUser = 0' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext.runAsGroup = 0' -i "${file}"
}
