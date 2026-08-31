// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/**
 * Chrome-Network-style overview timeline for focusing the trajectory ledger.
 * Adapted mechanically from `packages/client/ui-trajectory/src/client/TrajectoryTimeline.tsx`
 * under the repository MIT license.
 */

import {
  memo, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent,
  type PointerEvent,
} from 'react'
import { Tooltip } from '../primitives/index.ts'
import type { TrajectoryTurnModel } from '../trajectory/model.ts'
import type { AssistantMetricDetail, TrajectoryCellKind, TrajectoryCellProps } from '../trajectory/record.ts'
import { formatTokenCount, liveElapsedSeconds } from '../trajectory/record.ts'
import {
  deriveTrajectoryTimeline,
  formatTimelineOffset,
  type TrajectoryTimelineMode,
  type TrajectoryTimelineSegment,
  type TrajectoryTimelineSpan,
  type TrajectoryTimeRange,
} from '../trajectory/timeline.ts'
import css from './TrajectoryTimeline.module.css'

const MINIMUM_DRAG_PX = 3
const MINIMUM_ZOOM_OPERATIONS = 4
const MINIMUM_ZOOM_TOKENS = 256
const MINIMUM_ZOOM_MILLIS = 20
const EDGE_PAN_ZONE_FRACTION = 0.08
const EDGE_PAN_STEP_FRACTION = 0.025
const MAXIMUM_EDGE_PAN_PX = 32
const TIMELINE_TOOLTIP_DELAY_MS = 500

interface TimelineRecordDetail {
  decodingMs?: number
  durationMs?: number
  startedAt?: number
  ttftMs?: number
  cachedInput?: number
  cacheWriteInput?: number
  processedInput?: number
  outputTokens?: number
  reasoningTokens?: number
}

interface FractionRange {
  start: number
  end: number
}

interface HoverPoint {
  fraction: number
  recordIndex: number | null
}

interface PanGesture {
  anchorClientX: number
  anchorStart: number
  moved: boolean
  pannable: boolean
  pointerId: number
}

function assistantTimingDetail(
  metrics: AssistantMetricDetail | undefined,
): Pick<TimelineRecordDetail, 'ttftMs' | 'decodingMs'> {
  const start = metrics?.stepStartTime
  const first = metrics?.firstTokenTime
  const completed = metrics?.completedTime
  if (
    metrics?.timingRecorded !== true
    || typeof start !== 'number'
    || typeof first !== 'number'
    || typeof completed !== 'number'
    || !Number.isFinite(start)
    || !Number.isFinite(first)
    || !Number.isFinite(completed)
    || first < start
    || completed < first
  ) return {}
  return { ttftMs: first - start, decodingMs: completed - first }
}

function tokenUsageDetail(
  cell: TrajectoryCellProps,
): Pick<
  TimelineRecordDetail,
  'cachedInput' | 'cacheWriteInput' | 'processedInput' | 'outputTokens' | 'reasoningTokens'
> {
  const cachedInput = cell.cacheRead
  const processedInput = cell.input === undefined
    ? undefined
    : Math.max(0, cell.input - (cell.cacheRead ?? 0))
  return {
    ...(cachedInput === undefined ? {} : { cachedInput }),
    ...(cell.cacheWrite === undefined ? {} : { cacheWriteInput: cell.cacheWrite }),
    ...(processedInput === undefined ? {} : { processedInput }),
    ...(cell.output === undefined ? {} : { outputTokens: cell.output }),
    ...(cell.think === undefined ? {} : { reasoningTokens: cell.think }),
  }
}

function timelineRecordDetail(
  cell: TrajectoryCellProps,
  nowMilliseconds: number,
): TimelineRecordDetail {
  const elapsed = liveElapsedSeconds(cell, nowMilliseconds)
  const durationMs = elapsed === null || !Number.isFinite(elapsed)
    ? undefined
    : Math.max(0, elapsed * 1_000)
  const startedAt = cell.startedAt === null || !Number.isFinite(cell.startedAt)
    ? undefined
    : cell.startedAt
  return {
    ...(durationMs === undefined ? {} : { durationMs }),
    ...(startedAt === undefined ? {} : { startedAt }),
    ...assistantTimingDetail(cell.assistantMetrics),
    ...tokenUsageDetail(cell),
  }
}

function timelineKindLabel(kind: TrajectoryCellKind): string {
  switch (kind) {
    case 'system': return 'SYSTEM'
    case 'user': return 'USER'
    case 'context': return 'CONTEXT'
    case 'compacted': return 'COMPACTED'
    case 'message': return 'ASSISTANT'
    case 'tool': return 'TOOL'
    case 'subtool': return 'SUBTOOL'
  }
}

function formatRecordedTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
  })
}

function tokenTooltipLabel(
  segment: TrajectoryTimelineSegment,
  detail: TimelineRecordDetail | undefined,
): string {
  if (segment === 'input') {
    const processed = detail?.processedInput
    const cacheWrite = detail?.cacheWriteInput
    const cached = detail?.cachedInput
    const uncached = processed === undefined || cacheWrite === undefined
      ? null
      : `Uncached ${formatTokenCount(Math.max(0, processed - cacheWrite))}`
    return [
      'INPUT',
      [
        `Processed ${formatTokenCount(processed)}`,
        uncached,
        cacheWrite === undefined ? null : `Cache write ${formatTokenCount(cacheWrite)}`,
        cached === undefined ? null : `Cached ${formatTokenCount(cached)}`,
      ].filter(value => value !== null).join(' · '),
    ].join('\n')
  }
  const output = detail?.outputTokens
  const reasoning = detail?.reasoningTokens
  const content = output === undefined || reasoning === undefined
    ? null
    : `Content ${formatTokenCount(Math.max(0, output - reasoning))}`
  return [
    'OUTPUT',
    [
      `Total ${formatTokenCount(output)}`,
      reasoning === undefined ? null : `Reasoning ${formatTokenCount(reasoning)}`,
      content,
    ].filter(value => value !== null).join(' · '),
  ].join('\n')
}

function timelineTooltipLabel(
  kind: TrajectoryCellKind,
  detail: TimelineRecordDetail | undefined,
  segment: TrajectoryTimelineSegment | undefined,
): string {
  if (segment !== undefined) return tokenTooltipLabel(segment, detail)
  const heading = timelineKindLabel(kind)
  if (detail === undefined) return heading
  const duration = detail.durationMs === undefined
    ? null
    : `Total ${formatTimelineOffset(detail.durationMs)}`
  const range = detail.startedAt === undefined
    ? null
    : detail.durationMs === undefined
      ? `Started ${formatRecordedTime(detail.startedAt)}`
      : `${formatRecordedTime(detail.startedAt)} → ${formatRecordedTime(
        detail.startedAt + detail.durationMs,
      )}`
  const segments = detail.ttftMs === undefined || detail.decodingMs === undefined
    ? null
    : `TTFT ${formatTimelineOffset(detail.ttftMs)} · Decoding ${formatTimelineOffset(
      detail.decodingMs,
    )}`
  const timing = [duration, segments].filter(value => value !== null).join(' · ')
  return [heading, range, timing].filter(value => value !== null && value !== '').join('\n')
}

/** Props for the fixed full-domain overview above the trajectory ledger. */
export interface TrajectoryTimelineProps {
  turns: readonly TrajectoryTurnModel[]
  mode: TrajectoryTimelineMode
  range: TrajectoryTimeRange | null
  /** Whether one earlier history page request is pending anywhere. */
  olderHistoryLoading?: boolean
  /** Whether the loaded timeline omits an earlier history prefix. */
  hasEarlierRecords?: boolean
  /** Load one earlier history page from the truncation control. */
  onLoadEarlier?: () => Promise<boolean>
  selectedIndex?: number | null
  /** Record indexes matching the active ledger search, or null without a query. */
  searchMatchIndexes?: ReadonlySet<number> | null
  onRangeChange: (range: TrajectoryTimeRange | null) => void
  /** Select a directly clicked timeline block. */
  onRecordSelect?: (index: number) => void
  /** Bring the nearest record into view after clicking timeline whitespace. */
  onRecordFocus?: (index: number) => void
  /** Shared presentation clock used only for open running intervals. */
  nowMilliseconds?: number
}

function minimumZoomDomain(mode: TrajectoryTimelineMode): number {
  if (mode === 'sequence') return MINIMUM_ZOOM_OPERATIONS
  if (mode === 'tokens') return MINIMUM_ZOOM_TOKENS
  return MINIMUM_ZOOM_MILLIS
}

function spanKey(span: TrajectoryTimelineSpan): string {
  return `${span.lane}:${span.index}`
}

function spanCovers(span: TrajectoryTimelineSpan, index: number | null): boolean {
  if (index === null) return false
  return span.coveredIndexes === undefined
    ? span.index === index
    : span.index === index || span.coveredIndexes.includes(index)
}

function orderedRange(left: number, right: number): FractionRange {
  return left <= right ? { start: left, end: right } : { start: right, end: left }
}

function clampFraction(value: number): number {
  return Math.min(1, Math.max(0, value))
}

function centeredRange(
  center: number,
  width: number,
  minimum: number,
  maximum: number,
): FractionRange {
  const clampedWidth = Math.min(maximum - minimum, Math.max(0, width))
  const start = Math.min(
    Math.max(center - clampedWidth / 2, minimum),
    maximum - clampedWidth,
  )
  return { start, end: start + clampedWidth }
}

function rangeFraction(
  range: TrajectoryTimeRange,
  start: number,
  duration: number,
  minimum: number,
  maximum: number,
): FractionRange {
  const bounded = orderedRange(
    Math.min(maximum, Math.max(minimum, range.start)),
    Math.min(maximum, Math.max(minimum, range.end)),
  )
  return {
    start: (bounded.start - start) / duration,
    end: (bounded.end - start) / duration,
  }
}

function LaneLabels({ mode }: { mode: TrajectoryTimelineMode }) {
  if (mode === 'tokens') {
    return (
      <div className={css.labels} aria-hidden="true">
        <span>Input</span>
        <span>Output</span>
      </div>
    )
  }
  return (
    <div className={css.labels} aria-hidden="true">
      <span>Input</span>
      <span>Model</span>
      <span>Tools</span>
    </div>
  )
}

function EarlierHistoryBoundary({
  loading,
  onHover,
  onLoad,
}: {
  loading: boolean
  onHover: () => void
  onLoad: (() => void) | undefined
}) {
  return (
    <Tooltip
      label={loading ? 'Loading earlier history…' : 'Click to load earlier history'}
      side="right"
      delayMs={TIMELINE_TOOLTIP_DELAY_MS}
    >
      <button
        type="button"
        className={css.earlierHistory}
        data-earlier-history
        data-loading={loading || undefined}
        aria-label={loading ? 'Loading earlier history' : 'Load earlier history'}
        aria-disabled={loading || onLoad === undefined}
        disabled={loading || onLoad === undefined}
        onClick={onLoad}
        onPointerEnter={(event) => {
          event.stopPropagation()
          onHover()
        }}
        onPointerMove={(event) => { event.stopPropagation() }}
        onPointerDown={(event) => { event.stopPropagation() }}
      >
        …
      </button>
    </Tooltip>
  )
}

/** Overview renderer with drag ranges, click-sized focus, and Escape reset. */
export const TrajectoryTimeline = memo(function TrajectoryTimeline({
  turns,
  mode,
  range,
  olderHistoryLoading = false,
  hasEarlierRecords = false,
  onLoadEarlier,
  selectedIndex = null,
  searchMatchIndexes = null,
  onRangeChange,
  onRecordSelect,
  onRecordFocus,
  nowMilliseconds = Date.now(),
}: TrajectoryTimelineProps) {
  const model = useMemo(
    () => deriveTrajectoryTimeline(turns, mode, nowMilliseconds),
    [mode, nowMilliseconds, turns],
  )
  const detailByIndex = useMemo(
    () => new Map(turns.flatMap(turn =>
      turn.groups.flatMap(group =>
        group.cells.map(cell => [cell.index, timelineRecordDetail(cell, nowMilliseconds)] as const),
      ),
    )),
    [nowMilliseconds, turns],
  )
  const dragRef = useRef<{
    pointerId: number
    anchorTime: number
    anchorClientX: number
    recordIndex: number | null
    spanKey: string | null
  } | null>(null)
  const panRef = useRef<PanGesture | null>(null)
  const rootRef = useRef<HTMLElement | null>(null)
  const trackRef = useRef<HTMLDivElement | null>(null)
  const [draft, setDraft] = useState<TrajectoryTimeRange | null>(null)
  const [hover, setHover] = useState<HoverPoint | null>(null)
  const loadingEarlierRef = useRef<Promise<boolean> | null>(null)
  const [localOlderHistoryLoading, setLocalOlderHistoryLoading] = useState(false)
  useEffect(() => {
    loadingEarlierRef.current = null
    setLocalOlderHistoryLoading(false)
  }, [onLoadEarlier])
  const [panning, setPanning] = useState(false)
  const [viewport, setViewport] = useState<TrajectoryTimeRange | null>(null)
  const [animateViewport, setAnimateViewport] = useState(false)
  useEffect(() => {
    if (
      model !== null
      && range !== null
      && (range.end < model.start || range.start > model.end)
    ) {
      onRangeChange(null)
    }
  }, [model, onRangeChange, range])
  useEffect(() => {
    if (model === null) return
    setAnimateViewport(false)
    setViewport(current =>
      current !== null && (current.end < model.start || current.start > model.end)
        ? null
        : current)
  }, [model])
  useEffect(() => {
    if (model === null || selectedIndex === null) return
    const selectedSpan = model.spans.find(span => spanCovers(span, selectedIndex))
    if (selectedSpan === undefined) return
    setAnimateViewport(true)
    setViewport((current) => {
      if (current === null) return current
      if (
        selectedSpan.end > current.start
        && selectedSpan.start < current.end
      ) return current
      const duration = Math.max(1, current.end - current.start)
      const desiredStart = selectedSpan.end <= current.start
        ? selectedSpan.start
        : selectedSpan.end - duration
      const nextStart = Math.min(
        Math.max(desiredStart, model.start),
        Math.max(model.start, model.end - duration),
      )
      if (nextStart === current.start) return current
      return { start: nextStart, end: nextStart + duration }
    })
  }, [model, selectedIndex])
  const fullDuration = Math.max(1, (model?.end ?? 0) - (model?.start ?? 0))
  const viewportDuration = Math.min(
    fullDuration,
    Math.max(1, (viewport?.end ?? 0) - (viewport?.start ?? 0)),
  )
  const viewportStart = model === null || viewport === null
    ? model?.start ?? 0
    : Math.min(
      Math.max(viewport.start, model.start),
      model.end - viewportDuration,
    )
  const domainDuration = viewport === null ? fullDuration : viewportDuration
  const domainStart = viewport === null ? model?.start ?? 0 : viewportStart
  const showsEarlierBoundary = hasEarlierRecords
    && model !== null
    && domainStart === model.start
  const olderBusy = olderHistoryLoading || localOlderHistoryLoading
  const loadEarlier = onLoadEarlier === undefined || olderBusy
    ? undefined
    : () => {
      if (loadingEarlierRef.current !== null || olderHistoryLoading) return
      const operation = onLoadEarlier()
      loadingEarlierRef.current = operation
      setLocalOlderHistoryLoading(true)
      void operation.finally(() => {
        if (loadingEarlierRef.current !== operation) return
        loadingEarlierRef.current = null
        setLocalOlderHistoryLoading(false)
      })
    }
  const projectedDomainStyle = model === null
    ? undefined
    : {
      '--trajectory-domain-left':
        `${-(domainStart - model.start) / domainDuration * 100}%`,
      '--trajectory-domain-width': `${fullDuration / domainDuration * 100}%`,
    } as CSSProperties
  const committed = model === null || range === null
    ? null
    : rangeFraction(range, domainStart, domainDuration, model.start, model.end)
  const draftFraction = model === null || draft === null
    ? null
    : rangeFraction(draft, domainStart, domainDuration, model.start, model.end)
  const visibleRange = draftFraction ?? committed
  const activeRange = draft ?? range
  useEffect(() => {
    const root = rootRef.current
    if (root === null) return
    const onWheel = (event: globalThis.WheelEvent): void => {
      event.preventDefault()
      const track = trackRef.current
      if (track === null || model === null) return
      setAnimateViewport(false)
      const rect = track.getBoundingClientRect()
      const anchorFraction =
        clampFraction((event.clientX - rect.left) / Math.max(1, rect.width))
      const nextDuration = Math.min(
        fullDuration,
        Math.max(
          Math.min(minimumZoomDomain(mode), fullDuration),
          domainDuration * Math.exp(event.deltaY * 0.0015),
        ),
      )
      if (nextDuration >= fullDuration * 0.999) {
        setViewport(null)
        return
      }
      const anchorTime = domainStart + anchorFraction * domainDuration
      const nextStart = Math.min(
        Math.max(anchorTime - anchorFraction * nextDuration, model.start),
        model.end - nextDuration,
      )
      setViewport({ start: nextStart, end: nextStart + nextDuration })
    }
    root.addEventListener('wheel', onWheel, { passive: false })
    return () => { root.removeEventListener('wheel', onWheel) }
  }, [domainDuration, domainStart, fullDuration, mode, model])

  if (model === null) {
    return (
      <section ref={rootRef} className={css.root} aria-label="Trajectory timeline">
        <div className={css.plot}>
          <LaneLabels mode={mode} />
          <div className={css.track}>
            <span className={css.empty}>No timing data</span>
            {hasEarlierRecords && (
              <EarlierHistoryBoundary
                loading={olderBusy}
                onHover={() => { setHover(null) }}
                onLoad={loadEarlier}
              />
            )}
          </div>
        </div>
      </section>
    )
  }

  const minimumSelectionDuration = Math.min(
    domainDuration,
    fullDuration / model.spans.length,
  )

  const fractionAt = (event: PointerEvent<HTMLDivElement>): number => {
    const rect = event.currentTarget.getBoundingClientRect()
    return clampFraction((event.clientX - rect.left) / Math.max(1, rect.width))
  }

  const recordIndexAt = (event: PointerEvent<HTMLDivElement>): number | null => {
    const target = event.target instanceof HTMLElement ? event.target : null
    const value = target?.closest<HTMLElement>('[data-timeline-record-index]')
      ?.dataset.timelineRecordIndex
    if (value === undefined) return null
    const index = Number(value)
    return Number.isFinite(index) ? index : null
  }

  const spanKeyAt = (event: PointerEvent<HTMLDivElement>): string | null => {
    const target = event.target instanceof HTMLElement ? event.target : null
    return target?.closest<HTMLElement>('[data-timeline-span-key]')
      ?.dataset.timelineSpanKey ?? null
  }

  const commit = (nextRange: TrajectoryTimeRange) => {
    onRangeChange(nextRange)
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button === 2) {
      panRef.current = {
        anchorClientX: event.clientX,
        anchorStart: domainStart,
        moved: false,
        pannable: viewport !== null,
        pointerId: event.pointerId,
      }
      if (viewport !== null) setAnimateViewport(false)
      setPanning(true)
      if (typeof event.currentTarget.setPointerCapture === 'function') {
        event.currentTarget.setPointerCapture(event.pointerId)
      }
      return
    }
    if (event.button !== 0) return
    const anchor = fractionAt(event)
    const anchorTime = domainStart + anchor * domainDuration
    const recordIndex = recordIndexAt(event)
    setHover({ fraction: anchor, recordIndex })
    dragRef.current = {
      pointerId: event.pointerId,
      anchorTime,
      anchorClientX: event.clientX,
      recordIndex,
      spanKey: spanKeyAt(event),
    }
    if (typeof event.currentTarget.setPointerCapture === 'function') {
      event.currentTarget.setPointerCapture(event.pointerId)
    }
    setDraft({ start: anchorTime, end: anchorTime })
  }

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const fraction = fractionAt(event)
    setHover({ fraction, recordIndex: recordIndexAt(event) })
    const pan = panRef.current
    if (pan !== null && pan.pointerId === event.pointerId) {
      if (Math.abs(event.clientX - pan.anchorClientX) >= MINIMUM_DRAG_PX) {
        pan.moved = true
      }
      if (!pan.pannable) return
      const delta = (event.clientX - pan.anchorClientX) / Math.max(1, rect.width)
      const nextStart = Math.min(
        Math.max(pan.anchorStart - delta * domainDuration, model.start),
        model.end - domainDuration,
      )
      setViewport({ start: nextStart, end: nextStart + domainDuration })
      return
    }
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    let nextDomainStart = domainStart
    if (viewport !== null) {
      const localX = event.clientX - rect.left
      const edgeWidth = Math.min(
        MAXIMUM_EDGE_PAN_PX,
        Math.max(1, rect.width * EDGE_PAN_ZONE_FRACTION),
      )
      const direction = localX < edgeWidth
        ? -1
        : localX > rect.width - edgeWidth ? 1 : 0
      if (direction !== 0) {
        const edgeDistance = direction < 0
          ? edgeWidth - localX
          : localX - (rect.width - edgeWidth)
        const strength = clampFraction(edgeDistance / edgeWidth)
        const desiredStart = domainStart
          + direction * domainDuration * EDGE_PAN_STEP_FRACTION
          * Math.max(0.2, strength)
        nextDomainStart = Math.min(
          Math.max(desiredStart, model.start),
          model.end - domainDuration,
        )
        if (nextDomainStart !== domainStart) {
          setAnimateViewport(false)
          setViewport({
            start: nextDomainStart,
            end: nextDomainStart + domainDuration,
          })
        }
      }
    }
    const pointTime = nextDomainStart + fraction * domainDuration
    setDraft(orderedRange(drag.anchorTime, pointTime))
  }

  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current
    if (pan !== null && pan.pointerId === event.pointerId) {
      const moved = pan.moved
        || Math.abs(event.clientX - pan.anchorClientX) >= MINIMUM_DRAG_PX
      panRef.current = null
      setPanning(false)
      if (!moved) onRangeChange(null)
      return
    }
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    const pointFraction = fractionAt(event)
    const pointTime = domainStart + pointFraction * domainDuration
    const selected = orderedRange(drag.anchorTime, pointTime)
    setHover({ fraction: pointFraction, recordIndex: recordIndexAt(event) })
    dragRef.current = null
    setDraft(null)
    const click = Math.abs(event.clientX - drag.anchorClientX) < MINIMUM_DRAG_PX
    const clickedSpan = click && drag.spanKey !== null
      ? model.spans.find(span => spanKey(span) === drag.spanKey)
      : undefined
    if (clickedSpan !== undefined) {
      // An Input block stands for the records its request consumed, so clicking it
      // focuses that whole stretch of the ledger instead of the request row itself.
      if (clickedSpan.segment === 'input') {
        onRangeChange({ start: clickedSpan.start, end: clickedSpan.end })
        onRecordFocus?.(clickedSpan.coveredIndexes?.[0] ?? clickedSpan.index)
        return
      }
      onRangeChange(null)
      onRecordSelect?.(clickedSpan.index)
      return
    }
    const committedRange = selected.end - selected.start < minimumSelectionDuration
      ? centeredRange(
        click ? selected.start : (selected.start + selected.end) / 2,
        minimumSelectionDuration,
        model.start,
        model.end,
      )
      : selected
    commit(committedRange)
    if (click) {
      const timelinePoint = selected.start
      const nearest = model.spans.reduce((candidate, span) => {
        const candidateDistance = timelinePoint < candidate.start
          ? candidate.start - timelinePoint
          : timelinePoint > candidate.end ? timelinePoint - candidate.end : 0
        const spanDistance = timelinePoint < span.start
          ? span.start - timelinePoint
          : timelinePoint > span.end ? timelinePoint - span.end : 0
        return spanDistance < candidateDistance ? span : candidate
      })
      onRecordFocus?.(nearest.index)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape' || range === null) return
    event.preventDefault()
    onRangeChange(null)
  }

  const onPointerCancel = () => {
    dragRef.current = null
    panRef.current = null
    setDraft(null)
    setHover(null)
    setPanning(false)
  }

  return (
    <section ref={rootRef} className={css.root} aria-label="Trajectory timeline">
      <div className={css.plot}>
        <LaneLabels mode={mode} />
        <div
          ref={trackRef}
          className={css.track}
          data-panning={panning || undefined}
          aria-label="Timeline overview; drag horizontally to focus events"
          tabIndex={0}
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerCancel}
          onPointerLeave={() => {
            if (dragRef.current === null && panRef.current === null) setHover(null)
          }}
          onDoubleClick={(event) => {
            event.preventDefault()
            onRangeChange(null)
          }}
          onContextMenu={(event) => {
            event.preventDefault()
          }}
        >
          {showsEarlierBoundary && (
            <EarlierHistoryBoundary
              loading={olderBusy}
              onHover={() => { setHover(null) }}
              onLoad={loadEarlier}
            />
          )}
          {hover !== null && hover.recordIndex === null && draft === null && (
            <div
              className={css.hoverLine}
              data-timeline-hover-line
              aria-hidden="true"
              style={{
                '--trajectory-hover-left': `${hover.fraction * 100}%`,
              } as CSSProperties}
            />
          )}
          {visibleRange !== null && (
            <>
              <div
                className={css.selection}
                data-dragging={draft === null ? undefined : 'true'}
                aria-hidden="true"
                style={{
                  '--trajectory-selection-left': `${visibleRange.start * 100}%`,
                  '--trajectory-selection-width': `${(visibleRange.end - visibleRange.start) * 100}%`,
                } as CSSProperties}
              />
              <div
                className={css.selectionEdges}
                data-dragging={draft === null ? undefined : 'true'}
                aria-hidden="true"
                style={{
                  '--trajectory-selection-left': `${visibleRange.start * 100}%`,
                  '--trajectory-selection-width': `${(visibleRange.end - visibleRange.start) * 100}%`,
                } as CSSProperties}
              />
            </>
          )}
          <div
            className={css.turnBoundaries}
            data-animate-viewport={animateViewport || undefined}
            aria-hidden="true"
            style={projectedDomainStyle}
          >
            {model.turnBoundaries
              .filter(boundary =>
                boundary.time > model.start
                && boundary.time >= domainStart
                && boundary.time <= domainStart + domainDuration)
              .map(boundary => (
                <span
                  className={css.turnBoundary}
                  data-turn={boundary.turn}
                  key={boundary.turn}
                  style={{
                    '--trajectory-turn-left':
                      `${(boundary.time - model.start) / fullDuration * 100}%`,
                  } as CSSProperties}
                />
              ))}
          </div>
          <div
            className={css.lanes}
            data-animate-viewport={animateViewport || undefined}
            data-timeline-domain
            style={projectedDomainStyle}
          >
            {model.spans
              .filter(span =>
                spanCovers(span, selectedIndex)
                || (span.end >= domainStart && span.start <= domainStart + domainDuration))
              .map((span) => {
                const left = (span.start - model.start) / fullDuration
                const width = (span.end - span.start) / fullDuration
                const widthPercent = width * 100
                const detail = detailByIndex.get(span.index)
                const ttftMs = detail?.ttftMs
                const decodingMs = detail?.decodingMs
                const ttftFraction = ttftMs === undefined
                  || decodingMs === undefined
                  || ttftMs + decodingMs <= 0
                  ? null
                  : ttftMs / (ttftMs + decodingMs)
                const splitFraction = mode === 'tokens'
                  ? span.splitFraction ?? null
                  : ttftFraction
                return (
                  <Tooltip
                    key={spanKey(span)}
                    label={() => timelineTooltipLabel(span.kind, detail, span.segment)}
                    side="bottom"
                    delayMs={TIMELINE_TOOLTIP_DELAY_MS}
                  >
                    <span
                      aria-hidden="true"
                      className={css.span}
                      data-timeline-span={span.kind}
                      data-timeline-record-index={span.index}
                      data-timeline-span-key={spanKey(span)}
                      data-timeline-segment={span.segment}
                      data-span-split={splitFraction === null ? undefined : 'true'}
                      data-error={span.isError || undefined}
                      data-equal-duration={mode === 'time' || undefined}
                      data-current={spanCovers(span, selectedIndex) || undefined}
                      data-hovered={hover?.recordIndex === span.index || undefined}
                      data-search-match={searchMatchIndexes === null
                        ? undefined
                        : searchMatchIndexes.has(span.index) ? 'true' : 'false'}
                      data-selected={activeRange === null
                        ? undefined
                        : span.start <= activeRange.end && span.end >= activeRange.start
                          ? 'true'
                          : 'false'}
                      style={{
                        '--trajectory-span-left': `${left * 100}%`,
                        '--trajectory-span-width': `${widthPercent}%`,
                        '--trajectory-span-gap': `min(${widthPercent * 0.08}%, 1px)`,
                        '--trajectory-span-lane': span.lane,
                        ...(splitFraction === null
                          ? {}
                          : { '--trajectory-span-split': `${splitFraction * 100}%` }),
                      } as CSSProperties}
                    />
                  </Tooltip>
                )
              })}
          </div>
        </div>
      </div>
    </section>
  )
})
