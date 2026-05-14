import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { copyToClipboard } from "../clipboard.js";
import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import type { HistoryItem, ToolCallDisplay } from "../../types.js";

function formatTimestamp(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day}-${hours}${minutes}${seconds}`;
}

function extractFirstPrompt(entries: HistoryItem[]): string {
  const firstUserEntry = entries.find((e) => e.kind === "user");
  if (!firstUserEntry || firstUserEntry.kind !== "user") return "";
  const text = firstUserEntry.content.trim().split("\n")[0] || "";
  return text.length > 50 ? text.substring(0, 49) + "…" : text;
}

function sanitizeFilename(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s一-鿿-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function renderEntriesToPlainText(entries: HistoryItem[]): string {
  const lines: string[] = [];

  for (const entry of entries) {
    const time = entry.at ? new Date(entry.at).toLocaleString() : "";

    switch (entry.kind) {
      case "user":
        lines.push(`[User] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "assistant":
        lines.push(`[Assistant] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "thinking":
        lines.push(`[Thinking] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "tool_group": {
        const toolNames = entry.tools.map((t: ToolCallDisplay) => t.name).join(", ");
        lines.push(`[Tools] ${time} — ${toolNames}`);
        for (const tool of entry.tools) {
          lines.push(`  ${tool.name}${tool.summary ? `: ${tool.summary}` : ""}`);
          if (tool.result) {
            const truncated =
              tool.result.length > 500 ? tool.result.substring(0, 499) + "…" : tool.result;
            lines.push(`  Result: ${truncated}`);
          }
        }
        lines.push("");
        break;
      }

      case "collapsed_tool_group": {
        const hint = entry.latestHint || "";
        const counts = entry.counts;
        const countStr = Object.entries(counts)
          .filter(([, v]) => v > 0)
          .map(([k, v]) => `${v} ${k}`)
          .join(", ");
        lines.push(`[Tools] ${time} — ${hint || countStr}`);
        lines.push("");
        break;
      }

      case "system":
        lines.push(`[System] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "error":
        lines.push(`[Error] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "info":
        lines.push(`[Info] ${time}`);
        lines.push(entry.content);
        lines.push("");
        break;

      case "diff":
        lines.push(`[Diff] ${time}`);
        for (const turn of entry.meta.turns) {
          lines.push(`  Turn ${turn.turnIndex}: ${turn.userPromptPreview}`);
          for (const [, file] of Object.entries(turn.files)) {
            lines.push(
              `    ${file.filePath} (+${file.linesAdded}/-${file.linesRemoved}${file.isNewFile ? " [new]" : ""})`,
            );
          }
        }
        lines.push("");
        break;
    }
  }

  return lines.join("\n");
}

export function createExportCommand(): SlashCommand {
  return {
    name: "export",
    description: "Export current conversation to a file or clipboard",
    usage: "/export [filename]",
    example: "/export my-chat.txt",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: (ctx, args) => {
      const content = renderEntriesToPlainText(ctx.entries);
      const arg = args.trim();

      if (!arg) {
        if (!copyToClipboard(content)) {
          ctx.addItem(
            addError(ctx.sessionId, "Clipboard unavailable — specify a filename to export to file"),
          );
          return;
        }
        ctx.addItem(addInfo(ctx.sessionId, "Conversation exported to clipboard", "e"));
        return;
      }

      const filename = arg.endsWith(".txt") ? arg : arg.replace(/\.[^.]+$/, "") + ".txt";
      const workspaceDir = ctx.getWorkspaceDir() || process.cwd();
      const filepath = join(workspaceDir, filename);

      try {
        writeFileSync(filepath, content, { encoding: "utf-8" });
        ctx.addItem(addInfo(ctx.sessionId, `Conversation exported to: ${filepath}`, "e"));
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown error";
        ctx.addItem(addError(ctx.sessionId, `Failed to export: ${message}`));
      }
    },
    completion: (ctx, partial) => {
      const firstPrompt = extractFirstPrompt(ctx.entries);
      const timestamp = formatTimestamp(new Date());
      const defaults: string[] = [];

      if (firstPrompt) {
        const sanitized = sanitizeFilename(firstPrompt);
        defaults.push(`${timestamp}-${sanitized}.txt`);
      }
      defaults.push(`conversation-${timestamp}.txt`);

      if (partial) {
        return defaults.filter((d) => d.startsWith(partial));
      }
      return defaults;
    },
  };
}