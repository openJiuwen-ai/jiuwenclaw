// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { A2UIClientEventMessage, ServerToClientMessage } from '@a2ui/react';

type UnknownRecord = Record<string, unknown>;

interface ActionDefaultEntry {
  surfaceId: string;
  sourceComponentId: string;
  actionName: string;
  defaults: Record<string, unknown>;
}

const actionDefaults = new Map<string, ActionDefaultEntry>();
const surfaceDefaultsByPath = new Map<string, Map<string, unknown>>();

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function componentType(component: unknown): string | null {
  if (!isRecord(component)) {
    return null;
  }
  return Object.keys(component)[0] ?? null;
}

function componentProps(component: unknown): UnknownRecord | null {
  if (!isRecord(component)) {
    return null;
  }
  const type = componentType(component);
  if (!type || !isRecord(component[type])) {
    return null;
  }
  return component[type];
}

function normalizePath(path: unknown): string | null {
  if (typeof path !== 'string' || !path.trim()) {
    return null;
  }
  const trimmed = path.trim();
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}

function visibleChoiceDefault(props: UnknownRecord): unknown {
  const options = Array.isArray(props.options) ? props.options : [];
  const firstOption = options.find(isRecord);
  if (
    firstOption &&
    Object.prototype.hasOwnProperty.call(firstOption, 'value') &&
    firstOption.value !== null &&
    firstOption.value !== undefined
  ) {
    return firstOption.value;
  }

  const selections = isRecord(props.selections) ? props.selections : null;
  const literalArray = Array.isArray(selections?.literalArray)
    ? selections.literalArray
    : [];
  const firstLiteral = literalArray.find((value) => value !== null && value !== undefined);
  return firstLiteral ?? null;
}

function actionName(action: UnknownRecord): string | null {
  return typeof action.name === 'string'
    ? action.name
    : typeof action.actionName === 'string'
      ? action.actionName
      : null;
}

function actionKey(surfaceId: string, sourceComponentId: string, name: string): string {
  return `${surfaceId}\u0000${sourceComponentId}\u0000${name}`;
}

function isEmptyActionValue(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    (Array.isArray(value) && value.length === 0) ||
    (isRecord(value) && Object.keys(value).length === 0)
  );
}

export function clearA2UIActionDefaults(): void {
  actionDefaults.clear();
  surfaceDefaultsByPath.clear();
}

export function recordA2UIActionDefaults(messages: ServerToClientMessage[]): void {
  for (const message of messages) {
    if (message.deleteSurface?.surfaceId) {
      const surfaceId = message.deleteSurface.surfaceId;
      surfaceDefaultsByPath.delete(surfaceId);
      for (const [key, entry] of actionDefaults.entries()) {
        if (entry.surfaceId === surfaceId) {
          actionDefaults.delete(key);
        }
      }
      continue;
    }

    const update = message.surfaceUpdate;
    if (!update?.surfaceId || !Array.isArray(update.components)) {
      continue;
    }

    const defaultsByPath = surfaceDefaultsByPath.get(update.surfaceId) ?? new Map<string, unknown>();
    for (const instance of update.components) {
      const props = componentProps(instance.component);
      if (!props) {
        continue;
      }
      const selections = isRecord(props.selections) ? props.selections : null;
      const selectionsPath = normalizePath(selections?.path);
      const defaultValue = visibleChoiceDefault(props);
      if (selectionsPath && defaultValue !== null) {
        defaultsByPath.set(selectionsPath, [defaultValue]);
      }
    }
    if (defaultsByPath.size > 0) {
      surfaceDefaultsByPath.set(update.surfaceId, defaultsByPath);
    }

    for (const instance of update.components) {
      const props = componentProps(instance.component);
      if (!props || componentType(instance.component) !== 'Button' || !isRecord(props.action)) {
        continue;
      }

      const name = actionName(props.action);
      if (!name || !Array.isArray(props.action.context)) {
        continue;
      }

      const defaults: Record<string, unknown> = {};
      for (const contextItem of props.action.context) {
        if (!isRecord(contextItem) || typeof contextItem.key !== 'string' || !isRecord(contextItem.value)) {
          continue;
        }
        const path = normalizePath(contextItem.value.path);
        if (path && defaultsByPath.has(path)) {
          defaults[contextItem.key] = defaultsByPath.get(path);
        }
      }

      if (Object.keys(defaults).length > 0) {
        actionDefaults.set(actionKey(update.surfaceId, instance.id, name), {
          surfaceId: update.surfaceId,
          sourceComponentId: instance.id,
          actionName: name,
          defaults,
        });
      }
    }
  }
}

export function enrichA2UIClientEventWithDefaults(
  message: A2UIClientEventMessage
): A2UIClientEventMessage {
  const userAction = message.userAction;
  if (!userAction) {
    return message;
  }

  const name = actionName(userAction as unknown as UnknownRecord);
  const sourceComponentId = userAction.sourceComponentId;
  const surfaceId = userAction.surfaceId;
  if (!name || !sourceComponentId || !surfaceId) {
    return message;
  }

  const entry = actionDefaults.get(actionKey(surfaceId, sourceComponentId, name));
  if (!entry) {
    return message;
  }

  const currentContext = isRecord(userAction.context) ? userAction.context : {};
  const nextContext: Record<string, unknown> = { ...currentContext };
  let changed = false;
  for (const [key, value] of Object.entries(entry.defaults)) {
    if (isEmptyActionValue(nextContext[key])) {
      nextContext[key] = value;
      changed = true;
    }
  }

  if (!changed) {
    return message;
  }

  return {
    ...message,
    userAction: {
      ...userAction,
      context: nextContext,
    },
  };
}
