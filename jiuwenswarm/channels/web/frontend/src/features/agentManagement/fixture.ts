import {
  normalizeAgentFileContent,
  normalizeAgentFileTree,
  normalizeAgentTemplateDetail,
  normalizeAgentTemplateListItem,
  normalizeSkillOption,
  resolveLocalizedText,
  type SupportedLocale,
} from './adapter';
import { AgentManagementError, type AgentInstallResult, type AgentManagementClient } from './port';
import type { AgentCatalogItem, AgentDetail, AgentDraft, AgentFileContent, DefinitionFileEntry, SkillOption } from './types';
import type { RawAgentFileEntry, RawAgentTemplateDetail, RawAgentTemplateListItem, RawSkillOption } from './raw';

export type FixtureOperation = 'list' | 'detail' | 'files' | 'file' | 'skills' | 'create' | 'install' | 'uninstall';

export type AgentFixtureOptions = {
  faults?: Partial<Record<FixtureOperation, string>>;
  locale?: () => SupportedLocale;
};

const localized = (zh: string, en: string) => ({ zh, en });

const designTag = (id: string, zh: string, en: string) => ({ id, ...localized(zh, en) });

const DESIGN_DESCRIPTION = localized(
  '这是一段描述信息一段描述信息一段描述信息一段描述信息一段描述信息一段描述信万',
  'A concise capability description that explains the Agent and its reusable strengths.',
);

const DEVELOPER_DESCRIPTION = localized(
  '能理解自然语言需求的数字员工。它不仅会写代码，还能自主拆解任务、跨文件联动、并在沙箱中自动调试和修复 Bug，直至交付完整可运行的系统。',
  'A digital employee that understands natural-language requirements, breaks down work, edits across files, and debugs in a sandbox until delivery.',
);

const DEVELOPER_DETAILS_ZH = `# 开发工程师

你是开发工程师的「行走的架构师/技术教练」，服务没时间啃文档、没空重构的忙碌开发者。核心任务：把技术提升和项目交付拆成每日最小可执行动作——今天重构哪段代码（类/方法级）、什么时候学什么（分钟级），并持续追踪进度。所有档案与方案生成只通过 dev-pilot 完成，禁止手动读写 profile.json / repositories.json

## 核心能力

建档测算：采集技术栈、工龄、目标岗位、deadline、每日学习时间、自研比例、开发预算、作息、技术盲区/屎山项目 → 用 dev-pilot(init) 算出技术债指数(TDI)、每日时间预算，存档。
重构处方：基于真实代码库，给出可执行方案——类名、方法名、重构手法（提炼函数/多态等）、注意事项（兼容性/补测试）、复杂度估算。复杂架构设计交给 arch-planner。
碎片化学习：按当天可用时间安排（5min技术周刊/20min刷题/30min动手写Demo）；加班自动降级，不布置做不到的任务。
追踪复盘：记录 Commit 与学习执行，生成技术债趋势图，识别平台期，每周复盘调整。

## 工作流程

首次建档：加载 profile-manager，每次问3-4个问题采集信息 → dev-pilot(init) 生成TDI、时间预算 → 目标不切实际时明确指出并协商 → 输出首版《技术提升与重构方案》。
日常对话：先 dev-pilot(profile) 读档，再 dev-pilot(today, minutes=X) 拿今日方案。
落地到代码库：首次告知项目库时，联网查最佳实践（开源库/API/设计模式/性能损耗）→ dev-pilot(repo_add) 录入 → 之后按真实模块给具体重构方案（文件路径、改法、预计节省行数/降低圈复杂度）。项目库为空时只给通用参考，不冒充真实代码。
实践入账：用户问“学什么/写什么”→ 加载 task-scheduler → dev-pilot(today) 安排；用户报告完成情况 → 估算技术债消减、动态调整剩余任务。
打卡周报：加载 progress-tracker 记录+简短反馈；每7天或用户要求 → 生成周报（趋势图+执行率+下周调整）。

## 输出规范

建议必须可直接执行（含类名/方法名/手法），拒绝空泛描述。
每条方案标注复杂度数字或时间估算。
用编号列表：步骤+核心代码片段/概念+耗时，总时长不超用户可用时间。
每日方案控制在一屏内；周报用图表（雷达图/折线图）可视化。
语气像资深架构师同事：直接专业、不讲黑话、不灌鸡汤。
涉及工具结果时附 dev-pilot 关键数字作依据。`;

const DEVELOPER_FILE_CONTENT_ZH = DEVELOPER_DETAILS_ZH;

const DESIGN_TAGS = {
  efficiency: designTag('efficiency', '效率提升', 'Efficiency'),
  content: designTag('content-creation', '内容创作', 'Content Creation'),
};

const DRAFT_TAG_LABELS: Record<string, { zh: string; en: string }> = {
  'code-delivery': localized('代码交付', 'Code Delivery'),
  'code-review': localized('代码审查', 'Code Review'),
  'bug-fix': localized('Bug修复', 'Bug Fixes'),
};

const RAW_CATALOG: RawAgentTemplateListItem[] = [
  {
    id: 'workplace-slim-coach',
    displayName: localized('速记文员', 'Stenographer'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Efficiency',
    source: 'local',
    installed: true,
    enabled: true,
    tags: [DESIGN_TAGS.efficiency, DESIGN_TAGS.content],
    avatar: '/agent-management/avatar-yellow.svg',
  },
  {
    id: 'content-creator',
    displayName: localized('开发工程师', 'Software Engineer'),
    displayDescription: DEVELOPER_DESCRIPTION,
    category: 'ProductDevelopment',
    source: 'built-in',
    installed: true,
    enabled: true,
    updateAvailable: true,
    tags: [DESIGN_TAGS.efficiency, DESIGN_TAGS.content],
    avatar: '/agent-management/avatar-green.svg',
  },
  {
    id: 'python-code-reviewer',
    displayName: localized('市场洞察分析师', 'Market Insights Analyst'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Marketing',
    source: 'built-in',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-pink.svg',
  },
  {
    id: 'market-research-analyst',
    displayName: localized('商业尽调顾问', 'Business Due Diligence Advisor'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'DataAnalysis',
    source: 'built-in',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-cyan.svg',
  },
  {
    id: 'legal-assistant',
    displayName: localized('PPT专家', 'Presentation Expert'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Efficiency',
    source: 'built-in',
    installed: true,
    enabled: true,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-red.svg',
  },
  {
    id: 'meeting-assistant',
    displayName: localized('法务助理', 'Legal Assistant'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'SafetyCompliance',
    source: 'built-in',
    installed: true,
    enabled: true,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-orange.svg',
  },
  {
    id: 'business-assistant',
    displayName: localized('商务助理', 'Business Assistant'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Communication',
    source: 'local',
    installed: true,
    enabled: true,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-yellow.svg',
  },
  {
    id: 'ppt-expert',
    displayName: localized('架构专家', 'Architecture Expert'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'ProductDevelopment',
    source: 'built-in',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-purple.svg',
  },
  {
    id: 'research-assistant',
    displayName: localized('会议助手', 'Meeting Assistant'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Communication',
    source: 'built-in',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-pink.svg',
  },
  {
    id: 'document-expert',
    displayName: localized('coco', 'coco'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Efficiency',
    source: 'local',
    installed: true,
    enabled: true,
    tags: [DESIGN_TAGS.efficiency],
    avatar: '/agent-management/avatar-cyan.svg',
  },
  {
    id: 'architecture-expert',
    displayName: localized('纪要专员', 'Meeting Notes Specialist'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Communication',
    source: 'builtin',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency, DESIGN_TAGS.content],
    avatar: '/agent-management/avatar-cyan.svg',
  },
  {
    id: 'operations-expert',
    displayName: localized('运营专家', 'Operations Expert'),
    displayDescription: DESIGN_DESCRIPTION,
    category: 'Marketing',
    source: 'built-in',
    installed: false,
    enabled: false,
    tags: [DESIGN_TAGS.efficiency, DESIGN_TAGS.content],
    avatar: '/agent-management/avatar-cyan.svg',
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

function makeRawDetail(item: RawAgentTemplateListItem, locale: SupportedLocale, draft?: AgentDraft): RawAgentTemplateDetail {
  const name = item.displayName || localized(item.id, item.id);
  const description = item.displayDescription || localized('', '');
  const displayName = resolveLocalizedText(name, locale) || item.id;
  const displayDescription = resolveLocalizedText(description, locale);
  const isDeveloper = item.id === 'content-creator';
  return {
    ...clone(item),
    avatar: item.avatar || '',
    version: '1.0.0',
    details: isDeveloper
      ? locale === 'en'
        ? `# Software Engineer\n\n${displayDescription}\n\nA digital employee that understands natural-language requirements, breaks down tasks, edits across files, and debugs in a sandbox until delivery.`
        : DEVELOPER_DETAILS_ZH
      : locale === 'en'
        ? `# ${displayName}\n\n${displayDescription}\n\n${draft?.persona || '## How it works\n\nBreak down the problem from your goal, state the basis, and give an actionable next step.'}`
        : `# ${displayName}\n\n${displayDescription}\n\n${draft?.persona || '## 工作方式\n\n根据你的目标拆解问题，先说明依据，再给出可执行的下一步。'}`,
    prompt: locale === 'en' ? `You are ${displayName}. Keep your answers clear, reliable, and actionable.` : `你是${displayName}，请保持清晰、可靠、可执行。`,
    tags: isDeveloper
      ? [
          { id: 'code-delivery', ...localized('代码交付', 'Code Delivery') },
          { id: 'code-review', ...localized('代码审查', 'Code Review') },
          { id: 'bug-fix', ...localized('Bug修复', 'Bug Fixes') },
        ]
      : item.tags && item.tags.length > 0
        ? clone(item.tags)
        : [
            { id: 'structured-output', ...localized('结构化输出', 'Structured Output') },
            { id: 'workflow', ...localized('工作流程', 'Workflow') },
          ],
    skills: isDeveloper
      ? [
          {
            id: 'news-analysis',
            displayName: localized('新闻分析与整理', 'News Analysis and Curation'),
            displayDescription: localized('整理和提炼与任务相关的新闻信息。', 'Organizes and distills task-relevant news.'),
          },
          {
            id: 'world-trends',
            displayName: localized('世界局势分析', 'World Situation Analysis'),
            displayDescription: localized('分析全球形势与变化趋势。', 'Analyzes global situations and trends.'),
          },
          {
            id: 'political-analysis',
            displayName: localized('形式与政治分析技能包', 'Political and Situation Analysis'),
            displayDescription: localized('提供结构化的形势与政治分析能力。', 'Provides structured situation and political analysis.'),
          },
        ]
      : draft?.skillRefs && draft.skillRefs.length > 0
        ? draft.skillRefs.map(skillId => {
            const skill = RAW_SKILLS.find(option => (option.name || option.display_name) === skillId);
            return {
              id: skillId,
              displayName: localized(skill?.display_name || skillId, skill?.display_name || skillId),
              displayDescription: localized(skill?.description || '', skill?.description || ''),
            };
          })
        : [
            {
              id: 'content-methodology',
              displayName: localized('内容方法', 'Content Methodology'),
              displayDescription: localized('帮助智能体完成稳定、可复用的工作步骤。', 'Provides reusable working steps.'),
            },
          ],
    tools: isDeveloper
      ? []
      : [
          {
            id: 'workspace-search',
            displayName: localized('工作区检索', 'Workspace Search'),
            displayDescription: localized('在授权工作区中查找相关资料。', 'Finds relevant material in the authorized workspace.'),
          },
        ],
    rails: isDeveloper
      ? []
      : [
          {
            id: 'quality-reminder',
            displayName: localized('质量提醒', 'Quality Reminder'),
            displayDescription: localized('提醒输出依据、边界和下一步。', 'Keeps evidence, boundaries and next steps visible.'),
          },
        ],
    mcps: [],
    quickInputs:
      draft?.suggestedPrompts.map(prompt => localized(prompt, prompt)) ||
      (isDeveloper
        ? [
            localized(
              '“用户登录时抛出空指针异常，请分析下方 Traceback，定位并在沙箱中修复。”',
              '"A null pointer exception occurs during login. Analyze the traceback and fix it in the sandbox."',
            ),
            localized(
              '"帮我加个导出 PDF 账单功能，包括接口、后端逻辑和自动化测试。"',
              '"Add a PDF invoice export feature, including the API, backend logic, and automated tests."',
            ),
            localized(
              '"帮我从执行效率和代码可读性两方面重构订单处理模块，给出跨文件修改方案。"',
              '"Refactor the order processing module for efficiency and readability with a cross-file change plan."',
            ),
          ]
        : [
            localized(`请帮我用${name.zh || name.en || item.id}拆解一个具体问题`, `Help me break down a problem with ${name.en || item.id}`),
            localized('请先说明你的判断依据，再给出执行步骤', 'Explain your basis before giving the steps.'),
            localized('请帮我复盘执行结果，并指出下一步', 'Review the result and identify the next step.'),
          ]),
  };
}

function makeRawFiles(id: string): RawAgentFileEntry[] {
  if (id === 'content-creator') {
    return [
      {
        path: '文件/',
        type: 'dir',
        children: [
          { path: '文件/workplace-slim-coach.md', type: 'file', visible: false, size: DEVELOPER_FILE_CONTENT_ZH.length },
          { path: '文件/SKILL.md', type: 'file', size: 2840 },
          {
            path: '文件/Assets/',
            type: 'dir',
            children: [
              { path: '文件/Assets/motion.min.js', type: 'file', size: 18400 },
              { path: '文件/Assets/template.html', type: 'file', size: 3420 },
            ],
          },
          {
            path: '文件/References/',
            type: 'dir',
            children: [
              { path: '文件/References/checklist.md', type: 'file', size: 1280 },
              { path: '文件/References/layouts-swiss.md', type: 'file', size: 2160 },
              { path: '文件/References/themes.md', type: 'file', size: 1880 },
              { path: '文件/References/components.md', type: 'file', size: 2440 },
              { path: '文件/References/image-prompts.md', type: 'file', size: 1640 },
              { path: '文件/References/screenshot-frame.md', type: 'file', size: 1720 },
              { path: '文件/References/swiss-local-layout.md', type: 'file', size: 1960 },
            ],
          },
        ],
      },
    ];
  }
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
  return entries.flatMap(entry => [entry.path, ...(entry.children ? collectFilePaths(entry.children) : [])]);
}

export class FixtureAgentManagementClient implements AgentManagementClient {
  readonly source = 'fixture' as const;
  private readonly faults: Partial<Record<FixtureOperation, string>>;
  private readonly getLocale: () => SupportedLocale;
  private catalog: RawAgentTemplateListItem[];
  private readonly drafts = new Map<string, AgentDraft>();

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
    const item = this.catalog.find(entry => entry.id === id);
    if (!item) {
      throw new AgentManagementError(`Unknown fixture Agent: ${id}`, 'fixture_agent_not_found', false);
    }
    return item;
  }

  async listCatalog(): Promise<AgentCatalogItem[]> {
    this.maybeFail('list');
    const locale = this.getLocale();
    return this.catalog.map(item => normalizeAgentTemplateListItem(clone(item), locale));
  }

  async getDefinition(id: string): Promise<AgentDetail> {
    this.maybeFail('detail');
    const item = this.findItem(id);
    const locale = this.getLocale();
    return normalizeAgentTemplateDetail(makeRawDetail(item, locale, this.drafts.get(id)), locale);
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
    const detail = makeRawDetail(item, this.getLocale(), this.drafts.get(id));
    const content =
      id === 'content-creator' && relativePath === '文件/workplace-slim-coach.md'
        ? this.getLocale() === 'zh'
          ? DEVELOPER_FILE_CONTENT_ZH
          : `# workplace-slim-coach\n\n${detail.details || ''}`
        : relativePath.endsWith('manifest.json')
          ? JSON.stringify({ packageType: 'agent_template', id, displayName: detail.displayName, skills: detail.skills?.map(skill => skill.id) || [] }, null, 2)
          : relativePath.endsWith('.json')
            ? JSON.stringify({ id, source: item.source, installed: item.installed }, null, 2)
            : detail.details || `# ${detail.displayName?.zh || id}\n`;
    return normalizeAgentFileContent({ path: relativePath, content });
  }

  async listSkillOptions(): Promise<SkillOption[]> {
    this.maybeFail('skills');
    return RAW_SKILLS.map(item => normalizeSkillOption(clone(item)));
  }

  async createAgent(draft: AgentDraft): Promise<void> {
    this.maybeFail('create');
    if (this.catalog.some(item => item.id === draft.id)) {
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
      tags: draft.tagIds.map(id => ({ id, ...(DRAFT_TAG_LABELS[id] || localized(id, id)) })),
    });
    this.drafts.set(draft.id, clone(draft));
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
