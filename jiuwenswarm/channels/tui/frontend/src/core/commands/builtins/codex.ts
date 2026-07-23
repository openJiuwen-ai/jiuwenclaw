import { copyToClipboard as copyToSystemClipboard } from "../clipboard.js";
import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

type CodexAuthStatus = {
  enabled: boolean;
  available?: boolean;
  connected: boolean;
  state: string;
  operation_id?: string;
  verification_url?: string;
  user_code?: string;
  error_code?: string;
};

type CodexCommandDependencies = {
  copyToClipboard?: (text: string) => Promise<boolean>;
};

function statusItems(status: CodexAuthStatus): Array<{ label: string; value: string }> {
  return [
    { label: "enabled", value: String(status.enabled) },
    { label: "available", value: String(status.available) },
    { label: "connected", value: String(status.connected) },
    { label: "state", value: status.state },
    ...(status.error_code ? [{ label: "error", value: status.error_code }] : []),
  ];
}

export function createCodexCommand({
  copyToClipboard = copyToSystemClipboard,
}: CodexCommandDependencies = {}): SlashCommand {
  let activeOperationId: string | undefined;

  return {
    name: "codex",
    description: "Connect or inspect the instance-scoped Codex subscription provider",
    usage: "/codex [status|connect|cancel|logout]",
    example: "/codex connect",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const action = args.trim().toLowerCase() || "status";
      try {
        if (action === "connect") {
          const status = await ctx.request<CodexAuthStatus>(
            "provider.codex.auth.start",
            {},
            45_000,
          );
          activeOperationId = status.operation_id;
          if (status.connected) {
            ctx.addItem(
              addInfo(ctx.sessionId, "Codex is already connected for this Jiuwen instance.", "c"),
            );
            return;
          }
          if (!status.user_code || !status.verification_url) {
            if (activeOperationId) {
              try {
                await ctx.request("provider.codex.auth.cancel", {
                  operation_id: activeOperationId,
                });
              } finally {
                activeOperationId = undefined;
              }
            }
            ctx.addItem(addError(ctx.sessionId, "Codex did not return a device login handoff."));
            return;
          }
          const copied = await copyToClipboard(status.user_code);
          if (!copied) {
            try {
              if (activeOperationId) {
                await ctx.request("provider.codex.auth.cancel", {
                  operation_id: activeOperationId,
                });
              }
            } finally {
              activeOperationId = undefined;
            }
            ctx.addItem(
              addError(
                ctx.sessionId,
                "The device code could not be copied securely, so the login was canceled. Use the local dashboard to connect Codex.",
              ),
            );
            return;
          }
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Open ${status.verification_url}. The device code was copied to your clipboard and is intentionally hidden from chat history. Run /codex status after approving.`,
              "c",
              {
                view: "kv",
                title: "Codex subscription login",
                items: [
                  { label: "sign-in page", value: status.verification_url },
                  { label: "device code", value: "copied to clipboard (hidden)" },
                  { label: "state", value: status.state },
                ],
              },
            ),
          );
          return;
        }
        if (action === "cancel") {
          if (!activeOperationId) {
            const current = await ctx.request<CodexAuthStatus>("provider.codex.auth.status", {});
            activeOperationId = current.operation_id;
          }
          if (!activeOperationId) {
            ctx.addItem(addInfo(ctx.sessionId, "No Codex login is active.", "c"));
            return;
          }
          const status = await ctx.request<CodexAuthStatus>("provider.codex.auth.cancel", {
            operation_id: activeOperationId,
          });
          activeOperationId = undefined;
          ctx.addItem(
            addInfo(ctx.sessionId, "Codex login canceled.", "c", {
              view: "kv",
              items: statusItems(status),
            }),
          );
          return;
        }
        if (action === "logout" || action === "disconnect") {
          const status = await ctx.request<CodexAuthStatus>("provider.codex.auth.logout", {});
          activeOperationId = undefined;
          ctx.addItem(
            addInfo(ctx.sessionId, "Codex disconnected for this Jiuwen instance.", "c", {
              view: "kv",
              items: statusItems(status),
            }),
          );
          return;
        }
        if (action !== "status") {
          ctx.addItem(addError(ctx.sessionId, "Usage: /codex [status|connect|cancel|logout]"));
          return;
        }
        const status = await ctx.request<CodexAuthStatus>("provider.codex.auth.status", {});
        activeOperationId = status.operation_id;
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            status.connected
              ? "Codex is connected for this Jiuwen instance."
              : `Codex state: ${status.state}`,
            "c",
            { view: "kv", title: "Codex subscription", items: statusItems(status) },
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `Codex authentication failed: ${message}`));
      }
    },
  };
}
