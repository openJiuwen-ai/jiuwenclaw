import { existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

import { loadTuiConfig, saveTuiConfig } from "./tui-config-store.js";

/**
 * Trusted directories storage with persistence via ~/.jiuwenswarm-tui/config.json.
 * Managed at CLI startup and via /workspace commands.
 */
let _trustedDirs: string[] | null = null;

/**
 * Ensure _trustedDirs is loaded from persisted config.
 */
function ensureLoaded(): void {
  if (_trustedDirs === null) {
    const config = loadTuiConfig();
    _trustedDirs = Array.isArray(config.trustedDirs) ? [...config.trustedDirs!] : [];
  }
}

/**
 * Persist current _trustedDirs to config file.
 */
function persist(): void {
  saveTuiConfig({ trustedDirs: _trustedDirs! });
}

/**
 * Normalize a path for comparison (handle trailing separators, case on Windows)
 */
function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) {
    return "";
  }
  // Expand ~ to home directory before resolving
  let expanded = trimmed;
  if (expanded === "~") {
    expanded = homedir();
  } else if (expanded.startsWith("~/")) {
    expanded = homedir() + expanded.slice(1);
  }
  const resolved = resolve(expanded);
  // On Windows, normalize case
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

/**
 * Get all trusted directories.
 * Returns empty array if no dirs set (will use default workspace).
 * @returns Array of trusted directory paths (normalized)
 */
export function getTrustedDirs(): string[] {
  ensureLoaded();
  return [..._trustedDirs!];
}

/**
 * Add a trusted directory.
 * @param path - Directory path to add (must be a folder, not a file)
 * @returns "added" if added, "exists" if already trusted, "not_found" if path doesn't exist, "invalid" if invalid path or not a directory, "no_access" if permission denied
 */
export function addTrustedDir(path: string): "added" | "exists" | "not_found" | "invalid" | "no_access" {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  try {
    const stats = statSync(normalized);
    if (!stats.isDirectory()) {
      return "invalid";
    }
  } catch {
    return "invalid";
  }
  const access = checkDirAccess(normalized);
  if (access !== "valid") {
    return access;
  }
  if (_trustedDirs!.includes(normalized)) {
    return "exists";
  }
  _trustedDirs!.push(normalized);
  persist();
  return "added";
}

/**
 * Check that a normalized directory path is accessible (readable).
 * @returns "valid" if accessible, "no_access" if permission denied, "invalid" for other errors
 */
function checkDirAccess(normalized: string): "valid" | "no_access" | "invalid" {
  try {
    readdirSync(normalized);
  } catch (err: any) {
    if (err.code === "EACCES") {
      return "no_access";
    }
    return "invalid";
  }
  return "valid";
}

/**
 * Validate a directory path without modifying trusted dirs state.
 * @param path - Directory path to validate
 * @returns "valid" if accessible directory, "not_found" if path doesn't exist, "invalid" if not a directory, "no_access" if permission denied
 */
export function validateDirPath(path: string): "valid" | "not_found" | "invalid" | "no_access" {
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  try {
    const stats = statSync(normalized);
    if (!stats.isDirectory()) {
      return "invalid";
    }
  } catch {
    return "invalid";
  }
  const access = checkDirAccess(normalized);
  if (access !== "valid") {
    return access;
  }
  return "valid";
}

/**
 * Reset trusted dirs and set a single path.
 * @param path - Directory path to set as the only trusted dir (must be a folder, not a file)
 * @returns "set" if set successfully, "not_found" if path doesn't exist, "invalid" if invalid path or not a directory, "no_access" if permission denied
 */
export function setTrustedDir(path: string): "set" | "not_found" | "invalid" | "no_access" {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  try {
    const stats = statSync(normalized);
    if (!stats.isDirectory()) {
      return "invalid";
    }
  } catch {
    return "invalid";
  }
  const access = checkDirAccess(normalized);
  if (access !== "valid") {
    return access;
  }
  _trustedDirs = [normalized];
  persist();
  return "set";
}

/**
 * Remove a trusted directory.
 * @param path - Directory path to remove
 * @returns true if removed, false if not found
 */
export function removeTrustedDir(path: string): boolean {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  const index = _trustedDirs!.indexOf(normalized);
  if (index === -1) {
    return false;
  }
  _trustedDirs!.splice(index, 1);
  persist();
  return true;
}

/**
 * Clear all trusted directories (will use default workspace only).
 */
export function clearTrustedDirs(): void {
  ensureLoaded();
  _trustedDirs = [];
  persist();
}

/**
 * Check if a path is a trusted directory.
 * @param path - Directory path to check
 * @returns true if trusted
 */
export function isTrustedDir(path: string): boolean {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  return _trustedDirs!.includes(normalized);
}

/**
 * Get the default workspace path.
 */
export function getDefaultWorkspacePath(): string {
  return resolve(homedir(), ".jiuwenswarm/agent/workspace");
}
