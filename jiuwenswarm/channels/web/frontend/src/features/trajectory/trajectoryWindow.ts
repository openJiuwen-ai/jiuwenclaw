// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** Transactional browser window helpers for trajectory list and detail polling. */

import type {
  OtlpExportTraceServiceRequest,
  OtlpSpan,
} from './shared/otlp';
import type {
  TrajectoryDetailRecord,
  TrajectoryRevisionListResponse,
  TrajectoryTraceDetailResponse,
  TrajectoryTraceListResponse,
  TrajectoryTraceSummary,
} from './trajectoryClient';

export interface TrajectoryTraceBucket {
  revision: number;
  records: Map<string, OtlpExportTraceServiceRequest>;
  rawRecords: Map<string, TrajectoryDetailRecord>;
  versions?: Map<string, TrajectoryRecordVersion>;
}

export interface TrajectoryRecordVersion {
  lifecycle: 'running' | 'completed' | 'error';
  recordRevision: number;
}

export interface TrajectoryWindowState {
  buckets: Map<string, TrajectoryTraceBucket>;
  storeEpoch: string | null;
  pageCursor: string | null;
  revisionCursor: string | null;
  listWindowInitialized: boolean;
  rawSelection: string;
}

export type HeadRefreshWindow = {
  reset: true;
  storeEpoch: string;
} | {
  reset: false;
  storeEpoch: string;
  summaries: TrajectoryTraceSummary[];
  firstPageNextCursor: string | null;
  revisionCursor: string;
};

export type RevisionRefreshWindow = {
  reset: true;
  storeEpoch: string;
} | {
  reset: false;
  storeEpoch: string;
  summaries: TrajectoryTraceSummary[];
  nextCursor: string;
};

export interface TrajectoryOperationCoordinator {
  currentGeneration: () => number;
  invalidate: (restoreBusy: () => void) => number;
  isCurrent: (generation: number) => boolean;
  pendingLoadEarlier: (generation: number) => Promise<boolean> | null;
  runLoadEarlier: (
    operation: (generation: number) => Promise<boolean>,
    setBusy: (busy: boolean) => void,
  ) => Promise<boolean>;
}

export interface StagedTrajectoryTrace {
  bucket: TrajectoryTraceBucket;
  invalidRecordSeen: boolean;
}

export type TrajectoryContentMode = 'new' | 'loading' | 'blocking-error' | 'empty' | 'data';

type TraceListPageLoader = (
  cursor: string | null,
  signal: AbortSignal,
) => Promise<TrajectoryTraceListResponse>;

type RevisionListPageLoader = (
  afterRevision: string,
  signal: AbortSignal,
) => Promise<TrajectoryRevisionListResponse>;

type TraceDetailPageLoader = (
  sinceRevision: number,
  signal: AbortSignal,
) => Promise<TrajectoryTraceDetailResponse>;

function invalidPagination(message: string): Error {
  const error = new Error(message);
  error.name = 'TrajectoryPaginationError';
  return error;
}

export function createTrajectoryWindowState(): TrajectoryWindowState {
  return {
    buckets: new Map<string, TrajectoryTraceBucket>(),
    storeEpoch: null,
    pageCursor: null,
    revisionCursor: null,
    listWindowInitialized: false,
    rawSelection: '',
  };
}

export function resetTrajectoryWindowState(state: TrajectoryWindowState): void {
  state.buckets = new Map<string, TrajectoryTraceBucket>();
  state.storeEpoch = null;
  state.pageCursor = null;
  state.revisionCursor = null;
  state.listWindowInitialized = false;
  state.rawSelection = '';
}

export function createTrajectoryOperationCoordinator(): TrajectoryOperationCoordinator {
  let generation = 0;
  let earlier: { generation: number; promise: Promise<boolean> } | null = null;
  return {
    currentGeneration: () => generation,
    invalidate: (restoreBusy) => {
      generation += 1;
      earlier = null;
      restoreBusy();
      return generation;
    },
    isCurrent: candidate => candidate === generation,
    pendingLoadEarlier: candidate => (
      candidate === generation && earlier?.generation === generation
        ? earlier.promise
        : null
    ),
    runLoadEarlier: (operation, setBusy) => {
      if (earlier?.generation === generation) return earlier.promise;
      const operationGeneration = generation;
      setBusy(true);
      let start: () => void = () => {};
      const source = new Promise<boolean>((resolve, reject) => {
        start = () => {
          try {
            void operation(operationGeneration).then(resolve, reject);
          } catch (error) {
            reject(error);
          }
        };
      });
      let promise: Promise<boolean>;
      promise = source.finally(() => {
        if (earlier?.promise !== promise) return;
        earlier = null;
        if (operationGeneration === generation) setBusy(false);
      });
      earlier = { generation: operationGeneration, promise };
      start();
      return promise;
    },
  };
}

export function spansOf(record: OtlpExportTraceServiceRequest): OtlpSpan[] {
  return record.resourceSpans.flatMap(resource => (
    resource.scopeSpans ?? []
  ).flatMap(scope => scope.spans ?? []));
}

export function recordIdentity(record: OtlpExportTraceServiceRequest): string | null {
  const spans = spansOf(record);
  if (spans.length !== 1 || spans[0] === undefined) return null;
  return `${spans[0].traceId}:${spans[0].spanId}`;
}

export function detailRecordIdentity(record: TrajectoryDetailRecord): string | null {
  if (record.record_id !== undefined) return record.record_id;
  if (record.trace_id !== undefined && record.span_id !== undefined) {
    return `${record.trace_id}:${record.span_id}`;
  }
  return record.otlp === null ? null : recordIdentity(record.otlp);
}

export function detailRecordLifecycle(
  record: TrajectoryDetailRecord,
): TrajectoryRecordVersion['lifecycle'] {
  if (record.lifecycle === 'provisional' || record.lifecycle === 'running') return 'running';
  if (record.lifecycle === 'abandoned' || record.lifecycle === 'error') return 'error';
  return 'completed';
}

function detailRecordRevision(record: TrajectoryDetailRecord): number {
  return record.record_revision ?? record.change_seq ?? record.ingest_seq;
}

function terminal(lifecycle: TrajectoryRecordVersion['lifecycle']): boolean {
  return lifecycle === 'completed' || lifecycle === 'error';
}

/** Apply one revision page with per-record latest-wins and terminal absorption. */
export function applyTrajectoryDetailRecords(
  current: TrajectoryTraceBucket | undefined,
  detail: TrajectoryTraceDetailResponse,
): StagedTrajectoryTrace {
  let records = new Map<string, OtlpExportTraceServiceRequest>(current?.records ?? []);
  let rawRecords = new Map<string, TrajectoryDetailRecord>(current?.rawRecords ?? []);
  let versions = new Map<string, TrajectoryRecordVersion>(current?.versions ?? []);
  let invalidRecordSeen = false;
  if (detail.reset) {
    records = new Map();
    rawRecords = new Map();
    versions = new Map();
  }
  for (const item of detail.records) {
    const identity = detailRecordIdentity(item);
    if (identity === null) {
      invalidRecordSeen = true;
      continue;
    }
    const incoming = {
      lifecycle: detailRecordLifecycle(item),
      recordRevision: detailRecordRevision(item),
    };
    const prior = versions.get(identity);
    if (prior !== undefined) {
      if (terminal(prior.lifecycle) && incoming.lifecycle === 'running') continue;
      const incomingTerminal = terminal(incoming.lifecycle);
      if (!incomingTerminal || terminal(prior.lifecycle)) {
        if (incoming.recordRevision < prior.recordRevision) continue;
        if (incoming.recordRevision === prior.recordRevision) continue;
      }
    }
    if (item.operation === 'delete') {
      records.delete(identity);
      rawRecords.delete(identity);
      versions.delete(identity);
      continue;
    }
    versions.set(identity, incoming);
    rawRecords.set(identity, item);
    if (!item.raw_valid || item.otlp === null) {
      invalidRecordSeen = true;
      records.delete(identity);
      continue;
    }
    const otlpIdentity = recordIdentity(item.otlp);
    if (otlpIdentity !== identity) {
      invalidRecordSeen = true;
      continue;
    }
    records.set(identity, item.otlp);
  }
  return {
    bucket: {
      revision: detail.revision,
      records,
      rawRecords,
      versions,
    },
    invalidRecordSeen,
  };
}

export function dedupeTraceSummaries(
  summaries: readonly TrajectoryTraceSummary[],
): TrajectoryTraceSummary[] {
  const byTraceId = new Map<string, TrajectoryTraceSummary>();
  for (const summary of summaries) {
    const current = byTraceId.get(summary.trace_id);
    if (current === undefined || summary.revision >= current.revision) {
      byTraceId.set(summary.trace_id, summary);
    }
  }
  return [...byTraceId.values()];
}

export function selectSummariesNeedingLoad(
  loadedRevisions: ReadonlyMap<string, number>,
  ...summaryGroups: Array<readonly TrajectoryTraceSummary[]>
): TrajectoryTraceSummary[] {
  const summaries = dedupeTraceSummaries(summaryGroups.flatMap(group => [...group]));
  return summaries.filter((summary) => {
    const loadedRevision = loadedRevisions.get(summary.trace_id);
    return loadedRevision === undefined || summary.revision > loadedRevision;
  });
}

export async function collectHeadRefreshWindow(
  loadedRevisions: ReadonlyMap<string, number>,
  expectedStoreEpoch: string,
  signal: AbortSignal,
  loadPage: TraceListPageLoader,
): Promise<HeadRefreshWindow | null> {
  let cursor: string | null = null;
  let firstPageNextCursor: string | null = null;
  let revisionCursor = '';
  const summaries: TrajectoryTraceSummary[] = [];
  while (true) {
    if (signal.aborted) return null;
    const page = await loadPage(cursor, signal);
    if (signal.aborted) return null;
    if (page.store_epoch !== expectedStoreEpoch) {
      return { reset: true, storeEpoch: page.store_epoch };
    }
    if (cursor === null) {
      firstPageNextCursor = page.next_cursor;
      revisionCursor = page.revision_cursor;
    }
    summaries.push(...page.items);
    const overlapsLoadedWindow = page.items.some(item => loadedRevisions.has(item.trace_id));
    if (loadedRevisions.size === 0 || overlapsLoadedWindow || page.next_cursor === null) {
      return {
        reset: false,
        storeEpoch: expectedStoreEpoch,
        summaries: dedupeTraceSummaries(summaries),
        firstPageNextCursor,
        revisionCursor,
      };
    }
    if (page.next_cursor === cursor) {
      throw invalidPagination('Trajectory head pagination did not advance');
    }
    cursor = page.next_cursor;
  }
}

export async function collectRevisionRefreshWindow(
  afterRevision: string,
  expectedStoreEpoch: string,
  signal: AbortSignal,
  loadPage: RevisionListPageLoader,
): Promise<RevisionRefreshWindow | null> {
  let cursor = afterRevision;
  let expectedWatermark: string | null = null;
  const summaries: TrajectoryTraceSummary[] = [];
  while (true) {
    if (signal.aborted) return null;
    const page = await loadPage(cursor, signal);
    if (signal.aborted) return null;
    if (page.reset || page.store_epoch !== expectedStoreEpoch) {
      return { reset: true, storeEpoch: page.store_epoch };
    }
    if (expectedWatermark !== null && page.watermark !== expectedWatermark) {
      throw invalidPagination('Trajectory revision watermark changed during pagination');
    }
    expectedWatermark = page.watermark;
    summaries.push(...page.items);
    if (!page.has_more) {
      if (page.next_cursor !== page.watermark) {
        throw invalidPagination('Trajectory revision pagination ended before its watermark');
      }
      return {
        reset: false,
        storeEpoch: expectedStoreEpoch,
        summaries: dedupeTraceSummaries(summaries),
        nextCursor: page.next_cursor,
      };
    }
    if (page.next_cursor === cursor) {
      throw invalidPagination('Trajectory revision pagination did not advance');
    }
    cursor = page.next_cursor;
  }
}

export async function stageTrajectoryTracePages(
  current: TrajectoryTraceBucket | undefined,
  signal: AbortSignal,
  loadPage: TraceDetailPageLoader,
): Promise<StagedTrajectoryTrace | null> {
  let sinceRevision = current?.revision ?? 0;
  let stagedRecords = new Map<string, OtlpExportTraceServiceRequest>(
    current?.records ?? [],
  );
  let stagedRawRecords = new Map<string, TrajectoryDetailRecord>(
    current?.rawRecords ?? [],
  );
  let stagedVersions = new Map<string, TrajectoryRecordVersion>(current?.versions ?? []);
  let stagedRevision = current?.revision ?? 0;
  let invalidRecordSeen = false;
  while (true) {
    if (signal.aborted) return null;
    const detail = await loadPage(sinceRevision, signal);
    if (signal.aborted) return null;
    if (detail.reset) {
      stagedRecords = new Map<string, OtlpExportTraceServiceRequest>();
      stagedRawRecords = new Map<string, TrajectoryDetailRecord>();
      stagedVersions = new Map<string, TrajectoryRecordVersion>();
      sinceRevision = 0;
    }
    const applied = applyTrajectoryDetailRecords({
      revision: stagedRevision,
      records: stagedRecords,
      rawRecords: stagedRawRecords,
      versions: stagedVersions,
    }, detail);
    stagedRecords = applied.bucket.records;
    stagedRawRecords = applied.bucket.rawRecords;
    stagedVersions = applied.bucket.versions ?? new Map();
    invalidRecordSeen = invalidRecordSeen || applied.invalidRecordSeen;
    stagedRevision = detail.revision;
    if (!detail.has_more) break;
    if (detail.next_since_revision <= sinceRevision) {
      throw invalidPagination('Trajectory detail pagination did not advance');
    }
    sinceRevision = detail.next_since_revision;
  }
  return {
    bucket: {
      revision: stagedRevision,
      records: stagedRecords,
      rawRecords: stagedRawRecords,
      versions: stagedVersions,
    },
    invalidRecordSeen,
  };
}

export function trajectoryContentMode(input: {
  sessionId: string;
  loading: boolean;
  error: string | null;
  projectedCount: number;
  rawCount: number;
}): TrajectoryContentMode {
  if (input.sessionId === 'new') return 'new';
  const hasData = input.projectedCount > 0 || input.rawCount > 0;
  if (input.loading && !hasData) return 'loading';
  if (input.error !== null && !hasData) return 'blocking-error';
  if (!hasData) return 'empty';
  return 'data';
}
