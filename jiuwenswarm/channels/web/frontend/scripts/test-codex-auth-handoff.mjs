import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const root = new URL("..", import.meta.url);
const sourceUrl = new URL(
  "src/components/ConfigPanel/codexAuthHandoff.ts",
  root,
);
const tempDir = await mkdtemp(join(tmpdir(), "codex-auth-handoff-"));

try {
  const source = await readFile(sourceUrl, "utf8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2020,
      target: ts.ScriptTarget.ES2020,
      importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
    },
  });
  const outputPath = join(tempDir, "codexAuthHandoff.mjs");
  await writeFile(outputPath, transpiled.outputText, "utf8");
  const { shouldObserveCodexAuth, shouldRetainCodexAuthHandoff } = await import(
    `file://${outputPath.replace(/\\/g, "/")}`
  );

  const handoff = {
    available: true,
    connected: false,
    state: "waiting_for_user",
    operation_id: "operation-current",
  };
  const status = (state, overrides = {}) => ({
    available: true,
    connected: false,
    state,
    operation_id: "operation-current",
    ...overrides,
  });

  assert.equal(shouldRetainCodexAuthHandoff(handoff, status("waiting_for_user")), true);
  assert.equal(shouldRetainCodexAuthHandoff(handoff, status("reconciling")), true);

  for (const inactiveState of ["canceling", "error", "expired", "not_connected", "unavailable"]) {
    assert.equal(
      shouldRetainCodexAuthHandoff(handoff, status(inactiveState)),
      false,
      `${inactiveState} must clear the device handoff`,
    );
  }

  assert.equal(
    shouldRetainCodexAuthHandoff(handoff, status("waiting_for_user", { available: false })),
    false,
  );
  assert.equal(
    shouldRetainCodexAuthHandoff(handoff, status("waiting_for_user", { connected: true })),
    false,
  );
  assert.equal(
    shouldRetainCodexAuthHandoff(
      handoff,
      status("waiting_for_user", { operation_id: "operation-replaced" }),
    ),
    false,
  );
  assert.equal(shouldRetainCodexAuthHandoff(null, status("waiting_for_user")), false);

  assert.equal(shouldObserveCodexAuth(false, null, null), false);
  assert.equal(shouldObserveCodexAuth(true, null, null), true);
  assert.equal(shouldObserveCodexAuth(false, status("waiting_for_user"), null), true);
  assert.equal(shouldObserveCodexAuth(false, status("reconciling"), null), true);
  assert.equal(shouldObserveCodexAuth(false, status("canceling"), null), true);
  assert.equal(shouldObserveCodexAuth(false, status("not_connected"), null), false);
  assert.equal(shouldObserveCodexAuth(false, null, handoff), true);

  console.log("codex auth handoff tests passed");
} finally {
  await rm(tempDir, { recursive: true, force: true });
}
