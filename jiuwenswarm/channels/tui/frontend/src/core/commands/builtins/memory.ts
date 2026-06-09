import { join } from "path";
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

    if (files.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "No memory files found.", "m"));
      return;
    }

    const items = files.map((f) => ({
      label: f.path,
      value: f.kind,
      description: `${f.relative_path !== f.path ? f.relative_path + " · " : ""}${formatSize(f.size)} · ${f.lines} lines · ${formatTime(f.mtime)}`,
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

    // Check if project and local memory exist
    const hasProjectMemory = files.some(
      (f) => f.path.endsWith("JIUWENSWARM.md") && !f.path.endsWith("JIUWENSWARM.local.md"),
    );
    const hasLocalMemory = files.some((f) => f.path.endsWith("JIUWENSWARM.local.md"));

    // Add default options if not exist
    const projectMemoryPath = join(projectDir, "JIUWENSWARM.md");
    const localMemoryPath = join(projectDir, "JIUWENSWARM.local.md");

    const allFiles: MemoryFile[] = [
      ...files,
      ...(hasProjectMemory
        ? []
        : [
            {
              path: projectMemoryPath,
              relative_path: "JIUWENSWARM.md",
              kind: "project",
              exists: false,
              size: 0,
              mtime: 0,
              lines: 0,
            },
          ]),
      ...(hasLocalMemory
        ? []
        : [
            {
              path: localMemoryPath,
              relative_path: "JIUWENSWARM.local.md",
              kind: "local",
              exists: false,
              size: 0,
              mtime: 0,
              lines: 0,
            },
          ]),
    ];

    if (allFiles.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "No memory files to edit.", "m"));
      return;
    }

    const options = allFiles.map((f) => ({
      label: f.relative_path,
      description: `${f.kind} · ${f.lines} lines${f.exists ? "" : " (new)"}`,
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
