import { normalizeAgentFileContent, normalizeAgentFileTree, normalizeAgentTemplateDetail, normalizeAgentTemplateListItem, normalizeSkillOption, resolveLocalizedText, type SupportedLocale } from './adapter';
import { AgentManagementError, type AgentInstallResult, type AgentManagementClient } from './port';
import type { AgentCatalogItem, AgentDetail, AgentDraft, AgentFileContent, DefinitionFileEntry, SkillOption } from './types';
import type { RawAgentFileEntry, RawAgentTemplateDetail, RawAgentTemplateListItem, RawSkillOption } from './raw';

export type FixtureOperation = 'list' | 'detail' | 'files' | 'file' | 'skills' | 'create' | 'install' | 'uninstall';

export type AgentFixtureOptions = {
  faults?: Partial<Record<FixtureOperation, string>>;
  locale?: () => SupportedLocale;
};

const localized = (zh: string, en: string) => ({ zh, en });

const RAW_CATALOG: RawAgentTemplateListItem[] = [
  {
    id: 'workplace-slim-coach',
    displayName: localized('职场减肥教练', 'Workplace Weight-Loss Coach'),
    displayDescription: localized(
      '结合你的体重目标、商圈外卖、预算与作息，定制每日吃动方案并追踪复盘。',
      'Your walking fitness coach for office life.',
    ),
    category: 'IndustryConsultant',
    source: 'local',
    installed: true,
    enabled: true,
  },
  {
    id: 'content-creator',
    displayName: localized('内容创作专家', 'Content Creation Expert'),
    displayDescription: localized(
      '擅长选题策划、多平台内容适配、标题SEO优化、文案润色与内容结构化。',
      'A content creation expert skilled in topic planning and copy refinement.',
    ),
    category: 'Design',
    source: 'local',
    installed: true,
    enabled: true,
  },
  {
    id: 'python-code-reviewer',
    displayName: localized('Python 代码检视专家', 'Python Code Reviewer'),
    displayDescription: localized(
      'Python 代码综合质量检视：排查 Bug、规范 PEP8、提升可读性与可维护性，给出修改建议。',
      'Reviews Python code for correctness, PEP8 style, readability and maintainability.',
    ),
    category: 'Engineering',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
  {
    id: 'market-research-analyst',
    displayName: localized('市场洞察分析师', 'Market Research Analyst'),
    displayDescription: localized('从公开信息中提炼市场变化、竞品动向与可执行洞察。', 'Turns market signals into actionable insights.'),
    category: 'IndustryConsultant',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
  {
    id: 'legal-assistant',
    displayName: localized('法务助理', 'Legal Assistant'),
    displayDescription: localized('辅助梳理合同条款、风险提示和合规检查清单。', 'Helps review contracts, risks and compliance checklists.'),
    category: 'SafetyCompliance',
    source: 'built-in',
    installed: true,
    enabled: true,
  },
  {
    id: 'meeting-assistant',
    displayName: localized('会议助手', 'Meeting Assistant'),
    displayDescription: localized('整理会议材料、行动项和后续跟进信息。', 'Organizes meeting materials, actions and follow-ups.'),
    category: 'Communication',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
  {
    id: 'business-assistant',
    displayName: localized('商务助理', 'Business Assistant'),
    displayDescription: localized('协助准备商务材料、信息摘要和沟通要点。', 'Prepares business materials, summaries and talking points.'),
    category: 'Communication',
    source: 'local',
    installed: true,
    enabled: true,
  },
  {
    id: 'ppt-expert',
    displayName: localized('PPT专家', 'Presentation Expert'),
    displayDescription: localized('把结构化内容转成清晰、易读的演示方案。', 'Turns structured content into clear presentation plans.'),
    category: 'Design',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
  {
    id: 'research-assistant',
    displayName: localized('用户研究专家', 'User Research Expert'),
    displayDescription: localized('帮助设计访谈、归纳反馈并提炼用户需求。', 'Helps design interviews and synthesize user feedback.'),
    category: 'DataAnalysis',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
  {
    id: 'document-expert',
    displayName: localized('文档专家', 'Document Expert'),
    displayDescription: localized('整理资料、提炼结构并生成清晰的文档初稿。', 'Organizes material and turns it into clear document drafts.'),
    category: 'Design',
    source: 'local',
    installed: true,
    enabled: true,
  },
  {
    id: 'architecture-expert',
    displayName: localized('架构专家', 'Architecture Expert'),
    displayDescription: localized('帮助拆解系统边界、技术方案与演进路径。', 'Helps define system boundaries, technical plans and evolution paths.'),
    category: 'ProductDevelopment',
    source: 'local',
    installed: true,
    enabled: true,
  },
  {
    id: 'operations-expert',
    displayName: localized('运营专家', 'Operations Expert'),
    displayDescription: localized('辅助制定运营计划、复盘结果并沉淀执行动作。', 'Helps plan operations, review results and capture next actions.'),
    category: 'Marketing',
    source: 'built-in',
    installed: false,
    enabled: false,
  },
];

const RAW_SKILLS: RawSkillOption[] = [
  { name: 'content-methodology', display_name: 'content-methodology', description: '内容创作方法与结构化表达' },
  { name: 'news-analysis', display_name: 'news-analysis', description: '新闻信息分析与整理' },
  { name: 'world-trends', display_name: 'world-trends', description: '世界局势与趋势分析' },
  { name: 'code-review', display_name: 'code-review', description: '代码质量与可维护性检查' },
];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function makeRawDetail(item: RawAgentTemplateListItem, locale: SupportedLocale): RawAgentTemplateDetail {
  const name = item.displayName || localized(item.id, item.id);
  const description = item.displayDescription || localized('', '');
  const displayName = resolveLocalizedText(name, locale) || item.id;
  const displayDescription = resolveLocalizedText(description, locale);
  return {
    ...clone(item),
    avatar: '',
    version: '1.0.0',
    details: locale === 'en'
      ? `# ${displayName}\n\n${displayDescription}\n\n## How it works\n\nBreak down the problem from your goal, state the basis, and give an actionable next step.`
      : `# ${displayName}\n\n${displayDescription}\n\n## 工作方式\n\n根据你的目标拆解问题，先说明依据，再给出可执行的下一步。`,
    prompt: locale === 'en'
      ? `You are ${displayName}. Keep your answers clear, reliable, and actionable.`
      : `你是${displayName}，请保持清晰、可靠、可执行。`,
    tags: [
      { id: 'structured-output', ...localized('结构化输出', 'Structured Output') },
      { id: 'workflow', ...localized('工作流程', 'Workflow') },
    ],
    skills: [
      {
        id: item.id === 'python-code-reviewer' ? 'code-review' : 'content-methodology',
        displayName: localized(item.id === 'python-code-reviewer' ? '代码检视' : '内容方法', item.id === 'python-code-reviewer' ? 'Code Review' : 'Content Methodology'),
        displayDescription: localized('帮助智能体完成稳定、可复用的工作步骤。', 'Provides reusable working steps.'),
      },
    ],
    tools: [
      {
        id: 'workspace-search',
        displayName: localized('工作区检索', 'Workspace Search'),
        displayDescription: localized('在授权工作区中查找相关资料。', 'Finds relevant material in the authorized workspace.'),
      },
    ],
    rails: [
      {
        id: 'quality-reminder',
        displayName: localized('质量提醒', 'Quality Reminder'),
        displayDescription: localized('提醒输出依据、边界和下一步。', 'Keeps evidence, boundaries and next steps visible.'),
      },
    ],
    mcps: [],
    quickInputs: [
      localized(`请帮我用${name.zh || name.en || item.id}拆解一个具体问题`, `Help me break down a problem with ${name.en || item.id}`),
      localized('请先说明你的判断依据，再给出执行步骤', 'Explain your basis before giving the steps.'),
      localized('请帮我复盘执行结果，并指出下一步', 'Review the result and identify the next step.'),
    ],
  };
}

function makeRawFiles(id: string): RawAgentFileEntry[] {
  return [
    {
      path: 'avatars/',
      type: 'dir',
      children: [{ path: 'avatar.png', type: 'file', size: 1240 }],
    },
    {
      path: 'persona/',
      type: 'dir',
      children: [{ path: `persona/${id}.md`, type: 'file', size: 764 }],
    },
    {
      path: 'skills/',
      type: 'dir',
      children: [{ path: 'skills/content-methodology/', type: 'dir', children: [{ path: 'skills/content-methodology/SKILL.md', type: 'file', size: 1240 }] }],
    },
    {
      path: 'rails/',
      type: 'dir',
      children: [{ path: 'rails/slim_reminder_rail.py', type: 'file', size: 620 }],
    },
    {
      path: 'subagents/',
      type: 'dir',
      children: [{ path: 'subagents/research-helper.md', type: 'file', size: 720 }],
    },
    {
      path: 'tools/',
      type: 'dir',
      children: [{ path: 'tools/workspace-search.json', type: 'file', size: 460 }],
    },
    { path: 'manifest.json', type: 'file', size: 456 },
    { path: 'README.md', type: 'file', size: 980 },
    { path: 'runtime.bin', type: 'file', size: 82 },
  ];
}

function collectFilePaths(entries: RawAgentFileEntry[]): string[] {
  return entries.flatMap((entry) => [entry.path, ...(entry.children ? collectFilePaths(entry.children) : [])]);
}

export class FixtureAgentManagementClient implements AgentManagementClient {
  readonly source = 'fixture' as const;
  private readonly faults: Partial<Record<FixtureOperation, string>>;
  private readonly getLocale: () => SupportedLocale;
  private catalog: RawAgentTemplateListItem[];

  constructor(options: AgentFixtureOptions = {}) {
    this.catalog = clone(RAW_CATALOG);
    this.faults = options.faults || {};
    this.getLocale = options.locale || (() => 'zh');
  }

  private maybeFail(operation: FixtureOperation): void {
    const message = this.faults[operation];
    if (message) {
      throw new AgentManagementError(message, `fixture_${operation}_failed`, true);
    }
  }

  private findItem(id: string): RawAgentTemplateListItem {
    const item = this.catalog.find((entry) => entry.id === id);
    if (!item) {
      throw new AgentManagementError(`Unknown fixture Agent: ${id}`, 'fixture_agent_not_found', false);
    }
    return item;
  }

  async listCatalog(): Promise<AgentCatalogItem[]> {
    this.maybeFail('list');
    const locale = this.getLocale();
    return this.catalog.map((item) => normalizeAgentTemplateListItem(clone(item), locale));
  }

  async getDefinition(id: string): Promise<AgentDetail> {
    this.maybeFail('detail');
    const item = this.findItem(id);
    const locale = this.getLocale();
    return normalizeAgentTemplateDetail(makeRawDetail(item, locale), locale);
  }

  async getDefinitionFiles(id: string): Promise<DefinitionFileEntry[]> {
    this.maybeFail('files');
    this.findItem(id);
    return normalizeAgentFileTree(makeRawFiles(id));
  }

  async getDefinitionFile(id: string, relativePath: string): Promise<AgentFileContent> {
    this.maybeFail('file');
    this.findItem(id);
    const knownPaths = new Set(collectFilePaths(makeRawFiles(id)));
    if (!knownPaths.has(relativePath)) {
      throw new AgentManagementError(`Unknown fixture file: ${relativePath}`, 'fixture_file_not_found', false);
    }
    const item = this.findItem(id);
    const detail = makeRawDetail(item, this.getLocale());
    const content = relativePath.endsWith('manifest.json')
      ? JSON.stringify({ packageType: 'agent_template', id, displayName: detail.displayName, skills: detail.skills?.map((skill) => skill.id) || [] }, null, 2)
      : relativePath.endsWith('.json')
        ? JSON.stringify({ id, source: item.source, installed: item.installed }, null, 2)
        : detail.details || `# ${detail.displayName?.zh || id}\n`;
    return normalizeAgentFileContent({ path: relativePath, content });
  }

  async listSkillOptions(): Promise<SkillOption[]> {
    this.maybeFail('skills');
    return RAW_SKILLS.map((item) => normalizeSkillOption(clone(item)));
  }

  async createAgent(draft: AgentDraft): Promise<void> {
    this.maybeFail('create');
    if (this.catalog.some((item) => item.id === draft.id)) {
      throw new AgentManagementError('This Agent id already exists.', 'fixture_agent_id_exists', false);
    }
    this.catalog.push({
      id: draft.id,
      displayName: localized(draft.name, draft.name),
      displayDescription: localized(draft.description, draft.description),
      category: 'Custom',
      source: 'local',
      installed: false,
      enabled: false,
    });
  }

  async installDefinition(id: string): Promise<AgentInstallResult> {
    this.maybeFail('install');
    const item = this.findItem(id);
    item.installed = true;
    item.enabled = true;
    return { kind: 'ok' };
  }

  async uninstallDefinition(id: string): Promise<void> {
    this.maybeFail('uninstall');
    const item = this.findItem(id);
    item.installed = false;
    item.enabled = false;
  }
}

export function createFixtureAgentManagementClient(options?: AgentFixtureOptions): AgentManagementClient {
  return new FixtureAgentManagementClient(options);
}
