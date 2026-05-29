#!/usr/bin/env bash
set -euo >/dev/null 2>&1

install_pv_pvc() {
    local pv_template_file="${CONFIG["PV_TEMPLATE_FILE"]}"
    local pv_file="${CONFIG["PV_FILE"]}"
    local pvc_template_file="${CONFIG["PVC_TEMPLATE_FILE"]}"
    local pvc_file="${CONFIG["PVC_FILE"]}"

    render_config_template ${pv_template_file} ${pv_file} "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${pv_file}

    render_config_template ${pvc_template_file} ${pvc_file} "DEPLOY_VARS"
    exec_cmd kubectl apply -f ${pvc_file}
}

uninstall_pv_pvc() {
    local pv_file="${CONFIG["PV_FILE"]}"
    local pvc_file="${CONFIG["PVC_FILE"]}"

    exec_cmd kubectl delete -f ${pvc_file} false
    exec_cmd kubectl delete -f ${pv_file} false
}


# NFS is on default namespace
deploy_nfs() {
    local nfs_path=${DEPLOY_VARS["NFS_HOST_PATH"]}
    local nfs_dname=${DEPLOY_VARS["NFS_NAME"]}
    local template_file=${DEPLOY_VARS["NFS_TEMPLATE_FILE"]}
    local file=${DEPLOY_VARS["NFS_FILE"]}

    render_config_template ${template_file} ${file} "DEPLOY_VARS"
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
