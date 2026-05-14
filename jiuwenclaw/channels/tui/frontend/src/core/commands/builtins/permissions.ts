import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

const VALID_LEVELS = new Set(["allow", "ask", "deny"]);

const LEVEL_TO_SEVERITY: Record<string, string> = {
  allow: "LOW",
  ask: "HIGH",
  deny: "CRITICAL",
};

const RULE_MATCH_RE = /^(\S+?)\((.+)\)$/;

function formatToolsSection(tools: Record<string, string>): string {
  const entries = Object.entries(tools);
  if (entries.length === 0) return "  (无)";
  const maxLen = Math.max(...entries.map(([k]) => k.length));
  return entries.map(([k, v]) => `  ${k.padEnd(maxLen)}  →  ${v}`).join("\n");
}

function formatRulesSection(rules: Array<Record<string, unknown>>): string {
  if (rules.length === 0) return "  (无)";
  return rules
    .map((r) => {
      const id = String(r.id || "?");
      const tools = Array.isArray(r.tools) ? r.tools.join(",") : String(r.tools || "");
      const pattern = String(r.pattern || "");
      const severity = String(r.severity || r.action || "");
      return `  [${id}]  ${tools}  pattern: ${pattern}  severity: ${severity}`;
    })
    .join("\n");
}

export function createPermissionsCommand(): SlashCommand {
  return {
    name: "permissions",
    description: "View or set permission rules (tools & rules)",
    usage: "/permissions [allow|ask|deny] <tool_name | tool(pattern)>",
    example: "/permissions ask write_file\n/permissions allow bash(ls *)",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const raw = args.trim();

      if (!raw) {
        try {
          const toolsResp = await ctx.request<Record<string, unknown>>(
            "permissions.tools.get",
            {},
            60_000,
          );
          const rulesResp = await ctx.request<Record<string, unknown>>(
            "permissions.rules.get",
            {},
            60_000,
          );

          const tools = (toolsResp?.tools ?? toolsResp) as Record<string, string>;
          const rules = ((rulesResp?.rules ?? rulesResp) as Array<Record<string, unknown>>) || [];

          const toolsSection =
            typeof tools === "object" && tools !== null ? formatToolsSection(tools) : "  (无法读取)";
          const rulesSection = Array.isArray(rules) ? formatRulesSection(rules) : "  (无法读取)";

          ctx.addItem(
            addInfo(ctx.sessionId, `── 工具权限 ──\n${toolsSection}\n\n── 规则列表 ──\n${rulesSection}`, "c"),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          ctx.addItem(addError(ctx.sessionId, `获取权限规则失败：${message}`));
        }
        return;
      }

      const parts = raw.split(/\s+/);
      const level = parts[0].toLowerCase();
      const rest = parts.slice(1).join(" ").trim();

      if (!VALID_LEVELS.has(level)) {
        ctx.addItem(
          addError(ctx.sessionId, `无效级别 "${parts[0]}"，仅允许：allow、ask、deny`),
        );
        return;
      }
      if (!rest) {
        ctx.addItem(addError(ctx.sessionId, "工具名不能为空。"));
        return;
      }

      const ruleMatch = rest.match(RULE_MATCH_RE);
      if (ruleMatch) {
        const [, toolRaw, pattern] = ruleMatch;
        const tool = toolRaw.toLowerCase();
        const severity = LEVEL_TO_SEVERITY[level] || "HIGH";
        const ruleId = `cli_rule_${tool}_${pattern.replace(/[^a-zA-Z0-9]/g, "_")}`.toLowerCase();

        try {
          await ctx.request<Record<string, unknown>>(
            "permissions.rules.create",
            {
              rule: {
                id: ruleId,
                tools: [tool],
                pattern,
                severity,
              },
            },
            60_000,
          );
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              `已创建规则 [${ruleId}]  tools: ${tool}  pattern: ${pattern}  severity: ${severity}`,
              "i",
            ),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (message.includes("already exists")) {
            try {
              await ctx.request<Record<string, unknown>>(
                "permissions.rules.update",
                {
                  id: ruleId,
                  patch: { tools: [tool], pattern, severity },
                },
                60_000,
              );
              ctx.addItem(
                addInfo(
                  ctx.sessionId,
                  `已更新规则 [${ruleId}]  tools: ${tool}  pattern: ${pattern}  severity: ${severity}`,
                  "i",
                ),
              );
            } catch (updateError) {
              const updateMessage = updateError instanceof Error ? updateError.message : String(updateError);
              ctx.addItem(addError(ctx.sessionId, `permissions.rules.update 失败：${updateMessage}`));
            }
          } else {
            ctx.addItem(addError(ctx.sessionId, `permissions.rules.create 失败：${message}`));
          }
        }
      } else {
        try {
          await ctx.request<Record<string, unknown>>(
            "permissions.tools.update",
            { tool: rest.toLowerCase(), level },
            60_000,
          );
          ctx.addItem(
            addInfo(ctx.sessionId, `已设置 permissions.tools.${rest.toLowerCase()} = ${level}`, "i"),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          ctx.addItem(addError(ctx.sessionId, `permissions.tools.update 失败：${message}`));
        }
      }
    },
  };
}
