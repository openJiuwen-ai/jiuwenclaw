// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { ServerToClientMessage } from '@a2ui/react';

export const A2UI_PROTOCOL_VERSION = '0.8';
export const A2UI_OPEN_TAG = '<a2ui-json>';
export const A2UI_CLOSE_TAG = '</a2ui-json>';

const A2UI_MESSAGE_KEYS = [
  'beginRendering',
  'surfaceUpdate',
  'dataModelUpdate',
  'deleteSurface',
] as const;

export type A2UIProtocolVersion = typeof A2UI_PROTOCOL_VERSION;

export type A2UIContentPart =
  | { kind: 'text'; text: string }
  | {
      kind: 'a2ui';
      protocolVersion: A2UIProtocolVersion;
      messages: ServerToClientMessage[];
      surfaceIds: string[];
    };

interface ParseA2UIContentOptions {
  enabled?: boolean;
  isStreaming?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isA2UIMessage(value: unknown): value is ServerToClientMessage {
  if (!isRecord(value)) {
    return false;
  }
  return A2UI_MESSAGE_KEYS.filter((key) => key in value).length === 1;
}

function coerceA2UIMessageList(value: unknown): ServerToClientMessage[] | null {
  if (Array.isArray(value) && value.every(isA2UIMessage)) {
    return value;
  }
  if (isA2UIMessage(value)) {
    return [value];
  }
  return null;
}

function parseJsonMessageList(text: string): ServerToClientMessage[] | null {
  const trimmed = text.trim();
  if (!trimmed || (!trimmed.startsWith('[') && !trimmed.startsWith('{'))) {
    return null;
  }
  try {
    return coerceA2UIMessageList(JSON.parse(trimmed));
  } catch {
    return null;
  }
}

function parseJsonlMessages(text: string): ServerToClientMessage[] | null {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length || !lines.every((line) => line.startsWith('{') && line.endsWith('}'))) {
    return null;
  }

  const messages: ServerToClientMessage[] = [];
  try {
    for (const line of lines) {
      const parsed = JSON.parse(line);
      if (!isA2UIMessage(parsed)) {
        return null;
      }
      messages.push(parsed);
    }
  } catch {
    return null;
  }
  return messages;
}

function parseA2UIMessageText(text: string): ServerToClientMessage[] | null {
  return parseJsonlMessages(text) ?? parseJsonMessageList(text);
}

interface FencedA2UIBlock {
  start: number;
  end: number;
  messages: ServerToClientMessage[];
}

function stripLeadingOrphanedA2UIFenceTail(text: string): string {
  return text.replace(/^\s*(?:[}\]]\s*)+```[ \t]*/, '');
}

function findNextFencedA2UIBlock(content: string, cursor: number): FencedA2UIBlock | null {
  const fencePattern = /```(?:json|a2ui|a2ui-json)?[ \t]*\r?\n([\s\S]*?)\r?\n```/gi;
  fencePattern.lastIndex = cursor;

  let match: RegExpExecArray | null;
  while ((match = fencePattern.exec(content)) !== null) {
    const messages = parseA2UIMessageText(match[1]);
    if (messages) {
      return {
        start: match.index,
        end: match.index + match[0].length,
        messages,
      };
    }
  }
  return null;
}

function getMessageSurfaceId(message: ServerToClientMessage): string | null {
  if (message.beginRendering?.surfaceId) {
    return message.beginRendering.surfaceId;
  }
  if (message.surfaceUpdate?.surfaceId) {
    return message.surfaceUpdate.surfaceId;
  }
  if (message.dataModelUpdate?.surfaceId) {
    return message.dataModelUpdate.surfaceId;
  }
  if (message.deleteSurface?.surfaceId) {
    return message.deleteSurface.surfaceId;
  }
  return null;
}

export function extractA2UISurfaceIds(messages: ServerToClientMessage[]): string[] {
  const surfaceIds = new Set<string>();
  for (const message of messages) {
    const surfaceId = getMessageSurfaceId(message);
    if (surfaceId) {
      surfaceIds.add(surfaceId);
    }
  }
  return Array.from(surfaceIds);
}

function makeA2UIPart(messages: ServerToClientMessage[]): A2UIContentPart {
  return {
    kind: 'a2ui',
    protocolVersion: A2UI_PROTOCOL_VERSION,
    messages,
    surfaceIds: extractA2UISurfaceIds(messages),
  };
}

function invalidA2UIFallback(): A2UIContentPart {
  return {
    kind: 'text',
    text: '[A2UI content could not be rendered]',
  };
}

function pendingA2UIFallback(): A2UIContentPart {
  return {
    kind: 'text',
    text: 'A2UI 界面生成中...',
  };
}

export function parseA2UIContent(
  content: string,
  options: ParseA2UIContentOptions = {}
): A2UIContentPart[] {
  if (!content) {
    return [];
  }
  if (options.enabled === false) {
    return [{ kind: 'text', text: content }];
  }

  const jsonlMessages = parseJsonlMessages(content);
  if (jsonlMessages) {
    return [makeA2UIPart(jsonlMessages)];
  }

  const rawJsonMessages = parseJsonMessageList(content);
  if (rawJsonMessages) {
    return [makeA2UIPart(rawJsonMessages)];
  }

  const parts: A2UIContentPart[] = [];
  let cursor = 0;
  let sawA2UIBlock = false;

  while (cursor < content.length) {
    const openIndex = content.indexOf(A2UI_OPEN_TAG, cursor);
    const fencedBlock = findNextFencedA2UIBlock(content, cursor);
    const fencedIndex = fencedBlock?.start ?? -1;

    if (openIndex < 0 && fencedIndex < 0) {
      const tail = content.slice(cursor);
      if (tail) {
        parts.push({ kind: 'text', text: tail });
      }
      break;
    }

    const useFence = fencedBlock !== null && (openIndex < 0 || fencedBlock.start < openIndex);
    const blockStart = useFence ? fencedBlock.start : openIndex;

    sawA2UIBlock = true;
    if (blockStart > cursor) {
      const textBeforeBlock = stripLeadingOrphanedA2UIFenceTail(
        content.slice(cursor, blockStart)
      );
      if (textBeforeBlock.trim()) {
        parts.push({ kind: 'text', text: textBeforeBlock });
      }
    }

    if (useFence) {
      parts.push(makeA2UIPart(fencedBlock.messages));
      cursor = fencedBlock.end;
      continue;
    }

    const bodyStart = openIndex + A2UI_OPEN_TAG.length;
    const closeIndex = content.indexOf(A2UI_CLOSE_TAG, bodyStart);
    if (closeIndex < 0) {
      parts.push(options.isStreaming ? pendingA2UIFallback() : invalidA2UIFallback());
      break;
    }

    const body = content.slice(bodyStart, closeIndex);
    const messages = parseJsonMessageList(body);
    parts.push(messages ? makeA2UIPart(messages) : invalidA2UIFallback());
    cursor = closeIndex + A2UI_CLOSE_TAG.length;
  }

  return sawA2UIBlock ? parts.filter((part) => part.kind !== 'text' || part.text) : [{ kind: 'text', text: content }];
}

export function namespaceA2UIMessages(
  messages: ServerToClientMessage[],
  namespace: string
): ServerToClientMessage[] {
  return messages.map((message) => {
    const cloned = structuredClone(message) as ServerToClientMessage;
    if (cloned.beginRendering?.surfaceId) {
      cloned.beginRendering.surfaceId = `${namespace}:${cloned.beginRendering.surfaceId}`;
    }
    if (cloned.surfaceUpdate?.surfaceId) {
      cloned.surfaceUpdate.surfaceId = `${namespace}:${cloned.surfaceUpdate.surfaceId}`;
    }
    if (cloned.dataModelUpdate?.surfaceId) {
      cloned.dataModelUpdate.surfaceId = `${namespace}:${cloned.dataModelUpdate.surfaceId}`;
    }
    if (cloned.deleteSurface?.surfaceId) {
      cloned.deleteSurface.surfaceId = `${namespace}:${cloned.deleteSurface.surfaceId}`;
    }
    return cloned;
  });
}

export function a2uiContentToText(content: string): string {
  return parseA2UIContent(content)
    .map((part) => {
      if (part.kind === 'text') {
        return part.text.trim();
      }
      return '[A2UI interactive content]';
    })
    .filter(Boolean)
    .join('\n\n');
}
