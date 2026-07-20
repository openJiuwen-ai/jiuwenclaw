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

console.log("frontend tests passed");
