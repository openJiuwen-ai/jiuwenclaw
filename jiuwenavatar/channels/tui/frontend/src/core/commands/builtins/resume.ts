import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import type { AccentColorName } from "../../../ui/theme.js";

export interface SessionMeta {
  session_id: string;
  title?: string;
  accent_color?: AccentColorName;
  channel_id?: string;
  created_at?: number;
  last_message_at?: number;
  message_count?: number;
}

export interface SessionListPayload {
  sessions?: SessionMeta[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface ResumeResumePayload {
  session_id?: string;
  query?: string;
  resumed?: boolean;
  preview?: string;
}

export function createResumeCommand(): SlashCommand {
  return {
    name: "resume",
    altNames: ["continue"],
    description: "Resume a previous conversation, or list sessions with /resume",
    usage: "/resume [list | conversation id or search term]",
    example: "/resume",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const value = args.trim();
      try {
        // 获取 session 列表用于匹配
        const listPayload = await ctx.request<SessionListPayload>("session.list", {});
        const allSessions = listPayload.sessions ?? [];

        if (value === "" || value === "list") {
          const total = listPayload.total ?? allSessions.length;
          if (allSessions.length === 0) {
            ctx.addItem(addInfo(ctx.sessionId, "No sessions found", "r"));
            return;
          }
          const items = allSessions.map((s, i) => {
            const lastActive = s.last_message_at
              ? new Date(s.last_message_at * 1000).toLocaleString()
              : "-";
            const title = s.title || "-";
            return {
              label: String(i + 1),
              value: `${s.session_id}  |  ${title}  |  msgs: ${s.message_count ?? 0}  |  ${lastActive}`,
            };
          });
          ctx.addItem(
            addInfo(ctx.sessionId, `Sessions (${total} total)`, "r", {
              view: "list",
              title: "Resume Sessions",
              items,
            }),
          );
          return;
        }

        // 1. 先检查是否完全匹配 session_id
        const sessionIdMatch = allSessions.find(
          (s) => s.session_id === value || s.session_id.startsWith(value) && value.length >= 8,
        );
        if (sessionIdMatch) {
          const nextSessionId = sessionIdMatch.session_id;
          ctx.updateSession(nextSessionId);
          ctx.clearEntries();
          ctx.setAccentColor(sessionIdMatch.accent_color || "default");
          ctx.addItem(addInfo(nextSessionId, `Resumed session ${nextSessionId}`, "r"));
          void ctx.restoreHistory(nextSessionId);
          void (async () => {
            try {
              const meta = await ctx.request<{ session_id: string; title: string }>(
                "session.rename",
                { session_id: nextSessionId },
              );
              ctx.setSessionTitle(meta.title || "");
            } catch {
              ctx.setSessionTitle("");
            }
          })();
          return;
        }

        // 2. 检查是否完全匹配 title（显示内容，title 或 fallback 的 session_id）
        const titleMatches = allSessions.filter((s) => {
          const displayLabel = s.title?.trim() || s.session_id;
          return displayLabel === value;
        });

        if (titleMatches.length === 1) {
          const nextSessionId = titleMatches[0]!.session_id;
          ctx.updateSession(nextSessionId);
          ctx.clearEntries();
          ctx.setAccentColor(titleMatches[0].accent_color || "default");
          ctx.addItem(addInfo(nextSessionId, `Resumed session ${nextSessionId}`, "r"));
          void ctx.restoreHistory(nextSessionId);
          void (async () => {
            try {
              const meta = await ctx.request<{ session_id: string; title: string }>(
                "session.rename",
                { session_id: nextSessionId },
              );
              ctx.setSessionTitle(meta.title || "");
            } catch {
              ctx.setSessionTitle("");
            }
          })();
          return;
        }

        // 3. 多个 title 匹配
        if (titleMatches.length > 1) {
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Found ${titleMatches.length} sessions matching "${value}". Please use /resume to pick a specific session.`,
              "r",
            ),
          );
          return;
        }

        // 4. 没有匹配
        ctx.addItem(
          addInfo(ctx.sessionId, `Session "${value}" was not found. Use /resume to see available sessions.`, "r"),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `resume failed: ${message}`));
      }
    },
  };
}
