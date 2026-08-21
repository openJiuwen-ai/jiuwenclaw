// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** Reusable trajectory UI and OTLP projection surface. */

import './client/theme.css'

export { TrajectoryExplorer } from './client/TrajectoryExplorer.tsx'
export type { TrajectoryExplorerProps } from './client/TrajectoryExplorer.tsx'
export { TrajectoryTable } from './client/TrajectoryTable.tsx'
export type { TrajectoryTableProps } from './client/TrajectoryTable.tsx'
export { TrajectoryTimeline } from './client/TrajectoryTimeline.tsx'
export type { TrajectoryTimelineProps } from './client/TrajectoryTimeline.tsx'
export { TrajectoryToolbar } from './client/TrajectoryToolbar.tsx'
export type { TrajectoryToolbarProps } from './client/TrajectoryToolbar.tsx'
export {
  OtelGenAiTrajectoryProjector,
  projectOtelTrajectory,
} from './projector/otel-trajectory-projector.ts'
export type { TrajectoryProjector } from './projector/otel-trajectory-projector.ts'
export {
  normalizeTrajectoryAttributes,
  normalizeTrajectoryStreamEvents,
} from './projector/attribute-resolver.ts'
export type {
  NormalizedTrajectoryAttributes,
  NormalizedTrajectoryStreamEvent,
} from './projector/attribute-resolver.ts'
export type {
  OtlpAnyValue,
  OtlpExportTraceServiceRequest,
  OtlpInstrumentationScope,
  OtlpKeyValue,
  OtlpResource,
  OtlpResourceSpans,
  OtlpScopeSpans,
  OtlpSpan,
  OtlpSpanEvent,
  OtlpSpanLink,
  OtlpStatus,
} from './shared/otlp.ts'
export type {
  TrajectoryGroupModel,
  TrajectoryPromptSnapshot,
  TrajectoryRecordedFacts,
  TrajectoryRequest,
  TrajectoryRequestConfig,
  TrajectorySnapshot,
  TrajectoryToolSchema,
  TrajectoryTurnModel,
  TrajectoryUsage,
} from './trajectory/model.ts'
export type {
  AssistantMetricDetail,
  TrajectoryCell,
  TrajectoryCellKind,
  TrajectorySourceBlock,
} from './trajectory/record.ts'
export type { TrajectoryColorMode } from './theme/context.tsx'
export { TrajectoryPanel } from './TrajectoryPanel.tsx'
export type { TrajectoryPanelProps } from './TrajectoryPanel.tsx'
