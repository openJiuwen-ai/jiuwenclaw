import { CommandKind, type SlashCommand, type SlashCommandListProvider } from "../types.js";
import { makeItem } from "../helpers.js";
import type { InactiveUserCommand } from "../user-commands.js";

const COMMAND_GROUPS: Record<string, { name: string; commands: string[] }> = {
  core: {
    name: "Core",
    commands: ["help", "clear", "exit", "init", "simplify", "copy", "export", "review", "security-review"],
  },
  session: {
    name: "Session",
    commands: ["resume", "rename", "session", "compact", "sessions", "new", "recap"],
  },
  model: {
    name: "Model",
    commands: ["model", "theme", "color"],
  },
  mcp: {
    name: "MCP",
    commands: ["mcp"],
  },
  skills: {
    name: "Skills",
    commands: ["skills", "teamskills"],
  },
  config: {
    name: "Config",
    commands: ["config", "workspace", "diff", "plan", "permissions"],
  },
};

/** 获取命令所属分组 */
function getCommandGroup(commandName: string): string | null {
  for (const [groupKey, group] of Object.entries(COMMAND_GROUPS)) {
    if (group.commands.includes(commandName)) {
      return groupKey;
    }
  }
  return null;
}

export function createHelpCommand(
  getCommands: SlashCommandListProvider,
  getInactiveUserCommands?: () => readonly InactiveUserCommand[],
): SlashCommand {
  return {
    name: "help",
    description: "Show available commands",
    usage: "/help",
    example: "/help",
    kind: CommandKind.BUILT_IN,
    action: (ctx) => {
      const commands = getCommands().filter((command) => !command.hidden);

      const groupedCommands: Record<string, Array<{ label: string; value?: string; description: string }>> = {};
      const ungroupedCommands: Array<{ label: string; value?: string; description: string }> = [];
      // User-defined commands get their own group rather than falling into
      // "Other": which of these came from a file the user can edit is the first
      // thing they need to know, and it is not derivable from the name.
      const userCommands: Array<{ label: string; value?: string; description: string }> = [];

      for (const command of commands) {
        const item = {
          label: `/${command.name}`,
          value: command.usage?.replace(/^\/[^\s]+/, "").trim() || undefined,
          description: command.description,
        };

        if (command.kind === CommandKind.USER) {
          userCommands.push(item);
          continue;
        }

        const groupKey = getCommandGroup(command.name);
        if (groupKey) {
          if (!groupedCommands[groupKey]) {
            groupedCommands[groupKey] = [];
          }
          groupedCommands[groupKey].push(item);
        } else {
          ungroupedCommands.push(item);
        }
      }

      const groups = Object.keys(COMMAND_GROUPS)
        .filter((key) => groupedCommands[key]?.length > 0)
        .map((key) => ({
          name: COMMAND_GROUPS[key].name,
          items: groupedCommands[key],
        }));

      if (ungroupedCommands.length > 0) {
        groups.push({
          name: "Other",
          items: ungroupedCommands,
        });
      }

      if (userCommands.length > 0) {
        groups.push({
          name: "User-defined",
          items: userCommands,
        });
      }

      // A file the user wrote that does nothing is an unanswerable support
      // question. Listing it with the reason is the whole point of the server
      // reporting losers instead of dropping them.
      const inactive = getInactiveUserCommands?.() ?? [];
      if (inactive.length > 0) {
        groups.push({
          name: "User-defined (not loaded)",
          items: inactive.map((entry) => ({
            label: `/${entry.name}`,
            description: `${entry.reason} — ${entry.filePath}`,
          })),
        });
      }

      ctx.addItem(
        makeItem(ctx.sessionId, "info", "Available commands", "?", {
          view: "help",
          title: "Slash Commands",
          groups,
          version: ctx.version,
        }),
      );
    },
  };
}