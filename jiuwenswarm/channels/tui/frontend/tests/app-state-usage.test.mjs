import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Isolate ~/.jiuwenswarm-tui/config.json for this test process only (does not
// affect the developer's real config, and does not leak to other test files
// since each *.test.mjs runs in its own `node` process).
const isolatedHome = mkdtempSync(join(tmpdir(), "jiuwenswarm-sl-usage-"));
process.env.USERPROFILE = isolatedHome;
process.env.HOME = isolatedHome;

const { CliPiAppState } = await import("../dist/app-state.js");
const { handleIncomingFrame } = await import("../dist/core/event-handlers.js");
const { saveTuiConfig } = await import("../dist/core/tui-config-store.js");

function usageSummaryFrame(usage, model) {
  return {
    type: "event",
    event: "chat.usage_summary",
    payload: { usage, ...(model ? { model } : {}) },
  };
}

function newState(sessionId) {
  // wsClient is never used because we never call state.start(); a plain stub
  // object is sufficient for constructing AppState in isolation.
  return new CliPiAppState({}, sessionId);
}

/** Minimal event delegate for routing `chat.usage_summary` without private fields. */
function usageRoutingDelegate(state) {
  return {
    getSessionId: () => state.getCommandContext().sessionId,
    getConnectionStatus: () => "connected",
    appendUsageSummary: (usage, model) => state.appendUsageSummary(usage, model),
    setContextUsedPercentage: () => {},
    setContextWindowLimit: () => {},
  };
}

try {
  // ── 2.4 / P-6: 有效正费用按会话累加；缺失/非法费用不影响累计。 ──
  {
    saveTuiConfig({ statusLine: undefined, outputStyle: undefined });
    const state = newState("usage-1");

    state.appendUsageSummary({ total_cost: 0.02 }, "model-a");
    assert.equal(state.getUsageSummary().total_cost_usd, 0.02);

    // 缺失 total_cost：增量视为 0，累计不变
    state.appendUsageSummary({}, "model-a");
    assert.equal(state.getUsageSummary().total_cost_usd, 0.02);

    // 第二笔正费用继续累加
    state.appendUsageSummary({ total_cost: 0.03 }, "model-a");
    assert.equal(state.getUsageSummary().total_cost_usd, 0.05);

    // 非法值（负数、NaN、Infinity、字符串）均不得改变累计值或抛异常
    for (const bad of [-5, NaN, Infinity, "0.5", null, undefined, {}]) {
      state.appendUsageSummary({ total_cost: bad }, "model-a");
      assert.equal(state.getUsageSummary().total_cost_usd, 0.05, `illegal total_cost ${String(bad)} must not change accumulated cost`);
    }

    // token 累加语义不受费用累加影响（既有行为回归）
    state.appendUsageSummary({ input_tokens: 10, output_tokens: 5, total_tokens: 15 }, "model-a");
    const summary = state.getUsageSummary();
    assert.equal(summary.total_input_tokens, 10);
    assert.equal(summary.total_output_tokens, 5);
    assert.equal(summary.total_tokens, 15);
    assert.equal(summary.total_cost_usd, 0.05, "token-only event must not add cost");
  }

  // ── 2.4: 会话重置（updateSession）同步清零累计费用与 token。 ──
  {
    const state = newState("usage-2");
    state.appendUsageSummary({ total_cost: 1.23, input_tokens: 100, output_tokens: 50, total_tokens: 150 }, "model-a");
    assert.equal(state.getUsageSummary().total_cost_usd, 1.23);

    state.updateSession("usage-2-next");
    const resetSummary = state.getUsageSummary();
    assert.equal(resetSummary.total_cost_usd, 0, "updateSession must zero cumulative cost");
    assert.equal(resetSummary.total_input_tokens, 0);
    assert.equal(resetSummary.total_tokens, 0);
  }

  // ── 回归：appendUsageMetadata（chat.llm_usage / usage_metadata 路径）复用了
  //     safeNonNegativeNumber 泛化后的守卫，须确认 token 增量语义未被破坏
  //     （非法/缺失输入按 0 增量处理，不抛异常）。 ──
  {
    const state = newState("usage-metadata-1");

    state.appendUsageMetadata({ input_tokens: 7, output_tokens: 3, total_tokens: 10 });
    let currentUsage = state.getSnapshot().currentQueryUsage;
    assert.deepEqual(currentUsage, { input_tokens: 7, output_tokens: 3, total_tokens: 10 });

    // 非法/缺失 token 字段：增量按 0 处理，不抛异常，不改变既有累计值
    for (const bad of [-1, NaN, Infinity, "5", null, undefined, {}]) {
      state.appendUsageMetadata({ input_tokens: bad, output_tokens: bad });
      currentUsage = state.getSnapshot().currentQueryUsage;
      assert.deepEqual(currentUsage, { input_tokens: 7, output_tokens: 3, total_tokens: 10 });
    }

    state.appendUsageMetadata({ input_tokens: 2, output_tokens: 1, total_tokens: 3 });
    assert.deepEqual(state.getSnapshot().currentQueryUsage, { input_tokens: 9, output_tokens: 4, total_tokens: 13 });
  }

  // ── 2.5 / P-5, P-7, P-8: buildStatusLineJsonInput 恒含新字段，且不删除既有字段。 ──
  {
    saveTuiConfig({ statusLine: undefined, outputStyle: undefined });
    const state = newState("json-1");
    const ctx = state.getCommandContext();
    const json = ctx.getStatusLineJsonInput();

    // 新字段存在且为默认值（无 usage/config 时）
    assert.equal(typeof json.cost.total_cost_usd, "number");
    assert.ok(Number.isFinite(json.cost.total_cost_usd));
    assert.ok(json.cost.total_cost_usd >= 0);
    assert.equal(json.cost.total_cost_usd, 0, "no cost events yet -> 0 (test_plan 6.2)");
    assert.equal(json.output_style.name, "default");

    // 既有字段未被删除或改名
    assert.equal(typeof json.usage.total_input_tokens, "number");
    assert.equal(typeof json.usage.total_output_tokens, "number");
    assert.equal(typeof json.usage.total_tokens, "number");
    assert.ok("context_window" in json);
    assert.equal(typeof json.context_window.context_window_size, "number");
    assert.equal(typeof json.context_window.used_percentage, "number");
    assert.equal(typeof json.context_window.remaining_percentage, "number");
    assert.equal(typeof json.mode, "string");
    assert.equal(typeof json.model, "string");
  }

  // ── 2.5 / 2.6 / P-7: outputStyle 配置覆盖与非法值回落 "default"（test_plan 3.5, 6.3）。 ──
  {
    const state = newState("json-2");
    const ctx = state.getCommandContext();

    saveTuiConfig({ outputStyle: "compact" });
    assert.equal(ctx.getStatusLineJsonInput().output_style.name, "compact");

    for (const invalid of ["", "   ", 42, null, {}, []]) {
      saveTuiConfig({ outputStyle: invalid });
      assert.equal(
        ctx.getStatusLineJsonInput().output_style.name,
        "default",
        `invalid outputStyle ${JSON.stringify(invalid)} must fall back to "default"`,
      );
    }

    saveTuiConfig({ outputStyle: "verbose" });
    assert.equal(ctx.getStatusLineJsonInput().output_style.name, "verbose");
  }

  // ── 4.1 集成：chat.usage_summary 经 handleIncomingFrame 路由到公开 appendUsageSummary，
  //     累计费用同时反映在 getUsageSummary() 与 statusline JSON 中。 ──
  {
    saveTuiConfig({ statusLine: undefined, outputStyle: undefined });
    const state = newState("integration-1");
    const ctx = state.getCommandContext();
    assert.equal(ctx.getStatusLineJsonInput().cost.total_cost_usd, 0);

    const delegate = usageRoutingDelegate(state);
    handleIncomingFrame(delegate, usageSummaryFrame({ total_cost: 0.5 }, "model-a"));
    handleIncomingFrame(delegate, usageSummaryFrame({ total_cost: 0.25 }, "model-b"));

    assert.equal(state.getUsageSummary().total_cost_usd, 0.75);
    assert.equal(ctx.getStatusLineJsonInput().cost.total_cost_usd, 0.75);
  }

  // ── P-10 / 契约：已配置场景下 statusline JSON 可读新增字段，且不依赖共享临时文件。 ──
  {
    saveTuiConfig({ statusLine: { type: "command", command: "cat", padding: 0 }, outputStyle: "compact" });
    const state = newState("json-configured-1");
    state.appendUsageSummary({ total_cost: 0.09 }, "model-a");
    const json = state.getCommandContext().getStatusLineJsonInput();
    assert.equal(json.cost.total_cost_usd, 0.09);
    assert.equal(json.output_style.name, "compact");
    assert.ok("usage" in json && "context_window" in json, "existing fields must remain in the payload");
  }

  console.log("app-state-usage tests passed");
} finally {
  rmSync(isolatedHome, { recursive: true, force: true });
}
