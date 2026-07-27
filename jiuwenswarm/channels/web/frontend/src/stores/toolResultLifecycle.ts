import { ToolExecutionStatus, ToolResult } from '../types';

export function mergeToolResultProgress(existing: ToolResult | undefined, incoming: ToolResult): ToolResult {
  if (incoming.beamSearch || !existing?.beamSearch) {
    return incoming;
  }
  return {
    ...incoming,
    beamSearch: existing.beamSearch,
  };
}

function hasSameResultData(existing: ToolResult, incoming: ToolResult): boolean {
  return (
    existing.result === incoming.result &&
    existing.success === incoming.success &&
    Boolean(existing.canceled) === Boolean(incoming.canceled) &&
    (existing.summary || '') === (incoming.summary || '') &&
    existing.beamSearch === incoming.beamSearch
  );
}

export function shouldDropToolResult(currentStatus: ToolExecutionStatus, existing: ToolResult | undefined, incoming: ToolResult): boolean {
  const finalStatus: ToolExecutionStatus = incoming.canceled
    ? 'canceled'
    : incoming.success
      ? 'completed'
      : 'error';
  return currentStatus === finalStatus && existing !== undefined && hasSameResultData(existing, incoming);
}
