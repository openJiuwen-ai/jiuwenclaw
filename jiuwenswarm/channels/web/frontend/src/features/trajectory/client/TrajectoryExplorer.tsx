// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** Standalone trajectory explorer assembled from the DSH presentation components. */

import {
  useCallback, useEffect, useMemo, useState, type CSSProperties,
} from 'react'
import type {
  TrajectoryRequest, TrajectorySnapshot, TrajectoryTurnModel,
} from '../trajectory/model.ts'
import { trajectoryRecordId } from '../trajectory/record.ts'
import {
  trajectoryTimelineFocusIndexes,
  type TrajectoryTimelineMode,
  type TrajectoryTimeRange,
} from '../trajectory/timeline.ts'
import { TrajectorySearchIndex } from '../trajectory/search-index.ts'
import {
  TrajectoryThemeProvider, type TrajectoryColorMode,
} from '../theme/context.tsx'
import { TrajectoryTable } from './TrajectoryTable.tsx'
import { TrajectoryTimeline } from './TrajectoryTimeline.tsx'
import { TrajectoryToolbar } from './TrajectoryToolbar.tsx'
import {
  trajectoryTranslator, type TrajectoryKey, type TrajectoryTranslate,
} from './i18n.ts'
import css from './TrajectoryExplorer.module.css'

const EMPTY_TURN_IDS: ReadonlySet<number> = new Set()
const EMPTY_RECORD_IDS: ReadonlySet<string> = new Set()

interface ExplorerStyle extends CSSProperties {
  '--trajectory-bottom-inset'?: string
}

/** Ordinary React props; no Cordis, Session, slot, or locale service is required. */
export interface TrajectoryExplorerProps {
  /** Atomic read model produced by an OTel projector or a static fixture. */
  snapshot?: TrajectorySnapshot
  /** Static grouped records; ignored when `snapshot` is supplied. */
  turns?: readonly TrajectoryTurnModel[]
  /** Request-inspector metadata for static `turns`. */
  requests?: readonly TrajectoryRequest[]
  /** Whether the initial tail has not reached the browser yet. */
  loading?: boolean
  /** Whether one older page request is pending. */
  loadingEarlier?: boolean
  /** Whether an earlier page exists. */
  hasEarlier?: boolean
  /** Prepend one earlier page. */
  loadEarlier?: () => Promise<boolean>
  /** Retryable store or transport failure shown without hiding loaded records. */
  error?: string | null
  /** Optional inspector handoff using the projected tool call id. */
  inspectCallId?: string | null
  /** Called after an inspect handoff is consumed or cannot be resolved. */
  onInspectApplied?: () => void
  /** Override individual toolbar labels. */
  messages?: Partial<Record<TrajectoryKey, string>>
  /** Full custom translator; takes precedence over `messages`. */
  translate?: TrajectoryTranslate
  /** Space reserved for a host-owned bottom overlay. */
  bottomInset?: number | string
  /** Local viewer palette. The host remains responsible for choosing it. */
  colorMode?: TrajectoryColorMode
  className?: string
}

function firstSourceSeq(turns: readonly TrajectoryTurnModel[]): number | undefined {
  for (const turn of turns) {
    for (const group of turn.groups) {
      for (const cell of group.cells) {
        if (cell.sourceSeq !== undefined) return cell.sourceSeq
      }
    }
  }
  return undefined
}

function searchIndexes(
  index: TrajectorySearchIndex,
  turns: readonly TrajectoryTurnModel[],
  query: string,
): ReadonlySet<number> | null {
  index.update([turns])
  const ids = index.search(query)
  if (ids === null) return null
  const matches = new Set<number>()
  for (const turn of turns) {
    for (const group of turn.groups) {
      for (const cell of group.cells) {
        if (ids.has(trajectoryRecordId(cell))) matches.add(cell.index)
      }
    }
  }
  return matches
}

/** Render the full toolbar, overview timeline, virtual ledger, and inspector. */
export function TrajectoryExplorer({
  snapshot,
  turns: staticTurns = [],
  requests: staticRequests,
  loading = false,
  loadingEarlier = false,
  hasEarlier = false,
  loadEarlier,
  error = null,
  inspectCallId = null,
  onInspectApplied,
  messages,
  translate,
  bottomInset = 0,
  colorMode = 'light',
  className,
}: TrajectoryExplorerProps) {
  const turns = snapshot?.turns ?? staticTurns
  const requests = snapshot?.requests ?? staticRequests
  const streamingCells = snapshot?.streamingCells
  const hasRunningCells = useMemo(() => turns.some(turn => (
    turn.groups.some(group => group.cells.some(cell => cell.status === 'running'))
  )), [turns])
  const [liveNowMilliseconds, setLiveNowMilliseconds] = useState(() => Date.now())
  useEffect(() => {
    if (!hasRunningCells) return undefined
    const update = () => setLiveNowMilliseconds(Date.now())
    update()
    const interval = window.setInterval(update, 250)
    const onVisibility = () => {
      if (document.visibilityState === 'visible') update()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [hasRunningCells])
  const [actualDuration, setActualDuration] = useState(false)
  const [actualTime, setActualTime] = useState(false)
  const [collapsedTurns, setCollapsedTurns] = useState<ReadonlySet<number>>(EMPTY_TURN_IDS)
  const [collapsedAssistants, setCollapsedAssistants] =
    useState<ReadonlySet<string>>(EMPTY_RECORD_IDS)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchIndex] = useState(() => new TrajectorySearchIndex())
  const [timelineRange, setTimelineRange] = useState<TrajectoryTimeRange | null>(null)
  const [selectedTimelineIndex, setSelectedTimelineIndex] = useState<number | null>(null)
  const [recordSelection, setRecordSelection] = useState<{ readonly index: number } | null>(null)
  const [recordFocus, setRecordFocus] = useState<{ readonly index: number } | null>(null)
  const t = useMemo(
    () => translate ?? trajectoryTranslator(messages),
    [messages, translate],
  )
  const timelineMode: TrajectoryTimelineMode = actualDuration
    ? actualTime ? 'actual' : 'duration'
    : actualTime ? 'time' : 'sequence'
  const searchMatchIndexes = useMemo(
    () => searchIndexes(searchIndex, turns, searchQuery),
    [searchIndex, searchQuery, turns],
  )
  const timelineFocusIndexes = useMemo(
    () => timelineRange === null
      ? null
      : trajectoryTimelineFocusIndexes(
        turns,
        timelineRange,
        timelineMode,
        liveNowMilliseconds,
      ),
    [liveNowMilliseconds, timelineMode, timelineRange, turns],
  )
  const collapsibleTurnIds = useMemo(
    () => turns.flatMap(turn => turn.turn !== null && turn.groups.reduce(
      (count, group) => count + group.cells.filter(cell =>
        cell.requestOnly !== true && cell.kind !== 'system').length,
      0,
    ) > 1 ? [turn.turn] : []),
    [turns],
  )
  const collapsibleAssistantIds = useMemo(() => {
    const ids: string[] = []
    for (const turn of turns) {
      const cells = turn.groups.flatMap(group => group.cells)
      for (let index = 0; index < cells.length; index++) {
        const cell = cells[index]
        const next = cells[index + 1]
        if (cell?.kind === 'message' && (next?.kind === 'tool' || next?.kind === 'subtool')) {
          ids.push(trajectoryRecordId(cell))
        }
      }
    }
    return ids
  }, [turns])
  const allTurnsCollapsed = collapsibleTurnIds.length > 0
    && collapsibleTurnIds.every(turn => collapsedTurns.has(turn))
  const allAssistantsCollapsed = collapsibleAssistantIds.length > 0
    && collapsibleAssistantIds.every(id => collapsedAssistants.has(id))
  const toggleTurn = useCallback((turn: number) => {
    setCollapsedTurns((current) => {
      const next = new Set(current)
      if (next.has(turn)) next.delete(turn)
      else next.add(turn)
      return next
    })
  }, [])
  const toggleAssistant = useCallback((id: string) => {
    setCollapsedAssistants((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const toggleAllTurns = useCallback(() => {
    setCollapsedTurns((current) => {
      const next = new Set(current)
      for (const turn of collapsibleTurnIds) {
        if (allTurnsCollapsed) next.delete(turn)
        else next.add(turn)
      }
      return next
    })
  }, [allTurnsCollapsed, collapsibleTurnIds])
  const toggleAllAssistants = useCallback(() => {
    setCollapsedAssistants((current) => {
      const next = new Set(current)
      for (const id of collapsibleAssistantIds) {
        if (allAssistantsCollapsed) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }, [allAssistantsCollapsed, collapsibleAssistantIds])
  const rootStyle: ExplorerStyle = {
    '--trajectory-bottom-inset': typeof bottomInset === 'number'
      ? `${bottomInset}px`
      : bottomInset,
  }

  return (
    <TrajectoryThemeProvider colorMode={colorMode}>
      <div
        className={[css.root, 'jiuwenTrajectoryTheme', className].filter(Boolean).join(' ')}
        data-trajectory-theme={colorMode}
        style={rootStyle}
      >
      <TrajectoryToolbar
        actualDuration={actualDuration}
        onActualDurationChange={(value) => { setActualDuration(value); setTimelineRange(null) }}
        actualTime={actualTime}
        onActualTimeChange={(value) => { setActualTime(value); setTimelineRange(null) }}
        allTurnsCollapsed={allTurnsCollapsed}
        onToggleAllTurns={toggleAllTurns}
        allAssistantsCollapsed={allAssistantsCollapsed}
        onToggleAllAssistants={toggleAllAssistants}
        searchQuery={searchQuery}
        onSearchQueryChange={setSearchQuery}
        t={t}
      />
      <TrajectoryTimeline
        turns={turns}
        mode={timelineMode}
        range={timelineRange}
        olderHistoryLoading={loadingEarlier}
        hasEarlierRecords={hasEarlier}
        {...(loadEarlier === undefined ? {} : { onLoadEarlier: loadEarlier })}
        selectedIndex={selectedTimelineIndex}
        searchMatchIndexes={searchMatchIndexes}
        onRangeChange={setTimelineRange}
        onRecordSelect={(index) => {
          setTimelineRange(null)
          setRecordSelection({ index })
          setSelectedTimelineIndex(index)
        }}
        onRecordFocus={index => { setRecordFocus({ index }) }}
        nowMilliseconds={liveNowMilliseconds}
      />
      {error !== null && (
        <div className={css.error} role="status">{error}</div>
      )}
      <div className={css.ledger}>
        <TrajectoryTable
          turns={turns}
          {...(requests === undefined ? {} : { requestNumbers: requests })}
          {...(streamingCells === undefined ? {} : { streamingCells })}
          timelineFocusIndexes={timelineFocusIndexes}
          searchMatchIndexes={searchMatchIndexes}
          onSelectedIndexChange={setSelectedTimelineIndex}
          onRecordSelect={(index) => {
            if (timelineFocusIndexes !== null && !timelineFocusIndexes.has(index)) {
              setTimelineRange(null)
            }
          }}
          recordSelection={recordSelection}
          recordFocus={recordFocus}
          historyLoading={loading}
          olderHistoryLoading={loadingEarlier}
          historyStartSeq={firstSourceSeq(turns)}
          hasOlderRecords={hasEarlier}
          {...(loadEarlier === undefined ? {} : { onLoadOlder: loadEarlier })}
          onClearSelection={() => { setTimelineRange(null) }}
          collapsedTurns={collapsedTurns}
          onToggleTurn={toggleTurn}
          collapsedAssistants={collapsedAssistants}
          onToggleAssistant={toggleAssistant}
          inspectCallId={inspectCallId}
          nowMilliseconds={liveNowMilliseconds}
          {...(onInspectApplied === undefined ? {} : { onInspectApplied })}
        />
      </div>
      </div>
    </TrajectoryThemeProvider>
  )
}
