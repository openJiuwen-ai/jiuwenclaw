import { webRequest } from '../../../services/webClient';
import type { Message } from '../../../types/message';
import { usePlanStore } from '../../../stores/planStore';

/**
 * 斜杠命令（功能对齐 jiuwenswarm-tui 的 /btw、/compact、/plan）。
 *
 * 桌面版（exe/dmg）跑的就是这套 web 前端，所以这里实现 = 桌面 + web 同时生效。
 * 后端 command.btw / command.compact 与 TUI 共用同一个 agent_ws_server，前端直接复用。
 *
 * /plan 与 Codex/TUI 的 /plan 语义一致：切换 Plan 开关。但它**不发单独的后端请求**——
 * web 的 plan-mode 已由 planStore 开关 + WorkAgentModeRail + PlanApprovalInterruptRail + 审批
 * 弹窗整套落地（见 slash-commands-design.md §10）。这里只翻转 planStore：开启时置
 * explicitEntry，使下一条真实消息带 `agent.plan` + `plan_entry_source`，后端据此进入只读
 * 规划；计划完成弹审批，批准后 `plan.mode_exited` 自动关开关。集群会话不支持（同工具栏开关）。
 *
 * 命令结果留痕：作为一条 system 消息留在会话 transcript，渲染为「居中 + 浅色字」的指令
 * 输出样式（对齐 hermes：普通回复顶格实体字，指令输出居中浅色）。消息第一行回显用户输入
 * 的命令行（如 "/btw 介绍自己"），空行后接命令输出。
 */

/** 斜杠命令执行上下文：由 InputArea 在提交期构造并注入 */
export type SlashCommandContext = {
  sessionId: string;
  /** 当前会话模式（'agent' / 'team' 等），随请求带给后端做 agent 解析 */
  mode: string;
  /** 用户原始输入行（如 "/btw 介绍自己"），用于在结果消息第一行回显 */
  inputLine: string;
  addMessage: (sessionId: string, message: Message) => void;
};

export interface SlashCommand {
  name: string;
  description: string;
  usage?: string;
  takesArgs: boolean;
  /** 占位：后续补的命令可先注册为 hidden，不进弹窗 */
  hidden?: boolean;
  /**
   * 是否要求已存在的会话（默认 true，即需要真实 session 才能执行）。
   * 纯本地命令（如 /plan 只翻转 planStore 开关）可设 false：欢迎页（NEW_CONVERSATION_ID）
   * 也能用——其开关状态会在首次发送时由 App 迁移到真实会话。
   */
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

/** 弹窗过滤：按命令名前缀匹配；hidden 的不展示 */
export function filterSlashCommands(query: string): SlashCommand[] {
  const q = query.trim().toLowerCase();
  return SLASH_COMMANDS.filter((c) => !c.hidden && (!q || c.name.toLowerCase().includes(q)));
}

/**
 * 命令结果消息：system 角色 + isCommandOutput 标记。MessageItem 检测到该标记后走 hermes
 * 风格专属渲染（居中、无边框无底色、浅灰小字、命令名等宽），不落进默认的 system 小药丸。
 * content 第一行 = 用户输入（命令行），其后接输出。
 */
function commandResultMessage(inputLine: string, output: string): Message {
  return {
    id: `slash-out-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'system',
    isCommandOutput: true,
    content: output ? `${inputLine.trim()}\n${output.trim()}` : inputLine.trim(),
    timestamp: new Date().toISOString(),
  };
}

/**
 * /btw —— 快速侧问，不打断主对话。
 * 后端 _handle_command_btw → generate_btw_answer：无工具、单轮、复用当前上下文、prompt caching。
 * 结果作为 assistant 消息展示（走 markdown 渲染），第一行回显 /btw 命令行，换行后接答案，不入后端主上下文。
 */
const btwCommand: SlashCommand = {
  name: 'btw',
  description: '快速侧问，不打断主对话（基于当前上下文）',
  usage: '/btw <问题>',
  takesArgs: true,
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

/**
 * /compact —— 压缩对话历史，保留摘要以节省上下文。
 * 后端 _handle_command_compact：summarize 旧消息 → 替换为摘要；返回 busy/compressed/noop。
 * 同时后端会推 context.compressed / context.compression_state 事件（token 计数刷新，
 * 由 useWebSocket.ts 已有监听处理），这里再补一条命令结果消息反馈压缩结果。
 */
const compactCommand: SlashCommand = {
  name: 'compact',
  description: '压缩对话历史，保留摘要以节省上下文',
  usage: '/compact',
  takesArgs: false,
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
 * /plan —— 切换 Plan 开关（对齐 Codex/TUI 的 /plan：显示并切换计划模式）。
 *
 * 不发后端请求：web 的 plan-mode 整套（planStore + WorkAgentModeRail + PlanApprovalInterruptRail
 * + 审批弹窗 + plan.mode_exited）已落地。这里只翻转 planStore：
 * - 开启：setActive(sid, true, { explicitEntry: true }) → 下一条真实消息带 `agent.plan` +
 *   `plan_entry_source`，后端进入只读规划；Agent 调 `exit_plan_mode` 时弹审批，批准后自动退出。
 * - 关闭：setActive(sid, false)。
 * - 集群（team）会话不支持，与工具栏开关一致。
 * 注意：本命令本身只是状态翻转，不进消息队列、不触发后端；用户随后输入的真实请求才进入规划。
 */
const planCommand: SlashCommand = {
  name: 'plan',
  description: '切换计划模式（只读规划 → 审批 → 执行）',
  usage: '/plan',
  takesArgs: false,
  // 纯本地开关翻转，不调后端；欢迎页也能用（开关随首次发送迁移到真实会话）
  requiresSession: false,
  execute: async (ctx) => {
    // 集群不支持 Plan：仅这种「用不了」的情况回一条提示；正常开关静默——
    // 开/关状态已由工具栏 Plan 标签可视化，无需在对话流里留痕。
    if (ctx.mode === 'team') {
      ctx.addMessage(
        ctx.sessionId,
        commandResultMessage(ctx.inputLine, '计划模式仅对单 agent 开放，集群会话不支持。'),
      );
      return;
    }
    const store = usePlanStore.getState();
    store.ensureRuntime(ctx.sessionId);
    if (store.isActive(ctx.sessionId)) {
      store.setActive(ctx.sessionId, false);
    } else {
      store.setActive(ctx.sessionId, true, { explicitEntry: true });
    }
  },
};

export const SLASH_COMMANDS: SlashCommand[] = [btwCommand, compactCommand, planCommand];
