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

# Append a single hostPath code mount (volume + volumeMount) to the first
# container of every Deployment in <file>. 
add_code_mount() {
    local file="$1" name="$2" host_path="$3" mount_path="$4"
    yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].volumeMounts
            |= ((. // []) + [{
                "name": "'"${name}"'",
                "mountPath": "'"${mount_path}"'"
            }])
    ' -i "${file}"
    yq eval '
        select(.kind == "Deployment").spec.template.spec.volumes
            |= ((. // []) + [{
                "name": "'"${name}"'",
                "hostPath": {
                    "path": "'"${host_path}"'",
                    "type": "Directory"
                }
            }])
    ' -i "${file}"
}

mount_claw_code() {
    [ -z "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ] && return
    add_code_mount "$1" "claw-code" \
        "${DEPLOY_VARS["CLAW_CODE_PATH"]}" \
        "${DEPLOY_VARS["CLAW_POD_CODE_PATH"]}"
}

mount_runtime_code() {
    [ -z "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ] && return
    add_code_mount "$1" "runtime-code" \
        "${DEPLOY_VARS["RUNTIME_CODE_PATH"]}" \
        "${DEPLOY_VARS["RUNTIME_POD_CODE_PATH"]}"
}

mount_runtime_pkg() {
    [ -z "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ] && return
    local file="$1"
    local src="${DEPLOY_VARS["RUNTIME_CODE_PATH"]}"
    local dst="${DEPLOY_VARS["RUNTIME_POD_PKG_PATH"]}"
    add_code_mount "${file}" "foundation-code" \
        "${src}/foundation/openjiuwen_runtime/foundation" "${dst}/foundation"
    add_code_mount "${file}" "management-code" \
        "${src}/management/openjiuwen_runtime/management" "${dst}/management"
}

mount_core_pkg() {
    [ -z "${DEPLOY_VARS["CORE_CODE_PATH"]:-}" ] && return
    add_code_mount "$1" "core-code" \
        "${DEPLOY_VARS["CORE_CODE_PATH"]}/openjiuwen" \
        "${DEPLOY_VARS["CORE_POD_PKG_PATH"]}"
}

# Override the container command to run the frontend vite dev server (HMR) in
# <workdir> on <port>, bound to 0.0.0.0 so the Service/NodePort can reach it.
# Usage: run_frontend_dev <file> <workdir> <port>
run_frontend_dev() {
    local file="$1" comp="$2"
    local workdir="" port=""

    case "${comp}" in
        web)
            [ -z "${DEPLOY_VARS["CLAW_POD_CODE_PATH"]:-}" ] && return
            workdir="${DEPLOY_VARS["CLAW_POD_CODE_PATH"]}/jiuwenswarm/channels/web/frontend"
            port="${DEPLOY_VARS["WEB_HTTP_PORT"]}"
            ;;
        manager-web)
            [ -z "${DEPLOY_VARS["RUNTIME_CODE_PATH"]:-}" ] && return
            workdir="${DEPLOY_VARS["RUNTIME_POD_CODE_PATH"]}/applications/manager/manager_web"
            port="${DEPLOY_VARS["MANAGER_WEB_PORT"]}"
            ;;
        *)
            warning "run_frontend_dev: unknown component '${comp}', skipping"
            return
            ;;
    esac

    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].command = ["/bin/sh", "-c"]' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].args = ["cd '"${workdir}"' && npm run dev -- --host 0.0.0.0 --port '"${port}"'"]' -i "${file}"
}

enable_dev_mode_if_needed() {
    [ "${DEPLOY_VARS["MODE"]}" != "dev" ] && return

    local file="$1" comp="$2"
    case "${comp}" in
        gateway)
            mount_claw_code "${file}"
            mount_runtime_pkg "${file}"
            mount_core_pkg "${file}"
            ;;
        web)
            [ "${DEPLOY_VARS["IS_MOUNT_WEB_CODE"]}" != "true" ] && return
            mount_claw_code "${file}"
            mount_runtime_pkg "${file}"
            mount_core_pkg "${file}"
            run_frontend_dev "${file}" "${comp}"
            ;;
        manager-server)
            mount_runtime_code "${file}"
            ;;
        identity)
            mount_runtime_code "${file}"
            ;;
        manager-web)
            [ "${DEPLOY_VARS["IS_MOUNT_MANAGER_WEB_CODE"]}" != "true" ] && return
            mount_runtime_code "${file}"
            run_frontend_dev "${file}" "${comp}"
            ;;
        *)
            warning "enable_dev_mode_if_needed: unknown component '${comp}', skipping"
            return
            ;;
    esac

    # Pin to the current master node (hostPath is local) and run as root so
    # the hostPath-mounted source is readable/writable inside the container.
    yq eval 'select(.kind == "Deployment").spec.template.spec.nodeName = "'"${DEPLOY_VARS["CURRENT_NODE_NAME"]}"'"' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.securityContext.fsGroup = 0' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].securityContext = {
        "allowPrivilegeEscalation": true,
        "runAsNonRoot": false,
        "runAsUser": 0,
        "runAsGroup": 0
    }' -i "${file}"
}

add_resource_if_set() {
    local module="$1"  file="$2"
    local kind_type="Deployment"

    if [[ "${module}" == "MYSQL" || "${module}" == "POSTGRESQL" ]]; then
        kind_type="StatefulSet"
    fi

    if [ -n "${DEPLOY_VARS["${module}_CPU_REQUEST"]:-}" ]; then
        yq eval 'select(.kind == "'"${kind_type}"'").spec.template.spec.containers[0].resources.requests.cpu = "'"${DEPLOY_VARS["${module}_CPU_REQUEST"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_MEMORY_REQUEST"]:-}" ]; then
        yq eval 'select(.kind == "'"${kind_type}"'").spec.template.spec.containers[0].resources.requests.memory = "'"${DEPLOY_VARS["${module}_MEMORY_REQUEST"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_CPU_LIMIT"]:-}" ]; then
        yq eval 'select(.kind == "'"${kind_type}"'").spec.template.spec.containers[0].resources.limits.cpu = "'"${DEPLOY_VARS["${module}_CPU_LIMIT"]}"'"' -i "${file}"
    fi

    if [ -n "${DEPLOY_VARS["${module}_MEMORY_LIMIT"]:-}" ]; then
        yq eval 'select(.kind == "'"${kind_type}"'").spec.template.spec.containers[0].resources.limits.memory = "'"${DEPLOY_VARS["${module}_MEMORY_LIMIT"]}"'"' -i "${file}"
    fi
}