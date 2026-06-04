/**
 * MessageList 组件
 *
 * 消息列表显示：将普通消息与工具执行按时间线交错渲染。
 */

import { useMemo } from 'react';
import { Message, ToolExecution } from '../../types';
import { MessageItem, getMessageActor } from './MessageItem';
import { ToolGroupDisplay } from './ToolGroupDisplay';
import { useChatStore, useSessionStore } from '../../stores';
import { isTeamMemberCollaborationMessage } from './teamEventUtils';

interface MessageListProps {
  messages: Message[];
}

interface ChatTimelineListProps {
  messages: Message[];
  executions?: ToolExecution[];
  mode?: string;
}

type TimelineItem =
  | {
      type: 'message';
      key: string;
      timestampMs: number;
      sourceIndex: number;
      message: Message;
    }
  | {
      type: 'toolExecution';
      key: string;
      timestampMs: number;
      sourceIndex: number;
      execution: ToolExecution;
    };

type RenderItem =
  | {
      type: 'message';
      key: string;
      showAvatar: boolean;
      message: Message;
    }
  | {
      type: 'toolGroup';
      key: string;
      showAvatar: boolean;
      executions: ToolExecution[];
    };

/**
 * 将普通消息与工具执行合并为统一时间线，按时间升序渲染。
 */
function toTimestampMs(value: string | undefined): number {
  if (!value) {
    return Number.NaN;
  }
  const ts = Date.parse(value);
  return Number.isNaN(ts) ? Number.NaN : ts;
}

function compareTimelineItems(a: TimelineItem, b: TimelineItem): number {
  const aTsValid = Number.isFinite(a.timestampMs);
  const bTsValid = Number.isFinite(b.timestampMs);
  if (aTsValid && bTsValid && a.timestampMs !== b.timestampMs) {
    return a.timestampMs - b.timestampMs;
  }
  if (a.type !== b.type) {
    if (a.type === 'toolExecution') return -1;
    if (b.type === 'toolExecution') return 1;
  }
  if (aTsValid !== bTsValid) {
    return aTsValid ? -1 : 1;
  }
  return a.sourceIndex - b.sourceIndex;
}

function buildTimelineItems(
  messages: Message[],
  executions: ToolExecution[]
): TimelineItem[] {
  const messageItems: TimelineItem[] = messages
    .filter((msg) => msg.role !== 'tool')
    .map((message, index) => ({
      type: 'message',
      key: `message-${message.id}-${index}`,
      timestampMs: toTimestampMs(message.timestamp),
      sourceIndex: index,
      message,
    }));

  const executionItems: TimelineItem[] = executions.map((execution, index) => ({
    type: 'toolExecution',
    key: `tool-execution-${execution.toolCallId}`,
    timestampMs: toTimestampMs(execution.startedAt),
    sourceIndex: messages.length + index,
    execution,
  }));

  return [...messageItems, ...executionItems].sort(compareTimelineItems);
}

function isFinalMessage(message: Message): boolean {
  if (message.role === 'assistant' && !message.isStreaming) {
    return true;
  }
  if (message.id.startsWith('team-leader-')) {
    return typeof message.content === 'string' && message.content.startsWith('team.leader:');
  }
  return false;
}

function buildRenderItems(items: TimelineItem[], isTeamMode: boolean): RenderItem[] {
  const renderItems: RenderItem[] = [];
  let currentSegment = {
    toolExecutions: [] as ToolExecution[],
    messages: [] as { key: string; message: Message }[],
  };

  const flushCurrentSegment = () => {
    if (currentSegment.toolExecutions.length > 0) {
      renderItems.push({
        type: 'toolGroup',
        key: `tool-group-${currentSegment.toolExecutions[0].toolCallId}`,
        showAvatar: true,
        executions: currentSegment.toolExecutions,
      });
      currentSegment.toolExecutions = [];
    }
    for (const { key, message } of currentSegment.messages) {
      renderItems.push({
        type: 'message',
        key,
        showAvatar: true,
        message,
      });
    }
    currentSegment.messages = [];
  };

  const flushSegmentIfPresent = () => {
    if (
      currentSegment.toolExecutions.length > 0 ||
      currentSegment.messages.length > 0
    ) {
      flushCurrentSegment();
    }
  };

  for (const item of items) {
    if (item.type === 'toolExecution') {
      currentSegment.toolExecutions.push(item.execution);
      continue;
    }

    if (isTeamMemberCollaborationMessage(item.message)) {
      continue;
    }

    if (item.message.role === 'user') {
      flushSegmentIfPresent();
      renderItems.push({
        type: 'message',
        key: item.key,
        showAvatar: true,
        message: item.message,
      });
      continue;
    }

    if (isFinalMessage(item.message)) {
      flushSegmentIfPresent();
      renderItems.push({
        type: 'message',
        key: item.key,
        showAvatar: true,
        message: item.message,
      });
      continue;
    }

    currentSegment.messages.push({ key: item.key, message: item.message });
  }

  flushSegmentIfPresent();

  if (!isTeamMode) {
    for (const renderItem of renderItems) {
      if (renderItem.type === 'toolGroup') {
        renderItem.showAvatar = false;
      }
    }
    return renderItems;
  }

  let clusterBlockActive = false;
  for (const renderItem of renderItems) {
    if (renderItem.type === 'toolGroup') {
      renderItem.showAvatar = !clusterBlockActive;
      clusterBlockActive = true;
      continue;
    }

    const actor = getMessageActor(renderItem.message);
    if (actor === 'team_leader') {
      renderItem.showAvatar = !clusterBlockActive;
      clusterBlockActive = true;
      continue;
    }

    clusterBlockActive = false;
  }

  return renderItems;
}

export function ChatTimelineList({
  messages,
  executions = [],
  mode = 'default',
}: ChatTimelineListProps) {
  const isTeamMode = mode === 'team';
  const renderItems = useMemo(
    () => buildRenderItems(buildTimelineItems(messages, executions), isTeamMode),
    [messages, executions, isTeamMode]
  );

  if (renderItems.length === 0) {
    return null;
  }

  return (
    <div className="space-y-1">
      {renderItems.map((item) => {
        if (item.type === 'message') {
          return (
            <MessageItem
              key={item.key}
              message={item.message}
              showAvatar={item.showAvatar}
            />
          );
        }
        return (
          <ToolGroupDisplay
            key={item.key}
            executions={item.executions}
            showAvatar={item.showAvatar}
            teamLayout={isTeamMode}
          />
        );
      })}
    </div>
  );
}

export function MessageList({ messages }: MessageListProps) {
  const { toolExecutions, toolExecutionOrder } = useChatStore();
  const { mode } = useSessionStore();
  const executions = useMemo(
    () => toolExecutionOrder
      .map((toolCallId) => toolExecutions.get(toolCallId))
      .filter((item): item is NonNullable<typeof item> => !!item),
    [toolExecutions, toolExecutionOrder]
  );

  return <ChatTimelineList messages={messages} executions={executions} mode={mode} />;
}
