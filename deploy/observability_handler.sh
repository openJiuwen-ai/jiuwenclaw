#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_monitor_files() {
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi

    local otel_template_file="${CONFIG["OTEL_TEMPLATE_FILE"]}"
    local prom_template_file="${CONFIG["PROMETHEUS_TEMPLATE_FILE"]}"
    local loki_template_file="${CONFIG["LOKI_TEMPLATE_FILE"]}"
    local tempo_template_file="${CONFIG["TEMPO_TEMPLATE_FILE"]}"
    local observability_template_file="${CONFIG["OBSERVABILITY_TEMPLATE_FILE"]}"
    local otel_file="${CONFIG["OTEL_FILE"]}"
    local prom_file="${CONFIG["PROMETHEUS_FILE"]}"
    local loki_file="${CONFIG["LOKI_FILE"]}"
    local tempo_file="${CONFIG["TEMPO_FILE"]}"
    local observability_file="${CONFIG["OBSERVABILITY_FILE"]}"

    ensure_available_port "PROMETHEUS_NODE_PORT" "OBSERVABILITY_NODE_PORT"

    render_config_template "${otel_template_file}" "${otel_file}" "DEPLOY_VARS"
    add_resource_if_set "OTEL" "${otel_file}"

    render_config_template "${prom_template_file}" "${prom_file}" "DEPLOY_VARS"
    add_resource_if_set "PROMETHEUS" "${prom_file}"

    render_config_template "${loki_template_file}" "${loki_file}" "DEPLOY_VARS"
    add_resource_if_set "LOKI" "${loki_file}"

    render_config_template "${tempo_template_file}" "${tempo_file}" "DEPLOY_VARS"
    add_resource_if_set "TEMPO" "${tempo_file}"
 
    render_config_template "${observability_template_file}" "${observability_file}" "DEPLOY_VARS"
    add_resource_if_set "OBSERVABILITY" "${observability_file}"

    if [ "${DEPLOY_VARS["IS_MOUNT_OBSERVABILITY_CODE"]}" == "true" ]; then
        enable_dev_mode_if_needed ${observability_file}
    fi
}

deploy_monitor() {
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi

    local otel_name="${DEPLOY_VARS["OTEL_NAME"]}"
    local prom_name="${DEPLOY_VARS["PROMETHEUS_NAME"]}"
    local loki_name="${DEPLOY_VARS["LOKI_NAME"]}"
    local tempo_name="${DEPLOY_VARS["TEMPO_NAME"]}"
    local observability_name="${DEPLOY_VARS["OBSERVABILITY_NAME"]}"
    local otel_file="${CONFIG["OTEL_FILE"]}"
    local prom_file="${CONFIG["PROMETHEUS_FILE"]}"
    local loki_file="${CONFIG["LOKI_FILE"]}"
    local tempo_file="${CONFIG["TEMPO_FILE"]}"
    local observability_file="${CONFIG["OBSERVABILITY_FILE"]}"

    exec_cmd kubectl apply -f ${otel_file}
    wait_k8s_resource_ready "daemonset" "${otel_name}" "${namespace}"

    exec_cmd kubectl apply -f ${prom_file}
    wait_k8s_resource_ready "deployment" "${prom_name}" "${namespace}"

    exec_cmd kubectl apply -f ${loki_file}
    wait_k8s_resource_ready "statefulset" "${loki_name}" "${namespace}"

    exec_cmd kubectl apply -f ${tempo_file}
    wait_k8s_resource_ready "deployment" "${tempo_name}" "${namespace}"

    exec_cmd kubectl apply -f ${observability_file}
    wait_k8s_resource_ready "deployment" "${observability_name}" "${namespace}"

    success "PROMETHEUS_NODE_PORT: ${DEPLOY_VARS["PROMETHEUS_NODE_PORT"]}"
    success "OBSERVABILITY_NODE_PORT: ${DEPLOY_VARS["OBSERVABILITY_NODE_PORT"]}"
}

uninstall_monitor() {
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi

    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local otel_name="${DEPLOY_VARS["OTEL_NAME"]}"
    local prom_name="${DEPLOY_VARS["PROMETHEUS_NAME"]}"
    local loki_name="${DEPLOY_VARS["LOKI_NAME"]}"
    local tempo_name="${DEPLOY_VARS["TEMPO_NAME"]}"
    local observability_name="${DEPLOY_VARS["OBSERVABILITY_NAME"]}"
    local otel_file="${CONFIG["OTEL_FILE"]}"
    local prom_file="${CONFIG["PROMETHEUS_FILE"]}"
    local loki_file="${CONFIG["LOKI_FILE"]}"
    local tempo_file="${CONFIG["TEMPO_FILE"]}"
    local observability_file="${CONFIG["OBSERVABILITY_FILE"]}"

    exec_cmd kubectl delete -f ${otel_file} --ignore-not-found=true
    wait_pod_terminated "${otel_name}" "${namespace}"

    exec_cmd kubectl delete -f ${prom_file} --ignore-not-found=true
    wait_pod_terminated "${prom_name}" "${namespace}"

    exec_cmd kubectl delete -f ${loki_file} --ignore-not-found=true
    wait_pod_terminated "${loki_name}" "${namespace}"

    exec_cmd kubectl delete -f ${tempo_file} --ignore-not-found=true
    wait_pod_terminated "${tempo_name}" "${namespace}"

    exec_cmd kubectl delete -f ${observability_file} --ignore-not-found=true
    wait_pod_terminated "${observability_name}" "${namespace}"
}

