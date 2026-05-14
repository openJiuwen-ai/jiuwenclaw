#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
set -euo pipefail

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$TEST_DIR/.." && pwd)"
cd "$PROJECT_DIR"

JIUWENBOX_DIR="$(realpath "$PROJECT_DIR/src")"

TEST_TARGET="tests/integration/"
TEST_KIND="integration"
PERF_SANDBOX_COUNT="${JIUWENBOX_PERF_SANDBOX_COUNT:-1}"
PERF_CONCURRENCY="${JIUWENBOX_PERF_CONCURRENCY:-4}"
PERF_LOOP="${JIUWENBOX_PERF_LOOP:-8}"
PERF_EXEC_TIMEOUT_SECONDS="${JIUWENBOX_PERF_EXEC_TIMEOUT_SECONDS:-180}"

require_value() {
    local option="$1"
    if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "Missing value for ${option}" >&2
        exit 2
    fi
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        default)
            TEST_TARGET="tests/integration/test_server_api_default.py"
            TEST_KIND="integration"
            shift
            ;;
        inference-privacy-proxy)
            TEST_TARGET="tests/integration/test_inference_privacy_proxy.py"
            TEST_KIND="integration"
            shift
            ;;
        performance)
            TEST_TARGET="tests/performance/"
            TEST_KIND="performance"
            shift
            ;;
    esac
fi

PYTEST_ARGS=()
if [[ "$TEST_KIND" == "performance" ]]; then
    PYTEST_ARGS+=(
        "-s"
        "--log-cli-level=INFO"
        "--log-cli-format=%(message)s"
        "--log-disable=httpx"
        "--log-disable=httpcore"
    )
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --sandbox-count)
                require_value "$1" "${2:-}"
                PERF_SANDBOX_COUNT="$2"
                shift 2
                ;;
            --concurrency)
                require_value "$1" "${2:-}"
                PERF_CONCURRENCY="$2"
                shift 2
                ;;
            --loop)
                require_value "$1" "${2:-}"
                PERF_LOOP="$2"
                shift 2
                ;;
            *)
                PYTEST_ARGS+=("$1")
                shift
                ;;
        esac
    done
else
    PYTEST_ARGS=("$@")
fi

if [[ "$TEST_KIND" == "performance" ]]; then
    JIUWENBOX_PERF_SANDBOX_COUNT=${PERF_SANDBOX_COUNT} \
        JIUWENBOX_PERF_CONCURRENCY=${PERF_CONCURRENCY} \
        JIUWENBOX_PERF_LOOP=${PERF_LOOP} \
        JIUWENBOX_PERF_EXEC_TIMEOUT_SECONDS=${PERF_EXEC_TIMEOUT_SECONDS} \
        PYTHONPATH=${JIUWENBOX_DIR} \
        python3 -m pytest "$TEST_TARGET" -v --tb=short "${PYTEST_ARGS[@]}"
else
    PYTHONPATH=${JIUWENBOX_DIR} \
        python3 -m pytest "$TEST_TARGET" -v --tb=short "${PYTEST_ARGS[@]}"
fi
