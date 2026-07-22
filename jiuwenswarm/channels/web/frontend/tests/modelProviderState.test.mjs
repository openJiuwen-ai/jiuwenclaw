import assert from "node:assert/strict";
import test from "node:test";

import {
  CODEX_SUBSCRIPTION_MODEL,
  CODEX_SUBSCRIPTION_PROVIDER,
  transitionModelProvider,
} from "../node_modules/.cache/model-provider-state/components/ConfigPanel/modelProviderState.js";
import { ModelProviderStateStorage } from "../node_modules/.cache/model-provider-state/components/ConfigPanel/modelProviderStateStorage.js";

const openRouterModel = {
  model_name: "anthropic/claude-sonnet-4",
  api_base: "https://openrouter.ai/api/v1",
  api_key: "openrouter-canary-key",
  model_provider: "OpenRouter",
  alias: "sonnet",
  reasoning_level: "high",
  origin_index: 3,
};

test("captures API configuration when switching from OpenRouter to Codex", () => {
  const transition = transitionModelProvider(
    openRouterModel,
    CODEX_SUBSCRIPTION_PROVIDER,
  );

  assert.equal(transition.model.model_provider, CODEX_SUBSCRIPTION_PROVIDER);
  assert.equal(transition.model.model_name, CODEX_SUBSCRIPTION_MODEL);
  assert.equal(transition.model.api_base, "");
  assert.equal(transition.model.api_key, "");
  assert.equal(transition.model.alias, "sonnet");
  assert.deepEqual(transition.snapshot, {
    model_provider: "OpenRouter",
    model_name: "anthropic/claude-sonnet-4",
    api_base: "https://openrouter.ai/api/v1",
    api_key: "openrouter-canary-key",
  });
});

test("restores only the exact previous API provider after Codex", () => {
  const toCodex = transitionModelProvider(
    openRouterModel,
    CODEX_SUBSCRIPTION_PROVIDER,
  );
  const restored = transitionModelProvider(
    toCodex.model,
    "OpenRouter",
    toCodex.snapshot,
  );

  assert.deepEqual(restored.model, openRouterModel);
});

test("clears incompatible fields when Codex switches to another API provider", () => {
  const toCodex = transitionModelProvider(
    openRouterModel,
    CODEX_SUBSCRIPTION_PROVIDER,
  );
  const switched = transitionModelProvider(
    toCodex.model,
    "DeepSeek",
    toCodex.snapshot,
  );

  assert.equal(switched.model.model_provider, "DeepSeek");
  assert.equal(switched.model.model_name, "");
  assert.equal(switched.model.api_base, "");
  assert.equal(switched.model.api_key, "");
  assert.notEqual(switched.model.model_name, CODEX_SUBSCRIPTION_MODEL);
});

test("clears Codex fields when no restorable API snapshot exists", () => {
  const codexModel = {
    ...openRouterModel,
    model_provider: CODEX_SUBSCRIPTION_PROVIDER,
    model_name: CODEX_SUBSCRIPTION_MODEL,
    api_base: "",
    api_key: "",
  };
  const switched = transitionModelProvider(codexModel, "OpenRouter");

  assert.deepEqual(
    {
      provider: switched.model.model_provider,
      model: switched.model.model_name,
      base: switched.model.api_base,
      key: switched.model.api_key,
    },
    { provider: "OpenRouter", model: "", base: "", key: "" },
  );
});

test("new draft round trip restores its same-provider API values", () => {
  const draft = { ...openRouterModel };
  delete draft.origin_index;
  const toCodex = transitionModelProvider(draft, CODEX_SUBSCRIPTION_PROVIDER);
  const restored = transitionModelProvider(
    toCodex.model,
    "OpenRouter",
    toCodex.snapshot,
  );

  assert.deepEqual(restored.model, draft);
});

test("component storage restores a newly appended row without origin_index", () => {
  const initialRows = [{ ...openRouterModel }];
  const storage = new ModelProviderStateStorage(initialRows);
  const newRow = {
    ...openRouterModel,
    model_name: "new-row-model",
    api_base: "https://new-row.example/v1",
    api_key: "new-row-canary-key",
    alias: "new-row",
  };
  delete newRow.origin_index;

  let rows = [...initialRows, newRow];
  storage.appendRow(initialRows, rows);
  rows = storage.transitionProvider(rows, 1, CODEX_SUBSCRIPTION_PROVIDER);
  rows = storage.transitionProvider(rows, 1, "OpenRouter");

  assert.deepEqual(rows[1], newRow);
  assert.deepEqual(rows[0], initialRows[0]);
});

test("component storage carries identities through reorder without crossing credentials", () => {
  const rowA = { ...openRouterModel, api_key: "row-a-canary-key", alias: "row-a" };
  const rowB = {
    ...openRouterModel,
    model_name: "row-b-model",
    api_base: "https://row-b.example/v1",
    api_key: "row-b-canary-key",
    alias: "row-b",
    origin_index: 4,
  };
  const initialRows = [rowA, rowB];
  const storage = new ModelProviderStateStorage(initialRows);

  let rows = storage.transitionProvider(initialRows, 0, CODEX_SUBSCRIPTION_PROVIDER);
  const reordered = [rows[1], rows[0]];
  storage.moveRow(rows, reordered, 0, 1);
  rows = storage.transitionProvider(reordered, 1, "OpenRouter");

  assert.equal(rows[0].api_key, "row-b-canary-key");
  assert.equal(rows[1].api_key, "row-a-canary-key");
  assert.equal(rows[1].api_base, rowA.api_base);
});

test("external removal cannot restore a removed or reindexed row credential", () => {
  const rowA = { ...openRouterModel, api_key: "removed-row-canary-key" };
  const rowB = {
    ...openRouterModel,
    model_name: "surviving-row-model",
    api_key: "surviving-row-canary-key",
    origin_index: 4,
  };
  const storage = new ModelProviderStateStorage([rowA, rowB]);
  const withCodexSecond = storage.transitionProvider(
    [rowA, rowB],
    1,
    CODEX_SUBSCRIPTION_PROVIDER,
  );

  const afterExternalRemoval = [{ ...withCodexSecond[1] }];
  storage.synchronize(afterExternalRemoval);
  const restored = storage.transitionProvider(afterExternalRemoval, 0, "OpenRouter");

  assert.equal(restored[0].api_key, "");
  assert.equal(restored[0].api_base, "");
  assert.notEqual(restored[0].api_key, rowA.api_key);
  assert.notEqual(restored[0].api_key, rowB.api_key);
});

test("external refresh clears snapshots even when rows retain persisted indexes", () => {
  const initialRows = [{ ...openRouterModel }];
  const storage = new ModelProviderStateStorage(initialRows);
  const codexRows = storage.transitionProvider(
    initialRows,
    0,
    CODEX_SUBSCRIPTION_PROVIDER,
  );

  const refreshedRows = codexRows.map((row) => ({ ...row }));
  storage.synchronize(refreshedRows);
  const restored = storage.transitionProvider(refreshedRows, 0, "OpenRouter");

  assert.equal(restored[0].api_key, "");
  assert.equal(restored[0].api_base, "");
});

test("cancel reset clears the pending API snapshot", () => {
  const initialRows = [{ ...openRouterModel }];
  const storage = new ModelProviderStateStorage(initialRows);
  const codexRows = storage.transitionProvider(
    initialRows,
    0,
    CODEX_SUBSCRIPTION_PROVIDER,
  );

  const cancelledRows = codexRows.map((row) => ({ ...row }));
  storage.reset(cancelledRows);
  const restored = storage.transitionProvider(cancelledRows, 0, "OpenRouter");

  assert.equal(restored[0].api_key, "");
  assert.equal(restored[0].model_name, "");
});

test("OpenAIAccount replacement remains exact and discards prior API snapshots", () => {
  const initialRows = [{ ...openRouterModel }];
  const storage = new ModelProviderStateStorage(initialRows);
  const codexRows = storage.transitionProvider(
    initialRows,
    0,
    CODEX_SUBSCRIPTION_PROVIDER,
  );
  const openAIAccountRow = {
    ...codexRows[0],
    model_provider: "OpenAIAccount",
    model_name: "",
    api_base: "https://chatgpt.com/backend-api/codex",
    api_key: "",
  };

  const accountRows = storage.replaceRow(
    codexRows,
    0,
    openAIAccountRow,
    { clearSnapshot: true },
  );

  assert.deepEqual(accountRows[0], openAIAccountRow);
  const accountToCodex = storage.transitionProvider(
    accountRows,
    0,
    CODEX_SUBSCRIPTION_PROVIDER,
  );
  const freshAccountRows = storage.replaceRow(
    accountToCodex,
    0,
    openAIAccountRow,
    { clearSnapshot: true },
  );
  assert.deepEqual(freshAccountRows[0], openAIAccountRow);
  assert.equal(freshAccountRows[0].api_key.includes("openrouter"), false);
});
