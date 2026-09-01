/** Team 会话的 Web 输入框不提供内置斜杠指令；单 Agent 保留原有能力。 */
export function supportsWebSlashCommands(mode: string): boolean {
  return mode !== 'team';
}

/** 统一给快捷面板做模式过滤，避免 Team 会话泄露可点击的命令入口。 */
export function getWebSlashCommandsForMode<T>(commands: T[], mode: string): T[] {
  return supportsWebSlashCommands(mode) ? commands : [];
}

/**
 * `/plan` 是输入面板上的即时开关。只有独立的 `/plan` 才是命令；
 * 带有其他文本时（如 `/plan hi`）应保留原文并按普通消息发送。
 * Team 模式下所有注册命令都不由 Web 前端拦截执行。
 *
 * 调用方已先确认 name 存在于命令注册表中。
 */
export function shouldExecuteRegisteredSlashCommand(name: string, args: string, mode: string): boolean {
  if (!supportsWebSlashCommands(mode)) return false;
  return name.toLowerCase() !== 'plan' || args.trim().length === 0;
}
