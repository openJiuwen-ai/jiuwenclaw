import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Isolate ~/.jiuwenswarm-tui/config.json for this test process only (does not
// affect the developer's real config, and does not leak to other test files
// since each *.test.mjs runs in its own `node` process).
const isolatedHome = mkdtempSync(join(tmpdir(), "jiuwenswarm-sl-cmd-"));
process.env.USERPROFILE = isolatedHome;
process.env.HOME = isolatedHome;

const { createStatusLineCommand } = await import("../dist/core/commands/builtins/statusline.js");
const { loadTuiConfig, saveTuiConfig } = await import("../dist/core/tui-config-store.js");
const { CliPiAppState } = await import("../dist/app-state.js");

function makeMockContext(overrides = {}) {
  const items = [];
  const sentMessages = [];
  const ctx = {
    sessionId: "test-session",
    preferredLanguage: "en",
    addItem: (item) => items.push(item),
    sendMessage: (content) => {
      sentMessages.push(content);
      return "req-1";
    },
    restartStatusLine: () => {},
    getStatusLineJsonInput: () => ({ mode: "agent.plan", model: "test-model" }),
    ...overrides,
  };
  return { ctx, items, sentMessages };
}

const cmd = createStatusLineCommand();

try {
  // ── Issue #648 / P-1: 未配置 + 无参 → 必须走 setup（sendMessage），
  // 不得只停留在 "not configured" info。 (test_plan 2.1, 3.1) ──
  {
    saveTuiConfig({ statusLine: undefined });
    const { ctx, items, sentMessages } = makeMockContext();
    cmd.action(ctx, "");
    assert.equal(sentMessages.length, 1, "unconfigured bare /statusline must send exactly one setup message");
    assert.match(sentMessages[0], /^\/statusline /);
    assert.ok(
      !items.some((i) => i.content?.includes("StatusLine — not configured")),
      "must not fall back to plain not-configured info when unconfigured",
    );
  }

  // ── 默认 setup 描述随 preferredLanguage 变化（中/英）。 (test_plan 2.1) ──
  {
    saveTuiConfig({ statusLine: undefined });
    const { ctx: ctxEn, sentMessages: msgsEn } = makeMockContext({ preferredLanguage: "en" });
    cmd.action(ctxEn, "");
    assert.match(msgsEn[0], /cost\.total_cost_usd/);
    assert.doesNotMatch(msgsEn[0], /[\u4e00-\u9fa5]/, "en description must not contain Chinese characters");

    const { ctx: ctxZh, sentMessages: msgsZh } = makeMockContext({ preferredLanguage: "zh" });
    cmd.action(ctxZh, "");
    assert.match(msgsZh[0], /cost\.total_cost_usd/);
    assert.match(msgsZh[0], /[\u4e00-\u9fa5]/, "zh description should contain Chinese characters");
  }

  // ── Issue #648 / P-2: 已配置 + 无参 → 不发 setup 消息，展示当前配置。 (test_plan 2.2, 3.2) ──
  {
    saveTuiConfig({ statusLine: { type: "command", command: "echo hi", padding: 2 } });
    const { ctx, items, sentMessages } = makeMockContext();
    cmd.action(ctx, "");
    assert.equal(sentMessages.length, 0, "configured bare /statusline must not trigger setup agent");
    assert.equal(items.length, 1);
    assert.equal(items[0].kind, "info");
    assert.match(items[0].content, /command: 'echo hi'/);
    assert.match(items[0].content, /padding: 2/);
  }

  // ── P-3: `/statusline get` 在任意配置状态下都不发送 setup 消息。 (test_plan 2.2, 3.2) ──
  {
    saveTuiConfig({ statusLine: undefined });
    const { ctx: ctxUnconfigured, items: itemsUnconfigured, sentMessages: msgsUnconfigured } = makeMockContext();
    cmd.action(ctxUnconfigured, "get");
    assert.equal(msgsUnconfigured.length, 0, "/statusline get must never send a setup message when unconfigured");
    assert.match(itemsUnconfigured[0].content, /StatusLine — not configured/);

    saveTuiConfig({ statusLine: { type: "command", command: "echo hi", padding: 0 } });
    const { ctx: ctxConfigured, items: itemsConfigured, sentMessages: msgsConfigured } = makeMockContext();
    cmd.action(ctxConfigured, "get");
    assert.equal(msgsConfigured.length, 0, "/statusline get must never send a setup message when configured");
    assert.match(itemsConfigured[0].content, /command: 'echo hi'/);
  }

  // ── P-4: 已知子命令本地处理，未知首词进入 agentGenerate。 (test_plan 2.3, 3.3, 6.4) ──
  // 同时覆盖 5.1/5.2「成功配置后刷新底部栏」：set/padding/clear 均须调用
  // ctx.restartStatusLine() 使轮询立即生效，而不是等下一次自然轮询周期。
  {
    // set
    let restartCalls = 0;
    const { ctx: ctxSet, sentMessages: msgsSet } = makeMockContext({ restartStatusLine: () => { restartCalls += 1; } });
    cmd.action(ctxSet, "set 'echo $mode'");
    assert.equal(msgsSet.length, 0);
    assert.equal(loadTuiConfig().statusLine?.command, "echo $mode");
    assert.equal(restartCalls, 1, "set must refresh the status line poll");

    // padding
    restartCalls = 0;
    const { ctx: ctxPad, sentMessages: msgsPad } = makeMockContext({ restartStatusLine: () => { restartCalls += 1; } });
    cmd.action(ctxPad, "padding 3");
    assert.equal(msgsPad.length, 0);
    assert.equal(loadTuiConfig().statusLine?.padding, 3);
    assert.equal(restartCalls, 1, "padding must refresh the status line poll");

    // clear
    restartCalls = 0;
    const { ctx: ctxClear, sentMessages: msgsClear } = makeMockContext({ restartStatusLine: () => { restartCalls += 1; } });
    cmd.action(ctxClear, "clear");
    assert.equal(msgsClear.length, 0);
    assert.equal(loadTuiConfig().statusLine, undefined);
    assert.equal(restartCalls, 1, "clear must refresh the status line poll");

    // help
    const { ctx: ctxHelp, items: itemsHelp, sentMessages: msgsHelp } = makeMockContext();
    cmd.action(ctxHelp, "help");
    assert.equal(msgsHelp.length, 0);
    assert.match(itemsHelp[0].content, /cost\.total_cost_usd/);
    assert.match(itemsHelp[0].content, /output_style\.name/);

    // json
    const { ctx: ctxJson, items: itemsJson, sentMessages: msgsJson } = makeMockContext();
    cmd.action(ctxJson, "json");
    assert.equal(msgsJson.length, 0);
    assert.match(itemsJson[0].content, /"mode": "agent.plan"/);

    // unknown first word → agent-generated mode
    const { ctx: ctxUnknown, sentMessages: msgsUnknown } = makeMockContext();
    cmd.action(ctxUnknown, "show my PS1 config");
    assert.equal(msgsUnknown.length, 1);
    assert.equal(msgsUnknown[0], "/statusline show my PS1 config");
  }

  // ── 6.1: 未配置引导离线时，沿用既有 offline 提示，不写半截配置。 ──
  {
    saveTuiConfig({ statusLine: undefined });
    const { ctx, items, sentMessages } = makeMockContext({ sendMessage: () => null });
    cmd.action(ctx, "");
    assert.equal(sentMessages.length, 0);
    assert.equal(items.length, 1);
    assert.equal(items[0].kind, "error");
    assert.match(items[0].content, /offline/);
    assert.equal(loadTuiConfig().statusLine, undefined, "offline setup attempt must not write a half config");
  }

  // ── 5.2: `/statusline json` 基于真实 AppState 展示时须含 cost/output_style
  //     （而不止是 mock 的固定字段），覆盖端到端「已配置后 json 命令」场景。 ──
  {
    saveTuiConfig({ statusLine: { type: "command", command: "cat", padding: 0 }, outputStyle: "compact" });
    // wsClient is never used because we never call state.start().
    const state = new CliPiAppState({}, "statusline-json-e2e");
    const realCtx = state.getCommandContext();
    const items = [];
    realCtx.addItem = (item) => items.push(item);
    cmd.action(realCtx, "json");
    assert.match(items[0].content, /"total_cost_usd": 0/);
    assert.match(items[0].content, /"output_style": \{\s*\n\s*"name": "compact"/);
  }

  // ── 结构校验：子命令集合与既有实现一致。 ──
  {
    assert.equal(cmd.name, "statusline");
    assert.deepEqual(cmd.altNames, ["sl"]);
    const subNames = cmd.subCommands.map((s) => s.name).sort();
    assert.deepEqual(subNames, ["clear", "get", "help", "json", "padding", "set"]);
  }

  console.log("statusline-command tests passed");
} finally {
  rmSync(isolatedHome, { recursive: true, force: true });
}
