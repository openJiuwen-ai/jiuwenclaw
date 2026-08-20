/**
 * 设计创意模式的常量配置。
 *
 * v1 仅 PPT 一类子类别。每个子类别带 6 个快捷能力胶囊 + 2 个任务推荐卡片。
 * 后续扩展 website / document / poster 时按同样结构追加。
 */
import type { DesignCategory } from './types'

export interface DesignCategoryOption {
  id: DesignCategory
  label: string
  description: string
  /** v1 仅 PPT 可选；其余子类别渲染为 disabled 占位。 */
  enabled: boolean
}

/** 设计子类别列表（v1 仅 PPT 启用，预留 website/document/poster 占位）。 */
export const DESIGN_CATEGORIES: DesignCategoryOption[] = [
  { id: 'ppt', label: 'PPT 设计', description: '幻灯片 / 演示文稿 / deck', enabled: true },
  { id: 'website', label: '网站设计', description: '网页 / 落地页 / 响应式站点', enabled: false }
]

/** 各设计子类别的快捷能力胶囊（v1 仅 PPT 6 项）。 */
export const DESIGN_QUICK_ACTIONS: Record<DesignCategory, string[]> = {
  ppt: [
    '产品介绍PPT',
    '工作汇报PPT',
    '项目方案PPT',
    '教学课件PPT',
    '数据分析PPT',
    '商业计划PPT'
  ],
  website: [],
  document: [],
  poster: []
}

/** 各设计子类别的任务推荐卡片（v1 仅 PPT 2 项）。 */
export interface DesignTaskSuggestion {
  icon: string
  title: string
  desc: string
}

export const DESIGN_TASK_SUGGESTIONS: Record<DesignCategory, DesignTaskSuggestion[]> = {
  ppt: [
    {
      icon: '🎨',
      title: '创建产品介绍PPT',
      desc: '从零到一自动生成专业的产品介绍演示文稿，含封面、特性、对比、案例'
    },
    {
      icon: '📊',
      title: '生成工作汇报PPT',
      desc: '把工作进展、数据指标、下阶段计划一键整理成汇报 deck'
    }
  ],
  website: [],
  document: [],
  poster: []
}

/**
 * 构造设计模式的快捷能力 prompt。
 * 与 code/work 模式的 `帮我${action}` 不同，design 模式加"要求内容专业、版面美观"。
 */
export function buildDesignQuickActionPrompt(action: string): string {
  return `帮我做${action}，要求内容专业、版面美观`
}
