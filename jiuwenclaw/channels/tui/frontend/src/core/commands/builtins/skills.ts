import { flattenArrayPayload, formatValue, makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

type MarketPlaceItem = {
  name: string;
  url: string;
  enabled: boolean;
  install_location?: string | null;
  last_updated?: string | null;
};

async function listMarketplaces(ctx: import("../types.js").CommandContext): Promise<void> {
  const payload = await ctx.request<{ marketplaces?: MarketPlaceItem[] }>(
    "skills.marketplace.list",
    {},
  );
  const items = (payload.marketplaces || []).map((m) => ({
    label: m.name,
    value: m.url,
    description: `${m.enabled ? "enabled" : "disabled"} | ${m.last_updated || "never updated"}`,
  }));
  ctx.addItem(
    makeItem(
      ctx.sessionId,
      "info",
      items.length > 0 ? "Marketplace sources" : "No marketplace sources",
      "*",
      { view: "list", title: "Marketplaces", items },
    ),
  );
}

async function listSkills(ctx: import("../types.js").CommandContext): Promise<void> {
  const payload = await ctx.request("skills.list", {});
  const allSkills = flattenArrayPayload(payload);

  const installed: { label: string; value?: string; description: string }[] = [];
  const available: { label: string; value?: string; description: string }[] = [];

  for (const item of allSkills) {
    if (item && typeof item === "object") {
      const obj = item as Record<string, unknown>;
      const name = typeof obj.name === "string" ? obj.name : "?";
      const sourceTag = obj.is_builtin_source ? "[builtin]" : (obj.source === "local" ? "[local]" : `[${obj.source || "project"}]`);
      const desc = typeof obj.description === "string" ? obj.description : "";
      const entry = {
        label: name,
        value: typeof obj.path === "string" ? obj.path : undefined,
        description: `${sourceTag} ${desc}`,
      };
      if (obj.installed === true) {
        installed.push(entry);
      } else {
        available.push(entry);
      }
    }
  }

  // Group 1: Installed skills
  ctx.addItem(
    makeItem(
      ctx.sessionId,
      "info",
      installed.length > 0 ? `Installed (${installed.length})` : "No installed skills",
      "*",
      { view: "list", title: "Installed Skills", items: installed },
    ),
  );

  // Group 2: Available skills (not yet installed)
  if (available.length > 0) {
    ctx.addItem(
      makeItem(
        ctx.sessionId,
        "info",
        `Available to install (${available.length})`,
        "*",
        { view: "list", title: "Available Skills", items: available },
      ),
    );
  }
}

export function createSkillsCommand(): SlashCommand {
  return {
    name: "skills",
    description: "Manage skills (list, install, uninstall, marketplace, use)",
    usage: "/skills [list|install|uninstall|marketplace|use]",
    example: "/skills install my-skill  |  /skills install /path/to/skill",
    kind: CommandKind.BUILT_IN,
    action: async (ctx) => {
      await listSkills(ctx);
    },
    subCommands: [
      {
        name: "list",
        description: "List skills",
        usage: "/skills list",
        example: "/skills list",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          await listSkills(ctx);
        },
      },
      {
        name: "install",
        description: "Install a skill (builtin name, plugin@marketplace, or local path/URL)",
        usage: "/skills install <skill> | <skill@marketplace> | <path_or_url>",
        example: "/skills install my-skill  |  /skills install /path/to/skill",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const spec = args.trim();
          if (!spec) {
            ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills install <skill> | <skill@marketplace> | <path_or_url>"));
            return;
          }

          // Detect local path / URL — delegate to import_local
          const isLocalPath = /^([A-Za-z]:[\\/]|\/|\.\/|\.\.\/)/.test(spec);
          const isUrl = /^https?:\/\/.+/i.test(spec);
          if (isLocalPath || isUrl) {
            ctx.addItem(makeItem(ctx.sessionId, "info", `Importing skill from: ${spec}`));
            const payload = await ctx.request<{ success?: boolean; detail?: string; skill?: { name?: string } }>(
              "skills.import_local",
              { path: spec, force: false },
              120_000,
            );
            if (payload.success) {
              const skillName = payload.skill?.name || spec;
              ctx.addItem(makeItem(ctx.sessionId, "info", `Skill imported: ${skillName}`));
            } else {
              ctx.addItem(
                makeItem(ctx.sessionId, "error", payload.detail || `Import failed: ${spec}`),
              );
            }
            return;
          }

          // Marketplace / builtin install flow
          let finalSpec = spec;
          // Bare skill name without @ — auto-detect if it's a builtin
          if (!spec.includes("@")) {
            try {
              const payload = await ctx.request("skills.list", {});
              const skills = flattenArrayPayload(payload);
              const builtinMatch = skills.find((item) => {
                if (item && typeof item === "object") {
                  const obj = item as Record<string, unknown>;
                  return obj.name === spec
                    && (obj.is_builtin_source === true || obj.is_builtin === true);
                }
                return false;
              });
              if (builtinMatch) {
                finalSpec = `${spec}@builtin`;
              }
            } catch {
              // If list fails, let backend handle the bare name
            }
          }
          const payload = await ctx.request<{ success?: boolean; detail?: string }>(
            "skills.install",
            { spec: finalSpec, force: false },
            120_000,
          );
          if (payload.success) {
            ctx.addItem(makeItem(ctx.sessionId, "info", `Skill installed: ${finalSpec}`));
          } else {
            ctx.addItem(
              makeItem(ctx.sessionId, "error", payload.detail || `Install failed: ${finalSpec}`),
            );
          }
        },
      },
      {
        name: "uninstall",
        description: "Uninstall a skill",
        usage: "/skills uninstall <name>",
        example: "/skills uninstall my-skill",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const name = args.trim();
          if (!name) {
            ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills uninstall <name>"));
            return;
          }
          const payload = await ctx.request<{ success?: boolean; detail?: string }>(
            "skills.uninstall",
            { name },
            120_000,
          );
          if (payload.success) {
            ctx.addItem(makeItem(ctx.sessionId, "info", `Skill uninstalled: ${name}`));
          } else {
            ctx.addItem(
              makeItem(ctx.sessionId, "error", payload.detail || `Uninstall failed: ${name}`),
            );
          }
        },
      },
      {
        name: "marketplace",
        description: "Manage marketplace sources",
        usage: "/skills marketplace [list|add|remove|toggle]",
        example: "/skills marketplace list",
        kind: CommandKind.BUILT_IN,
        action: async (ctx) => {
          await listMarketplaces(ctx);
        },
        subCommands: [
          {
            name: "list",
            description: "List marketplace sources",
            usage: "/skills marketplace list",
            example: "/skills marketplace list",
            kind: CommandKind.BUILT_IN,
            takesArgs: false,
            action: async (ctx) => {
              await listMarketplaces(ctx);
            },
          },
          {
            name: "add",
            description: "Add a marketplace source",
            usage: "/skills marketplace add <name> <url>",
            example: "/skills marketplace add my-repo https://github.com/user/skills",
            kind: CommandKind.BUILT_IN,
            takesArgs: true,
            action: async (ctx, args) => {
              const parts = args.trim().split(/\s+/);
              const name = parts[0];
              const url = parts.slice(1).join(" ");
              if (!name || !url) {
                ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills marketplace add <name> <url>"));
                return;
              }
              const payload = await ctx.request<{ success?: boolean; detail?: string }>(
                "skills.marketplace.add",
                { name, url },
              );
              if (payload.success) {
                ctx.addItem(makeItem(ctx.sessionId, "info", `Marketplace added: ${name}`));
                await listMarketplaces(ctx);
              } else {
                ctx.addItem(makeItem(ctx.sessionId, "error", payload.detail || `Add failed: ${name}`));
              }
            },
          },
          {
            name: "remove",
            description: "Remove a marketplace source",
            usage: "/skills marketplace remove <name>",
            example: "/skills marketplace remove my-repo",
            kind: CommandKind.BUILT_IN,
            takesArgs: true,
            action: async (ctx, args) => {
              const name = args.trim();
              if (!name) {
                ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills marketplace remove <name>"));
                return;
              }
              const payload = await ctx.request<{ success?: boolean; detail?: string }>(
                "skills.marketplace.remove",
                { name, remove_cache: true },
              );
              if (payload.success) {
                ctx.addItem(makeItem(ctx.sessionId, "info", `Marketplace removed: ${name}`));
                await listMarketplaces(ctx);
              } else {
                ctx.addItem(makeItem(ctx.sessionId, "error", payload.detail || `Remove failed: ${name}`));
              }
            },
          },
          {
            name: "toggle",
            description: "Enable/disable a marketplace source",
            usage: "/skills marketplace toggle <name> <on|off>",
            example: "/skills marketplace toggle my-repo on",
            kind: CommandKind.BUILT_IN,
            takesArgs: true,
            action: async (ctx, args) => {
              const parts = args.trim().split(/\s+/);
              const name = parts[0];
              const enabledStr = parts[1]?.toLowerCase();
              if (!name || !enabledStr) {
                ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills marketplace toggle <name> <on|off>"));
                return;
              }
              const enabled = enabledStr === "on" || enabledStr === "true" || enabledStr === "1";
              const payload = await ctx.request<{ success?: boolean; detail?: string }>(
                "skills.marketplace.toggle",
                { name, enabled },
                120_000,
              );
              if (payload.success) {
                ctx.addItem(makeItem(ctx.sessionId, "info", `Marketplace ${name}: ${enabled ? "enabled" : "disabled"}`));
                await listMarketplaces(ctx);
              } else {
                ctx.addItem(makeItem(ctx.sessionId, "error", payload.detail || `Toggle failed: ${name}`));
              }
            },
          },
        ],
      },
      {
        name: "use",
        description: "Use a skill to execute a query",
        usage: "/skills use <skill_name>, <query>",
        example: "/skills use my-skill, Code and execute a Hello World program.",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        completionSuffix: ", ",
        completion: async (ctx, partial) => {
          const commaIndex = partial.indexOf(",");
          if (commaIndex !== -1 && partial.slice(0, commaIndex).trim()) return [];
          const filterTerm = commaIndex !== -1 ? "" : partial;
          try {
            const payload = await ctx.request("skills.list", {});
            const skills = flattenArrayPayload(payload)
              .map((item) => {
                if (item && typeof item === "object") {
                  const obj = item as Record<string, unknown>;
                  return typeof obj.name === "string" ? obj.name : null;
                }
                return null;
              })
              .filter((name): name is string => name !== null);
            if (!filterTerm) return skills;
            const lower = filterTerm.toLowerCase();
            return skills.filter((name) => name.toLowerCase().includes(lower));
          } catch {
            return [];
          }
        },
        action: async (ctx, args) => {
          const parts = args.trim().split(/\s*,\s*(.*)/);
          const skill_name = parts[0];
          const query = parts[1];
          if (!skill_name || !query) {
            ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /skills use <skill_name>, <query>"));
            return;
          }
          const text = `/skills use ${skill_name}, ${query}`

          const requestId = ctx.sendMessage(text)
          if (!requestId) {
            ctx.addItem(
              makeItem(ctx.sessionId, "error", "offline: waiting for reconnect before sending /skills use request"),
            );
            return;
          }
        },
      },
    ],
  };
}
