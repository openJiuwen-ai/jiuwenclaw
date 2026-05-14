import { addCommandEcho, addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export interface TurnInfo {
  turn_index: number;
  content_preview: string;
  timestamp: number;
  id: string;
  request_id: string;
  stats: {
    filesChanged: number;
    linesAdded: number;
    linesRemoved: number;
  };
}

export interface ListTurnsPayload {
  turns?: TurnInfo[];
  total?: number;
}

export interface RewindPayload {
  session_id?: string;
  turn_index?: number;
  content?: string;
  content_preview?: string;
  remaining_records?: number;
  removed_records?: number;
  restored_files?: string[];
  deleted_files?: string[];
  restore_errors?: { file: string; error: string }[];
}

/** 恢复选项类型 */
type RestoreOption = "both" | "conversation" | "code" | "cancel";

export function createRewindCommand(): SlashCommand {
  return {
    name: "rewind",
    altNames: ["checkpoint"],
    description: "Rewind the conversation to before a previous turn",
    usage: "/rewind [turn_number]",
    example: "/rewind 2",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      if (ctx.isProcessing) {
        ctx.addItem(
          addError(ctx.sessionId, "session is busy; stop the current run before rewinding"),
        );
        return;
      }

      const directTurn = args.trim();

      try {
        const payload = await ctx.request<ListTurnsPayload>("history.list_turns", {
          session_id: ctx.sessionId,
        });

        const turns = payload.turns ?? [];
        const total = payload.total ?? turns.length;

        if (turns.length === 0) {
          ctx.addItem(addInfo(ctx.sessionId, "No conversation turns to rewind to", "r"));
          return;
        }

        let selectedTurnIndex: number;

        if (directTurn) {
          const parsed = parseInt(directTurn, 10);
          if (Number.isNaN(parsed) || parsed < 1 || parsed > total) {
            ctx.addItem(
              addError(
                ctx.sessionId,
                `Invalid turn number: ${directTurn}. Valid range: 1-${total}`,
              ),
            );
            return;
          }
          selectedTurnIndex = parsed;
        } else {
          const items = turns.map((t) => {
            const time = t.timestamp
              ? new Date(t.timestamp * 1000).toLocaleString()
              : "-";
            const statsStr =
              t.stats.filesChanged > 0
                ? ` | files: ${t.stats.filesChanged} +${t.stats.linesAdded}/-${t.stats.linesRemoved}`
                : "";
            return {
              label: String(t.turn_index),
              value: `${t.content_preview}  |  ${time}${statsStr}`,
            };
          });

          ctx.addItem(
            addInfo(ctx.sessionId, `Conversation Turns (${total} total)`, "r", {
              view: "list",
              title: "Rewind - Select a Turn",
              items,
            }),
          );

          const answers = await ctx.askQuestions(
            [
              {
                header: "Turn",
                question: "Which turn do you want to rewind to? (this turn and all after will be removed)",
                options: turns.map((t) => ({
                  label: String(t.turn_index),
                  description: t.content_preview,
                })),
              },
            ],
            "rewind",
          );

          const userInput = answers[0]?.selected_options?.[0] || answers[0]?.custom_input || "";
          const parsed = parseInt(userInput, 10);
          if (Number.isNaN(parsed) || parsed < 1 || parsed > total) {
            ctx.addItem(addError(ctx.sessionId, `Invalid turn number: ${userInput}`));
            return;
          }
          selectedTurnIndex = parsed;
        }

        const selectedTurn = turns.find((t) => t.turn_index === selectedTurnIndex);
        if (!selectedTurn) {
          ctx.addItem(addError(ctx.sessionId, `Turn ${selectedTurnIndex} not found`));
          return;
        }

        // 判断目标 turn 是否有文件变更（决定是否显示 code 相关选项）
        const hasCodeChanges = selectedTurn.stats.filesChanged > 0;

        // 构建恢复选项
        const restoreOptions: { label: string; description: string; value: RestoreOption }[] = [
          {
            label: "Restore conversation and code",
            description: "Remove this turn and all after; restore modified files to their prior state",
            value: "both",
          },
          {
            label: "Restore conversation only",
            description: "Remove this turn and all after; files remain unchanged",
            value: "conversation",
          },
        ];

        if (hasCodeChanges) {
          restoreOptions.push({
            label: "Restore code only",
            description: "Restore modified files to their prior state; conversation remains unchanged",
            value: "code",
          });
        }

        restoreOptions.push({
          label: "Cancel",
          description: "Keep conversation and files as is",
          value: "cancel",
        });

        // 局限提示
        const limitationNote =
          "\nNote: Rewinding does not affect files edited manually or via bash commands.";

        const confirmAnswers = await ctx.askQuestions(
          [
            {
              header: "Confirm Rewind",
              question:
                `Rewind to before turn ${selectedTurnIndex}: "${selectedTurn.content_preview}"?` +
                limitationNote,
              options: restoreOptions,
            },
          ],
          "rewind_confirm",
        );

        const selectedOption = confirmAnswers[0]?.selected_options?.[0] as RestoreOption | undefined;
        // 从选项 label 反推 value（askQuestions 返回的是 label）
        const optionValue = restoreOptions.find((o) => o.label === selectedOption)?.value ?? "cancel";

        if (optionValue === "cancel") {
          ctx.addItem(addInfo(ctx.sessionId, "Rewind cancelled", "c"));
          return;
        }

        // 根据恢复选项调用不同 RPC
        if (optionValue === "both") {
          // 截断对话 + 恢复文件
          const rewindPayload = await ctx.request<RewindPayload>("session.rewind_and_restore", {
            session_id: ctx.sessionId,
            turn_index: selectedTurnIndex,
          });

          ctx.clearEntries();
          ctx.addItem(addCommandEcho(ctx.sessionId, `/rewind ${selectedTurnIndex}`));

          const restoredFiles = rewindPayload.restored_files ?? [];
          const deletedFiles = rewindPayload.deleted_files ?? [];
          const restoreErrors = rewindPayload.restore_errors ?? [];
          let fileRestoreMsg = "";
          if (restoredFiles.length > 0) {
            fileRestoreMsg += `\nRestored ${restoredFiles.length} file(s) to prior state.`;
          }
          if (deletedFiles.length > 0) {
            fileRestoreMsg += `\nDeleted ${deletedFiles.length} file(s) created after this turn.`;
          }
          if (restoreErrors.length > 0) {
            fileRestoreMsg += `\nWarning: ${restoreErrors.length} file(s) could not be restored.`;
          }

          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Rewound conversation and code: removed turn ${selectedTurnIndex} and everything after.\n` +
                `Removed ${rewindPayload.removed_records ?? 0} records, ` +
                `${rewindPayload.remaining_records ?? 0} remaining` +
                fileRestoreMsg,
              "i",
            ),
          );
          await ctx.restoreHistory(ctx.sessionId);

          const restoreText = rewindPayload.content ?? selectedTurn.content_preview;
          if (restoreText) {
            ctx.setInput?.(restoreText);
          }
        } else if (optionValue === "conversation") {
          // 仅截断对话（原有行为）
          const rewindPayload = await ctx.request<RewindPayload>("session.rewind", {
            session_id: ctx.sessionId,
            turn_index: selectedTurnIndex,
          });

          ctx.clearEntries();
          ctx.addItem(addCommandEcho(ctx.sessionId, `/rewind ${selectedTurnIndex}`));
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Rewound conversation: removed turn ${selectedTurnIndex} and everything after.\n` +
                `Removed ${rewindPayload.removed_records ?? 0} records, ` +
                `${rewindPayload.remaining_records ?? 0} remaining`,
              "i",
            ),
          );
          await ctx.restoreHistory(ctx.sessionId);

          const restoreText = rewindPayload.content ?? selectedTurn.content_preview;
          if (restoreText) {
            ctx.setInput?.(restoreText);
          }
        } else if (optionValue === "code") {
          // 仅恢复文件（不截断对话）
          const restorePayload = await ctx.request<RewindPayload>("session.restore_files", {
            session_id: ctx.sessionId,
            turn_index: selectedTurnIndex,
          });

          const restoredFiles = restorePayload.restored_files ?? [];
          const deletedFiles = restorePayload.deleted_files ?? [];
          const restoreErrors = restorePayload.restore_errors ?? [];

          let msg = `Restored files to state before turn ${selectedTurnIndex}:`;
          if (restoredFiles.length > 0) {
            msg += `\n  Written back: ${restoredFiles.length} file(s)`;
          }
          if (deletedFiles.length > 0) {
            msg += `\n  Deleted: ${deletedFiles.length} file(s)`;
          }
          if (restoreErrors.length > 0) {
            msg += `\n  Failed: ${restoreErrors.length} file(s)`;
          }
          if (restoredFiles.length === 0 && deletedFiles.length === 0) {
            msg += "\n  No file changes found to restore.";
          }

          ctx.addItem(addInfo(ctx.sessionId, msg, "i"));
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `rewind failed: ${message}`));
      }
    },
  };
}