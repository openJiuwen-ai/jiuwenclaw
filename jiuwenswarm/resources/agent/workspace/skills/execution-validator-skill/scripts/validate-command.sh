#!/bin/bash
# Execution Validator - Command Validation Script
# 验证命令安全级别：block（拦截）、confirm（确认）、pass（通过）
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/../config"

COMMAND="${1:-}"

if [ -z "$COMMAND" ]; then
    echo "Usage: validate-command.sh <command>"
    exit 1
fi

if [ ! -f "$CONFIG_DIR/dangerous-commands.json" ] || [ ! -f "$CONFIG_DIR/warning-commands.json" ]; then
    echo "❌配置文件缺失"
    exit 1
fi

# 解析 commands 数组
parse_commands() {
    local json_file="$1"
    sed -n '/"commands":/,/\]/p' "$json_file" | grep -oP '"[^"]+"(?=\s*,|\s*\])' | sed 's/"//g'
}

# 检查是否匹配任意模式
check_patterns() {
    local patterns=("$@")
    local command="$COMMAND"
    
    for pattern in "${patterns[@]}"; do
        if echo "$command" | grep -iqE "$pattern"; then
            return 0
        fi
    done
    return 1
}

# 1. 检查严重命令（block）
DANGEROUS_PATTERNS=($(parse_commands "$CONFIG_DIR/dangerous-commands.json"))
if check_patterns "${DANGEROUS_PATTERNS[@]}"; then
    echo "BLOCK: High-risk command detected. Execution is strictly prohibited."
    exit 2
fi

# 2. 检查警告命令（confirm）
WARNING_PATTERNS=($(parse_commands "$CONFIG_DIR/warning-commands.json"))
if check_patterns "${WARNING_PATTERNS[@]}"; then
    echo "NEED CONFIRM: Medium-risk command detected. Explicit user approval is required before execution."
    exit 1
fi

# 3. 检查关键目录
CRITICAL_DIRS=("/etc" "/root" "/boot" "/sys" "/proc" "/dev")
for dir in "${CRITICAL_DIRS[@]}"; do
    if echo "$COMMAND" | grep -q "$dir"; then
        if echo "$COMMAND" | grep -qE "(rm|mkfs|dd|chmod|chown|tee|touch|mkdir).*$dir"; then
            echo "NEED CONFIRM: Medium-risk command detected. Explicit user approval is required before execution."
            exit 1
        fi
    fi
done

# 4. 通过
echo "PASS"
exit 0