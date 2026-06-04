import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

type SandboxFileEntry =
  | string
  | { path: string; permissions?: string; kind?: "file" | "directory" };

type SandboxRuntime = {
  enabled?: boolean;
  excluded_commands?: string[];
  files?: { allow?: SandboxFileEntry[]; deny?: SandboxFileEntry[] };
};

type SandboxEffectiveFiles = {
  allow_write?: SandboxFileEntry[];
  deny_write?: SandboxFileEntry[];
};

type SandboxResponse = {
  runtime?: SandboxRuntime;
  excluded_commands?: string[];
  files?: { allow?: SandboxFileEntry[]; deny?: SandboxFileEntry[] };
  effective_files?: SandboxEffectiveFiles;
  jiuwenbox?: { host?: string; port?: number; ready?: boolean };
  agent_recreated?: boolean;
  rebuilt_modes?: string[];
  jiuwenbox_stopped?: boolean;
};

function tokenize(raw: string): string[] {
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  const out: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = re.exec(raw)) !== null) {
    out.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return out;
}

function formatBool(value: unknown): string {
  return value === true ? "on" : "off";
}

function formatFileEntry(entry: SandboxFileEntry): string {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") {
    const path = String(entry.path ?? "");
    const perm = entry.permissions ? ` (${entry.permissions})` : "";
    return `${path}${perm}`;
  }
  return String(entry);
}

function showRuntime(
  ctx: Parameters<SlashCommand["action"]>[0],
  runtime: SandboxRuntime | undefined,
  effective?: SandboxEffectiveFiles,
): void {
  const rt = runtime ?? {};
  const excludes = rt.excluded_commands ?? [];
  const allowWrite = effective?.allow_write ?? rt.files?.allow ?? [];
  const denyWrite = effective?.deny_write ?? rt.files?.deny ?? [];
  ctx.addItem(
    addInfo(ctx.sessionId, "Sandbox status", "s", {
      view: "kv",
      title: "Sandbox Runtime",
      items: [
        { label: "enabled", value: formatBool(rt.enabled) },
        {
          label: "excluded_commands",
          value: excludes.length ? excludes.join(", ") : "(empty)",
        },
        {
          label: "files.allow_write",
          value: allowWrite.length ? allowWrite.map(formatFileEntry).join(", ") : "(empty)",
        },
        {
          label: "files.deny_write",
          value: denyWrite.length ? denyWrite.map(formatFileEntry).join(", ") : "(empty)",
        },
      ],
    }),
  );
}

function usageText(): string {
  // The InfoMessageComponent renderer only prepends the "· " bullet to the
  // first physical line of the content, so we indent every subsequent line
  // with two spaces to keep "/sandbox …" left-aligned with line 1.
  const lines = [
    "/sandbox                              show current runtime status",
    "/sandbox enable                       enter sandbox mode (spawns jiuwenbox + recreates agent)",
    "/sandbox disable                      leave sandbox mode (recreates agent)",
    "/sandbox exclude add <pattern>        add a glob whose match runs locally instead of sandbox",
    "/sandbox exclude remove <pattern>     remove a previously added pattern",
    "/sandbox exclude list                 list current excluded_commands",
    "/sandbox files allow <path> [perm]    allow accessing <path> in sandbox (trailing / => directory)",
    "/sandbox files deny <path>            deny accessing <path> in sandbox",
    "/sandbox files remove <path>          remove the path from both allow & deny",
    "/sandbox files list                   list configured files",
  ];
  return lines.map((line, i) => (i === 0 ? line : `  ${line}`)).join("\n");
}

export function createSandboxCommand(): SlashCommand {
  return {
    name: "sandbox",
    description: "Manage jiuwenbox sandbox mode",
    usage: "/sandbox <enable|disable|exclude|files> ...",
    example: "/sandbox enable",
    kind: CommandKind.BUILT_IN,
    hidden: true,
    takesArgs: true,
    action: async (ctx, args) => {
      const raw = (args ?? "").trim();
      const tokens = tokenize(raw);
      const sub = (tokens[0] ?? "").toLowerCase();

      try {
        if (!sub || sub === "status" || sub === "show") {
          const payload = await ctx.request<SandboxResponse>("command.sandbox", { sub: "status" });
          showRuntime(ctx, payload.runtime, payload.effective_files);
          return;
        }

        if (sub === "help") {
          ctx.addItem(addInfo(ctx.sessionId, usageText(), "s"));
          return;
        }

        if (sub === "enable") {
          const payload = await ctx.request<SandboxResponse>("command.sandbox", { sub: "enable" });
          const jb = payload.jiuwenbox;
          const host = jb?.host ?? "127.0.0.1";
          const port = jb?.port ?? 8321;
          const rebuilt = payload.rebuilt_modes ?? [];
          const rebuiltText = rebuilt.length ? ` (rebuilt: ${rebuilt.join(", ")})` : "";
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Sandbox enabled (jiuwenbox @ ${host}:${port}); agent rebuilt${rebuiltText}.`,
              "s",
            ),
          );
          showRuntime(ctx, payload.runtime, payload.effective_files);
          return;
        }

        if (sub === "disable") {
          const payload = await ctx.request<SandboxResponse>("command.sandbox", { sub: "disable" });
          const rebuilt = payload.rebuilt_modes ?? [];
          const rebuiltText = rebuilt.length ? ` (rebuilt: ${rebuilt.join(", ")})` : "";
          const jb = payload.jiuwenbox;
          let jbText = "";
          if (payload.jiuwenbox_stopped) {
            const host = jb?.host ?? "127.0.0.1";
            const port = jb?.port ?? 8321;
            jbText = `; jiuwenbox stopped @ ${host}:${port}`;
          } else if (jb?.host && jb?.port) {
            jbText = `; jiuwenbox @ ${jb.host}:${jb.port} left running (external)`;
          }
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `Sandbox disabled; agent rebuilt${rebuiltText}${jbText}.`,
              "s",
            ),
          );
          showRuntime(ctx, payload.runtime, payload.effective_files);
          return;
        }

        if (sub === "exclude") {
          const op = (tokens[1] ?? "list").toLowerCase();
          if (op === "list") {
            const payload = await ctx.request<SandboxResponse>("command.sandbox", { sub: "exclude.list" });
            const patterns = payload.excluded_commands ?? [];
            ctx.addItem(
              addInfo(ctx.sessionId, `excluded_commands (${patterns.length})`, "s", {
                view: "list",
                title: "Sandbox Exclude Patterns",
                items: patterns.length
                  ? patterns.map((p, i) => ({ label: String(i + 1), value: p }))
                  : [{ label: "—", value: "(empty)" }],
              }),
            );
            return;
          }
          if (op === "add" || op === "remove") {
            const pattern = tokens.slice(2).join(" ").trim();
            if (!pattern) {
              ctx.addItem(addError(ctx.sessionId, `Missing pattern. Usage: /sandbox exclude ${op} <pattern>`));
              return;
            }
            const payload = await ctx.request<SandboxResponse>("command.sandbox", {
              sub: op === "add" ? "exclude.add" : "exclude.remove",
              pattern,
            });
            ctx.addItem(
              addInfo(
                ctx.sessionId,
                `${op === "add" ? "Added" : "Removed"} exclude pattern: ${pattern}`,
                "s",
              ),
            );
            showRuntime(ctx, payload.runtime, payload.effective_files);
            return;
          }
          ctx.addItem(addError(ctx.sessionId, "Usage: /sandbox exclude <add|remove|list> [pattern]"));
          return;
        }

        if (sub === "files") {
          const op = (tokens[1] ?? "list").toLowerCase();
          if (op === "list") {
            const payload = await ctx.request<SandboxResponse>("command.sandbox", { sub: "files.list" });
            const effective = payload.effective_files ?? {};
            const files = payload.files ?? {};
            const allowWrite = effective.allow_write ?? files.allow ?? [];
            const denyWrite = effective.deny_write ?? files.deny ?? [];
            ctx.addItem(
              addInfo(ctx.sessionId, "Sandbox files", "s", {
                view: "kv",
                title: "Sandbox Files",
                items: [
                  {
                    label: "allow_write",
                    value: allowWrite.length ? allowWrite.map(formatFileEntry).join(", ") : "(empty)",
                  },
                  {
                    label: "deny_write",
                    value: denyWrite.length ? denyWrite.map(formatFileEntry).join(", ") : "(empty)",
                  },
                ],
              }),
            );
            return;
          }
          if (op === "allow" || op === "deny" || op === "remove") {
            const path = tokens[2];
            const perm = tokens[3];
            if (!path) {
              ctx.addItem(addError(ctx.sessionId, `Missing path. Usage: /sandbox files ${op} <path> [permissions]`));
              return;
            }
            const subAction =
              op === "allow" ? "files.allow" : op === "deny" ? "files.deny" : "files.remove";
            const params: Record<string, unknown> = { sub: subAction, path };
            if (perm) params.permissions = perm;
            const payload = await ctx.request<SandboxResponse>("command.sandbox", params);
            ctx.addItem(
              addInfo(
                ctx.sessionId,
                `${op === "allow" ? "Allowed" : op === "deny" ? "Denied" : "Removed"}: ${path}`,
                "s",
              ),
            );
            showRuntime(ctx, payload.runtime, payload.effective_files);
            return;
          }
          ctx.addItem(addError(ctx.sessionId, "Usage: /sandbox files <allow|deny|remove|list> [path]"));
          return;
        }

        ctx.addItem(addError(ctx.sessionId, `Unknown sub-command: ${sub}\n${usageText()}`));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `sandbox failed: ${message}`));
      }
    },
  };
}
