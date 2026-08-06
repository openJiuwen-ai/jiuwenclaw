import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { MemoryViewController } from "../dist/ui/memory-view.js";

const tempRoot = mkdtempSync(join(tmpdir(), "jiuwenswarm-memory-view-"));
const memoryDir = join(tempRoot, ".jiuwen");
const userMemoryPath = join(memoryDir, "JIUWENSWARM.md");
mkdirSync(memoryDir);

let editorOpenCount = 0;
let renderCount = 0;
const tui = {
  requestRender() {
    renderCount += 1;
  },
};
const controller = new MemoryViewController({}, tui, () => {
  editorOpenCount += 1;
});

controller.state = {
  tab: "edit",
  list: {
    render: () => [],
  },
  mode: "code.normal",
  files: [
    {
      path: userMemoryPath,
      relative_path: "JIUWENSWARM.md",
      kind: "user",
      exists: false,
      size: 0,
      mtime: 0,
      lines: 0,
    },
  ],
  statusPayload: null,
  openPayload: null,
  projectDir: tempRoot,
  gitRoot: null,
  userMemoryPath,
  loading: false,
};

try {
  chmodSync(memoryDir, 0o444);

  // Windows does not enforce chmod write bits on directories. In that case,
  // use a read-only placeholder to exercise the post-creation write check;
  // POSIX exercises the missing-file creation path directly.
  const permissionProbe = join(memoryDir, ".permission-probe");
  let directoryIsEffectivelyReadOnly = false;
  try {
    writeFileSync(permissionProbe, "");
    rmSync(permissionProbe);
  } catch {
    directoryIsEffectivelyReadOnly = true;
  }
  if (!directoryIsEffectivelyReadOnly) {
    chmodSync(memoryDir, 0o777);
    writeFileSync(userMemoryPath, "");
    chmodSync(userMemoryPath, 0o444);
    controller.state.files[0].exists = true;
  }

  await controller.handleSelect(
    "edit",
    { value: userMemoryPath, label: "User memory" },
    "code.normal",
    tempRoot,
  );

  const rendered = controller
    .buildLines(120)
    .join("\n")
    .replace(/\u001b\[[0-9;]*m/g, "");

  assert.equal(editorOpenCount, 0, "the editor must not open for an unwritable memory path");
  assert.match(rendered, /Cannot (?:create|edit) memory file:/);
  assert.ok(renderCount > 0, "the TUI must render the permission error immediately");

  // Restoring write access must preserve the normal create-and-open flow.
  if (existsSync(userMemoryPath)) chmodSync(userMemoryPath, 0o666);
  chmodSync(memoryDir, 0o777);
  rmSync(userMemoryPath, { force: true });
  controller.state.files[0].exists = false;
  controller.statusMessage = null;
  editorOpenCount = 0;

  await controller.handleSelect(
    "edit",
    { value: userMemoryPath, label: "User memory" },
    "code.normal",
    tempRoot,
  );

  assert.equal(
    existsSync(userMemoryPath),
    true,
    "a writable User memory file must still be created",
  );
  assert.equal(editorOpenCount, 1, "the editor must still open for a writable User memory file");
} finally {
  if (existsSync(userMemoryPath)) chmodSync(userMemoryPath, 0o666);
  chmodSync(memoryDir, 0o777);
  rmSync(tempRoot, { recursive: true, force: true });
}

console.log("memory view permission tests passed");
