import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

/**
 * /debug - 为本轮请求开启调试 dump（透传到服务端解析）。
 * Usage: /debug <prompt>
 *
 * 将原始 `/debug <prompt>` 字符串原样发往后端。后端 adapter 剥离前缀、
 * 挂载 DebugTraceLogger，把模型输出与工具调用写入
 * `~/.jiuwenswarm/.agent/traces/` 或 `.code/traces/` 下的 dump 文件。
 * 解析逻辑集中在服务端，TUI 只做透传，与 Team 模式行为一致。
 */
export function createDebugCommand(): SlashCommand {
  return {
    name: "debug",
    description: "为本轮请求开启调试 dump（透传到服务端解析）",
    usage: "/debug <prompt>",
    example: "/debug 你好",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: (ctx, args) => {
      const command = args.trim() ? `/debug ${args.trim()}` : "/debug";
      const requestId = ctx.sendMessage(command);
      if (!requestId) {
        ctx.addItem(
          makeItem(ctx.sessionId, "error", "offline: 等待重连后再发送 /debug 请求"),
        );
      }
    },
  };
}
