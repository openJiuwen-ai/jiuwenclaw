#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# Wait for kubernetes resource rollout ready
# Args:
#   1: resource kind
#   2: resource name
#   3: namespace (optional, default: default)
wait_k8s_resource_ready() {
    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"
    
    info "Waiting for k8s resource: ${kind}/${namespace}/${name}"
    exec_cmd kubectl rollout status "${kind}/${name}" --namespace="${namespace}"
    success "${kind}/${namespace}/${name} is ready now"
}

# Check if kubernetes resource exists
# Args:
#   1: resource kind
#   2: resource name
#   3: namespace (optional, default: default)
# Return: 0 = exists, 1 = not exists
check_k8s_resource_exists() {
    if [ "${DEPLOY_VARS["RENDER_ONLY"]}" == "true" ]; then
        return 0
    fi

    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"
    local args=""

    # Cluster-scoped resource: PersistentVolume
    if [[ "${kind}" == "pv" || "${kind}" == "PersistentVolume" ]]; then
        args="${kind} ${name}"
        namespace=""
    else
        args="${kind} ${name} -n ${namespace}"
    fi

    if kubectl get ${args} >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# delete all pods with specified name prefix
delete_k8s_pods() {
    local name="$1"
    local namespace="${2:-default}"

    info "Deleting all pods with prefix: ${name} (namespace: ${namespace})"
    # Get prefixed pod list and execute batch deletion
    info "Executing: kubectl get pods -n \"${namespace}\" -o name | grep -E \"^pod/${name}\" | xargs -r kubectl delete -n \"${namespace}\""
    kubectl get pods -n "${namespace}" -o name \
        | grep -E "^pod/${name}" \
        | xargs -r kubectl delete -n "${namespace}"
    success "All pods with prefix '${name}' have been deleted"
}


# Create specified kubernetes resource
# Args:
#   1: resource kind
#   2: resource name
#   3: namespace (optional, default: default)
create_k8s_resource() {
    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"

    local cmd_args=""
    # PV and namespace are cluster-scoped, without namespace
    if [[ "${kind}" == "pv" || "${kind}" == "ns" ]]; then
        cmd_args="${kind} ${name}"
        namespace=""
    else
        cmd_args="${kind} ${name} -n ${namespace}"
    fi

    # Check whether target resource exists
    if kubectl get ${cmd_args} >/dev/null 2>&1; then
        info "${kind}/${namespace}/${name} exists, skipping create."
        return
    fi

    info "Creating k8s resource:  ${kind}/${namespace}/${name}"
    exec_cmd kubectl create ${cmd_args}
    success "${kind}/${namespace}/${name} is created now"
}

# Delete specified kubernetes resource
# Args:
#   1: resource kind
#   2: resource name
#   3: namespace (optional, default: default)
delete_k8s_resource() {
    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"

    local cmd_args=""
    # PV is cluster-scoped, without namespace
    if [[ "${kind}" == "pv" || "${kind}" == "PersistentVolume" ]]; then
        cmd_args="${kind} ${name}"
        namespace=""
    else
        cmd_args="${kind} ${name} -n ${namespace}"
    fi

    # Check whether target resource exists
    if ! kubectl get ${cmd_args} >/dev/null 2>&1; then
        info "${kind}/${namespace}/${name} not found, skipping deletion."
        return
    fi

    info "Deleting k8s resource:  ${kind}/${namespace}/${name}"
    exec_cmd kubectl delete ${cmd_args}
    success "${kind}/${namespace}/${name} is deleted now"
}


# Wait for a pod in a given namespace to be fully terminated and removed
wait_pod_terminated() {
  local pod_name_prefix="$1"
  local namespace="${2:-default}"

  echo "=== Waiting for pod [${namespace}/${pod_name_prefix}*] to terminate completely..."

  while kubectl get pods -n "${namespace}" | grep -q "${pod_name_prefix}"; do
    info "Pod is still terminating, waiting 3 seconds..."
    sleep 3
  done

  success "Pod has been fully terminated and cleaned up!"
}

fetch_master_node_ip() {
    if [ -n "${DEPLOY_VARS["MASTER_NODE_IP"]:-}" ]; then
        return
    fi

    # Get InternalIP of current master node
    DEPLOY_VARS["MASTER_NODE_IP"]=$(kubectl get node "${DEPLOY_VARS["MASTER_NODE_NAME"]}" -o json | \
        jq -r '.status.addresses[] | 
            select(.type == "InternalIP") | 
            .address')
    info "MASTER_NODE_IP: ${DEPLOY_VARS["MASTER_NODE_IP"]}"
}


# Collect Kubernetes cluster information:
#     current master IP
#     current master name
#     worker IPs
#     other master IPs
collect_k8s_cluster_info() {
    # Get node name of current master node
    if [ -z "${DEPLOY_VARS["MASTER_NODE_NAME"]:-}" ]; then
        DEPLOY_VARS["MASTER_NODE_NAME"]=$(hostname)
        info "MASTER_NODE_NAME: ${DEPLOY_VARS["MASTER_NODE_NAME"]}"
    fi

    fetch_master_node_ip

    # Get Ready worker node IPs (nodes without control-plane or master role)
    if [ -z "${DEPLOY_VARS["WORKER_NODE_IPS"]:-}" ]; then
        WORKER_NODE_IPS=($(kubectl get nodes -o json | \
            jq -r '.items[] | 
                select(
                    (.metadata.labels["node-role.kubernetes.io/control-plane"] == null and
                    .metadata.labels["node-role.kubernetes.io/master"] == null) and
                    (.status.conditions[] | select(.type=="Ready") | .status) == "True"
                ) | 
                .status.addresses[] | 
                select(.type=="InternalIP") | 
                .address'))

        info "WORKER_NODE_IPS: ${WORKER_NODE_IPS[*]}"
    fi

    # Get OTHER MASTER IPS (control-plane nodes EXCEPT current master)
    if [ -z "${DEPLOY_VARS["OTHER_MASTER_IPS"]:-}" ]; then
        OTHER_MASTER_IPS=($(kubectl get nodes -o json | \
            jq -r --arg CURRENT_NODE "${DEPLOY_VARS["MASTER_NODE_NAME"]}" \
            '.items[] | 
                select(
                    (.metadata.labels["node-role.kubernetes.io/control-plane"] != null or
                    .metadata.labels["node-role.kubernetes.io/master"] != null) and
                    .metadata.name != $CURRENT_NODE and
                    (.status.conditions[] | select(.type=="Ready") | .status) == "True"
                ) | 
                .status.addresses[] | 
                select(.type=="InternalIP") | 
                .address'))
        info "OTHER_MASTER_IPS: ${OTHER_MASTER_IPS[*]}"
    fi

    if [ -z "${DEPLOY_VARS["OTHER_NODE_IPS"]:-}" ]; then
        OTHER_NODE_IPS=("${WORKER_NODE_IPS[@]}" "${OTHER_MASTER_IPS[@]}")
        info "OTHER_NODE_IPS: ${OTHER_NODE_IPS[*]}"
    fi
}


# Check if any node has the gateway=enable label
if_any_nodes_gateway_label() {
    info "=== Checking if any node has gateway=enable label ==="

    local all_nodes=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}')

    for node in ${all_nodes}; do
        local label_value=$(kubectl get node "${node}" -o jsonpath='{.metadata.labels.gateway}')
        if [ "$label_value" == "enable" ]; then
            info "Check result: Node ${node} with gateway=enable label **exists** in the cluster"
            return 0
        fi
    done

    info "Check result: No nodes with gateway=enable label **exist** in the cluster"
    return 1
}
