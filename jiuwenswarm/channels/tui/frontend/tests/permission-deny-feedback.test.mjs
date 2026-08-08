import assert from "node:assert/strict";

import {
  AppScreen,
  shouldCollectPermissionDenyFeedback,
  shouldCollectPlanRejectFeedback,
  shouldRoutePermissionPromptEscape,
} from "../dist/ui/app-screen.js";

assert.equal(
  shouldCollectPermissionDenyFeedback("permission_interrupt", "拒绝"),
  true,
);
assert.equal(
  shouldCollectPermissionDenyFeedback("confirm_interrupt", "Reject"),
  true,
);
assert.equal(
  shouldCollectPermissionDenyFeedback("confirm_interrupt", "拒绝", "plan_approval"),
  false,
  "plan approval must keep using the plan helper, not this one",
);
assert.equal(
  shouldCollectPlanRejectFeedback("confirm_interrupt", "拒绝", "plan_approval"),
  true,
);
assert.equal(
  shouldCollectPermissionDenyFeedback("permission_interrupt", "Allow once"),
  false,
);
assert.equal(
  shouldCollectPermissionDenyFeedback("ask_user_interrupt", "Reject"),
  false,
);
assert.equal(shouldRoutePermissionPromptEscape("permission_interrupt"), true);
assert.equal(shouldRoutePermissionPromptEscape("confirm_interrupt"), true);
assert.equal(
  shouldRoutePermissionPromptEscape("confirm_interrupt", "plan_approval"),
  false,
);
assert.equal(shouldRoutePermissionPromptEscape("ask_user_interrupt"), false);

async function submitPermissionDeny(feedback) {
  const submitted = [];
  const pendingQuestion = {
    requestId: "permission-deny",
    source: "permission_interrupt",
    questions: [
      {
        header: "Permission required",
        question: "Allow Bash?",
        options: [{ label: "本次允许" }, { label: "拒绝" }],
      },
    ],
  };
  const screen = Object.create(AppScreen.prototype);
  Object.assign(screen, {
    activeQuestionIndex: 0,
    pendingQuestionAnswers: new Map(),
    pendingMultiSelectAnswers: new Map(),
    pendingQuestionCustomInputs: new Map(),
    questionList: { getSelectedItem: () => ({ value: "拒绝" }) },
    questionCheckboxList: null,
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

  screen.handleQuestionSelection("拒绝");
  assert.equal(screen.otherInputMode, true);
  assert.equal(screen.questionList, null);
  assert.equal(submitted.length, 0);

  await screen.handleSubmit(feedback);
  return submitted[0];
}

assert.deepEqual(await submitPermissionDeny("use Read instead"), [
  {
    question: "Allow Bash?",
    selected_options: ["拒绝"],
    custom_input: "use Read instead",
  },
]);
assert.deepEqual(await submitPermissionDeny(""), [
  {
    question: "Allow Bash?",
    selected_options: ["拒绝"],
    custom_input: "",
  },
]);

// Esc / confirm:no must still deny immediately without opening the note editor,
// even though Enter on the same "拒绝" option collects optional feedback (#1).
function submitImmediateDeny() {
  const submitted = [];
  const pendingQuestion = {
    requestId: "permission-deny-esc",
    source: "permission_interrupt",
    questions: [
      {
        header: "Permission required",
        question: "Allow Bash?",
        options: [{ label: "本次允许" }, { label: "拒绝" }],
      },
    ],
  };
  const screen = Object.create(AppScreen.prototype);
  Object.assign(screen, {
    activeQuestionIndex: 0,
    pendingQuestionAnswers: new Map(),
    pendingMultiSelectAnswers: new Map(),
    pendingQuestionCustomInputs: new Map(),
    questionList: { getSelectedItem: () => ({ value: "拒绝" }) },
    questionCheckboxList: null,
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

  screen.handleQuestionSelection("拒绝", true);
  return { submitted, screen };
}

{
  const { submitted, screen } = submitImmediateDeny();
  assert.equal(screen.otherInputMode, false, "Esc-deny must not open the note editor");
  assert.equal(submitted.length, 1, "Esc-deny must submit immediately");
  assert.deepEqual(submitted[0], [
    {
      question: "Allow Bash?",
      selected_options: ["拒绝"],
    },
  ]);
}

function makePermissionPromptScreen({ submitted, pendingQuestion, overrides = {} }) {
  const screen = Object.create(AppScreen.prototype);
  Object.assign(screen, {
    activeQuestionIndex: 0,
    pendingQuestionAnswers: new Map(),
    pendingMultiSelectAnswers: new Map(),
    pendingQuestionCustomInputs: new Map(),
    questionCheckboxList: null,
    otherInputMode: false,
    configEditorState: null,
    modelList: null,
    composerAttachments: [],
    startupPromptList: null,
    fileViewerState: null,
    diffViewerState: null,
    transientNotice: null,
    expandPastedText: (text) => text,
    buildOutgoingMessage: (text) => ({ content: text, attachments: [] }),
    setMouseTrackingEnabled: () => undefined,
    syncEditorSubmitState: () => undefined,
    syncQuestionList: () => undefined,
    editor: { setText: () => undefined, getText: () => "", handleInput: () => undefined },
    state: {
      recordActivity: () => undefined,
      getSnapshot: () => ({ pendingQuestion }),
      submitQuestionAnswers: (answers) => submitted.push(answers),
    },
    tui: { requestRender: () => undefined },
    handleQuestionSelection: AppScreen.prototype.handleQuestionSelection,
    handlePendingQuestionInput: AppScreen.prototype.handlePendingQuestionInput,
    handleInput: AppScreen.prototype.handleInput,
    ...overrides,
  });
  return screen;
}

// Esc on the permission select list must deny immediately (not interruptTask).
{
  const submitted = [];
  const pendingQuestion = {
    requestId: "permission-deny-esc-key",
    source: "permission_interrupt",
    questions: [
      {
        header: "Permission required",
        question: "Allow Bash?",
        options: [{ label: "本次允许" }, { label: "拒绝" }],
      },
    ],
  };
  let interruptCount = 0;
  const screen = makePermissionPromptScreen({ submitted, pendingQuestion, overrides: {
    interruptTask: () => {
      interruptCount += 1;
    },
    questionList: {
      handleInput(data) {
        if (data === "\x1b") {
          this.onCancel();
        }
      },
      getSelectedItem: () => ({ value: "拒绝" }),
      onCancel: null,
    },
  } });
  screen.questionList.onCancel = () => {
    const reject = pendingQuestion.questions[0].options.find((option) => option.label === "拒绝");
    screen.handleQuestionSelection.call(screen, reject.label, true);
  };

  screen.handleInput("\x1b");
  assert.equal(interruptCount, 0, "Esc on permission prompt must not interrupt the task");
  assert.equal(screen.otherInputMode, false);
  assert.equal(submitted.length, 1);
  assert.deepEqual(submitted[0], [
    {
      question: "Allow Bash?",
      selected_options: ["拒绝"],
    },
  ]);
}

// Esc in the deny-note editor must return to the option list, not interrupt.
{
  const submitted = [];
  const pendingQuestion = {
    requestId: "permission-deny-esc-back",
    source: "permission_interrupt",
    questions: [
      {
        header: "Permission required",
        question: "Allow Bash?",
        options: [{ label: "本次允许" }, { label: "拒绝" }],
      },
    ],
  };
  let interruptCount = 0;
  let syncQuestionListCount = 0;
  const screen = makePermissionPromptScreen({ submitted, pendingQuestion, overrides: {
    interruptTask: () => {
      interruptCount += 1;
    },
    otherInputMode: true,
    questionList: null,
    syncQuestionList: () => {
      syncQuestionListCount += 1;
    },
  } });

  screen.handleInput("\x1b");
  assert.equal(interruptCount, 0, "Esc in deny-note editor must not interrupt the task");
  assert.equal(screen.otherInputMode, false);
  assert.equal(syncQuestionListCount, 1);
  assert.equal(submitted.length, 0, "Esc back must not submit a deny answer yet");
}

console.log("permission deny feedback tests passed");
