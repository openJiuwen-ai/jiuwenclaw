import type {
  AgentCatalogItem,
  AgentDetail,
  AgentDraft,
  AgentFileContent,
  AgentManagementErrorShape,
  AgentManagementSource,
  AgentSelectionIntent,
  DefinitionFileEntry,
  SkillOption,
} from './types';

export type AgentInstallResult =
  | { kind: 'ok' }
  | {
      kind: 'auth_required';
      id: string;
      authId: string;
      mcpId: string;
      prompt: string;
      fields: Array<{ name: string; type: string; label: string }>;
    };

export class AgentManagementError extends Error implements AgentManagementErrorShape {
  code: string;
  retriable: boolean;

  constructor(message: string, code = 'agent_management_request_failed', retriable = true) {
    super(message);
    this.name = 'AgentManagementError';
    this.code = code;
    this.retriable = retriable;
  }
}

export interface AgentManagementClient {
  readonly source: AgentManagementSource;
  listCatalog(): Promise<AgentCatalogItem[]>;
  getDefinition(id: string): Promise<AgentDetail>;
  getDefinitionFiles(id: string): Promise<DefinitionFileEntry[]>;
  getDefinitionFile(id: string, relativePath: string): Promise<AgentFileContent>;
  listSkillOptions(): Promise<SkillOption[]>;
  createAgent(draft: AgentDraft): Promise<void>;
  installDefinition(id: string): Promise<AgentInstallResult>;
  uninstallDefinition(id: string): Promise<void>;
}

export function buildDefinitionSelectionPayload(intent: AgentSelectionIntent): Record<string, string> {
  if (intent.kind === 'select') {
    return { agent_template_name: intent.id };
  }
  if (intent.kind === 'clear') {
    return { agent_template_name: '' };
  }
  return {};
}
