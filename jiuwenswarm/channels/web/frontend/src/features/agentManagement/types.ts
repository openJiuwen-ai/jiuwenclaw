export type AgentSource = 'builtin' | 'local';

export type AgentManagementSource = 'fixture' | 'live';

export type RequestStatus = 'idle' | 'loading' | 'success' | 'error';

export type AgentCatalogItem = {
  id: string;
  displayName: string;
  description: string;
  category: string;
  source: AgentSource;
  installed: boolean;
  enabled?: boolean;
  updateAvailable?: boolean;
  tags: Array<{ id: string; label: string }>;
  avatarUrl: string | null;
};

export type AgentCapability = {
  id: string;
  name: string;
  description: string;
};

export type AgentDetail = AgentCatalogItem & {
  prompt: string;
  details: string;
  skills: AgentCapability[];
  tools: AgentCapability[];
  rails: AgentCapability[];
  mcps: AgentCapability[];
  suggestedPrompts: string[];
};

export type DefinitionFileEntry = {
  relativePath: string;
  kind: 'file' | 'directory';
  visible?: boolean;
  size?: number;
  children?: DefinitionFileEntry[];
  previewable: boolean;
};

export type AgentFileContent = {
  relativePath: string;
  content: string;
};

export type SkillOption = {
  id: string;
  name: string;
  description: string;
};

export type AgentDraft = {
  id: string;
  name: string;
  description: string;
  persona: string;
  tagIds: string[];
  skillRefs: string[];
  suggestedPrompts: string[];
};

export type AgentSelectionIntent = { kind: 'keep' } | { kind: 'clear' } | { kind: 'select'; id: string };

export type AgentManagementErrorShape = {
  code: string;
  message: string;
  retriable: boolean;
};
