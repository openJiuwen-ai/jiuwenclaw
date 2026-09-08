const avatarColors = [
  "bg-red-500",
  "bg-orange-500",
  "bg-amber-500",
  "bg-yellow-500",
  "bg-lime-500",
  "bg-green-500",
  "bg-emerald-500",
  "bg-teal-500",
  "bg-cyan-500",
  "bg-sky-500",
  "bg-blue-500",
  "bg-indigo-500",
  "bg-violet-500",
  "bg-purple-500",
  "bg-fuchsia-500",
  "bg-pink-500",
  "bg-rose-500",
];

export interface AvatarStyle {
  firstChar: string;
  color: string;
}

// 2026-08-19：整合 ConnectorMarket/avatar.ts 的 deriveAvatarStyle——原来插件/MCP 那边另起了一份
// hex 配色的头像生成（用透明度混色做浅底彩字风格），跟这里字母大小写/取色逻辑本质相同但两套实现、
// 两套配色表。用户明确要求统一成这一份（技能面板已用的实心圆+白字母风格），插件/MCP 详情页、卡片、
// 选择弹窗全部改用这个函数，avatar.ts 已删除。
/** 通用首字母头像：展示字母大写；颜色按首字母小写哈希，避免 Weather/weather 颜色不一致。
 * `color` 是 Tailwind 背景色 class（如 `bg-red-500`），配合 `text-text-inverse` 使用。 */
export function getSkillAvatar(name: string): AvatarStyle {
  const trimmed = String(name || "").trim() || "?";
  const firstChar = trimmed.charAt(0).toUpperCase();
  const colorSeed = trimmed.charAt(0).toLowerCase().charCodeAt(0) || 0;
  const colorIndex = colorSeed % avatarColors.length;
  return { firstChar, color: avatarColors[colorIndex] };
}
