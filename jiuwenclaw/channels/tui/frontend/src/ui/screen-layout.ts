import type { AppSnapshot } from "../app-state.js";
import { isTeamMode } from "../core/modes.js";
import { renderMiniTeamTree, renderTeamPanel } from "./components/team-panel.js";
import { isTeamWorking } from "./components/team-shared.js";
import { renderTeamStatusPill } from "./components/team-status-pill.js";
import { renderTodoList } from "./components/todo-list.js";
import { APP_SCREEN_KEY_BINDINGS } from "./keymap.js";
import { padToWidth } from "./rendering/text.js";
import { palette } from "./theme.js";
import { buildTranscriptLines } from "./transcript-renderer.js";
import { loadTuiConfig } from "../core/tui-config-store.js";

export interface ScreenLayoutOptions {
  width: number;
  height?: number;
  questionLines: string[];
  editorLines: string[];
  composerPreviewLines: string[];
  pendingInput?: string;
  pendingInputBaseline?: number;
  showFullThinking: boolean;
  showToolDetails: boolean;
  showShortcutHelp: boolean;
  todosCollapsed: boolean;
  showTeamPanel: boolean;
  selectedTeamMemberId: string | null;
  viewedTeamMemberId: string | null;
  transientNotice: string | null;
  animationPhase: number;
  runningElapsedMs?: number;
  transcriptScrollOffset?: number;
  onTranscriptScrollOffsetChange?: (offset: number) => void;
}

function formatSubtaskStatus(status: string): string {
  switch (status) {
    case "starting":
      return "starting";
    case "tool_call":
      return "tool";
    case "tool_result":
      return "result";
    case "completed":
      return "done";
    case "error":
      return "error";
    default:
      return status;
  }
}

function formatElapsed(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return "0s";
  }
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function renderRunningStatus(animationPhase: number, elapsedMs: number | undefined): string {
  const label = "Working";
  const sweep = animationPhase % (label.length + 3);
  const focus = sweep - 1;
  const animatedLabel = label
    .split("")
    .map((char, index) => {
      const distance = Math.abs(index - focus);
      if (distance === 0) return palette.text.assistant(char);
      if (distance === 1) return palette.text.dim(char);
      return palette.text.subtle(char);
    })
    .join("");
  return `• ${animatedLabel} (${formatElapsed(elapsedMs)} • esc to interrupt)`;
}

function renderInterruptedStatus(): string {
  return "• Interrupted";
}

function connectionStatusLabel(status: AppSnapshot["connectionStatus"]): string | null {
  switch (status) {
    case "connecting":
      return "connecting to backend";
    case "reconnecting":
      return "backend unavailable · retrying";
    case "auth_failed":
      return "auth failed";
    case "idle":
      return "backend unavailable";
    case "connected":
    default:
      return null;
  }
}

function buildStatusLines(
  snapshot: AppSnapshot,
  width: number,
  transientNotice: string | null,
  animationPhase: number,
  runningElapsedMs: number | undefined,
): string[] {
  const left: string[] = [];
  const connectionLabel = connectionStatusLabel(snapshot.connectionStatus);
  if (connectionLabel) left.push(connectionLabel);
  if (snapshot.sessionTitle) {
    // Lowercase "(Branch)" / "(Branch N)" for the status bar — less
    // prominent than the uppercase metadata version used in /resume list.
    const raw = snapshot.sessionTitle.replace("(Branch", "(branch");
    const displayTitle = raw.length > 30 ? raw.slice(0, 30) + "..." : raw;
    left.push(displayTitle);
  }
  if (snapshot.mode !== "agent.plan") left.push(`mode:${snapshot.mode}`);
  if (snapshot.transcriptFoldMode !== "none") left.push(`fold:${snapshot.transcriptFoldMode}`);
  const teamWorking =
    isTeamMode(snapshot.mode) &&
    isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);

  const right = snapshot.lastError
    ? `error:${snapshot.lastError}`
    : snapshot.isInterrupted
      ? renderInterruptedStatus()
    : snapshot.isPaused
      ? "paused"
      : snapshot.isProcessing || teamWorking
        ? renderRunningStatus(animationPhase, runningElapsedMs)
        : null;

  const lines = transientNotice ? [padToWidth(palette.status.warning(transientNotice), width)] : [];
  const leadSubtask = snapshot.activeSubtasks[0];

  const content = right ? [...left, right].join(" | ") : left.join(" | ");
  if (content) {
    lines.push(padToWidth(palette.text.dim(content), width));
  }

  if (leadSubtask) {
    const parts = [
      `subtask ${leadSubtask.index}/${leadSubtask.total || "?"}`,
      formatSubtaskStatus(leadSubtask.status),
      leadSubtask.description || leadSubtask.task_id,
    ];
    if (leadSubtask.tool_name) parts.push(leadSubtask.tool_name);
    if (leadSubtask.message) parts.push(leadSubtask.message);
    if (snapshot.activeSubtasks.length > 1)
      parts.push(`+${snapshot.activeSubtasks.length - 1} more`);
    lines.push(padToWidth(palette.text.dim(parts.join(" | ")), width));
  } else if (snapshot.evolutionStatus === "running") {
    lines.push(padToWidth(palette.text.dim("evolution | running"), width));
  }
  return lines;
}

function buildStatusLineBar(snapshot: AppSnapshot, width: number): string[] {
  if (!snapshot.statusLineText) return [];
  const config = loadTuiConfig();
  const sl = config.statusLine;
  const paddingX = sl?.padding ?? 0;
  const paddedWidth = width - paddingX * 2;
  if (paddedWidth <= 0) return [];
  const text = snapshot.statusLineText.length > paddedWidth
    ? snapshot.statusLineText.slice(0, paddedWidth)
    : snapshot.statusLineText;
  return [padToWidth(palette.text.dim(text), paddedWidth)];
}

function buildShortcutLines(width: number): string[] {
  const lines = [
    padToWidth(palette.text.secondary("Shortcuts"), width),
    ...APP_SCREEN_KEY_BINDINGS.map((binding) =>
      padToWidth(palette.text.dim(`${binding.label} | ${binding.description}`), width),
    ),
    padToWidth(palette.text.dim("/help | show slash commands"), width),
    " ".repeat(width),
  ];
  return lines;
}

export function buildAppScreenLines(snapshot: AppSnapshot, options: ScreenLayoutOptions): string[] {
  const statusLines = buildStatusLines(
    snapshot,
    options.width,
    options.transientNotice,
    options.animationPhase,
    options.runningElapsedMs,
  );
  const shortcutLines = options.showShortcutHelp ? buildShortcutLines(options.width) : [];
  const statusLineBarLines = buildStatusLineBar(snapshot, options.width);

  // When a custom statusline bar is active, replace the built-in status lines
  // to avoid redundant information (both show session name, mode, etc.)
  const effectiveStatusLines = statusLineBarLines.length > 0 ? [] : statusLines;

  const transcriptLines = buildTranscriptLines(
    snapshot,
    options.width,
    options.showFullThinking,
    options.showToolDetails,
    options.animationPhase,
    options.pendingInput,
    options.pendingInputBaseline,
  );
  const todoLines = renderTodoList(snapshot.todos, options.width, options.todosCollapsed, options.animationPhase);
  const hasTeamActivity =
    isTeamMode(snapshot.mode) ||
    snapshot.teamMemberEvents.length > 0 ||
    snapshot.teamTaskEvents.length > 0 ||
    snapshot.teamMessageEvents.length > 0;
  const teamStatusLines =
    hasTeamActivity
      ? renderTeamStatusPill(
          snapshot.teamMemberEvents,
          snapshot.teamTaskEvents,
          snapshot.teamMessageEvents,
          options.width,
        )
      : [];
  const teamPanelLines =
    options.showTeamPanel && hasTeamActivity
      ? renderTeamPanel(
          snapshot.teamMemberEvents,
          snapshot.teamTaskEvents,
          snapshot.teamMessageEvents,
          options.width,
          options.selectedTeamMemberId,
          options.viewedTeamMemberId,
        )
      : [];
  const miniTeamTreeLines =
    !options.showTeamPanel && hasTeamActivity
      ? renderMiniTeamTree(
          snapshot.teamMemberEvents,
          snapshot.teamTaskEvents,
          snapshot.teamMessageEvents,
          options.width,
        )
      : [];
  const fixedLines = [
    ...todoLines,
    ...(todoLines.length > 0 &&
    (teamStatusLines.length > 0 || miniTeamTreeLines.length > 0 || teamPanelLines.length > 0)
      ? [" ".repeat(options.width)]
      : []),
    ...teamStatusLines,
    ...miniTeamTreeLines,
    ...teamPanelLines,
    ...options.questionLines,
    ...options.editorLines,
    ...options.composerPreviewLines,
    ...effectiveStatusLines,
    ...statusLineBarLines,
    ...shortcutLines,
  ];
  const height = Math.floor(options.height ?? 0);
  if (height <= 0) {
    return [...transcriptLines, ...fixedLines];
  }
  if (fixedLines.length >= height) {
    if ((options.transcriptScrollOffset ?? 0) !== 0) {
      options.onTranscriptScrollOffsetChange?.(0);
    }
    return fixedLines.slice(-height);
  }

  const transcriptHeight = height - fixedLines.length;
  if (transcriptLines.length <= transcriptHeight) {
    if ((options.transcriptScrollOffset ?? 0) !== 0) {
      options.onTranscriptScrollOffsetChange?.(0);
    }
    return [...transcriptLines, ...fixedLines];
  }

  const requestedOffset = Math.max(0, Math.floor(options.transcriptScrollOffset ?? 0));
  const teamWorking =
    isTeamMode(snapshot.mode) &&
    isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
  const liveTranscript = snapshot.isProcessing || snapshot.isPaused || teamWorking;
  if (requestedOffset === 0 && !liveTranscript) {
    return [...transcriptLines, ...fixedLines];
  }

  const maxOffset = transcriptLines.length - transcriptHeight;
  const offset = Math.min(maxOffset, requestedOffset);
  if (offset !== requestedOffset) {
    options.onTranscriptScrollOffsetChange?.(offset);
  }
  const start = transcriptLines.length - transcriptHeight - offset;
  return [...transcriptLines.slice(start, start + transcriptHeight), ...fixedLines];
}
