// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** Stable host boundary for the single-Agent chat and trajectory surfaces. */

import type { ReactNode } from 'react';

export type ChatSurfaceView = 'chat' | 'trajectory';

export interface SingleAgentSurfaceProps {
  activeView: ChatSurfaceView;
  chat: ReactNode;
  chatLabel: string;
  mode: string;
  onViewChange: (view: ChatSurfaceView) => void;
  tabListLabel: string;
  trajectory: ReactNode;
  trajectoryLabel: string;
  trajectoryControls?: ReactNode;
  trajectoryRequested: boolean;
}

/**
 * Keep the chat subtree mounted while gating the trajectory subtree until its
 * first explicit request. Non-agent modes never expose or mount trajectory UI.
 */
export function SingleAgentSurface({
  activeView,
  chat,
  chatLabel,
  mode,
  onViewChange,
  tabListLabel,
  trajectory,
  trajectoryControls,
  trajectoryLabel,
  trajectoryRequested,
}: SingleAgentSurfaceProps) {
  const agentMode = mode === 'agent';
  const resolvedView: ChatSurfaceView = agentMode ? activeView : 'chat';

  return (
    <>
      {agentMode ? (
        <div className="chat-surface-toolbar">
          <div
            className="chat-surface-tabs"
            role="tablist"
            aria-label={tabListLabel}
            data-testid="single-agent-surface-tabs"
          >
            <button
              type="button"
              role="tab"
              aria-selected={resolvedView === 'chat'}
              className={`chat-surface-tabs__tab ${resolvedView === 'chat' ? 'is-active' : ''}`}
              onClick={() => onViewChange('chat')}
              data-testid="single-agent-chat-tab"
            >
              {chatLabel}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={resolvedView === 'trajectory'}
              className={`chat-surface-tabs__tab ${resolvedView === 'trajectory' ? 'is-active' : ''}`}
              onClick={() => onViewChange('trajectory')}
              data-testid="single-agent-trajectory-tab"
            >
              {trajectoryLabel}
            </button>
          </div>
          {resolvedView === 'trajectory' && trajectoryControls !== undefined ? (
            <div className="chat-surface-layout-controls" data-testid="trajectory-layout-controls">
              {trajectoryControls}
            </div>
          ) : null}
        </div>
      ) : null}
      <div
        className={`chat-surface-view flex-1 min-h-0 ${resolvedView === 'chat' ? '' : 'chat-surface-view--hidden'}`}
        aria-hidden={resolvedView !== 'chat'}
        data-testid="single-agent-chat-view"
      >
        {chat}
      </div>
      {agentMode && trajectoryRequested ? (
        <div
          className={`chat-surface-view flex-1 min-h-0 ${resolvedView === 'trajectory' ? '' : 'chat-surface-view--hidden'}`}
          aria-hidden={resolvedView !== 'trajectory'}
          data-testid="single-agent-trajectory-view"
        >
          {trajectory}
        </div>
      ) : null}
    </>
  );
}
