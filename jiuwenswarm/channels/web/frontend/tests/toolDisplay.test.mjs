import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatToolDisplayLabel,
  formatToolResultDisplayLabel,
  getToolDisplayInfo,
  getToolGroupDisplayState,
} from '../node_modules/.cache/tool-display/features/tool-events/toolDisplay.js';

const toolCall = (name, args = {}, overrides = {}) => ({
  name,
  arguments: args,
  ...overrides,
});

test('describes an edit_file call as editing its shortened file path', () => {
  assert.deepEqual(
    getToolDisplayInfo(
      toolCall('edit_file', {
        path: '/Users/example/project/src/components/App.tsx',
      })
    ),
    {
      action: 'editFile',
      target: 'src/components/App.tsx',
      rawName: 'edit_file',
      displayName: 'Edit File',
    }
  );
});

test('recognizes namespaced tool names and alternate file path arguments', () => {
  assert.deepEqual(
    getToolDisplayInfo(
      toolCall('functions.read_file', {
        file_path: 'C:\\project\\src\\config.ts',
      })
    ),
    {
      action: 'readFile',
      target: 'project/src/config.ts',
      rawName: 'functions.read_file',
      displayName: 'Read File',
    }
  );
});

test('summarizes commands and searches with their useful argument', () => {
  assert.deepEqual(getToolDisplayInfo(toolCall('mcp_exec_command', { cmd: 'npm run test:unit' })), {
    action: 'runCommand',
    target: 'npm run test:unit',
    rawName: 'mcp_exec_command',
    displayName: 'Mcp Exec Command',
  });
  assert.deepEqual(getToolDisplayInfo(toolCall('grep', { pattern: 'toolCall.name', path: 'src' })), {
    action: 'searchContent',
    target: 'toolCall.name',
    rawName: 'grep',
    displayName: 'Grep',
  });
});

test('classifies the remaining supported file and web operations', () => {
  assert.equal(getToolDisplayInfo(toolCall('write_file', { file: 'docs/README.md' })).action, 'writeFile');
  assert.deepEqual(getToolDisplayInfo(toolCall('list_dir', { dir_path: '/project/src/features' })), {
    action: 'listDirectory',
    target: 'project/src/features',
    rawName: 'list_dir',
    displayName: 'List Dir',
  });
  assert.equal(getToolDisplayInfo(toolCall('glob_file_search', { pattern: '*.tsx' })).action, 'findFiles');
  assert.equal(getToolDisplayInfo(toolCall('fetch_webpage', { url: 'https://example.com/docs' })).action, 'fetchWebpage');
  assert.equal(getToolDisplayInfo(toolCall('memory_search', { query: '工具展示' })).action, 'searchMemory');
  assert.equal(getToolDisplayInfo(toolCall('mcp_free_search', { q: 'JiuwenSwarm' })).action, 'searchWeb');
  assert.equal(getToolDisplayInfo(toolCall('skill_tool', { skill_name: 'code-review' })).action, 'useSkill');
});

test('recognizes double-underscore MCP namespaces', () => {
  assert.equal(
    getToolDisplayInfo(
      toolCall('mcp__filesystem__edit_file', {
        path: 'src/App.tsx',
      })
    ).action,
    'editFile'
  );
});

test('uses the session description instead of its technical tool name', () => {
  assert.deepEqual(getToolDisplayInfo(toolCall('session', { description: '检查前端代码' })), {
    action: 'runSession',
    target: '检查前端代码',
    rawName: 'session',
    displayName: 'Session',
  });
});

test('falls back to a readable name for unknown tools without exposing snake case', () => {
  assert.deepEqual(getToolDisplayInfo(toolCall('create_release_note')), {
    action: 'generic',
    rawName: 'create_release_note',
    displayName: 'Create Release Note',
  });
  assert.equal(getToolDisplayInfo(toolCall('custom_action', {}, { description: '生成发布说明' })).displayName, '生成发布说明');
});

test('formats labels through the caller translation function', () => {
  const translations = {
    'chatUi.toolActions.editFile': '编辑文件 {{target}}',
    'chatUi.toolActions.generic': '执行 {{name}}',
    'chatUi.toolResult.successLabel': '{{tool}}成功',
    'chatUi.toolResult.failureLabel': '{{tool}}失败',
  };
  const translate = (key, params = {}) => translations[key]
    .replace('{{target}}', params.target || '')
    .replace('{{name}}', params.name || '')
    .replace('{{tool}}', params.tool || '');

  assert.equal(formatToolDisplayLabel(toolCall('edit_file', { path: 'src/App.tsx' }), translate), '编辑文件 src/App.tsx');
  assert.equal(formatToolDisplayLabel(toolCall('edit_file'), translate), '编辑文件');
  assert.equal(formatToolDisplayLabel(toolCall('create_release_note'), translate), '执行 Create Release Note');
  assert.equal(
    formatToolResultDisplayLabel(toolCall('edit_file'), true, '已更新 3 行', translate),
    '编辑文件成功 · 已更新 3 行'
  );
  assert.equal(
    formatToolResultDisplayLabel(toolCall('edit_file'), false, '权限不足', translate),
    '编辑文件失败 · 权限不足'
  );
});

test('reports running, failed, timed out, and completed tool groups', () => {
  assert.deepEqual(getToolGroupDisplayState([{ status: 'completed', result: { success: false, result: 'failed' } }]), {
    kind: 'completedWithFailures',
    total: 1,
    failedCount: 1,
    timeoutCount: 0,
  });
  assert.deepEqual(getToolGroupDisplayState([{ status: 'timeout' }]), { kind: 'completedWithTimeouts', total: 1, failedCount: 0, timeoutCount: 1 });
  assert.deepEqual(getToolGroupDisplayState([{ status: 'completed', result: { success: true, result: 'done' } }, { status: 'pending' }]), {
    kind: 'running',
    total: 2,
    failedCount: 0,
    timeoutCount: 0,
  });
  assert.deepEqual(getToolGroupDisplayState([{ status: 'completed', result: { success: false, result: 'failed' } }, { status: 'timeout' }]), {
    kind: 'completedWithIssues',
    total: 2,
    failedCount: 1,
    timeoutCount: 1,
  });
  assert.deepEqual(getToolGroupDisplayState([{ status: 'completed', result: { success: true, result: 'done' } }]), {
    kind: 'completed',
    total: 1,
    failedCount: 0,
    timeoutCount: 0,
  });
});
