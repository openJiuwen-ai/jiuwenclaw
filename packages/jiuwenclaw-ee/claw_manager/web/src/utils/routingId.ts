/** service_id / agent_id 占位符校验（与 Gateway substitute_template 约定一致） */

const ROUTING_PLACEHOLDER = /\$\{(user_id|group_id|bot_id)\}/g;

export function validateRoutingId(value: string): string | null {
  const text = (value ?? '').trim();
  if (!text) {
    return 'routing id is required';
  }
  if (!text.includes('$')) {
    return null;
  }
  const remainder = text.replace(ROUTING_PLACEHOLDER, '');
  if (remainder.includes('$')) {
    return 'invalid routing id placeholder';
  }
  return null;
}
