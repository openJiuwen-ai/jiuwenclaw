export type ToolDisplayAction =
  | 'readFile'
  | 'editFile'
  | 'writeFile'
  | 'listDirectory'
  | 'searchContent'
  | 'findFiles'
  | 'runCommand'
  | 'fetchWebpage'
  | 'searchMemory'
  | 'searchWeb'
  | 'runSession'
  | 'useSkill'
  | 'generic';

export interface ToolDisplaySource {
  name: string;
  arguments?: Record<string, unknown>;
  description?: string;
  formatted_args?: string;
}

export interface ToolDisplayInfo {
  action: ToolDisplayAction;
  target?: string;
  rawName: string;
  displayName: string;
}

export type ToolDisplayTranslator = (key: string, options?: Record<string, string>) => string;

export interface ToolGroupDisplaySource {
  status: 'pending' | 'timeout' | 'completed' | 'error';
  result?: {
    success: boolean;
    result?: string;
  };
}

export type ToolGroupDisplayKind = 'running' | 'completed' | 'completedWithFailures' | 'completedWithTimeouts' | 'completedWithIssues';

export interface ToolGroupDisplayState {
  kind: ToolGroupDisplayKind;
  total: number;
  failedCount: number;
  timeoutCount: number;
}

const READ_FILE_TOOLS = new Set(['read', 'read_file', 'read_text_file', 'view']);
const EDIT_FILE_TOOLS = new Set(['edit', 'edit_file', 'search_replace']);
const WRITE_FILE_TOOLS = new Set(['write', 'write_file', 'write_text_file']);
const LIST_DIRECTORY_TOOLS = new Set(['ls', 'list_dir', 'list_files']);
const SEARCH_CONTENT_TOOLS = new Set(['grep', 'rg', 'ripgrep', 'search']);
const FIND_FILE_TOOLS = new Set(['glob', 'glob_files', 'glob_file_search']);
const RUN_COMMAND_TOOLS = new Set(['bash', 'command', 'create_terminal', 'exec', 'mcp_exec_command', 'powershell', 'run', 'sh', 'shell']);
const FETCH_WEBPAGE_TOOLS = new Set(['fetch', 'fetch_webpage', 'mcp_fetch_webpage']);
const SEARCH_WEB_TOOLS = new Set(['mcp_free_search', 'mcp_paid_search', 'web_free_search', 'web_paid_search', 'web_search']);

function getStringArgument(args: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = args[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function summarizeText(value: string | undefined, maxLength = 96): string | undefined {
  if (!value) {
    return undefined;
  }
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return undefined;
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}

function summarizePath(value: string | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const parts = value.split(/[\\/]/).filter(part => part && part !== '.');
  if (parts.length === 0) {
    return value;
  }
  return parts.slice(-3).join('/');
}

function canonicalToolName(name: string): string {
  const trimmed = name.trim();
  const doubleUnderscoreParts = trimmed.split('__').filter(Boolean);
  const withoutMcpNamespace = doubleUnderscoreParts.length > 1 ? doubleUnderscoreParts[doubleUnderscoreParts.length - 1] : trimmed;
  const namespaceParts = withoutMcpNamespace.split(/[./:]/).filter(Boolean);
  const basename = namespaceParts[namespaceParts.length - 1] || withoutMcpNamespace;
  return basename.toLowerCase().replace(/[\s-]+/g, '_');
}

function humanizeToolName(name: string): string {
  const canonical = canonicalToolName(name);
  const words = canonical.split('_').filter(Boolean);
  if (words.length === 0) {
    return 'Tool';
  }
  return words.map(word => `${word.charAt(0).toUpperCase()}${word.slice(1)}`).join(' ');
}

function createInfo(source: ToolDisplaySource, action: ToolDisplayAction, target?: string): ToolDisplayInfo {
  return {
    action,
    ...(target ? { target } : {}),
    rawName: source.name,
    displayName: humanizeToolName(source.name),
  };
}

export function getToolDisplayInfo(source: ToolDisplaySource): ToolDisplayInfo {
  const args = source.arguments || {};
  const name = canonicalToolName(source.name);
  const filePath = summarizePath(getStringArgument(args, 'path', 'file_path', 'file', 'filename', 'dir_path'));

  if (READ_FILE_TOOLS.has(name)) {
    return createInfo(source, 'readFile', filePath);
  }
  if (EDIT_FILE_TOOLS.has(name)) {
    return createInfo(source, 'editFile', filePath);
  }
  if (WRITE_FILE_TOOLS.has(name)) {
    return createInfo(source, 'writeFile', filePath);
  }
  if (LIST_DIRECTORY_TOOLS.has(name)) {
    return createInfo(source, 'listDirectory', filePath || '.');
  }
  if (name === 'memory_search') {
    return createInfo(source, 'searchMemory', summarizeText(getStringArgument(args, 'query', 'q', 'prompt')));
  }
  if (SEARCH_WEB_TOOLS.has(name)) {
    return createInfo(source, 'searchWeb', summarizeText(getStringArgument(args, 'query', 'q', 'prompt')));
  }
  if (SEARCH_CONTENT_TOOLS.has(name)) {
    return createInfo(source, 'searchContent', summarizeText(getStringArgument(args, 'pattern', 'query', 'q', 'prompt')));
  }
  if (FIND_FILE_TOOLS.has(name)) {
    return createInfo(source, 'findFiles', summarizeText(getStringArgument(args, 'glob', 'pattern', 'path', 'file_path')));
  }
  if (RUN_COMMAND_TOOLS.has(name)) {
    return createInfo(source, 'runCommand', summarizeText(getStringArgument(args, 'cmd', 'command', 'script', 'input')));
  }
  if (FETCH_WEBPAGE_TOOLS.has(name)) {
    return createInfo(source, 'fetchWebpage', summarizeText(getStringArgument(args, 'url')));
  }
  if (name === 'session') {
    return createInfo(source, 'runSession', summarizeText(getStringArgument(args, 'description') || source.description));
  }
  if (name === 'skill_tool') {
    return createInfo(source, 'useSkill', summarizeText(getStringArgument(args, 'skill_name', 'skillName', 'name')));
  }

  const info = createInfo(source, 'generic');
  return {
    ...info,
    displayName: summarizeText(source.description || source.formatted_args) || info.displayName,
  };
}

export function formatToolDisplayLabel(source: ToolDisplaySource, translate: ToolDisplayTranslator): string {
  const info = getToolDisplayInfo(source);
  return translate(`chatUi.toolActions.${info.action}`, {
    target: info.target || '',
    name: info.displayName,
  }).trim();
}

export function formatToolResultDisplayLabel(
  source: ToolDisplaySource,
  success: boolean,
  summary: string | undefined,
  translate: ToolDisplayTranslator,
): string {
  const toolLabel = formatToolDisplayLabel(source, translate);
  const outcomeLabel = translate(
    success
      ? 'chatUi.toolResult.successLabel'
      : 'chatUi.toolResult.failureLabel',
    { tool: toolLabel },
  ).trim();
  const normalizedSummary = summary?.trim();
  return normalizedSummary ? `${outcomeLabel} · ${normalizedSummary}` : outcomeLabel;
}

export function isToolDisplayResultSuccessful(result: ToolGroupDisplaySource['result']): boolean {
  return Boolean(result?.success && !String(result.result || '').includes('success=False'));
}

export function getToolGroupDisplayState(executions: ToolGroupDisplaySource[]): ToolGroupDisplayState {
  const total = executions.length;
  const timeoutCount = executions.filter(execution => execution.status === 'timeout').length;
  const failedCount = executions.filter(
    execution =>
      execution.status !== 'timeout' && (execution.status === 'error' || Boolean(execution.result && !isToolDisplayResultSuccessful(execution.result)))
  ).length;

  let kind: ToolGroupDisplayKind = 'completed';
  if (executions.some(execution => execution.status === 'pending')) {
    kind = 'running';
  } else if (failedCount > 0 && timeoutCount > 0) {
    kind = 'completedWithIssues';
  } else if (failedCount > 0) {
    kind = 'completedWithFailures';
  } else if (timeoutCount > 0) {
    kind = 'completedWithTimeouts';
  }

  return {
    kind,
    total,
    failedCount,
    timeoutCount,
  };
}
