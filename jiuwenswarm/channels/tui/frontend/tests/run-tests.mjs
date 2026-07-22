import assert from "node:assert/strict";

import {
  AppScreen,
  buildPlanApprovalQuestionItems,
  formatQuestionOptionLabelForDisplay,
  getPendingQuestionTitle,
  getPlanApprovalListLayout,
  getPlanRejectFeedbackHint,
  isPlanApprovalRequest,
  shouldAppendPlanRejectFeedback,
  shouldCollectPlanRejectFeedback,
} from "../dist/ui/app-screen.js";
import { planSwarmflowToggle } from "../dist/core/commands/builtins/swarmflow.js";
import {
  canOpenSessionHistory,
  groupWorkflowAgentsByName,
  isSessionNode,
  shouldShowSessionTree,
  shouldShowTurnInDetailOrReply,
  sessionTurnLabelNumber,
} from "../dist/core/workflows.js";
import { CommandKind } from "../dist/core/commands/types.js";
import { createBuiltinCommands } from "../dist/core/commands/registry.js";
import {
  createAuthCommand,
  getOpenAIAccountLoginTtlMs,
  getOpenAIAccountPollDelayMs,
  isOpenAIAccountProvider,
  isOpenAIAccountReady,
  isRetryableOpenAIAccountError,
  parseOpenAIAccountModelArgument,
} from "../dist/core/commands/builtins/auth.js";
import {
  createModelCommand,
  isManuallyCreatableModelProvider,
  isModelConfigFieldEditable,
  isSupportedModelProvider,
} from "../dist/core/commands/builtins/model.js";
import { CliPiAppState } from "../dist/app-state.js";
import { QuestionBusyError, QuestionCancelledError } from "../dist/core/question-errors.js";
import { WsClient, WsRequestError } from "../dist/core/ws-client.js";

const planQuestion = "**Plan Approval**\n\nThe agent has completed a plan.";
const planApprovalKind = "plan_approval";

assert.equal(isPlanApprovalRequest("confirm_interrupt", planApprovalKind), true);
assert.equal(isPlanApprovalRequest("confirm_interrupt", "permission"), false);
assert.equal(isPlanApprovalRequest("permission_interrupt", planApprovalKind), false);

assert.equal(
  getPendingQuestionTitle("confirm_interrupt", "", 0, 1, planApprovalKind),
  "Exit Plan and Execute:",
);
assert.equal(getPendingQuestionTitle("confirm_interrupt", "", 0, 1), "Confirm action");

assert.equal(formatQuestionOptionLabelForDisplay("本次允许", false), "Allow once");
assert.equal(formatQuestionOptionLabelForDisplay("拒绝", false), "Reject");
assert.equal(formatQuestionOptionLabelForDisplay("本次允许", true), "Approve");
assert.equal(formatQuestionOptionLabelForDisplay("拒绝", true), "Reject");
assert.equal(getPlanRejectFeedbackHint(""), "[ tell jiuwenswarm what to change ]");
assert.equal(getPlanRejectFeedbackHint("use pytest"), "[ use pytest ]");
assert.equal(
  getPlanRejectFeedbackHint("", true),
  "[ \x1b[7m \x1b[0mtell jiuwenswarm what to change ]",
);
assert.equal(getPlanRejectFeedbackHint("use pytest", true, 4), "[ use \x1b[7m \x1b[0mpytest ]");

assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "拒绝", planApprovalKind), true);
assert.equal(
  shouldCollectPlanRejectFeedback("confirm_interrupt", "Reject", planApprovalKind),
  true,
);
assert.equal(
  shouldCollectPlanRejectFeedback("confirm_interrupt", "本次允许", planApprovalKind),
  false,
);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "拒绝", "permission"), false);
assert.equal(shouldAppendPlanRejectFeedback("confirm_interrupt", "拒绝", planApprovalKind), true);
assert.equal(
  shouldAppendPlanRejectFeedback("confirm_interrupt", "本次允许", planApprovalKind),
  false,
);

assert.deepEqual(
  buildPlanApprovalQuestionItems(
    [
      { label: "本次允许", description: "仅本次授权执行" },
      { label: "总是允许", description: "记住该规则，以后自动放行" },
      { label: "拒绝", description: "拒绝执行此工具" },
    ],
    "",
    false,
  ),
  [
    { value: "本次允许", label: "Approve", description: undefined },
    {
      value: "拒绝",
      label: "Reject",
      description: "[ tell jiuwenswarm what to change ]",
    },
  ],
);
assert.equal(
  buildPlanApprovalQuestionItems([{ label: "拒绝" }], "use pytest", true, 4)[0]?.description,
  "[ use \x1b[7m \x1b[0mpytest ]",
);
assert.deepEqual(getPlanApprovalListLayout(), {
  minPrimaryColumnWidth: 10,
  maxPrimaryColumnWidth: 10,
});

const slashCommands = AppScreen.prototype.buildSlashCommands.call({
  commands: {
    getAll: () => [
      {
        name: "swarmflows",
        altNames: ["swarmworkflows"],
        description: "Show swarm workflow runs for the current session",
        kind: CommandKind.BUILT_IN,
        action: () => undefined,
      },
      {
        name: "workspace",
        altNames: ["workspace_dir", "workspace-dir"],
        description: "Manage trusted directories for file operations",
        kind: CommandKind.BUILT_IN,
        action: () => undefined,
      },
    ],
  },
  state: {
    getCommandContext: () => ({}),
  },
});
assert.deepEqual(
  slashCommands.map((command) => command.name),
  ["swarmflows", "workspace"],
);

const pendingQuestionScreen = Object.create(AppScreen.prototype);
let pendingQuestionExitCount = 0;
let pendingQuestionInterruptCount = 0;
let pendingQuestionRenderCount = 0;
Object.assign(pendingQuestionScreen, {
  activeQuestionIndex: 0,
  transientNotice: "stale hint",
  startupPromptList: null,
  fileViewerState: null,
  diffViewerState: null,
  // Provide a minimal question list so Ctrl+D falls through to the
  // approval input handler (which ignores it) instead of crashing.
  questionList: { handleInput: () => undefined, getSelectedItem: () => null },
  questionCheckboxList: null,
  otherInputMode: false,
  state: {
    recordActivity: () => undefined,
    getSnapshot: () => ({
      pendingQuestion: {
        requestId: "plan-approval",
        source: "confirm_interrupt",
        questions: [{ header: "Exit Plan and Execute", question: planQuestion, options: [] }],
      },
    }),
  },
  tui: {
    requestRender: () => {
      pendingQuestionRenderCount += 1;
    },
  },
  exit: () => {
    pendingQuestionExitCount += 1;
  },
  interruptTask: () => {
    pendingQuestionInterruptCount += 1;
  },
});

// Ctrl+C on the approval box interrupts the task (single press) and does NOT exit
pendingQuestionScreen.handleInput("\x03");
assert.equal(pendingQuestionInterruptCount, 1);
assert.equal(pendingQuestionExitCount, 0);
assert.equal(pendingQuestionScreen.transientNotice, null);

// Esc likewise interrupts the task (single press)
pendingQuestionScreen.handleInput("\x1b");
assert.equal(pendingQuestionInterruptCount, 2);
assert.equal(pendingQuestionExitCount, 0);
assert.equal(pendingQuestionScreen.transientNotice, null);

// Ctrl+D is no longer supported on the approval box: does nothing
const renderCountBeforeCtrlD = pendingQuestionRenderCount;
pendingQuestionScreen.handleInput("\x04");
assert.equal(pendingQuestionInterruptCount, 2);
assert.equal(pendingQuestionExitCount, 0);
// Ctrl+D did not trigger an interrupt/exit; it may or may not request a
// render depending on the list handler, but it must not interrupt or exit.
assert.ok(pendingQuestionInterruptCount === 2 && pendingQuestionExitCount === 0);
console.log("ctrl+d render requests:", pendingQuestionRenderCount - renderCountBeforeCtrlD);

const agent = (name, node_type, correlation_id, id = `${name}-${node_type ?? "plain"}-${correlation_id ?? "none"}`) => ({
  id,
  name,
  status: "completed",
  node_type,
  correlation_id,
});

assert.equal(isSessionNode({ node_type: "agent_session" }), true);
assert.equal(isSessionNode({ node_type: "human_session" }), true);
assert.equal(isSessionNode({ node_type: "agent" }), false);
assert.equal(isSessionNode({ node_type: "human" }), false);
assert.equal(canOpenSessionHistory({ node_type: "agent_session" }), true);
assert.equal(canOpenSessionHistory({ node_type: "human_session" }), true);
assert.equal(canOpenSessionHistory({ node_type: "human", correlation_id: "p:h:0" }), false);
assert.equal(canOpenSessionHistory({ node_type: "agent" }), false);
assert.equal(canOpenSessionHistory({}), false);

const grouped = groupWorkflowAgentsByName([
  agent("coder", "agent", undefined),
  agent("coder", "agent", undefined),
  agent("review", "agent_session", "p:review:0"),
  agent("review", "agent_session", "p:review:1"),
  agent("host", "human", "p:host:0"),
]);
assert.equal(grouped.oneShots.length, 3);
assert.equal(grouped.sessions.length, 1);
assert.equal(grouped.sessions[0]?.label, "review");
assert.equal(grouped.sessions[0]?.members.length, 2);

// one-shot human() carries a real correlation_id but is NOT a session node.
assert.equal(isSessionNode(agent("host", "human", "p:host:0")), false);
assert.equal(isSessionNode(agent("review", "agent_session", "p:review:0")), true);
assert.equal(shouldShowTurnInDetailOrReply(agent("host", "human", "p:host:0")), false);
assert.equal(shouldShowTurnInDetailOrReply(agent("review", "agent_session", "p:review:0")), true);
assert.equal(
  shouldShowSessionTree(agent("review", "agent_session", "p:review:0"), [
    agent("review", "agent_session", "p:review:0"),
  ]),
  true,
);
const multiTurnPhase = [
  agent("review", "agent_session", "p:review:0"),
  agent("review", "agent_session", "p:review:1"),
];
assert.equal(shouldShowSessionTree(agent("review", "agent_session", "p:review:0"), multiTurnPhase), true);
assert.equal(sessionTurnLabelNumber(agent("host", "human", "p:host:0"), []), null);
assert.equal(sessionTurnLabelNumber(agent("review", "agent_session", "p:review:0"), [
  agent("review", "agent_session", "p:review:0"),
]), 0);
assert.equal(sessionTurnLabelNumber(agent("review", "agent_session", "p:review:1"), multiTurnPhase), 1);

// Single-turn session still forms a tree (parent + turn 0) — distinct from human()/agent().
const singleSessionGrouped = groupWorkflowAgentsByName([
  agent("solo", "human_session", "p:solo:0"),
  agent("plain", "human", "p:plain:0"),
]);
assert.equal(singleSessionGrouped.sessions.length, 1);
assert.equal(singleSessionGrouped.sessions[0]?.label, "solo");
assert.equal(singleSessionGrouped.sessions[0]?.members.length, 1);
assert.equal(singleSessionGrouped.oneShots.length, 1);
assert.equal(singleSessionGrouped.oneShots[0]?.name, "plain");
assert.equal(
  sessionTurnLabelNumber(agent("solo", "human_session", "p:solo:0"), [
    agent("solo", "human_session", "p:solo:0"),
  ]),
  0,
);
assert.equal(
  sessionTurnLabelNumber(agent("plain", "human", "p:plain:0"), [
    agent("plain", "human", "p:plain:0"),
  ]),
  null,
);

assert.deepEqual(
  planSwarmflowToggle({ target: "on", currentEnabled: true, mode: "team" }),
  {
    writeConfig: false,
    switchToTeam: false,
    message: "SwarmFlow is already on in team mode. No changes were made.",
  },
);
assert.deepEqual(
  planSwarmflowToggle({ target: "on", currentEnabled: true, mode: "code.normal" }),
  {
    writeConfig: false,
    switchToTeam: true,
    message:
      "SwarmFlow is already on. Switched to team mode — the next workflow run uses the enabled setting.",
  },
);
assert.deepEqual(
  planSwarmflowToggle({ target: "off", currentEnabled: false, mode: "team" }),
  {
    writeConfig: false,
    switchToTeam: false,
    message: "SwarmFlow is already off. Mode remains team. No changes were made.",
  },
);
assert.equal(
  planSwarmflowToggle({ target: "on", currentEnabled: false, mode: "team" }).writeConfig,
  true,
);

assert.equal(parseOpenAIAccountModelArgument(""), undefined);
assert.equal(parseOpenAIAccountModelArgument("model=gpt-test"), "gpt-test");
assert.equal(parseOpenAIAccountModelArgument("gpt-test"), "gpt-test");
assert.throws(() => parseOpenAIAccountModelArgument("model=two models"), /single model ID/);
assert.equal(getOpenAIAccountPollDelayMs(5), 15_000);
assert.equal(getOpenAIAccountPollDelayMs(20), 20_000);
assert.equal(getOpenAIAccountLoginTtlMs(60), 60_000);
assert.equal(getOpenAIAccountLoginTtlMs(undefined), 300_000);
assert.equal(getOpenAIAccountLoginTtlMs(-1), 300_000);
assert.equal(isOpenAIAccountProvider("openaiaccount"), true);
assert.equal(isOpenAIAccountProvider("OpenAI"), false);
assert.equal(isOpenAIAccountReady({ authenticated: true, needs_refresh: true }), true);
assert.equal(isOpenAIAccountReady({ authenticated: false }), false);
assert.equal(
  isRetryableOpenAIAccountError(new WsRequestError("temporary", { payload: { retriable: true } })),
  true,
);
assert.equal(isSupportedModelProvider("OpenAIAccount"), true);
assert.equal(isManuallyCreatableModelProvider("OpenAIAccount"), false);
assert.equal(isManuallyCreatableModelProvider("OpenAI"), true);
assert.equal(isModelConfigFieldEditable("model_name", "OpenAIAccount", "edit"), false);
assert.equal(isModelConfigFieldEditable("api_base", "OpenAIAccount", "edit"), false);
assert.equal(isModelConfigFieldEditable("api_key", "OpenAIAccount", "edit"), false);
assert.equal(isModelConfigFieldEditable("model_provider", "OpenAIAccount", "edit"), false);
assert.equal(isModelConfigFieldEditable("alias", "OpenAIAccount", "edit"), true);
assert.equal(isModelConfigFieldEditable("reasoning_level", "OpenAIAccount", "edit"), true);

const responseErrorClient = new WsClient("ws://example.test/tui");
const responseErrorPromise = new Promise((resolve, reject) => {
  responseErrorClient.pending.set("oauth-request", {
    resolve,
    reject,
    timer: setTimeout(() => reject(new Error("response mapping timed out")), 1000),
  });
});
responseErrorClient.dispatchFrame({
  type: "res",
  id: "oauth-request",
  ok: false,
  code: "openai_account_device_code_poll_network_error",
  error: "temporary network failure",
  payload: { retriable: true, relogin_required: false },
});
const mappedResponseError = await responseErrorPromise.catch((error) => error);
assert.ok(mappedResponseError instanceof WsRequestError);
assert.equal(mappedResponseError.code, "openai_account_device_code_poll_network_error");
assert.equal(mappedResponseError.requestId, "oauth-request");
assert.equal(mappedResponseError.retriable, true);
assert.equal(mappedResponseError.payload.relogin_required, false);

const questionState = new CliPiAppState(new WsClient("ws://example.test/tui"));
const pendingLocalQuestion = questionState.askQuestions([
  {
    header: "Model",
    question: "Choose a model",
    options: [{ label: "gpt-test" }],
  },
]);
const busyQuestionError = await questionState
  .askQuestions([
    {
      header: "Other",
      question: "Choose another value",
      options: [{ label: "other" }],
    },
  ])
  .catch((error) => error);
assert.ok(busyQuestionError instanceof QuestionBusyError);
assert.equal(questionState.cancelQuestion(), true);
const cancelledQuestionError = await pendingLocalQuestion.catch((error) => error);
assert.ok(cancelledQuestionError instanceof QuestionCancelledError);
assert.equal(questionState.getSnapshot().pendingQuestion, null);

const builtinCommandNames = createBuiltinCommands().map((command) => command.name);
assert.equal(builtinCommandNames.includes("login"), false);
assert.equal(builtinCommandNames.includes("auth"), true);

function createAuthTestContext(request, questionAnswers = []) {
  const items = [];
  const running = [];
  const selectedModels = [];
  const questions = [];
  const pendingAnswers = [...questionAnswers];
  let interrupted = false;
  return {
    context: {
      sessionId: "test-session",
      request,
      askQuestions: async (questionItems, source) => {
        questions.push({ questionItems, source });
        const selected = pendingAnswers.shift();
        if (!selected) throw new Error("unexpected or cancelled test question");
        return [{ selected_options: [selected], custom_input: selected }];
      },
      addItem: (item) => items.push(item),
      isInterruptRequested: () => interrupted,
      clearInterruptRequested: () => {
        interrupted = false;
      },
      setRunningCommand: (name) => running.push(name),
      setModel: (name) => selectedModels.push(name),
    },
    items,
    running,
    selectedModels,
    questions,
    interrupt: () => {
      interrupted = true;
    },
  };
}

const loginRequests = [];
const loginContext = createAuthTestContext(async (method, params) => {
  loginRequests.push([method, params]);
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") return { status: "none" };
  if (method === "openai_account.auth.start_login") {
    return {
      status: "pending",
      login_id: "login-1",
      user_code: "TEST-CODE",
      verification_uri: "https://example.test/device",
      interval: 0,
      expires_in: 60,
      expires_at: Date.now() / 1000 + 60,
    };
  }
  if (method === "openai_account.auth.poll_login") {
    return { status: "authenticated", authenticated: true, auth: { authenticated: true } };
  }
  if (method === "openai_account.models.list") {
    return {
      models: ["gpt-test"],
      base_url: "https://example.test/codex",
      auth: { authenticated: true },
    };
  }
  if (method === "command.model") {
    return {
      current: "gpt-test",
      models: [
        {
          name: "gpt-test",
          model_name: "gpt-test",
          model_provider: "OpenAIAccount",
          is_current: true,
        },
      ],
    };
  }
  throw new Error(`unexpected method: ${method}`);
});
await createAuthCommand({ minPollIntervalMs: 0 }).action(loginContext.context, "login");
assert.deepEqual(
  loginRequests.map(([method]) => method),
  [
    "openai_account.auth.status",
    "openai_account.auth.pending_login",
    "openai_account.auth.start_login",
    "openai_account.auth.poll_login",
    "openai_account.models.list",
    "command.model",
  ],
);
assert.deepEqual(loginContext.running, ["openai-account-login", null]);
assert.equal(loginContext.questions.length, 0);
assert.equal(
  loginContext.items.some((item) =>
    item.meta?.items?.some(
      (entry) => entry.label === "current model" && entry.value === "gpt-test",
    ),
  ),
  true,
);

let resumedPollCount = 0;
const resumedContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") {
    return {
      status: "pending",
      login_id: "login-resumed",
      user_code: "RESUME-CODE",
      verification_uri: "https://example.test/device",
      interval: 0,
      expires_at: Date.now() / 1000 + 60,
    };
  }
  if (method === "openai_account.auth.poll_login") {
    resumedPollCount += 1;
    if (resumedPollCount === 1) {
      throw new WsRequestError("temporary network failure", {
        code: "openai_account_device_code_poll_network_error",
        payload: { retriable: true },
      });
    }
    return { status: "authenticated", authenticated: true, auth: { authenticated: true } };
  }
  if (method === "openai_account.models.list") {
    return {
      models: ["gpt-test"],
      base_url: "https://example.test/codex",
      auth: { authenticated: true },
    };
  }
  if (method === "command.model") {
    return {
      current: "gpt-test",
      models: [
        {
          name: "gpt-test",
          model_name: "gpt-test",
          model_provider: "OpenAIAccount",
          is_current: true,
        },
      ],
    };
  }
  throw new Error(`unexpected method: ${method}`);
});
await createAuthCommand({ minPollIntervalMs: 0 }).action(resumedContext.context, "login");
assert.equal(resumedPollCount, 2);

let cancelledContext;
const cancelledMethods = [];
cancelledContext = createAuthTestContext(async (method) => {
  cancelledMethods.push(method);
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") return { status: "none" };
  if (method === "openai_account.auth.start_login") {
    cancelledContext.interrupt();
    return {
      status: "pending",
      login_id: "login-cancelled",
      user_code: "CANCEL-CODE",
      verification_uri: "https://example.test/device",
      interval: 0,
      expires_at: Date.now() / 1000 + 60,
    };
  }
  throw new Error(`unexpected method after cancellation: ${method}`);
});
await createAuthCommand({ minPollIntervalMs: 0 }).action(cancelledContext.context, "login");
assert.equal(cancelledMethods.includes("openai_account.auth.poll_login"), false);
assert.equal(
  cancelledContext.items.some((item) => String(item.content).includes("login cancelled")),
  true,
);
assert.deepEqual(cancelledContext.running, ["openai-account-login", null]);

let monotonicNowMs = 0;
let monotonicPollCount = 0;
const monotonicContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") return { status: "none" };
  if (method === "openai_account.auth.start_login") {
    return {
      status: "pending",
      login_id: "login-monotonic",
      user_code: "MONOTONIC-CODE",
      verification_uri: "https://example.test/device",
      interval: 0,
      expires_in: 60,
      // This display-only wall-clock value is intentionally stale.
      expires_at: 1,
    };
  }
  if (method === "openai_account.auth.poll_login") {
    monotonicPollCount += 1;
    return { status: "authenticated", authenticated: true, auth: { authenticated: true } };
  }
  if (method === "openai_account.models.list") {
    return {
      models: ["gpt-test"],
      base_url: "https://example.test/codex",
      auth: { authenticated: true },
    };
  }
  if (method === "command.model") {
    return {
      current: "gpt-test",
      models: [
        {
          name: "gpt-test",
          model_name: "gpt-test",
          model_provider: "OpenAIAccount",
          is_current: true,
        },
      ],
    };
  }
  throw new Error(`unexpected monotonic login method: ${method}`);
});
await createAuthCommand({
  minPollIntervalMs: 0,
  nowMs: () => monotonicNowMs,
  sleep: async (delayMs) => {
    monotonicNowMs += delayMs;
  },
}).action(monotonicContext.context, "login");
assert.equal(monotonicPollCount, 1);
assert.equal(
  monotonicContext.items.some((item) =>
    item.meta?.items?.some((entry) => entry.label === "authenticated" && entry.value === "true"),
  ),
  true,
);

let shortTtlNowMs = 0;
let shortTtlPollCount = 0;
const shortTtlSleeps = [];
const shortTtlContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") return { status: "none" };
  if (method === "openai_account.auth.start_login") {
    return {
      status: "pending",
      login_id: "login-short-ttl",
      user_code: "SHORT-TTL-CODE",
      verification_uri: "https://example.test/device",
      interval: 5,
      expires_in: 1,
      expires_at: Date.now() / 1000 + 60,
    };
  }
  if (method === "openai_account.auth.poll_login") {
    shortTtlPollCount += 1;
    return { status: "pending", authenticated: false };
  }
  throw new Error(`unexpected short TTL method: ${method}`);
});
await createAuthCommand({
  minPollIntervalMs: 0,
  nowMs: () => shortTtlNowMs,
  sleep: async (delayMs) => {
    shortTtlSleeps.push(delayMs);
    shortTtlNowMs += delayMs;
  },
}).action(shortTtlContext.context, "login");
assert.equal(shortTtlPollCount, 0);
assert.equal(
  shortTtlSleeps.reduce((total, delayMs) => total + delayMs, 0),
  1_000,
);
assert.equal(Math.max(...shortTtlSleeps), 100);
assert.equal(
  shortTtlContext.items.some((item) => String(item.content).includes("login expired")),
  true,
);

let serverExpiredPollCount = 0;
const serverExpiredContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "openai_account.auth.pending_login") return { status: "none" };
  if (method === "openai_account.auth.start_login") {
    return {
      status: "pending",
      login_id: "login-server-expired",
      user_code: "SERVER-EXPIRED-CODE",
      verification_uri: "https://example.test/device",
      interval: 0,
      expires_in: 60,
    };
  }
  if (method === "openai_account.auth.poll_login") {
    serverExpiredPollCount += 1;
    return { status: "expired", authenticated: false };
  }
  throw new Error(`unexpected server-expired method: ${method}`);
});
await createAuthCommand({ minPollIntervalMs: 0 }).action(serverExpiredContext.context, "login");
assert.equal(serverExpiredPollCount, 1);
assert.equal(
  serverExpiredContext.items.some((item) => String(item.content).includes("login expired")),
  true,
);

const logoutRequests = [];
const logoutContext = createAuthTestContext(async (method, params) => {
  logoutRequests.push([method, params]);
  if (method === "openai_account.auth.logout") {
    return { logged_out: true, auth: { authenticated: false } };
  }
  if (method === "command.model") {
    return {
      current: "gpt-test",
      models: [
        {
          name: "gpt-test",
          model_name: "gpt-test",
          model_provider: "OpenAIAccount",
          is_current: true,
        },
      ],
    };
  }
  throw new Error(`unexpected logout method: ${method}`);
});
await createAuthCommand().action(logoutContext.context, "logout");
assert.deepEqual(
  logoutRequests.map(([method]) => method),
  ["openai_account.auth.logout", "command.model"],
);
assert.equal(
  logoutContext.items.some((item) => String(item.content).includes("configurations were kept")),
  true,
);
assert.equal(
  logoutContext.items.some((item) =>
    item.meta?.items?.some(
      (entry) => entry.label === "model readiness" && entry.value === "login required",
    ),
  ),
  true,
);

const logoutFollowUpFailureContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.logout") {
    return { logged_out: true, auth: { authenticated: false } };
  }
  if (method === "command.model") throw new Error("model service unavailable");
  throw new Error(`unexpected logout follow-up method: ${method}`);
});
await createAuthCommand().action(logoutFollowUpFailureContext.context, "logout");
assert.equal(
  logoutFollowUpFailureContext.items.some((item) =>
    String(item.content).includes("Current model could not be loaded"),
  ),
  true,
);
assert.equal(
  logoutFollowUpFailureContext.items.some((item) =>
    String(item.content).includes("auth logout failed"),
  ),
  false,
);
assert.equal(
  logoutFollowUpFailureContext.items.some((item) =>
    String(item.content).includes("configurations were kept"),
  ),
  true,
);

const catalogFailureContext = createAuthTestContext(async (method) => {
  if (method === "openai_account.auth.status") return { authenticated: true };
  if (method === "openai_account.models.list") throw new Error("catalog unavailable");
  throw new Error(`unexpected catalog failure method: ${method}`);
});
await createAuthCommand().action(catalogFailureContext.context, "login");
assert.equal(
  catalogFailureContext.items.some((item) =>
    String(item.content).includes("OpenAI Account is connected"),
  ),
  true,
);
assert.equal(
  catalogFailureContext.items.some((item) => String(item.content).includes("catalog could not")),
  true,
);
assert.equal(
  catalogFailureContext.items.some((item) => String(item.content).includes("login failed")),
  false,
);

function createPickerFailureContext(questionError) {
  const testContext = createAuthTestContext(async (method) => {
    if (method === "openai_account.auth.status") return { authenticated: true };
    if (method === "openai_account.models.list") {
      return {
        models: ["gpt-test"],
        base_url: "https://example.test/codex",
        auth: { authenticated: true },
      };
    }
    if (method === "command.model") {
      return {
        current: "other",
        models: [
          {
            name: "other",
            model_name: "other",
            model_provider: "OpenAI",
            is_current: true,
          },
        ],
      };
    }
    throw new Error(`unexpected picker failure method: ${method}`);
  });
  testContext.context.askQuestions = async () => {
    throw questionError;
  };
  return testContext;
}

const pickerFailureContext = createPickerFailureContext(new Error("picker render failed"));
await createAuthCommand().action(pickerFailureContext.context, "login");
assert.equal(
  pickerFailureContext.items.some((item) =>
    String(item.content).includes("model selection failed: picker render failed"),
  ),
  true,
);
assert.equal(
  pickerFailureContext.items.some((item) => String(item.content).includes("selection cancelled")),
  false,
);

const pickerCancelledContext = createPickerFailureContext(
  new QuestionCancelledError("selection cancelled"),
);
await createAuthCommand().action(pickerCancelledContext.context, "login");
assert.equal(
  pickerCancelledContext.items.some((item) => String(item.content).includes("selection cancelled")),
  true,
);
assert.equal(
  pickerCancelledContext.items.some((item) => String(item.content).includes("selection failed")),
  false,
);

const pickerBusyContext = createPickerFailureContext(new QuestionBusyError());
await createAuthCommand().action(pickerBusyContext.context, "login");
assert.equal(
  pickerBusyContext.items.some((item) => String(item.content).includes("try /auth models again")),
  true,
);
assert.equal(
  pickerBusyContext.items.some((item) => String(item.content).includes("selection cancelled")),
  false,
);

const useRequests = [];
const useContext = createAuthTestContext(async (method, params) => {
  useRequests.push([method, params]);
  if (method === "openai_account.models.list") {
    return {
      models: ["gpt-other", "gpt-test"],
      base_url: "https://example.test/codex",
      auth: { authenticated: true },
    };
  }
  if (method === "openai_account.models.use") {
    assert.deepEqual(params, { model_id: "gpt-test" });
    return {
      type: "switched",
      current: "gpt-test",
      requested: "gpt-test",
      saved: true,
      applied: true,
    };
  }
  throw new Error(`unexpected method: ${method}`);
});
await createAuthCommand().action(useContext.context, "use 2");
assert.deepEqual(useContext.selectedModels, ["gpt-test"]);
assert.equal(JSON.stringify(useRequests).includes("access-secret"), false);
assert.equal(JSON.stringify(useRequests).includes("refresh-secret"), false);
assert.equal(
  useRequests.some(([method]) => method === "openai_account.models.use"),
  true,
);

const promptedLoginRequests = [];
let promptedLoginModels = [
  {
    name: "existing",
    model_name: "existing",
    model_provider: "OpenAI",
    api_base: "https://example.test/v1",
    is_current: true,
  },
];
const promptedLoginContext = createAuthTestContext(
  async (method, params) => {
    promptedLoginRequests.push([method, params]);
    if (method === "openai_account.auth.status") return { authenticated: true };
    if (method === "openai_account.models.list") {
      return {
        models: ["gpt-first", "gpt-second"],
        base_url: "https://example.test/codex",
        auth: { authenticated: true },
      };
    }
    if (method === "command.model" && !params.action && !params.model) {
      return {
        current: promptedLoginModels[0]?.name,
        models: promptedLoginModels,
        available_models: promptedLoginModels.map((model) => model.name),
      };
    }
    if (method === "openai_account.models.use") {
      return {
        type: "switched",
        current: params.model_id,
        requested: params.model_id,
        saved: true,
        applied: true,
      };
    }
    throw new Error(`unexpected prompted login method: ${method}`);
  },
  ["gpt-second"],
);
await createAuthCommand().action(promptedLoginContext.context, "login");
assert.equal(promptedLoginContext.questions.length, 1);
assert.equal(promptedLoginContext.questions[0].source, "openai_account_model");
assert.deepEqual(
  promptedLoginContext.questions[0].questionItems[0].options.map((option) => option.label),
  ["Keep current model", "gpt-first", "gpt-second"],
);
assert.deepEqual(promptedLoginContext.selectedModels, ["gpt-second"]);

let catalogModels = [
  {
    name: "gpt-first",
    model_name: "gpt-first",
    model_provider: "OpenAIAccount",
    api_base: "https://example.test/codex",
    is_current: true,
  },
];
const catalogContext = createAuthTestContext(
  async (method, params) => {
    if (method === "openai_account.models.list") {
      return {
        models: ["gpt-first", "gpt-second"],
        base_url: "https://example.test/codex",
        auth: { authenticated: true },
      };
    }
    if (method === "command.model" && !params.action && !params.model) {
      return {
        current: catalogModels[0]?.name,
        models: catalogModels,
        available_models: catalogModels.map((model) => model.name),
      };
    }
    if (method === "openai_account.models.use") {
      return {
        type: "switched",
        current: params.model_id,
        requested: params.model_id,
        saved: true,
        applied: true,
      };
    }
    throw new Error(`unexpected catalog method: ${method}`);
  },
  ["gpt-second"],
);
await createAuthCommand().action(catalogContext.context, "models");
assert.equal(catalogContext.questions.length, 1);
assert.deepEqual(catalogContext.selectedModels, ["gpt-second"]);

let blockedModelSwitch = false;
const loggedOutModelContext = createAuthTestContext(async (method, params) => {
  if (method === "command.model" && !params.model) {
    return {
      current: "gpt-test",
      available_models: ["video", "gpt-test"],
      models: [
        {
          name: "video",
          model_name: "video",
          model_provider: "OpenAI",
          is_current: false,
        },
        {
          name: "gpt-test",
          model_name: "gpt-test",
          model_provider: "OpenAIAccount",
          is_current: true,
        },
      ],
    };
  }
  if (method === "openai_account.auth.status") return { authenticated: false };
  if (method === "command.model" && params.model) {
    blockedModelSwitch = true;
    return { current: params.model, requested: params.model };
  }
  throw new Error(`unexpected logged-out model method: ${method}`);
});
await createModelCommand().action(loggedOutModelContext.context, "list");
assert.equal(
  loggedOutModelContext.items.some((item) =>
    item.meta?.items?.some((entry) => String(entry.value).includes("login required")),
  ),
  true,
);
await createModelCommand().action(loggedOutModelContext.context, "gpt-test");
assert.equal(blockedModelSwitch, false);
assert.equal(
  loggedOutModelContext.items.some((item) =>
    String(item.content).includes("Run /auth login first"),
  ),
  true,
);

let manualOAuthAddRequested = false;
const manualOAuthAddContext = createAuthTestContext(async () => {
  manualOAuthAddRequested = true;
  return { type: "model_added", saved: true, applied: true };
});
await createModelCommand().action(
  manualOAuthAddContext.context,
  "add oauth model_name=gpt-test api_base=https://example.test model_provider=OpenAIAccount",
);
assert.equal(manualOAuthAddRequested, false);
assert.equal(
  manualOAuthAddContext.items.some((item) =>
    String(item.content).includes("managed through /auth login or /auth models"),
  ),
  true,
);

const savedOnlyModelContext = createAuthTestContext(async (method, params) => {
  if (method === "command.model" && !params.model) {
    return {
      current: "old",
      models: [
        {
          name: "next",
          model_name: "next",
          model_provider: "OpenAI",
          is_current: false,
        },
      ],
    };
  }
  if (method === "command.model" && params.model === "next") {
    return {
      type: "switched",
      current: "next",
      requested: "next",
      saved: true,
      applied: false,
      apply_error: "Model configuration was saved but not applied; restart or retry.",
    };
  }
  throw new Error(`unexpected saved-only model method: ${method}`);
});
await createModelCommand().action(savedOnlyModelContext.context, "next");
assert.deepEqual(savedOnlyModelContext.selectedModels, []);
assert.equal(
  savedOnlyModelContext.items.some((item) =>
    String(item.content).includes("saved but not applied"),
  ),
  true,
);

console.log("frontend tests passed");
