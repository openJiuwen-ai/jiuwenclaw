import { spawnSync, type SpawnSyncOptions, type SpawnSyncReturns } from "node:child_process";
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

export function openFileInEditor(tui: TUI, filePath: string): void {
  const editor = getExternalEditor();
  const { cmd, args } = parseEditorCommand(editor);
  const gui = isGuiEditor(editor);

  tui.stop();

  try {
    if (!gui) {
      process.stdout.write("\x1b[?1049h");
      process.stdout.write("\x1b[2J\x1b[H");
      process.stdout.write("\x1b[?25h");

      if (process.stdin.setRawMode) {
        process.stdin.setRawMode(false);
      }
      process.stdin.resume();
    }

    const result = spawnEditor(cmd, args, filePath);
    if (spawnFailed(result)) {
      spawnFallback(filePath);
    }
  } finally {
    if (!gui) {
      process.stdout.write("\x1b[?1049l");
    }

    tui.start();
    tui.requestRender(true);
  }
}

function spawnEditor(cmd: string, args: string[], filePath: string): SpawnSyncReturns<string | Buffer> {
  const spawnOptions: SpawnSyncOptions = { stdio: "inherit" };
  const fullArgs = [...args, filePath];

  if (process.platform === "win32") {
    if (cmd === "start") {
      // Windows start command: start /wait "title" program args
      // An empty title "" prevents the first quoted arg from being treated as window title
      // /wait must remain unquoted — it's a flag, not an argument
      const waitFlag = fullArgs[0] === "/wait" ? "/wait " : "";
      const programArgs = waitFlag ? fullArgs.slice(1) : fullArgs;
      const quoted = programArgs.map((a) => `"${a}"`).join(" ");
      return spawnSync(`start ${waitFlag}"" ${quoted}`, { ...spawnOptions, shell: true });
    }
    // shell: true required for .cmd/.bat files (code.cmd, cursor.cmd)
    const quoted = fullArgs.map((a) => `"${a}"`).join(" ");
    return spawnSync(`${cmd} ${quoted}`, { ...spawnOptions, shell: true });
  }

  // POSIX: direct argv spawn — no shell, prevents command injection
  return spawnSync(cmd, fullArgs, spawnOptions);
}

function spawnFallback(filePath: string): SpawnSyncReturns<string | Buffer> {
  const spawnOptions: SpawnSyncOptions = { stdio: "inherit" };

  if (process.platform === "win32") {
    return spawnSync(`start /wait "" notepad "${filePath}"`, { ...spawnOptions, shell: true });
  }

  return spawnSync("vi", [filePath], spawnOptions);
}

/**
 * Open a folder in the system file explorer (not an editor).
 * - Windows: explorer
 * - macOS: open -R (reveals in Finder)
 * - Linux: xdg-open
 */
export function openFolderInExplorer(folderPath: string): void {
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
    // Windows: use explorer to open folder
    spawnSync(`explorer "${folderPath}"`, { ...spawnOptions, shell: true });
  } else if (process.platform === "darwin") {
    // macOS: use open -R to reveal in Finder
    spawnSync("open", ["-R", folderPath], spawnOptions);
  } else {
    // Linux: use xdg-open
    spawnSync("xdg-open", [folderPath], spawnOptions);
  }
}