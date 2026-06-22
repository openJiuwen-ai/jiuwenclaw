import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import {
  isKeybindingAction,
  isKeybindingContext,
  type KeybindingAction,
} from "./actions.js";
import { DEFAULT_BINDINGS } from "./defaultBindings.js";
import { isReservedKey, validateKeyId } from "./reserved.js";
import type {
  KeybindingBlock,
  KeybindingWarning,
  KeybindingsFile,
  LoadResult,
  ResolvedBindings,
} from "./types.js";

const CONFIG_DIR = join(homedir(), ".jiuwenswarm-tui");
const KEYBINDINGS_FILE = join(CONFIG_DIR, "keybindings.json");

export function getKeybindingsPath(): string {
  return KEYBINDINGS_FILE;
}

export function keybindingsFileExists(): boolean {
  return existsSync(KEYBINDINGS_FILE);
}

/** Build a resolved map from a list of blocks (no validation). */
function buildResolved(blocks: KeybindingBlock[]): ResolvedBindings {
  const resolved: ResolvedBindings = new Map();
  for (const block of blocks) {
    if (!isKeybindingContext(block.context)) continue;
    let ctxMap = resolved.get(block.context);
    if (!ctxMap) {
      ctxMap = new Map<string, KeybindingAction>();
      resolved.set(block.context, ctxMap);
    }
    for (const [key, action] of Object.entries(block.bindings)) {
      if (action === null) {
        ctxMap.delete(key);
        continue;
      }
      if (isKeybindingAction(action)) {
        ctxMap.set(key, action);
      }
    }
  }
  return resolved;
}

/** Apply a user block on top of an already-resolved map, collecting warnings. */
function applyUserBlock(
  resolved: ResolvedBindings,
  block: KeybindingBlock,
  warnings: KeybindingWarning[],
): void {
  if (!isKeybindingContext(block.context)) {
    warnings.push({ context: String(block.context), message: `未知 context："${block.context}"` });
    return;
  }
  if (typeof block.bindings !== "object" || block.bindings === null) {
    warnings.push({ context: block.context, message: "bindings 必须是对象" });
    return;
  }
  let ctxMap = resolved.get(block.context);
  if (!ctxMap) {
    ctxMap = new Map<string, KeybindingAction>();
    resolved.set(block.context, ctxMap);
  }
  for (const [key, action] of Object.entries(block.bindings)) {
    const keyError = validateKeyId(key);
    if (keyError) {
      warnings.push({ context: block.context, key, message: keyError });
      continue;
    }
    if (isReservedKey(key)) {
      warnings.push({
        context: block.context,
        key,
        message: `"${key}" 是保留键，不可重绑`,
      });
      continue;
    }
    if (action === null) {
      ctxMap.delete(key);
      continue;
    }
    if (typeof action !== "string" || !isKeybindingAction(action)) {
      warnings.push({
        context: block.context,
        key,
        message: `未知 action："${String(action)}"`,
      });
      continue;
    }
    ctxMap.set(key, action);
  }
}

/**
 * Load keybindings: start from defaults, merge the user's keybindings.json on
 * top. Always succeeds — on any error it falls back to defaults and reports a
 * warning, so the TUI can never fail to start because of a bad config.
 */
export function loadKeybindings(): LoadResult {
  const resolved = buildResolved(DEFAULT_BINDINGS);
  const warnings: KeybindingWarning[] = [];

  if (!existsSync(KEYBINDINGS_FILE)) {
    return { resolved, warnings, userFileLoaded: false };
  }

  let parsed: KeybindingsFile;
  try {
    const raw = readFileSync(KEYBINDINGS_FILE, "utf8").trim();
    if (!raw) {
      return { resolved, warnings, userFileLoaded: false };
    }
    parsed = JSON.parse(raw) as KeybindingsFile;
  } catch (err) {
    warnings.push({ message: `解析 keybindings.json 失败：${(err as Error).message}` });
    return { resolved, warnings, userFileLoaded: false };
  }

  if (!parsed || !Array.isArray(parsed.bindings)) {
    warnings.push({ message: 'keybindings.json 必须包含 "bindings" 数组' });
    return { resolved, warnings, userFileLoaded: true };
  }

  for (const block of parsed.bindings) {
    if (!block || typeof block !== "object") {
      warnings.push({ message: "bindings 数组中存在无效的 block" });
      continue;
    }
    applyUserBlock(resolved, block as KeybindingBlock, warnings);
  }

  return { resolved, warnings, userFileLoaded: true };
}
