#!/bin/bash
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC_FILE=${PROJECT_DIR}/jiuwenclaw/resources/config.yaml
DEST_FILE=${PROJECT_DIR}/deploy/templates/gateway-config-jiuwen.template.yaml

rm -f "${DEST_FILE}"
cp -f "${SRC_FILE}" "${DEST_FILE}"

yq eval '.gateway.agent_client.type = "jiuwen"' -i "${DEST_FILE}"
yq eval '.gateway.session_map_scope = "<<GATEWAY_SESSION_MAP_SCOPE>>"' -i "${DEST_FILE}"
yq eval '.channels.feishu = {}' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.host = "${AGENT_CLIENT_REST_HOST:-0.0.0.0}"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db_type = "<<DB_TYPE>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.sqlite_path = "<<GATEWAY_SQLITE_PATH>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db.host = "<<DB_HOST>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db.port = "<<DB_PORT>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db.user = "<<DB_USER>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db.password = "<<DB_PASSWORD>>"' -i "${DEST_FILE}"
yq eval '.extensions.agent_client_rest.database.db.db_name = "<<GATEWAY_DB_NAME>>"' -i "${DEST_FILE}"
