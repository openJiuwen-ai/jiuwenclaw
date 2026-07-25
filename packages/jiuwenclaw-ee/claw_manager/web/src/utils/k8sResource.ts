/** 与后端一致：K8s CPU / 内存 quantity 格式。 */
const K8S_CPU_RE = /^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)m?$/;
const K8S_MEMORY_RE =
  /^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)(?:(?:[KMGTPE]i)|[kMGTPE]|m)?$/;

export function isValidK8sCpu(value: string): boolean {
  const text = value.trim();
  if (!text) return true; // 可选字段，空视为未填
  return text.length <= 32 && K8S_CPU_RE.test(text);
}

export function isValidK8sMemory(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  return text.length <= 32 && K8S_MEMORY_RE.test(text);
}
