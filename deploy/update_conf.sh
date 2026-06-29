#!/bin/bash
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC_FILE=${PROJECT_DIR}/jiuwenclaw/resources/config.yaml
DEST_FILE=${PROJECT_DIR}/deploy/templates/gateway-config-jiuwen.template.yaml

rm -f "${DEST_FILE}"
cp -f "${SRC_FILE}" "${DEST_FILE}"

yq eval '.gateway.agent_client.type = "jiuwen"' -i "${DEST_FILE}"
yq eval '.gateway.session_map_scope = "<<GATEWAY_SESSION_MAP_SCOPE>>"' -i "${DEST_FILE}"
yq eval '.channels.feishu = {}' -i "${DEST_FILE}"
