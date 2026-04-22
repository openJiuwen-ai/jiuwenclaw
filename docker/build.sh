#!/bin/bash
set -e

case "$(uname -m)" in
  x86_64)
    ARCH_TAG="x86_64"
    BUILD_ARG_ARCH="x86_64"
    ;;
  aarch64|arm64)
    ARCH_TAG="aarch64"
    BUILD_ARG_ARCH="aarch64"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)"
    exit 1
    ;;
esac

build_version=$1

if [ -z "$build_version" ]; then
    echo "Error: Missing build_version. Usage: $0 [0.1.10]"
    exit 1
fi

IMAGE_NAME="jiuwen:${build_version}-py311-ubuntu22.04-${ARCH_TAG}"
echo "Building for ${BUILD_ARG_ARCH}, tagging as: ${IMAGE_NAME}"
docker build --build-arg ARCH="${BUILD_ARG_ARCH}" -t "${IMAGE_NAME}" -f jiuwenclaw.dockerfile .
echo "构建$build_type镜像完成！"

