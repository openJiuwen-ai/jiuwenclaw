#!/usr/bin/env bash
set -euo >/dev/null 2>&1

wait_k8s_resource_ready() {
    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"
    
    info "Waiting for k8s resource: ${kind}/${namespace}/${name}"
    exec_cmd kubectl rollout status "${kind}/${name}" --namespace="${namespace}"
    success "${kind}/${namespace}/${name} is ready now"
}

delete_k8s_resource() {
    local kind="$1"
    local name="$2"
    local namespace="${3:-default}"

    # Check if resource exists before deletion
    if ! kubectl get ${kind} ${name} -n ${namespace} >/dev/null 2>&1; then
        info "${kind}/${name} not found in namespace ${namespace}, skipping deletion."
        return
    fi

    info "Deleting k8s resource:  ${kind}/${namespace}/${name}"
    exec_cmd kubectl delete ${kind} ${name} -n ${namespace}
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

collect_k8s_cluster_info() {
    DEPLOY_VARS["MASTER_NODE_IP"]=$(kubectl get nodes \
        --selector='node-role.kubernetes.io/master' \
        -o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}')
    info "MASTER_NODE_IP: ${DEPLOY_VARS["MASTER_NODE_IP"]}"

    MASTER_NODE_NAME=$(kubectl get nodes \
        --selector='node-role.kubernetes.io/master' \
        -o jsonpath='{.items[*].metadata.name}')
    info "MASTER_NODE_NAME: ${MASTER_NODE_NAME}"

    WORKER_NODE_IPS=($(kubectl get nodes -o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}' \
        | tr ' ' '\n' \
        | grep -v "${DEPLOY_VARS["MASTER_NODE_IP"]}"))
    info "WORKER_NODE_IPS: ${WORKER_NODE_IPS[*]}"
}

