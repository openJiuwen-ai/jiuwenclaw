import {
  CombinedAutocompleteProvider,
  Editor,
  SelectList,
  type SelectItem,
  type AutocompleteItem,
  type AutocompleteProvider,
  type Component,
  type Focusable,
  type SlashCommand as TuiSlashCommand,
  TUI,
  matchesKey,
  decodeKittyPrintable,
  truncateToWidth,
} from "@mariozechner/pi-tui";
import { spawnSync } from "node:child_process";
import { statSync } from "node:fs";
import type { CliPiAppState } from "../app-state.js";
import { openFileInEditor as openInExternalEditor } from "../core/utils/editor.js";
import {
  extractAttachmentsFromText,
  extractFilePathsFromPaste,
  findAttachmentTokenAtCursor,
  formatAttachmentMention,
  isImageAttachment,
  isSupportedAttachment,
  syncComposerImageTokens,
} from "../core/attachments.js";
import {
  CommandService,
  parseSlashCommand,
  type InstalledSkillEntry,
} from "../core/commands/CommandService.js";
import type { SlashCommand } from "../core/commands/types.js";
import { addCommandEcho, addError, addInfo } from "../core/commands/helpers.js";
import type { FileAttachment } from "../core/protocol.js";
import {
  type ModelListPayload,
  isReservedMultimodalModelKey,
} from "../core/commands/builtins/model.js";
import type { SessionListPayload, SessionMeta } from "../core/commands/builtins/resume.js";
import type { ConfigItemSchema } from "../core/commands/builtins/config.js";
import type { McpListItem, McpListPayload } from "../core/commands/builtins/mcp.js";
import { buildModeAutocompleteItems } from "../core/commands/builtins/mode.js";
import { isTeamMode } from "../core/modes.js";
import { addTrustedDir, getTrustedDirs, isTrustedDir } from "../core/tui-trusted-dirs-store.js";
import { handleAppScreenKeyInput } from "./keymap.js";
import { buildAppScreenLines } from "./screen-layout.js";
import {
  isTeamWorking,
  orderedMemberIds,
  teamWorkingStartedAtMs,
} from "./components/team-shared.js";
import { padToWidth } from "./rendering/text.js";
import { editorTheme, palette, selectListTheme } from "./theme.js";

const END_CURSOR = "\x1b[7m \x1b[0m";
const PERMISSION_TOOL_RE = /工具\s+`([^`]+)`\s+需要授权/;
const PERMISSION_RISK_RE = /安全风险评估：\**\s*([^\s*]+)?\s*\**([^*\n]+?风险)\**/m;
const PERMISSION_QUOTE_RE = /^>\s*(.+)$/gm;
const PERMISSION_JSON_BLOCK_RE = /```json\s*([\s\S]*?)\s*```/i;

function wrapText(text: string, maxWidth: number): string[] {
  if (maxWidth < 1) return [text];
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    if (!word) continue;
    const test = current ? `${current} ${word}` : word;
    if (test.length > maxWidth && current) {
      lines.push(current);
      current = word;
    } else {
      current = test;
    }
  }
  if (current) lines.push(current);
  return lines.length > 0 ? lines : [""];
}
const RUNNING_TIMER_RESET_GRACE_MS = 15_000;

type PermissionSummary = {
  tool?: string;
  risk?: string;
  reason?: string;
  command?: string;
  description?: string;
};

type ResumeSessionListState = {
  list: SelectList;
  sessions: SessionMeta[];
  total: number;
  searchQuery: string;
};

type ModelListState = {
  list: SelectList;
  models: string[];
  current: string;
};

type ThemeListState = {
  list: SelectList;
  current: string;
};

type McpListState = {
  list: SelectList;
  items: McpListItem[];
};

type McpDetailState = {
  serverName: string;
  info: Record<string, unknown>;
  enabled: boolean;
  actions: SelectList;
};

type McpToolItem = {
  id: string;
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  server_name: string;
};

type McpToolsState = {
  serverName: string;
  tools: McpToolItem[];
  list: SelectList;
};

type McpToolDetailState = {
  serverName: string;
  tool: McpToolItem;
};

type ConfigEditorPhase = "select_group" | "select_item" | "select_value" | "input_value";

type ConfigEditorState = {
  phase: ConfigEditorPhase;
  schemaList: ConfigItemSchema[];
  currentValues: Record<string, string>;
  selectedGroup: string | null;
  selectedKey: string | null;
  list: SelectList;
};

type StatusViewTab = "status" | "usage" | "config";

type StatusViewPhase = "tab_view" | "config_editor";

type StatusViewState = {
  phase: StatusViewPhase;
  tab: StatusViewTab;
  list: SelectList;
  statusPayload: import("../core/commands/builtins/status.js").StatusPayload | null;
  configPayload: (Record<string, unknown> & { schema?: ConfigItemSchema[] }) | null;
};

// FileViewer state for viewing large content (e.g., formatted logs)
type FileViewerState = {
  content: string;       // Full content text
  title: string;         // Title for header
  source: string;        // Source info
  scrollOffset: number;  // Current scroll position
  searchMode: boolean;   // Whether in search mode
  searchTerm: string;    // Search term
};

class ComposerAutocompleteProvider implements AutocompleteProvider {
  constructor(private readonly inner: AutocompleteProvider) {}

  getSuggestions(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    options: { signal: AbortSignal; force?: boolean },
  ) {
    const currentLine = lines[cursorLine] ?? "";
    const textBeforeCursor = currentLine.slice(0, cursorCol);
    const isCommandNameCompletion =
      textBeforeCursor.startsWith("/") && !textBeforeCursor.includes(" ");

    if (isCommandNameCompletion && cursorCol !== currentLine.length) {
      return Promise.resolve(null);
    }

    return this.inner.getSuggestions(lines, cursorLine, cursorCol, options);
  }

  applyCompletion(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    item: AutocompleteItem,
    prefix: string,
  ) {
    const currentLine = lines[cursorLine] ?? "";
    const textBeforeCursor = currentLine.slice(0, cursorCol);
    const isCommandNameCompletion = prefix.startsWith("/") && !prefix.slice(1).includes("/");

    if (isCommandNameCompletion && textBeforeCursor !== prefix) {
      return { lines, cursorLine, cursorCol };
    }

    return this.inner.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
  }
}

const IMAGE_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

function resolveFdBinary(): string | null {
  for (const candidate of ["fd", "fdfind"]) {
    const result = spawnSync(candidate, ["--version"], {
      stdio: "ignore",
      timeout: 400,
    });
    if (result.status === 0) {
      return candidate;
    }
  }
  return null;
}

function isPermissionRequest(source: string | undefined, questionText: string): boolean {
  return source === "permission_interrupt" || PERMISSION_TOOL_RE.test(questionText);
}

function parsePermissionSummary(questionText: string): PermissionSummary {
  const tool = PERMISSION_TOOL_RE.exec(questionText)?.[1]?.trim();
  const riskMatch = PERMISSION_RISK_RE.exec(questionText);
  const risk = riskMatch
    ? `${(riskMatch[1] ?? "").trim()} ${riskMatch[2].trim()}`.trim()
    : undefined;
  const reason = [...questionText.matchAll(PERMISSION_QUOTE_RE)]
    .map((match) => match[1]?.trim() ?? "")
    .find(Boolean);

  let command: string | undefined;
  let description: string | undefined;
  const jsonBlock = PERMISSION_JSON_BLOCK_RE.exec(questionText)?.[1]?.trim();
  if (jsonBlock) {
    try {
      const parsed = JSON.parse(jsonBlock) as Record<string, unknown>;
      command =
        typeof parsed.command === "string"
          ? parsed.command.trim()
          : typeof parsed.cmd === "string"
            ? parsed.cmd.trim()
            : undefined;
      description = typeof parsed.description === "string" ? parsed.description.trim() : undefined;
    } catch {
      // Ignore malformed JSON blocks in permission prompts.
    }
  }

  return {
    tool,
    risk,
    reason,
    command,
    description,
  };
}

function compressRiskLabel(risk: string | undefined): string | undefined {
  if (!risk) return undefined;
  const normalized = risk.replace(/\s+/g, " ").trim();
  return normalized
    .replace(/^高\s*/u, "High ")
    .replace(/^中\s*/u, "Medium ")
    .replace(/^低\s*/u, "Low ")
    .replace(/风险$/u, "risk");
}

function permissionToolKind(tool: string | undefined): "bash" | "filesystem" | "generic" {
  const normalized = tool?.trim().toLowerCase() ?? "";
  if (
    normalized === "bash" ||
    normalized === "shell" ||
    normalized === "sh" ||
    normalized === "powershell" ||
    normalized === "command" ||
    normalized === "exec" ||
    normalized === "run" ||
    normalized === "mcp_exec_command" ||
    normalized === "create_terminal"
  ) {
    return "bash";
  }
  if (
    normalized.includes("read") ||
    normalized.includes("write") ||
    normalized.includes("edit") ||
    normalized.includes("search") ||
    normalized.includes("grep") ||
    normalized.includes("glob") ||
    normalized.includes("fetch") ||
    normalized.includes("file") ||
    normalized.includes("memory")
  ) {
    return "filesystem";
  }
  return "generic";
}

function extractFilesystemTarget(summary: PermissionSummary): string | undefined {
  const raw = summary.command ?? summary.description ?? "";
  const quoted = /(["'`])([^"'`]+)\1/.exec(raw)?.[2]?.trim();
  if (quoted) return quoted;
  const pathish = /((?:\/|\.\/|\.\.\/)[^\s,)]+)/.exec(raw)?.[1]?.trim();
  if (pathish) return pathish;
  return undefined;
}

function renderPermissionBlock(
  width: number,
  summary: PermissionSummary,
  progressLabel: string,
): string[] {
  const lines: string[] = [];
  const risk = compressRiskLabel(summary.risk);
  const kind = permissionToolKind(summary.tool);
  const primaryDetail = summary.command ?? summary.description ?? summary.reason;

  lines.push(padToWidth(palette.status.warning(progressLabel), width));

  if (kind === "bash") {
    lines.push(
      padToWidth(palette.text.assistant(`${summary.tool ?? "command"} wants to run`), width),
    );
    if (summary.command) {
      lines.push(
        ...wrapPlainText(summary.command, width)
          .slice(0, 2)
          .map((line) => padToWidth(palette.text.tool(line), width)),
      );
    } else if (primaryDetail) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 2)
          .map((line) => padToWidth(palette.text.dim(line), width)),
      );
    }
  } else if (kind === "filesystem") {
    lines.push(
      padToWidth(palette.text.assistant(`${summary.tool ?? "tool"} wants to access files`), width),
    );
    const target = extractFilesystemTarget(summary);
    if (target) {
      lines.push(padToWidth(palette.text.tool(target), width));
    }
    if (primaryDetail && primaryDetail !== target) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 1)
          .map((line) => padToWidth(palette.text.dim(line), width)),
      );
    }
  } else {
    if (summary.tool) {
      lines.push(padToWidth(palette.text.assistant(`${summary.tool} requires permission`), width));
    }
    if (primaryDetail) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 2)
          .map((line) =>
            padToWidth(summary.command ? palette.text.tool(line) : palette.text.dim(line), width),
          ),
      );
    }
  }

  if (risk) {
    lines.push(
      padToWidth(
        /high/i.test(risk) ? palette.status.error(risk) : palette.status.warning(risk),
        width,
      ),
    );
  }

  return lines;
}

function normalizePermissionOptionLabel(label: string): string {
  const trimmed = label.trim();
  if (trimmed === "本次允许") return "Allow once";
  if (trimmed === "总是允许") return "Always allow";
  if (trimmed === "拒绝") return "Reject";
  return trimmed;
}

function isAllowOption(label: string): boolean {
  const normalized = label.trim();
  return normalized.includes("允许") || /^allow\b/i.test(normalized);
}

function isRejectOption(label: string): boolean {
  const normalized = label.trim();
  return (
    normalized.includes("拒绝") || /^reject\b/i.test(normalized) || /^deny\b/i.test(normalized)
  );
}

function wrapPlainText(text: string, width: number): string[] {
  const maxWidth = Math.max(12, width - 1);
  const source = text.replace(/\r/g, "").split("\n");
  const lines: string[] = [];
  for (const rawLine of source) {
    const words = rawLine.split(/\s+/).filter((word) => word.length > 0);
    if (words.length === 0) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (next.length <= maxWidth) {
        current = next;
        continue;
      }
      if (current) {
        lines.push(current);
      }
      current = word.length <= maxWidth ? word : word.slice(0, maxWidth);
    }
    if (current) {
      lines.push(current);
    }
  }
  return lines.length > 0 ? lines : [text.slice(0, maxWidth)];
}

function formatSessionTime(timestamp: number | undefined): string {
  if (!timestamp) return "-";
  return new Date(timestamp * 1000).toLocaleString();
}

function getDisplayLabel(s: SessionMeta): string {
  return s.title?.trim() || s.session_id;
}

function sessionToSelectItem(s: SessionMeta): SelectItem {
  return {
    value: s.session_id,
    label: getDisplayLabel(s),
    description: `${s.session_id} · msgs ${s.message_count ?? 0} · ${formatSessionTime(s.last_message_at)}`,
  };
}

function buildResumeSessionItems(sessions: SessionMeta[]): SelectItem[] {
  return sessions.map(sessionToSelectItem);
}

function filterResumeSessions(sessions: SessionMeta[], query: string): SelectItem[] {
  const normalizedQuery = query.toLowerCase();
  return sessions
    .filter((s) => getDisplayLabel(s).toLowerCase().includes(normalizedQuery))
    .map(sessionToSelectItem);
}

export class AppScreen implements Component, Focusable {
  private readonly editor: Editor;
  private readonly unsubscribe: () => void;
  private composerAutocompleteProvider: AutocompleteProvider;
  private _focused = false;
  private activeQuestionId: string | null = null;
  private activeQuestionIndex = 0;
  private draftBeforeQuestion = "";
  private syncingComposerInput = false;
  private pendingQuestionAnswers = new Map<number, string>();
  private questionList: SelectList | null = null;
  private otherInputMode = false;
  private resumeSessionList: ResumeSessionListState | null = null;
  private modelList: ModelListState | null = null;
  private mcpList: McpListState | null = null;
  private mcpDetail: McpDetailState | null = null;
  private mcpTools: McpToolsState | null = null;
  private mcpToolDetail: McpToolDetailState | null = null;
  private themeList: ThemeListState | null = null;
  private configEditorState: ConfigEditorState | null = null;
  private statusViewState: StatusViewState | null = null;
  private startupPromptList: SelectList | null = null;
  private todosCollapsed = false;
  private showTeamPanel = false;
  private selectedTeamMemberId: string | null = null;
  private viewedTeamMemberId: string | null = null;
  private transientNotice: string | null = null;
  private transientNoticeTimer: ReturnType<typeof setTimeout> | null = null;
  private animationTimer: ReturnType<typeof setInterval> | null = null;
  private animationPhase = 0;
  private runningStartedAtMs: number | null = null;
  private runningStoppedAtMs: number | null = null;
  /** Whether the eager skill-cache fetch on first WebSocket connection has already been fired. */
  private didEagerFetchSkills = false;
  private pendingSubmittedInput: string | null = null;
  private pendingSubmittedBaseline = 0;
  private pendingSubmittedSessionId: string | null = null;
  private transcriptScrollOffset = 0;
  /** Image attachments keyed by composer `@path` tokens (e.g. cached base64 for terminal preview). */
  private composerAttachments: FileAttachment[] = [];
  /** FileViewer state for viewing large content (e.g., formatted logs) */
  private fileViewerState: FileViewerState | null = null;

  constructor(
    private readonly tui: TUI,
    private readonly state: CliPiAppState,
    private readonly commands: CommandService,
    private readonly exit: () => void,
  ) {
    this.editor = new Editor(tui, editorTheme, { paddingX: 1, autocompleteMaxVisible: 6 });
    this.composerAutocompleteProvider = this.rebuildAutocompleteProvider();
    this.editor.setAutocompleteProvider(this.composerAutocompleteProvider);
    // Whenever CommandService refreshes its installed-skills cache (on first
    // WebSocket connection and after every execute() call), rebuild the
    // CombinedAutocompleteProvider so that the /<skillName> shorthands appear
    // in the command-name dropdown.
    this.commands.onInstalledSkillsChange = (skills: readonly InstalledSkillEntry[]) => {
      this.composerAutocompleteProvider = this.rebuildAutocompleteProvider(skills);
      this.editor.setAutocompleteProvider(this.composerAutocompleteProvider);
    };
    this.editor.onChange = () => {
      this.tui.requestRender();
    };
    this.editor.onSubmit = (value) => {
      void this.handleSubmit(value);
    };
    this.unsubscribe = this.state.onChange(() => {
      this.handleStateChange();
    });
    // Inject editor refs into app-state so tryAutoRestoreAfterCancel can
    // check input emptiness and populate the input field after auto-restore.
    this.state.setInputRef((text: string) => {
      this.editor.setText(text);
    });
    this.state.getInputValueRef(() => this.editor.getText());
    // Initialize startup prompt for workspace trust
    this.initStartupPrompt();
  }

  private initStartupPrompt(): void {
    const cwd = process.cwd();
    if (isTrustedDir(cwd)) {
      return;
    }
    const items: SelectItem[] = [
      {
        label: "Yes, I trust this folder",
        value: "yes",
        description: "JiuwenClaw will be able to read, edit, and execute files here",
      },
      {
        label: "No, use default workspace",
        value: "no",
        description: "Only ~/.jiuwenclaw/agent/jiuwenclaw_workspace will be accessible",
      },
    ];
    this.startupPromptList = new SelectList(items, 2, selectListTheme, {
      minPrimaryColumnWidth: 40,
      maxPrimaryColumnWidth: 60,
    });
    this.startupPromptList.onSelect = (item) => {
      if (item.value === "yes") {
        addTrustedDir(cwd);
      }
      this.startupPromptList = null;
      this.tui.requestRender();
    };
    this.startupPromptList.onCancel = () => {
      // Same as "No" - use default workspace
      this.startupPromptList = null;
      this.tui.requestRender();
    };
  }

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
    this.editor.focused = value;
  }

  dispose(): void {
    if (this.transientNoticeTimer) {
      clearTimeout(this.transientNoticeTimer);
      this.transientNoticeTimer = null;
    }
    if (this.animationTimer) {
      clearInterval(this.animationTimer);
      this.animationTimer = null;
    }
    this.unsubscribe();
  }

  invalidate(): void {
    this.editor.invalidate();
  }

  /** Enter FileViewer mode to view large content (e.g., formatted logs) */
  enterFileViewer(content: string, title: string, source: string): void {
    this.fileViewerState = {
      content,
      title,
      source,
      scrollOffset: 0,
      searchMode: false,
      searchTerm: "",
    };
    this.tui.requestRender();
  }

  /** Exit FileViewer mode and return to normal view */
  exitFileViewer(): void {
    this.fileViewerState = null;
    this.tui.requestRender();
  }

  /** Handle FileViewer input - scrolling and navigation */
  private handleFileViewerInput(data: string): void {
    if (!this.fileViewerState) return;

    const contentLines = this.fileViewerState.content.split("\n");
    const height = this.tui.terminal.rows;
    const availableHeight = Math.max(1, height - 2); // Reserve for title + hint

    // Esc or q to exit
    if (matchesKey(data, "escape") || data.toLowerCase() === "q") {
      this.exitFileViewer();
      return;
    }

    // Scroll up (up arrow or k)
    if (matchesKey(data, "up") || data.toLowerCase() === "k") {
      this.fileViewerState.scrollOffset = Math.max(0, this.fileViewerState.scrollOffset - 1);
      this.tui.requestRender();
      return;
    }

    // Scroll down (down arrow or j)
    if (matchesKey(data, "down") || data.toLowerCase() === "j") {
      const maxScroll = Math.max(0, contentLines.length - availableHeight);
      this.fileViewerState.scrollOffset = Math.min(maxScroll, this.fileViewerState.scrollOffset + 1);
      this.tui.requestRender();
      return;
    }

    // Page up
    if (matchesKey(data, "pageUp")) {
      this.fileViewerState.scrollOffset = Math.max(0, this.fileViewerState.scrollOffset - availableHeight);
      this.tui.requestRender();
      return;
    }

    // Page down
    if (matchesKey(data, "pageDown")) {
      const maxScroll = Math.max(0, contentLines.length - availableHeight);
      this.fileViewerState.scrollOffset = Math.min(maxScroll, this.fileViewerState.scrollOffset + availableHeight);
      this.tui.requestRender();
      return;
    }

    // Go to top (Home)
    if (matchesKey(data, "home") || data.toLowerCase() === "g") {
      this.fileViewerState.scrollOffset = 0;
      this.tui.requestRender();
      return;
    }

    // Go to bottom (End)
    if (matchesKey(data, "end") || data.toLowerCase() === "shift+g") {
      const maxScroll = Math.max(0, contentLines.length - availableHeight);
      this.fileViewerState.scrollOffset = maxScroll;
      this.tui.requestRender();
      return;
    }
  }

  /** Render FileViewer mode - show content in a scrollable viewer */
  private renderFileViewer(width: number): string[] {
    if (!this.fileViewerState) return [];

    const height = Math.max(3, this.tui.terminal.rows);
    const safeWidth = Math.max(1, width);
    const lines: string[] = [];

    // Title bar (line 1)
    const titleText = `━━━ ${this.fileViewerState.title} ━━━`;
    lines.push(padToWidth(palette.border.panel(titleText), safeWidth));

    // Content area
    const contentLines = this.fileViewerState.content.split("\n");
    const availableHeight = Math.max(1, height - 2);
    const scrollOffset = this.fileViewerState.scrollOffset;

    // Add visible content lines
    for (let i = 0; i < availableHeight; i++) {
      const lineIndex = scrollOffset + i;
      if (lineIndex < contentLines.length) {
        const rawLine = contentLines[lineIndex] || "";
        lines.push(truncateToWidth(rawLine, safeWidth, ""));
      } else {
        // Pad with empty lines
        lines.push(" ".repeat(safeWidth));
      }
    }

    // Hint bar (last line) - show scroll position
    const totalLines = contentLines.length;
    const scrollPercent = totalLines > 0 ? Math.round((scrollOffset / totalLines) * 100) : 0;
    const positionInfo = totalLines > availableHeight ? ` [${scrollOffset + 1}-${Math.min(scrollOffset + availableHeight, totalLines)}/${totalLines} (${scrollPercent}%)]` : "";
    const hintText = `按 Esc/q 退出 | ↑↓ 滚动 | PgUp/PgDown 翻页${positionInfo}`;
    lines.push(padToWidth(palette.text.dim(hintText), safeWidth));

    return lines;
  }

  /**
   * Ctrl+C / SIGINT 始终尝试向服务端发送当前 session 的中断请求。
   * 是否真的存在运行任务由服务端判断；CLI/TUI 本身不退出。
   */
  interruptTask(): void {
    this.state.cancel();
    this.editor.setText("");
    this.tui.requestRender();
  }

  handleInput(data: string): void {
    // FileViewer mode: handle input separately
    if (this.fileViewerState) {
      this.handleFileViewerInput(data);
      return;
    }

    const snapshot = this.state.getSnapshot();
    const pendingQuestion = snapshot.pendingQuestion;
    const activeQuestion =
      pendingQuestion?.questions[this.activeQuestionIndex] ?? pendingQuestion?.questions[0];
    const permissionRequest = activeQuestion
      ? isPermissionRequest(pendingQuestion?.source, activeQuestion.question)
      : false;

    const hasOverlay =
      this.mcpDetail !== null ||
      this.mcpToolDetail !== null ||
      this.mcpList !== null ||
      this.mcpTools !== null ||
      this.modelList !== null ||
      this.themeList !== null ||
      this.configEditorState !== null;

    if (!pendingQuestion && snapshot.cancellableWork && matchesKey(data, "escape") && !hasOverlay) {
      this.state.cancel();
      return;
    }

    if (this.startupPromptList !== null && matchesKey(data, "ctrl+c")) {
      this.startupPromptList.handleInput(data);
      this.tui.requestRender();
      return;
    }

    const handled = handleAppScreenKeyInput(data, {
      interruptTask: () => this.interruptTask(),
      exitApp: () => this.exit(),
      toggleTodos: () => {
        this.todosCollapsed = !this.todosCollapsed;
        this.tui.requestRender();
      },
      toggleTeamPanel: () => {
        this.showTeamPanel = !this.showTeamPanel;
        if (!this.showTeamPanel) {
          this.viewedTeamMemberId = null;
        }
        this.tui.requestRender();
      },
      toggleTranscript: () => {
        const snapshot = this.state.getSnapshot();
        this.state.setTranscriptMode(
          snapshot.transcriptMode === "detailed" ? "compact" : "detailed",
        );
      },
      redraw: () => {
        this.tui.invalidate();
        this.tui.requestRender(true);
        this.transientNotice = "Screen redrawn";
        if (this.transientNoticeTimer) {
          clearTimeout(this.transientNoticeTimer);
        }
        this.transientNoticeTimer = setTimeout(() => {
          this.transientNotice = null;
          this.transientNoticeTimer = null;
          this.tui.requestRender();
        }, 1200);
        this.tui.requestRender();
      },
      clearInput: () => {
        this.editor.setText("");
        this.tui.requestRender();
      },
      isIdle: () => {
        return !snapshot.isProcessing && !snapshot.pendingQuestion && !snapshot.cancellableWork;
      },
      hasServerTask: () => this.state.hasServerTask(),
      requestLocalInterrupt: () => {
        this.state.requestLocalInterrupt();
      },
    });
    if (handled) {
      return;
    }

    if (permissionRequest && activeQuestion) {
      const lower = data.toLowerCase();
      if (lower === "y") {
        const allow = activeQuestion.options.find((option) => isAllowOption(option.label));
        if (allow) {
          this.handleQuestionSelection(allow.label);
          return;
        }
      }
      if (lower === "n") {
        const reject = activeQuestion.options.find((option) => isRejectOption(option.label));
        if (reject) {
          this.handleQuestionSelection(reject.label);
          return;
        }
      }
    }

    // Startup prompt for workspace trust (shown first)
    if (this.startupPromptList !== null) {
      this.startupPromptList.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.resumeSessionList !== null) {
      const printableChar = this.getPrintableChar(data);
      if (printableChar !== undefined) {
        const newQuery = this.resumeSessionList.searchQuery + printableChar;
        this.updateResumeSearchQuery(newQuery);
      } else if (matchesKey(data, "backspace")) {
        const newQuery = this.resumeSessionList.searchQuery.slice(0, -1);
        this.updateResumeSearchQuery(newQuery);
      } else if (matchesKey(data, "escape")) {
        if (this.resumeSessionList.searchQuery) {
          this.updateResumeSearchQuery("");
        } else {
          this.resumeSessionList = null;
          this.tui.requestRender();
        }
      } else {
        this.resumeSessionList.list.handleInput(data);
      }
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.statusViewState !== null) {
      if (this.statusViewState.phase === "config_editor") {
        if (this.configEditorState?.phase === "input_value") {
          if (matchesKey(data, "escape")) {
            if (this.configEditorState.selectedGroup) {
              const groupSchemas = this.configEditorState.schemaList.filter(
                (s) => s.group === this.configEditorState!.selectedGroup,
              );
              this.showConfigGroupItems(
                this.configEditorState.selectedGroup,
                groupSchemas,
                this.configEditorState.currentValues,
              );
            } else {
              this.configEditorState = null;
              this.statusViewState = {
                ...this.statusViewState,
                phase: "tab_view",
                tab: "config",
                list: this.buildStatusViewTabState("config", this.statusViewState.statusPayload, this.statusViewState.configPayload),
              };
            }
            this.tui.requestRender();
            return;
          }
          if (matchesKey(data, "return")) {
            const text = this.editor.getText().trim();
            if (text && this.configEditorState.selectedKey) {
              const key = this.configEditorState.selectedKey;
              const schema = this.configEditorState.schemaList.find((s) => s.key === key);
              if (schema) {
                void this.applyConfigEditorSet(key, text, schema, this.configEditorState.currentValues);
                this.editor.setText("");
              }
            }
            return;
          }
          this.editor.handleInput(data);
        } else if (this.configEditorState) {
          this.configEditorState.list.handleInput(data);
        }
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "escape")) {
        this.closeStatusView();
        return;
      }
      if (matchesKey(data, "left")) {
        this.switchStatusViewTab(-1);
        return;
      }
      if (matchesKey(data, "right")) {
        this.switchStatusViewTab(1);
        return;
      }
      this.statusViewState.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.configEditorState !== null) {
      if (this.configEditorState.phase === "input_value") {
        // Handle Esc to cancel input and go back to group selection
        if (matchesKey(data, "escape")) {
          if (this.configEditorState.selectedGroup) {
            const groupSchemas = this.configEditorState.schemaList.filter(
              (s) => s.group === this.configEditorState!.selectedGroup,
            );
            this.showConfigGroupItems(
              this.configEditorState.selectedGroup,
              groupSchemas,
              this.configEditorState.currentValues,
            );
          } else {
            this.configEditorState = null;
            this.tui.requestRender();
          }
          return;
        }
        // Handle Enter to submit the config value (single-line input)
        if (matchesKey(data, "return")) {
          const text = this.editor.getText().trim();
          if (text && this.configEditorState.selectedKey) {
            const key = this.configEditorState.selectedKey;
            const schema = this.configEditorState.schemaList.find((s) => s.key === key);
            if (schema) {
              void this.applyConfigEditorSet(key, text, schema, this.configEditorState.currentValues);
              this.editor.setText("");
            }
          }
          return;
        }
        this.editor.handleInput(data);
      } else {
        this.configEditorState.list.handleInput(data);
      }
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.modelList !== null) {
      this.modelList.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.mcpList !== null) {
      this.mcpList.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (this.mcpDetail !== null) {
      if (matchesKey(data, "escape")) {
        this.mcpDetail = null;
        this.openMcpList();
        return;
      }
      if (!snapshot.pendingQuestion) {
        this.mcpDetail.actions.handleInput(data);
      }
      this.tui.requestRender();
      return;
    }

    if (this.mcpToolDetail !== null) {
      if (matchesKey(data, "escape")) {
        const serverName = this.mcpToolDetail.serverName;
        this.mcpToolDetail = null;
        void this.openMcpToolsList(serverName);
        return;
      }
      return;
    }

    if (this.mcpTools !== null) {
      if (matchesKey(data, "escape")) {
        const serverName = this.mcpTools.serverName;
        this.mcpTools = null;
        void this.handleMcpSelection(serverName);
        return;
      }
      this.mcpTools.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.themeList !== null) {
      this.themeList.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.showTeamPanel) {
      if (matchesKey(data, "left")) {
        this.viewedTeamMemberId = null;
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "return")) {
        this.viewedTeamMemberId = this.selectedTeamMemberId;
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "up")) {
        this.moveTeamPanelSelection(snapshot, -1);
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "down")) {
        this.moveTeamPanelSelection(snapshot, 1);
        this.tui.requestRender();
        return;
      }
    }

    if (snapshot.pendingQuestion && this.questionList !== null) {
      this.questionList.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (snapshot.pendingQuestion && this.otherInputMode) {
      if (matchesKey(data, "escape")) {
        this.otherInputMode = false;
        this.syncQuestionList(this.state.getSnapshot());
        this.tui.requestRender();
        return;
      }
      this.editor.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.handleTranscriptScrollInput(data)) {
      return;
    }

    // Detect pasted file paths (drag-and-drop) in the terminal
    // When files are dragged in, they arrive as a pasted string.
    // Windows/PowerShell may not send bracketed paste markers,
    // so we detect file paths in any multi-character input.
    if (!snapshot.pendingQuestion && data.length > 4) {
      const pastedContent = data.replace(/\x1b\[200~/, "").replace(/\x1b\[201~/, "");
      const filePaths = extractFilePathsFromPaste(pastedContent);
      if (filePaths.length > 0) {
        // 若解析出路径但无一通过附件校验（扩展名不在白名单等），须把原文交给编辑器，避免粘贴被吞掉
        if (this.handleDroppedFiles(filePaths)) {
          return;
        }
      }
    }

    this.editor.handleInput(data);
  }

  render(width: number): string[] {
    // FileViewer mode: render file viewer instead of normal view
    if (this.fileViewerState) {
      return this.renderFileViewer(width);
    }

    const snapshot = this.state.getSnapshot();
    const teamWorking =
      isTeamMode(snapshot.mode) &&
      isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    this.editor.borderColor = snapshot.pendingQuestion
      ? palette.border.question
      : palette.border.panel;
    // When in config editor input_value phase, editor is rendered inside buildConfigEditorLines
    // to avoid duplicate rendering, don't include editorLines in that case
    const isConfigInputValue = this.configEditorState?.phase === "input_value";
    const editorLines = isConfigInputValue
      ? []
      : this.applySlashCommandHint(this.editor.render(width), width);
    const composerPreviewLines: string[] = [];
    const questionLines = [
      ...this.buildStartupPromptLines(width),
      ...this.buildStatusViewLines(width),
      ...(!this.statusViewState ? this.buildConfigEditorLines(width) : []),
      ...this.buildResumeSessionListLines(width),
      ...this.buildModelListLines(width),
      ...this.buildMcpListLines(width),
      ...this.buildMcpDetailLines(width),
      ...this.buildMcpToolsLines(width),
      ...this.buildMcpToolDetailLines(width),
      ...this.buildThemeListLines(width),
      ...this.buildPendingQuestionLines(snapshot, width),
    ];
    return buildAppScreenLines(snapshot, {
      width,
      height: this.tui.terminal.rows,
      questionLines,
      editorLines,
      composerPreviewLines,
      pendingInput: this.pendingSubmittedInput ?? undefined,
      pendingInputBaseline: this.pendingSubmittedInput ? this.pendingSubmittedBaseline : undefined,
      showFullThinking: snapshot.transcriptMode === "detailed",
      showToolDetails: snapshot.transcriptMode === "detailed",
      showShortcutHelp: false,
      todosCollapsed: this.todosCollapsed,
      showTeamPanel: this.showTeamPanel,
      selectedTeamMemberId: this.selectedTeamMemberId,
      viewedTeamMemberId: this.viewedTeamMemberId,
      transientNotice: this.transientNotice,
      animationPhase: this.animationPhase,
      transcriptScrollOffset: this.transcriptScrollOffset,
      onTranscriptScrollOffsetChange: (offset) => {
        this.transcriptScrollOffset = offset;
      },
      runningElapsedMs:
        !snapshot.isInterrupted &&
        (snapshot.isProcessing || teamWorking) &&
        this.runningStartedAtMs !== null
          ? Date.now() - this.runningStartedAtMs
          : undefined,
    });
  }

  private async handleSubmit(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text) return;

    const { content, attachments } = this.buildOutgoingMessage(text);

    // Config editor input_value phase: submit the typed value
    if (this.configEditorState?.phase === "input_value" && this.configEditorState.selectedKey) {
      const key = this.configEditorState.selectedKey;
      const schema = this.configEditorState.schemaList.find((s) => s.key === key);
      if (schema) {
        void this.applyConfigEditorSet(key, text, schema, this.configEditorState.currentValues);
      }
      this.editor.setText("");
      this.composerAttachments = [];
      return;
    }

    if (!content) return;

    const snapshot = this.state.getSnapshot();
    if (snapshot.pendingQuestion) {
      if (this.questionList === null) {
        if (this.otherInputMode) {
          this.pendingQuestionAnswers.set(this.activeQuestionIndex, text);
          this.otherInputMode = false;

          const pendingQuestion = snapshot.pendingQuestion;
          if (this.activeQuestionIndex < pendingQuestion.questions.length - 1) {
            this.activeQuestionIndex += 1;
            this.syncQuestionList(this.state.getSnapshot());
            this.editor.setText("");
            this.tui.requestRender();
            return;
          }

          const answers = pendingQuestion.questions.map((question, index) => {
            const answerValue = this.pendingQuestionAnswers.get(index) ?? question.options[0]?.label ?? "";
            return {
              question: question.question,
              selected_options: [answerValue],
            };
          });
          this.state.submitQuestionAnswers(answers);
          this.editor.setText("");
          return;
        }
        this.state.answerQuestion(text);
      }
      this.editor.setText("");
      return;
    }

    if (text.startsWith("/")) {
      if (/^\/(?:resume|continue)\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.state.addItem(addCommandEcho(snapshot.sessionId, text));
        await this.openResumeSessionList();
        return;
      }
      if (/^\/model\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.state.addItem(addCommandEcho(snapshot.sessionId, text));
        await this.openModelList();
        return;
      }
      if (/^\/mcp(?:\s+list)?\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.state.addItem(addCommandEcho(snapshot.sessionId, text));
        await this.openMcpList();
        return;
      }
      if (/^\/status(?:\s+\S*)?\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.state.addItem(addCommandEcho(snapshot.sessionId, text));
        const subMatch = text.match(/^\/status\s+(\S+)/);
        const tab: StatusViewTab | undefined =
          subMatch?.[1] === "usage" ? "usage" :
          subMatch?.[1] === "config" ? "config" :
          undefined;
        await this.openStatusView(tab);
        return;
      }
      if (/^\/theme\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.state.addItem(addCommandEcho(snapshot.sessionId, text));
        this.openThemeList();
        return;
      }
      this.beginPendingSubmittedInput(text, snapshot);
      this.editor.addToHistory(text);
      this.editor.setText("");
      this.state.addItem(addCommandEcho(snapshot.sessionId, text));
      try {
        await this.commands.execute(text, {
          ...this.state.getCommandContext(),
          exitApp: this.exit,
          setInput: (text: string) => {
            this.editor.setText(text);
          },
          enterConfigEditor: (focusKey, configPayload) => {
            this.openConfigEditor(focusKey, configPayload);
          },
          openInEditor: (filePath: string) => {
            openInExternalEditor(this.tui, filePath);
          },
          enterFileViewer: (content, title, source) => {
            this.enterFileViewer(content, title, source);
          },
        });
      } finally {
        this.clearPendingSubmittedInput();
      }
      return;
    }

    if (snapshot.isProcessing || snapshot.isPaused) {
      this.beginPendingSubmittedInput(text, snapshot);
      const requestId = this.state.supplement(content, attachments);
      if (!requestId) {
        this.clearPendingSubmittedInput();
        this.state.addItem({
          kind: "error",
          id: `offline-${Date.now()}`,
          sessionId: snapshot.sessionId,
          content: "offline: waiting for reconnect",
          at: new Date().toISOString(),
        });
        return;
      }
      this.editor.addToHistory(text);
      this.editor.setText("");
      return;
    }

    this.beginPendingSubmittedInput(text, snapshot);
    const requestId = this.state.sendMessage(content, attachments);
    if (!requestId) {
      this.clearPendingSubmittedInput();
      this.state.addItem({
        kind: "error",
        id: `offline-${Date.now()}`,
        sessionId: snapshot.sessionId,
        content: "offline: waiting for reconnect",
        at: new Date().toISOString(),
      });
      return;
    }

    this.editor.addToHistory(text);
    this.editor.setText("");
  }

  private handleStateChange(): void {
    const snapshot = this.state.getSnapshot();
    // Populate the skill cache as soon as the WebSocket connection is established
    if (!this.didEagerFetchSkills && snapshot.connectionStatus === "connected") {
      this.didEagerFetchSkills = true;
      void this.commands.refreshSkills(this.state.getCommandContext());
    }
    if (
      this.pendingSubmittedInput &&
      (snapshot.sessionId !== this.pendingSubmittedSessionId ||
        snapshot.entries.length !== this.pendingSubmittedBaseline)
    ) {
      this.clearPendingSubmittedInput(false);
    }
    const questionId = snapshot.pendingQuestion?.requestId ?? null;
    if (questionId && questionId !== this.activeQuestionId) {
      this.activeQuestionId = questionId;
      this.activeQuestionIndex = 0;
      this.pendingQuestionAnswers.clear();
      this.draftBeforeQuestion = this.editor.getText();
      this.editor.setText("");
      this.syncQuestionList(snapshot);
    } else if (questionId && this.activeQuestionId) {
      this.syncQuestionList(snapshot);
    } else if (!questionId && this.activeQuestionId) {
      this.activeQuestionId = null;
      this.activeQuestionIndex = 0;
      this.pendingQuestionAnswers.clear();
      this.questionList = null;
      if (!this.editor.getText() && this.draftBeforeQuestion) {
        this.editor.setText(this.draftBeforeQuestion);
      }
      this.draftBeforeQuestion = "";
    }
    this.syncTeamPanelSelection(snapshot);
    this.syncAnimationLoop(snapshot);
    this.tui.requestRender();
  }

  private beginPendingSubmittedInput(
    text: string,
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
  ): void {
    this.transcriptScrollOffset = 0;
    this.pendingSubmittedInput = text;
    this.pendingSubmittedBaseline = snapshot.entries.length;
    this.pendingSubmittedSessionId = snapshot.sessionId;
    this.tui.requestRender();
  }

  private handleTranscriptScrollInput(data: string): boolean {
    const pageSize = Math.max(1, Math.floor(this.tui.terminal.rows * 0.8));
    if (matchesKey(data, "pageUp") || matchesKey(data, "shift+pageUp")) {
      this.transcriptScrollOffset += pageSize;
      this.tui.requestRender();
      return true;
    }
    if (matchesKey(data, "pageDown") || matchesKey(data, "shift+pageDown")) {
      this.transcriptScrollOffset = Math.max(0, this.transcriptScrollOffset - pageSize);
      this.tui.requestRender();
      return true;
    }
    if (matchesKey(data, "ctrl+home")) {
      this.transcriptScrollOffset = Number.MAX_SAFE_INTEGER;
      this.tui.requestRender();
      return true;
    }
    if (matchesKey(data, "ctrl+end")) {
      this.transcriptScrollOffset = 0;
      this.tui.requestRender();
      return true;
    }
    return false;
  }

  private clearPendingSubmittedInput(requestRender = true): void {
    this.pendingSubmittedInput = null;
    this.pendingSubmittedBaseline = 0;
    this.pendingSubmittedSessionId = null;
    if (requestRender) {
      this.tui.requestRender();
    }
  }

  private syncTeamPanelSelection(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const memberIds = orderedMemberIds(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    if (memberIds.length === 0) {
      this.selectedTeamMemberId = null;
      this.viewedTeamMemberId = null;
      return;
    }
    if (!this.selectedTeamMemberId || !memberIds.includes(this.selectedTeamMemberId)) {
      this.selectedTeamMemberId = memberIds[0] ?? null;
    }
    if (this.viewedTeamMemberId && !memberIds.includes(this.viewedTeamMemberId)) {
      this.viewedTeamMemberId = null;
    }
  }

  private moveTeamPanelSelection(
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
    delta: -1 | 1,
  ): void {
    const memberIds = orderedMemberIds(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    if (memberIds.length === 0) {
      this.selectedTeamMemberId = null;
      return;
    }
    const currentIndex = this.selectedTeamMemberId
      ? memberIds.indexOf(this.selectedTeamMemberId)
      : 0;
    const baseIndex = currentIndex >= 0 ? currentIndex : 0;
    const nextIndex = Math.max(0, Math.min(memberIds.length - 1, baseIndex + delta));
    const nextMemberId = memberIds[nextIndex] ?? memberIds[0] ?? null;
    this.selectedTeamMemberId = nextMemberId;
    if (this.viewedTeamMemberId !== null) {
      this.viewedTeamMemberId = nextMemberId;
    }
  }

  private async openResumeSessionList(): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<SessionListPayload>("session.list", {});
      const sessions = payload.sessions ?? [];
      const total = payload.total ?? sessions.length;
      if (sessions.length === 0) {
        this.resumeSessionList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No sessions found", "r"));
        return;
      }

      const items = buildResumeSessionItems(sessions);
      const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
        minPrimaryColumnWidth: 24,
        maxPrimaryColumnWidth: 42,
      });
      list.onSelect = (item) => {
        void this.handleResumeSessionSelection(item.value);
      };
      list.onCancel = () => {
        this.resumeSessionList = null;
        this.tui.requestRender();
      };
      this.resumeSessionList = { list, sessions, total, searchQuery: "" };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.resumeSessionList = null;
      this.state.addItem(addError(snapshot.sessionId, `resume failed: ${message}`));
    }
  }

  private async handleResumeSessionSelection(sessionId: string): Promise<void> {
    const nextSessionId = sessionId.trim();
    if (!nextSessionId) {
      return;
    }
    this.resumeSessionList = null;
    this.state.updateSession(nextSessionId);
    this.state.clearEntries();
    await this.state.restoreHistory(nextSessionId);
    this.tui.requestRender();
  }

  private getPrintableChar(data: string): string | undefined {
    // Kitty protocol printable character
    const kittyChar = decodeKittyPrintable(data);
    if (kittyChar) return kittyChar;

    // Check for printable Unicode character (not control sequences)
    if (data.length === 1) {
      const code = data.charCodeAt(0);
      // Control characters (0-31, 127) and DEL (127) are not printable
      // Extended ASCII (128-255) and Unicode (>255) printable chars are accepted
      if (code >= 32 && code !== 127) return data;
    }

    // UTF-8 multi-byte characters (Chinese, etc.)
    // Check if data looks like a valid UTF-8 printable string (not an escape sequence)
    if (data.length > 1 && !data.startsWith("\x1b")) {
      try {
        // Verify it's a valid printable string
        const firstChar = data[0];
        if (firstChar && firstChar.charCodeAt(0) >= 32) {
          return data;
        }
      } catch {
        // Invalid UTF-8, ignore
      }
    }

    return undefined;
  }

  private updateResumeSearchQuery(query: string): void {
    if (!this.resumeSessionList) return;
    const filteredItems = filterResumeSessions(this.resumeSessionList.sessions, query);
    const list = new SelectList(filteredItems, Math.min(Math.max(filteredItems.length, 1), 8), selectListTheme, {
      minPrimaryColumnWidth: 24,
      maxPrimaryColumnWidth: 42,
    });
    list.onSelect = (item) => void this.handleResumeSessionSelection(item.value);
    list.onCancel = () => {
      this.resumeSessionList = null;
      this.tui.requestRender();
    };
    this.resumeSessionList = { ...this.resumeSessionList, list, searchQuery: query };
    this.tui.requestRender();
  }

  private buildStartupPromptLines(width: number): string[] {
    if (!this.startupPromptList) {
      return [];
    }
    const cwd = process.cwd();
    return [
      "",
      padToWidth(palette.status.warning("Safety Check"), width),
      "",
      padToWidth(palette.text.primary(`Current folder: ${cwd}`), width),
      "",
      padToWidth(palette.text.dim("Is this a project you created or one you trust?"), width),
      padToWidth(palette.text.dim("(e.g. your own code, well-known open source, or team project)"), width),
      padToWidth(palette.text.dim("If unfamiliar, please review the folder contents before proceeding."), width),
      "",
      ...this.startupPromptList.render(width),
      padToWidth(palette.text.dim("↑/↓ choose · Enter confirm · Esc / Ctrl+C use default workspace"), width),
    ];
  }

  private buildResumeSessionListLines(width: number): string[] {
    if (!this.resumeSessionList) {
      return [];
    }
    const searchBox = this.resumeSessionList.searchQuery
      ? padToWidth(palette.text.primary(`Search: ${this.resumeSessionList.searchQuery}${END_CURSOR}`), width)
      : padToWidth(palette.text.dim("Type to search · ↑/↓ choose · Enter resume · Esc cancel"), width);
    return [
      padToWidth(
        palette.status.warning(`Resume session (${this.resumeSessionList.total} total)`),
        width,
      ),
      searchBox,
      ...this.resumeSessionList.list.render(width),
      padToWidth(
        palette.text.dim(
          this.resumeSessionList.searchQuery
            ? "Backspace delete · Enter resume · Esc clear"
            : "↑/↓ choose · Enter resume · Esc cancel"
        ),
        width,
      ),
    ];
  }

  async openModelList(): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<ModelListPayload>("command.model", {});
      const models = payload.available_models ?? [];
      const current = payload.current ?? "unknown";
      if (models.length === 0) {
        this.modelList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No models configured", "m"));
        return;
      }

      const skipped = models.filter((m) => isReservedMultimodalModelKey(m));
      const selectable = models.filter((m) => !isReservedMultimodalModelKey(m));
      if (skipped.length > 0) {
        this.state.addItem(
          addInfo(
            snapshot.sessionId,
            "video, audio, and vision are not offered as the default chat model here (multimodal-only). To configure them, use /config edit → Vision / Audio / Video, or /config set on keys such as vision_model, audio_model, video_model.",
            "m",
          ),
        );
      }
      if (selectable.length === 0) {
        this.modelList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No switchable models in list", "m"));
        return;
      }

      const modelsMeta = payload.models ?? [];
      const items = selectable.map((m, i) => {
        const isCurrent = m === current;
        const meta = modelsMeta.find((x) => x.name === m);
        const displayName = (meta?.model_name && meta.model_name !== m)
          ? `${m} (${meta.model_name})`
          : m;
        return {
          label: `${i + 1}. ${displayName}${isCurrent ? " (current)" : ""}`,
          value: m,
        };
      });
      const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
        minPrimaryColumnWidth: 24,
        maxPrimaryColumnWidth: 42,
      });
      list.onSelect = (item) => {
        void this.handleModelSelection(item.value);
      };
      list.onCancel = () => {
        this.modelList = null;
        this.tui.requestRender();
      };
      this.modelList = { list, models: selectable, current };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.modelList = null;
      this.state.addItem(addError(snapshot.sessionId, `Failed to load models: ${message}`));
    }
  }

  private async handleModelSelection(modelName: string): Promise<void> {
    if (!modelName) {
      return;
    }
    if (isReservedMultimodalModelKey(modelName)) {
      this.modelList = null;
      this.state.addItem(
        addError(
          this.state.getSnapshot().sessionId,
          "Cannot select video, audio, or vision as the default chat model. Configure multimodal APIs in /config edit (Vision / Audio / Video) or /config set (e.g. vision_model, audio_model, video_model).",
        ),
      );
      this.tui.requestRender();
      return;
    }
    this.modelList = null;
    try {
      const payload = await this.state.request<{
        current?: string;
        requested?: string;
        applied?: boolean;
      }>("command.model", { model: modelName });
      const nextModel = payload.current ?? modelName;
      this.state.setModel(nextModel);
      this.state.clearEntries();
      this.state.addItem(
        addInfo(this.state.getSnapshot().sessionId, `Switched model to: ${nextModel}`, "m"),
      );
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.state.addItem(
        addError(this.state.getSnapshot().sessionId, `Failed to switch model: ${message}`),
      );
      this.tui.requestRender();
    }
  }

  private buildModelListLines(width: number): string[] {
    if (!this.modelList) {
      return [];
    }
    return [
      padToWidth(
        palette.status.warning(`Available models (${this.modelList.models.length} total)`),
        width,
      ),
      ...this.modelList.list.render(width),
      padToWidth(palette.text.dim("choose model · Enter switch · Esc cancel"), width),
    ];
  }

  private async openMcpList(): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<McpListPayload>("command.mcp", { action: "list" });
      const items = payload.items ?? [];
      if (items.length === 0) {
        this.mcpList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No MCP servers configured", "m"));
        return;
      }

      const selectItems: SelectItem[] = items.map((x) => ({
        label: `${x.name} | ${x.transport}${x.enabled ? " · ✔ enabled" : " · ◯ disabled"}`,
        value: x.name,
      }));
      const list = new SelectList(
        selectItems,
        Math.min(Math.max(selectItems.length, 1), 8),
        selectListTheme,
        { minPrimaryColumnWidth: 24, maxPrimaryColumnWidth: 42 },
      );
      list.onSelect = (item) => {
        void this.handleMcpSelection(item.value);
      };
      list.onCancel = () => {
        this.mcpList = null;
        this.tui.requestRender();
      };
      this.mcpList = { list, items };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.mcpList = null;
      this.state.addItem(addError(snapshot.sessionId, `mcp list failed: ${message}`));
    }
  }

  private async handleMcpSelection(serverName: string): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<{
        type: string;
        item?: Record<string, unknown>;
      }>("command.mcp", { action: "show", name: serverName });
      if (payload.type === "detail" && payload.item) {
        const enabled = Boolean(payload.item.enabled !== false);
        const actionItems: SelectItem[] = [];
        actionItems.push({ label: "View tools", value: "view_tools", description: "Browse tools from this server" });
        if (enabled) {
          actionItems.push({ label: "Disable", value: "disable", description: "Stop and disable this server" });
        } else {
          actionItems.push({ label: "Enable", value: "enable", description: "Enable this server" });
        }
        actionItems.push({ label: "Remove", value: "remove", description: "Remove this server from config" });
        const actionsList = new SelectList(actionItems, actionItems.length, selectListTheme, {
          minPrimaryColumnWidth: 24,
          maxPrimaryColumnWidth: 42,
        });
        actionsList.onSelect = (item) => {
          void this.handleMcpDetailAction(serverName, item.value);
        };
        actionsList.onCancel = () => {
          this.mcpDetail = null;
          this.openMcpList();
        };
        this.mcpList = null;
        this.mcpDetail = {
          serverName,
          info: payload.item,
          enabled,
          actions: actionsList,
        };
        this.tui.requestRender();
      } else {
        this.mcpList = null;
        this.state.addItem(addError(snapshot.sessionId, `MCP server '${serverName}' not found`));
        this.tui.requestRender();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.mcpList = null;
      this.state.addItem(addError(snapshot.sessionId, `mcp show failed: ${message}`));
      this.tui.requestRender();
    }
  }

  private async handleMcpDetailAction(serverName: string, action: string): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      if (action === "view_tools") {
        await this.openMcpToolsList(serverName);
        return;
      }
      if (action === "enable" || action === "disable" || action === "remove") {
        await this.state.request("command.mcp", { action, name: serverName });
        this.mcpDetail = null;
        if (action === "remove") {
          this.state.addItem(addInfo(snapshot.sessionId, `MCP server removed: ${serverName}`, "m"));
          this.tui.requestRender();
        } else {
          // After enable/disable, reopen the MCP list to show updated status
          await this.openMcpList();
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.mcpDetail = null;
      this.state.addItem(addError(snapshot.sessionId, `mcp ${action} failed: ${message}`));
      this.tui.requestRender();
    }
  }

  private buildMcpListLines(width: number): string[] {
    if (!this.mcpList) return [];
    return [
      padToWidth(palette.status.warning(`MCP servers (${this.mcpList.items.length})`), width),
      ...this.mcpList.list.render(width),
      padToWidth(palette.text.dim("↑/↓ choose · Enter show detail · Esc cancel"), width),
    ];
  }

  private buildMcpDetailLines(width: number): string[] {
    if (!this.mcpDetail) return [];
    const { serverName, info, enabled, actions } = this.mcpDetail;
    const lines: string[] = [];

    const borderFn = palette.border.panel;
    const borderV = "│";
    // Layout: " " + "│" + " " + content + " " + "│" = 6 extra chars
    const contentWidth = Math.max(1, width - 6);
    const innerWidth = contentWidth + 2;

    // Collect all boxed lines: title, detail fields, separator, actions
    const boxedLines: string[] = [];

    // Title line
    boxedLines.push(padToWidth(palette.status.warning(`MCP Server: ${serverName}`), contentWidth));

    // Detail fields
    boxedLines.push(padToWidth(
      `  Status: ${enabled ? palette.status.success("✔ enabled") : palette.text.dim("◯ disabled")}`,
      contentWidth,
    ));
    if (info.transport) {
      boxedLines.push(padToWidth(palette.text.dim(`  Transport: ${String(info.transport)}`), contentWidth));
    }
    if (info.command) {
      boxedLines.push(padToWidth(palette.text.dim(`  Command: ${String(info.command)}`), contentWidth));
    }
    if (typeof info.tool_count === "number") {
      boxedLines.push(padToWidth(palette.text.dim(`  Tools: ${info.tool_count} tool${info.tool_count === 1 ? "" : "s"}`), contentWidth));
    }
    if (info.args) {
      const argsStr = Array.isArray(info.args) ? info.args.join(" ") : String(info.args);
      boxedLines.push(padToWidth(palette.text.dim(`  Args: ${argsStr}`), contentWidth));
    }
    if (info.url) {
      boxedLines.push(padToWidth(palette.text.dim(`  URL: ${String(info.url)}`), contentWidth));
    }
    if (info.timeout_s) {
      boxedLines.push(padToWidth(palette.text.dim(`  Timeout: ${String(info.timeout_s)}s`), contentWidth));
    }

    // Blank separator line
    boxedLines.push(padToWidth("", contentWidth));

    // Actions rendered inside the box
    const actionLines = actions.render(contentWidth);
    boxedLines.push(...actionLines);

    // Top border
    lines.push(" " + borderFn("╭" + "─".repeat(innerWidth) + "╮"));
    // Boxed content
    for (const bl of boxedLines) {
      lines.push(" " + borderFn(borderV) + " " + padToWidth(bl, contentWidth) + " " + borderFn(borderV));
    }
    // Bottom border
    lines.push(" " + borderFn("╰" + "─".repeat(innerWidth) + "╯"));

    lines.push(padToWidth(palette.text.dim("↑/↓ choose · Enter select · Esc back"), width));
    return lines;
  }

  private async openMcpToolsList(serverName: string): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<{
        type: string;
        tools: McpToolItem[];
        server_name: string;
      }>("command.mcp", { action: "list_tools", name: serverName });
      const tools = payload.tools ?? [];
      const toolItems: SelectItem[] = tools.map((t) => ({
        label: t.name,
        value: t.id,
        description: t.description ? (t.description.length > 60 ? t.description.slice(0, 57) + "..." : t.description) : "",
      }));
      const list = new SelectList(toolItems, Math.min(Math.max(toolItems.length, 1), 10), selectListTheme, {
        minPrimaryColumnWidth: 24,
        maxPrimaryColumnWidth: 50,
      });
      list.onSelect = (item) => {
        const tool = tools.find((t) => t.id === item.value);
        if (tool) {
          this.mcpToolDetail = { serverName, tool };
          this.tui.requestRender();
        }
      };
      list.onCancel = () => {
        this.mcpTools = null;
        void this.handleMcpSelection(serverName);
      };
      this.mcpDetail = null;
      this.mcpTools = { serverName, tools, list };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.mcpDetail = null;
      this.state.addItem(addError(snapshot.sessionId, `mcp list_tools failed: ${message}`));
      this.tui.requestRender();
    }
  }

  private buildMcpToolsLines(width: number): string[] {
    if (!this.mcpTools || this.mcpToolDetail) return [];
    const { serverName, tools, list } = this.mcpTools;
    const lines: string[] = [];

    const borderFn = palette.border.panel;
    const borderV = "│";
    const contentWidth = Math.max(1, width - 6);
    const innerWidth = contentWidth + 2;

    const boxedLines: string[] = [];
    boxedLines.push(padToWidth(palette.status.warning(`Tools for ${serverName} (${tools.length} tool${tools.length === 1 ? "" : "s"})`), contentWidth));

    if (tools.length === 0) {
      boxedLines.push(padToWidth(palette.text.dim("  No tools available. Enable the server first."), contentWidth));
    } else {
      const listLines = list.render(contentWidth);
      boxedLines.push(...listLines);
    }

    // Top border
    lines.push(" " + borderFn("╭" + "─".repeat(innerWidth) + "╮"));
    for (const bl of boxedLines) {
      lines.push(" " + borderFn(borderV) + " " + padToWidth(bl, contentWidth) + " " + borderFn(borderV));
    }
    lines.push(" " + borderFn("╰" + "─".repeat(innerWidth) + "╯"));

    lines.push(padToWidth(palette.text.dim("↑/↓ choose · Enter view detail · Esc back"), width));
    return lines;
  }

  private buildMcpToolDetailLines(width: number): string[] {
    if (!this.mcpToolDetail) return [];
    const { serverName, tool } = this.mcpToolDetail;
    const lines: string[] = [];

    const borderFn = palette.border.panel;
    const borderV = "│";
    const contentWidth = Math.max(1, width - 6);
    const innerWidth = contentWidth + 2;

    const boxedLines: string[] = [];

    // Title: toolname (serverName)
    boxedLines.push(padToWidth(palette.status.warning(`${tool.name} (${serverName})`), contentWidth));

    // Tool name / Full name
    boxedLines.push(padToWidth("", contentWidth));
    boxedLines.push(padToWidth(`Tool name: ${tool.name}`, contentWidth));
    boxedLines.push(padToWidth(`Full name: mcp__${serverName}__${tool.name}`, contentWidth));

    // Description
    if (tool.description) {
      boxedLines.push(padToWidth("", contentWidth));
      boxedLines.push(padToWidth("Description:", contentWidth));
      const descLines = wrapText(tool.description, contentWidth - 2);
      for (const dl of descLines) {
        boxedLines.push(padToWidth(`  ${dl}`, contentWidth));
      }
    }

    // Parameters
    if (tool.parameters && typeof tool.parameters === "object") {
      const params = tool.parameters as Record<string, unknown>;
      const properties = params.properties as Record<string, unknown> | undefined;
      if (properties && Object.keys(properties).length > 0) {
        boxedLines.push(padToWidth("", contentWidth));
        boxedLines.push(padToWidth("Parameters:", contentWidth));
        for (const [paramName, paramDef] of Object.entries(properties)) {
          const def = paramDef as Record<string, unknown>;
          const typeStr = def.type ? String(def.type) : "any";
          const required = Array.isArray(params.required) && params.required.includes(paramName);
          const reqMark = required ? " (required)" : "";
          const descText = def.description ? ` - ${String(def.description)}` : "";
          const paramLine = `  • ${paramName}${reqMark}: ${typeStr}${descText}`;
          const paramLines = wrapText(paramLine, contentWidth - 2);
          for (const pl of paramLines) {
            boxedLines.push(padToWidth(pl, contentWidth));
          }
        }
      }
    }

    // Top border
    lines.push(" " + borderFn("╭" + "─".repeat(innerWidth) + "╮"));
    for (const bl of boxedLines) {
      lines.push(" " + borderFn(borderV) + " " + padToWidth(bl, contentWidth) + " " + borderFn(borderV));
    }
    lines.push(" " + borderFn("╰" + "─".repeat(innerWidth) + "╯"));

    lines.push(padToWidth(palette.text.dim("Esc to go back"), width));
    return lines;
  }

  private openThemeList(): void {
    const snapshot = this.state.getSnapshot();
    const current = snapshot.themeName ?? "dark";
    const options: readonly ["dark", "light"] = ["dark", "light"];
    const items: SelectItem[] = options.map((theme) => ({
      value: theme,
      label: theme === current ? `${theme} ✔` : theme,
    }));
    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
      minPrimaryColumnWidth: 24,
      maxPrimaryColumnWidth: 42,
    });
    list.onSelect = (item) => {
      this.themeList = null;
      this.state.setThemeName(item.value as "dark" | "light");
      this.state.addItem(
        addInfo(this.state.getSnapshot().sessionId, `Theme set to ${item.value}`, "t"),
      );
      this.tui.requestRender();
    };
    list.onCancel = () => {
      this.themeList = null;
      this.tui.requestRender();
    };
    this.themeList = { list, current };
    this.tui.requestRender();
  }

  private buildThemeListLines(width: number): string[] {
    if (!this.themeList) {
      return [];
    }
    return [
      padToWidth(palette.status.warning("Theme"), width),
      ...this.themeList.list.render(width),
      padToWidth(palette.text.dim("↑/↓ choose · Enter to select · Esc to cancel"), width),
    ];
  }

  private buildOutgoingMessage(text: string): { content: string; attachments: FileAttachment[] } {
    return {
      content: text.replace(/[ \t]{2,}/g, " ").replace(/[ \t]+\n/g, "\n").trim(),
      attachments: this.collectComposerAttachments(text),
    };
  }

  private buildConfigEditorLines(width: number): string[] {
    if (!this.configEditorState) {
      return [];
    }
    const state = this.configEditorState;
    const title =
      state.phase === "select_group"
        ? "Configuration Editor"
        : state.phase === "select_item"
          ? state.selectedGroup ?? "Config"
          : state.phase === "select_value"
            ? `Select value for "${state.selectedKey}"`
            : `Enter new value for "${state.selectedKey}"`;
    const hint =
      state.phase === "input_value"
        ? "Enter value · Esc back"
        : "↑/↓ choose · Enter confirm · Esc cancel";

    const lines: string[] = [
      padToWidth(palette.status.warning(title), width),
    ];

    if (
      (state.phase === "select_value" || state.phase === "input_value") &&
      state.selectedKey
    ) {
      const schema = state.schemaList.find((s) => s.key === state.selectedKey);
      const rawVal = state.currentValues[state.selectedKey] ?? "";
      const currentVal = schema?.sensitive
        ? rawVal.length > 8 ? `${rawVal.slice(0, 4)}****${rawVal.slice(-4)}` : rawVal ? "***" : "(empty)"
        : rawVal || "(empty)";
      lines.push(padToWidth(palette.text.dim(`current: ${currentVal}`), width));
    }

    if (state.phase === "input_value") {
      lines.push(...this.editor.render(width));
    } else {
      lines.push(...state.list.render(width));
    }

    lines.push(padToWidth(palette.text.dim(hint), width));
    return lines;
  }

  private openConfigEditor(
    focusKey?: string,
    configPayload?: Record<string, unknown> & { schema?: ConfigItemSchema[] },
  ): void {
    const schemaList = configPayload?.schema ?? [];
    if (schemaList.length === 0) {
      this.state.addItem(addError(this.state.getSnapshot().sessionId, "No config schema available"));
      return;
    }
    const currentValues: Record<string, string> = {};
    for (const schema of schemaList) {
      currentValues[schema.key] = String(configPayload?.[schema.key] ?? "");
    }

    if (focusKey) {
      const schema = schemaList.find((s) => s.key === focusKey);
      if (schema && schema.type === "select" && schema.options) {
        // 用临时的 select_group 状态承载 schemaList/currentValues，再 showConfigValueSelect 会替换成 select_value
        this.configEditorState = {
          phase: "select_group",
          schemaList,
          currentValues,
          selectedGroup: null,
          selectedKey: null,
          list: new SelectList([], 1, selectListTheme),
        };
        this.showConfigValueSelect(schema, currentValues);
        return;
      }
    }

    this.showConfigGroupSelector(schemaList, currentValues);
  }

  private showConfigGroupSelector(
    schemaList: ConfigItemSchema[],
    currentValues: Record<string, string>,
  ): void {
    const groups: Record<string, ConfigItemSchema[]> = {};
    for (const schema of schemaList) {
      const group = schema.group || "Other";
      if (!groups[group]) groups[group] = [];
      groups[group].push(schema);
    }

    const groupItems: SelectItem[] = Object.keys(groups).map((groupName) => ({
      value: groupName,
      label: groupName,
      description: `${groups[groupName].length} items`,
    }));
    const list = new SelectList(
      groupItems,
      Math.min(Math.max(groupItems.length, 1), 8),
      selectListTheme,
      { minPrimaryColumnWidth: 24, maxPrimaryColumnWidth: 42 },
    );
    list.onSelect = (item) => {
      this.showConfigGroupItems(item.value, groups[item.value], currentValues);
    };
    list.onCancel = () => {
      if (this.statusViewState) {
        this.statusViewState.phase = "tab_view";
        this.statusViewState.tab = "config";
        this.rebuildStatusViewTabList();
        this.configEditorState = null;
        this.tui.requestRender();
      } else {
        this.configEditorState = null;
        this.tui.requestRender();
      }
    };
    this.configEditorState = {
      phase: "select_group",
      schemaList,
      currentValues,
      selectedGroup: null,
      selectedKey: null,
      list,
    };
    this.tui.requestRender();
  }

  private showConfigGroupItems(
    groupName: string,
    schemas: ConfigItemSchema[],
    currentValues: Record<string, string>,
  ): void {
    const items: SelectItem[] = schemas.map((schema) => {
      const val = currentValues[schema.key] ?? "";
      const displayVal =
        schema.type === "toggle"
          ? val === "true" ? "Enabled" : "Disabled"
          : schema.sensitive
            ? val.length > 8 ? `${val.slice(0, 4)}****${val.slice(-4)}` : "***"
            : val;
      return {
        value: schema.key,
        label: `${schema.label}: ${displayVal}`,
        description: schema.description,
      };
    });
    items.push({ value: "__back__", label: "Back", description: "" });
    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
      minPrimaryColumnWidth: 24,
      maxPrimaryColumnWidth: 42,
    });
    list.onSelect = (item) => {
      if (item.value === "__back__") {
        this.showConfigGroupSelector(this.configEditorState!.schemaList, currentValues);
        return;
      }
      const schema = schemas.find((s) => s.key === item.value);
      if (!schema) return;
      this.handleConfigItemSelection(schema, currentValues);
    };
    list.onCancel = () => {
      if (this.statusViewState) {
        this.statusViewState.phase = "tab_view";
        this.statusViewState.tab = "config";
        this.rebuildStatusViewTabList();
        this.configEditorState = null;
        this.tui.requestRender();
      } else {
        this.showConfigGroupSelector(this.configEditorState!.schemaList, currentValues);
      }
    };
    this.configEditorState = {
      phase: "select_item",
      schemaList: this.configEditorState!.schemaList,
      currentValues,
      selectedGroup: groupName,
      selectedKey: null,
      list,
    };
    this.tui.requestRender();
  }

  private handleConfigItemSelection(
    schema: ConfigItemSchema,
    currentValues: Record<string, string>,
  ): void {
    if (schema.type === "toggle") {
      const currentVal = currentValues[schema.key] ?? "false";
      const newValue = currentVal === "true" ? "false" : "true";
      void this.applyConfigEditorSet(schema.key, newValue, schema, currentValues);
      return;
    }
    if (schema.type === "select" && schema.options) {
      this.showConfigValueSelect(schema, currentValues);
      return;
    }
    // string / password → input mode
    this.editor.setText("");
    this.configEditorState = {
      phase: "input_value",
      schemaList: this.configEditorState!.schemaList,
      currentValues,
      selectedGroup: this.configEditorState!.selectedGroup,
      selectedKey: schema.key,
      list: this.configEditorState!.list,
    };
    this.tui.requestRender();
  }

  private showConfigValueSelect(
    schema: ConfigItemSchema,
    currentValues: Record<string, string>,
  ): void {
    const currentValue = currentValues[schema.key] ?? "";
    const items: SelectItem[] = (schema.options ?? []).map((option) => ({
      value: option,
      label: option,
      description: option === currentValue ? "(current)" : undefined,
    }));
    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
      minPrimaryColumnWidth: 24,
      maxPrimaryColumnWidth: 42,
    });
    list.onSelect = (item) => {
      void this.applyConfigEditorSet(schema.key, item.value, schema, currentValues);
    };
    list.onCancel = () => {
      if (this.configEditorState?.selectedGroup) {
        const groupSchemas = this.configEditorState.schemaList.filter(
          (s) => s.group === this.configEditorState!.selectedGroup,
        );
        this.showConfigGroupItems(this.configEditorState.selectedGroup, groupSchemas, currentValues);
      } else if (this.statusViewState) {
        this.statusViewState.phase = "tab_view";
        this.statusViewState.tab = "config";
        this.rebuildStatusViewTabList();
        this.configEditorState = null;
        this.tui.requestRender();
      } else {
        this.configEditorState = null;
        this.tui.requestRender();
      }
    };
    this.configEditorState = {
      phase: "select_value",
      schemaList: this.configEditorState!.schemaList,
      currentValues,
      selectedGroup: this.configEditorState?.selectedGroup ?? null,
      selectedKey: schema.key,
      list,
    };
    this.tui.requestRender();
  }

  private async applyConfigEditorSet(
    key: string,
    value: string,
    schema: ConfigItemSchema,
    currentValues: Record<string, string>,
  ): Promise<void> {
    try {
      const result = await this.state.request<{
        updated: string[];
        applied_without_restart: boolean;
      }>("config.set", { [key]: value });
      currentValues[key] = value;
      const msg = result.applied_without_restart
        ? `✓ ${key}: ${schema.sensitive ? "***" : value} (applied)`
        : `✓ ${key}: ${schema.sensitive ? "***" : value} (restart required)`;
      this.state.addItem(addInfo(this.state.getSnapshot().sessionId, msg, "c"));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.state.addItem(addError(this.state.getSnapshot().sessionId, `config.set failed: ${message}`));
    }
    if (this.statusViewState) {
      // Return to config tab in StatusView instead of closing entirely
      this.statusViewState.phase = "tab_view";
      this.statusViewState.tab = "config";
      this.rebuildStatusViewTabList();
      this.configEditorState = null;
      this.tui.requestRender();
    } else {
      this.configEditorState = null;
      this.tui.requestRender();
    }
  }

  // ──────────────────────────── StatusView ────────────────────────────

  private async openStatusView(tab?: StatusViewTab): Promise<void> {
    const initialTab: StatusViewTab = tab ?? "status";

    // Fetch status payload
    let statusPayload: import("../core/commands/builtins/status.js").StatusPayload | null = null;
    try {
      statusPayload = await this.state.request<import("../core/commands/builtins/status.js").StatusPayload>(
        "command.status",
        {},
      );
    } catch {
      // proceed with null — tab will show placeholder
    }

    // Fetch config payload (needed for Config tab)
    let configPayload: (Record<string, unknown> & { schema?: ConfigItemSchema[] }) | null = null;
    try {
      configPayload = await this.state.request<Record<string, unknown> & { schema?: ConfigItemSchema[] }>(
        "config.get",
        {},
      );
    } catch {
      // proceed with null
    }

    this.statusViewState = {
      phase: "tab_view",
      tab: initialTab,
      list: this.buildStatusViewTabState(initialTab, statusPayload, configPayload),
      statusPayload,
      configPayload,
    };
    this.tui.requestRender();
  }

  private buildStatusViewTabState(
    tab: StatusViewTab,
    statusPayload: import("../core/commands/builtins/status.js").StatusPayload | null,
    configPayload: (Record<string, unknown> & { schema?: ConfigItemSchema[] }) | null,
  ): SelectList {
    const items: SelectItem[] =
      tab === "status"
        ? this.buildStatusTabItems(statusPayload)
        : tab === "usage"
          ? this.buildUsageTabItems()
          : this.buildConfigTabItems(configPayload);

    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 10), selectListTheme, {
      minPrimaryColumnWidth: 20,
      maxPrimaryColumnWidth: 50,
    });
    list.onSelect = (item) => {
      if (tab === "config" && item.value !== "__display__") {
        this.transitionToConfigEditor(item.value);
      }
    };
    list.onCancel = () => {
      this.closeStatusView();
    };
    return list;
  }

  private buildStatusTabItems(
    payload: import("../core/commands/builtins/status.js").StatusPayload | null,
  ): SelectItem[] {
    if (!payload) {
      return [{ value: "__display__", label: "Failed to load status data", description: "" }];
    }
    const snapshot = this.state.getSnapshot();
    const items: SelectItem[] = [
      { value: "__display__", label: `version: ${payload.version || "unknown"}`, description: "" },
      { value: "__display__", label: `session: ${payload.session_id || snapshot.sessionId}`, description: "" },
      { value: "__display__", label: `name: ${snapshot.sessionTitle || "/rename to add a name"}`, description: "" },
      { value: "__display__", label: `cwd: ${payload.cwd || "unknown"}`, description: "" },
      { value: "__display__", label: `mode: ${snapshot.mode}`, description: "" },
      { value: "__display__", label: `model: ${payload.model || "unknown"}`, description: "" },
      { value: "__display__", label: `provider: ${payload.provider || "unknown"}`, description: "" },
      { value: "__display__", label: `api_base: ${payload.api_base || "unknown"}`, description: "" },
      { value: "__display__", label: `connection: ${payload.connection_status || snapshot.connectionStatus}`, description: "" },
    ];

    const mcpServers = payload.mcp_servers ?? [];
    for (const srv of mcpServers) {
      items.push({
        value: "__display__",
        label: `mcp: ${srv.name}`,
        description: `${srv.transport} | ${srv.enabled ? "enabled" : "disabled"}`,
      });
    }

    const sources = payload.settings_sources ?? [];
    for (const s of sources) {
      items.push({ value: "__display__", label: `config_source: ${s}`, description: "" });
    }
    items.push({ value: "__display__", label: `config_path: ${payload.config_path || "unknown"}`, description: "" });

    return items;
  }

  private buildUsageTabItems(): SelectItem[] {
    const summary = this.state.getUsageSummary();
    const fmt = (n: number) => n.toLocaleString("en-US");
    const items: SelectItem[] = [
      { value: "__display__", label: `input_tokens: ${fmt(summary.total_input_tokens)}`, description: "" },
      { value: "__display__", label: `output_tokens: ${fmt(summary.total_output_tokens)}`, description: "" },
      { value: "__display__", label: `total_tokens: ${fmt(summary.total_tokens)}`, description: "" },
    ];

    for (const entry of summary.byModel) {
      items.push(
        { value: "__display__", label: `model: ${entry.model}`, description: `${fmt(entry.total_tokens)} tokens` },
        { value: "__display__", label: `  input`, description: fmt(entry.input_tokens) },
        { value: "__display__", label: `  output`, description: fmt(entry.output_tokens) },
      );
    }
    return items;
  }

  private buildConfigTabItems(
    configPayload: (Record<string, unknown> & { schema?: ConfigItemSchema[] }) | null,
  ): SelectItem[] {
    if (!configPayload?.schema?.length) {
      return [{ value: "__display__", label: "No config schema available", description: "" }];
    }
    const schemaList = configPayload.schema;
    const groups: Record<string, ConfigItemSchema[]> = {};
    for (const schema of schemaList) {
      const group = schema.group || "Other";
      if (!groups[group]) groups[group] = [];
      groups[group].push(schema);
    }

    const items: SelectItem[] = [];
    for (const groupName of Object.keys(groups)) {
      const groupSchemas = groups[groupName];
      items.push({ value: "__display__", label: groupName, description: `${groupSchemas.length} items` });
      for (const schema of groupSchemas) {
        const val = String(configPayload[schema.key] ?? "");
        const displayVal =
          schema.type === "toggle"
            ? val === "true" ? "Enabled" : "Disabled"
            : schema.sensitive
              ? val.length > 8 ? `${val.slice(0, 4)}****${val.slice(-4)}` : "***"
              : val || "(empty)";
        items.push({
          value: schema.key,
          label: `  ${schema.label}: ${displayVal}`,
          description: schema.description,
        });
      }
    }
    return items;
  }

  private renderTabBar(width: number): string[] {
    const tabs: StatusViewTab[] = ["status", "usage", "config"];
    const labels = tabs.map((t) => (t === this.statusViewState!.tab ? `[${t}]` : ` ${t} `));
    const barText = labels.join("  ");
    const activeIndex = tabs.indexOf(this.statusViewState!.tab);
    // Highlight active tab
    const parts: string[] = [];
    let pos = 0;
    for (let i = 0; i < labels.length; i++) {
      const seg = labels[i];
      if (i === activeIndex) {
        parts.push(palette.status.warning(seg));
      } else {
        parts.push(palette.text.dim(seg));
      }
      pos += seg.length;
      if (i < labels.length - 1) {
        parts.push(palette.text.dim("  "));
        pos += 2;
      }
    }
    const combined = parts.join("");
    return [padToWidth(combined, width)];
  }

  private getTabHint(tab: StatusViewTab): string {
    if (tab === "status" || tab === "usage") {
      return "←/→ switch tab · Esc close";
    }
    return "←/→ switch tab · Enter edit item · Esc close";
  }

  private buildStatusViewLines(width: number): string[] {
    if (!this.statusViewState) return [];
    if (this.statusViewState.phase === "config_editor") {
      return this.buildConfigEditorLines(width);
    }
    const lines: string[] = [];
    lines.push(...this.renderTabBar(width));
    lines.push(padToWidth(palette.status.warning("Status"), width));
    lines.push(...this.statusViewState.list.render(width));
    lines.push(padToWidth(palette.text.dim(this.getTabHint(this.statusViewState.tab)), width));
    return lines;
  }

  private switchStatusViewTab(direction: -1 | 1): void {
    if (!this.statusViewState || this.statusViewState.phase !== "tab_view") return;
    const tabs: StatusViewTab[] = ["status", "usage", "config"];
    const current = tabs.indexOf(this.statusViewState.tab);
    const next = (current + direction + tabs.length) % tabs.length;
    this.statusViewState.tab = tabs[next];
    this.rebuildStatusViewTabList();
    this.tui.requestRender();
  }

  private rebuildStatusViewTabList(): void {
    if (!this.statusViewState) return;
    this.statusViewState.list = this.buildStatusViewTabState(
      this.statusViewState.tab,
      this.statusViewState.statusPayload,
      this.statusViewState.configPayload,
    );
    this.tui.requestRender();
  }

  private transitionToConfigEditor(key: string): void {
    if (!this.statusViewState?.configPayload?.schema?.length) return;
    const schemaList = this.statusViewState.configPayload.schema;
    const schema = schemaList.find((s) => s.key === key);
    if (!schema) return;

    const currentValues: Record<string, string> = {};
    for (const s of schemaList) {
      currentValues[s.key] = String(this.statusViewState.configPayload?.[s.key] ?? "");
    }

    this.statusViewState.phase = "config_editor";
    this.configEditorState = {
      phase: "select_group",
      schemaList,
      currentValues,
      selectedGroup: null,
      selectedKey: null,
      list: new SelectList([], 1, selectListTheme),
    };

    // Navigate directly to the item's group or value
    const group = schema.group || "Other";
    const groupSchemas = schemaList.filter((s) => (s.group || "Other") === group);
    this.showConfigGroupItems(group, groupSchemas, currentValues);
  }

  private closeStatusView(): void {
    this.statusViewState = null;
    this.configEditorState = null;
    this.tui.requestRender();
  }

  private syncComposerAttachmentsFromEditor(): void {
    if (this.syncingComposerInput) {
      return;
    }

    const originalText = this.editor.getText();
    const { normalizedText, attachments } = syncComposerImageTokens(
      originalText,
      this.composerAttachments,
      (path) => this.isComposerImageFile(path),
    );

    this.composerAttachments = attachments;

    if (normalizedText !== originalText) {
      this.syncingComposerInput = true;
      this.editor.setText(normalizedText);
      this.syncingComposerInput = false;
    }
  }

  private deleteComposerAttachmentTokenBackwards(): boolean {
    const cursor = this.editor.getCursor();
    const lines = this.editor.getLines();
    const currentLine = lines[cursor.line] ?? "";
    const tokenRange = findAttachmentTokenAtCursor(currentLine, cursor.col);
    if (!tokenRange) {
      return false;
    }

    const nextLine =
      `${currentLine.slice(0, tokenRange.start)}${currentLine.slice(tokenRange.end)}`.replace(
        / {2,}/g,
        " ",
      );
    const nextLines = [...lines];
    nextLines[cursor.line] = nextLine;
    const nextText = nextLines.join("\n");
    const nextCol = Math.min(tokenRange.start, nextLine.length);

    this.syncingComposerInput = true;
    this.editor.setText(nextText);
    const ed = this.editor as unknown as {
      state: { cursorLine: number };
      setCursorCol: (col: number) => void;
    };
    ed.state.cursorLine = cursor.line;
    ed.setCursorCol(nextCol);
    this.syncingComposerInput = false;
    this.syncComposerAttachmentsFromEditor();
    this.tui.requestRender();
    return true;
  }

  private collectComposerAttachments(text: string): FileAttachment[] {
    const cwd = getTrustedDirs()[0] || process.cwd();
    return extractAttachmentsFromText(text, {
      cwd,
      classifyAttachment: (path) => (this.isAcceptedAttachment(path) ? (isImageAttachment(path) ? "image" : "file") : null),
    }).map(({ resolvedPath, ...attachment }) => attachment);
  }

  private isAcceptedAttachment(path: string): boolean {
    if (!isSupportedAttachment(path)) {
      return false;
    }

    try {
      const stats = statSync(path);
      if (!stats.isFile()) {
        return false;
      }
      return true;
    } catch {
      return false;
    }
  }

  private isComposerImageFile(path: string): boolean {
    return this.isAcceptedAttachment(path) && isImageAttachment(path);
  }

  /** Handle pasted/dragged content - detects file paths and converts to @path references. */
  private handleDroppedFiles(filePaths: string[]): boolean {
    const insertText = filePaths
      .filter((path) => this.isAcceptedAttachment(path))
      .map((path) => formatAttachmentMention(path))
      .join(" ");

    if (!insertText) return false;

    const currentText = this.editor.getText();
    const newText = currentText ? `${currentText}\n${insertText}` : insertText;
    this.syncingComposerInput = true;
    this.editor.setText(newText);
    this.syncingComposerInput = false;
    this.tui.requestRender();
    return true;
  }

  private syncAnimationLoop(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const hasRunningTools = snapshot.toolExecutions.some(
      (execution) => execution.tool.status === "running",
    );
    const teamWorking =
      isTeamMode(snapshot.mode) &&
      isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    const teamStartedAt = teamWorkingStartedAtMs(
      snapshot.teamMemberEvents,
      snapshot.teamMessageEvents,
    );
    const shouldAnimate =
      !snapshot.isInterrupted && (snapshot.isProcessing || hasRunningTools || teamWorking);
    if (!shouldAnimate) {
      const nowMs = Date.now();
      if (this.runningStoppedAtMs === null) {
        this.runningStoppedAtMs = nowMs;
      }
      if (this.animationTimer) {
        clearInterval(this.animationTimer);
        this.animationTimer = null;
      }
      this.animationPhase = 0;
      if (
        this.runningStartedAtMs !== null &&
        nowMs - this.runningStoppedAtMs >= RUNNING_TIMER_RESET_GRACE_MS
      ) {
        this.runningStartedAtMs = null;
        this.runningStoppedAtMs = null;
      }
      return;
    }
    this.runningStoppedAtMs = null;
    if (snapshot.isProcessing) {
      if (this.runningStartedAtMs === null) {
        this.runningStartedAtMs = Date.now();
      }
    } else if (teamWorking) {
      this.runningStartedAtMs = teamStartedAt ?? this.runningStartedAtMs ?? Date.now();
    }
    if (this.animationTimer) {
      return;
    }
    this.animationTimer = setInterval(() => {
      this.animationPhase = (this.animationPhase + 1) % 12;
      this.tui.requestRender();
    }, 220);
  }

  private applySlashCommandHint(editorLines: string[], width: number): string[] {
    const hint = this.getInlineSlashCommandHint();
    if (!hint || editorLines.length < 3) {
      return editorLines;
    }

    const contentIndex = 1;
    const line = editorLines[contentIndex] ?? "";
    const cursorIndex = line.indexOf(END_CURSOR);
    if (cursorIndex === -1) {
      return editorLines;
    }

    const hintedLine = padToWidth(
      line.replace(END_CURSOR, `${END_CURSOR}${palette.text.dim(` ${hint}`)}`),
      width,
    );

    const nextLines = [...editorLines];
    nextLines[contentIndex] = hintedLine;
    return nextLines;
  }

  private getInlineSlashCommandHint(): string | null {
    const text = this.editor.getText();
    if (!text.startsWith("/") || text.includes("\n")) {
      return null;
    }

    const cursor = this.editor.getCursor();
    const lines = this.editor.getLines();
    const currentLine = lines[cursor.line] ?? "";
    if (cursor.line !== 0 || cursor.col !== currentLine.length) {
      return null;
    }

    const parsed = parseSlashCommand(text, this.commands.getAll());
    if (!parsed.command || parsed.args.trim()) {
      return null;
    }

    const usage = parsed.command.usage?.trim() ?? "";
    if (!usage.startsWith("/")) {
      return null;
    }

    const suffix = usage.replace(/^\/[^\s]+/, "").trim();
    return suffix || null;
  }

  /**
   * Builds a fresh {@link ComposerAutocompleteProvider} wrapping a new
   * {@link CombinedAutocompleteProvider}.  Skill shorthands are prepended to
   * the regular slash-command list so they appear first in the dropdown.
   *
   * @param skills - snapshot of the installed-skills cache; defaults to the
   *   current cache exposed by {@link CommandService.getInstalledSkills}.
   */
  private rebuildAutocompleteProvider(
    skills: readonly InstalledSkillEntry[] = this.commands.getInstalledSkills(),
  ): ComposerAutocompleteProvider {
    // Convert each installed skill to a TuiSlashCommand so CombinedAutocompleteProvider
    // treats /<skillName> exactly like any other slash command for name completion.
    const registeredNames = new Set(this.commands.getAll().map((c) => c.name));
    const skillCommands: TuiSlashCommand[] = skills
      .filter((skill) => !registeredNames.has(skill.name))
      .map((skill) => ({
        name: skill.name,
        description: skill.description || `Use the "${skill.name}" skill`,
      }));

    return new ComposerAutocompleteProvider(
      new CombinedAutocompleteProvider(
        // Skill shorthands come last so they appear at the bottom of the dropdown.
        [...this.buildSlashCommands(), ...skillCommands],
        getTrustedDirs()[0] || process.cwd(),
        resolveFdBinary(),
      ),
    );
  }

  private buildSlashCommands(): TuiSlashCommand[] {
    const hasAnyCompletion = (cmd: SlashCommand): boolean =>
      !!cmd.completion || (cmd.subCommands?.some(hasAnyCompletion) ?? false);
    return this.commands.getAll().map((command) => ({
      name: command.name,
      description: command.description,
      getArgumentCompletions: hasAnyCompletion(command)
        ? async (argumentPrefix: string): Promise<AutocompleteItem[] | null> => {
            const trimmed = argumentPrefix.trim();
            // Traverse subcommand chain to find the deepest command with completion
            let currentCommand: typeof command = command;
            let matchedPath: string[] = [];
            let remainingTokens: string[] = [];

            if (currentCommand.subCommands?.length && trimmed.length > 0) {
              const tokens = trimmed.split(/\s+/).filter(Boolean);
              let matchIndex = 0;

              for (let i = 0; i < tokens.length; i++) {
                const token = tokens[i];
                const matchedSub = currentCommand.subCommands?.find(
                  (sub) => sub.name === token || sub.altNames?.includes(token)
                );
                if (!matchedSub) {
                  // No more subcommand matches, remaining tokens are args
                  remainingTokens = tokens.slice(i);
                  break;
                }

                matchedPath.push(matchedSub.name);
                currentCommand = matchedSub;
                matchIndex = i + 1;
              }

              // If all tokens matched subcommands, remainingTokens is empty
              if (matchIndex >= tokens.length) {
                remainingTokens = [];
              }
            }

            // Use the deepest matched command's completion if available
            if (currentCommand.completion) {
              if (currentCommand.name === "mode") {
                return buildModeAutocompleteItems();
              }
              const remainingArgs = remainingTokens.join(" ");
              const items = await currentCommand.completion(this.state.getCommandContext(), remainingArgs);
              const prefix = matchedPath.length > 0 ? matchedPath.join(" ") + " " : "";
              const suffix = currentCommand.completionSuffix ?? "";
              return items.map((value) => ({
                value: prefix + value + suffix,
                label: value,
                description: "",
              }));
            }

            return null;
          }
        : undefined,
    }));
  }

  private buildPendingQuestionLines(
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
    width: number,
  ): string[] {
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      return [];
    }

    const question =
      pendingQuestion.questions[this.activeQuestionIndex] ?? pendingQuestion.questions[0];
    if (!question) {
      return [];
    }

    const total = pendingQuestion.questions.length;
    const progress = total > 1 ? ` (${this.activeQuestionIndex + 1}/${total})` : "";
    const permissionRequest = isPermissionRequest(pendingQuestion.source, question.question);
    const lines: string[] = [];

    if (permissionRequest) {
      const summary = parsePermissionSummary(question.question);
      const title = progress ? `Permission ${this.activeQuestionIndex + 1}/${total}` : "Permission";
      lines.push(...renderPermissionBlock(width, summary, title));
    } else if (this.otherInputMode) {
      lines.push(
        ...wrapPlainText(
          `[${question.header || "Question"}${progress}] ${question.question}`,
          width,
        ).map((line) => padToWidth(palette.status.warning(line), width)),
      );
      if (question.options.length > 0) {
        lines.push("");
        for (const opt of question.options) {
          const optLine = `  ${opt.label}${opt.description ? ` - ${opt.description}` : ""}`;
          lines.push(padToWidth(palette.text.dim(optLine), width));
        }
      }
      lines.push("");
      lines.push(
        ...wrapPlainText(
          `[Answer] Please enter your answer:`,
          width,
        ).map((line) => padToWidth(palette.status.info(line), width)),
      );
      lines.push(padToWidth(palette.text.dim("Type your answer · Enter submit · Esc back to options"), width));
    } else {
      lines.push(
        ...wrapPlainText(
          `[${question.header || "Question"}${progress}] ${question.question}`,
          width,
        ).map((line) => padToWidth(palette.status.warning(line), width)),
      );
    }

    if (this.questionList !== null) {
      lines.push(...this.questionList.render(width));
      lines.push(
        padToWidth(
          palette.text.dim(
            permissionRequest
              ? "↑/↓ review · Enter confirm · Esc reject"
              : "↑/↓ choose · Enter confirm · Esc reject",
          ),
          width,
        ),
      );
    }
    return lines;
  }

  private syncQuestionList(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      this.questionList = null;
      return;
    }

    const question = pendingQuestion.questions[this.activeQuestionIndex];
    if (!question || question.options.length === 0) {
      this.questionList = null;
      return;
    }

    const items: SelectItem[] = question.options.map((option) => ({
      value: option.label,
      label:
        pendingQuestion.source === "permission_interrupt"
          ? normalizePermissionOptionLabel(option.label)
          : option.label,
      description: option.description,
    }));
    const maxVisible = pendingQuestion.source === "permission_interrupt" ? 4 : 6;
    const list = new SelectList(
      items,
      Math.min(Math.max(items.length, 1), maxVisible),
      selectListTheme,
    );
    list.onSelect = (item) => {
      this.handleQuestionSelection(item.value);
    };
    list.onCancel = () => {
      const reject = question.options.find((option) => option.label === "拒绝");
      if (reject) {
        this.handleQuestionSelection(reject.label);
      } else {
        this.handleQuestionSelection("");
      }
    };
    const selectedValue = this.pendingQuestionAnswers.get(this.activeQuestionIndex);
    const selectedIndex = selectedValue
      ? items.findIndex((item) => item.value === selectedValue)
      : 0;
    if (selectedIndex >= 0) {
      list.setSelectedIndex(selectedIndex);
    }
    this.questionList = list;
  }

  private handleQuestionSelection(label: string): void {
    const snapshot = this.state.getSnapshot();
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      return;
    }

    if (label === "Other") {
      this.otherInputMode = true;
      this.questionList = null;
      this.tui.requestRender();
      return;
    }

    this.pendingQuestionAnswers.set(this.activeQuestionIndex, label);
    if (this.activeQuestionIndex < pendingQuestion.questions.length - 1) {
      this.activeQuestionIndex += 1;
      this.syncQuestionList(this.state.getSnapshot());
      this.tui.requestRender();
      return;
    }

    const answers = pendingQuestion.questions.map((question, index) => {
      const answerValue = this.pendingQuestionAnswers.get(index) ?? question.options[0]?.label ?? "";
      return {
        question: question.question,
        selected_options: [answerValue],
      };
    });
    this.state.submitQuestionAnswers(answers);
  }
}
