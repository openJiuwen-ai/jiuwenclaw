// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import React, { Suspense, act, lazy, useEffect, useState } from 'react';
import { JSDOM } from 'jsdom';
import { createRoot } from 'react-dom/client';

import { SingleAgentSurface } from '../node_modules/.cache/trajectory-host/SingleAgentSurface.mjs';

test('trajectory host relies on native sidebar controls', () => {
  const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const panelSource = readFileSync(
    new URL('../src/features/trajectory/TrajectoryPanel.tsx', import.meta.url),
    'utf8',
  );
  const explorerSource = readFileSync(
    new URL('../src/features/trajectory/client/TrajectoryExplorer.tsx', import.meta.url),
    'utf8',
  );
  const tableSource = readFileSync(
    new URL('../src/features/trajectory/client/TrajectoryTable.tsx', import.meta.url),
    'utf8',
  );
  const chatPanelSource = readFileSync(
    new URL('../src/components/ChatPanel/index.tsx', import.meta.url),
    'utf8',
  );
  const appStyles = readFileSync(new URL('../src/App.css', import.meta.url), 'utf8');

  assert.doesNotMatch(appSource, /data-testid="trajectory-toggle-sessions"/);
  assert.doesNotMatch(appSource, /data-testid="trajectory-toggle-tasks"/);
  assert.match(appSource, /chat-workspace--trajectory-floating-tools/);
  assert.match(appSource, /shouldInsetTrajectoryForFloatingTasks/);
  assert.doesNotMatch(appStyles, /transition:\s*padding-right/);
  assert.match(appSource, /showNavigation=\{sessionId !== NEW_CONVERSATION_ID\}/);
  assert.doesNotMatch(panelSource, /window\.setInterval/);
  assert.doesNotMatch(panelSource, /data-testid="trajectory-refresh"/);
  assert.match(panelSource, /data-testid="trajectory-archive-import"/);
  assert.match(panelSource, /data-testid="trajectory-archive-export"/);
  assert.doesNotMatch(panelSource, /preferBrowserFilePicker/);
  assert.match(panelSource, /data-testid="trajectory-archive-exit"/);
  assert.doesNotMatch(panelSource, /if \(!active \|\| sessionId === 'new'/);
  assert.match(panelSource, /'harness\.session_finished'/);
  assert.match(panelSource, /subjectGroups\.groups\.map/);
  assert.match(panelSource, /key=\{group\.subject\.id\}/);
  assert.match(panelSource, /data-trajectory-subject-explorer/);
  assert.match(panelSource, /createTrajectorySubjectViewCache/);
  assert.match(panelSource, /active=\{active && selected\}/);
  assert.match(panelSource, /deferredPublishRef\.current = true/);
  assert.match(panelSource, /setPublishedWindow\(\{/);
  assert.match(panelSource, /page\.items\.reduce\(\(count, summary\) => count \+ summary\.span_count, 0\)/);
  assert.match(panelSource, /role="progressbar"/);
  assert.match(panelSource, /initialLoadProgress\.loaded \/ initialLoadProgress\.total/);
  assert.match(panelSource, /error\.code === 'UNSUPPORTED_SESSION_MODE'/);
  assert.match(panelSource, /if \(staged === null[\s\S]*setError\(null\);/);
  assert.doesNotMatch(panelSource, /setRecords\(|setRawRecords\(|setLifecycleByRecordId\(/);
  assert.match(panelSource, /export const TrajectoryPanel = memo/);
  assert.match(explorerSource, /export const TrajectoryExplorer = memo/);
  assert.match(chatPanelSource, /export const ChatPanel = React\.memo/);
  assert.match(
    appStyles,
    /\.single-agent-surface--trajectory \.chat-panel-shell > :not\(\.chat-panel-header\) \{[\s\S]*?visibility: hidden;/,
  );
  assert.match(explorerSource, /if \(!active \|\| !hasRunningCells\) return undefined/);
  assert.match(tableSource, /\{ id: 'raw', label: 'Raw Output' \}/);
  assert.match(tableSource, /\{ id: 'facts', label: 'Facts' \}/);
  assert.match(tableSource, /data=\{selected\.cell\.compactionDetail\}/);
  assert.match(tableSource, /record\.cell\.isError[\s\S]*?css\.errorKindTag/);
  assert.match(tableSource, /label="Compaction facts JSON"/);
  assert.match(tableSource, />Session cumulative</);
  assert.doesNotMatch(tableSource, />Window cumulative</);
});

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
});

globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
});
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function click(element) {
  element.dispatchEvent(new dom.window.MouseEvent('click', {
    bubbles: true,
    cancelable: true,
  }));
}

test('single-Agent tabs keep chat mounted and load trajectory only after first request', async () => {
  const counters = {
    chatMounts: 0,
    chatUnmounts: 0,
    trajectoryLoads: 0,
    trajectoryMounts: 0,
    trajectoryUnmounts: 0,
  };
  const LazyTrajectoryProbe = lazy(async () => {
    counters.trajectoryLoads += 1;
    return {
      default: function TrajectoryProbe() {
        useEffect(() => {
          counters.trajectoryMounts += 1;
          return () => {
            counters.trajectoryUnmounts += 1;
          };
        }, []);
        return React.createElement('div', { 'data-testid': 'trajectory-probe' }, 'trace');
      },
    };
  });

  function ChatProbe() {
    useEffect(() => {
      counters.chatMounts += 1;
      return () => {
        counters.chatUnmounts += 1;
      };
    }, []);
    return React.createElement('input', { 'data-testid': 'chat-probe' });
  }

  function Harness() {
    const [activeView, setActiveView] = useState('chat');
    const [trajectoryRequested, setTrajectoryRequested] = useState(false);
    const onViewChange = (nextView) => {
      if (nextView === 'trajectory') {
        setTrajectoryRequested(true);
      }
      setActiveView(nextView);
    };
    return React.createElement(SingleAgentSurface, {
      activeView,
      chat: React.createElement(ChatProbe),
      chatLabel: 'Chat',
      mode: 'agent',
      onViewChange,
      tabListLabel: 'Single Agent surface',
      trajectory: React.createElement(
        Suspense,
        { fallback: React.createElement('div', null, 'Loading') },
        React.createElement(LazyTrajectoryProbe),
      ),
      trajectoryLabel: 'Trajectory',
      trajectoryRequested,
    });
  }

  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(Harness));
  });

  const originalChatInput = container.querySelector('[data-testid="chat-probe"]');
  assert.ok(originalChatInput);
  originalChatInput.value = 'draft stays mounted';
  assert.equal(container.querySelectorAll('[role="tab"]').length, 2);
  assert.equal(counters.chatMounts, 1);
  assert.equal(counters.trajectoryLoads, 0);
  assert.equal(container.querySelector('[data-testid="single-agent-trajectory-view"]'), null);

  await act(async () => {
    click(container.querySelector('[data-testid="single-agent-trajectory-tab"]'));
    await Promise.resolve();
  });

  assert.equal(counters.trajectoryLoads, 1);
  assert.equal(counters.trajectoryMounts, 1);
  assert.equal(counters.chatMounts, 1);
  assert.equal(counters.chatUnmounts, 0);
  assert.equal(container.querySelector('[data-testid="chat-probe"]'), originalChatInput);
  assert.equal(originalChatInput.value, 'draft stays mounted');
  assert.equal(
    container.querySelector('[data-testid="single-agent-chat-view"]').getAttribute('aria-hidden'),
    'true',
  );

  await act(async () => {
    click(container.querySelector('[data-testid="single-agent-chat-tab"]'));
  });

  assert.equal(container.querySelector('[data-testid="trajectory-probe"]').textContent, 'trace');
  assert.equal(counters.trajectoryMounts, 1);
  assert.equal(counters.trajectoryUnmounts, 0);
  assert.equal(
    container.querySelector('[data-testid="single-agent-trajectory-view"]').getAttribute('aria-hidden'),
    'true',
  );

  await act(async () => {
    root.unmount();
  });
  container.remove();
  assert.equal(counters.chatUnmounts, 1);
  assert.equal(counters.trajectoryUnmounts, 1);
});

test('single-Agent welcome surface hides navigation until a conversation exists', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(React.createElement(SingleAgentSurface, {
      activeView: 'chat',
      chat: React.createElement('div', { 'data-testid': 'welcome-probe' }, 'welcome'),
      chatLabel: 'Chat',
      mode: 'agent',
      onViewChange: () => {},
      showNavigation: false,
      tabListLabel: 'Single Agent surface',
      trajectory: React.createElement('div', null, 'trace'),
      trajectoryLabel: 'Trajectory',
      trajectoryRequested: false,
    }));
  });

  assert.ok(container.querySelector('[data-testid="welcome-probe"]'));
  assert.equal(container.querySelector('[data-testid="single-agent-surface-tabs"]'), null);

  await act(async () => {
    root.unmount();
  });
  container.remove();
});

test('non-Agent modes never expose tabs or mount trajectory UI', async () => {
  let trajectoryMounts = 0;
  function TrajectoryProbe() {
    trajectoryMounts += 1;
    return React.createElement('div', null, 'trace');
  }

  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(SingleAgentSurface, {
      activeView: 'trajectory',
      chat: React.createElement('div', { 'data-testid': 'chat-probe' }, 'chat'),
      chatLabel: 'Chat',
      mode: 'team',
      onViewChange: () => {},
      tabListLabel: 'Single Agent surface',
      trajectory: React.createElement(TrajectoryProbe),
      trajectoryLabel: 'Trajectory',
      trajectoryRequested: true,
    }));
  });

  assert.equal(container.querySelector('[data-testid="single-agent-surface-tabs"]'), null);
  assert.equal(container.querySelector('[data-testid="single-agent-trajectory-view"]'), null);
  assert.equal(
    container.querySelector('[data-testid="single-agent-chat-view"]').getAttribute('aria-hidden'),
    'false',
  );
  assert.equal(trajectoryMounts, 0);

  await act(async () => {
    root.unmount();
  });
  container.remove();
});
