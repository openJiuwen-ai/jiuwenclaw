/**
 * 设计创意模式（design mode）的子类别类型。
 *
 * v1 仅 PPT 启用；website / document / poster 作为 UI 占位渲染（disabled），
 * 后续版本按需启用。
 */
export type DesignCategory = 'ppt' | 'website' | 'document' | 'poster'

/** v1 已启用（可选）的设计子类别 ID 集合。 */
export const ENABLED_DESIGN_CATEGORIES: ReadonlySet<DesignCategory> = new Set<DesignCategory>(['ppt'])
