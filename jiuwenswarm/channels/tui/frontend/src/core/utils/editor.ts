import { spawn, spawnSync, type SpawnOptions, type SpawnSyncOptions, type SpawnSyncReturns } from "node:child_process";
import { basename } from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import type { TUI } from "@mariozechner/pi-tui";

const GUI_EDITORS = [
  "code",
  "cursor",
  "windsurf",
  "codium",
  "subl",
  "atom",
  "notepad",
  "notepad++",
  "gedit",
  "kate",
  "mousepad",
];

const GUI_EDITOR_WAIT_FLAGS: Record<string, string[]> = {
  code: ["-w"],
  cursor: ["-w"],
  windsurf: ["-w"],
  codium: ["-w"],
  subl: ["--wait"],
  atom: ["--wait"],
};

export function getExternalEditor(): string {
  if (process.env.VISUAL?.trim()) return process.env.VISUAL.trim();
  if (process.env.EDITOR?.trim()) return process.env.EDITOR.trim();
  if (process.platform === "win32") return "start /wait notepad";
  return "vi";
}

export function getEditorInfo(): { source: string; value: string } {
  if (process.env.VISUAL) return { source: "$VISUAL", value: process.env.VISUAL };
  if (process.env.EDITOR) return { source: "$EDITOR", value: process.env.EDITOR };
  return {
    source: "default",
    value: process.platform === "win32" ? "start /wait notepad" : "vi",
  };
}

export function isGuiEditor(editor: string): boolean {
  // Check all parts of the command, not just the first word.
  // On Windows, the default editor is "start /wait notepad" — the actual
  // editor name ("notepad") is in the arguments, not the command itself.
  return editor.split(/\s+/).some((token) => {
    if (!token) return false;
    const base = basename(token);
    return GUI_EDITORS.some((gui) => base.includes(gui));
  });
}

export function parseEditorCommand(editor: string): { cmd: string; args: string[] } {
  const parts = editor.split(/\s+/);
  const cmd = parts[0];
  const baseArgs = parts.slice(1);

  const waitArgs = GUI_EDITOR_WAIT_FLAGS[cmd];
  if (waitArgs && !baseArgs.some((a) => waitArgs.includes(a))) {
    return { cmd, args: [...waitArgs, ...baseArgs] };
  }

  return { cmd, args: baseArgs };
}

function spawnFailed(result: SpawnSyncReturns<string | Buffer>): boolean {
  return result.status !== 0 || result.error != null;
}

/**
 * Open a file in the user's external editor.
 *
 * For GUI editors (notepad, VS Code, Sublime, etc.): spawns the editor
 * detached and non-blocking — the TUI keeps running, no stop/start cycle.
 * This avoids the race condition where tui.stop()/tui.start() could leave
 * stdin in a bad state (Kitty protocol query interference, buffered data
 * from the editor process, etc.).
 *
 * For terminal editors (vi, nano): uses spawnSync (blocking) with proper
 * alt-screen switching and tui.stop()/tui.start() — the TUI is suspended
 * while the editor takes over the terminal.
 *
 * @param onExit Called when the editor process exits (GUI editors only).
 *               Terminal editors call onExit synchronously after tui.start().
 */
export function openFileInEditor(tui: TUI, filePath: string, onExit?: () => void): void {
  const editor = getExternalEditor();
  const gui = isGuiEditor(editor);

  if (gui) {
    spawnGuiEditorDetached(editor, filePath, onExit);
    return;
  }

  // Terminal editor: spawnSync + tui.stop/start (blocks until editor exits)
  const { cmd, args } = parseEditorCommand(editor);

  tui.stop();

  try {
    // Enter alt screen + clear + show cursor.
    // The editor (vim/nano) will manage its own alt screen on top of ours.
    process.stdout.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25h");

    if (process.stdin.setRawMode) {
      process.stdin.setRawMode(false);
    }
    process.stdin.resume();

    const result = spawnEditorSync(cmd, args, filePath);
    if (spawnFailed(result)) {
      spawnFallbackSync(filePath);
    }
  } finally {
    // ── Terminal recovery (mirrors claude-code's exitAlternateScreen) ──
    //
    // Terminal editors (vim, nano, less) write smcup/rmcup (?1049h/?1049l).
    // On exit, vim's rmcup drops us back to the MAIN screen — our alt screen
    // is already gone. Simply writing ?1049l is a no-op here.
    //
    // We re-enter alt screen → clear → exit alt screen, which gives a clean
    // main screen without wiping the user's scrollback. This is the key fix
    // for the "Win10 + git bash + vim → TUI stuck" bug.
    process.stdout.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?1049l\x1b[?25l");

    // Drain any buffered stdin data left by the editor process.
    // vim may leave escape sequences in the stdin buffer; if these reach
    // tui.start()'s Kitty protocol query handler, they can confuse input
    // parsing and cause the TUI to appear frozen.
    try {
      while (process.stdin.read() !== null) {
        // discard all buffered data
      }
    } catch {
      // stdin not readable or already destroyed — ignore
    }

    tui.start();
    tui.requestRender(true);
    onExit?.();
  }
}

// ---------------------------------------------------------------------------
// GUI editor: non-blocking detached spawn (TUI keeps running)
// ---------------------------------------------------------------------------

/**
 * Spawn a GUI editor detached from the parent process.
 *
 * The editor runs in its own window/process. The TUI event loop keeps
 * running, so the user can interact with the TUI while editing.
 *
 * On exit, the optional `onExit` callback is called (best-effort — for
 * editors launched via shell wrappers like `code.cmd` on Windows, the
 * exit event may fire immediately when the wrapper exits, not when the
 * actual editor window closes).
 */
function spawnGuiEditorDetached(editor: string, filePath: string, onExit?: () => void): void {
  const detachedOpts: SpawnOptions = { detached: true, stdio: "ignore" };

  let child;

  if (process.platform === "win32") {
    // Windows: keep "start /wait" in the command.
    // "start /wait" is a cmd.exe builtin that makes the shell wait for the
    // GUI editor to close. Without it, cmd.exe would exit immediately after
    // launching the GUI app, and child.on('exit') would fire prematurely.
    // With it, the spawned cmd.exe process stays alive until the editor closes,
    // so the exit event fires at the right time.
    //
    // We use shell: true because:
    // - "start" is a cmd.exe builtin (not an executable)
    // - .cmd/.bat editors like code.cmd need shell resolution
    child = spawn(`${editor} "${filePath}"`, { ...detachedOpts, shell: true });
  } else {
    // POSIX: spawn the editor directly (no shell).
    // Strip --wait/-w flags — they're for blocking spawnSync, not needed
    // for non-blocking spawn. We detect exit via child.on('exit').
    const parts = editor.split(/\s+/).filter((p) => p && p !== "-w" && p !== "--wait");
    const cmd = parts[0];
    const fullArgs = [...parts.slice(1), filePath];
    child = spawn(cmd, fullArgs, detachedOpts);
  }

  child.on("error", () => {
    // Spawn failed — try fallback
    spawnFallbackAsync(filePath, onExit);
  });

  if (onExit) {
    child.on("exit", () => {
      onExit();
    });
  }

  // unref() so the TUI process can exit without waiting for the editor
  child.unref();
}

// ---------------------------------------------------------------------------
// Terminal editor: synchronous spawn (blocking)
// ---------------------------------------------------------------------------

function spawnEditorSync(cmd: string, args: string[], filePath: string): SpawnSyncReturns<string | Buffer> {
  const spawnOptions: SpawnSyncOptions = { stdio: "inherit" };
  const fullArgs = [...args, filePath];

  if (process.platform === "win32") {
    if (cmd === "start") {
      const waitFlag = fullArgs[0] === "/wait" ? "/wait " : "";
      const programArgs = waitFlag ? fullArgs.slice(1) : fullArgs;
      const quoted = programArgs.map((a) => `"${a}"`).join(" ");
      return spawnSync(`start ${waitFlag}"" ${quoted}`, { ...spawnOptions, shell: true });
    }
    const quoted = fullArgs.map((a) => `"${a}"`).join(" ");
    return spawnSync(`${cmd} ${quoted}`, { ...spawnOptions, shell: true });
  }

  return spawnSync(cmd, fullArgs, spawnOptions);
}

function spawnFallbackSync(filePath: string): SpawnSyncReturns<string | Buffer> {
  const spawnOptions: SpawnSyncOptions = { stdio: "inherit" };

  if (process.platform === "win32") {
    return spawnSync(`start /wait "" notepad "${filePath}"`, { ...spawnOptions, shell: true });
  }

  return spawnSync("vi", [filePath], spawnOptions);
}

function spawnFallbackAsync(filePath: string, onExit?: () => void): void {
  if (process.platform === "win32") {
    // Use "start /wait" so cmd.exe waits for notepad to close
    // (without /wait, cmd.exe exits immediately for GUI apps)
    const child = spawn(`start /wait "" notepad "${filePath}"`, {
      detached: true,
      stdio: "ignore",
      shell: true,
    });
    if (onExit) child.on("exit", onExit);
    child.unref();
    return;
  }

  const child = spawn("vi", [filePath], { detached: true, stdio: "ignore" });
  if (onExit) child.on("exit", onExit);
  child.unref();
}

// ---------------------------------------------------------------------------
// Folder opening (file explorer)
// ---------------------------------------------------------------------------

/**
 * Open a folder in the system file explorer (not an editor).
 * - Windows: explorer
 * - macOS: open -R (reveals in Finder)
 * - Linux: xdg-open (requires a GUI/Display; falls back gracefully on headless servers)
 *
 * Returns true if an explorer was (likely) launched, false if the platform
 * has no GUI explorer available (e.g. a headless Linux server). Callers can
 * use the false return to show a copyable path hint instead.
 */
export function openFolderInExplorer(folderPath: string): boolean {
  const spawnOptions: SpawnSyncOptions = { stdio: "inherit" };

  // Ensure folder exists before opening (explorer opens Documents if path doesn't exist)
  if (!existsSync(folderPath)) {
    try {
      mkdirSync(folderPath, { recursive: true });
    } catch {
      // Ignore errors - just try to open anyway
    }
  }

  if (process.platform === "win32") {
    spawnSync(`explorer "${folderPath}"`, { ...spawnOptions, shell: true });
    return true;
  } else if (process.platform === "darwin") {
    spawnSync("open", ["-R", folderPath], spawnOptions);
    return true;
  } else {
    // Headless Linux server: no xdg-open, no DISPLAY → don't block on a
    // spawnSync that will just hang or error out. Tell the caller to fall
    // back to a path hint.
    const hasDisplay = !!process.env.DISPLAY || !!process.env.WAYLAND_DISPLAY;
    if (!hasDisplay) {
      return false;
    }
    try {
      const res = spawnSync("xdg-open", [folderPath], spawnOptions);
      return res.status === 0 || res.status === null;
    } catch {
      return false;
    }
  }
}
