import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isPreviewableFile,
  normalizeAgentSource,
  normalizeAgentTemplateDetail,
  normalizeAgentTemplateListItem,
  normalizeAgentFileTree,
} from '../node_modules/.cache/agent-management/adapter.js';
import { buildDefinitionSelectionPayload } from '../node_modules/.cache/agent-management/port.js';
import { FixtureAgentManagementClient } from '../node_modules/.cache/agent-management/fixture.js';
import { agentManagementReducer, initialAgentManagementState } from '../node_modules/.cache/agent-management/state.js';
import { buildCatalogViewModel } from '../node_modules/.cache/agent-management/viewModel.js';
import { mergeAgentDetailWithCatalog } from '../node_modules/.cache/agent-management/viewModel.js';

test('normalizes interface source variants and bilingual display fields', () => {
  assert.equal(normalizeAgentSource('built-in'), 'builtin');
  assert.equal(normalizeAgentSource('builtin-in'), 'builtin');
  assert.equal(normalizeAgentSource('local'), 'local');

  const item = normalizeAgentTemplateListItem(
    {
      id: 'python-code-reviewer',
      displayName: { zh: 'Python 代码检视专家', en: 'Python Code Reviewer' },
      displayDescription: { zh: '检查 Python 代码', en: 'Reviews Python code' },
      category: 'Engineering',
      source: 'built-in',
      installed: true,
      enabled: true,
    },
    'en',
  );

  assert.deepEqual(item, {
    id: 'python-code-reviewer',
    displayName: 'Python Code Reviewer',
    description: 'Reviews Python code',
    category: 'Engineering',
    source: 'builtin',
    installed: true,
    enabled: true,
    tags: [],
    avatarUrl: null,
  });
  assert.equal(item.enabled, true);
});

test('projects detail capabilities without leaking raw package fields', () => {
  const detail = normalizeAgentTemplateDetail(
    {
      id: 'content-creator',
      displayName: { zh: '内容创作专家', en: 'Content Creation Expert' },
      displayDescription: { zh: '内容能力', en: 'Content capability' },
      source: 'local',
      avatar: 'avatars/avatar.png',
      version: '1.0.0',
      details: '# 内容创作专家',
      tags: [{ id: 'copywriting', zh: '文案创作', en: 'Copywriting' }],
      skills: [{ id: 'content-methodology', displayName: { zh: '内容方法', en: 'Content Methodology' } }],
      tools: [],
      rails: [],
      mcps: [],
      quickInputs: [{ zh: '帮我写标题', en: 'Write titles' }],
    },
    'zh',
  );

  assert.equal(detail.displayName, '内容创作专家');
  assert.equal(detail.tags[0].id, 'copywriting');
  assert.equal(detail.skills[0].id, 'content-methodology');
  assert.deepEqual(detail.suggestedPrompts, ['帮我写标题']);
  assert.equal('version' in detail, false);
  assert.equal('api_key' in detail, false);
});

test('detail merges authoritative install state from list when show omits it', () => {
  const detail = normalizeAgentTemplateDetail({ id: 'python-code-reviewer', displayName: { zh: 'Python' } }, 'zh');
  const merged = mergeAgentDetailWithCatalog(detail, {
    id: 'python-code-reviewer',
    displayName: 'Python 代码检视专家',
    description: '检查 Python 代码',
    category: 'Engineering',
    source: 'builtin',
    installed: true,
    tags: [],
    avatarUrl: null,
  });

  assert.equal(merged.installed, true);
  assert.equal(merged.source, 'builtin');
});

test('preserves an explicitly disabled template for selection guards', () => {
  const item = normalizeAgentTemplateListItem(
    {
      id: 'disabled-agent',
      displayName: { zh: '不可用专家' },
      installed: true,
      enabled: false,
    },
    'zh',
  );

  assert.equal(item.enabled, false);
});

test('normalizes package file tree and keeps preview policy extension-based', () => {
  assert.equal(isPreviewableFile('README.md'), true);
  assert.equal(isPreviewableFile('manifest.JSON'), true);
  assert.equal(isPreviewableFile('runtime.bin'), false);

  const tree = normalizeAgentFileTree([
    { path: 'persona/', type: 'dir', children: [{ path: 'persona/agent.md', type: 'file', size: 12 }] },
    { path: 'manifest.json', type: 'file', size: 42 },
  ]);

  assert.equal(tree[0].kind, 'directory');
  assert.equal(tree[0].children[0].previewable, true);
  assert.equal(tree[1].previewable, true);
});

test('selection payload preserves keep, clear and select semantics', () => {
  assert.deepEqual(buildDefinitionSelectionPayload({ kind: 'keep' }), {});
  assert.deepEqual(buildDefinitionSelectionPayload({ kind: 'clear' }), { agent_template_name: '' });
  assert.deepEqual(buildDefinitionSelectionPayload({ kind: 'select', id: 'content-creator' }), {
    agent_template_name: 'content-creator',
  });
});

test('fixture writes refresh authoritative list state instead of optimistic UI state', async () => {
  const client = new FixtureAgentManagementClient();
  const before = await client.listCatalog();
  assert.equal(before.find((item) => item.id === 'python-code-reviewer')?.installed, false);

  const result = await client.installDefinition('python-code-reviewer');
  assert.deepEqual(result, { kind: 'ok' });
  const afterInstall = await client.listCatalog();
  assert.equal(afterInstall.find((item) => item.id === 'python-code-reviewer')?.installed, true);

  await client.uninstallDefinition('python-code-reviewer');
  const afterUninstall = await client.listCatalog();
  assert.equal(afterUninstall.find((item) => item.id === 'python-code-reviewer')?.installed, false);
});

test('fixture fault controls expose deterministic request error states', async () => {
  const client = new FixtureAgentManagementClient({ faults: { list: 'Fixture list failed' } });
  await assert.rejects(() => client.listCatalog(), /Fixture list failed/);
});

test('fixture projects the active locale through list and detail adapters', async () => {
  const client = new FixtureAgentManagementClient({ locale: () => 'en' });
  const catalog = await client.listCatalog();
  assert.equal(catalog.find((item) => item.id === 'content-creator')?.displayName, 'Content Creation Expert');

  const detail = await client.getDefinition('content-creator');
  assert.equal(detail.displayName, 'Content Creation Expert');
  assert.match(detail.details, /Content Creation Expert/);
});

test('fixture create adds a local definition and its file source remains addressable', async () => {
  const client = new FixtureAgentManagementClient();
  await client.createAgent({
    id: 'custom-agent',
    name: '自定义专家',
    description: '用于测试创建流程',
    persona: '你是一个测试专家。',
    skillRefs: [],
  });

  const created = (await client.listCatalog()).find((item) => item.id === 'custom-agent');
  assert.equal(created?.source, 'local');
  assert.equal(created?.installed, false);

  const files = await client.getDefinitionFiles('custom-agent');
  assert.equal(files.some((entry) => entry.relativePath === 'README.md'), true);
  assert.equal(files.some((entry) => entry.relativePath === 'rails/' && entry.children?.some((child) => child.relativePath === 'rails/slim_reminder_rail.py')), true);
  const content = await client.getDefinitionFile('custom-agent', 'README.md');
  assert.match(content.content, /自定义专家/);
});

test('catalog view model filters mine/search and clamps pages deterministically', () => {
  const catalog = [
    { id: 'a', displayName: '甲', description: '市场', category: 'Design', source: 'local', installed: true, tags: [], avatarUrl: null },
    { id: 'b', displayName: '乙', description: '工程', category: 'Engineering', source: 'builtin', installed: false, tags: [], avatarUrl: null },
  ];
  const view = buildCatalogViewModel(catalog, {
    scope: 'mine',
    category: '',
    query: '市场',
    page: 99,
    pageSize: 1,
  });

  assert.equal(view.totalItems, 1);
  assert.equal(view.page, 1);
  assert.deepEqual(view.items.map((item) => item.id), ['a']);

  const installedBuiltin = buildCatalogViewModel([
    { id: 'builtin', displayName: '官方', description: '', category: '', source: 'builtin', installed: true, tags: [], avatarUrl: null },
  ], { scope: 'mine', category: '', query: '', page: 1, pageSize: 6 });
  assert.deepEqual(installedBuiltin.items.map((item) => item.id), ['builtin']);
});

test('canonical reducer keeps file selection and content status separate from source DTOs', () => {
  const loading = agentManagementReducer(initialAgentManagementState, {
    type: 'file.loading',
    relativePath: 'README.md',
  });
  assert.equal(loading.fileStatus, 'loading');
  assert.equal(loading.selectedFilePath, 'README.md');

  const ready = agentManagementReducer(loading, {
    type: 'file.loaded',
    content: { relativePath: 'README.md', content: '# ready' },
  });
  assert.equal(ready.fileStatus, 'success');
  assert.equal(ready.fileContent.content, '# ready');

  const reset = agentManagementReducer(
    { ...ready, filesStatus: 'success', files: [{ relativePath: 'README.md', kind: 'file', previewable: true }] },
    { type: 'detail.loading' },
  );
  assert.equal(reset.detailStatus, 'loading');
  assert.equal(reset.filesStatus, 'idle');
  assert.equal(reset.selectedFilePath, null);
});
