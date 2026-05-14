import { addError, addInfo } from "../helpers.js";
import { CommandKind, type CommandContext, type SlashCommand } from "../types.js";

function showAllTrustedPaths(ctx: CommandContext): void {
  const trustedDirs = ctx.getTrustedDirs();

  const items: Array<{ label: string; value: string }> = [];

  // Show system default workspace (fixed)
  items.push({
    label: "workspace (system)",
    value: "~/.jiuwenclaw/agent/jiuwenclaw_workspace",
  });

  // Show trusted directories
  if (trustedDirs.length > 0) {
    trustedDirs.forEach((dir, index) => {
      items.push({
        label: `trusted[${index}]`,
        value: dir,
      });
    });
  } else {
    items.push({
      label: "trusted",
      value: "(none - using workspace only)",
    });
  }

  ctx.addItem(
    addInfo(ctx.sessionId, "Trusted paths for file operations", "c", {
      view: "kv",
      title: "Trusted Paths",
      items,
    }),
  );
}

/**
 * Ask user for Yes/No confirmation.
 * @returns true if user selected Yes, false if No or cancelled
 */
async function askYesNo(
  ctx: CommandContext,
  header: string,
  question: string,
  yesLabel: string,
  noLabel: string,
): Promise<boolean> {
  const answers = await ctx.askQuestions(
    [
      {
        header,
        question,
        options: [{ label: yesLabel }, { label: noLabel }],
      },
    ],
    "local_command",
  );
  const answer = answers[0];
  if (!answer) return false;
  const selected = answer.selected_options[0] ?? "";
  return selected === yesLabel;
}

export function createWorkspaceCommand(): SlashCommand {
  return {
    name: "workspace",
    altNames: ["workspace_dir", "workspace-dir"],
    description: "Manage trusted directories for file operations",
    usage: "/workspace [get|add <path>|set <path>|remove <path>|clear]",
    example: "/workspace add ./",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    subCommands: [
      {
        name: "get",
        description: "Show all trusted paths (workspace + trusted directories)",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          showAllTrustedPaths(ctx);
        },
      },
      {
        name: "add",
        description: "Add a trusted directory (cwd by default)",
        usage: "/workspace add [path]",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const directoryPath = args.trim();
          // Default to cwd if no path specified
          const resolvedPath = directoryPath || process.cwd();
          if (!resolvedPath) {
            ctx.addItem(addError(ctx.sessionId, "usage: /workspace add [path]"));
            return;
          }
          const result = ctx.addTrustedDir(resolvedPath);
          if (result === "added") {
            // Sync to server-side permissions
            try {
              ctx.sendEventOnly("command.add_dir", {
              path: resolvedPath,
              remember: true
            });
            } catch (error) {
              // Ignore sync errors, still add locally
              console.warn("Failed to sync trusted directory to server:", error);
            }
            ctx.addItem(
              addInfo(ctx.sessionId, `Trusted directory added: ${resolvedPath}`, "c", {
                view: "kv",
                title: "Add Trusted Dir",
                items: [{ label: "path", value: resolvedPath }],
              }),
            );
          } else if (result === "exists") {
            ctx.addItem(addInfo(ctx.sessionId, `Path already set as trusted dir: ${resolvedPath}`, "c"));
          } else if (result === "not_found") {
            ctx.addItem(addError(ctx.sessionId, `Path does not exist: ${resolvedPath}`));
          } else if (result === "no_access") {
            ctx.addItem(addError(ctx.sessionId, `Permission denied: cannot access directory ${resolvedPath}`));
          } else {
            ctx.addItem(addError(ctx.sessionId, `Path is not a directory: ${resolvedPath}`));
          }
        },
      },
      {
        name: "set",
        description: "Reset trusted dirs and set a single path",
        usage: "/workspace set <path>",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const directoryPath = args.trim();
          if (!directoryPath) {
            ctx.addItem(addError(ctx.sessionId, "usage: /workspace set <path>"));
            return;
          }

          // Validate path without modifying state
          const result = ctx.validateDirPath(directoryPath);
          if (result === "not_found") {
            ctx.addItem(addError(ctx.sessionId, `Path does not exist: ${directoryPath}`));
            return;
          }
          if (result === "invalid") {
            ctx.addItem(addError(ctx.sessionId, `Path is not a directory: ${directoryPath}`));
            return;
          }
          if (result === "no_access") {
            ctx.addItem(addError(ctx.sessionId, `Permission denied: cannot access directory ${directoryPath}`));
            return;
          }

          // If trusted dirs already has content, ask for confirmation
          const currentDirs = ctx.getTrustedDirs();
          if (currentDirs.length > 0) {
            const confirmed = await askYesNo(
              ctx,
              "Confirm Reset",
              `This will clear all existing trusted directories and set "${directoryPath}" as the only trusted path.\nCurrent trusted dirs will be removed: ${currentDirs.join(", ")}`,
              "Yes, reset and set",
              "No, keep current",
            );
            if (!confirmed) {
              ctx.addItem(addInfo(ctx.sessionId, "Operation cancelled. Current trusted dirs unchanged.", "c"));
              return;
            }
          }

          // Execute the set operation
          ctx.setTrustedDir(directoryPath);
          // Sync to server-side permissions
          try {
            ctx.sendEventOnly("command.add_dir", {
              path: directoryPath,
              remember: true
            });
          } catch (error) {
            // Ignore sync errors, still set locally
            console.warn("Failed to sync trusted directory to server:", error);
          }
          ctx.addItem(
            addInfo(ctx.sessionId, `Trusted directory set: ${directoryPath}`, "c", {
              view: "kv",
              title: "Set Trusted Dir",
              items: [{ label: "path", value: directoryPath }],
            }),
          );
        },
      },
      {
        name: "remove",
        description: "Remove a trusted directory",
        usage: "/workspace remove <path>",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const directoryPath = args.trim();
          if (!directoryPath) {
            ctx.addItem(addError(ctx.sessionId, "usage: /workspace remove <path>"));
            return;
          }
          const removed = ctx.removeTrustedDir(directoryPath);
          if (removed) {
            ctx.addItem(addInfo(ctx.sessionId, `Trusted directory removed: ${directoryPath}`, "c"));
          } else {
            ctx.addItem(addInfo(ctx.sessionId, `Path not in trusted dirs: ${directoryPath}`, "c"));
          }
        },
      },
      {
        name: "clear",
        description: "Clear all trusted directories (will use default workspace only)",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        action: async (ctx) => {
          ctx.clearTrustedDirs();
          ctx.addItem(addInfo(ctx.sessionId, "Trusted directories cleared. Using default workspace only.", "c"));
        },
      },
    ],
    action: async (ctx, args) => {
      if (!args.trim()) {
        showAllTrustedPaths(ctx);
        return;
      }
      ctx.addItem(
        addError(
          ctx.sessionId,
          "usage: /workspace [get|add [path]|set <path>|remove <path>|clear] — use subcommands",
        ),
      );
    },
  };
}