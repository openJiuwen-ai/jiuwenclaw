import { useSessionStore } from '../stores/sessionStore';
import { usePluginPackageStore } from '../stores/pluginPackageStore';
import { useConnectorStore } from '../stores/connectorStore';

/**
 * 会话内"扩展"面板（ChatPanel/ExtensionPickerPanel.tsx）的启用开关只在用户点击那一刻校验过
 * 一次连接态——sessionStore.enabledPlugins/enabledMcps 之后不随连接状态变化自动同步，如果这个
 * MCP 在别的地方被断开/删除、或插件被卸载，数组里的名字会一直留着，直到用户手动再点一次开关。
 * 2026-08-21 用户明确要求：不做"断连/卸载那一刻主动清理所有会话"的源头治理（改动面太大、跨
 * store 遍历所有 runtime），只在两处兜底校验：chat.send 组装参数前（useWebSocket.ts），以及
 * "扩展"面板每次拉到最新列表之后（ExtensionPickerPanel.tsx）。两处共用这一个函数，避免过滤逻辑
 * 分叉。
 *
 * 校验依据跟 ExtensionPickerPanel.tsx 面板本身"何时把开关渲染成可点"的判断完全一致：插件要
 * installed && connectionState==='connected'；MCP 要能在 connectorStore.connectors（builtin+
 * local 合并视图，见该 store 头注释）里查到且 connectionState==='connected'。
 *
 * 过滤掉的失效项会同步调用 removeEnabledPlugin/removeEnabledMcp 从这个会话的开关状态里摘除
 * （用户明确要求：让面板开关同步变回关闭，不要"发了但开关看着还是开着"的认知不一致）。
 */
export function pruneEnabledExtensions(sessionId: string): { plugins: string[]; mcps: string[] } {
  const sessionStore = useSessionStore.getState();
  const runtime = sessionStore.runtimes[sessionId];
  if (!runtime) return { plugins: [], mcps: [] };

  const { installed, connectionStateMap: pluginConnectionStateMap } = usePluginPackageStore.getState();
  const mcpConnectionByName = new Map(
    useConnectorStore.getState().connectors.map((c) => [c.name, c.connectionState]),
  );

  const plugins: string[] = [];
  for (const id of runtime.enabledPlugins) {
    if (installed[id] && pluginConnectionStateMap[id] === 'connected') {
      plugins.push(id);
    } else {
      sessionStore.removeEnabledPlugin(sessionId, id);
    }
  }

  const mcps: string[] = [];
  for (const name of runtime.enabledMcps) {
    if (mcpConnectionByName.get(name) === 'connected') {
      mcps.push(name);
    } else {
      sessionStore.removeEnabledMcp(sessionId, name);
    }
  }

  return { plugins, mcps };
}

/**
 * 组装 chat.send（含 resume/激活确认等同族请求）里 plugin_names/mcp 这两个字段——语义见上面
 * pruneEnabledExtensions 调用方 useWebSocket.ts sendMessage 原有注释：plugin_names 恒传（含
 * 空数组，[] 和"不传"是两种不同语义，见 专家与插件装备-前端接口_v2.md §1.3/§3.6）；mcp 只在
 * 非空时才带（缺失和 [] 效果一致，MCP 接口文档 v2 §6.2）。
 *
 * 2026-08-25：所有会发出 chat.send 的地方都要调这个函数，不要各自重新手写一遍——之前
 * sendUserAnswer（ask-user 等交互确认 resume、activate_confirm 分支）、respondActivate、
 * sendStructuredChatContent 都是独立手写 payload，漏了这两个字段，导致 resume 后插件/MCP
 * 选中状态丢失（用户反馈：ask-user 工具被拒绝后自动 resume，chat.send 没带上 plugin_names/
 * mcp）。
 */
export function buildExtensionSendPayload(sessionId: string): { plugin_names: string[]; mcp?: string[] } {
  const { plugins, mcps } = pruneEnabledExtensions(sessionId);
  return {
    plugin_names: plugins,
    ...(mcps.length > 0 ? { mcp: mcps } : {}),
  };
}
