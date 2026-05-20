/**
 * SkillDevPanel - Skill 开发模式主面板
 *
 * 功能:
 * - 聊天界面（需求输入、进度展示）
 * - Todo 列表（阶段进度）
 * - 产物列表（下载）
 * - 文件浏览器
 * - 确认弹窗（计划确认、评测审阅、描述优化）
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillDevStore } from '../../stores';
import { webClient } from '../../services/webClient';
import {
  startSkillDev,
  respondSkillDev,
  listSkillDevSessions,
  restoreSkillDevSession,
  getSkillDevFileTree,
  readSkillDevFile,
  downloadSkillDevArtifact,
  triggerDownload,
  cancelSkillDev,
  parseSkillPackage,
} from '../../services/skillDevService';
import type {
  SkillDevTodo,
  SkillDevArtifact,
  SkillDevPlan,
  ConfirmRequest,
  ClarifyAnswer,
  ClarifyQuestion,
  SkillDevSessionSummary,
  SkillDevRestoreResult,
  SkillDevRestoreTimelineItem,
  SkillDevRestoreSnapshot,
} from '../../types/skilldev';
import type { MediaItem, ToolExecution, AskUserQuestionPayload, UserAnswer } from '../../types';

// 子组件
import { SkillDevChat } from './SkillDevChat';
import { SkillDevTodos } from './SkillDevTodos';
import { SkillDevArtifacts } from './SkillDevArtifacts';
import { SkillDevFileBrowser } from './SkillDevFileBrowser';
import { SkillDevSessionsTab } from './SkillDevSessionsTab';

/** 恢复后的会话是否允许展示「继续任务」（续跑 skilldev.start） */
function skillDevResumeEligible(snapshot: SkillDevRestoreSnapshot): boolean {
  // 只有真正等待用户确认（存在 pending_confirm）时，不展示“继续任务”
  if (snapshot.is_suspended && snapshot.pending_confirm) return false;
  const st = String(snapshot.stage);
  if (st === 'completed' || st === 'error') return false;
  return true;
}

export function SkillDevPanel() {
  const { t } = useTranslation();

  // 本地状态
  const [inputValue, setInputValue] = useState('');
  const [activeTab, setActiveTab] = useState<'chat' | 'sessions' | 'files'>('chat');
  const [sessionList, setSessionList] = useState<SkillDevSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [restoringTaskId, setRestoringTaskId] = useState<string | null>(null);
  /** 恢复会话后是否展示「继续任务」（skilldev.start 续跑） */
  const [showResumeBanner, setShowResumeBanner] = useState(false);
  const resumeQueryRef = useRef('');
  const fileBrowserLoaded = useRef(false);
  const restoreGenerationRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  const testProgressIdRef = useRef<string | null>(null);
  const hasTaskStartedRef = useRef(false);
  // TEST_RUN 阶段：stage key → message ID 映射，用于将 AGENT_OUTPUT 路由到对应 case 卡片
  const caseCardIdMapRef = useRef<Map<string, string>>(new Map());
  /** 终止后为 true，忽略迟到的流式事件（如 ask_user_question / confirm_request） */
  const taskCancelledRef = useRef(false);

  // Store 状态
  const {
    taskId,
    stage,
    todos,
    artifacts,
    messages,
    toolExecutions,
    toolExecutionOrder,
    isProcessing,
    isSuspended,
    fileTree,
    currentFile,
    clarifyQuestions,
    isClarifySubmitted,
    addMessage,
    addThinkingMessage,
    addOutputMessage,
    addToolCall,
    addToolResult,
    setTaskId,
    setStage,
    setQuery,
    setTodos,
    setArtifacts,
    setProcessing,
    setSuspended,
    setError,
    setFileTree,
    setCurrentFile,
    setClarifyQuestions,
    setClarifySubmitted,
    pendingQuestion,
    setPendingQuestion,
    updateMessage,
    appendToAgentRunCard,
    finalizeAgentRunCard,
    reset,
    // 审批相关 action
    addConfirmRequestMessage,
    resolveConfirmMessage,
    endThinkingStream,
    hydrateFromSkillDevRestore,
    dismissPendingInteraction,
  } = useSkillDevStore();

  // ========== 事件处理 ==========
  const eventTimestamp = (data: unknown): string => {
    if (data && typeof data === 'object') {
      const maybe = (data as Record<string, unknown>).__restore_ts;
      if (typeof maybe === 'string' && maybe.trim()) {
        return maybe;
      }
    }
    return new Date().toISOString();
  };

  const getOrCreateSessionId = useCallback(() => {
    if (!sessionIdRef.current) {
      sessionIdRef.current = crypto.randomUUID();
    }
    return sessionIdRef.current;
  }, []);

  const handleStarted = useCallback((data: { task_id: string; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    taskCancelledRef.current = false;
    sessionIdRef.current = data.task_id;
    hasTaskStartedRef.current = true;
    setTaskId(data.task_id);
    setProcessing(true);
    setSuspended(false);
    addMessage({
      id: `system-${Date.now()}-${data.task_id}`,
      role: 'system',
      content: t('skilldev.taskStarted', { taskId: data.task_id }),
      type: 'text',
      timestamp: ts,
    });
  }, [setTaskId, setProcessing, setSuspended, addMessage, t]);

  // ========== 事件处理 ==========

  const handleStageChanged = useCallback((data: { stage: string; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    testProgressIdRef.current = null;
    caseCardIdMapRef.current.clear();
    endThinkingStream();
    setStage(data.stage as typeof stage);
    addMessage({
      id: `stage-${Date.now()}`,
      role: 'system',
      content: t('skilldev.stageChanged', { stage: data.stage }),
      type: 'progress',
      timestamp: ts,
      metadata: { stage: data.stage },
    });
  }, [setStage, addMessage, t, endThinkingStream]);

  const handleProgress = useCallback((data: {
    message: string;
    eval_name?: string;
    variant?: string;
    case_done?: boolean;
    completed?: number;
    total?: number;
    __restore_ts?: string;
  }) => {
    const ts = eventTimestamp(data);
    // EVALUATE 阶段 grader case 开始信号
    if (data.eval_name && data.variant && !data.case_done) {
      const stageKey = `evaluate_grader/${data.eval_name}/${data.variant}`;
      if (!caseCardIdMapRef.current.has(stageKey)) {
        const cardId = `eval-run-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        caseCardIdMapRef.current.set(stageKey, cardId);
        addMessage({
          id: cardId,
          role: 'assistant',
          content: '',
          type: 'agent_run_card',
          timestamp: ts,
          isStreaming: true,
          metadata: {
            caseKey: stageKey,
            caseName: data.eval_name,
            caseVariant: data.variant,
            caseStatus: 'running',
            caseOutput: '',
          },
        });
      }
      return;
    }

    // EVALUATE 阶段 grader case 完成信号
    if (data.eval_name && data.variant && data.case_done) {
      const stageKey = `evaluate_grader/${data.eval_name}/${data.variant}`;
      const cardId = caseCardIdMapRef.current.get(stageKey);
      if (cardId) {
        finalizeAgentRunCard(cardId, 'success');
      }
      return;
    }

    // 普通进度消息
    addMessage({
      id: `progress-${Date.now()}`,
      role: 'assistant',
      content: data.message,
      type: 'progress',
      timestamp: ts,
    });
  }, [addMessage, finalizeAgentRunCard]);

  const handleAgentThinking = useCallback((data: { delta: string; stage?: string; __restore_ts?: string }) => {
    addThinkingMessage(data.delta, { timestamp: eventTimestamp(data) });
  }, [addThinkingMessage]);

  const handleAgentOutput = useCallback((data: { delta: string; stage?: string; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    const stage = data.stage;

    // TEST_RUN / EVALUATE grader 子 Agent：统一路由逻辑
    // 两个阶段 stage 格式均为 "{prefix}/{caseName}/{variant}[/retry{N}]"
    // 取前三段作为 canonical key，自动忽略 retry 后缀
    if (stage?.startsWith('test_run/') || stage?.startsWith('evaluate_grader/')) {
      const parts = stage.split('/');
      const canonicalKey = parts.slice(0, 3).join('/');
      const cardId = caseCardIdMapRef.current.get(canonicalKey);
      if (cardId) {
        appendToAgentRunCard(cardId, data.delta);
        return;
      }
      // 防御兜底：start 信号应先于 output 到达，若未到则懒创建
      if (parts.length >= 3) {
        const caseName = parts[1];
        const caseVariant = parts[2];
        const newCardId = `agent-run-${Date.now()}`;
        caseCardIdMapRef.current.set(canonicalKey, newCardId);
        addMessage({
          id: newCardId,
          role: 'assistant',
          content: '',
          type: 'agent_run_card',
          timestamp: ts,
          isStreaming: true,
          metadata: { caseKey: canonicalKey, caseName, caseVariant, caseStatus: 'running', caseOutput: '' },
        });
        appendToAgentRunCard(newCardId, data.delta);
      }
      return;
    }

    addOutputMessage(data.delta, { timestamp: ts });
  }, [addOutputMessage, appendToAgentRunCard, addMessage]);

  const handleTestProgress = useCallback((data: {
    message: string;
    case_name?: string;
    variant?: string;
    case_status?: string;
    prompt?: string;
    total?: number;
    completed?: number;
    __restore_ts?: string;
  }) => {
    const ts = eventTimestamp(data);
    // case 开始信号：有 case_name + variant，但没有 case_status
    if (data.case_name && data.variant && !data.case_status) {
      const stageKey = `test_run/${data.case_name}/${data.variant}`;
      if (!caseCardIdMapRef.current.has(stageKey)) {
        const cardId = `agent-run-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        caseCardIdMapRef.current.set(stageKey, cardId);
        addMessage({
          id: cardId,
          role: 'assistant',
          content: '',
          type: 'agent_run_card',
          timestamp: ts,
          isStreaming: true,
          metadata: {
            caseKey: stageKey,
            caseName: data.case_name,
            caseVariant: data.variant,
            caseStatus: 'running',
            caseOutput: '',
            casePrompt: data.prompt ?? '',
          },
        });
      }
      return;
    }

    // case 完成信号：有 case_name + variant + case_status
    if (data.case_name && data.variant && data.case_status) {
      const stageKey = `test_run/${data.case_name}/${data.variant}`;
      const cardId = caseCardIdMapRef.current.get(stageKey);
      if (cardId) {
        finalizeAgentRunCard(cardId, data.case_status as 'success' | 'error' | 'timeout');
      }
      // 同步更新全局进度条
      const existingId = testProgressIdRef.current;
      if (existingId) {
        updateMessage(existingId, { content: data.message, timestamp: ts });
      }
      return;
    }

    // 全局进度（无 case_name）：更新或创建进度消息
    const existingId = testProgressIdRef.current;
    if (existingId) {
      updateMessage(existingId, { content: data.message, timestamp: ts });
      return;
    }
    const messageId = `test-${Date.now()}`;
    testProgressIdRef.current = messageId;
    addMessage({
      id: messageId,
      role: 'assistant',
      content: data.message,
      type: 'test_progress',
      timestamp: ts,
    });
  }, [addMessage, updateMessage, finalizeAgentRunCard]);

  const handleTodosUpdate = useCallback((data: { todos: SkillDevTodo[] }) => {
    setTodos(data.todos);
  }, [setTodos]);

  // Load file tree - defined early for use in handleArtifactReady
  const handleLoadFileTree = useCallback(async () => {
    if (!taskId || fileBrowserLoaded.current) return;

    try {
      const tree = await getSkillDevFileTree(taskId);
      setFileTree(tree);
      fileBrowserLoaded.current = true;
    } catch (err) {
      console.error('Failed to load file tree:', err);
    }
  }, [taskId, setFileTree]);

  const handleConfirmRequest = useCallback((data: ConfirmRequest & {
    __restore_ts?: string;
    __restore_id?: string;
    __restore_replay?: boolean;
    interactive?: boolean;
    resolved_action?: string;
    resolved_feedback?: string;
  }) => {
    const ts = eventTimestamp(data);
    const interactive = data.interactive !== false;
    if (data.confirm_type === 'question_clarify') {
      // 问题澄清以内联卡片形式展示在聊天流中
      if (interactive) {
        setClarifyQuestions(data.data.questions || []);
        setClarifySubmitted(false);
        setSuspended(true);
        setProcessing(false);
      }
    } else {
      // 其他确认类型：插入消息流卡片 + 保留弹窗兼容
      addConfirmRequestMessage(data, {
        timestamp: ts,
        id: data.__restore_id,
      });
      if (interactive) {
        setSuspended(true);
        setProcessing(false);
      } else if (data.__restore_replay && data.__restore_id) {
        resolveConfirmMessage(
          data.__restore_id,
          data.resolved_action || 'submit',
          data.resolved_feedback,
          { resolvedAt: ts, isSuspended: false, isProcessing: false }
        );
      }
    }
  }, [setClarifyQuestions, setClarifySubmitted, setSuspended, setProcessing, addConfirmRequestMessage, resolveConfirmMessage]);

  const handleArtifactReady = useCallback((data: { artifact: SkillDevArtifact; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    // Replace existing artifact of the same type, keeping only the latest
    const current = useSkillDevStore.getState().artifacts;
    const filtered = current.filter((a) => a.type !== data.artifact.type);
    setArtifacts([...filtered, data.artifact]);
    // Reset file browser cache and reload file tree when skill files are ready
    if (data.artifact.type === 'skill_md' || data.artifact.type === 'skill_package') {
      fileBrowserLoaded.current = false;
      // Immediately reload file tree to show new files
      void handleLoadFileTree();
    }
    addMessage({
      id: `artifact-${Date.now()}`,
      role: 'system',
      content: t('skilldev.artifactReady', { name: data.artifact.name }),
      type: 'text',
      timestamp: ts,
    });
  }, [setArtifacts, addMessage, t, handleLoadFileTree]);

  const handleSkillNameReady = useCallback((data: { skill_name?: string; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    const skillName = (data.skill_name || '').trim();
    if (!skillName) return;
    addMessage({
      id: `skillname-${Date.now()}`,
      role: 'system',
      content: `Skill 名称已确定：${skillName}`,
      type: 'skill_name_ready',
      timestamp: ts,
      metadata: { skill_name: skillName },
    });
  }, [addMessage]);

  const handleEvalReady = useCallback((data: {
    benchmark: unknown;
    iteration: number;
    report: string;
    __restore_ts?: string;
  }) => {
    const ts = eventTimestamp(data);
    addMessage({
      id: `eval-${Date.now()}`,
      role: 'assistant',
      content: data.report ?? '评测完成',   // 适配后端report markdown 内容
      type: 'eval_ready',
      timestamp: ts,
      metadata: { benchmark: data.benchmark, iteration: data.iteration },
    });
  }, [addMessage]);

  const handleValidateResult = useCallback((data: { valid: boolean; message?: string; __restore_ts?: string }) => {
    const ts = eventTimestamp(data);
    addMessage({
      id: `validate-${Date.now()}`,
      role: 'system',
      content: data.valid
        ? t('skilldev.validateSuccess')
        : t('skilldev.validateFailed', { errors: data.message }),
      type: 'validate_result',
      timestamp: ts,
      metadata: { valid: data.valid, message: data.message },
    });
  }, [addMessage, t]);

  const handleDescOptReady = useCallback(
    (data: {
      task_id?: string;
      /** 后端 desc_optimize_result */
      original_description?: string;
      best_description?: string;
      best_score?: string;
      iterations_run?: number;
      history?: unknown[];
      /** 兼容旧字段名 */
      before?: string;
      after?: string;
      __restore_ts?: string;
    }) => {
      const ts = eventTimestamp(data);
      const before = data.original_description ?? data.before ?? '';
      const after = data.best_description ?? data.after ?? '';
      const score = data.best_score ?? 'N/A';
      const iterations = data.iterations_run ?? 0;
      const unchanged = !after || before === after;

      const detail = unchanged
        ? t('skilldev.descOpt.unchanged')
        : [
            `**${t('skilldev.descOpt.before')}**`,
            '',
            '```text',
            before || ' ',
            '```',
            '',
            `**${t('skilldev.descOpt.after')}**`,
            '',
            '```text',
            after,
            '```',
          ].join('\n');

      const content = [
        `### ${t('skilldev.descOptReady')}`,
        '',
        `- ${t('skilldev.descOpt.score')}: ${score}`,
        `- ${t('skilldev.descOpt.iterations')}: ${iterations}`,
        '',
        detail,
      ].join('\n');

      addMessage({
        id: `descopt-${Date.now()}`,
        role: 'assistant',
        content,
        type: 'desc_opt_ready',
        timestamp: ts,
        metadata: {
          before,
          after,
          best_score: score,
          iterations_run: iterations,
          history: data.history,
        },
      });
    },
    [addMessage, t]
  );

  const handleError = useCallback((data: { error: string; __restore_ts?: string }) => {
    setError(data.error);
    setProcessing(false);
    addMessage({
      id: `error-${Date.now()}`,
      role: 'system',
      content: data.error,
      type: 'error',
      timestamp: eventTimestamp(data),
    });
  }, [setError, setProcessing, addMessage]);

  const handleSuspended = useCallback((data?: { __restore_ts?: string }) => {
    setSuspended(true);
    setProcessing(false);
    addMessage({
      id: `suspended-${Date.now()}`,
      role: 'system',
      content: t('skilldev.waitingForInput'),
      type: 'text',
      timestamp: eventTimestamp(data),
    });
  }, [setSuspended, setProcessing, addMessage, t]);

  const handleCompleted = useCallback((data?: { __restore_ts?: string }) => {
    endThinkingStream();
    setProcessing(false);
    setSuspended(false);
    // Mark all remaining in_progress todos as completed
    setTodos(
      useSkillDevStore.getState().todos.map((todo) =>
        todo.status === 'in_progress' ? { ...todo, status: 'completed' as const } : todo
      )
    );
    addMessage({
      id: `completed-${Date.now()}`,
      role: 'system',
      content: t('skilldev.taskCompleted'),
      type: 'text',
      timestamp: eventTimestamp(data),
    });
  }, [endThinkingStream, setProcessing, setSuspended, setTodos, addMessage, t]);

  const handleAgentCompleted = useCallback((data?: { __restore_ts?: string }) => {
    endThinkingStream();
    setProcessing(false);
    setSuspended(false);
  }, [endThinkingStream, setProcessing, setSuspended]);

  const handleToolCall = useCallback((data: {
    tool_call_id?: string;
    tool_name?: string;
    arguments?: Record<string, unknown>;
    stage?: string;
    [key: string]: unknown;
  }) => {
    // TEST_RUN / EVALUATE / DESC_OPTIMIZE 子 Agent 的工具调用不混入全局工具执行列表
    if (
      data.stage?.startsWith('test_run/')
      || data.stage?.startsWith('evaluate_')
      || data.stage?.startsWith('desc_optimize_eval/')
    ) return;
    const toolCallId = data.tool_call_id || `${Date.now()}`;
    const toolName = data.tool_name || 'unknown';
    addToolCall({
      id: toolCallId,
      name: toolName,
      arguments: data.arguments || {},
    }, { timestamp: eventTimestamp(data) });
  }, [addToolCall]);

  const handleToolResult = useCallback((data: {
    tool_call_id?: string;
    tool_name?: string;
    result?: string;
    success?: boolean;
    stage?: string;
    [key: string]: unknown;
  }) => {
    // TEST_RUN / EVALUATE / DESC_OPTIMIZE 子 Agent 的工具结果不混入全局工具执行列表
    if (
      data.stage?.startsWith('test_run/')
      || data.stage?.startsWith('evaluate_')
      || data.stage?.startsWith('desc_optimize_eval/')
    ) return;
    addToolResult({
      toolCallId: data.tool_call_id,
      toolName: data.tool_name || 'unknown',
      result: String(data.result ?? ''),
      success: Boolean(data.success ?? true),
    }, { timestamp: eventTimestamp(data) });
  }, [addToolResult]);

  const handleConfirmResolved = useCallback((data: {
    confirm_type?: string;
    action?: string;
    feedback?: string;
    confirm_seq?: number;
    __restore_ts?: string;
    __restore_replay?: boolean;
    answers?: ClarifyAnswer[];
  }) => {
    if (data.confirm_type === 'question_clarify') {
      setClarifyQuestions(null);
      setClarifySubmitted(true);
    }
    const messages = useSkillDevStore.getState().messages;
    const target = [...messages].reverse().find((msg) => {
      if (msg.type !== 'confirm_request' || !msg.metadata) return false;
      if (typeof data.confirm_seq === 'number') {
        return msg.id === `restore-confirm-${data.confirm_seq}`;
      }
      if (!data.confirm_type) return true;
      return msg.metadata.confirmType === data.confirm_type;
    });
    if (!target) return;
    resolveConfirmMessage(
      target.id,
      data.action || 'submit',
      data.feedback,
      {
        resolvedAt: eventTimestamp(data),
        isSuspended: false,
        isProcessing: !Boolean(data.__restore_replay),
      }
    );
  }, [resolveConfirmMessage, setClarifyQuestions, setClarifySubmitted]);

  const handleAskUserQuestion = useCallback((data: AskUserQuestionPayload) => {
    endThinkingStream();
    setPendingQuestion(data);
    setSuspended(true);
    setProcessing(false);
  }, [endThinkingStream, setPendingQuestion, setSuspended, setProcessing]);

  const handleSubmitUserAnswer = useCallback(
    async (requestId: string, answers: UserAnswer[], source?: string) => {
      try {
        setPendingQuestion(null);
        setSuspended(false);
        setProcessing(true);
        const sessionId = sessionIdRef.current ?? taskId ?? '';
        await webClient.request('skilldev.user_answer', {
          session_id: sessionId,
          request_id: requestId,
          answers,
          ...(source ? { source } : {}),
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : '提交回答失败');
        setProcessing(false);
      }
    },
    [taskId, setPendingQuestion, setSuspended, setProcessing, setError]
  );

  // 用 ref 保存最新 handler，避免事件监听因依赖变化而反复重注册导致事件丢失
  const handlersRef = useRef({
    handleStarted,
    handleStageChanged,
    handleProgress,
    handleAgentThinking,
    handleAgentOutput,
    handleTestProgress,
    handleTodosUpdate,
    handleConfirmRequest,
    handleConfirmResolved,
    handleArtifactReady,
    handleSkillNameReady,
    handleEvalReady,
    handleValidateResult,
    handleDescOptReady,
    handleError,
    handleSuspended,
    handleCompleted,
    handleAgentCompleted,
    handleToolCall,
    handleToolResult,
    handleAskUserQuestion,
  });
  useEffect(() => {
    handlersRef.current = {
      handleStarted, handleStageChanged, handleProgress, handleAgentThinking,
      handleAgentOutput, handleTestProgress, handleTodosUpdate, handleConfirmRequest,
      handleConfirmResolved,
      handleArtifactReady, handleSkillNameReady, handleEvalReady, handleValidateResult, handleDescOptReady,
      handleError, handleSuspended, handleCompleted, handleAgentCompleted, handleToolCall, handleToolResult,
      handleAskUserQuestion,
    };
  });

  // 注册事件监听 —— 只在 mount 时执行一次
  useEffect(() => {
    const events: Array<{ name: string; key: keyof typeof handlersRef.current }> = [
      { name: 'skilldev.started',        key: 'handleStarted' },
      { name: 'skilldev.stage_changed',  key: 'handleStageChanged' },
      { name: 'skilldev.progress',       key: 'handleProgress' },
      { name: 'skilldev.agent_thinking', key: 'handleAgentThinking' },
      { name: 'skilldev.agent_output',   key: 'handleAgentOutput' },
      { name: 'skilldev.test_progress',  key: 'handleTestProgress' },
      { name: 'skilldev.todos_update',   key: 'handleTodosUpdate' },
      { name: 'skilldev.confirm_request',key: 'handleConfirmRequest' },
      { name: 'skilldev.confirm_resolved', key: 'handleConfirmResolved' },
      { name: 'skilldev.artifact_ready', key: 'handleArtifactReady' },
      { name: 'skilldev.skill_name_ready', key: 'handleSkillNameReady' },
      { name: 'skilldev.eval_ready',     key: 'handleEvalReady' },
      { name: 'skilldev.validate_result',key: 'handleValidateResult' },
      { name: 'skilldev.desc_opt_ready', key: 'handleDescOptReady' },
      { name: 'skilldev.error',          key: 'handleError' },
      { name: 'skilldev.suspended',      key: 'handleSuspended' },
      { name: 'skilldev.completed',      key: 'handleCompleted' },
      { name: 'skilldev.agent_completed', key: 'handleAgentCompleted' },
      { name: 'skilldev.tool_call',      key: 'handleToolCall' },
      { name: 'skilldev.tool_result',    key: 'handleToolResult' },
      { name: 'skilldev.ask_user_question', key: 'handleAskUserQuestion' },
    ];

    const handleInterruptResult = (payload: unknown) => {
      const data = payload as { session_id?: string; intent?: string; success?: boolean };
      const sid = sessionIdRef.current ?? useSkillDevStore.getState().taskId;
      if (sid && data.session_id && data.session_id !== sid) {
        return;
      }
      if (data.intent === 'cancel' && data.success !== false) {
        taskCancelledRef.current = true;
        dismissPendingInteraction();
        setProcessing(false);
        setSuspended(false);
      }
    };

    const unsubs = events.map(({ name, key }) =>
      webClient.on(name, ({ payload }) => {
        if (taskCancelledRef.current) {
          return;
        }
        (handlersRef.current[key] as (d: unknown) => void)(payload);
      })
    );
    unsubs.push(
      webClient.on('chat.interrupt_result', ({ payload }) => {
        handleInterruptResult(payload);
      })
    );

    return () => unsubs.forEach((unsub) => unsub());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const replayRestoreTimeline = useCallback((timelineItems: SkillDevRestoreTimelineItem[]) => {
    const ordered = [...timelineItems].sort((a, b) => {
      if (a.seq !== b.seq) return a.seq - b.seq;
      return Date.parse(a.timestamp) - Date.parse(b.timestamp);
    });
    for (const item of ordered) {
      if (item.event_type === 'skilldev.user_start') {
        const query = typeof item.payload.query === 'string' ? item.payload.query : '';
        if (query.trim()) {
          addMessage({
            id: `restore-user-start-${item.seq}`,
            role: 'user',
            content: query,
            type: 'text',
            timestamp: item.timestamp,
          });
        }
        continue;
      }
      if (item.event_type === 'skilldev.user_respond') {
        // 保持与实时流程一致：respond 仅驱动状态流转，不额外渲染用户载荷文本
        continue;
      }

      const payload = {
        ...item.payload,
        __restore_ts: item.timestamp,
        __restore_replay: true,
      };
      switch (item.event_type) {
        case 'skilldev.started':
          handlersRef.current.handleStarted(payload as unknown as { task_id: string; __restore_ts?: string });
          break;
        case 'skilldev.stage_changed':
          handlersRef.current.handleStageChanged(payload as unknown as { stage: string; __restore_ts?: string });
          break;
        case 'skilldev.progress':
          handlersRef.current.handleProgress(payload as unknown as { message: string; __restore_ts?: string });
          break;
        case 'skilldev.agent_thinking':
          handlersRef.current.handleAgentThinking(payload as unknown as { delta: string; __restore_ts?: string });
          break;
        case 'skilldev.agent_output':
          handlersRef.current.handleAgentOutput(payload as unknown as { delta: string; stage?: string; __restore_ts?: string });
          break;
        case 'skilldev.test_progress':
          handlersRef.current.handleTestProgress(payload as unknown as { message: string; __restore_ts?: string });
          break;
        case 'skilldev.todos_update':
          handlersRef.current.handleTodosUpdate(payload as unknown as { todos: SkillDevTodo[] });
          break;
        case 'skilldev.confirm_request':
          const restoreConfirmPayload = payload as Record<string, unknown>;
          const confirmTypeRaw = String(restoreConfirmPayload.confirm_type ?? '');
          const confirmType: ConfirmRequest['confirm_type'] =
            confirmTypeRaw === 'question_clarify'
            || confirmTypeRaw === 'plan_confirm'
            || confirmTypeRaw === 'review'
            || confirmTypeRaw === 'desc_optimize_confirm'
            || confirmTypeRaw === 'skip_tests_confirm'
              ? confirmTypeRaw
              : 'plan_confirm';
          handlersRef.current.handleConfirmRequest({
            confirm_type: confirmType,
            title: String(restoreConfirmPayload.title ?? ''),
            message: String(restoreConfirmPayload.message ?? ''),
            actions: Array.isArray(restoreConfirmPayload.actions)
              ? (restoreConfirmPayload.actions as ConfirmRequest['actions'])
              : [],
            data: (restoreConfirmPayload.data && typeof restoreConfirmPayload.data === 'object')
              ? restoreConfirmPayload.data as Record<string, unknown>
              : {},
            __restore_id: `restore-confirm-${item.seq}`,
            __restore_ts: item.timestamp,
            __restore_replay: true,
            interactive: Boolean(restoreConfirmPayload.interactive ?? true),
            resolved_action: restoreConfirmPayload.resolved_action as string | undefined,
            resolved_feedback: restoreConfirmPayload.resolved_feedback as string | undefined,
          });
          break;
        case 'skilldev.confirm_resolved':
          handlersRef.current.handleConfirmResolved(payload as unknown as {
            confirm_type?: string;
            action?: string;
            feedback?: string;
            confirm_seq?: number;
            __restore_ts?: string;
            __restore_replay?: boolean;
          });
          break;
        case 'skilldev.artifact_ready':
          handlersRef.current.handleArtifactReady(payload as unknown as { artifact: SkillDevArtifact; __restore_ts?: string });
          break;
        case 'skilldev.skill_name_ready':
          handlersRef.current.handleSkillNameReady(payload as unknown as { skill_name?: string; __restore_ts?: string });
          break;
        case 'skilldev.eval_ready':
          handlersRef.current.handleEvalReady(payload as unknown as {
            benchmark: unknown;
            iteration: number;
            report: string;
            __restore_ts?: string;
          });
          break;
        case 'skilldev.validate_result':
          handlersRef.current.handleValidateResult(payload as unknown as { valid: boolean; message?: string; __restore_ts?: string });
          break;
        case 'skilldev.desc_opt_ready':
          handlersRef.current.handleDescOptReady(payload as unknown as {
            original_description?: string;
            best_description?: string;
            best_score?: string;
            iterations_run?: number;
            history?: unknown[];
            __restore_ts?: string;
          });
          break;
        case 'skilldev.error':
          handlersRef.current.handleError(payload as unknown as { error: string; __restore_ts?: string });
          break;
        case 'skilldev.suspended':
          handlersRef.current.handleSuspended(payload as { __restore_ts?: string });
          break;
        case 'skilldev.completed':
          handlersRef.current.handleCompleted(payload as { __restore_ts?: string });
          break;
        case 'skilldev.tool_call':
          handlersRef.current.handleToolCall(payload as Record<string, unknown>);
          break;
        case 'skilldev.tool_result':
          handlersRef.current.handleToolResult(payload as Record<string, unknown>);
          break;
        default:
          break;
      }
    }
  }, [addMessage]);

  const handleRefreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const sessions = await listSkillDevSessions();
      setSessionList(sessions);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载会话失败';
      setError(msg);
    } finally {
      setSessionsLoading(false);
    }
  }, [setError]);

  const handleRestoreSession = useCallback(async (targetTaskId: string) => {
    const generation = restoreGenerationRef.current + 1;
    restoreGenerationRef.current = generation;
    setRestoringTaskId(targetTaskId);
    try {
      const restored = await restoreSkillDevSession(targetTaskId);
      if (generation !== restoreGenerationRef.current) {
        return;
      }
      hydrateFromSkillDevRestore(restored as SkillDevRestoreResult);
      sessionIdRef.current = restored.task_id;
      hasTaskStartedRef.current = true;
      testProgressIdRef.current = null;
      caseCardIdMapRef.current.clear();
      replayRestoreTimeline(restored.timeline_items || []);
      resumeQueryRef.current =
        typeof restored.snapshot.query === 'string' ? restored.snapshot.query : '';
      const canResume = skillDevResumeEligible(restored.snapshot);
      setShowResumeBanner(canResume);
      if (canResume) {
        // 历史里可能有“非确认挂起”的结束事件，恢复后允许用户点击“继续任务”
        setSuspended(false);
        setProcessing(false);
      }
      fileBrowserLoaded.current = false;
      setActiveTab('chat');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '恢复会话失败';
      setError(msg);
    } finally {
      if (generation === restoreGenerationRef.current) {
        setRestoringTaskId(null);
      }
    }
  }, [hydrateFromSkillDevRestore, replayRestoreTimeline, setError]);

  const handleResumeTask = useCallback(async () => {
    const tid = useSkillDevStore.getState().taskId ?? sessionIdRef.current;
    if (!tid) return;
    try {
      setShowResumeBanner(false);
      taskCancelledRef.current = false;
      setProcessing(true);
      setSuspended(false);
      await startSkillDev({
        query: resumeQueryRef.current || '',
        session_id: tid,
        task_id: tid,
      });
    } catch (err) {
      const state = useSkillDevStore.getState();
      const eligible =
        !state.isSuspended && state.stage !== 'completed' && state.stage !== 'error';
      setShowResumeBanner(eligible);
      setProcessing(false);
      setError(err instanceof Error ? err.message : t('skilldev.resumeTaskFailed'));
    }
  }, [setProcessing, setSuspended, setError, t]);

  // ========== 用户操作 ==========

  const handleStart = useCallback(async (files?: MediaItem[], toolSpecFiles?: MediaItem[], skillPackages?: MediaItem[]) => {
    if (
      !inputValue.trim() &&
      (!files || files.length === 0) &&
      (!toolSpecFiles || toolSpecFiles.length === 0) &&
      (!skillPackages || skillPackages.length === 0)
    ) {
      return;
    }

    try {
      setQuery(inputValue);
      const content = inputValue.trim();
      addMessage({
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        type: 'text',
        timestamp: new Date().toISOString(),
        mediaItems: files,
      });

      const sessionId = getOrCreateSessionId();
      setTaskId(sessionId);
      taskCancelledRef.current = false;
      await startSkillDev({
        query: inputValue,
        session_id: sessionId,
        files,
        skill_packages: skillPackages,
        tool_spec_files: toolSpecFiles,
      });
      setInputValue('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [inputValue, setQuery, addMessage, setError, setTaskId, getOrCreateSessionId]);

  const handleImportSkill = useCallback(async (file: MediaItem) => {
    if (hasTaskStartedRef.current) {
      const newSessionId = crypto.randomUUID();
      sessionIdRef.current = newSessionId;
      hasTaskStartedRef.current = false;
      setTaskId(newSessionId);
      const blockedMessage = t('skilldev.importSkillAutoNewTask');
      addMessage({
        id: `import-skill-blocked-${Date.now()}`,
        role: 'system',
        content: blockedMessage,
        type: 'text',
        timestamp: new Date().toISOString(),
      });
    }
    try {
      const sessionId = getOrCreateSessionId();
      console.info('[SkillDevPanel] import skill selected', {
        filename: file?.filename,
        sessionId,
        taskId: taskId || undefined,
      });
      const result = await parseSkillPackage(sessionId, file, taskId || undefined);
      console.info('[SkillDevPanel] import skill request succeeded', {
        responseTaskId: result?.task_id,
        message: result?.message,
      });
      setTaskId(result.task_id);
      fileBrowserLoaded.current = false;
      if (activeTab === 'files') {
        const tree = await getSkillDevFileTree(result.task_id);
        setFileTree(tree);
        fileBrowserLoaded.current = true;
      }
      addMessage({
        id: `import-skill-${Date.now()}`,
        role: 'system',
        content: result.message || t('skilldev.importSkillSuccess', { name: file.filename }),
        type: 'text',
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : t('skilldev.importSkillFailed');
      console.error('[SkillDevPanel] import skill request failed', {
        error: err,
        errorMsg,
      });
      setError(errorMsg);
      addMessage({
        id: `import-skill-error-${Date.now()}`,
        role: 'system',
        content: t('skilldev.importSkillFailedWithError', { error: errorMsg }),
        type: 'error',
        timestamp: new Date().toISOString(),
      });
    }
  }, [activeTab, addMessage, getOrCreateSessionId, setFileTree, setTaskId, t, taskId]);

  const handleConfirm = useCallback(
    async (
      action: string,
      data?: {
        plan?: SkillDevPlan;
        feedback?: string;
        answers?: ClarifyAnswer[];
        messageId?: string;
      }
    ) => {
      if (!taskId) return;

      try {
        const sessionId = sessionIdRef.current ?? taskId;

        // 优先使用传入的 messageId，否则反向查找最新的 confirm_request
        const targetConfirmId =
          data?.messageId ?? [...messages].reverse().find((msg) => msg.type === 'confirm_request')?.id;

        // 先发送请求，成功后再更新 UI
        await respondSkillDev({
          task_id: taskId,
          session_id: sessionId,
          action,
          plan: data?.plan,
          feedback: data?.feedback,
          answers: data?.answers,
        });

        // 后端确认成功后，更新审批卡片状态
        if (targetConfirmId) {
          resolveConfirmMessage(targetConfirmId, action, data?.feedback);
        } else {
          // 添加用户确认反馈消息
          const actionLabels: Record<string, string> = {
            confirm: t('skilldev.actionConfirmed', '确认'),
            modify: t('skilldev.actionModified', '修改并确认'),
            skip: t('skilldev.actionSkipped', '跳过'),
            improve: t('skilldev.actionImprove', '改进'),
            accept: t('skilldev.actionAccepted', '继续'),
            reject: t('skilldev.actionRejected', '拒绝'),
            submit: t('skilldev.actionSubmit', '提交回答'),
          };

          let content = actionLabels[action] || action;
          if (data?.feedback) {
            content = `${content}: ${data.feedback}`;
          } else if (data?.answers) {
            content = `${content} (${data.answers.length} 个问题)`;
          }

          addMessage({
            id: `user-confirm-${Date.now()}`,
            role: 'user',
            content,
            type: 'text',
            timestamp: new Date().toISOString(),
          });
          setSuspended(false);
          setProcessing(true);
        }
      } catch (err) {
        // 失败时保持审批卡片状态，用户可以重试
        setError(err instanceof Error ? err.message : 'Unknown error');
      }
    },
    [taskId, messages, resolveConfirmMessage, setSuspended, setProcessing, setError, addMessage, t]
  );

  // 处理问题澄清提交
  const handleClarifySubmit = useCallback(
    async (answers: ClarifyAnswer[], questions: ClarifyQuestion[]) => {
      if (!taskId) return;

      try {
        const sessionId = sessionIdRef.current ?? taskId;
        setClarifySubmitted(true);
        setSuspended(false);
        setProcessing(true);

        // 构建 "问题：回答" 格式的消息
        const qaLines = answers.map((ans) => {
          const question = questions.find((q) => q.id === ans.question_id);
          const qText = question?.question || ans.question_id;
          return `**${qText}**：${ans.answer}`;
        });

        const content = `${t('skilldev.clarify.submittedTitle', '已提交回答')}\n\n${qaLines.join('\n\n')}`;

        // 添加用户提交消息
        addMessage({
          id: `user-clarify-${Date.now()}`,
          role: 'user',
          content,
          type: 'text',
          timestamp: new Date().toISOString(),
        });

        await respondSkillDev({
          task_id: taskId,
          session_id: sessionId,
          action: 'submit',
          answers,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
        setProcessing(false);
        setClarifySubmitted(false);
      }
    },
    [taskId, setClarifySubmitted, setSuspended, setProcessing, setError, addMessage, t]
  );

  const handleFileSelect = useCallback(
    async (path: string) => {
      if (!taskId) return;

      try {
        const file = await readSkillDevFile(taskId, path);
        setCurrentFile(file);
      } catch (err) {
        console.error('Failed to read file:', err);
      }
    },
    [taskId, setCurrentFile]
  );

  const handleDownload = useCallback(
    async () => {
      if (!taskId) return;

      try {
        const result = await downloadSkillDevArtifact(taskId);
        triggerDownload(result.filename, result.content_base64);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Download failed');
      }
    },
    [taskId, setError]
  );

  const handleReset = useCallback(() => {
    taskCancelledRef.current = false;
    sessionIdRef.current = null;
    testProgressIdRef.current = null;
    hasTaskStartedRef.current = false;
    caseCardIdMapRef.current.clear();
    resumeQueryRef.current = '';
    setShowResumeBanner(false);
    reset();
    fileBrowserLoaded.current = false;
  }, [reset]);

  const handleCancel = useCallback(async () => {
    if (!taskId) return;

    const sessionId = sessionIdRef.current ?? taskId;

    // 确认对话框
    const confirmed = window.confirm(t('skilldev.cancelConfirm', '确定要终止当前任务吗？'));
    if (!confirmed) return;

    console.log('[SkillDevPanel] Cancelling task:', taskId, 'session:', sessionId);
    try {
      taskCancelledRef.current = true;
      dismissPendingInteraction();
      await cancelSkillDev(sessionId);
      setProcessing(false);
      setSuspended(false);
      addMessage({
        id: `cancelled-${Date.now()}`,
        role: 'system',
        content: t('skilldev.taskCancelled'),
        type: 'text',
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Cancel failed';
      console.error('[SkillDevPanel] Cancel error:', err);
      setError(errorMsg);
      // 同时在聊天中显示错误
      addMessage({
        id: `cancel-error-${Date.now()}`,
        role: 'system',
        content: t('skilldev.cancelFailed', { error: errorMsg }),
        type: 'error',
        timestamp: new Date().toISOString(),
      });
    }
  }, [taskId, dismissPendingInteraction, setProcessing, setSuspended, addMessage, setError, t]);

  // 加载文件树
  useEffect(() => {
    if (activeTab === 'files' && !fileBrowserLoaded.current) {
      void handleLoadFileTree();
    }
  }, [activeTab, handleLoadFileTree]);

  useEffect(() => {
    if (activeTab === 'sessions') {
      void handleRefreshSessions();
    }
  }, [activeTab, handleRefreshSessions]);

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden bg-bg">
      {/* 左侧：聊天/文件区域 */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Tab 切换 - 参考 nav-item.active 样式 */}
        <div className="flex items-center gap-2 p-3 border-b border-border bg-secondary">
          <button
            onClick={() => setActiveTab('chat')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'chat'
                ? 'bg-accent-subtle text-accent'
                : 'text-text-muted hover:text-text hover:bg-hover'
            }`}
          >
            {t('skilldev.tabChat')}
          </button>
          <button
            onClick={() => setActiveTab('files')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'files'
                ? 'bg-accent-subtle text-accent'
                : 'text-text-muted hover:text-text hover:bg-hover'
            }`}
          >
            {t('skilldev.tabFiles')}
          </button>
          <button
            onClick={() => setActiveTab('sessions')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === 'sessions'
                ? 'bg-accent-subtle text-accent'
                : 'text-text-muted hover:text-text hover:bg-hover'
            }`}
          >
            {t('skilldev.tabSessions')}
          </button>
          <div className="flex-1" />
          {taskId && isProcessing && !isSuspended && (
            <button
              onClick={handleCancel}
              className="px-3 py-1.5 text-sm text-danger bg-danger-subtle border border-danger rounded-md hover:bg-danger hover:text-white transition-all duration-200 flex items-center gap-1.5 shadow-sm hover:shadow"
              title={t('skilldev.cancelTask')}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
              {t('skilldev.cancelTask')}
            </button>
          )}
          {taskId && (
            <button
              onClick={handleReset}
              className="px-3 py-1.5 text-sm text-text-muted bg-secondary border border-border rounded-md hover:bg-accent hover:text-white hover:border-accent transition-all duration-200 shadow-sm hover:shadow"
            >
              {t('skilldev.newTask')}
            </button>
          )}
        </div>

        {/* 内容区域 */}
        <div className="flex-1 overflow-hidden">
          {activeTab === 'chat' ? (
            <SkillDevChat
              messages={messages}
              toolExecutions={toolExecutionOrder.map((id) => toolExecutions.get(id)).filter(Boolean) as ToolExecution[]}
              inputValue={inputValue}
              setInputValue={setInputValue}
              onSend={handleStart}
              onImportSkill={handleImportSkill}
              canImportSkill
              importSkillBlockedReason={t('skilldev.importSkillBlocked')}
              isProcessing={isProcessing}
              isSuspended={isSuspended}
              clarifyQuestions={clarifyQuestions}
              isClarifySubmitted={isClarifySubmitted}
              onClarifySubmit={handleClarifySubmit}
              onConfirm={handleConfirm}
              pendingQuestion={pendingQuestion}
              onSubmitAnswer={handleSubmitUserAnswer}
              onDismissQuestion={() => setPendingQuestion(null)}
              showResumeTask={showResumeBanner}
              onResumeTask={handleResumeTask}
            />
          ) : activeTab === 'files' ? (
            <SkillDevFileBrowser
              fileTree={fileTree}
              currentFile={currentFile}
              onFileSelect={handleFileSelect}
            />
          ) : (
            <SkillDevSessionsTab
              sessions={sessionList}
              loading={sessionsLoading}
              restoringTaskId={restoringTaskId}
              activeTaskId={taskId}
              onRefresh={handleRefreshSessions}
              onRestore={handleRestoreSession}
            />
          )}
        </div>
      </div>

      {/* 右侧：Todo + 产物 */}
      <div className="w-[220px] shrink-0 border-l border-border bg-secondary flex flex-col overflow-hidden">
        <SkillDevTodos todos={todos} currentStage={stage} />
        <SkillDevArtifacts artifacts={artifacts} onDownload={handleDownload} />
      </div>
    </div>
  );
}
