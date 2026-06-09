import type { Component } from "@mariozechner/pi-tui";
import type { HistoryItem, FileDiff, GitDiffFile } from "../../../core/types.js";
import { palette } from "../../theme.js";
import { renderWrappedText } from "../../rendering/text.js";

const MAX_FILES_PER_SECTION = 5;

export class DiffComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "diff" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const showDetail = this.entry.meta.showDetail === true;
    return showDetail ? this._renderDetail(width) : this._renderCompact(width);
  }

  // ── Compact view (default /diff) ──────────────────────────────────

  private _renderCompact(width: number): string[] {
    const lines: string[] = [];
    const turns = this.entry.meta.turns || [];
    const gitDiff = this.entry.meta.gitDiff || null;
    const innerWidth = Math.max(1, width);

    const hasTurns = turns.length > 0;
    const hasGitDiff = gitDiff && gitDiff.stats.filesChanged > 0;

    if (!hasTurns && !hasGitDiff) {
      lines.push(...renderWrappedText(innerWidth, "· No file changes in this session", palette.text.dim));
      return lines;
    }

    // Header
    lines.push("");
    lines.push(...renderWrappedText(innerWidth,
      `╭─ /diff ${"─".repeat(Math.max(0, innerWidth - 9))}`,
      palette.text.info));

    // --- Git working tree ---
    if (hasGitDiff) {
      const trackedFiles: GitDiffFile[] = [];
      const untrackedFiles: GitDiffFile[] = [];
      for (const f of Object.values(gitDiff!.files)) {
        (f.isNewFile ? untrackedFiles : trackedFiles).push(f);
      }

      lines.push(...renderWrappedText(innerWidth,
        `│ 🗂 Working Tree  +${gitDiff!.stats.linesAdded} -${gitDiff!.stats.linesRemoved}`,
        palette.text.accent));

      // Tracked
      for (const f of trackedFiles.slice(0, MAX_FILES_PER_SECTION)) {
        lines.push(...this._renderCompactFile(f, innerWidth));
      }
      const remainingTracked = trackedFiles.length - MAX_FILES_PER_SECTION;
      if (remainingTracked > 0) {
        lines.push(...renderWrappedText(innerWidth,
          `│   ...and ${remainingTracked} more tracked file(s)`, palette.text.dim));
      }

      // Untracked
      if (untrackedFiles.length > 0) {
        const shown = untrackedFiles.slice(0, MAX_FILES_PER_SECTION);
        for (const f of shown) {
          lines.push(...this._renderCompactFile(f, innerWidth, "(untracked)"));
        }
        const remainingUntracked = untrackedFiles.length - MAX_FILES_PER_SECTION;
        if (remainingUntracked > 0) {
          lines.push(...renderWrappedText(innerWidth,
            `│   ...and ${remainingUntracked} more untracked file(s)`, palette.text.dim));
        }
      }

      // separator before turns
      if (hasTurns) {
        lines.push(...renderWrappedText(innerWidth, `│ ${"─".repeat(Math.max(0, innerWidth - 3))}`, palette.text.dim));
      }
    }

    // --- Per-turn sections ---
    for (const turn of turns) {
      const fileList = Object.values(turn.files);
      const promptPreview = turn.userPromptPreview.length > 40
        ? turn.userPromptPreview.slice(0, 40) + "..."
        : turn.userPromptPreview;

      lines.push(...renderWrappedText(innerWidth,
        `│ 📋 Turn ${turn.turnIndex}: "${promptPreview}"  +${turn.stats.linesAdded} -${turn.stats.linesRemoved}`,
        palette.text.accent));

      for (const f of fileList.slice(0, MAX_FILES_PER_SECTION)) {
        lines.push(...this._renderCompactFile(f, innerWidth));
      }
      if (fileList.length > MAX_FILES_PER_SECTION) {
        lines.push(...renderWrappedText(innerWidth,
          `│   ...and ${fileList.length - MAX_FILES_PER_SECTION} more`, palette.text.dim));
      }
    }

    // Footer
    lines.push(...renderWrappedText(innerWidth,
      `╰${"─".repeat(innerWidth - 2)}`, palette.text.dim));
    lines.push(...renderWrappedText(innerWidth,
      "Use /diff --detail to see full diffs", palette.text.dim));
    lines.push("");

    return lines;
  }

  private _renderCompactFile(
    fileDiff: FileDiff | GitDiffFile,
    width: number,
    tag?: string,
  ): string[] {
    const fileName = fileDiff.filePath.split(/[/\\]/).pop() || fileDiff.filePath;
    const label = tag || (fileDiff.isNewFile ? "(new)" : "");
    const added = palette.status.success(`+${fileDiff.linesAdded}`);
    const removed = palette.status.error(`-${fileDiff.linesRemoved}`);

    const lines: string[] = [];
    lines.push(...renderWrappedText(width,
      `│   ${fileName} ${label}  ${added} ${removed}`,
      palette.text.assistant));
    return lines;
  }

  // ── Detail view (/diff --detail) ──────────────────────────────────

  private _renderDetail(width: number): string[] {
    const lines: string[] = [];
    const turns = this.entry.meta.turns || [];
    const gitDiff = this.entry.meta.gitDiff || null;
    const innerWidth = Math.max(1, width);

    const hasTurns = turns.length > 0;
    const hasGitDiff = gitDiff && gitDiff.stats.filesChanged > 0;

    if (!hasTurns && !hasGitDiff) {
      lines.push(...renderWrappedText(innerWidth, "· No file changes in this session", palette.text.dim));
      return lines;
    }

    const totalFiles = (gitDiff?.stats.filesChanged ?? 0)
      + turns.reduce((sum, t) => sum + t.stats.filesChanged, 0);
    const totalAdded = (gitDiff?.stats.linesAdded ?? 0)
      + turns.reduce((sum, t) => sum + t.stats.linesAdded, 0);
    const totalRemoved = (gitDiff?.stats.linesRemoved ?? 0)
      + turns.reduce((sum, t) => sum + t.stats.linesRemoved, 0);

    lines.push("");
    lines.push(...renderWrappedText(innerWidth,
      `╭─ /diff --detail ${"─".repeat(Math.max(0, innerWidth - 18))}`,
      palette.text.info));

    const statsLine = `│ ${totalFiles} files  +${totalAdded} -${totalRemoved}`;
    lines.push(...renderWrappedText(innerWidth, statsLine, palette.text.dim));

    if (hasGitDiff) {
      lines.push(...renderWrappedText(innerWidth, `├${"─".repeat(innerWidth - 2)}`, palette.text.dim));
      lines.push(...renderWrappedText(innerWidth,
        `│ 🗂 Working Tree  +${gitDiff!.stats.linesAdded} -${gitDiff!.stats.linesRemoved}`,
        palette.text.accent));
      for (const [, fileDiff] of Object.entries(gitDiff!.files)) {
        lines.push(...this._renderFileDiff(fileDiff, innerWidth));
      }
    }

    for (const turn of turns) {
      lines.push(...renderWrappedText(innerWidth, `├${"─".repeat(innerWidth - 2)}`, palette.text.dim));
      const promptPreview = turn.userPromptPreview.length >= 30
        ? turn.userPromptPreview + "..."
        : turn.userPromptPreview;
      lines.push(...renderWrappedText(innerWidth,
        `│ Turn ${turn.turnIndex}: "${promptPreview}"`,
        palette.text.accent));
      for (const [, fileDiff] of Object.entries(turn.files)) {
        lines.push(...this._renderFileDiff(fileDiff, innerWidth));
      }
    }

    lines.push(...renderWrappedText(innerWidth, `╰${"─".repeat(innerWidth - 2)}`, palette.text.dim));
    lines.push("");

    return lines;
  }

  // ── Shared: full diff with hunks ──────────────────────────────────

  private _renderFileDiff(fileDiff: FileDiff | GitDiffFile, width: number): string[] {
    const lines: string[] = [];
    const fileName = fileDiff.filePath.split(/[/\\]/).pop() || fileDiff.filePath;

    let timeStr = "";
    if (fileDiff.lastEditTime) {
      const dt = new Date(fileDiff.lastEditTime);
      timeStr = ` [${dt.toLocaleDateString()} ${dt.toLocaleTimeString()}]`;
    }

    const header = `│   ${fileName} ${fileDiff.isNewFile ? "(new)" : ""} +${fileDiff.linesAdded} -${fileDiff.linesRemoved}${timeStr}`;
    lines.push(...renderWrappedText(width, header, palette.text.assistant));

    const maxHunkLines = 20;
    let totalLines = 0;

    for (const hunk of fileDiff.hunks) {
      if (totalLines >= maxHunkLines) {
        lines.push(...renderWrappedText(width, `│     ... (truncated)`, palette.text.dim));
        break;
      }

      const hunkHeader = `│     @@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`;
      lines.push(...renderWrappedText(width, hunkHeader, palette.text.dim));

      for (const line of hunk.lines) {
        if (totalLines >= maxHunkLines) break;
        totalLines++;

        if (line.startsWith("+")) {
          lines.push(...renderWrappedText(width, `│     ${palette.status.success(line)}`, palette.text.dim));
        } else if (line.startsWith("-")) {
          lines.push(...renderWrappedText(width, `│     ${palette.status.error(line)}`, palette.text.dim));
        } else {
          lines.push(...renderWrappedText(width, `│     ${palette.text.dim(line)}`, palette.text.dim));
        }
      }
    }

    return lines;
  }
}
