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
const cnPlanQuestion = "**计划审批**\n\nAgent 已完成计划制定，等待你审批。";
const toolQuestion = "**Tool `bash` requires your approval**";

assert.equal(isPlanApprovalRequest("confirm_interrupt", planQuestion), true);
assert.equal(isPlanApprovalRequest("confirm_interrupt", cnPlanQuestion), true);
assert.equal(isPlanApprovalRequest("confirm_interrupt", toolQuestion), false);

assert.equal(getPendingQuestionTitle("confirm_interrupt", planQuestion, "", 0, 1), "Exit Plan and Execute:");
assert.equal(getPendingQuestionTitle("confirm_interrupt", toolQuestion, "", 0, 1), "Confirm action");

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

assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", planQuestion, "拒绝"), true);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", planQuestion, "Reject"), true);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", planQuestion, "本次允许"), false);
assert.equal(shouldCollectPlanRejectFeedback("confirm_interrupt", toolQuestion, "拒绝"), false);
assert.equal(shouldAppendPlanRejectFeedback("confirm_interrupt", planQuestion, "拒绝"), true);
assert.equal(shouldAppendPlanRejectFeedback("confirm_interrupt", planQuestion, "本次允许"), false);

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

console.log("frontend tests passed");
