// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { ComponentType } from 'react';
import { A2UIRenderer, ComponentRegistry, registerDefaultCatalog } from '@a2ui/react';
import { A2UI_PROTOCOL_VERSION, type A2UIProtocolVersion } from './a2uiContent';
import { MultipleChoiceWithDefaults } from './MultipleChoiceWithDefaults';

export interface A2UIRendererProps {
  surfaceId: string;
}

const a2uiV08Registry = new ComponentRegistry();
registerDefaultCatalog(a2uiV08Registry);
a2uiV08Registry.register('MultipleChoice', {
  component: MultipleChoiceWithDefaults,
});

const A2UIV08Renderer = ({ surfaceId }: A2UIRendererProps) => (
  <A2UIRenderer surfaceId={surfaceId} registry={a2uiV08Registry} />
);

export const rendererByVersion: Record<
  A2UIProtocolVersion,
  ComponentType<A2UIRendererProps>
> = {
  [A2UI_PROTOCOL_VERSION]: A2UIV08Renderer,
};

export function getA2UIRenderer(
  version: string
): ComponentType<A2UIRendererProps> | null {
  return rendererByVersion[version as A2UIProtocolVersion] ?? null;
}
