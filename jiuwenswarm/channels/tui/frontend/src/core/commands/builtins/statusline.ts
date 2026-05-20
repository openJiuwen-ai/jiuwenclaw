import { makeItem } from "../helpers.js";
import { loadTuiConfig, saveTuiConfig, type StatusLineSetting } from "../../tui-config-store.js";
import { CommandKind, type CommandContext, type SlashCommand } from "../types.js";

function getStatusLineConfig(): StatusLineSetting | undefined {
  return loadTuiConfig().statusLine;
}

function showCurrentConfig(ctx: CommandContext): void {
  const sl = getStatusLineConfig();
  if (!sl || sl.type !== "command" || !sl.command) {
    ctx.addItem(makeItem(ctx.sessionId, "info", "StatusLine — not configured", "m"));
    return;
  }
  const lines = [
    `command: '${sl.command}'`,
    `padding: ${sl.padding ?? 0}`,
  ];
  ctx.addItem(makeItem(ctx.sessionId, "info", `StatusLine\n  ${lines.join("\n  ")}`, "m"));
}

function setConfig(ctx: CommandContext, args: string): void {
  const command = args.trim().replace(/^['"]|['"]$/g, "");
  if (!command) {
    ctx.addItem(makeItem(ctx.sessionId, "error", "usage: /statusline set <command>", "m"));
    return;
  }
  saveTuiConfig({ statusLine: { type: "command", command } });
  ctx.restartStatusLine?.();
  ctx.addItem(
    makeItem(ctx.sessionId, "info", `StatusLine — Updated\n  command: '${command}'\n  padding: 0`, "m"),
  );
}

function clearConfig(ctx: CommandContext): void {
  saveTuiConfig({ statusLine: undefined });
  ctx.restartStatusLine?.();
  ctx.addItem(makeItem(ctx.sessionId, "info", "StatusLine — cleared", "m"));
}

function showJsonInput(ctx: CommandContext): void {
  const fields = [
    "session_id        — current session ID",
    "session_name      — session title / name",
    "cwd               — current working directory",
    "mode              — chat mode (agent.plan, code.normal, team, etc.)",
    "model             — AI model name",
    "provider          — model provider",
    "version           — TUI version",
    "connection        — connection status (connected, connecting, etc.)",
    "theme             — current theme name",
    "accent_color      — current accent color",
    "transcript_mode   — transcript display mode (compact/detailed)",
    "transcript_fold_mode — fold mode (none/tools/thinking/all)",
    "is_processing     — whether a request is in progress (true/false)",
    "is_paused         — whether session is paused (true/false)",
    "is_interrupted    — whether session was interrupted (true/false)",
    "cancellable_work  — whether cancellable work exists (true/false)",
    "streaming_state   — streaming state (idle, streaming, etc.)",
    "last_error        — last error message (empty string if none)",
    "evolution_status  — evolution status (idle/running)",
    "active_subtask_count — number of active subtasks",
    "todo_count        — number of todo items",
    "usage.total_input_tokens — total input tokens",
    "usage.total_output_tokens — total output tokens",
    "usage.total_tokens — total tokens (input + output)",
  ];
  ctx.addItem(
    makeItem(ctx.sessionId, "info", `StatusLine — JSON input fields\n${fields.map((f) => `  ${f}`).join("\n")}`, "m"),
  );
}

export function createStatusLineCommand(): SlashCommand {
  return {
    name: "statusline",
    description: "Configure custom status line footer",
    usage: "/statusline <set|clear|help|json>",
    example: "/statusline set 'echo $mode | $model'",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    subCommands: [
      {
        name: "set",
        description: "Set statusline command",
        usage: "/statusline set <command>",
        example: "/statusline set 'echo mode:$mode model:$model'",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: (ctx, args) => setConfig(ctx, args),
      },
      {
        name: "clear",
        description: "Clear statusline configuration",
        usage: "/statusline clear",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        isSafeConcurrent: true,
        action: (ctx) => clearConfig(ctx),
      },
      {
        name: "help",
        description: "Show statusline help and available fields",
        usage: "/statusline help",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        isSafeConcurrent: true,
        action: (ctx) => {
          showCurrentConfig(ctx);
          showJsonInput(ctx);
        },
      },
      {
        name: "json",
        description: "Show current JSON input data",
        usage: "/statusline json",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        isSafeConcurrent: true,
        action: (ctx) => showJsonInput(ctx),
      },
    ],
    action: (ctx, args) => {
      const sub = args.trim().split(/\s+/)[0];
      if (!sub) {
        showCurrentConfig(ctx);
        return;
      }
      const matched = createStatusLineCommand().subCommands?.find((s) => s.name === sub);
      if (matched) {
        const rest = args.trim().slice(sub.length).trim();
        matched.action(ctx, rest);
        return;
      }
      ctx.addItem(
        makeItem(ctx.sessionId, "error", `Unknown sub-command: ${sub}\nUsage: /statusline <set|clear|help|json>`, "m"),
      );
    },
  };
}