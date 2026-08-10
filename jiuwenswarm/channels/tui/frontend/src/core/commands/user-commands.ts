import {
  CommandKind,
  type CommandContext,
  type SlashCommand,
  type UserCommandDefinition,
} from "./types.js";

/**
 * Turn the `commands.list` RPC result into runnable slash commands.
 *
 * Expansion of `$ARGUMENTS`, `$1..$9` and `@file` happens **server-side**, via
 * a separate `commands.expand` round trip: resolving a file reference needs the
 * workspace boundary the server owns and the TUI does not.
 */

/** A command that will not run, and the reason, for `/help` to explain. */
export interface InactiveUserCommand {
  name: string;
  reason: string;
  filePath: string;
}

export interface UserCommandsResult {
  commands: SlashCommand[];
  inactive: InactiveUserCommand[];
}

function describe(def: UserCommandDefinition): string {
  const base = def.description || "User-defined command";
  return `${base} (${def.source})`;
}

/**
 * Build the runnable set, dropping the ones that lost.
 *
 * `builtinNames` wins every collision. A file cannot take over `/help` or
 * `/compact`: making the system undiscoverable, or shadowing the command that
 * saves a conversation, is not something a directory in a cloned repo should be
 * able to do. Losers are returned in `inactive` rather than discarded, so the
 * user can be told why their file does nothing.
 */
export function buildUserCommands(
  definitions: UserCommandDefinition[],
  builtinNames: Set<string>,
  runCommand: (
    def: UserCommandDefinition,
    args: string,
    ctx: CommandContext,
  ) => void | Promise<void>,
): UserCommandsResult {
  const commands: SlashCommand[] = [];
  const inactive: InactiveUserCommand[] = [];
  const claimed = new Set<string>();

  for (const def of definitions) {
    const name = def.name.toLowerCase();
    if (def.reserved || builtinNames.has(name)) {
      inactive.push({
        name,
        reason: "name is reserved by a built-in command",
        filePath: def.file_path,
      });
      continue;
    }
    if (def.shadowed_by) {
      inactive.push({
        name,
        reason: `shadowed by the ${def.shadowed_by} definition`,
        filePath: def.file_path,
      });
      continue;
    }
    // Defensive: the server already resolves precedence, but a duplicate here
    // would silently register two commands with one name.
    if (claimed.has(name)) {
      inactive.push({
        name,
        reason: "duplicate name",
        filePath: def.file_path,
      });
      continue;
    }
    claimed.add(name);

    commands.push({
      name,
      description: describe(def),
      usage: def.argument_hint ? `/${name} ${def.argument_hint}` : `/${name}`,
      argGuide: def.argument_hint || undefined,
      kind: CommandKind.USER,
      takesArgs: def.accepts_args ?? Boolean(def.argument_hint),
      action: (ctx, args) => runCommand(def, args, ctx),
    });
  }

  return { commands, inactive };
}
