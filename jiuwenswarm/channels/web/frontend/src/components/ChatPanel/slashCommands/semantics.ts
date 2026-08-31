/**
 * `/plan` 是输入面板上的即时开关。只有独立的 `/plan` 才是命令；
 * 带有其他文本时（如 `/plan hi`）应保留原文并按普通消息发送。
 *
 * 调用方已先确认 name 存在于命令注册表中。
 */
export function shouldExecuteRegisteredSlashCommand(name: string, args: string): boolean {
  return name.toLowerCase() !== 'plan' || args.trim().length === 0;
}
