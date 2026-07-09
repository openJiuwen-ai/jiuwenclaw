// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useEffect, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useA2UIActions } from '@a2ui/react';
import {
  extractA2UISurfaceIds,
  namespaceA2UIMessages,
  parseA2UIContent,
  type A2UIContentPart,
} from './a2uiContent';
import { recordA2UIActionDefaults } from './actionDefaults';
import { isA2UIFeatureEnabled } from './featureConfig';
import { getA2UIRenderer } from './rendererRegistry';

interface A2UIMessageContentProps {
  content: string;
  messageId: string;
  isStreaming?: boolean;
  testId?: string;
}

type RenderPart =
  | { kind: 'text'; text: string; key: string }
  | {
      kind: 'a2ui';
      key: string;
      protocolVersion: string;
      messages: Extract<A2UIContentPart, { kind: 'a2ui' }>['messages'];
      surfaceIds: string[];
    };

function safeNamespace(input: string): string {
  return input.replace(/[^A-Za-z0-9_-]/g, '_');
}

function MarkdownPart({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children, ...props }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

export function A2UIMessageContent({
  content,
  messageId,
  isStreaming = false,
  testId,
}: A2UIMessageContentProps) {
  const { processMessages } = useA2UIActions();
  const namespace = useMemo(() => `msg_${safeNamespace(messageId)}`, [messageId]);
  const a2uiEnabled = isA2UIFeatureEnabled();

  const renderParts = useMemo<RenderPart[]>(() => {
    return parseA2UIContent(content, {
      enabled: a2uiEnabled,
      isStreaming,
    }).map((part, index) => {
      if (part.kind === 'text') {
        return {
          kind: 'text',
          text: part.text,
          key: `text-${index}`,
        };
      }

      const messages = namespaceA2UIMessages(part.messages, namespace);
      return {
        kind: 'a2ui',
        key: `a2ui-${index}`,
        protocolVersion: part.protocolVersion,
        messages,
        surfaceIds: extractA2UISurfaceIds(messages),
      };
    });
  }, [a2uiEnabled, content, isStreaming, namespace]);

  useEffect(() => {
    for (const part of renderParts) {
      if (a2uiEnabled && part.kind === 'a2ui') {
        recordA2UIActionDefaults(part.messages);
        processMessages(part.messages);
      }
    }
  }, [a2uiEnabled, processMessages, renderParts]);

  return (
    <div className="chat-text a2ui-message-content" data-testid={testId}>
      {renderParts.map((part) => {
        if (part.kind === 'text') {
          return <MarkdownPart key={part.key} text={part.text} />;
        }

        const Renderer = getA2UIRenderer(part.protocolVersion);
        if (!Renderer) {
          return (
            <div key={part.key} className="text-sm text-danger">
              Unsupported A2UI protocol version: {part.protocolVersion}
            </div>
          );
        }

        return (
          <div key={part.key} className="a2ui-message-content__surfaces">
            {part.surfaceIds.map((surfaceId) => (
              <Renderer key={surfaceId} surfaceId={surfaceId} />
            ))}
          </div>
        );
      })}
      {isStreaming && <span className="streaming-cursor" />}
    </div>
  );
}
