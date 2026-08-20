/**
 * 设计创意模式（design mode）前端隔离模块。
 *
 * 把 design 模式专属的 UI 组件、常量、类型集中在此目录，
 * ChatPanel / ConversationSidebar 仅做最小接线（导入并条件渲染）。
 */
export { DesignCategorySelector } from './DesignCategorySelector';
export { DesignWelcomeContent } from './DesignWelcomeContent';
export {
  DESIGN_CATEGORIES,
  DESIGN_QUICK_ACTIONS,
  DESIGN_TASK_SUGGESTIONS,
  buildDesignQuickActionPrompt,
} from './constants';
export type { DesignCategory } from './types';
export type { DesignCategoryOption, DesignTaskSuggestion } from './constants';
