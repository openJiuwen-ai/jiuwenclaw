/** 内置 slash 与 Gateway 受控指令对齐时参见仓库 `jiuwenswarm/gateway/slash_command.py`（SSOT）与 `docs/zh/CLI_COMMANDS.md`。 */
import type { SlashCommand } from "./types.js";
import { createBranchCommand } from "./builtins/branch.js";
import { createBtwCommand } from "./builtins/btw.js";
import { createClearCommand } from "./builtins/clear.js";
import { createColorCommand } from "./builtins/color.js";
import { createCompactCommand } from "./builtins/compact.js";
import { createConfigCommand } from "./builtins/config.js";
import { createContextCommand } from "./builtins/context.js";
import { createCronCommand } from "./builtins/cron.js";
import { createCopyCommand } from "./builtins/copy.js";
import { createRecapCommand } from "./builtins/recap.js";
import { createDiffCommand } from "./builtins/diff.js";
import { createExportCommand } from "./builtins/export.js";
import {
  createEvolveCommand,
  createEvolveListCommand,
  createEvolveRebuildCommand,
  createEvolveSimplifyCommand,
} from "./builtins/evolve.js";
import { createExitCommand } from "./builtins/exit.js";
import { createHelpCommand } from "./builtins/help.js";
import { createHooksCommand } from "./builtins/hooks.js";
import { createKeybindingsCommand } from "./builtins/keybindings.js";
import { createInitCommand } from "./builtins/init.js";
import { createModelCommand } from "./builtins/model.js";
import { createMcpCommand } from "./builtins/mcp.js";
import { createMemoryCommand } from "./builtins/memory.js";
import { createPluginCommand } from "./builtins/plugin.js";
import { createReloadPluginsCommand } from "./builtins/reload-plugins.js";
import { createModeCommand } from "./builtins/mode.js";
import { createPermissionsCommand } from "./builtins/permissions.js";
import { createPlanCommand } from "./builtins/plan.js";
import { createResumeCommand } from "./builtins/resume.js";
import { createRenameCommand } from "./builtins/rename.js";
import { createRewindCommand } from "./builtins/rewind.js";
import { createSandboxCommand } from "./builtins/sandbox.js";
import { createSessionCommand } from "./builtins/session.js";
import { createSimplifyCommand } from "./builtins/simplify.js";
import { createStatusCommand } from "./builtins/status.js";
import { createStatusLineCommand } from "./builtins/statusline.js";
import { createSkillsCommand } from "./builtins/skills.js";
import { createSwarmFlowsCommand } from "./builtins/swarmflows.js";
import { createTeamSkillsCommand } from "./builtins/teamskills.js";
import { createAgentsCommand } from "./builtins/agents.js";
import { createAutoHarnessCommand } from "./builtins/auto-harness.js";
import { createThemeCommand } from "./builtins/theme.js";
import { createWorkspaceCommand } from "./builtins/workspace-dir.js";
import { createUsageCommand } from "./builtins/usage.js";
import { createReviewCommand } from "./builtins/review.js";
import { createSecurityReviewCommand } from "./builtins/security-review.js";

export function createBuiltinCommands(): SlashCommand[] {
  const commands: SlashCommand[] = [
    createAgentsCommand(),
    createHelpCommand(() => commands),
    createHooksCommand(),
    createKeybindingsCommand(),
    createBranchCommand(),
    createBtwCommand(),
    createClearCommand(),
    createInitCommand(),
    createColorCommand(),
    createCompactCommand(),
    createConfigCommand(),
    createContextCommand(),
    createCronCommand(),
    createRecapCommand(),
    createCopyCommand(),
    createDiffCommand(),
    createExportCommand(),
    createEvolveCommand(),
    createEvolveListCommand(),
    createEvolveRebuildCommand(),
    createEvolveSimplifyCommand(),
    createExitCommand(),
    createModelCommand(),
    createMcpCommand(),
    createModeCommand(),
    createPermissionsCommand(),
    createPlanCommand(),
    createResumeCommand(),
    createRenameCommand(),
    createRewindCommand(),
    createSandboxCommand(),
    createSessionCommand(),
    createSimplifyCommand(),
    createSkillsCommand(),
    createStatusCommand(),
    createStatusLineCommand(),
    createSwarmFlowsCommand(),
    createTeamSkillsCommand(),
    createAutoHarnessCommand(),
    createThemeCommand(),
    createWorkspaceCommand(),
    createUsageCommand(),
    createReviewCommand(),
    createSecurityReviewCommand(),
    createMemoryCommand(),
    createPluginCommand(),
    createReloadPluginsCommand(),
  ];

  return commands;
}
