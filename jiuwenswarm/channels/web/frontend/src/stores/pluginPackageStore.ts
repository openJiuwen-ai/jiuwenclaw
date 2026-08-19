import { create } from 'zustand';
import { PluginInstallPendingError, pluginPackagesApi } from '../services/pluginPackagesApi';
import type { PluginConnectionState, PluginPackageDetail, PluginPackageSummary } from '../types/pluginPackage';

// 2026-08-07：installed/enabledMap 已经改成以后端 list/show 真实下发的字段为准（对齐
// 专家与插件装备-前端接口(3).md v1.5）——loadList() 每次都会用 pkg.installed/pkg.enabled
// 覆盖本地状态。localStorage 兜底从"唯一数据源"降级成"乐观更新缓存"：install/toggle 调用
// 成功之间的空档，靠本地先翻转一次状态给用户即时反馈，下次 loadList() 会被后端真实值覆盖掉，
// 不会永久跑偏。
//
// create 目前后端仍缺，保持"如实报错"策略，不做本地模拟成功——理由见
// state-model-rectification.md §5：这个操作改变"实体存不存在"，假装成功会导致刷新后
// 诡异地又出现。install/toggle/uninstall 后端已有真实接口，正常走接口结果即可。
//
// deletePackage 没有对应的 plugin_packages.delete（backend-requests.md 需求20，通篇文档
// 没有这个方法）——产品结论是"我的插件"的删除直接复用 uninstall，见该 action 自己的注释。
//
// 2026-08-10 去掉 myPluginIds 这份 localStorage"曾经安装过"名单：用户明确纠正过"我的插件"
// 归属模型——只看 source 字段（local 永远在"我的"，built-in 永远在广场），从条目"出生"起就固定，
// 跟 installed/enabled 状态完全无关。旧版靠 myPluginIds 模拟"卸载后仍留在我的列表"是错的产品
// 理解（同款错误也出现在 connectorStore.ts 的 myConnectorNames，一并删除，见该文件头注释）。
// 归属判断现在直接在 MarketplacePage.tsx 按 pkg.source==='local' 过滤，这个 store 不再需要
// 维护任何"曾经xx过"的历史记录。
//
// 2026-08-15 去除全局启用/禁用（状态C）：enabledMap/toggle 整个删除，插件不再有这个维度，见
// state-model-rectification-v2-remove-global-toggle.md。
//
// 2026-08-17 对齐 专家与插件装备-前端接口_v2.md §1.6：
// - connectedMap（占位布尔）→ connectionStateMap（真实 connection_state 三态），来源
//   list/show 真实字段，见 types/pluginPackage.ts 头注释。
// - 新增 installPendingMap：install 失败且带 pending_connectors（§1.6.3）时，把待连名单存
//   在这里，不再当成普通 error 丢给用户看一句话——组件层（PluginDetailPage.tsx /
//   ExtensionPickerPanel.tsx）据此驱动 usePendingConnectorFlow 走连接续跑，连完再调一次
//   install（幂等）。§1.6.4"已装重连"（installed=true 但 connectionState≠connected）走的是
//   另一条路：组件直接 show() 拿 detail.pendingConnectors 连，不经过这个 map、也不再调 install
//   （文档强调了两次不能调）。
// - 当前后端实例这批接口还没实现（unknown method，预期内），install 的两阶段流程暂时无法端到
//   端验证，按文档实现，等后端 ready 后联调。
//
// 2026-08-18 新增 localPackages（照抄 connectorStore.ts 的 builtinConnectors/myConnectors 拆分）：
// 之前 packages 是唯一一份列表，谁调 loadList() 传什么 filter 都写进同一个字段——ConnectorMarket/
// index.tsx 的市场页轮询固定传 undefined（全量），如果 ExtensionPickerPanel.tsx（会话内"扩展"
// 面板，只需要 local）也共用 packages，两边互相覆盖：市场页每 10s 静默轮询一次会把面板刚拉到的
// local-only 结果整个替换成全量，反之亦然。拆开后 installed/connectionStateMap 这两个按 id 查的
// map 本来就该是两份列表的并集，因此改成合并写入（保留上一次其他来源写入的 id），不再整份覆盖
// 丢失另一侧的数据。
//
// 2026-08-19 更新：packages 的用途从"市场页全量场景"收窄成跟 MCP 侧 builtinConnectors 对称的
// "插件广场"专用桶（filter='builtin'）——之前"插件广场"tab 传 undefined 拿全量再靠前端
// pkg.source 二次过滤，用户反馈"为什么不像 MCP 一样直接用后端 filter 分开"，`loadList()` 现在
// 按 seqKey 分桶的规则不变（filter==='local' 才写 localPackages，其余含 undefined/'builtin' 都写
// packages），但调用方（ConnectorMarket/index.tsx）不再传 undefined，"插件广场"/"我的插件"两个
// tab 各自显式传 'builtin'/'local'，语义上和 MCP 的 builtinConnectors/myConnectors 完全对称。

const LOCAL_STORAGE_KEY = 'jiuwenswarm_plugin_package_local_state';

interface PersistedLocalState {
  installed: Record<string, boolean>;
}

function loadPersistedLocalState(): PersistedLocalState {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (!raw) return { installed: {} };
    const parsed = JSON.parse(raw);
    return {
      installed: typeof parsed?.installed === 'object' && parsed.installed !== null ? parsed.installed : {},
    };
  } catch {
    return { installed: {} };
  }
}

function persistLocalState(state: PersistedLocalState) {
  queueMicrotask(() => {
    try {
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* ignore：配额满/隐私模式 */
    }
  });
}

interface PluginPackageState {
  packages: PluginPackageSummary[];
  /** filter='local' 的独立结果，供 ExtensionPickerPanel.tsx 用（见文件头 2026-08-18 注释）。 */
  localPackages: PluginPackageSummary[];
  detailCache: Record<string, PluginPackageDetail>;
  installed: Record<string, boolean>;
  // 插件依赖的 connector 是否就绪，未加载视为 'disconnected'（见文件头注释——数据缺失时宁可
  // 让 UI 走"需要连接"分支）。
  connectionStateMap: Record<string, PluginConnectionState>;
  // install 失败且带 pending_connectors 时记在这里，key 不存在/值为 undefined 表示没有待处理
  // 的续连（见文件头注释 §1.6.3）。
  installPendingMap: Record<string, string[] | undefined>;
  isLoading: boolean;
  error: string | null;
  /** v2 §3.6：卸载成功但包声明了 connector 依赖时，后端带的引导文案（原文透传，不是 i18n key）。 */
  noticeMessage: string | null;
  busyId: string | null;

  loadList: (filter?: 'builtin' | 'local', options?: { silent?: boolean }) => Promise<void>;
  // 返回是否成功——PluginDetailPage.tsx 卸载后要重新 show() 探测这个插件还在不在（新方案
  // "我的插件"卸载后的收尾逻辑：还能读到就留在详情页，读不到才退出到列表页），需要知道结果。
  loadDetail: (id: string) => Promise<boolean>;
  create: (params: { id: string; name: string; description: string; skills: string[] }) => Promise<boolean>;
  install: (id: string) => Promise<void>;
  uninstall: (id: string) => Promise<void>;
  deletePackage: (id: string) => Promise<boolean>;
  /** 组件层的连接续跑走完/用户取消后调用，清掉 installPendingMap[id]，不留残留状态。 */
  clearInstallPending: (id: string) => void;
  clearError: () => void;
  clearNotice: () => void;
}

const persisted = loadPersistedLocalState();

// 2026-08-18：同 connectorStore.ts 的 listRequestSeq——ExtensionPickerPanel 每次打开都无条件
// 重新 loadList，同一个 filter 桶（'local' 或 packages 那半，undefined/'builtin' 共用）可能有
// 多次调用同时在途，没有序号保护时旧请求姗姗来迟会把新请求已经写入的数据冲掉。key 按实际会
// 写入的 state 字段分桶（跟下面 `filter === 'local' ? localPackages : packages` 的判断保持一致），
// 不是按 filter 原始取值分——undefined 和 'builtin' 本来就写同一个 packages 字段，理应共用同一个
// 序号桶，否则这两者各自的序号互不感知，挡不住彼此的旧请求覆盖。
const listRequestSeq: Record<'local' | 'packages', number> = { local: 0, packages: 0 };

export const usePluginPackageStore = create<PluginPackageState>((set) => ({
  packages: [],
  localPackages: [],
  detailCache: {},
  installed: persisted.installed,
  connectionStateMap: {},
  installPendingMap: {},
  isLoading: false,
  error: null,
  noticeMessage: null,
  busyId: null,

  // silent=true 用于切页/回到市场页时的轮询兜底刷新（每 10s，见 ConnectorMarketPanel）：
  // 不切 isLoading、失败时保留现有列表且不弹 error——同款静默刷新模式见 connectorStore.ts
  // 的 loadList(options)、抄自 CronPanel/index.tsx 的 loadJobs(silent)。
  loadList: async (filter, options) => {
    const silent = options?.silent ?? false;
    const seqKey = filter === 'local' ? 'local' : 'packages';
    const mySeq = ++listRequestSeq[seqKey];
    if (!silent) set({ isLoading: true, error: null });
    try {
      const packages = await pluginPackagesApi.list(filter);
      if (listRequestSeq[seqKey] !== mySeq) return; // 已有更新的同桶调用发起过，这次结果作废
      set((state) => {
        // installed/connectionStateMap 是 packages+localPackages 两份列表的并集，合并写入而不是
        // 整份覆盖——否则市场页（filter=undefined）和会话面板（filter='local'）交替 loadList 时，
        // 后写入的一方会把对方那批 id 的记录冲掉（见文件头 2026-08-18 注释）。
        const nextInstalled = { ...state.installed };
        const nextConnectionStateMap = { ...state.connectionStateMap };
        for (const pkg of packages) {
          nextInstalled[pkg.id] = pkg.installed;
          nextConnectionStateMap[pkg.id] = pkg.connectionState;
        }
        persistLocalState({ installed: nextInstalled });
        return {
          ...(filter === 'local' ? { localPackages: packages } : { packages }),
          isLoading: false,
          installed: nextInstalled,
          connectionStateMap: nextConnectionStateMap,
        };
      });
    } catch (error) {
      if (listRequestSeq[seqKey] !== mySeq) return;
      if (silent) return;
      set({
        ...(filter === 'local' ? { localPackages: [] } : { packages: [] }),
        isLoading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },

  loadDetail: async (id: string) => {
    try {
      const detail = await pluginPackagesApi.show(id);
      set((state) => ({
        detailCache: { ...state.detailCache, [id]: detail },
        connectionStateMap: { ...state.connectionStateMap, [id]: detail.connectionState },
      }));
      return true;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
      return false;
    }
  },

  // 和 install 不同：create 产出的是一个全新实体，后续 show() 还要能读到它，前端没法安全地
  // "假装成功"——后端没实现这个接口时（backend-requests.md 需求2），这里如实失败，让调用方给
  // 用户看错误提示，而不是伪造一条本地数据后刷新就消失。
  create: async (params) => {
    try {
      await pluginPackagesApi.create(params);
      // 新建的包必然是 source==='local'，刷新 localPackages（'我的插件'桶）即可；2026-08-19
      // loadList() 的 filter 语义改成跟 MCP 侧对齐后，裸调 loadList()（等价于 filter='builtin'）
      // 会用只含 builtin 的结果覆盖 packages，刷不出刚创建的这条、还会短暂污染"插件广场"数据。
      await usePluginPackageStore.getState().loadList('local');
      return true;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
      return false;
    }
  },

  // install 成功后后端会把 installed 置 true（v2 §1.4 状态机），本地乐观更新同步，等下次
  // loadList() 用真实值覆盖。失败区分两种（§1.6.3）：带 pending_connectors 的半途失败不算
  // "出错"给用户看一句 error 文案，而是记进 installPendingMap，交给组件层驱动连接续跑后幂等
  // 重试本方法；不带 pending_connectors 的才是普通硬失败，走原来的 error 展示路径。
  install: async (id: string) => {
    set((state) => ({ busyId: id, error: null, installPendingMap: { ...state.installPendingMap, [id]: undefined } }));
    try {
      await pluginPackagesApi.install(id);
      set((state) => {
        const nextInstalled = { ...state.installed, [id]: true };
        persistLocalState({ installed: nextInstalled });
        return { installed: nextInstalled, busyId: null };
      });
    } catch (error) {
      if (error instanceof PluginInstallPendingError) {
        set((state) => ({
          busyId: null,
          installPendingMap: { ...state.installPendingMap, [id]: error.pendingConnectors },
        }));
        return;
      }
      set({ busyId: null, error: error instanceof Error ? error.message : String(error) });
    }
  },

  // 卸载只让 installed 变 false——"我的插件"列表的成员资格现在只看 source==='local'
  // （见文件头注释），卸载不影响它是否还留在"我的"里。如实报错，不强行本地模拟成功。
  //
  // 2026-08-15：新方案里插件的"卸载"按钮在广场/我的两处都统一叫"卸载"（不再单独区分"删除"），
  // 都是这一个 action——见 deletePackage 的注释，两者本来就是同一个后端调用。
  uninstall: async (id: string) => {
    set({ busyId: id, error: null });
    try {
      const { notice } = await pluginPackagesApi.uninstall(id);
      set((state) => {
        const nextInstalled = { ...state.installed, [id]: false };
        persistLocalState({ installed: nextInstalled });
        return { installed: nextInstalled, busyId: null, noticeMessage: notice ?? null };
      });
    } catch (error) {
      set({ busyId: null, error: error instanceof Error ? error.message : String(error) });
    }
  },

  // 插件没有真正的"删除"接口（backend-requests.md 需求20，全文档没有 plugin_packages.delete），
  // 复用 plugin_packages.uninstall——和上面的 uninstall action 调用的是同一个后端方法，只是
  // "我的插件"详情页调用这个入口，方便调用方（PluginDetailPage.tsx）在卸载后按需要做
  // 探测收尾（见该组件注释）。等后端真的给出独立的删除接口再拆开。
  deletePackage: async (id: string) => {
    set({ busyId: id, error: null });
    try {
      const { notice } = await pluginPackagesApi.uninstall(id);
      set((state) => {
        const nextInstalled = { ...state.installed, [id]: false };
        persistLocalState({ installed: nextInstalled });
        return { installed: nextInstalled, busyId: null, noticeMessage: notice ?? null };
      });
      return true;
    } catch (error) {
      set({ busyId: null, error: error instanceof Error ? error.message : String(error) });
      return false;
    }
  },

  clearInstallPending: (id: string) =>
    set((state) => ({ installPendingMap: { ...state.installPendingMap, [id]: undefined } })),

  clearError: () => set({ error: null }),
  clearNotice: () => set({ noticeMessage: null }),
}));
