#!/usr/bin/env node

import { ProcessTerminal, TUI } from "@mariozechner/pi-tui";
import { parseArgs } from "node:util";
import { CliPiAppState } from "./app-state.js";
import { CommandService } from "./core/commands/CommandService.js";
import { createBuiltinCommands } from "./core/commands/registry.js";
import { WsClient } from "./core/ws-client.js";
import { AppScreen } from "./ui/app-screen.js";

const { values } = parseArgs({
  options: {
    url: { type: "string", default: "ws://127.0.0.1:19001/tui" },
    session: { type: "string" },
    token: { type: "string", default: "" },
    help: { type: "boolean", short: "h" },
  },
  strict: true,
});

if (values.help) {
  console.log(`jiuwenswarm-tui - Terminal CLI for JiuwenSwarm

Options:
  --url <url>       Gateway CLI WebSocket URL (default: ws://127.0.0.1:19001/tui)
  --session <id>    Resume a specific session
  --token <token>   Authentication token
  -h, --help        Show this help
`);
  process.exit(0);
}

// 允许通过环境变量跳过 TTY 检查（用于自动化测试）
if (!process.env.JIUWENSWARM_TUI_HEADLESS && (!process.stdin.isTTY || !process.stdout.isTTY)) {
  console.error("jiuwenswarm-tui requires an interactive TTY");
  process.exit(1);
}

const wsClient = new WsClient(values.url ?? "ws://127.0.0.1:19001/tui", values.token ?? "");
const appState = new CliPiAppState(wsClient, values.session);
const commandService = new CommandService();
commandService.register(createBuiltinCommands());

const terminal = new ProcessTerminal();
const tui = new TUI(terminal);

let closed = false;
let screen: AppScreen | null = null;

/** 正常退出 CLI 前显式通知服务端；异常崩溃不走该路径。 */
async function notifyDisconnectBeforeExit(): Promise<void> {
  await appState.notifyDisconnectBeforeExit("user_exit");
}

async function closeUi(exitCode = 0): Promise<void> {
  if (closed) return;
  closed = true;
  try {
    await notifyDisconnectBeforeExit();
  } catch {
    // Best effort only.
  }
  screen?.dispose();
  appState.stop();
  try {
    tui.stop();
  } catch {
    // Ignore repeated stop failures.
  }
  process.exit(exitCode);
}

async function crash(error: unknown): Promise<void> {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  if (!closed) {
    screen?.dispose();
    appState.stop();
    try {
      tui.stop();
    } catch {
      // Ignore repeated stop failures.
    }
    closed = true;
  }
  console.error(message);
  process.exit(1);
}

screen = new AppScreen(tui, appState, commandService, () => {
  void closeUi(0);
});
tui.addChild(screen);
tui.setFocus(screen);

process.on("SIGTERM", () => {
  void closeUi(0);
});
// 双击 Ctrl+C 退出：第一次中断当前任务，3 秒内再按一次退出进程。
// 当 Ctrl+C 消费在取消命令（如 /recap）上时，重置计时器，
// 需要再连按两次才能退出，而非只需一次。
let lastInterruptTime = 0;
process.on("SIGINT", () => {
  const now = Date.now();
  if (now - lastInterruptTime < 3000) {
    void closeUi(0);
    return;
  }
  lastInterruptTime = now;
  screen?.interruptTask();
});
process.on("uncaughtException", (error) => {
  void crash(error);
});
process.on("unhandledRejection", (error) => {
  void crash(error);
});

appState.start();
tui.start();
