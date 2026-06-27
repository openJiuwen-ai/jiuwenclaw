#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_nfs_files() {
    local template_file=${CONFIG["NFS_TEMPLATE_FILE"]}
    local file=${CONFIG["NFS_FILE"]}

    render_config_template ${template_file} ${file} "DEPLOY_VARS"
}

# NFS is on default namespace
deploy_nfs() {
    local nfs_path=${DEPLOY_VARS["NFS_HOST_PATH"]}
    local nfs_dname=${DEPLOY_VARS["NFS_NAME"]}
    local file=${CONFIG["NFS_FILE"]}

    exec_cmd mkdir -p ${nfs_path}
    exec_cmd chmod -R 777 ${nfs_path}
    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${nfs_dname}"
}

uninstall_nfs() {
    local nfs_name=${DEPLOY_VARS["NFS_NAME"]}

    delete_k8s_resource "deployment" "${nfs_name}"
    wait_pod_terminated "${nfs_name}"
}
