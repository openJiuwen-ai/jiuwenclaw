#!/usr/bin/env bash
set -euo >/dev/null 2>&1

install_pv_pvc() {
    render_config_template ${PV_TEMPLATE_FILE} ${PV_FILE} "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${PV_FILE}

    render_config_template ${PVC_TEMPLATE_FILE} ${PVC_FILE} "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${PVC_FILE}
}

uninstall_pv_pvc() {
    exec_cmd kubectl delete -f ${PVC_FILE} false
    exec_cmd kubectl delete -f ${PV_FILE} false
}


# NFS is on default namespace
deploy_nfs() {
    local nfs_path=${DEPLOY_VARS["NFS_HOST_PATH"]}
    local nfs_dname=${DEPLOY_VARS["NFS_NAME"]}

    render_config_template ${NFS_TEMPLATE_FILE} ${NFS_FILE} "DEPLOY_VARS"
    exec_cmd mkdir -p ${nfs_path}
    exec_cmd chmod -R 777 ${nfs_path}
    exec_cmd kubectl apply -f ${NFS_FILE}
    wait_k8s_resource_ready "deployment" "${nfs_dname}"
}

uninstall_nfs() {
    local nfs_name=${DEPLOY_VARS["NFS_NAME"]}

    delete_k8s_resource "deployment" "${nfs_name}"
    wait_pod_terminated "${nfs_name}"
}
