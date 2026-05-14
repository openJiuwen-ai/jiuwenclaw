import { visibleWidth } from "@mariozechner/pi-tui";
import type { Component } from "@mariozechner/pi-tui";
import type { HistoryItem } from "../../../core/types.js";
import { palette } from "../../theme.js";
import { padToWidth, prefixedLines, renderWrappedText, summarize } from "../../rendering/text.js";
import { renderClaudeResponseLines, renderMediaItems } from "./shared.js";

function renderGroupedHelpView(
  width: number,
  meta: Extract<HistoryItem, { kind: "info" }>["meta"],
): string[] {
  const lines: string[] = [];
  const innerWidth = Math.max(1, width);

  const version = meta?.version || "";
  const versionText = version ? `jiuwenclaw CLI v${version}` : "jiuwenclaw CLI";
  lines.push(...renderWrappedText(innerWidth, `· ${versionText} — ${meta?.title ?? "Slash Commands"}`, palette.text.info));
  lines.push("");

  for (const group of meta?.groups ?? []) {
    const groupTitle = `── ${group.name} `;
    const groupPadding = Math.max(0, innerWidth - visibleWidth(groupTitle));
    const fullGroupTitle = groupTitle + "─".repeat(groupPadding);
    lines.push(padToWidth(palette.text.secondary(fullGroupTitle), innerWidth));

    for (const item of group.items) {
      const value = item.value ? ` ${item.value}` : "";
      const labelLine = `  ${item.label}${value}`;
      lines.push(padToWidth(palette.text.accent(labelLine), innerWidth));
      if (item.description) {
        lines.push(padToWidth(palette.text.dim(`      ${item.description}`), innerWidth));
      }
    }
    lines.push("");
  }

  lines.push(padToWidth(palette.text.dim("Press Esc to close"), innerWidth));

  return lines;
}

export class SystemMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "system" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return renderWrappedText(width, `· ${this.entry.content}`, palette.text.system);
  }
}

export class ErrorMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "error" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return prefixedLines(
      renderWrappedText(Math.max(1, width - 2), this.entry.content, palette.status.error),
      width,
      "! ",
      palette.status.error,
      "  ",
    );
  }
}

export class CommandEchoComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "command_echo" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return [padToWidth(palette.surface.user(`❯ ${this.entry.content}`), width)];
  }
}

export class InfoMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "info" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const meta = this.entry.meta;

    if (meta?.view === "help" && meta.groups?.length) {
      return renderGroupedHelpView(width, meta);
    }

    const lines: string[] = [];
    const innerWidth = Math.max(1, width);
    const title = meta?.title ?? this.entry.content;
    lines.push(...renderWrappedText(innerWidth, `· ${title}`, palette.text.info));
    if (this.entry.mediaItems?.length) {
      lines.push(...renderMediaItems(width, this.entry.mediaItems));
    }
    for (const item of meta?.items ?? []) {
      const value = item.value ? `: ${item.value}` : "";
      lines.push(
        ...renderClaudeResponseLines(
          width,
          renderWrappedText(
            Math.max(1, width - 4),
            `${item.label}${value}`,
            palette.text.assistant,
          ),
          palette.text.assistant,
        ),
      );
      if (item.description) {
        lines.push(
          ...renderClaudeResponseLines(
            width,
            renderWrappedText(Math.max(1, width - 4), item.description, palette.text.dim),
            palette.text.dim,
          ),
        );
      }
    }
    return lines;
  }
}

export class CompactMessageComponent implements Component {
  constructor(
    private readonly entry: Exclude<HistoryItem, { kind: "tool_group" | "collapsed_tool_group" }>,
  ) {}

  invalidate(): void {}

  render(width: number): string[] {
    const content =
      this.entry.kind === "assistant" || this.entry.kind === "thinking"
        ? summarize(this.entry.content, 120)
        : this.entry.content;
    const prefix =
      this.entry.kind === "assistant"
        ? "• "
        : this.entry.kind === "thinking"
          ? "· "
          : this.entry.kind === "user"
            ? "> "
            : this.entry.kind === "error"
              ? "! "
              : "· ";
    const color =
      this.entry.kind === "error"
        ? palette.status.error
        : this.entry.kind === "assistant"
          ? palette.text.assistant
          : this.entry.kind === "user"
            ? palette.text.user
            : this.entry.kind === "thinking"
              ? palette.text.thinking
              : palette.text.dim;
    return renderWrappedText(width, `${prefix}${content}`, color);
  }
}
