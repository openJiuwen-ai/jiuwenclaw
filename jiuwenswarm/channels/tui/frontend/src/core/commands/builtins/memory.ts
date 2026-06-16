import { existsSync, readdirSync, statSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, parse, relative } from "node:path";
import { addError, addInfo, makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import { getEditorInfo } from "../../utils/editor.js";

interface MemoryFile {
  path: string;
  relative_path: string;
  kind: string;
  exists: boolean;
  size: number;
  mtime: number;
  lines: number;
}

interface MemoryEditResult {
  path: string;
  exists: boolean;
  content_preview: string;
  kind: string;
  editable: boolean;
}

interface MemoryStatusResult {
  current_mode: string;
  storage_mode: string;
  engine: string;
  enabled: boolean;
  proactive: boolean;
  forbidden_enabled: boolean;
  index?: {
    available: boolean;
    provider?: string | null;
    model?: string | null;
    files_count: number;
    chunks_count: number;
    dirty: boolean;
    fts: Record<string, unknown>;
    vector: Record<string, unknown>;
    cache: Record<string, unknown>;
  };
  project_memory?: {
    files_count: number;
    total_chars: number;
    max_chars: number;
    project_dir?: string;
  };
  coding_memory?: {
    files_count: number;
    total_chars: number;
    dir: string;
  };
  auto_memory?: {
    files_count: number;
    total_chars: number;
    dir: string;
  };
  external_memory?: {
    provider: string;
    enabled: boolean;
  };
}

interface MemoryToggleResult {
  key: string;
  old_value: boolean;
  new_value: boolean;
  mode_affected: string;
  needs_restart: boolean;
}

interface MemoryOpenResult {
  memory_dir: string;
  project_memory_dir: string;
  project_dir?: string;
  coding_memory_dir?: string;
}

// ---------------------------------------------------------------------------
// Frontend-side memory file discovery (mirrors Claude Code's unguarded walk)
// ---------------------------------------------------------------------------

/** File patterns to scan at each directory level (aligned with backend's files.py). */
const PROJECT_MEMORY_FILES: [string, string][] = [
  ["JIUWENSWARM.md", "project"],
  [".jiuwen/JIUWENSWARM.md", "project"],
];
const LOCAL_MEMORY_FILES: [string, string][] = [
  ["JIUWENSWARM.local.md", "local"],
];

/** Probe a single path on disk; returns real state if file exists, placeholder if not. */
function probeFile(absPath: string, relPath: string, kind: string): MemoryFile {
  if (existsSync(absPath)) {
    try {
      const stat = statSync(absPath);
      const content = readFileSync(absPath, "utf-8");
      const lines = content.split("\n").length;
      return {
        path: absPath,
        relative_path: relPath,
        kind,
        exists: true,
        size: stat.size,
        mtime: Math.floor(stat.mtimeMs / 1000),
        lines,
      };
    } catch {
      // stat/read failed — still mark exists, just with zero metrics
      return { path: absPath, relative_path: relPath, kind, exists: true, size: 0, mtime: 0, lines: 0 };
    }
  }
  return { path: absPath, relative_path: relPath, kind, exists: false, size: 0, mtime: 0, lines: 0 };
}

/** Normalize path for de-duplication (case-insensitive on Windows). */
function normalizePathKey(p: string): string {
  try {
    return process.platform === "win32" ? p.toLowerCase() : p;
  } catch {
    return p;
  }
}

/**
 * Walk from CWD upward to root, scanning each directory for memory files.
 * This mirrors Claude Code's unguarded traversal in claudemd.ts — no project
 * root marker is required, every level is scanned unconditionally.
 *
 * Order: root → CWD (outermost ancestor first, CWD last), so closer files
 * have higher priority (loaded later → override earlier).
 */
function discoverMemoryFilesFromFs(cwd: string): MemoryFile[] {
  const results: MemoryFile[] = [];
  const seenPaths = new Set<string>();

  // 1. User-level memory
  const userJiuwenDir = join(homedir(), ".jiuwen");
  const userMemoryPath = join(userJiuwenDir, "JIUWENSWARM.md");
  const userFile = probeFile(userMemoryPath, relative(homedir(), userMemoryPath), "user");
  if (userFile.exists) {
    results.push(userFile);
    seenPaths.add(normalizePathKey(userFile.path));
  }
  // .jiuwen/rules/*.md at user level
  const userRulesDir = join(userJiuwenDir, "rules");
  if (existsSync(userRulesDir)) {
    try {
      for (const entry of readdirSync(userRulesDir)) {
        if (entry.endsWith(".md")) {
          const absPath = join(userRulesDir, entry);
          const f = probeFile(absPath, relative(homedir(), absPath), "user");
          if (f.exists && !seenPaths.has(normalizePathKey(f.path))) {
            results.push(f);
            seenPaths.add(normalizePathKey(f.path));
          }
        }
      }
    } catch { /* ignore unreadable dirs */ }
  }

  // 2. Project & Local — walk from root → CWD (reversed so closer dirs come last = higher priority)
  const dirs: string[] = [];
  let currentDir = cwd;
  const root = parse(currentDir).root;
  while (currentDir !== root) {
    dirs.push(currentDir);
    currentDir = dirname(currentDir);
  }
  // root directory itself is NOT included (same as Claude Code)

  // Reverse: root → CWD so closer-to-CWD files appear later (higher priority)
  dirs.reverse();

  for (const dir of dirs) {
    for (const [rel, kind] of PROJECT_MEMORY_FILES) {
      const absPath = join(dir, rel);
      const f = probeFile(absPath, relative(cwd, absPath), kind);
      if (!seenPaths.has(normalizePathKey(absPath))) {
        seenPaths.add(normalizePathKey(absPath));
        if (f.exists) results.push(f);
      }
    }
    // .jiuwen/rules/*.md at this level
    const rulesDir = join(dir, ".jiuwen", "rules");
    if (existsSync(rulesDir)) {
      try {
        for (const entry of readdirSync(rulesDir)) {
          if (entry.endsWith(".md")) {
            const absPath = join(rulesDir, entry);
            if (!seenPaths.has(normalizePathKey(absPath))) {
              seenPaths.add(normalizePathKey(absPath));
              const f = probeFile(absPath, relative(cwd, absPath), "project");
              if (f.exists) results.push(f);
            }
          }
        }
      } catch { /* ignore */ }
    }
    for (const [rel, kind] of LOCAL_MEMORY_FILES) {
      const absPath = join(dir, rel);
      if (!seenPaths.has(normalizePathKey(absPath))) {
        seenPaths.add(normalizePathKey(absPath));
        const f = probeFile(absPath, relative(cwd, absPath), kind);
        if (f.exists) results.push(f);
      }
    }
  }

  return results;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(mtime: number): string {
  if (!mtime) return "";
  const diff = Date.now() / 1000 - mtime;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function modeToShort(mode: string): string {
  if (mode.startsWith("code")) return "code";
  return mode.replace("agent.", "");
}

async function showMemoryOverview(ctx: import("../types.js").CommandContext): Promise<void> {
  const mode = modeToShort(ctx.mode);
  try {
    const payload = await ctx.request<MemoryStatusResult>("memory.status", {
      detailed: true,
      mode,
    });

    const items: { label: string; value: string; description?: string }[] = [];

    items.push({ label: "Mode", value: payload.current_mode });
    items.push({ label: "Engine", value: `${payload.engine} (${payload.storage_mode})` });
    items.push({ label: "Enabled", value: payload.enabled ? "✓ on" : "✗ off" });
    items.push({ label: "Proactive", value: payload.proactive ? "✓ on" : "✗ off" });
    items.push({ label: "Forbidden Filter", value: payload.forbidden_enabled ? "✓ on" : "✗ off" });

    if (payload.index) {
      items.push({
        label: "Index",
        value: `Files: ${payload.index.files_count}  Chunks: ${payload.index.chunks_count}`,
        description: `FTS: ${payload.index.fts?.available ? "✓" : "✗"}  Vector: ${payload.index.vector?.available ? "✓" : "✗"}`,
      });
    }

    if (payload.project_memory) {
      items.push({
        label: "Project Memory",
        value: `${payload.project_memory.files_count} files`,
        description: `${payload.project_memory.total_chars} / ${payload.project_memory.max_chars} chars`,
      });
      if (payload.project_memory.project_dir) {
        items.push({
          label: "Project Dir",
          value: payload.project_memory.project_dir,
        });
      }
    }

    if (payload.coding_memory) {
      items.push({
        label: "Coding Memory",
        value: `${payload.coding_memory.files_count} files`,
        description: `${payload.coding_memory.total_chars} chars`,
      });
    }

    if (payload.auto_memory) {
      items.push({
        label: "Auto Memory",
        value: `${payload.auto_memory.files_count} files`,
        description: `${payload.auto_memory.total_chars} chars`,
      });
    }

    if (payload.external_memory) {
      items.push({
        label: "External Memory",
        value: `${payload.external_memory.provider} ${payload.external_memory.enabled ? "✓" : "✗"}`,
      });
    }

    ctx.addItem(
      makeItem(ctx.sessionId, "info", "Memory Status", "m", {
        view: "kv",
        title: "Memory",
        items,
      }),
    );

    ctx.addItem(
      addInfo(
        ctx.sessionId,
        "Usage: /memory list|edit|status|toggle|open",
        "i",
      ),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to get memory status: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function listMemory(ctx: import("../types.js").CommandContext): Promise<void> {
  const mode = modeToShort(ctx.mode);
  try {
    const payload = await ctx.request<{ files: MemoryFile[] }>("memory.list", {
      mode,
    });
    const files = payload.files ?? [];

    // Patch backend relative_path fallback: when the backend returns
    // relative_path === path (absolute path) for files outside workspace/project_dir,
    // use frontend discovery to compute a correct relative path.
    const projectDir = ctx.getCurrentProjectDir();
    if (projectDir && files.some((f) => f.relative_path === f.path)) {
      const discovered = discoverMemoryFilesFromFs(projectDir);
      const frontendByPath = new Map<string, MemoryFile>();
      for (const d of discovered) {
        frontendByPath.set(normalizePathKey(d.path), d);
      }
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        if (f.relative_path === f.path) {
          const frontend = frontendByPath.get(normalizePathKey(f.path));
          if (frontend && frontend.relative_path !== frontend.path) {
            files[i] = { ...f, relative_path: frontend.relative_path };
          }
        }
      }
    }

    if (files.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "No memory files found.", "m"));
      return;
    }

    const items = files.map((f) => ({
      label: f.path,
      value: f.kind,
      description: `${f.relative_path !== f.path ? f.relative_path + " · " : ""}${formatSize(f.size)} · ${f.lines} line${f.lines <= 1 ? "" : "s"} · ${formatTime(f.mtime)}`,
    }));

    ctx.addItem(
      makeItem(ctx.sessionId, "info", `${files.length} memory files`, "m", {
        view: "list",
        title: "Memory Files",
        items,
      }),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to list memory files: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function editMemory(
  ctx: import("../types.js").CommandContext,
  args: string,
): Promise<void> {
  const targetPath = args.trim();

  if (!targetPath) {
    await editMemoryInteractive(ctx);
    return;
  }

  await editMemoryByPath(ctx, targetPath);
}

async function editMemoryInteractive(
  ctx: import("../types.js").CommandContext,
): Promise<void> {
  const mode = modeToShort(ctx.mode);

  try {
    const payload = await ctx.request<{ files: MemoryFile[] }>("memory.list", {
      mode,
    });
    const files = payload.files ?? [];

    // Get workspace and project directories
    const workspaceDir = ctx.getWorkspaceDir() || "";
    const projectDir = ctx.getCurrentProjectDir();

    // Frontend-side unguarded traversal (aligned with Claude Code's claudemd.ts):
    // Walk from CWD up to root, scanning every directory for memory files.
    // This supplements the backend's `discover_and_load_memory_files`, which
    // skips the entire project layer when `find_project_root()` returns None
    // (e.g. empty dirs without .git/.jiuwen markers), causing JIUWENSWARM.md
    // to never be discovered even if it exists on disk.
    const discovered = discoverMemoryFilesFromFs(projectDir);

    // Build a frontend lookup by normalized path for relative_path patching.
    // When the backend's _relative_path() falls back to the absolute path itself
    // (i.e. relative_path === path, e.g. parent-directory memory files outside
    // both workspace and project_dir), the frontend's own discovery can provide
    // a correct relative path like "../JIUWENSWARM.md".  Use it to fix the
    // backend's stale relative_path instead of discarding the frontend entry.
    const frontendByPath = new Map<string, MemoryFile>();
    for (const f of discovered) {
      frontendByPath.set(normalizePathKey(f.path), f);
    }

    // Merge: backend results take precedence, but patch stale relative_path
    // with frontend-computed values when the backend fell back to abs_path.
    const seenPaths = new Set(files.map((f) => normalizePathKey(f.path)));
    const mergedFiles: MemoryFile[] = files.map((f) => {
      if (f.relative_path === f.path) {
        const frontend = frontendByPath.get(normalizePathKey(f.path));
        if (frontend && frontend.relative_path !== frontend.path) {
          return { ...f, relative_path: frontend.relative_path };
        }
      }
      return f;
    });
    // Frontend-discovered files fill gaps (files the backend didn't find at all).
    for (const f of discovered) {
      if (!seenPaths.has(normalizePathKey(f.path))) {
        mergedFiles.push(f);
        seenPaths.add(normalizePathKey(f.path));
      }
    }

    // Always provide JIUWENSWARM.md / JIUWENSWARM.local.md entries so users
    // can create them if they don't exist yet.  Use actual file state when
    // available rather than fabricating a stale "0 lines (new)" placeholder.
    const hasProjectMemory = mergedFiles.some(
      (f) => f.path.endsWith("JIUWENSWARM.md") && !f.path.endsWith("JIUWENSWARM.local.md"),
    );
    const hasLocalMemory = mergedFiles.some((f) => f.path.endsWith("JIUWENSWARM.local.md"));

    const projectMemoryPath = join(projectDir, "JIUWENSWARM.md");
    const localMemoryPath = join(projectDir, "JIUWENSWARM.local.md");

    const allFiles: MemoryFile[] = [
      ...mergedFiles,
      ...(hasProjectMemory ? [] : [probeFile(projectMemoryPath, "JIUWENSWARM.md", "project")]),
      ...(hasLocalMemory ? [] : [probeFile(localMemoryPath, "JIUWENSWARM.local.md", "local")]),
    ];

    if (allFiles.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "No memory files to edit.", "m"));
      return;
    }

    const options = allFiles.map((f) => ({
      label: f.relative_path,
      description: `${f.kind} · ${f.lines} line${f.lines <= 1 ? "" : "s"}${f.exists ? "" : " (new)"}`,
      details: f.relative_path !== f.path ? [f.path] : undefined,
    }));

    let selectedLabel: string | undefined;
    try {
      const [answer] = await ctx.askQuestions(
        [
          {
            header: "Edit Memory",
            question: "Select a memory file to edit:",
            options,
          },
        ],
        "local_command_memory_edit",
      );
      selectedLabel = answer?.selected_options?.[0];
    } catch {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled memory editing.", "i"));
      return;
    }

    if (!selectedLabel) {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled memory editing.", "i"));
      return;
    }

    const selectedFile = allFiles.find(
      (f) => f.relative_path === selectedLabel || f.path === selectedLabel,
    );

    if (!selectedFile) {
      ctx.addItem(addError(ctx.sessionId, `Could not find selected file: ${selectedLabel}`));
      return;
    }

    await editMemoryByPath(ctx, selectedFile.path);
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to list files for edit: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function editMemoryByPath(
  ctx: import("../types.js").CommandContext,
  path: string,
): Promise<void> {
  try {
    const trustedDirs = ctx.getTrustedDirs();
    const payload = await ctx.request<MemoryEditResult>("memory.edit", {
      path,
      trusted_dirs: trustedDirs.length > 0 ? trustedDirs : undefined,
      cwd: ctx.getWorkspaceDir(),
    });

    if (!payload.editable) {
      ctx.addItem(addError(ctx.sessionId, `Cannot edit: ${path} — path not in allowed memory directories.`));
      return;
    }

    if (ctx.openInEditor) {
      ctx.openInEditor(payload.path);

      const { source, value } = getEditorInfo();
      const editorHint = source !== "default"
        ? `(${source}="${value}")`
        : "(default: vi)";

      ctx.addItem(
        addInfo(
          ctx.sessionId,
          `Finished editing ${payload.path} ${editorHint}`,
          "m",
        ),
      );
    } else {
      ctx.addItem(
        addInfo(
          ctx.sessionId,
          `Edit with:  $EDITOR ${payload.path}`,
          "i",
        ),
      );
    }
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to edit memory file: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function showMemoryStatus(
  ctx: import("../types.js").CommandContext,
): Promise<void> {
  const mode = modeToShort(ctx.mode);
  try {
    const payload = await ctx.request<MemoryStatusResult>("memory.status", {
      detailed: true,
      mode,
    });

    const items: { label: string; value: string; description?: string }[] = [];

    items.push({ label: "Current Mode", value: payload.current_mode });
    items.push({ label: "Storage Mode", value: payload.storage_mode });
    items.push({ label: "Engine", value: payload.engine });
    items.push({ label: "Enabled", value: payload.enabled ? "✓ on" : "✗ off" });
    items.push({ label: "Proactive", value: payload.proactive ? "✓ on" : "✗ off" });
    items.push({ label: "Forbidden Filter", value: payload.forbidden_enabled ? "✓ on" : "✗ off" });

    if (payload.index) {
      items.push({ label: "Index Available", value: payload.index.available ? "✓" : "✗" });
      items.push({ label: "Embedding Provider", value: payload.index.provider ?? "N/A" });
      items.push({ label: "Embedding Model", value: payload.index.model ?? "N/A" });
      items.push({ label: "Files Indexed", value: String(payload.index.files_count) });
      items.push({ label: "Chunks", value: String(payload.index.chunks_count) });
      items.push({ label: "Dirty", value: payload.index.dirty ? "yes" : "no" });
      const ftsInfo = payload.index.fts as { enabled?: boolean; available?: boolean; error?: string } | undefined;
      const vecInfo = payload.index.vector as { enabled?: boolean; available?: boolean; dims?: number; error?: string } | undefined;
      const cacheInfo = payload.index.cache as { enabled?: boolean; entries?: number } | undefined;
      items.push({
        label: "FTS5",
        value: ftsInfo?.available ? "✓ enabled" : "✗ disabled",
        description: ftsInfo?.error,
      });
      items.push({
        label: "Vector",
        value: vecInfo?.available ? `✓ enabled (dims: ${vecInfo.dims ?? "?"})` : "✗ disabled",
        description: vecInfo?.error,
      });
      items.push({
        label: "Cache",
        value: cacheInfo?.enabled ? `✓ ${cacheInfo.entries ?? 0} entries` : "✗ disabled",
      });
    }

    if (payload.project_memory) {
      items.push({
        label: "Project Memory Files",
        value: String(payload.project_memory.files_count),
      });
      items.push({
        label: "Project Memory Chars",
        value: `${payload.project_memory.total_chars} / ${payload.project_memory.max_chars}`,
      });
      if (payload.project_memory.project_dir) {
        items.push({
          label: "Project Dir",
          value: payload.project_memory.project_dir,
        });
      }
    }

    if (payload.coding_memory) {
      items.push({
        label: "Coding Memory Files",
        value: String(payload.coding_memory.files_count),
      });
      items.push({
        label: "Coding Memory Chars",
        value: String(payload.coding_memory.total_chars),
      });
      if (payload.coding_memory.dir) {
        items.push({
          label: "Coding Memory Dir",
          value: payload.coding_memory.dir,
        });
      }
    }

    if (payload.auto_memory) {
      items.push({
        label: "Auto Memory Files",
        value: String(payload.auto_memory.files_count),
      });
      items.push({
        label: "Auto Memory Chars",
        value: String(payload.auto_memory.total_chars),
      });
      if (payload.auto_memory.dir) {
        items.push({
          label: "Auto Memory Dir",
          value: payload.auto_memory.dir,
        });
      }
    }

    if (payload.external_memory) {
      items.push({
        label: "External Memory",
        value: `${payload.external_memory.provider} ${payload.external_memory.enabled ? "✓" : "✗"}`,
      });
    }

    ctx.addItem(
      makeItem(ctx.sessionId, "info", "Memory Status (detailed)", "m", {
        view: "kv",
        title: "Memory Status",
        items,
      }),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to get memory status: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

const TOGGLE_KEYS = [
  { key: "memory_enabled", label: "Enabled", config_path: "modes.agent.<mode>.memory.enabled" },
  { key: "memory_proactive", label: "Proactive", config_path: "modes.agent.<mode>.memory.is_proactive" },
  { key: "memory_forbidden_enabled", label: "Forbidden Filter", config_path: "memory.forbidden_memory_definition.enabled" },
];

async function toggleMemory(
  ctx: import("../types.js").CommandContext,
  args: string,
): Promise<void> {
  const key = args.trim();

  if (!key) {
    await showToggleList(ctx);
    return;
  }

  await toggleByKey(ctx, key);
}

async function showToggleList(
  ctx: import("../types.js").CommandContext,
): Promise<void> {
  const mode = modeToShort(ctx.mode);
  try {
    const payload = await ctx.request<MemoryStatusResult>("memory.status", {
      mode,
    });

    const items = TOGGLE_KEYS.map((t) => {
      let current: boolean;
      if (t.key === "memory_enabled") current = payload.enabled;
      else if (t.key === "memory_proactive") current = payload.proactive;
      else current = payload.forbidden_enabled;

      return {
        label: t.key,
        value: `${t.label} ${current ? "✓ on" : "✗ off"}`,
        description: t.config_path,
      };
    });

    ctx.addItem(
      makeItem(ctx.sessionId, "info", "Memory Toggles", "m", {
        view: "kv",
        title: "Memory Toggles",
        items,
      }),
    );

    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `Usage: /memory toggle <key>  (affects mode: ${mode})`,
        "i",
      ),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to get toggle status: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function toggleByKey(
  ctx: import("../types.js").CommandContext,
  key: string,
): Promise<void> {
  const validKeys = TOGGLE_KEYS.map((t) => t.key);
  if (!validKeys.includes(key)) {
    ctx.addItem(
      addError(ctx.sessionId, `Unknown toggle key: ${key}. Valid keys: ${validKeys.join(", ")}`),
    );
    return;
  }

  const mode = modeToShort(ctx.mode);
  try {
    const payload = await ctx.request<MemoryToggleResult>("memory.toggle", {
      key,
      mode,
    });

    const label = TOGGLE_KEYS.find((t) => t.key === key)?.label ?? key;
    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `${label}: ${payload.old_value ? "on" : "off"} → ${payload.new_value ? "on" : "off"}${payload.needs_restart ? " (restart session to apply)" : ""}`,
        "m",
      ),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Toggle failed: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

async function openMemoryDir(
  ctx: import("../types.js").CommandContext,
): Promise<void> {
  try {
    const payload = await ctx.request<MemoryOpenResult>("memory.open", {});

    const items: { label: string; value: string }[] = [];
    items.push({ label: "Memory Dir", value: payload.memory_dir });
    items.push({ label: "Project Dir", value: payload.project_memory_dir });
    if (payload.project_dir) {
      items.push({ label: "User Project Dir", value: payload.project_dir });
    }
    if (payload.coding_memory_dir) {
      items.push({ label: "Coding Memory Dir", value: payload.coding_memory_dir });
    }

    ctx.addItem(
      makeItem(ctx.sessionId, "info", "Memory Directories", "m", {
        view: "kv",
        title: "Memory Open",
        items,
      }),
    );

    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `Open with:  open ${payload.memory_dir}  (macOS)  |  xdg-open ${payload.memory_dir}  (Linux)`,
        "i",
      ),
    );
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to get memory directories: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

export function createMemoryCommand(): SlashCommand {
  return {
    name: "memory",
    altNames: ["mem"],
    description: "Edit memory files (list, edit, status, toggle, open)",
    usage: "/memory [list|edit|status|toggle|open] [args]",
    example: "/memory edit",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx) => {
      await editMemoryInteractive(ctx);
    },
    completion: async () => {
      return ["list", "edit", "status", "toggle", "open"];
    },
    subCommands: [
      {
        name: "list",
        description: "List all memory files",
        usage: "/memory list",
        example: "/memory list",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          await listMemory(ctx);
        },
      },
      {
        name: "edit",
        description: "Edit a memory file (interactive selection if no path given)",
        usage: "/memory edit [path]",
        example: "/memory edit memory/MEMORY.md",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          await editMemory(ctx, args);
        },
        completion: async () => ["memory/MEMORY.md", "coding_memory/MEMORY.md"],
      },
      {
        name: "status",
        description: "Show detailed memory system status",
        usage: "/memory status",
        example: "/memory status",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          await showMemoryStatus(ctx);
        },
      },
      {
        name: "toggle",
        description: "Toggle memory settings (memory_enabled, memory_proactive, memory_forbidden_enabled)",
        usage: "/memory toggle [key]",
        example: "/memory toggle memory_enabled",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          await toggleMemory(ctx, args);
        },
        completion: async () => ["memory_enabled", "memory_proactive", "memory_forbidden_enabled"],
      },
      {
        name: "open",
        description: "Show memory directory paths",
        usage: "/memory open",
        example: "/memory open",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          await openMemoryDir(ctx);
        },
      },
    ],
  };
}
