import type {
  CommandContext,
  CommandSuggestion,
  SlashCommand,
  UserCommandDefinition,
} from "./types.js";
import { flattenArrayPayload, makeItem, parseArgs } from "./helpers.js";
import {
  buildUserCommands,
  type InactiveUserCommand,
} from "./user-commands.js";

export function parseSlashCommand(raw: string, commands: readonly SlashCommand[]) {
  const trimmed = raw.trim();
  const parts = trimmed.substring(1).trim().split(/\s+/).filter(Boolean);
  let currentCommands = commands;
  let command: SlashCommand | undefined;
  let parentCommand: SlashCommand | undefined;
  let pathIndex = 0;
  const canonicalPath: string[] = [];

  for (const part of parts) {
    const lower = part.toLowerCase();
    let found = currentCommands.find((candidate) => candidate.name.toLowerCase() === lower);
    if (!found) {
      found = currentCommands.find((candidate) => candidate.altNames?.some((alt) => alt.toLowerCase() === lower));
    }
    if (!found) break;
    parentCommand = command;
    command = found;
    canonicalPath.push(found.name);
    pathIndex += 1;
    if (found.subCommands) {
      currentCommands = found.subCommands;
    } else {
      break;
    }
  }

  const args = parts.slice(pathIndex).join(" ");
  if (command && command.takesArgs === false && args.length > 0 && parentCommand) {
    return {
      name: parentCommand.name,
      args: parts.slice(pathIndex - 1).join(" "),
      canonicalPath: canonicalPath.slice(0, -1),
      command: parentCommand,
    };
  }

  return {
    name: command?.name ?? parts[0] ?? "",
    args,
    canonicalPath,
    command,
  };
}

export interface InstalledSkillEntry {
  name: string;
  description: string;
}

export class CommandService {
  private commands = new Map<string, SlashCommand>();
  private aliases = new Map<string, string>();
  private topLevelCommands: SlashCommand[] = [];
  private installedSkills: InstalledSkillEntry[] = [];
  /** 配置未成功读取前保持关闭，避免演进命令意外暴露。 */
  private skillEvolutionEnabled = false;
  /**
   * Built-ins and user commands are kept apart because they refresh on
   * different clocks: built-ins are registered once at boot, user commands are
   * re-read from disk whenever `refreshUserCommands` runs. Rebuilding from the
   * two lists is what lets a reload replace user commands without disturbing
   * the built-ins.
   */
  private builtinCommands: SlashCommand[] = [];
  private userCommands: SlashCommand[] = [];
  private inactiveUserCommands: InactiveUserCommand[] = [];

  /**
   * Optional callback invoked whenever the command registry changes (user
   * commands loaded/refreshed or installed skills updated). The UI layer uses
   * this to rebuild its autocomplete provider.
   */
  onCommandRegistryChange?: (skills: readonly InstalledSkillEntry[]) => void;

  /** @deprecated Use {@link onCommandRegistryChange}. */
  onInstalledSkillsChange?: (skills: readonly InstalledSkillEntry[]) => void;

  register(commands: readonly SlashCommand[]): void {
    this.builtinCommands = [...commands];
    this.rebuild();
  }

  /**
   * Re-register everything from the two source lists.
   *
   * A colliding user command is dropped here rather than ordered around.
   * Lookup happens two ways -- `resolve` reads the name map, `parseSlashCommand`
   * scans the array -- and the two would disagree under any ordering that let
   * both entries exist. `buildUserCommands` and the server already refuse these
   * names, so this is the third guard, not the only one.
   */
  private rebuild(): void {
    this.commands.clear();
    this.aliases.clear();
    const builtinNames = this.builtinNameSet();
    this.topLevelCommands = [
      ...this.builtinCommands,
      ...this.userCommands.filter((command) => !builtinNames.has(command.name)),
    ];
    for (const command of this.topLevelCommands) {
      this.registerCommand(command);
    }
    this.applySkillEvolutionVisibility();
  }

  /**
   * 更新技能自演进命令的展示状态。
   * 返回值用于让 UI 仅在状态变化时重建补全 provider。
   */
  setSkillEvolutionEnabled(enabled: boolean): boolean {
    if (this.skillEvolutionEnabled === enabled) {
      return false;
    }
    this.skillEvolutionEnabled = enabled;
    this.applySkillEvolutionVisibility();
    return true;
  }

  private applySkillEvolutionVisibility(): void {
    const visit = (commands: readonly SlashCommand[]): void => {
      for (const command of commands) {
        if (command.requiresSkillEvolution) {
          command.hidden = !this.skillEvolutionEnabled;
        }
        if (command.subCommands) {
          visit(command.subCommands);
        }
      }
    };
    visit(this.topLevelCommands);
  }

  /** Top-level built-in names only (not nested subcommands). */
  private topLevelBuiltinNameSet(): Set<string> {
    const names = new Set<string>();
    for (const command of this.builtinCommands) {
      names.add(command.name.toLowerCase());
      for (const alias of command.altNames ?? []) names.add(alias.toLowerCase());
    }
    return names;
  }

  /** @deprecated Use {@link topLevelBuiltinNameSet}. Subcommand names are not reserved. */
  private builtinNameSet(): Set<string> {
    return this.topLevelBuiltinNameSet();
  }

  private registerCommand(command: SlashCommand): void {
    this.commands.set(command.name, command);
    for (const alias of command.altNames ?? []) {
      this.aliases.set(alias.toLowerCase(), command.name);
    }
    for (const subCommand of command.subCommands ?? []) {
      this.registerCommand(subCommand);
    }
  }

  resolve(name: string): SlashCommand | undefined {
    const lower = name.toLowerCase();
    const target = this.aliases.get(lower) ?? lower;
    return this.commands.get(target);
  }

  getAll(includeHidden = false): SlashCommand[] {
    return this.topLevelCommands
      .filter((command) => includeHidden || !command.hidden)
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Fetches the current installed-skill list from the backend via `ctx` and
   * stores it in `this.installedSkills`. Called on every `execute()` so that
   * the cache stays fresh without any extra wiring. This function is also
   * called by the first WebSocket connection. (From app-screen.ts)
   */
  async refreshSkills(
    ctx: CommandContext,
  ): Promise<void> {
    try {
      const payload = await ctx.request("skills.list", {});
      const skills = flattenArrayPayload(payload);
      this.installedSkills = skills.flatMap((item) => {
        if (item && typeof item === "object") {
          const obj = item as Record<string, unknown>;
          if (obj.installed === true && typeof obj.name === "string") {
            return [{
              name: obj.name as string,
              description: typeof obj.description === "string" ? obj.description : "",
            }];
          }
        }
        return [];
      });
      // Notify the UI so it can rebuild the autocomplete provider with the
      // fresh `/<skillName>` shorthands.
      this.notifyRegistryChange();
    } catch {
      // Keep the previous cache if the RPC fails.
    }
  }

  private notifyRegistryChange(): void {
    const skills = this.installedSkills;
    this.onCommandRegistryChange?.(skills);
    this.onInstalledSkillsChange?.(skills);
  }

  getInstalledSkills(): readonly InstalledSkillEntry[] {
    return this.installedSkills;
  }

  isTopLevelBuiltin(name: string): boolean {
    return this.topLevelBuiltinNameSet().has(name.toLowerCase());
  }

  /**
   * Fetch user-defined commands from the backend and merge them in.
   *
   * Called from {@link execute} before every slash dispatch, and eagerly on
   * WebSocket connect so autocomplete and `/help` see user commands before
   * the first submit. Re-reading the directory is the whole reload story; the
   * server globs disk per call, so there is no cache to invalidate.
   *
   * The built-in names travel with the request. The server cannot know them --
   * they are defined here, not there -- so it can only refuse `model.md` if we
   * say `model` is taken. Without that the server would report a command as
   * active that this client silently never runs.
   */
  async refreshUserCommands(ctx: CommandContext): Promise<void> {
    const builtinNames = this.topLevelBuiltinNameSet();
    try {
      const payload = await ctx.request<{
        commands?: UserCommandDefinition[];
        workspace_resolved?: boolean;
      }>(
        "commands.list",
        { builtin_names: [...builtinNames] },
      );
      if (payload?.workspace_resolved === false) {
        return;
      }
      const definitions = (payload?.commands ?? []) as UserCommandDefinition[];
      const { commands, inactive } = buildUserCommands(
        definitions,
        builtinNames,
        (def, args, runCtx) => this.runUserCommand(def, args, runCtx),
      );
      this.userCommands = commands;
      this.inactiveUserCommands = inactive;
      this.rebuild();
      this.notifyRegistryChange();
    } catch {
      // Keep the previous set if the RPC fails. A dropped connection should not
      // make the user's own commands disappear mid-session.
    }
  }

  /** User commands that will not run, and why, for `/help` to explain. */
  getInactiveUserCommands(): readonly InactiveUserCommand[] {
    return this.inactiveUserCommands;
  }

  /**
   * Expand a user command server-side, then send the result as a message.
   *
   * The expansion is a separate round trip so failures are visible before
   * anything reaches the model: a `@file` that could not be read is reported
   * here, next to the command that asked for it, instead of arriving as a gap
   * in a prompt the user never sees.
   */
  private async runUserCommand(
    def: UserCommandDefinition,
    args: string,
    ctx: CommandContext,
  ): Promise<void> {
    let payload: { text?: string; errors?: string[] };
    try {
      payload = await ctx.request<{ text?: string; errors?: string[] }>(
        "commands.expand",
        {
          name: def.name,
          args,
          builtin_names: [...this.builtinNameSet()],
        },
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.addItem(
        makeItem(ctx.sessionId, "error", `/${def.name} failed to expand: ${message}`),
      );
      return;
    }

    const text = payload?.text ?? "";
    if (!text.trim()) {
      ctx.addItem(
        makeItem(ctx.sessionId, "error", `/${def.name} expanded to nothing.`),
      );
      return;
    }
    // Errors do not block the send: the text is usable, with each unreadable
    // reference marked inline. Say so, then send it.
    for (const problem of payload?.errors ?? []) {
      ctx.addItem(makeItem(ctx.sessionId, "error", `/${def.name}: ${problem}`));
    }
    ctx.sendMessage(text);
  }

  async execute(raw: string, ctx: CommandContext): Promise<void> {
    const trimmed = raw.trim();
    // Re-read user commands before lookup when the first token is not a
    // top-level built-in, so a newly created file is visible on first use.
    if (trimmed.startsWith("/")) {
      const firstToken = trimmed.substring(1).trim().split(/\s+/)[0]?.toLowerCase() ?? "";
      if (!this.topLevelBuiltinNameSet().has(firstToken)) {
        await this.refreshUserCommands(ctx);
      }
    }
    const parsed = parseSlashCommand(trimmed, this.getAll(true));
    const command = parsed.command;
    if (!command) {
      // 注：/<skill> 已在 app-screen.handleSubmit 的行首分流里落到普通消息分支
      //（content 原样发送 + 提取 skills_to_use），不再改写成 /skills use。
      // 能走到这里的说明第一个 token 既非注册命令也非已装 skill → 未知命令。
      ctx.addItem(makeItem(ctx.sessionId, "error", `Unknown command: /${parsed.name || ""}`));
      return;
    }
    try {
      await command.action(ctx, parsed.args);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      ctx.addItem(makeItem(ctx.sessionId, "error", message));
    }
  }

  // Note: Command suggestions use the pi-tui library from app-screen.ts. This function is currently unused.
  async getSuggestions(partial: string, ctx?: CommandContext): Promise<CommandSuggestion[]> {
    const normalized = partial.replace(/^\//, "").toLowerCase();
    const parts = parseArgs(normalized);

    if (parts.length > 1) {
      // Traverse command chain to find the deepest matching command with completion
      let currentCommands = this.getAll();
      let matchedCommand: SlashCommand | undefined;
      let matchedPath: string[] = [];
      let remainingParts = parts;

      for (const part of parts) {
        const found = currentCommands.find((cmd) =>
          cmd.name === part || (cmd.altNames && cmd.altNames.includes(part))
        );
        if (!found) break;

        matchedCommand = found;
        matchedPath.push(found.name);
        remainingParts = remainingParts.slice(1);

        if (found.subCommands) {
          currentCommands = found.subCommands;
        } else {
          break;
        }
      }

      // If we found a command with completion and have remaining args
      if (matchedCommand?.completion && ctx && remainingParts.length >= 0) {
        const completionInput = remainingParts.join(" ");
        const values = await matchedCommand.completion(ctx, completionInput);
        const prefix = matchedPath.join(" ");
        return values.map((value) => ({
          value: `/${prefix} ${value}`,
          description: matchedCommand!.description,
          usage: matchedCommand!.usage,
          example: matchedCommand!.example,
        }));
      }

      // If we're at a subcommand level but haven't matched the final command,
      // suggest available subcommands
      if (matchedCommand?.subCommands && remainingParts.length > 0) {
        const lastPart = remainingParts[remainingParts.length - 1] || "";
        const subCommandSuggestions = matchedCommand.subCommands
          .filter((sub) => sub.name.startsWith(lastPart) || !lastPart)
          .map((sub) => ({
            value: `/${matchedPath.join(" ")} ${sub.name}`,
            description: sub.description,
            usage: sub.usage,
            example: sub.example,
          }));
        if (subCommandSuggestions.length > 0) {
          return subCommandSuggestions;
        }
      }
    }

    return this.getAll()
      .flatMap((command) =>
        [command.name, ...(command.altNames ?? [])].map((alias) => ({ command, alias })),
      )
      .filter(({ alias }) => alias.toLowerCase().startsWith(normalized))
      .map(({ command }) => ({
        value: `/${command.name}`,
        description: command.description,
        usage: command.usage,
        example: command.example,
      }))
      .filter(
        (item, index, self) =>
          self.findIndex((candidate) => candidate.value === item.value) === index,
      );
  }
}
