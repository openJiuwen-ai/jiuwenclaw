// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { useCallback, useEffect, useId } from 'react';
import type { CSSProperties, ChangeEvent } from 'react';
import {
  classMapToString,
  stylesToObject,
  useA2UIComponent,
  type A2UIComponentProps,
  type AnyComponentNode,
  type DataValue,
} from '@a2ui/react';

type MultipleChoiceNodeLike = Extract<AnyComponentNode, { type: 'MultipleChoice' }>;

function selectedValueFromData(value: DataValue | null): string | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      if (typeof item === 'string') {
        return item;
      }
    }
    return null;
  }
  return typeof value === 'string' ? value : null;
}

function isMissingSelection(value: DataValue | null): boolean {
  return value === null || value === undefined || (Array.isArray(value) && value.length === 0);
}

export function visibleMultipleChoiceDefault(
  props: MultipleChoiceNodeLike['properties']
): string | null {
  const firstOption = props.options?.find((option) => typeof option.value === 'string');
  if (firstOption) {
    return firstOption.value;
  }
  for (const item of props.selections?.literalArray ?? []) {
    if (typeof item === 'string') {
      return item;
    }
  }
  return null;
}

export function MultipleChoiceWithDefaults({
  node,
  surfaceId,
}: A2UIComponentProps<MultipleChoiceNodeLike>) {
  const { theme, resolveString, setValue, getValue } = useA2UIComponent(node, surfaceId);
  const props = node.properties;
  const id = useId();
  const selectionsPath = props.selections?.path;
  const descriptionValue = (props as MultipleChoiceNodeLike['properties'] & {
    description?: Parameters<typeof resolveString>[0];
  }).description;
  const description = resolveString(descriptionValue) ?? 'Select an item';
  const defaultValue = visibleMultipleChoiceDefault(props);
  const dataValue = selectionsPath ? getValue(selectionsPath) : null;
  const selectedValue = selectedValueFromData(dataValue) ?? defaultValue ?? '';

  useEffect(() => {
    if (selectionsPath && defaultValue !== null && isMissingSelection(getValue(selectionsPath))) {
      setValue(selectionsPath, [defaultValue]);
    }
  }, [defaultValue, getValue, selectionsPath, setValue]);

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLSelectElement>) => {
      if (selectionsPath) {
        setValue(selectionsPath, [event.target.value]);
      }
    },
    [selectionsPath, setValue]
  );

  const hostStyle = (
    node.weight !== undefined ? { '--weight': node.weight } : {}
  ) as CSSProperties;

  return (
    <div className="a2ui-multiplechoice" style={hostStyle}>
      <section className={classMapToString(theme.components.MultipleChoice.container)}>
        <label
          htmlFor={id}
          className={classMapToString(theme.components.MultipleChoice.label)}
        >
          {description}
        </label>
        <select
          name="data"
          id={id}
          value={selectedValue}
          className={classMapToString(theme.components.MultipleChoice.element)}
          style={stylesToObject(theme.additionalStyles?.MultipleChoice)}
          onChange={handleChange}
        >
          {(props.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {resolveString(option.label)}
            </option>
          ))}
        </select>
      </section>
    </div>
  );
}
