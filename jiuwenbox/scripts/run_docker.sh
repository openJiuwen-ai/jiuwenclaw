#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CALLER_CWD="$(pwd)"

IMAGE_NAME="${JIUWENBOX_IMAGE_NAME:-jiuwenbox}"
IMAGE_TAG="${JIUWENBOX_IMAGE_TAG:-latest}"
IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
CONTAINER_NAME="${JIUWENBOX_CONTAINER_NAME:-jiuwenbox}"
HOST_PORT="${JIUWENBOX_HOST_PORT:-8321}"
PROXY_PORT="${JIUWENBOX_PROXY_PORT:-8322}"
POLICY_CONFIG=""
CONTAINER_POLICY_PATH="/app/runtime-config/policy.yaml"
CONTAINER_DEFAULT_POLICY_PATH="/app/configs/default-policy.yaml"
DOCKER_ENV_ARGS=()
DOCKER_VOLUME_ARGS=()

usage() {
  cat <<'EOF'
Usage: scripts/run_docker.sh [policy-config.yaml] [docker run args...]

Examples:
  scripts/run_docker.sh
  scripts/run_docker.sh configs/default-policy.yaml
EOF
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      ;;
    *)
      POLICY_CONFIG="$1"
      shift
      ;;
  esac
fi

echo "Starting jiuwenbox container:"
echo "  image:     $IMAGE_REF"
echo "  container: $CONTAINER_NAME"
echo "  url:       http://127.0.0.1:${HOST_PORT}"

if [[ -n "$POLICY_CONFIG" ]]; then
  POLICY_CONFIG_ABS=""

  if [[ "$POLICY_CONFIG" = /* && -f "$POLICY_CONFIG" ]]; then
    POLICY_CONFIG_ABS="$(realpath "$POLICY_CONFIG")"
  elif [[ -f "$CALLER_CWD/$POLICY_CONFIG" ]]; then
    POLICY_CONFIG_ABS="$(realpath "$CALLER_CWD/$POLICY_CONFIG")"
  elif [[ -f "$PROJECT_DIR/$POLICY_CONFIG" ]]; then
    POLICY_CONFIG_ABS="$(realpath "$PROJECT_DIR/$POLICY_CONFIG")"
  else
    echo "error: policy config not found: $POLICY_CONFIG" >&2
    exit 1
  fi

  DOCKER_ENV_ARGS+=(-e "JIUWENBOX_POLICY_PATH=${CONTAINER_POLICY_PATH}")
  DOCKER_VOLUME_ARGS+=(-v "${POLICY_CONFIG_ABS}:${CONTAINER_POLICY_PATH}:ro")
  echo "  policy:    $POLICY_CONFIG_ABS"
else
  DOCKER_ENV_ARGS+=(-e "JIUWENBOX_POLICY_PATH=${CONTAINER_DEFAULT_POLICY_PATH}")
  echo "  policy:    ${CONTAINER_DEFAULT_POLICY_PATH} (container default)"
fi

echo

docker run -itd \
    --name "$CONTAINER_NAME" \
    --cap-add=SYS_ADMIN \
    --cap-add=NET_ADMIN \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    -p "${HOST_PORT}:8321" \
    -p "${PROXY_PORT}:8322" \
    "${DOCKER_ENV_ARGS[@]}" \
    "${DOCKER_VOLUME_ARGS[@]}" \
    "$@" \
    "$IMAGE_REF"
