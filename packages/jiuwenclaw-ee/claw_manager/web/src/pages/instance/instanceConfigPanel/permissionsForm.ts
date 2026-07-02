import { tryParseJson } from '../../../components/JsonField';
import { safeStringify } from '../../../utils/format';
import { stripExampleLabel } from '../../../utils/jsonExample';
import type {
  PermissionAction,
  PermissionRuleAction,
  PermissionRuleEntry,
  PermissionToolEntry,
  PermissionsFormState,
} from '../../../types';

export { stripExampleLabel } from '../../../utils/jsonExample';

const EMPTY_FILE_GUARD_GLOBAL_JSON = '{}';
const EMPTY_FILE_GUARD_TRUSTED_EXEC_JSON = '[]';
const EMPTY_FILE_GUARD_TOOL_BINDINGS_JSON = '{}';

let _rowKey = 0;
function nextKey(prefix: string) {
  _rowKey += 1;
  return `${prefix}-${_rowKey}`;
}

function asPermissionAction(value: unknown, fallback: PermissionAction = 'ask'): PermissionAction {
  if (value === 'allow' || value === 'ask' || value === 'deny') return value;
  return fallback;
}

function asRuleAction(value: unknown, fallback: PermissionRuleAction = 'allow'): PermissionRuleAction {
  if (value === 'allow' || value === 'deny') return value;
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function parseJsonField<T>(text: string, fallback: T): T {
  return tryParseJson(stripExampleLabel(text), fallback);
}

const COMMAND_INTENT_EXTRA_BODY = { thinking: { type: 'disabled' } };

function jsonFromBody(
  value: unknown,
  isEmpty: (value: unknown) => boolean,
  emptyJson: string
): string {
  if (isEmpty(value)) return emptyJson;
  return safeStringify(value, 2);
}

function fileGuardGlobalJsonFromBody(global: unknown): string {
  return jsonFromBody(global, (v) => Object.keys(asRecord(v)).length === 0, EMPTY_FILE_GUARD_GLOBAL_JSON);
}

function fileGuardTrustedExecJsonFromBody(trusted: unknown): string {
  return jsonFromBody(
    trusted,
    (v) => !Array.isArray(v) || v.length === 0,
    EMPTY_FILE_GUARD_TRUSTED_EXEC_JSON
  );
}

function fileGuardToolBindingsJsonFromBody(bindings: unknown): string {
  return jsonFromBody(
    bindings,
    (v) => Object.keys(asRecord(v)).length === 0,
    EMPTY_FILE_GUARD_TOOL_BINDINGS_JSON
  );
}

function approvalOverridesFromBody(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function ownerScopesFromBody(value: unknown): Record<string, unknown> {
  return asRecord(value);
}

function externalDirectoryFromBody(value: unknown): Record<string, unknown> | undefined {
  if (value === undefined) return undefined;
  const record = asRecord(value);
  return Object.keys(record).length > 0 ? record : undefined;
}

function commandIntentExtraBodyFromBody(value: unknown): Record<string, unknown> {
  const body = asRecord(value);
  return Object.keys(body).length > 0 ? body : { ...COMMAND_INTENT_EXTRA_BODY };
}

export function createDefaultPermissionsFormState(): PermissionsFormState {
  return {
    enabled: true,
    defaults: 'ask',
    denyGuidanceMessage: '',
    tools: [],
    rules: [],
    approvalOverrides: [],
    ownerScopes: {},
    externalDirectory: undefined,
    commandIntentEnabled: true,
    commandIntentTimeout: 15,
    commandIntentExtraBody: { ...COMMAND_INTENT_EXTRA_BODY },
    fileGuardWorkspaceRwEnabled: true,
    fileGuardGlobalJson: EMPTY_FILE_GUARD_GLOBAL_JSON,
    fileGuardTrustedExecJson: EMPTY_FILE_GUARD_TRUSTED_EXEC_JSON,
    fileGuardToolBindingsJson: EMPTY_FILE_GUARD_TOOL_BINDINGS_JSON,
  };
}

export function permissionsBodyToFormState(body: Record<string, unknown>): PermissionsFormState {
  const defaults = createDefaultPermissionsFormState();
  const toolsRaw = asRecord(body.tools);
  const tools: PermissionToolEntry[] = Object.entries(toolsRaw).map(([name, action]) => ({
    key: nextKey('tool'),
    name,
    action: asPermissionAction(action),
  }));

  const rulesRaw = Array.isArray(body.rules) ? body.rules : [];
  const rules: PermissionRuleEntry[] = rulesRaw
    .filter((item) => item && typeof item === 'object')
    .map((item) => {
      const row = item as Record<string, unknown>;
      return {
        key: nextKey('rule'),
        id: String(row.id ?? ''),
        description: String(row.description ?? ''),
        pattern: String(row.pattern ?? ''),
        action: asRuleAction(row.action),
      };
    });

  const commandIntent = asRecord(body.command_intent);
  const fileGuard = asRecord(body.file_guard);
  const workspace = asRecord(fileGuard.workspace);

  return {
    enabled: body.enabled !== false,
    defaults: asPermissionAction(body.defaults, defaults.defaults),
    denyGuidanceMessage: String(body.deny_guidance_message ?? ''),
    tools,
    rules,
    approvalOverrides: approvalOverridesFromBody(body.approval_overrides),
    ownerScopes: ownerScopesFromBody(body.owner_scopes),
    externalDirectory: externalDirectoryFromBody(body.external_directory),
    commandIntentEnabled: commandIntent.enabled !== false,
    commandIntentTimeout: Number(commandIntent.timeout_seconds ?? 15) || 15,
    commandIntentExtraBody: commandIntentExtraBodyFromBody(commandIntent.extra_body),
    fileGuardWorkspaceRwEnabled: workspace.rw_enabled !== false,
    fileGuardGlobalJson: fileGuardGlobalJsonFromBody(fileGuard.global),
    fileGuardTrustedExecJson: fileGuardTrustedExecJsonFromBody(fileGuard.trusted_exec_directory),
    fileGuardToolBindingsJson: fileGuardToolBindingsJsonFromBody(fileGuard.tool_bindings),
  };
}

export function permissionsFormStateToBody(form: PermissionsFormState): Record<string, unknown> {
  const tools: Record<string, PermissionAction> = {};
  for (const row of form.tools) {
    const name = row.name.trim();
    if (!name) continue;
    tools[name] = row.action;
  }

  const rules = form.rules
    .map((row) => {
      const id = row.id.trim();
      const pattern = row.pattern.trim();
      if (!id || !pattern) return null;
      const item: Record<string, unknown> = {
        id,
        pattern,
        action: row.action,
      };
      const description = row.description.trim();
      if (description) item.description = description;
      return item;
    })
    .filter(Boolean) as Record<string, unknown>[];

  const body: Record<string, unknown> = {
    enabled: form.enabled,
    defaults: form.defaults,
    tools,
    rules,
    approval_overrides: form.approvalOverrides,
    owner_scopes: form.ownerScopes,
    deny_guidance_message: form.denyGuidanceMessage,
    command_intent: {
      enabled: form.commandIntentEnabled,
      timeout_seconds: form.commandIntentTimeout,
      extra_body: form.commandIntentExtraBody,
    },
    file_guard: {
      workspace: {
        rw_enabled: form.fileGuardWorkspaceRwEnabled,
      },
      global: parseJsonField(form.fileGuardGlobalJson, {}),
      trusted_exec_directory: parseJsonField(form.fileGuardTrustedExecJson, []),
      tool_bindings: parseJsonField(form.fileGuardToolBindingsJson, {}),
    },
  };

  if (form.externalDirectory !== undefined) {
    body.external_directory = form.externalDirectory;
  }

  return body;
}
