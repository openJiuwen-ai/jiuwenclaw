#!/usr/bin/env bash
# 构建本仓库 4 个"含我们改动"的自定义镜像,并(可选)分发到 worker 节点。
# 这些镜像 registry 里没有(没 push 权限),deploy.sh 只引用不构建,故需先跑本脚本。
#   - identity   : 全新认证中心(base 复用 registry 的 manager 镜像,已含依赖)
#   - manager-iam: 资源服务器版 manager(含 bot/IAM 路由 + JWT 验签 + PyJWT)
#   - web-fileapi: 企业版 broker(把 /file-api 挂到 19000)
#   - webui-chat : 统一前端(外壳 + 同源内嵌 /chat 聊天)
#
# 用法: bash docker/build-custom-images.sh
# 可用环境变量覆盖: REG / BASE_TAG / WORKER(空=不分发,单节点或 MODE=dev 时)
set -euo pipefail

REG="${REG:-swr.cn-north-4.myhuaweicloud.com/openjiuwen}"
BASE_TAG="${BASE_TAG:-0.0.78k}"
WORKER="${WORKER:-}"   # 多节点集群设为 root@<worker-ip> 以分发镜像;单节点留空即可

cd "$(dirname "$0")/.."   # 仓库根 = 构建上下文

MANAGER_BASE="$REG/jiuwenclaw-manager-server-amd64:$BASE_TAG"
WEB_BASE="$REG/jiuwenclaw-web-amd64:$BASE_TAG"

IDENTITY_IMG="$REG/jiuwenclaw-identity-amd64:$BASE_TAG"
MANAGER_IMG="$REG/jiuwenclaw-manager-server-amd64:$BASE_TAG-iam"
WEB_IMG="$REG/jiuwenclaw-web-amd64:$BASE_TAG-fileapi"
WEBUI_IMG="$REG/jiuwenclaw-webui-amd64:$BASE_TAG-chat"

echo "==> [1/4] identity (base=$MANAGER_BASE)"
docker build -f docker/Dockerfile.identity --build-arg BASE_IMAGE="$MANAGER_BASE" -t "$IDENTITY_IMG" .

echo "==> [2/4] manager-iam (base=$MANAGER_BASE)"
docker build -f docker/Dockerfile.manager --build-arg BASE_IMAGE="$MANAGER_BASE" -t "$MANAGER_IMG" .

echo "==> [3/4] web-fileapi (base=$WEB_BASE)"
docker build -f docker/Dockerfile.web --build-arg BASE_IMAGE="$WEB_BASE" -t "$WEB_IMG" .

echo "==> [4/4] webui-chat (node->nginx,无需 base)"
docker build -f docker/Dockerfile.webui -t "$WEBUI_IMG" .

echo "==> built:"
printf '    %s\n' "$IDENTITY_IMG" "$MANAGER_IMG" "$WEB_IMG" "$WEBUI_IMG"

if [ -n "${WORKER:-}" ]; then
  echo "==> 分发到 worker: $WORKER"
  docker save "$IDENTITY_IMG" "$MANAGER_IMG" "$WEB_IMG" "$WEBUI_IMG" -o /tmp/jw-custom-images.tar
  scp /tmp/jw-custom-images.tar "$WORKER:/tmp/"
  ssh "$WORKER" 'docker load -i /tmp/jw-custom-images.tar && rm -f /tmp/jw-custom-images.tar'
  rm -f /tmp/jw-custom-images.tar
  echo "==> worker 已 load 完成"
else
  echo "==> WORKER 为空,跳过分发(确保 pod 调度到本机,或用 MODE=dev 钉 master)"
fi
echo "==> DONE"
