import { matchesKey } from "@mariozechner/pi-tui";

/**
 * 快捷键约定（Ctrl+C）：
 * 第一次按下：设置本地中断标志（用于中断长运行命令如日志流）；
 * 如果有服务端任务运行，同时发送 chat.interrupt；
 * 如果处于空闲状态且无本地中断发生，则清空输入框；
 * 1 秒内再次按下则退出 CLI/TUI。
 */

let lastInterruptTime = 0;

export interface AppScreenKeymapDelegate {
  /** Interrupt server-side task (send chat.interrupt) */
  interruptTask(): void;
  /**
   * Set local interrupt flag only (for long-running local commands like log streaming).
   * Returns true if an active command WS request was cancelled — this means the
   * Ctrl+C keystroke was consumed by the command cancellation and the "double-press-
   * to-exit" timer should be reset.
   */
  requestLocalInterrupt(): boolean;
  /** Show a brief hint that pressing Ctrl+C again will exit */
  showCtrlCExitHint(): void;
  exitApp(): void;
  toggleTodos(): void;
  toggleTeamPanel(): void;
  toggleTranscript(): void;
  redraw(): void;
  clearInput(): void;
  isIdle(): boolean;
  /** Check if there's a server task running (for deciding whether to send chat.interrupt) */
  hasServerTask(): boolean;
}

interface KeyBinding {
  key: Parameters<typeof matchesKey>[1];
  label: string;
  description: string;
  run: (delegate: AppScreenKeymapDelegate) => void;
}

export const APP_SCREEN_KEY_BINDINGS: readonly KeyBinding[] = [
  {
    key: "ctrl+c",
    label: "ctrl+c",
    description: "中断任务；连按两次退出",
    run: (delegate) => {
      const now = Date.now();
      if (now - lastInterruptTime < 3000) {
        delegate.exitApp();
        return;
      }

      // Always set local interrupt flag (for long-running local commands)
      // Returns true if an active command request was cancelled — this means
      // Ctrl+C was consumed by command cancellation, not a generic interrupt.
      const commandCancelled = delegate.requestLocalInterrupt();

      // Only send chat.interrupt if there's a server task running
      if (delegate.hasServerTask()) {
        delegate.interruptTask();
      }

      // When a command (e.g. /recap) was cancelled, reset the double-press timer
      // so the user needs TWO fresh Ctrl+C presses to exit, not just one more.
      if (commandCancelled && !delegate.hasServerTask()) {
        lastInterruptTime = 0;
        // Don't show the "Press Ctrl+C again to exit" hint — the user just
        // cancelled a command, they don't intend to exit the TUI.
        return;
      }

      // If idle (no server task and no local command running), clear input
      if (delegate.isIdle()) {
        delegate.clearInput();
      }

      // Show hint that pressing Ctrl+C again will exit
      delegate.showCtrlCExitHint();

      lastInterruptTime = now;
    },
  },
  {
    key: "ctrl+d",
    label: "ctrl+d",
    description: "中断任务；连按两次退出",
    run: (delegate) => {
      const now = Date.now();
      if (now - lastInterruptTime < 3000) {
        delegate.exitApp();
        return;
      }
      lastInterruptTime = now;
      delegate.interruptTask();
      delegate.showCtrlCExitHint();
    },
  },
  {
    key: "ctrl+l",
    label: "ctrl+l",
    description: "redraw screen",
    run: (delegate) => {
      delegate.redraw();
    },
  },
  {
    key: "ctrl+t",
    label: "ctrl+t",
    description: "toggle todos",
    run: (delegate) => {
      delegate.toggleTodos();
    },
  },
  {
    key: "ctrl+g",
    label: "ctrl+g",
    description: "toggle team panel",
    run: (delegate) => {
      delegate.toggleTeamPanel();
    },
  },
  {
    key: "ctrl+o",
    label: "ctrl+o",
    description: "toggle transcript detail",
    run: (delegate) => {
      delegate.toggleTranscript();
    },
  },
] as const;

export function handleAppScreenKeyInput(data: string, delegate: AppScreenKeymapDelegate): boolean {
  for (const binding of APP_SCREEN_KEY_BINDINGS) {
    if (!matchesKey(data, binding.key)) continue;
    binding.run(delegate);
    return true;
  }

  return false;
}
