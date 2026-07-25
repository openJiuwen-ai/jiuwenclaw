import assert from "node:assert/strict";

import {
  AppScreen,
  buildPlanApprovalQuestionItems,
  formatQuestionOptionLabelForDisplay,
  getPendingQuestionTitle,
  getPlanApprovalListLayout,
  getPlanRejectFeedbackHint,
  isPlanApprovalRequest,
  renderWrappedQuestionOptions,
  shouldCaptureTerminalMouse,
  shouldAppendPlanRejectFeedback,
  shouldCollectPlanRejectFeedback,
  wrapPlainText,
} from "../dist/ui/app-screen.js";
import { CheckboxList } from "../dist/ui/components/checkbox-list.js";
import { visibleWidth } from "@mariozechner/pi-tui";
import { planSwarmflowToggle } from "../dist/core/commands/builtins/swarmflow.js";
import { buildAppScreenLines } from "../dist/ui/screen-layout.js";
import {
  canOpenSessionHistory,
  groupWorkflowAgentsByName,
  isSessionNode,
  shouldShowSessionTree,
  shouldShowTurnInDetailOrReply,
  sessionTurnLabelNumber,
} from "../dist/core/workflows.js";
import { CommandKind } from "../dist/core/commands/types.js";

const planQuestion = "**Plan Approval**\n\nThe agent has completed a plan.";
const planApprovalKind = "plan_approval";

assert.equal(isPlanApprovalRequest("confirm_interrupt", planApprovalKind), true);
assert.equal(isPlanApprovalRequest("confirm_interrupt", "permission"), false);
assert.equal(isPlanApprovalRequest("permission_interrupt", planApprovalKind), false);

assert.equal(getPendingQuestionTitle("confirm_interrupt", "", 0, 1, planApprovalKind), "Exit Plan and Execute:");
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
assert.equal(
  getPlanRejectFeedbackHint("use pytest", true, 4),
  "[ use \x1b[7m \x1b[0mpytest ]",
);

assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "拒绝", planApprovalKind), true);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "Reject", planApprovalKind), true);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "本次允许", planApprovalKind), false);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", "拒绝", "permission"), false);
assert.equal(shouldAppendPlanRejectFeedback("confirm_interrupt", "拒绝", planApprovalKind), true);
assert.equal(shouldAppendPlanRejectFeedback("confirm_interrupt", "本次允许", planApprovalKind), false);

assert.deepEqual(
  buildPlanApprovalQuestionItems([
    { label: "本次允许", description: "仅本次授权执行" },
    { label: "总是允许", description: "记住该规则，以后自动放行" },
    { label: "拒绝", description: "拒绝执行此工具" },
  ], "", false),
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
assert.deepEqual(getPlanApprovalListLayout(), { minPrimaryColumnWidth: 10, maxPrimaryColumnWidth: 10 });

const narrowQuestionTitle =
  "[Redis 方案] Redis 接入有三种方案，范围和依赖递增。请根据当前项目选择。";
const wrappedQuestionTitle = wrapPlainText(narrowQuestionTitle, 30);
assert.ok(wrappedQuestionTitle.length > 1);
assert.ok(wrappedQuestionTitle.every((line) => visibleWidth(line) <= 29));
assert.equal(
  wrappedQuestionTitle.join("").replace(/\s/g, ""),
  narrowQuestionTitle.replace(/\s/g, ""),
);

const wrappedQuestionOptions = renderWrappedQuestionOptions(
  [
    {
      value: "session",
      label: "方案 A：仅 session",
      description: "依赖 ioredis 与 express-session，保留完整说明不得截断",
    },
    {
      value: "global",
      label: "方案 B：全量",
      description: "增加限流缓存以及额外响应缓存",
    },
  ],
  0,
  2,
  36,
);
assert.ok(wrappedQuestionOptions.lines.length > 2);
assert.ok(wrappedQuestionOptions.lines.every((line) => visibleWidth(line) <= 36));
assert.ok(
  wrappedQuestionOptions.lines
    .join("")
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\s/g, "")
    .includes("保留完整说明不得截断"),
);
assert.ok(wrappedQuestionOptions.selectedEndIndex > 1);

const narrowCheckboxList = new CheckboxList(
  [
    {
      name: "启用哪些功能模块",
      items: [
        {
          label: "auth",
          value: "auth",
          checked: false,
          description: "认证模块，处理用户登录、权限验证以及完整审计记录",
        },
      ],
    },
  ],
  1,
);
const narrowCheckboxLines = narrowCheckboxList.render(32);
assert.ok(narrowCheckboxLines.every((line) => visibleWidth(line) <= 32));
assert.ok(
  narrowCheckboxLines
    .join("")
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\s/g, "")
    .includes("完整审计记录"),
);

// A long/scrollable transcript must not capture drag events: users should be
// able to select and copy completed responses with the terminal's native UI.
assert.equal(shouldCaptureTerminalMouse(false, false), false);
assert.equal(shouldCaptureTerminalMouse(true, false), true);
assert.equal(shouldCaptureTerminalMouse(false, true), true);

const teamSnapshot = {
  connectionStatus: "connected",
  sessionId: "team-session",
  mode: "code.normal",
  themeName: "default",
  accentColor: "blue",
  transcriptMode: "compact",
  transcriptFoldMode: "none",
  collapsedToolGroupIds: new Set(),
  entries: [],
  toolExecutions: [],
  streamingState: "idle",
  pendingQuestion: null,
  lastError: null,
  isProcessing: false,
  cancellableWork: false,
  isPaused: false,
  isInterrupted: false,
  activeSubtasks: [],
  todos: [],
  teamMemberEvents: [
    {
      id: "member-ready",
      type: "team.member.status_changed",
      teamId: "team-1",
      memberId: "member-1",
      newStatus: "idle",
      timestamp: Date.now(),
    },
  ],
  teamTaskEvents: [],
  teamMessageEvents: [],
  workflowRuns: [],
  pendingHumanPrompts: new Map(),
  evolutionStatus: "idle",
  contextCompression: null,
  contextWindowLimit: null,
  contextUsedPercentage: null,
  modelInfo: { provider: "", model: "", version: "" },
  preferredLanguage: "zh",
  sessionTitle: "",
  statusLineText: null,
  memoryWarnings: [],
  runningCommand: null,
  streamStalled: false,
  streamIdleMs: null,
  currentQueryUsage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
  btwOverlay: null,
  btwOverlayIndex: -1,
  btwOverlayTotal: 0,
  btwActive: false,
};
const teamLayoutOptions = {
  width: 80,
  questionLines: [],
  editorLines: [],
  composerPreviewLines: [],
  showFullThinking: false,
  showToolDetails: false,
  showShortcutHelp: false,
  todosCollapsed: false,
  showTeamPanel: false,
  selectedTeamMemberId: "member-1",
  viewedTeamMemberId: null,
  transientNotice: null,
  animationPhase: 0,
  overlayTranscriptLines: [],
};
const collapsedTeamLines = buildAppScreenLines(teamSnapshot, teamLayoutOptions);
assert.equal(collapsedTeamLines.some((line) => line.includes("teammate")), false);
assert.equal(collapsedTeamLines.some((line) => line.includes("Member 1")), false);

const expandedTeamLines = buildAppScreenLines(teamSnapshot, {
  ...teamLayoutOptions,
  showTeamPanel: true,
});
assert.equal(expandedTeamLines.some((line) => line.includes("teammate")), true);

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

async function submitMultiSelectOther(selectedValues, customInput) {
  const submitted = [];
  const pendingQuestion = {
    requestId: "multi-select-other",
    source: "ask_user_interrupt",
    questions: [
      {
        header: "Modules",
        question: "Which modules?",
        multiSelect: true,
        options: [
          { label: "auth" },
          { label: "log" },
          { label: "Other" },
        ],
      },
    ],
  };
  const screen = Object.create(AppScreen.prototype);
  Object.assign(screen, {
    activeQuestionIndex: 0,
    pendingQuestionAnswers: new Map(),
    pendingMultiSelectAnswers: new Map(),
    pendingQuestionCustomInputs: new Map(),
    questionList: null,
    questionCheckboxList: { handleInput: () => undefined },
    otherInputMode: false,
    configEditorState: null,
    modelList: null,
    composerAttachments: [],
    expandPastedText: (text) => text,
    buildOutgoingMessage: (text) => ({ content: text, attachments: [] }),
    setMouseTrackingEnabled: () => undefined,
    syncEditorSubmitState: () => undefined,
    editor: { setText: () => undefined },
    state: {
      recordActivity: () => undefined,
      getSnapshot: () => ({ pendingQuestion }),
      submitQuestionAnswers: (answers) => submitted.push(answers),
    },
    tui: { requestRender: () => undefined },
  });

  screen.handleMultiSelectConfirm(selectedValues);
  assert.equal(screen.otherInputMode, true);
  assert.equal(screen.questionCheckboxList, null);
  assert.equal(submitted.length, 0);

  await screen.handleSubmit(customInput);
  return submitted[0];
}

assert.deepEqual(
  await submitMultiSelectOther(["Other"], "metrics"),
  [
    {
      question: "Which modules?",
      selected_options: ["Other"],
      custom_input: "metrics",
    },
  ],
);
assert.deepEqual(
  await submitMultiSelectOther(["auth", "Other"], "metrics"),
  [
    {
      question: "Which modules?",
      selected_options: ["auth", "Other"],
      custom_input: "metrics",
    },
  ],
);

// No "Other" selected: must not enter the free-text input mode, and must submit
// immediately without a custom_input field.
function submitMultiSelectNoOther(selectedValues) {
  const submitted = [];
  const pendingQuestion = {
    requestId: "multi-select-no-other",
    source: "ask_user_interrupt",
    questions: [
      {
        header: "Modules",
        question: "Which modules?",
        multiSelect: true,
        options: [{ label: "auth" }, { label: "log" }, { label: "Other" }],
      },
    ],
  };
  const screen = Object.create(AppScreen.prototype);
  Object.assign(screen, {
    activeQuestionIndex: 0,
    pendingQuestionAnswers: new Map(),
    pendingMultiSelectAnswers: new Map(),
    pendingQuestionCustomInputs: new Map(),
    questionList: null,
    questionCheckboxList: { handleInput: () => undefined },
    otherInputMode: false,
    configEditorState: null,
    modelList: null,
    composerAttachments: [],
    expandPastedText: (text) => text,
    buildOutgoingMessage: (text) => ({ content: text, attachments: [] }),
    setMouseTrackingEnabled: () => undefined,
    syncEditorSubmitState: () => undefined,
    syncQuestionList: () => undefined,
    editor: { setText: () => undefined },
    state: {
      recordActivity: () => undefined,
      getSnapshot: () => ({ pendingQuestion }),
      submitQuestionAnswers: (answers) => submitted.push(answers),
    },
    tui: { requestRender: () => undefined },
  });

  screen.handleMultiSelectConfirm(selectedValues);
  assert.equal(screen.otherInputMode, false);
  assert.equal(submitted.length, 1);
  return submitted[0];
}

assert.deepEqual(submitMultiSelectNoOther(["auth", "log"]), [
  {
    question: "Which modules?",
    selected_options: ["auth", "log"],
  },
]);

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

console.log("frontend tests passed");
