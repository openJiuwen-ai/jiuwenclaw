#!/bin/bash
set -e
OPENCLAW_WORKSPACE="${1:-$HOME/.jiuwenswarm/agent/workspace}"
AGENTS_MD="$OPENCLAW_WORKSPACE/AGENTS.md"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && cd .. && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"

EXPECTED_REDLINES="$CONFIG_DIR/expected-redlines.txt"
EXPECTED_TOOLS="$CONFIG_DIR/expected-tools-section.txt"

[ ! -f "$AGENTS_MD" ] && echo "❌ AGENTS.md not found" && exit 1
[ ! -f "$EXPECTED_REDLINES" ] && echo "❌ expected-redlines.txt not found" && exit 1
[ ! -f "$EXPECTED_TOOLS" ] && echo "❌ expected-tools-section.txt not found" && exit 1

# Extract Red Lines section content (lines between ## Red Lines and next ##)
extract_redlines() {
    awk '/^## Red(lines| Lines)$/{found=1; next} found && /^## /{exit} found{print}' "$AGENTS_MD" \
        | sed '/^[[:space:]]*$/d'
}

# Extract Execution Validator block from Tools section
extract_tools_section() {
    awk '/Execution Validator Skill/{found=1} found && (/^### / && !/Execution Validator Skill/ || /^## /){exit} found{print}' "$AGENTS_MD" \
        | sed '/^[[:space:]]*$/d'
}

verify_section() {
    local label="$1"
    local actual="$2"
    local expected_file="$3"

    local expected
    expected=$(sed '/^[[:space:]]*$/d' "$expected_file")

    # Hash check first (fast)
    local actual_hash expected_hash
    actual_hash=$(echo "$actual" | sha256sum | cut -d' ' -f1)
    expected_hash=$(echo "$expected" | sha256sum | cut -d' ' -f1)

    if [ "$actual_hash" = "$expected_hash" ]; then
        echo "✅ $label: OK"
        return 0
    fi

    # Hash mismatch — show diff
    echo "❌ $label: content mismatch"
    diff <(echo "$expected") <(echo "$actual") | sed 's/^/   /'
    return 1
}

FAIL=0

verify_section "Red Lines" "$(extract_redlines)" "$EXPECTED_REDLINES" || FAIL=1
verify_section "Tools / Execution Validator" "$(extract_tools_section)" "$EXPECTED_TOOLS" || FAIL=1

if [ "$FAIL" -eq 1 ]; then
    echo ""
    echo "⚠️  Reinstalling to restore expected content..."
    bash "$SCRIPT_DIR/install.sh" "$OPENCLAW_WORKSPACE"
    exit $?
fi

exit 0