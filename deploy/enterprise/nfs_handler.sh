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
    local file=${CONFIG["NFS_FILE"]}

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${nfs_name}"
}

render_nfs_sc_files() {
    local template_file=${CONFIG["NFS_SC_TEMPLATE_FILE"]}
    local file=${CONFIG["NFS_SC_FILE"]}

    render_config_template ${template_file} ${file} "DEPLOY_VARS"
}

deploy_nfs_sc() {
    local nfs_dname=${DEPLOY_VARS["NFS_SC_DNAME"]}
    local file=${CONFIG["NFS_SC_FILE"]}

    exec_cmd kubectl apply -f ${file}
    wait_k8s_resource_ready "deployment" "${nfs_dname}"
}

uninstall_nfs_sc() {
    local nfs_dname=${DEPLOY_VARS["NFS_SC_DNAME"]}
    local file=${CONFIG["NFS_SC_FILE"]}

    exec_cmd kubectl delete -f ${file} --ignore-not-found=true
    wait_pod_terminated "${nfs_dname}"
}

