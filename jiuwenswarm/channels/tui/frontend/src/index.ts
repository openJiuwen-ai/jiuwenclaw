#!/usr/bin/env node

import { ProcessTerminal, TUI } from "@mariozechner/pi-tui";
import { parseArgs } from "node:util";
import { CliPiAppState } from "./app-state.js";
import { CommandService } from "./core/commands/CommandService.js";
import { createBuiltinCommands } from "./core/commands/registry.js";
import { WsClient } from "./core/ws-client.js";
import { AppScreen } from "./ui/app-screen.js";
import { HandoffPortImpl } from "./core/supervision/handoff-port.js";
import { ReauthenticationPortImpl } from "./core/supervision/reauth-port.js";
import { TaskLifecyclePortImpl } from "./core/supervision/task-lifecycle-port.js";
import { UiLifecyclePortImpl } from "./core/supervision/ui-lifecycle.js";
import { readSupervisionEnv } from "./core/supervision/supervised-env.js";
import type { UiExitReason } from "./core/supervision/protocol.js";

const { values } = parseArgs({
  options: {
    url: { type: "string", default: "ws://127.0.0.1:19001/tui" },
    session: { type: "string" },
    token: { type: "string", default: "" },
    "user-id": { type: "string", default: "" },
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
  --user-id <id>    User identifier for the session
  -h, --help        Show this help
`);
  process.exit(0);
}

// 允许通过环境变量跳过 TTY 检查（用于自动化测试）
if (!process.env.JIUWENSWARM_TUI_HEADLESS && (!process.stdin.isTTY || !process.stdout.isTTY)) {
  console.error("jiuwenswarm-tui requires an interactive TTY");
  process.exit(1);
}

const wsClient = new WsClient(values.url ?? "ws://127.0.0.1:19001/tui", values.token ?? "", values["user-id"] ?? "");

// 读取 launcher 注入的监督协议快照（非托管启动时 supervised=false）。
const supervisionEnv = readSupervisionEnv();

const terminal = new ProcessTerminal();
const tui = new TUI(terminal);

let closed = false;
let screen: AppScreen | null = null;

/**
 * 统一顶层关闭路径：串行完成退出通知 → screen.dispose → appState.stop →
 * tui.stop → process.exit。由 UiLifecyclePort 封装，handoff/reauth 走该路径。
 * index.ts 启动时即构造，避免依赖 AppState 实例。
 */
function buildUiLifecycle(): UiLifecyclePortImpl {
  return new UiLifecyclePortImpl({
    notifyDisconnect: async (reason: UiExitReason) => {
      await appState.notifyDisconnectBeforeExit(reason);
    },
    disposeScreen: () => {
      screen?.dispose();
    },
    stopAppState: () => {
      appState.stop();
    },
    stopTui: () => {
      try {
        tui.stop();
      } catch {
        // Ignore repeated stop failures.
      }
    },
  });
}

// 先建 UiLifecyclePort 和 TaskLifecyclePort（后两者不依赖 AppState 实例的方法）。
// AppState 构造函数会接收已构造的端口；这里先用占位引用，构造后回填。
let uiLifecycle = buildUiLifecycle();

const appState = new CliPiAppState(wsClient, values.session, {
  // 在构造 AppState 之前无法直接构造 TaskLifecyclePort/HandoffPort/ReauthPort
  // （它们依赖 AppState 的方法）；这里先传 null，构造后回填。
  handoffPort: null,
  taskLifecycle: null,
  reauthPort: null,
  uiLifecycle,
});

// AppState 已构造完成，现在构造依赖 AppState 的端口并回填。
const taskLifecycle = new TaskLifecyclePortImpl({
  getSnapshot: () => {
    const snapshot = appState.getSnapshot();
    return {
      cancellableWork: snapshot.cancellableWork,
      sessionId: snapshot.sessionId,
    };
  },
  cancel: (opts) => appState.cancel(opts),
  sendEventOnly: (method, params) => appState.sendEventOnly(method, params),
  onInterruptResult: (h) => appState.onInterruptResult(h),
  onConnectionLost: (h) => appState.onConnectionLost(h),
  onStop: (h) => appState.onStop(h),
  isConnectionAlive: () => appState.getSnapshot().connectionStatus === "connected",
});
const handoffPort = new HandoffPortImpl(supervisionEnv, uiLifecycle);
const reauthPort = new ReauthenticationPortImpl(supervisionEnv, handoffPort, uiLifecycle);
appState.setSupervisionPorts({ handoffPort, taskLifecycle, reauthPort, uiLifecycle });

// 配置 ws-client 在权威认证过期（close code 1008）时触发重新认证。
// 仅托管模式注入了 reauth exit code 时，reauthPort 才会以 89 退出；
// 非托管模式下 reauthPort.requestReauthentication 会抛错，UI 保持 auth_failed 状态。
wsClient.onAuthExpired = () => {
  void reauthPort.requestReauthentication("access-token-expired").catch(() => {
    // 非托管模式或 handoff 已在进行：保持原有 auth_failed UI，不强制退出。
  });
};

const commandService = new CommandService();
commandService.register(createBuiltinCommands({ switchEnabled: supervisionEnv.supervised }));

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
