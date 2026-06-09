// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { A2UIClientEventMessage } from '@a2ui/react';
import { isA2UIFeatureEnabled } from './featureConfig';
import { A2UI_PROTOCOL_VERSION } from './a2uiContent';
import { enrichA2UIClientEventWithDefaults } from './actionDefaults';

export interface A2UIClientEventContent {
  type: 'a2ui.client_event';
  protocolVersion: typeof A2UI_PROTOCOL_VERSION;
  event: A2UIClientEventMessage;
}

type A2UIActionHandler = (
  message: A2UIClientEventMessage
) => void | Promise<void>;

let currentHandler: A2UIActionHandler | null = null;

export function buildA2UIClientEventContent(
  message: A2UIClientEventMessage
): A2UIClientEventContent {
  const enrichedMessage = enrichA2UIClientEventWithDefaults(message);
  return {
    type: 'a2ui.client_event',
    protocolVersion: A2UI_PROTOCOL_VERSION,
    event: enrichedMessage,
  };
}

export function setA2UIActionHandler(
  handler: A2UIActionHandler | null
): () => void {
  currentHandler = handler;
  return () => {
    if (currentHandler === handler) {
      currentHandler = null;
    }
  };
}

export async function dispatchA2UIAction(
  message: A2UIClientEventMessage
): Promise<void> {
  if (!isA2UIFeatureEnabled()) {
    return;
  }
  if (!currentHandler) {
    console.warn('[A2UI] action ignored because no chat sender is registered');
    return;
  }
  await currentHandler(message);
}
