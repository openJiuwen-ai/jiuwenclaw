#!/bin/bash
# Execution Validator - Message Validation Script
# 验证消息是否包含敏感信息
set -e
MESSAGE="${1:-}"

if [ -z "$MESSAGE" ]; then
    echo "Usage: validate-message.sh <message>"
    exit 1
fi

# 敏感信息模式
SENSITIVE_PATTERNS=(
    "private_key:[a-fA-F0-9]{64}"
    "id_card:[1-9][0-9]{5}(18|19|20)[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx]"
    "api_key:(sk-|api-|key-)[a-zA-Z0-9]{20,}"
    "feishu_token:t-[a-zA-Z0-9]{10,}"
    "feishu_user:u-[a-zA-Z0-9]{10,}"
    "phone:1[3-9][0-9]{9}"
    "bank_card:[1-9][0-9]{15,18}"
    "webhook:https?://[^ ]*webhook[^ ]*"
)

# 检查敏感模式
for entry in "${SENSITIVE_PATTERNS[@]}"; do
    type="${entry%%:*}"
    pattern="${entry#*:}"
    if echo "$MESSAGE" | grep -iqE "$pattern"; then
        echo "❌"
        exit 1
    fi
done

echo "✅"
exit 0