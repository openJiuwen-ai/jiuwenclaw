#!/bin/bash
NS="$1"
if [ -z "${NS}" ]; then
    echo "用法：$0 <namespace>"
    exit 1
fi

echo "===== 清理 namespace=$NS 旧日志监听进程 ====="
ps aux | grep "kubectl logs" | grep "${NS}" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null

LOG_DIR="./pod_logs_$(date +%Y%m%d_%H%M%S)"

mkdir -p "${LOG_DIR}"
echo "日志将保存至：${LOG_DIR}"

kubectl get pods -n ${NS} --no-headers | awk '{print $1}' | while read pod_name; do
    echo "==================== 开始采集 Pod: ${pod_name} ===================="
    containers=$(kubectl get pod "${pod_name}" -n ${NS} -o jsonpath='{.spec.containers[*].name}')
    for ctr in ${containers}; do
        log_file="${LOG_DIR}/${pod_name}-${ctr}.log"
        echo "后台持续监听 ${pod_name}/${ctr} → ${log_file}"
        kubectl logs -f "${pod_name}" -c "${ctr}" -n "${NS}" > "${log_file}" 2>&1 &
    done
done

echo "日志采集完成，目录：${LOG_DIR}"
ls -ld "${LOG_DIR}"
