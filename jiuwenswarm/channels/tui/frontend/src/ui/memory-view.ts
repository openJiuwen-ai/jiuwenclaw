// ── MemoryView：页签式记忆控制台（从 app-screen.ts 提取）──
//
// 管理 /memory 的四个页签（edit/status/toggle/open）的交互、渲染和状态。
// app-screen.ts 通过持有 MemoryViewController 实例并委托调用其方法来使用。

import * as fs from "node:fs";
import * as path from "node:path";
import { type TUI, type SelectItem, SelectList } from "@mariozechner/pi-tui";
import { addInfo } from "../core/commands/helpers.js";
import type { CliPiAppState } from "../app-state.js";
import { openFileInEditor as openInExternalEditor, openFolderInExplorer } from "../core/utils/editor.js";
import { collectOrderedMemoryFiles, type MemoryFile } from "../core/commands/builtins/memory.js";
import { getDisplayPath } from "../core/commands/builtins/memory-path-utils.js";
import { palette, selectListTheme } from "./theme.js";
import { padToWidth } from "./rendering/text.js";
import { resolveAction } from "../core/keybindings/resolver.js";

// ── 类型定义 ──

export type MemoryViewTab = "edit" | "status" | "toggle" | "open";

export interface MVFile {
  path: string;
  relative_path: string;
  kind: string;
  exists: boolean;
  size: number;
  mtime: number;
  lines: number;
}

export interface MVStatus {
  current_mode: string;
  enabled: boolean;
  proactive: boolean;
  forbidden_enabled: boolean;
  auto_memory_enabled: boolean;
  auto_coding_memory?: boolean | null;
  storage_mode: string;
  engine: string;
  project_memory?: { files_count: number; total_chars: number; max_chars: number; project_dir?: string };
  coding_memory?: { files_count: number; total_chars: number; dir: string };
  auto_memory?: { files_count: number; total_chars: number; dir: string };
  external_memory?: { provider: string; enabled: boolean };
}

export interface MVOpen {
  memory_dir: string;
  project_memory_dir: string;
  project_dir?: string;
  coding_memory_dir?: string;
  auto_memory_dir?: string;
}

interface MemoryViewState {
  tab: MemoryViewTab;
  list: SelectList;
  mode: string;
  files: MVFile[];
  statusPayload: MVStatus | null;
  openPayload: MVOpen | null;
  projectDir: string;
  gitRoot: string | null;
  userMemoryPath: string;
  loading?: boolean;
}

// ── Controller ──

export class MemoryViewController {
  private state: MemoryViewState | null = null;
  private showFullPath = false;
  /** 底部通知行：显示最新一次操作结果，新覆盖旧 */
  private statusMessage: string | null = null;
  /** 上一次 open 的目录绝对路径；Ctrl+O 切换时据此重算 statusMessage 的显示格式 */
  private lastOpenedPath: string | null = null;

  constructor(
    private appState: CliPiAppState,
    private tui: TUI,
  ) {}

  // ── 状态查询 ──

  get isOpen(): boolean {
    return this.state !== null;
  }

  // ── 补全 ──

  /**
   * 获取 /memory edit|toggle 参数补全列表（带类型标识）。
   * edit：返回文件路径 + 记忆类型（Project/Local/User memory）
   * toggle：返回开关 key + 中文描述
   */
  async getMemoryCompletions(sub: string): Promise<{ label: string; description: string }[]> {
    const ctx = this.appState.getCommandContext();
    const mode = this.shortMode(ctx.mode ?? "code.normal");

    if (sub === "edit") {
      const { files, projectDir, userMemoryPath } = await collectOrderedMemoryFiles(ctx, mode).catch(() => ({
        files: [] as MemoryFile[],
        userMemoryPath: "",
        projectDir: "",
      }));
      const lower = (p: string) => (process.platform === "win32" ? p.toLowerCase() : p);
      return files
        .filter((f) => f.exists)
        .map((f) => {
          const dp = getDisplayPath(f.path, projectDir);
          const p = f.path.replace(/\\/g, "/");
          let desc = "";
          if (lower(f.path) === lower(userMemoryPath)) {
            desc = "User memory";
          } else if (
            f.kind === "project"
            && p.endsWith("JIUWENSWARM.md")
            && !p.endsWith("JIUWENSWARM.local.md")
            && !p.endsWith(".jiuwen/JIUWENSWARM.md")
          ) {
            desc = "Project memory";
          } else if (f.kind === "local" && p.endsWith("JIUWENSWARM.local.md")) {
            desc = "Local memory";
          } else {
            desc = f.kind;
          }
          return { label: dp, description: desc };
        });
    }

    if (sub === "toggle") {
      return this.togglesForMode(mode).map((t) => ({
        label: t.key,
        description: t.desc,
      }));
    }

    return [];
  }

  // ── 打开 / 关闭 ──

  async open(tab?: MemoryViewTab): Promise<void> {
    this.showFullPath = false;
    this.statusMessage = null;
    this.lastOpenedPath = null;
    const ctx = this.appState.getCommandContext();
    const fullMode = ctx.mode ?? "code.normal";
    const projectDir = ctx.getCurrentProjectDir() || process.cwd();
    const initialTab: MemoryViewTab = tab ?? "edit";

    // 立即展示 loading 状态，不阻塞用户界面
    this.state = {
      tab: initialTab,
      list: new SelectList([], 1, selectListTheme),
      mode: fullMode,
      files: [],
      statusPayload: null,
      openPayload: null,
      projectDir,
      gitRoot: null,
      userMemoryPath: "",
      loading: true,
    };
    this.tui.requestRender();

    // 异步加载数据（首次可能需等待后端 Agent 创建完成）
    const [collected, statusPayload, openPayload] = await Promise.all([
      collectOrderedMemoryFiles(ctx, this.shortMode(fullMode)).catch(() => ({
        files: [] as MemoryFile[],
        userMemoryPath: "",
        gitRoot: null as string | null,
        projectDir,
      })),
      this.appState.request<MVStatus>("memory.status", { detailed: true, mode: this.shortMode(fullMode) }).catch(() => null),
      this.appState.request<MVOpen>("memory.open", { project_dir: projectDir }).catch(() => null),
    ]);

    if (!this.state) return; // 用户可能已关闭

    const files = collected.files;
    this.state = {
      tab: initialTab,
      list: this.buildTabState(initialTab, files, statusPayload, openPayload, fullMode, projectDir, collected.gitRoot, collected.userMemoryPath),
      mode: fullMode,
      files,
      statusPayload,
      openPayload,
      projectDir,
      gitRoot: collected.gitRoot,
      userMemoryPath: collected.userMemoryPath,
      loading: false,
    };
    this.tui.requestRender();
  }

  close(): void {
    const sessionId = this.appState.getSnapshot().sessionId;
    this.appState.addItem(addInfo(sessionId, "Memory console dismissed", "✓"));
    this.state = null;
    this.tui.requestRender();
  }

  // ── 键盘输入 ──

  /** 处理 MemoryView 上下文的键盘输入，返回 true 表示已消费。 */
  handleInput(data: string): boolean {
    if (this.state === null) return false;
    // loading 时只允许 Esc 关闭
    if (this.state.loading) {
      if (resolveAction("MemoryView", data) === "memory:close") {
        this.close();
      }
      return true;
    }
    switch (resolveAction("MemoryView", data)) {
      case "memory:close":
        this.close();
        return true;
      case "memory:prevTab":
        this.switchTab(-1);
        return true;
      case "memory:nextTab":
        this.switchTab(1);
        return true;
      case "memory:toggleFullPath":
        this.showFullPath = !this.showFullPath;
        this.rebuildTabList();
        this.reformatLastOpenedStatus();
        return true;
      default:
        break;
    }
    this.state.list.handleInput(data);
    this.tui.requestRender();
    return true;
  }

  // ── 渲染 ──

  buildLines(width: number): string[] {
    if (!this.state) return [];
    const lines: string[] = [];
    lines.push(...this.renderTabBar(width));
    lines.push(padToWidth(palette.text.dim("─".repeat(width)), width));
    if (this.state.loading) {
      lines.push(padToWidth(palette.text.dim("  Loading..."), width));
    } else if (this.state.tab === "status") {
      const items = this.buildStatusItems(this.state.statusPayload, this.state.mode);
      for (const item of items) {
        const label = item.label.padEnd(20);
        lines.push(padToWidth(`  ${label}${item.description ?? ""}`, width));
      }
    } else {
      lines.push(...this.state.list.render(width));
    }
    // 底部通知行：显示最新操作结果（新覆盖旧），仅在 toggle 页签有消息时显示
    if (this.statusMessage) {
      lines.push(padToWidth(palette.status.warning(this.statusMessage), width));
    }
    const hint =
      this.state.tab === "edit"
        ? "←/→ tabs · Enter edit file · Ctrl+O toggle full path · Esc close"
        : this.state.tab === "status"
          ? "←/→ tabs · Esc close"
          : this.state.tab === "toggle"
            ? "←/→ tabs · Enter toggle · Esc close"
            : "←/→ tabs · Enter open folder · Ctrl+O toggle full path · Esc close";
    lines.push(padToWidth(palette.text.dim(hint), width));
    return lines;
  }

  // ── 内部方法 ──

  private renderTabBar(width: number): string[] {
    const tabs: MemoryViewTab[] = ["edit", "status", "toggle", "open"];
    const labels = tabs.map((t) => (t === this.state!.tab ? `[${t}]` : t));
    const activeIndex = tabs.indexOf(this.state!.tab);
    const parts: string[] = [];
    for (let i = 0; i < labels.length; i++) {
      const seg = labels[i];
      parts.push(i === activeIndex ? palette.status.warning(seg) : palette.text.dim(seg));
      if (i < labels.length - 1) parts.push(palette.text.dim("  "));
    }
    return [padToWidth(parts.join(""), width)];
  }

  private switchTab(direction: -1 | 1): void {
    if (!this.state) return;
    const tabs: MemoryViewTab[] = ["edit", "status", "toggle", "open"];
    const current = tabs.indexOf(this.state.tab);
    const next = (current + direction + tabs.length) % tabs.length;
    this.state.tab = tabs[next];
    this.showFullPath = false;
    this.statusMessage = null;
    this.lastOpenedPath = null;
    this.rebuildTabList();
    this.tui.requestRender();
  }

  /**
   * Ctrl+O 切换 full-path 显示后,重算底部 statusMessage 里上一次 open
   * 操作的路径显示格式,使提示行与列表项的格式保持一致。
   * 仅在 open tab 有 lastOpenedPath 时生效,其他场景静默跳过。
   */
  private reformatLastOpenedStatus(): void {
    if (!this.state || !this.lastOpenedPath) return;
    const projectDir = this.state.projectDir ?? "";
    const gitRoot = this.state.gitRoot ?? null;
    const displayPath = this.showFullPath
      ? this.lastOpenedPath.replace(/\\/g, "/")
      : getDisplayPath(this.lastOpenedPath, projectDir, gitRoot);
    // 保留原有 "Opened:" / "No GUI explorer detected. ..." 前缀,只替换显示路径
    if (this.statusMessage?.startsWith("Opened: ")) {
      this.statusMessage = `Opened: ${displayPath}`;
    } else if (this.statusMessage?.startsWith("No GUI explorer detected. Path: ")) {
      this.statusMessage = `No GUI explorer detected. Path: ${displayPath}`;
    }
    this.tui.requestRender();
  }

  private rebuildTabList(): void {
    if (!this.state) return;
    const s = this.state;
    this.state.list = this.buildTabState(
      s.tab,
      s.files,
      s.statusPayload,
      s.openPayload,
      s.mode,
      s.projectDir,
      s.gitRoot,
      s.userMemoryPath,
    );
    this.tui.requestRender();
  }

  private buildTabState(
    tab: MemoryViewTab,
    files: MVFile[],
    status: MVStatus | null,
    openP: MVOpen | null,
    mode: string,
    projectDir: string,
    gitRoot: string | null,
    _userMemoryPath: string,
  ): SelectList {
    const items =
      tab === "edit"
        ? this.buildEditItems(files, projectDir, gitRoot)
        : tab === "status"
          ? this.buildStatusItems(status, mode)
          : tab === "toggle"
            ? this.buildToggleItems(status, mode)
            : this.buildOpenItems(openP, mode);

    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 12), selectListTheme, {
      minPrimaryColumnWidth: 20,
      maxPrimaryColumnWidth: 50,
    });
    list.onSelect = (item: SelectItem) => {
      this.handleSelect(tab, item, mode, projectDir);
    };
    list.onCancel = () => {
      this.close();
    };
    return list;
  }

  private buildEditItems(files: MVFile[], projectDir: string, gitRoot: string | null): SelectItem[] {
    return files.map((f) => {
      const label = this.fileLabel(f);
      const dp = this.showFullPath ? f.path.replace(/\\/g, "/") : getDisplayPath(f.path, projectDir, gitRoot);
      const isGitTracked = gitRoot && f.kind !== "local" && f.kind !== "user";
      const desc = label === dp ? undefined : isGitTracked ? `Checked in at ${dp}` : `Saved in ${dp}`;
      return { value: f.path, label, description: desc };
    });
  }

  private buildStatusItems(status: MVStatus | null, mode: string): SelectItem[] {
    if (!status) return [{ value: "__display__", label: "Failed to load status", description: "" }];
    const cat = this.modeCategory(mode);
    const items: SelectItem[] = [
      { value: "__display__", label: "Engine", description: `${status.engine} (${status.storage_mode})` },
    ];
    for (const t of this.togglesForMode(mode)) {
      items.push({ value: "__display__", label: t.label, description: t.read(status) ? "✓ on" : "✗ off" });
    }
    if (cat === "agent") {
      if (status.auto_memory)
        items.push({ value: "__display__", label: "Auto Memory", description: `${status.auto_memory.files_count} files · ${status.auto_memory.total_chars} chars` });
    } else {
      if (status.coding_memory)
        items.push({ value: "__display__", label: "Coding Memory", description: `${status.coding_memory.files_count} files · ${status.coding_memory.total_chars} chars` });
    }
    if (status.project_memory)
      items.push({ value: "__display__", label: "Project Memory", description: `${status.project_memory.files_count} files · ${status.project_memory.total_chars} chars` });
    if (status.external_memory)
      items.push({ value: "__display__", label: "External Memory", description: `${status.external_memory.provider} ${status.external_memory.enabled ? "✓" : "✗"}` });
    return items;
  }

  private buildToggleItems(status: MVStatus | null, mode: string): SelectItem[] {
    if (!status) return [{ value: "__display__", label: "Failed to load status", description: "" }];
    const toggles = this.togglesForMode(mode);
    const maxKeyLen = Math.max(...toggles.map((t) => t.key.length));
    return toggles.map((t) => ({
      value: t.key,
      label: t.key.padEnd(maxKeyLen),
      description: `${t.read(status) ? "✓ on " : "✗ off"}  ${t.desc}`,
    }));
  }

  private buildOpenItems(openP: MVOpen | null, mode: string): SelectItem[] {
    if (!openP) return [{ value: "__display__", label: "Failed to load directories", description: "" }];
    const cat = this.modeCategory(mode);
    const projectDir = this.state?.projectDir ?? "";
    const gitRoot = this.state?.gitRoot ?? null;
    const fmt = (p: string) => (this.showFullPath ? p : getDisplayPath(p, projectDir, gitRoot));
    const items: SelectItem[] = [];
    if (cat === "agent") items.push({ value: openP.memory_dir, label: "Memory Dir", description: fmt(openP.memory_dir) });
    if (cat === "code" && openP.coding_memory_dir) items.push({ value: openP.coding_memory_dir, label: "Coding Memory Dir", description: fmt(openP.coding_memory_dir) });
    items.push({ value: openP.project_memory_dir, label: "Project Dir", description: fmt(openP.project_memory_dir) });
    if (openP.project_dir) items.push({ value: openP.project_dir, label: "User Project Dir", description: fmt(openP.project_dir) });
    return items;
  }

  private async handleSelect(tab: MemoryViewTab, item: SelectItem, mode: string, _projectDir: string): Promise<void> {
    if (tab === "edit" && item.value && item.value !== "__display__") {
      const filePath = item.value;
      if (!fs.existsSync(filePath)) {
        try {
          fs.mkdirSync(path.dirname(filePath), { recursive: true });
          fs.writeFileSync(filePath, "", "utf-8");
        } catch {
          // 创建失败仍尝试打开，让编辑器报错
        }
      }
      // GUI 编辑器：异步 spawn，TUI 不阻塞，编辑器关闭后通过 onExit 回调刷新
      // 终端编辑器：spawnSync 阻塞，tui.start() 后同步调用 onExit
      openInExternalEditor(this.tui, filePath, () => {
        this.refreshFiles();
      });
      return;
    }
    if (tab === "toggle" && item.value && item.value !== "__display__") {
      const key = item.value;
      const before = this.state?.statusPayload;
      const oldVal = before ? this.togglesForMode(mode).find((t) => t.key === key)?.read(before) ?? false : false;
      try {
        await this.appState.request("memory.toggle", { key, mode: this.shortMode(mode) });
        const statusPayload = await this.appState.request<MVStatus>("memory.status", { mode: this.shortMode(mode) }).catch(() => null);
        const newVal = statusPayload ? this.togglesForMode(mode).find((t) => t.key === key)?.read(statusPayload) ?? oldVal : oldVal;
        const label = this.togglesForMode(mode).find((t) => t.key === key)?.label ?? key;
        // 底部通知行：新覆盖旧，不累积到 transcript
        this.statusMessage = `${label}: ${oldVal ? "on" : "off"} → ${newVal ? "on" : "off"} (restart session to apply)`;
        if (this.state) {
          this.state.statusPayload = statusPayload;
          this.rebuildTabList();
        }
        this.tui.requestRender();
      } catch (err) {
        this.statusMessage = `Toggle failed: ${err instanceof Error ? err.message : String(err)}`;
        this.tui.requestRender();
      }
      return;
    }
    if (tab === "open" && item.value && item.value !== "__display__") {
      const opened = openFolderInExplorer(item.value);
      // Remember the absolute path so Ctrl+O can reformat the statusMessage
      // later without re-opening the folder.
      this.lastOpenedPath = item.value;
      // Display path follows the showFullPath toggle (Ctrl+O), matching
      // the format used by buildOpenItems so the hint stays consistent
      // with what the user sees in the list.
      const projectDir = this.state?.projectDir ?? "";
      const gitRoot = this.state?.gitRoot ?? null;
      const displayPath = this.showFullPath
        ? item.value.replace(/\\/g, "/")
        : getDisplayPath(item.value, projectDir, gitRoot);
      if (opened) {
        this.statusMessage = `Opened: ${displayPath}`;
      } else {
        // No GUI explorer (e.g. headless Linux server): show a copyable
        // path hint instead of silently failing.
        this.statusMessage = `No GUI explorer detected. Path: ${displayPath}`;
      }
      this.tui.requestRender();
      return;
    }
    // status 只读，无操作
  }

  /** 编辑器关闭后重新加载文件列表，刷新 edit 页签的元信息 */
  private async refreshFiles(): Promise<void> {
    const s = this.state;
    if (!s) return;
    const ctx = this.appState.getCommandContext();
    const collected = await collectOrderedMemoryFiles(ctx, this.shortMode(s.mode)).catch(() => ({
      files: [] as MemoryFile[],
      userMemoryPath: s.userMemoryPath,
      gitRoot: s.gitRoot,
      projectDir: s.projectDir,
    }));
    s.files = collected.files;
    if (collected.gitRoot) s.gitRoot = collected.gitRoot;
    s.userMemoryPath = collected.userMemoryPath;
    this.rebuildTabList();
  }

  // ── 辅助方法 ──

  private shortMode(mode: string): string {
    if (mode.startsWith("code")) return "code";
    return mode.replace("agent.", "");
  }

  private modeCategory(mode: string): "agent" | "code" {
    return this.shortMode(mode) === "code" ? "code" : "agent";
  }

  private togglesForMode(mode: string): { key: string; label: string; desc: string; read: (s: MVStatus) => boolean }[] {
    const cat = this.modeCategory(mode);
    const all = [
      { key: "memory_enabled", label: "Memory", cats: ["agent", "code"], desc: "记忆功能总开关", read: (s: MVStatus) => s.enabled },
      { key: "memory_proactive", label: "Proactive memory", cats: ["agent"], desc: "对话中自动搜索和记录", read: (s: MVStatus) => s.proactive },
      { key: "auto_coding_memory", label: "Auto coding memory", cats: ["code"], desc: "每轮对话后自动提取记忆（需总开关开启）", read: (s: MVStatus) => s.auto_coding_memory ?? false },
      { key: "memory_forbidden_enabled", label: "Forbidden filter", cats: ["agent", "code"], desc: "过滤敏感信息", read: (s: MVStatus) => s.forbidden_enabled },
    ];
    return all.filter((t) => t.cats.includes(cat)).map((t) => ({ key: t.key, label: t.label, desc: t.desc, read: t.read }));
  }

  private fileLabel(f: MVFile): string {
    const p = f.path.replace(/\\/g, "/");
    if (f.kind === "user") return "User memory";
    if (f.kind === "local") return "Local memory";
    if (f.kind === "project" && p.endsWith("JIUWENSWARM.md") && !p.endsWith("JIUWENSWARM.local.md")) return "Project memory";
    return f.relative_path || p;
  }
}
