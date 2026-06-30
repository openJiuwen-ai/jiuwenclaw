import { existsSync, mkdirSync, readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
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
  auto_memory_enabled: boolean;
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
  auto_memory_dir?: string;
}

// ---------------------------------------------------------------------------
// Path display utilities (aligned with Claude Code's getDisplayPath)
// ---------------------------------------------------------------------------

/**
 * Convert an absolute path to a display-friendly relative path.
 * Mirrors Claude Code's getDisplayPath logic — uses git root as base for
 * computing relative paths (not the deep projectDir subdirectory), which
 * produces short paths like ".jiuwen/rules/foo.md" instead of long
 * "../../../../../.jiuwen/rules/foo.md".
 *
 * Priority order (aligned with Claude Code):
 * 1. If git root found → compute relative path from git root
 *    (e.g. ".jiuwen/rules/foo.md", "JIUWENSWARM.md")
 * 2. If no git root → compute relative path from projectDir
 *    (e.g. "../JIUWENSWARM.md", ".jiuwen/rules/foo.md")
 * 3. If file is in home directory → use tilde notation
 *    (e.g. "~/.jiuwen/JIUWENSWARM.md")
 * 4. Otherwise → fallback to absolute path with forward slashes
 *
 * On Windows, THREE critical issues must be handled:
 * - path.relative() returns absolute paths when source/target are on different
 *   drives (e.g. E: vs C:) — must NOT treat these as valid relative paths.
 * - Case mismatch: getCurrentProjectDir() lowercases paths on Windows, but
 *   backend returns original-case paths. We normalize both paths to the same
 *   case before computing relative().
 * - **Unicode path bug**: Node.js path.relative() on Windows silently drops
 *   backslash separators from paths containing multi-byte characters (e.g.
 *   C:\Users\李雯琳 → "C:Users李雯琳"), producing garbage output. We convert
 *   all backslashes to forward slashes before calling relative(), which avoids
 *   this bug because forward slashes are also valid separators on Windows.
 *
 * All output is normalized to forward slashes (aligned with Claude Code).
 */
/**
 * Convert an absolute path to the shortest display-friendly path.
 * Mirrors Claude Code's getDisplayPath logic — generates ALL candidate paths
 * and picks the shortest one:
 *
 *   Candidates:
 *   1. Relative from git root  (e.g. ".jiuwen/rules/foo.md", "../JIUWENSWARM.md")
 *   2. Relative from projectDir (e.g. "../../JIUWENSWARM.md", ".jiuwen/rules/foo.md")
 *   3. Tilde notation          (e.g. "~/.jiuwen/JIUWENSWARM.md")
 *
 *   Winner = shortest candidate.
 *   Examples:
 *   - ../JIUWENSWARM.md (17 chars) beats ~/AppData/Local/.../JIUWENSWARM.md (49 chars)
 *   - .jiuwen/rules/git.md (21 chars) beats ../../.jiuwen/rules/git.md (26 chars)
 *   - ~/.jiuwen/JIUWENSWARM.md (22 chars) beats ../JIUWENSWARM.md when in deep subdir
 *
 * All output uses forward slashes. Claude Code allows ../ prefix paths.
 *
 * On Windows, THREE critical issues must be handled:
 * - path.relative() returns absolute paths for cross-drive paths — must discard.
 * - Case mismatch: getCurrentProjectDir() lowercases on Windows, backend doesn't.
 * - Unicode bug: Node.js path.relative() silently drops \ from multi-byte paths.
 */
function getDisplayPath(filePath: string, projectDir: string): string {
  const fileSlashes = filePath.replace(/\\/g, "/");
  const fileNorm = process.platform === "win32" ? fileSlashes.toLowerCase() : fileSlashes;
  const homeDir = homedir();
  const homeDirSlashes = homeDir.replace(/\\/g, "/");
  const homeDirNorm = process.platform === "win32" ? homeDirSlashes.toLowerCase() : homeDirSlashes;

  // Collect all valid candidate paths, then pick the shortest
  const candidates: string[] = [];

  // Candidate 1: relative from git root (if inside git repo)
  // 守卫:仅当文件位于 projectDir 内部时才采用 git-root 相对路径候选。
  // 否则(文件在 projectDir 的父级/祖先目录)git-root 相对路径会丢掉 ../
  // 前缀,把父目录文件伪装成当前目录文件,误导用户。此时应只保留 candidate 2
  // 的 projectDir 相对路径(必带 ../ 前缀)。
  const fileInsideProject = isAncestorOrSelfDir(projectDir, filePath);
  const gitRoot = findGitRoot(projectDir);
  if (gitRoot && fileInsideProject) {
    const gitRootSlashes = gitRoot.replace(/\\/g, "/");
    const gitRootNorm = process.platform === "win32" ? gitRootSlashes.toLowerCase() : gitRootSlashes;
    const projectDirSlashes = projectDir.replace(/\\/g, "/");
    const projectDirNorm = process.platform === "win32" ? projectDirSlashes.toLowerCase() : projectDirSlashes;
    // Only use git root as base if projectDir is inside the git repo
    if (projectDirNorm.startsWith(gitRootNorm + "/") || projectDirNorm === gitRootNorm) {
      const relFromGit = relative(gitRootNorm, fileNorm);
      // Discard cross-drive absolute paths from relative()
      if (relFromGit && !relFromGit.startsWith("/") && !/^[A-Za-z]:/.test(relFromGit)) {
        const display = relative(gitRootSlashes, fileSlashes).replace(/\\/g, "/");
        candidates.push(display);
      }
    }
  }

  // Candidate 2: relative from projectDir
  const projectDirSlashes = projectDir.replace(/\\/g, "/");
  const projectDirNorm = process.platform === "win32" ? projectDirSlashes.toLowerCase() : projectDirSlashes;
  const relFromProj = relative(projectDirNorm, fileNorm);
  if (relFromProj && !relFromProj.startsWith("/") && !/^[A-Za-z]:/.test(relFromProj)) {
    const display = relative(projectDirSlashes, fileSlashes).replace(/\\/g, "/");
    candidates.push(display);
  }

  // Candidate 3: tilde notation (if file is in home directory)
  if (fileNorm.startsWith(homeDirNorm + "/") || fileNorm === homeDirNorm) {
    const tildePath = "~" + fileSlashes.slice(homeDirSlashes.length);
    candidates.push(tildePath);
  }

  // Pick the shortest candidate — this is exactly what Claude Code does
  if (candidates.length > 0) {
    return candidates.reduce((shortest, c) => c.length < shortest.length ? c : shortest, candidates[0]);
  }

  // Fallback: absolute path with forward slashes
  return fileSlashes;
}

/**
 * Find the git repository root directory.
 * Walks upward from cwd looking for a .git directory or file (worktree/submodule).
 * Returns the git root path if found, or null if not in a git repo.
 * Mirrors Claude Code's findGitRoot logic.
 */
function findGitRoot(cwd: string): string | null {
  let current = cwd;
  const root = parse(current).root;
  while (current !== root) {
    try {
      const gitPath = join(current, ".git");
      const stat = statSync(gitPath);
      // .git can be a directory (regular repo) or file (worktree/submodule)
      if (stat.isDirectory() || stat.isFile()) {
        return current;
      }
    } catch {
      // .git doesn't exist at this level, continue walking up
    }
    current = dirname(current);
  }
  return null;
}

/**
 * 判断 ancestor 是否是 target 的祖先目录或本身(Windows 大小写不敏感)。
 *
 * 用于识别"文件位于 projectDir 的上级目录"这一场景——后端
 * `_validate_edit_path` 只白名单 project_dir 单层,会拒绝编辑 projectDir
 * 祖先目录里的 JIUWENSWARM.md / JIUWENSWARM.local.md;同时 getDisplayPath
 * 在父目录是 git root 时会把这类文件显示成无 ../ 的当前目录文件(伪装)。
 * 两处都需要用此函数识别并特殊处理。
 */
function isAncestorOrSelfDir(ancestor: string, target: string): boolean {
  const a = ancestor.replace(/\\/g, "/").replace(/\/$/, "");
  const t = target.replace(/\\/g, "/").replace(/\/$/, "");
  const aNorm = process.platform === "win32" ? a.toLowerCase() : a;
  const tNorm = process.platform === "win32" ? t.toLowerCase() : t;
  return aNorm === tNorm || tNorm.startsWith(aNorm + "/");
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
    items.push({ label: "Auto Memory", value: payload.auto_memory_enabled ? "✓ on" : "✗ off" });

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
  // Aligned with Claude Code: /memory list uses an interactive selector
  // with two-column layout (label + description), same as the default /memory action.
  await showMemorySelector(ctx);
}

/**
 * Show memory file selector — the core UI shared by /memory and /memory list.
 * EXACTLY aligned with Claude Code's /memory display format:
 *
 *   1. Project memory           Checked in at ./CLAUDE.md
 *   2. .claude/rules/git.md
 *   3. .claude/rules/testing.md
 *   4. User memory              Saved in ~/.claude/CLAUDE.md
 *
 * Key principles (mirroring Claude Code exactly):
 * - "Project memory" and "User memory" are short labels with descriptions
 *   showing "Checked in at"/"Saved in" + the relative path
 * - Rules files (.jiuwen/rules/*.md) are listed with their relative path
 *   as the ONLY label — NO description column (same as Claude Code)
 * - Paths are always relative (from git root when available), using /
 *   separators, never absolute or long ../../ paths
 * - Sort order: Project memory → project rules → Local memory → User memory → user rules
 *   (mirrors Claude Code's project-first ordering)
 *
 * Uses ctx.askQuestions → SelectList for proper two-column rendering that
 * never truncates the label.
 */
// Sentinel values for non-file actions in the selector
const ACTION_TOGGLE_MEMORY_ENABLED = "__toggle_memory_enabled__";
const ACTION_TOGGLE_AUTO_MEMORY = "__toggle_auto_memory__";
const ACTION_OPEN_AUTO_MEMORY_FOLDER = "__open_auto_memory_folder__";

async function showMemorySelector(ctx: import("../types.js").CommandContext): Promise<void> {
  const mode = modeToShort(ctx.mode);
  const projectDir = ctx.getCurrentProjectDir();

  try {
    // Fetch both memory file list and status (for auto-memory toggle state)
    const [listPayload, statusPayload] = await Promise.all([
      ctx.request<{ files: MemoryFile[] }>("memory.list", { mode }),
      ctx.request<MemoryStatusResult>("memory.status", { detailed: true, mode }),
    ]);
    const files = listPayload.files ?? [];
    const memoryEnabled = statusPayload.enabled ?? false;
    const autoMemoryEnabled = statusPayload.auto_memory_enabled ?? false;

    // Frontend-side unguarded traversal to fill gaps
    const discovered = discoverMemoryFilesFromFs(projectDir);
    const frontendByPath = new Map<string, MemoryFile>();
    for (const f of discovered) {
      frontendByPath.set(normalizePathKey(f.path), f);
    }
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
    for (const f of discovered) {
      if (!seenPaths.has(normalizePathKey(f.path))) {
        mergedFiles.push(f);
        seenPaths.add(normalizePathKey(f.path));
      }
    }

    const homeDir = homedir();
    const gitRoot = findGitRoot(projectDir);
    const homeDirLower = process.platform === "win32" ? homeDir.toLowerCase() : homeDir;
    const userMemoryPath = join(homeDir, ".jiuwen", "JIUWENSWARM.md");
    const filePathLowerFn = (p: string) => process.platform === "win32" ? p.toLowerCase() : p;

    // Categorize files into groups, aligned with Claude Code's display order.
    const projectMemoryFile = mergedFiles.find(
      (f) => f.kind === "project"
        && f.path.endsWith("JIUWENSWARM.md")
        && !f.path.endsWith("JIUWENSWARM.local.md")
        && !f.path.endsWith(".jiuwen/JIUWENSWARM.md"),
    );
    const localMemoryFile = mergedFiles.find(
      (f) => f.kind === "local" && f.path.endsWith("JIUWENSWARM.local.md"),
    );
    const userMemoryFile = mergedFiles.find(
      (f) => filePathLowerFn(f.path) === filePathLowerFn(userMemoryPath),
    );

    // Project-level rules files: any project-kind file that is NOT the main JIUWENSWARM.md
    const projectRules = mergedFiles.filter(
      (f) => f.kind === "project"
        && f !== projectMemoryFile
        && f !== localMemoryFile,
    );

    // User-level rules files: any user-kind file that is NOT the main user JIUWENSWARM.md
    const userRules = mergedFiles.filter(
      (f) => f.kind === "user"
        && filePathLowerFn(f.path) !== filePathLowerFn(userMemoryPath),
    );

    // Build ordered file list matching Claude Code's exact order:
    // Project memory → project rules → Local memory → User memory → user rules
    const orderedFiles: MemoryFile[] = [];
    if (projectMemoryFile) orderedFiles.push(projectMemoryFile);
    for (const f of projectRules) orderedFiles.push(f);
    if (localMemoryFile) orderedFiles.push(localMemoryFile);
    if (userMemoryFile) orderedFiles.push(userMemoryFile);
    for (const f of userRules) orderedFiles.push(f);

    // Always provide JIUWENSWARM.md / JIUWENSWARM.local.md entries so users can create them
    const projMemBase = gitRoot || projectDir;
    if (!projectMemoryFile) {
      orderedFiles.unshift(probeFile(join(projMemBase, "JIUWENSWARM.md"), "JIUWENSWARM.md", "project"));
    }
    if (!localMemoryFile) {
      const insertIdx = orderedFiles.findIndex(
        (f) => filePathLowerFn(f.path) === filePathLowerFn(userMemoryPath),
      );
      const localProbe = probeFile(join(projMemBase, "JIUWENSWARM.local.md"), "JIUWENSWARM.local.md", "local");
      if (insertIdx >= 0) {
        orderedFiles.splice(insertIdx, 0, localProbe);
      } else {
        orderedFiles.push(localProbe);
      }
    }
    if (!userMemoryFile) {
      orderedFiles.push(probeFile(userMemoryPath, "JIUWENSWARM.md", "user"));
    }

    // Build options — EXACTLY aligned with Claude Code's /memory display
    // Aligned with Claude Code format:
    //   1. Memory: on/off                   (toggle — control CodingMemoryRail/ProjectMemoryRail)
    //   2. Auto-memory: on/off              (toggle — control auto memory extraction)
    //   3. Project memory                  Checked in at ./CLAUDE.md
    //   4. .claude/rules/git.md
    //   5. User memory                     Saved in ~/.claude/CLAUDE.md
    //   6. Open auto-memory folder         (only when auto-memory is on)

    const options: { label: string; description: string | undefined; value: string }[] = [];

    // 1. Memory enabled toggle — control CodingMemoryRail and ProjectMemoryRail loading
    options.push({
      label: `Memory: ${memoryEnabled ? "on" : "off"}`,
      description: memoryEnabled ? "Press Enter to toggle" : "Memory disabled - files won't be auto-loaded",
      value: ACTION_TOGGLE_MEMORY_ENABLED,
    });

    // 2. Auto-memory toggle — control auto memory extraction after conversation ends
    options.push({
      label: `Auto-memory: ${autoMemoryEnabled ? "on" : "off"}`,
      description: "Press Enter to toggle",
      value: ACTION_TOGGLE_AUTO_MEMORY,
    });

    // 3. Memory file entries
    for (const f of orderedFiles) {
      const filePathLower = filePathLowerFn(f.path);
      const displayPath = getDisplayPath(f.path, projectDir);

      let label: string;
      let description: string | undefined;

      if (filePathLower === filePathLowerFn(userMemoryPath)) {
        label = "User memory";
        description = `Saved in ${displayPath}`;
      } else if (f.kind === "project" && f.path.endsWith("JIUWENSWARM.md") && !f.path.endsWith("JIUWENSWARM.local.md") && !f.path.endsWith(".jiuwen/JIUWENSWARM.md")) {
        label = "Project memory";
        description = `${gitRoot ? "Checked in at" : "Saved in"} ${displayPath}`;
      } else if (f.kind === "local" && f.path.endsWith("JIUWENSWARM.local.md")) {
        label = "Local memory";
        description = `Saved in ${displayPath}`;
      } else {
        // Rules files — just show the relative path as label, NO description
        label = displayPath;
        description = undefined;
      }

      options.push({ label, description, value: f.path });
    }

    // 3. Open auto-memory folder — only on Windows + when auto-memory is enabled
    // Linux/macOS: not shown because xdg-open/file-manager support varies and cannot be tested
    if (autoMemoryEnabled && process.platform === "win32") {
      options.push({
        label: "Open auto-memory folder",
        description: undefined,
        value: ACTION_OPEN_AUTO_MEMORY_FOLDER,
      });
    }

    let selectedValue: string | undefined;
    try {
      const [answer] = await ctx.askQuestions(
        [
          {
            header: "Memory",
            question: "Select an action:",
            options: options.map((opt) => ({
              label: opt.label,
              description: opt.description,
            })),
          },
        ],
        "local_command_memory_edit",
      );
      const selectedLabel = answer?.selected_options?.[0];
      selectedValue = selectedLabel
        ? options.find((opt) => opt.label === selectedLabel)?.value
        : undefined;
    } catch {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled.", "i"));
      return;
    }

    if (!selectedValue) {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled.", "i"));
      return;
    }

    // Handle selected action
    if (selectedValue === ACTION_TOGGLE_MEMORY_ENABLED) {
      await toggleByKey(ctx, "memory_enabled");
      return;
    }

    if (selectedValue === ACTION_TOGGLE_AUTO_MEMORY) {
      await toggleByKey(ctx, "auto_memory_enabled");
      return;
    }

    if (selectedValue === ACTION_OPEN_AUTO_MEMORY_FOLDER) {
      const openPayload = await ctx.request<MemoryOpenResult>("memory.open", {
        project_dir: projectDir,
      });
      const targetDir = openPayload.coding_memory_dir || openPayload.auto_memory_dir;
      if (targetDir && ctx.openFolder) {
        ctx.openFolder(targetDir);
        ctx.addItem(addInfo(ctx.sessionId, "Opened memory folder", "m"));
      }
      return;
    }

    // Edit the selected memory file
    await editMemoryByPath(ctx, selectedValue);
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
    await showMemorySelector(ctx);
    return;
  }

  await editMemoryByPath(ctx, targetPath);
}

async function editMemoryByPath(
  ctx: import("../types.js").CommandContext,
  path: string,
): Promise<void> {
  try {
    const trustedDirs = ctx.getTrustedDirs();
    const projectDir = ctx.getCurrentProjectDir();

    // 后端 _validate_edit_path 只白名单 project_dir 单层,会拒绝编辑 projectDir
    // 祖先目录里的 JIUWENSWARM.md / JIUWENSWARM.local.md。这类文件是合法的
    // project memory(前端 discoverMemoryFilesFromFs 已识别并列入选择器),
    // 且由用户主动从列表选中,故绕过 memory.edit RPC,直接用本地 openInEditor
    // 打开(与 keybindings.ts 打开配置文件同级风险,不经后端校验)。
    const baseName = path.replace(/\\/g, "/").split("/").pop() ?? "";
    const fileParent = path.replace(/[/\\][^/\\]*$/, "");
    const isAncestorMemFile =
      (baseName === "JIUWENSWARM.md" || baseName === "JIUWENSWARM.local.md")
      && !!projectDir
      && !isAncestorOrSelfDir(projectDir, path) // 文件不在 projectDir 内部
      && isAncestorOrSelfDir(fileParent, projectDir); // 其父目录是 projectDir 的祖先/本身

    if (isAncestorMemFile) {
      const displayPath = getDisplayPath(path, projectDir);
      // 文件不存在则先创建(与后端 handle_memory_edit 的 touch 行为对齐)
      if (!existsSync(path)) {
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, "");
      }
      if (ctx.openInEditor) {
        ctx.openInEditor(path);
        const { source, value } = getEditorInfo();
        const editorHint = source !== "default"
          ? `(${source}="${value}")`
          : "(default: vi)";
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            `Opened memory file at ${displayPath} ${editorHint}`,
            "m",
          ),
        );
      } else {
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            `Edit with:  $EDITOR ${displayPath}`,
            "i",
          ),
        );
      }
      return;
    }

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

      const projectDir = ctx.getCurrentProjectDir();
      const displayPath = getDisplayPath(payload.path, projectDir);
      const { source, value } = getEditorInfo();
      const editorHint = source !== "default"
        ? `(${source}="${value}")`
        : "(default: vi)";

      ctx.addItem(
        addInfo(
          ctx.sessionId,
          `Opened memory file at ${displayPath} ${editorHint}`,
          "m",
        ),
      );
    } else {
      const projectDir = ctx.getCurrentProjectDir();
      const displayPath = getDisplayPath(payload.path, projectDir);
      ctx.addItem(
        addInfo(
          ctx.sessionId,
          `Edit with:  $EDITOR ${displayPath}`,
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
  {
    key: "memory_enabled",
    label: "Enabled",
    getConfigPath: (mode: string) =>
      mode === "code" ? "modes.code.memory.enabled" : `modes.agent.${mode}.memory.enabled`,
  },
  { key: "memory_proactive", label: "Proactive", getConfigPath: (mode: string) => `modes.agent.${mode}.memory.is_proactive` },
  { key: "memory_forbidden_enabled", label: "Forbidden Filter", getConfigPath: () => "memory.forbidden_memory_definition.enabled" },
  { key: "auto_memory_enabled", label: "Auto Memory", getConfigPath: () => "auto_memory_enabled" },
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
      else if (t.key === "memory_forbidden_enabled") current = payload.forbidden_enabled;
      else if (t.key === "auto_memory_enabled") current = payload.auto_memory_enabled;
      else current = false;

      return {
        label: t.key,
        value: `${t.label} ${current ? "✓ on" : "✗ off"}`,
        description: t.getConfigPath(mode),
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
    if (payload.auto_memory_dir) {
      items.push({ label: "Auto Memory Dir", value: payload.auto_memory_dir });
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

const OPEN_FOLDER_PREFIX = "__open_folder__";

async function showAutoMemoryInteractive(
  ctx: import("../types.js").CommandContext,
): Promise<void> {
  const mode = modeToShort(ctx.mode);
  const workspaceDir = ctx.getWorkspaceDir() || "";
  const projectDir = ctx.getCurrentProjectDir() || workspaceDir;

  try {
    // Get memory status and files
    const statusPayload = await ctx.request<MemoryStatusResult>("memory.status", {
      detailed: true,
      mode,
    });

    const listPayload = await ctx.request<{ files: MemoryFile[] }>("memory.list", {
      mode,
      include_project: true,
      project_dir: projectDir,
    });

    const autoMemoryEnabled = statusPayload.auto_memory_enabled ?? false;
    const files = listPayload.files ?? [];

    // Build memory file options (Project and User memory)
    const projectMemoryPath = join(projectDir, "JIUWENSWARM.md");
    const userMemoryPath = join(workspaceDir, "JIUWENSWARM.local.md");

    const hasProjectMemory = files.some(
      (f) => f.path === projectMemoryPath || f.relative_path === "JIUWENSWARM.md",
    );
    const hasUserMemory = files.some(
      (f) => f.path === userMemoryPath || f.relative_path === "JIUWENSWARM.local.md",
    );

    // Add memory file options
    const memoryOptions: { label: string; description: string; value: string }[] = [];

    // Project memory
    memoryOptions.push({
      label: "Project memory",
      value: projectMemoryPath,
      description: hasProjectMemory ? `Saved in ./JIUWENSWARM.md` : "Saved in ./JIUWENSWARM.md (new)",
    });

    // User memory (local)
    memoryOptions.push({
      label: "User memory",
      value: userMemoryPath,
      description: hasUserMemory ? "Saved in ./JIUWENSWARM.local.md" : "Saved in ./JIUWENSWARM.local.md (new)",
    });

    // Add open folder option at the bottom (only when auto-memory enabled)
    if (autoMemoryEnabled) {
      memoryOptions.push({
        label: "Open auto-memory folder",
        value: `${OPEN_FOLDER_PREFIX}auto`,
        description: "",
      });
    }

    // Build toggle option at the top - align with Claude Code format: "Auto-memory: on/off"
    const toggleOption: { label: string; description: string; value: string } = {
      label: `Auto-memory: ${autoMemoryEnabled ? "on" : "off"}`,
      value: "__toggle__",
      description: "Press Enter to toggle",
    };

    // Combine all options: toggle at top, then files, then open folder
    const allOptions = [toggleOption, ...memoryOptions];

    let selectedValue: string | undefined;
    try {
      const [answer] = await ctx.askQuestions(
        [
          {
            header: "Memory",
            question: "Select an action:",
            options: allOptions.map((opt) => ({
              label: opt.label,
              description: opt.description,
            })),
          },
        ],
        "local_command_memory",
      );
      selectedValue = answer?.selected_options?.[0]
        ? allOptions.find((opt) => opt.label === answer.selected_options[0])?.value
        : undefined;
    } catch {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled Memory interaction.", "i"));
      return;
    }

    if (!selectedValue) {
      ctx.addItem(addInfo(ctx.sessionId, "Cancelled Memory interaction.", "i"));
      return;
    }

    // Handle selected action
    if (selectedValue === "__toggle__") {
      await toggleByKey(ctx, "auto_memory_enabled");
    } else if (selectedValue.startsWith(OPEN_FOLDER_PREFIX)) {
      // Open coding memory folder in system file explorer (unified with Auto Memory)
      const openPayload = await ctx.request<MemoryOpenResult>("memory.open", {
        project_dir: projectDir,
      });
      // Use coding_memory_dir as the unified memory location
      const targetDir = openPayload.coding_memory_dir || openPayload.auto_memory_dir;
      if (targetDir && ctx.openFolder) {
        ctx.openFolder(targetDir);
        ctx.addItem(
          addInfo(ctx.sessionId, "Opened memory folder", "m"),
        );
      }
    } else {
      // Edit the selected memory file
      await editMemoryByPath(ctx, selectedValue);
    }
  } catch (err) {
    ctx.addItem(
      addError(ctx.sessionId, `Failed to show Memory interface: ${err instanceof Error ? err.message : String(err)}`),
    );
  }
}

export function createMemoryCommand(): SlashCommand {
  return {
    name: "memory",
    altNames: ["mem"],
    description: "Manage memory settings and files (Auto-memory, edit, toggle, open)",
    usage: "/memory [list|edit|status|toggle|open] [args]",
    example: "/memory",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx) => {
      await showMemorySelector(ctx);
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
