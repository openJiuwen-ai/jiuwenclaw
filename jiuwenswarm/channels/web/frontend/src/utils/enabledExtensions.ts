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
