// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** Session-scoped transport host for the migrated trajectory explorer. */

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import { webClient } from '../../services/webClient';
import { saveBlob } from '../../utils/desktopSave';
import { TrajectoryExplorer } from './client/TrajectoryExplorer';
import { JsonTree } from './primitives/JsonTree';
import { projectOtelTrajectory } from './projector/otel-trajectory-projector';
import type { OtlpExportTraceServiceRequest } from './shared/otlp';
import {
  getTrajectoryRawRecord,
  getTrajectoryArchive,
  getTrajectoryTrace,
  listTrajectoryTraceRevisions,
  listTrajectoryTraces,
  TrajectoryApiError,
  type TrajectoryDetailRecord,
  type TrajectoryTraceSummary,
} from './trajectoryClient';
import {
  exitTrajectoryReplay,
  parseTrajectoryArchive,
  shouldCatchUpTrajectory,
  trajectoryArchiveView,
  type TrajectoryArchive,
} from './trajectoryArchive';
import {
  collectHeadRefreshWindow,
  collectRevisionRefreshWindow,
  createTrajectoryOperationCoordinator,
  createTrajectoryWindowState,
  detailRecordIdentity,
  resetTrajectoryWindowState,
  selectSummariesNeedingLoad,
  spansOf,
  stageTrajectoryTracePages,
  trajectoryContentMode,
} from './trajectoryWindow';
import {
  RAW_INSPECTOR_DEFAULT_HEIGHT,
  clampRawInspectorHeight,
  rawInspectorHeightBounds,
  rawInspectorKeyboardHeight,
} from './trajectoryLayout';
import css from './TrajectoryPanel.module.css';
import './client/theme.css';

const INITIAL_TRACE_LIMIT = 30;
const DETAIL_LIMIT = 1000;
const DETAIL_CONCURRENCY = 6;
const MAX_ARCHIVE_BYTES = 128 * 1024 * 1024;

interface TraceUpdatedPayload {
  session_id?: unknown;
  trace_id?: unknown;
  revision?: unknown;
  change_seq?: unknown;
  store_epoch?: unknown;
  lifecycle?: unknown;
}

interface RawInspectorResizeDrag {
  pointerId: number;
  startHeight: number;
  startY: number;
}

export interface TrajectoryPanelProps {
  active: boolean;
  sessionId: string;
}

function recordLabel(record: OtlpExportTraceServiceRequest): string {
  const span = spansOf(record)[0];
  if (span === undefined) return 'OTLP record';
  return `${span.name} · ${span.traceId.slice(0, 8)}/${span.spanId.slice(0, 8)}`;
}

function detailRecordLabel(record: TrajectoryDetailRecord): string {
  if (record.otlp !== null) return recordLabel(record.otlp);
  const identity = detailRecordIdentity(record);
  const shortIdentity = identity === null
    ? `record #${record.ingest_seq}`
    : `${identity.slice(0, 8)}/${identity.slice(33, 41)}`;
  const size = record.raw_size_bytes === undefined
    ? ''
    : ` · ${record.raw_size_bytes.toLocaleString()} B`;
  return `Raw ${shortIdentity}${size}`;
}

function errorMessage(error: unknown, chinese: boolean): string {
  if (error instanceof TrajectoryApiError && error.code === 'TRAJECTORY_DISABLED') {
    return chinese ? '轨迹观测当前未启用。' : 'Trajectory observability is disabled.';
  }
  if (error instanceof Error && error.message) return error.message;
  return chinese ? '轨迹数据加载失败。' : 'Failed to load trajectory data.';
}

export function TrajectoryPanel({
  active,
  sessionId,
}: TrajectoryPanelProps) {
  const { i18n } = useTranslation();
  const chinese = (i18n.resolvedLanguage ?? i18n.language).toLowerCase().startsWith('zh');
  const windowStateRef = useRef(createTrajectoryWindowState());
  const operationCoordinatorRef = useRef(createTrajectoryOperationCoordinator());
  const loadedSessionRef = useRef<string | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const refreshPromiseRef = useRef<Promise<void> | null>(null);
  const rebuildPromiseRef = useRef<Promise<boolean> | null>(null);
  const hintFrameRef = useRef<number | null>(null);
  const pendingHintsRef = useRef(new Map<string, number>());
  const bodyRef = useRef<HTMLDivElement>(null);
  const rawResizeDragRef = useRef<RawInspectorResizeDrag | null>(null);
  const rawContentId = useId();
  const archiveInputRef = useRef<HTMLInputElement>(null);
  const [records, setRecords] = useState<OtlpExportTraceServiceRequest[]>([]);
  const [rawRecords, setRawRecords] = useState<TrajectoryDetailRecord[]>([]);
  const [lifecycleByRecordId, setLifecycleByRecordId] = useState(
    new Map<string, 'running' | 'completed' | 'error'>(),
  );
  const [traceCount, setTraceCount] = useState(0);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [replayArchive, setReplayArchive] = useState<TrajectoryArchive | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [invalidRecordSeen, setInvalidRecordSeen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rawSelection, setRawSelection] = useState('');
  const [fetchedRaw, setFetchedRaw] = useState<{ identity: string; data: unknown } | null>(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [rawError, setRawError] = useState<string | null>(null);
  const [rawExpanded, setRawExpanded] = useState(true);
  const [rawHeight, setRawHeight] = useState(RAW_INSPECTOR_DEFAULT_HEIGHT);
  const [rawContainerHeightPx, setRawContainerHeightPx] = useState(600);

  const copy = useMemo(() => chinese ? {
    loading: '正在加载轨迹…',
    emptyTitle: '暂无轨迹',
    emptyText: '运行一次单 Agent 对话后，这里会展示模型、推理和工具调用轨迹。',
    newTitle: '尚未创建会话',
    newText: '发送第一条消息后可查看轨迹。',
    retry: '重试',
    importArchive: '导入',
    exportArchive: '导出',
    exportingArchive: '导出中…',
    exitReplay: '退出复现',
    replay: (sourceSession: string) => `只读复现 · ${sourceSession}`,
    archiveTooLarge: '轨迹归档超过 128 MB，无法在浏览器中导入。',
    summary: (traces: number, spans: number) => `${traces} 条 trace · ${spans} 个 OTel Span`,
    invalid: '· 存在无法投影的原始记录',
    raw: (count: number) => `原始 OTel 记录 (${count})`,
    rawLabel: '选择原始 OTel 记录',
    rawLoad: '按需加载原始记录',
    rawLoading: '正在加载原始记录…',
    rawTooLarge: '该 Span 超过详情投影预算，未自动载入；可按需读取原始数据。',
    rawInvalid: '该记录无法按 OTLP JSON 投影；可按需查看原始内容。',
    rawOnly: '当前 trace 只有未投影的原始记录，可在下方按需查看。',
    rawCollapse: '收起原始 OTel 面板',
    rawExpand: '展开原始 OTel 面板',
    rawResize: '调整原始 OTel 面板高度',
    toolbar: {
      'toolbar.aria': '轨迹工具栏',
      'toolbar.duration': '时长',
      'toolbar.useActualDuration': '使用实际时长',
      'toolbar.useEqualWidth': '使用等宽操作',
      'toolbar.actualTime': '实际时间',
      'toolbar.turns': '轮次',
      'toolbar.expandTurns': '展开所有轮次',
      'toolbar.collapseTurns': '折叠所有轮次',
      'toolbar.calls': '调用',
      'toolbar.expandCalls': '展开所有调用',
      'toolbar.collapseCalls': '折叠所有调用',
      'toolbar.search': '搜索轨迹',
      'toolbar.searchPlaceholder': '搜索',
    },
  } : {
    loading: 'Loading trajectory…',
    emptyTitle: 'No trajectory yet',
    emptyText: 'Run a single-Agent conversation to see model, reasoning, and tool traces here.',
    newTitle: 'No conversation yet',
    newText: 'Send the first message to view its trajectory.',
    retry: 'Retry',
    importArchive: 'Import',
    exportArchive: 'Export',
    exportingArchive: 'Exporting…',
    exitReplay: 'Exit replay',
    replay: (sourceSession: string) => `Read-only replay · ${sourceSession}`,
    archiveTooLarge: 'The trajectory archive exceeds the 128 MB browser import limit.',
    summary: (traces: number, spans: number) => `${traces} traces · ${spans} OTel spans`,
    invalid: '· Some raw records could not be projected',
    raw: (count: number) => `Raw OTel records (${count})`,
    rawLabel: 'Select a raw OTel record',
    rawLoad: 'Load raw record on demand',
    rawLoading: 'Loading raw record…',
    rawTooLarge: 'This Span exceeds the detail projection budget and was not loaded automatically.',
    rawInvalid: 'This record could not be projected as OTLP JSON. Its raw content remains available.',
    rawOnly: 'This trace currently contains only unprojected raw records.',
    rawCollapse: 'Collapse raw OTel panel',
    rawExpand: 'Expand raw OTel panel',
    rawResize: 'Resize raw OTel panel height',
    toolbar: undefined,
  }, [chinese]);

  const publish = useCallback((generation: number) => {
    if (!operationCoordinatorRef.current.isCurrent(generation)) return;
    const nextRecords = [...windowStateRef.current.buckets.values()].flatMap(bucket => (
      [...bucket.records.values()]
    ));
    const nextRawRecords = [...windowStateRef.current.buckets.values()].flatMap(bucket => (
      [...bucket.rawRecords.values()]
    ));
    setRecords(nextRecords);
    setRawRecords(nextRawRecords);
    setLifecycleByRecordId(new Map(
      [...windowStateRef.current.buckets.values()].flatMap(bucket => (
        [...(bucket.versions ?? [])].map(([identity, version]) => (
          [identity, version.lifecycle] as const
        ))
      )),
    ));
    setTraceCount(windowStateRef.current.buckets.size);
  }, []);

  const clearPublishedWindow = useCallback(() => {
    resetTrajectoryWindowState(windowStateRef.current);
    setRecords([]);
    setRawRecords([]);
    setLifecycleByRecordId(new Map());
    setTraceCount(0);
    setHasEarlier(false);
    setInvalidRecordSeen(false);
    setError(null);
    setRawSelection('');
    setFetchedRaw(null);
    setRawLoading(false);
    setRawError(null);
  }, []);

  const loadTrace = useCallback(async (
    traceId: string,
    targetRevision: number,
    signal: AbortSignal,
    generation: number,
  ) => {
    if (!operationCoordinatorRef.current.isCurrent(generation)) return;
    const current = windowStateRef.current.buckets.get(traceId);
    if (current !== undefined && current.revision >= targetRevision) return;
    const staged = await stageTrajectoryTracePages(
      current,
      signal,
      (sinceRevision, pageSignal) => getTrajectoryTrace(sessionId, traceId, {
        signal: pageSignal,
        sinceRevision,
        limit: DETAIL_LIMIT,
      }),
    );
    if (staged === null
      || signal.aborted
      || !operationCoordinatorRef.current.isCurrent(generation)) return;
    const latest = windowStateRef.current.buckets.get(traceId);
    if (latest !== current
      && latest !== undefined
      && latest.revision > staged.bucket.revision) return;
    windowStateRef.current.buckets.set(traceId, staged.bucket);
    if (staged.invalidRecordSeen) setInvalidRecordSeen(true);
  }, [sessionId]);

  const loadSummaries = useCallback(async (
    summaries: readonly TrajectoryTraceSummary[],
    signal: AbortSignal,
    generation: number,
  ) => {
    for (let offset = 0; offset < summaries.length; offset += DETAIL_CONCURRENCY) {
      const batch = summaries.slice(offset, offset + DETAIL_CONCURRENCY);
      await Promise.all(batch.map(summary => loadTrace(
        summary.trace_id,
        summary.revision,
        signal,
        generation,
      )));
      if (signal.aborted
        || !operationCoordinatorRef.current.isCurrent(generation)) return;
      publish(generation);
    }
  }, [loadTrace, publish]);

  const rebuildFromHead = useCallback((signal: AbortSignal): Promise<boolean> => {
    if (rebuildPromiseRef.current !== null) return rebuildPromiseRef.current;
    const coordinator = operationCoordinatorRef.current;
    const generation = coordinator.invalidate(() => setLoadingEarlier(false));
    clearPublishedWindow();
    setLoading(true);
    const operation = (async () => {
      try {
        const page = await listTrajectoryTraces(sessionId, {
          signal,
          cursor: null,
          limit: INITIAL_TRACE_LIMIT,
        });
        if (signal.aborted || !coordinator.isCurrent(generation)) return false;
        await loadSummaries(page.items, signal, generation);
        if (signal.aborted || !coordinator.isCurrent(generation)) return false;
        const windowState = windowStateRef.current;
        windowState.storeEpoch = page.store_epoch;
        windowState.pageCursor = page.next_cursor;
        windowState.revisionCursor = page.revision_cursor;
        windowState.listWindowInitialized = true;
        setHasEarlier(page.next_cursor !== null);
        setError(null);
        return true;
      } catch (rebuildError) {
        if (!signal.aborted && coordinator.isCurrent(generation)) {
          setError(errorMessage(rebuildError, chinese));
        }
        return false;
      } finally {
        if (coordinator.isCurrent(generation)) setLoading(false);
      }
    })();
    rebuildPromiseRef.current = operation;
    void operation.finally(() => {
      if (rebuildPromiseRef.current === operation) rebuildPromiseRef.current = null;
    });
    return operation;
  }, [chinese, clearPublishedWindow, loadSummaries, sessionId]);

  const refreshLatest = useCallback(async () => {
    if (!active || sessionId === 'new' || requestControllerRef.current?.signal.aborted) return;
    if (refreshPromiseRef.current !== null) return refreshPromiseRef.current;
    const signal = requestControllerRef.current?.signal;
    if (signal === undefined) return;
    const operation = (async () => {
      try {
        const coordinator = operationCoordinatorRef.current;
        const generation = coordinator.currentGeneration();
        const earlier = coordinator.pendingLoadEarlier(generation);
        if (earlier !== null) await earlier;
        if (signal.aborted || !coordinator.isCurrent(generation)) return;
        const expectedStoreEpoch = windowStateRef.current.storeEpoch;
        if (expectedStoreEpoch === null) {
          await rebuildFromHead(signal);
          return;
        }
        const loadedRevisions = new Map<string, number>(
          [...windowStateRef.current.buckets]
            .map(([traceId, bucket]): [string, number] => [traceId, bucket.revision]),
        );
        const headWindow = await collectHeadRefreshWindow(
          loadedRevisions,
          expectedStoreEpoch,
          signal,
          (cursor, pageSignal) => listTrajectoryTraces(sessionId, {
            signal: pageSignal,
            cursor,
            limit: INITIAL_TRACE_LIMIT,
          }),
        );
        if (headWindow === null
          || signal.aborted
          || !coordinator.isCurrent(generation)) return;
        if (headWindow.reset) {
          await rebuildFromHead(signal);
          return;
        }
        const feedStart = windowStateRef.current.revisionCursor ?? headWindow.revisionCursor;
        const revisionWindow = await collectRevisionRefreshWindow(
          feedStart,
          expectedStoreEpoch,
          signal,
          (afterRevision, pageSignal) => listTrajectoryTraceRevisions(sessionId, {
            signal: pageSignal,
            afterRevision,
            limit: 100,
          }),
        );
        if (revisionWindow === null
          || signal.aborted
          || !coordinator.isCurrent(generation)) return;
        if (revisionWindow.reset) {
          await rebuildFromHead(signal);
          return;
        }
        const summaries = selectSummariesNeedingLoad(
          loadedRevisions,
          headWindow.summaries,
          revisionWindow.summaries,
        );
        await loadSummaries(summaries, signal, generation);
        if (signal.aborted
          || !coordinator.isCurrent(generation)
          || windowStateRef.current.storeEpoch !== expectedStoreEpoch) return;
        if (!windowStateRef.current.listWindowInitialized || loadedRevisions.size === 0) {
          windowStateRef.current.pageCursor = headWindow.firstPageNextCursor;
          windowStateRef.current.listWindowInitialized = true;
          setHasEarlier(headWindow.firstPageNextCursor !== null);
        }
        windowStateRef.current.revisionCursor = revisionWindow.nextCursor;
        setError(null);
      } catch (refreshError) {
        if (!signal.aborted) setError(errorMessage(refreshError, chinese));
      }
    })();
    refreshPromiseRef.current = operation;
    try {
      await operation;
    } finally {
      if (refreshPromiseRef.current === operation) {
        refreshPromiseRef.current = null;
      }
    }
  }, [active, chinese, loadSummaries, rebuildFromHead, sessionId]);

  const flushTraceHints = useCallback(async () => {
    const signal = requestControllerRef.current?.signal;
    if (!active || signal === undefined || signal.aborted) return;
    const hints = [...pendingHintsRef.current];
    pendingHintsRef.current.clear();
    if (hints.length === 0) return;
    const coordinator = operationCoordinatorRef.current;
    const generation = coordinator.currentGeneration();
    await Promise.all(hints.map(([traceId, revision]) => loadTrace(
      traceId,
      revision,
      signal,
      generation,
    )));
    if (signal.aborted || !coordinator.isCurrent(generation)) return;
    publish(generation);
  }, [active, loadTrace, publish]);

  useEffect(() => {
    requestControllerRef.current?.abort();
    refreshPromiseRef.current = null;
    rebuildPromiseRef.current = null;
    operationCoordinatorRef.current.invalidate(() => setLoadingEarlier(false));
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setRawLoading(false);
    pendingHintsRef.current.clear();
    if (hintFrameRef.current !== null) {
      window.cancelAnimationFrame(hintFrameRef.current);
      hintFrameRef.current = null;
    }
    const sessionChanged = loadedSessionRef.current !== sessionId;
    if (sessionChanged) {
      loadedSessionRef.current = sessionId;
      clearPublishedWindow();
    }
    if (!active || sessionId === 'new') {
      setLoading(false);
      return () => controller.abort();
    }
    if (!sessionChanged && windowStateRef.current.storeEpoch !== null) {
      setLoading(false);
      void refreshLatest();
      return () => controller.abort();
    }
    const operation = rebuildFromHead(controller.signal).then(() => undefined);
    refreshPromiseRef.current = operation;
    void operation.finally(() => {
      if (refreshPromiseRef.current === operation) refreshPromiseRef.current = null;
    });
    return () => controller.abort();
  }, [active, clearPublishedWindow, rebuildFromHead, refreshLatest, sessionId]);

  useEffect(() => {
    if (!active || sessionId === 'new' || replayArchive !== null) return undefined;
    const unsubscribe = webClient.on<TraceUpdatedPayload>('trace.updated', (event) => {
      if (event.payload.session_id !== sessionId) return;
      const traceId = event.payload.trace_id;
      const revisionValue = event.payload.revision ?? event.payload.change_seq;
      const revision = typeof revisionValue === 'number'
        ? revisionValue
        : typeof revisionValue === 'string' && /^\d+$/.test(revisionValue)
          ? Number(revisionValue)
          : Number.NaN;
      const eventEpoch = event.payload.store_epoch;
      const currentEpoch = windowStateRef.current.storeEpoch;
      if (typeof eventEpoch === 'string'
        && currentEpoch !== null
        && eventEpoch !== currentEpoch) {
        const signal = requestControllerRef.current?.signal;
        if (signal !== undefined && !signal.aborted) void rebuildFromHead(signal);
        return;
      }
      if (typeof traceId !== 'string'
        || !/^[0-9a-f]{32}$/.test(traceId)
        || !Number.isSafeInteger(revision)
        || revision < 0) {
        void refreshLatest();
        return;
      }
      const prior = pendingHintsRef.current.get(traceId) ?? -1;
      if (revision > prior) pendingHintsRef.current.set(traceId, revision);
      if (hintFrameRef.current !== null) return;
      hintFrameRef.current = window.requestAnimationFrame(() => {
        hintFrameRef.current = null;
        void flushTraceHints();
      });
    });
    let previousConnectionState = webClient.getState();
    const unsubscribeState = webClient.onStateChange((nextConnectionState) => {
      if (shouldCatchUpTrajectory(previousConnectionState, nextConnectionState)) {
        void refreshLatest();
      }
      previousConnectionState = nextConnectionState;
    });
    return () => {
      unsubscribe();
      unsubscribeState();
      if (hintFrameRef.current !== null) {
        window.cancelAnimationFrame(hintFrameRef.current);
        hintFrameRef.current = null;
      }
      pendingHintsRef.current.clear();
    };
  }, [active, flushTraceHints, rebuildFromHead, refreshLatest, replayArchive, sessionId]);

  const loadEarlier = useCallback((): Promise<boolean> => {
    const signal = requestControllerRef.current?.signal;
    if (!active || replayArchive !== null || signal === undefined || signal.aborted) {
      return Promise.resolve(false);
    }
    const coordinator = operationCoordinatorRef.current;
    return coordinator.runLoadEarlier(async (generation) => {
      try {
        const runningRefresh = refreshPromiseRef.current;
        if (runningRefresh !== null) await runningRefresh;
        if (signal.aborted || !coordinator.isCurrent(generation)) return false;
        const cursor = windowStateRef.current.pageCursor;
        const expectedStoreEpoch = windowStateRef.current.storeEpoch;
        if (cursor === null || expectedStoreEpoch === null) return false;
        const page = await listTrajectoryTraces(sessionId, {
          signal,
          cursor,
          limit: INITIAL_TRACE_LIMIT,
        });
        if (signal.aborted || !coordinator.isCurrent(generation)) return false;
        if (page.store_epoch !== expectedStoreEpoch) {
          await rebuildFromHead(signal);
          return false;
        }
        const loadedRevisions = new Map<string, number>(
          [...windowStateRef.current.buckets]
            .map(([traceId, bucket]): [string, number] => [traceId, bucket.revision]),
        );
        const summaries = selectSummariesNeedingLoad(loadedRevisions, page.items);
        await loadSummaries(summaries, signal, generation);
        if (signal.aborted
          || !coordinator.isCurrent(generation)
          || windowStateRef.current.storeEpoch !== expectedStoreEpoch
          || windowStateRef.current.pageCursor !== cursor) return false;
        windowStateRef.current.pageCursor = page.next_cursor;
        windowStateRef.current.listWindowInitialized = true;
        setHasEarlier(page.next_cursor !== null);
        setError(null);
        return page.items.length > 0;
      } catch (loadError) {
        if (!signal.aborted && coordinator.isCurrent(generation)) {
          setError(errorMessage(loadError, chinese));
        }
        return false;
      }
    }, setLoadingEarlier);
  }, [active, chinese, loadSummaries, rebuildFromHead, replayArchive, sessionId]);

  const replayView = useMemo(
    () => replayArchive === null ? null : trajectoryArchiveView(replayArchive),
    [replayArchive],
  );
  const displayedRecords = replayView?.records ?? records;
  const displayedRawRecords = replayView?.rawRecords ?? rawRecords;
  const displayedLifecycle = replayView?.lifecycleByRecordId ?? lifecycleByRecordId;
  const displayedTraceCount = replayView?.traceCount ?? traceCount;
  const displayedInvalidRecordSeen = replayView?.invalidRecordSeen ?? invalidRecordSeen;

  useEffect(() => {
    if (displayedRawRecords.length === 0) {
      windowStateRef.current.rawSelection = '';
      setRawSelection('');
      return;
    }
    const identities = displayedRawRecords
      .map(detailRecordIdentity)
      .filter((value): value is string => value !== null);
    if (!identities.includes(rawSelection)) {
      const nextSelection = identities[0] ?? '';
      windowStateRef.current.rawSelection = nextSelection;
      setRawSelection(nextSelection);
    }
  }, [displayedRawRecords, rawSelection]);

  useEffect(() => {
    setFetchedRaw(null);
    setRawError(null);
    setRawLoading(false);
  }, [rawSelection, replayArchive, sessionId]);

  const snapshot = useMemo(() => projectOtelTrajectory(displayedRecords, {
    lifecycleByRecordId: displayedLifecycle,
  }), [displayedLifecycle, displayedRecords]);
  const rawRecord = useMemo(
    () => displayedRawRecords.find(record => detailRecordIdentity(record) === rawSelection),
    [displayedRawRecords, rawSelection],
  );
  const rawData = replayView === null
    ? rawRecord?.otlp ?? (fetchedRaw?.identity === rawSelection ? fetchedRaw.data : undefined)
    : replayView.rawDataByRecordId.get(rawSelection);

  const loadSelectedRaw = useCallback(async () => {
    if (replayArchive !== null) return;
    if (rawRecord?.trace_id === undefined || rawRecord.span_id === undefined) return;
    const signal = requestControllerRef.current?.signal;
    if (signal === undefined || signal.aborted) return;
    const generation = operationCoordinatorRef.current.currentGeneration();
    setRawLoading(true);
    setRawError(null);
    try {
      const data = await getTrajectoryRawRecord(
        sessionId,
        rawRecord.trace_id,
        rawRecord.span_id,
        { signal },
      );
      if (!signal.aborted
        && operationCoordinatorRef.current.isCurrent(generation)) {
        setFetchedRaw({ identity: rawSelection, data });
      }
    } catch (loadError) {
      if (!signal.aborted
        && operationCoordinatorRef.current.isCurrent(generation)) {
        setRawError(errorMessage(loadError, chinese));
      }
    } finally {
      if (!signal.aborted
        && operationCoordinatorRef.current.isCurrent(generation)) {
        setRawLoading(false);
      }
    }
  }, [chinese, rawRecord, rawSelection, replayArchive, sessionId]);

  const exportArchive = useCallback(async () => {
    if (exporting || (replayArchive === null && sessionId === 'new')) return;
    const signal = requestControllerRef.current?.signal;
    if (replayArchive === null && (signal === undefined || signal.aborted)) return;
    setExporting(true);
    setArchiveError(null);
    try {
      const archive = replayArchive ?? parseTrajectoryArchive(
        await getTrajectoryArchive(sessionId, { signal }),
      );
      const safeSession = archive.session_id.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 80) || 'session';
      const blob = new Blob([`${JSON.stringify(archive, null, 2)}\n`], {
        type: 'application/json;charset=utf-8',
      });
      const outcome = await saveBlob(blob, `trajectory-${safeSession}.json`);
      if (outcome === 'failed') throw new Error('Trajectory archive could not be saved');
    } catch (exportError) {
      if (!(exportError instanceof DOMException && exportError.name === 'AbortError')) {
        setArchiveError(errorMessage(exportError, chinese));
      }
    } finally {
      setExporting(false);
    }
  }, [chinese, exporting, replayArchive, sessionId]);

  const importArchive = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (file === undefined) return;
    setArchiveError(null);
    try {
      if (file.size > MAX_ARCHIVE_BYTES) throw new Error(copy.archiveTooLarge);
      const archive = parseTrajectoryArchive(await file.text());
      setReplayArchive(archive);
      setRawSelection('');
      setFetchedRaw(null);
      setRawError(null);
    } catch (importError) {
      setArchiveError(errorMessage(importError, chinese));
    }
  }, [chinese, copy.archiveTooLarge]);

  const exitReplay = useCallback(() => {
    const transition = exitTrajectoryReplay(replayArchive);
    setReplayArchive(transition.archive);
    setArchiveError(null);
    setRawSelection('');
    if (transition.catchUpLiveRevision) void refreshLatest();
  }, [refreshLatest, replayArchive]);

  const rawContainerHeight = useCallback(() => (
    bodyRef.current?.getBoundingClientRect().height ?? 600
  ), []);

  useEffect(() => {
    const body = bodyRef.current;
    if (body === null || typeof ResizeObserver === 'undefined') return undefined;
    const syncHeight = () => {
      const nextContainerHeight = body.getBoundingClientRect().height;
      if (nextContainerHeight <= 0) return;
      setRawContainerHeightPx(nextContainerHeight);
      setRawHeight(current => clampRawInspectorHeight(current, nextContainerHeight));
    };
    syncHeight();
    const observer = new ResizeObserver(syncHeight);
    observer.observe(body);
    return () => observer.disconnect();
  }, []);

  const handleRawResizePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || rawResizeDragRef.current !== null) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add('trajectory-raw-resize-active');
    rawResizeDragRef.current = {
      pointerId: event.pointerId,
      startHeight: rawHeight,
      startY: event.clientY,
    };
  }, [rawHeight]);

  const handleRawResizePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = rawResizeDragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    const nextHeight = drag.startHeight + drag.startY - event.clientY;
    setRawHeight(clampRawInspectorHeight(nextHeight, rawContainerHeight()));
  }, [rawContainerHeight]);

  const clearRawResize = useCallback((pointerId?: number) => {
    if (pointerId !== undefined && rawResizeDragRef.current?.pointerId !== pointerId) return;
    rawResizeDragRef.current = null;
    document.body.classList.remove('trajectory-raw-resize-active');
  }, []);

  const finishRawResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    clearRawResize(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, [clearRawResize]);

  const handleRawResizeKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const nextHeight = rawInspectorKeyboardHeight(
      rawHeight,
      event.key,
      rawContainerHeight(),
    );
    if (nextHeight === null) return;
    event.preventDefault();
    setRawHeight(nextHeight);
  }, [rawContainerHeight, rawHeight]);

  useEffect(() => () => {
    document.body.classList.remove('trajectory-raw-resize-active');
  }, []);

  const rawHeightBounds = rawInspectorHeightBounds(rawContainerHeightPx);

  const rawInspector = displayedRawRecords.length === 0 ? null : (
    <section
      className={`${css.rawInspector} ${rawExpanded ? css.rawInspectorExpanded : ''} jiuwenTrajectoryTheme`}
      data-trajectory-theme="light"
      data-testid="trajectory-raw-inspector"
      // The migrated theme gives every `.jiuwenTrajectoryTheme` root
      // `height: 100%`. Override it in both states so a collapsed inspector is
      // only its summary row instead of filling the trajectory body.
      style={{ height: rawExpanded ? `${rawHeight}px` : 'auto' }}
    >
      {rawExpanded ? (
        <div
          className={css.rawResizeHandle}
          role="separator"
          aria-label={copy.rawResize}
          aria-orientation="horizontal"
          aria-valuemin={rawHeightBounds.min}
          aria-valuemax={rawHeightBounds.max}
          aria-valuenow={rawHeight}
          tabIndex={0}
          onKeyDown={handleRawResizeKeyDown}
          onPointerDown={handleRawResizePointerDown}
          onPointerMove={handleRawResizePointerMove}
          onPointerUp={finishRawResize}
          onPointerCancel={finishRawResize}
          onLostPointerCapture={(event) => clearRawResize(event.pointerId)}
          data-testid="trajectory-raw-resize-handle"
        />
      ) : null}
      <button
        type="button"
        className={css.rawSummary}
        aria-controls={rawContentId}
        aria-expanded={rawExpanded}
        title={rawExpanded ? copy.rawCollapse : copy.rawExpand}
        onClick={() => setRawExpanded(expanded => !expanded)}
        data-testid="trajectory-raw-toggle"
      >
        <span>{copy.raw(displayedRawRecords.length)}</span>
        <span aria-hidden="true">{rawExpanded ? '▾' : '▴'}</span>
      </button>
      {rawExpanded ? (
        <div id={rawContentId} className={css.rawContent}>
          <div className={css.rawControls}>
            <select
              className={css.rawSelect}
              aria-label={copy.rawLabel}
              value={rawSelection}
              onChange={(event) => {
                const nextSelection = event.currentTarget.value;
                windowStateRef.current.rawSelection = nextSelection;
                setRawSelection(nextSelection);
              }}
            >
              {displayedRawRecords.map((record) => {
                const identity = detailRecordIdentity(record);
                return identity === null ? null : (
                  <option key={identity} value={identity}>{detailRecordLabel(record)}</option>
                );
              })}
            </select>
          </div>
          {rawRecord?.projection_omitted === 'record_too_large' ? (
            <p className={css.rawNotice}>{copy.rawTooLarge}</p>
          ) : rawRecord?.raw_valid === false ? (
            <p className={css.rawNotice}>{copy.rawInvalid}</p>
          ) : null}
          {rawData === undefined && rawRecord !== undefined && replayArchive === null ? (
            <button
              className={css.rawLoad}
              type="button"
              disabled={rawLoading}
              onClick={() => { void loadSelectedRaw(); }}
            >
              {rawLoading ? copy.rawLoading : copy.rawLoad}
            </button>
          ) : null}
          {rawError === null ? null : <p className={`${css.rawNotice} ${css.errorText}`}>{rawError}</p>}
          {rawData === undefined ? null : (
            <JsonTree
              data={typeof rawData === 'object' && rawData !== null ? rawData : { raw: rawData }}
              className={css.rawTree}
              label={copy.rawLabel}
              expandTopLevel={false}
            />
          )}
        </div>
      ) : null}
    </section>
  );

  const contentMode = trajectoryContentMode({
    sessionId: replayArchive?.session_id ?? sessionId,
    loading: replayArchive === null ? loading : false,
    error: replayArchive === null ? error : null,
    projectedCount: displayedRecords.length,
    rawCount: displayedRawRecords.length,
  });
  let content;
  if (contentMode === 'new') {
    content = (
      <div className={css.state}>
        <div className={css.stateContent}>
          <h2 className={css.stateTitle}>{copy.newTitle}</h2>
          <p className={css.stateText}>{copy.newText}</p>
        </div>
      </div>
    );
  } else if (contentMode === 'loading') {
    content = <div className={css.state}><p className={css.stateText}>{copy.loading}</p></div>;
  } else if (contentMode === 'blocking-error') {
    content = (
      <div className={css.state}>
        <div className={css.stateContent}>
          <h2 className={css.stateTitle}>{copy.emptyTitle}</h2>
          <p className={`${css.stateText} ${css.errorText}`}>{error}</p>
          <button className={css.retry} type="button" onClick={() => { void refreshLatest(); }}>
            {copy.retry}
          </button>
        </div>
      </div>
    );
  } else if (contentMode === 'empty') {
    content = (
      <div className={css.state}>
        <div className={css.stateContent}>
          <h2 className={css.stateTitle}>{copy.emptyTitle}</h2>
          <p className={css.stateText}>{copy.emptyText}</p>
        </div>
      </div>
    );
  } else {
    content = (
      <>
        {error !== null && replayArchive === null && displayedRecords.length === 0 ? (
          <p className={`${css.rawNotice} ${css.errorText}`}>{error}</p>
        ) : null}
        {displayedRecords.length === 0 ? (
          <div className={css.state}><p className={css.stateText}>{copy.rawOnly}</p></div>
        ) : (
          <TrajectoryExplorer
            snapshot={snapshot}
            loading={replayArchive === null ? loading : false}
            loadingEarlier={replayArchive === null ? loadingEarlier : false}
            hasEarlier={replayArchive === null ? hasEarlier : false}
            loadEarlier={loadEarlier}
            error={replayArchive === null ? error : null}
            messages={copy.toolbar}
            colorMode="light"
            className={css.explorer}
          />
        )}
        {rawInspector}
      </>
    );
  }

  return (
    <section className={css.root} aria-label={chinese ? '轨迹' : 'Trajectory'}>
      <header className={css.header}>
        <div className={css.summary}>
          {replayArchive === null
            ? copy.summary(displayedTraceCount, displayedRawRecords.length || displayedRecords.length)
            : copy.replay(replayArchive.session_id)}
          {displayedInvalidRecordSeen ? <span className={css.warning}> {copy.invalid}</span> : null}
        </div>
        <div className={css.archiveActions}>
          <input
            ref={archiveInputRef}
            className={css.archiveInput}
            type="file"
            accept="application/json,.json"
            onChange={(event) => { void importArchive(event); }}
            data-testid="trajectory-archive-input"
          />
          <button
            type="button"
            className={css.archiveAction}
            onClick={() => archiveInputRef.current?.click()}
            data-testid="trajectory-archive-import"
          >
            {copy.importArchive}
          </button>
          <button
            type="button"
            className={css.archiveAction}
            disabled={exporting || (replayArchive === null && sessionId === 'new')}
            onClick={() => { void exportArchive(); }}
            data-testid="trajectory-archive-export"
          >
            {exporting ? copy.exportingArchive : copy.exportArchive}
          </button>
          {replayArchive === null ? null : (
            <button
              type="button"
              className={css.archiveAction}
              onClick={exitReplay}
              data-testid="trajectory-archive-exit"
            >
              {copy.exitReplay}
            </button>
          )}
        </div>
      </header>
      {archiveError === null ? null : (
        <p className={`${css.archiveError} ${css.errorText}`} role="alert">{archiveError}</p>
      )}
      <div ref={bodyRef} className={css.body}>{content}</div>
    </section>
  );
}
