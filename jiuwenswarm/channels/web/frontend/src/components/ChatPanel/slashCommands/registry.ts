import { webRequest } from '../../../services/webClient';
import type { Message } from '../../../types/message';
import { usePlanStore } from '../../../stores/planStore';

/**
 * 斜杠命令注册表（/btw、/compact、/plan，对齐 TUI）。
 * 后端与 TUI 共用 agent_ws_server；命令结果以 system 消息留痕，
 * 第一行回显命令行，MessageItem 按 isCommandOutput 渲染。
 */

/** 斜杠命令执行上下文：由 InputArea 在提交期构造并注入 */
export type SlashCommandContext = {
  sessionId: string;
  /** 当前会话模式（'agent' / 'team' 等），随请求带给后端做 agent 解析 */
  mode: string;
  /** 用户原始输入行（如 "/btw 介绍自己"），用于在结果消息第一行回显 */
  inputLine: string;
  addMessage: (sessionId: string, message: Message) => void;
  submitMessage?: (content: string) => void;
};

export interface SlashCommand {
  name: string;
  /** 是否要求真实会话；纯本地命令（/plan）设 false，欢迎页也能用 */
  requiresSession?: boolean;
  execute: (ctx: SlashCommandContext, args: string) => Promise<void>;
}

/** 解析 "/btw some question" → { name: "btw", args: "some question" } */
export function parseSlashLine(raw: string): { name: string; args: string } {
  const trimmed = raw.trim().replace(/^\/+/, '');
  const spaceIdx = trimmed.search(/\s/);
  if (spaceIdx === -1) return { name: trimmed.toLowerCase(), args: '' };
  return {
    name: trimmed.slice(0, spaceIdx).toLowerCase(),
    args: trimmed.slice(spaceIdx + 1).trim(),
  };
}

export function findSlashCommand(name: string): SlashCommand | undefined {
  const lower = name.toLowerCase();
  return SLASH_COMMANDS.find((c) => c.name === lower);
}

/** 命令结果消息：保留兼容 content，同时附带结构化字段供专用卡片渲染。 */
function commandResultMessage(inputLine: string, output: string): Message {
  const normalizedInput = inputLine.trim();
  const normalizedOutput = output.trim();
  return {
    id: `slash-out-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'system',
    isCommandOutput: true,
    commandName: parseSlashLine(normalizedInput).name,
    commandInput: normalizedInput,
    commandOutput: normalizedOutput,
    content: normalizedOutput ? `${normalizedInput}\n${normalizedOutput}` : normalizedInput,
    timestamp: new Date().toISOString(),
  };
}

/** /btw —— 快速侧问：单轮、无工具、复用当前上下文，不打断主对话。 */
const btwCommand: SlashCommand = {
  name: 'btw',
  execute: async (ctx, args) => {
    const question = args.trim();
    if (!question) {
      ctx.addMessage(ctx.sessionId, commandResultMessage(ctx.inputLine, '用法：/btw <你的问题>'));
      return;
    }
    let output: string;
    try {
      const res = await webRequest<{ status: string; answer?: string }>(
        'command.btw',
        { session_id: ctx.sessionId, question, mode: ctx.mode },
        { timeoutMs: 120000 },
      );
      if (res.status === 'ok' && res.answer) {
        output = res.answer;
      } else if (res.status === 'no_context') {
        output = '还没有对话上下文，先发一条消息再侧问。';
      } else {
        output = res.answer ?? '侧问失败，请稍后再试。';
      }
    } catch {
      output = '侧问失败：网络异常或请求超时。';
    }
    ctx.addMessage(ctx.sessionId, commandResultMessage(ctx.inputLine, output));
  },
};

/** /compact —— 压缩对话历史为摘要；token 计数刷新由 context.* 事件监听处理。 */
const compactCommand: SlashCommand = {
  name: 'compact',
  execute: async (ctx) => {
    let output: string;
    try {
      const res = await webRequest<{
        result: string;
        stats?: { total_tokens?: number; raw_total_tokens?: number };
      }>(
        'command.compact',
        { session_id: ctx.sessionId, mode: ctx.mode },
        { timeoutMs: 600000 },
      );
      if (res.result === 'busy') {
        output = '压缩已在进行中，请稍候。';
      } else if (res.result === 'noop') {
        output = '上下文已是最优，无需压缩。';
      } else if (res.result === 'compressed') {
        const before = res.stats?.raw_total_tokens ?? 0;
        const after = res.stats?.total_tokens ?? 0;
        const rate = before > 0 ? Math.round(((before - after) / before) * 100) : 0;
        const fmt = (n: number) => Math.max(1, Math.round(n / 1000));
        output = `✓ 上下文已压缩：${fmt(after)}K / ${fmt(before)}K tokens（节省 ${rate}%）`;
      } else {
        output = '压缩未完成，请稍后再试。';
      }
    } catch {
      output = '压缩失败：网络异常或请求超时。';
    }
    ctx.addMessage(ctx.sessionId, commandResultMessage(ctx.inputLine, output));
  },
};

/**
 * /plan —— 翻转 planStore 的 Plan 开关（纯本地，不调后端）。
 * 开启时置 explicitEntry，下一条真实消息带 agent.plan + plan_entry_source；
 * 集群（team）不支持，与工具栏开关一致。
 */
const planCommand: SlashCommand = {
  name: 'plan',
  requiresSession: false,
  execute: async (ctx, args) => {
    // 集群不支持：仅回提示；正常开关静默（状态已由工具栏可视化）
    if (ctx.mode === 'team') {
      ctx.addMessage(
        ctx.sessionId,
        commandResultMessage(ctx.inputLine, '计划模式仅对单 agent 开放，集群会话不支持。'),
      );
      return;
    }
    const store = usePlanStore.getState();
    store.ensureRuntime(ctx.sessionId);
    const request = args.trim();
    if (!request && store.isActive(ctx.sessionId)) {
      store.setActive(ctx.sessionId, false);
    } else {
      if (!store.isActive(ctx.sessionId)) {
        store.setActive(ctx.sessionId, true, {
          explicitEntry: true,
          entrySource: 'slash_command',
        });
      }
      if (request && request !== 'open') {
        ctx.submitMessage?.(request);
      }
    }
  },
};

export const SLASH_COMMANDS: SlashCommand[] = [btwCommand, compactCommand, planCommand];
