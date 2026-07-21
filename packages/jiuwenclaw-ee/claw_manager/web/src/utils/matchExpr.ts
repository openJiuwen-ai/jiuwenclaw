/** match_expr 树形可视化编辑与序列化（与 Gateway evaluate_match_expr 约定一致） */

export type MatchField = 'group_id' | 'user_id' | 'bot_id';
export type MatchOp = '==' | '!=';
export type MatchCombineOp = 'and' | 'or';

export const MATCH_FIELDS: MatchField[] = ['group_id', 'user_id', 'bot_id'];

/** 组嵌套深度：0=根层，1=子层（括号内）；最多 2 层组 */
export const MAX_GROUP_DEPTH = 1;

export interface MatchCondNode {
  kind: 'cond';
  id: string;
  field: MatchField;
  op: MatchOp;
  value: string;
}

export interface MatchGroupNode {
  kind: 'group';
  id: string;
  op: MatchCombineOp;
  children: MatchNode[];
}

export type MatchNode = MatchCondNode | MatchGroupNode;

export type MatchExprMode = 'all' | 'custom';

export interface MatchExprModel {
  mode: MatchExprMode;
  root: MatchGroupNode;
  raw?: string;
}

let _seq = 0;
function newId(): string {
  _seq += 1;
  return `match-${_seq}`;
}

export function newCondNode(): MatchCondNode {
  return { kind: 'cond', id: newId(), field: 'group_id', op: '==', value: '' };
}

export function newGroupNode(op: MatchCombineOp = 'or', children?: MatchNode[]): MatchGroupNode {
  return {
    kind: 'group',
    id: newId(),
    op,
    children: children ?? [newCondNode()],
  };
}

export function newDefaultRoot(): MatchGroupNode {
  return newGroupNode('or', [newCondNode()]);
}

function escapeSingleQuoted(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function unescapeSingleQuoted(value: string): string {
  return value.replace(/\\'/g, "'").replace(/\\\\/g, '\\');
}

const SINGLE_CONDITION =
  /^\s*(group_id|user_id|bot_id)\s*(==|!=)\s*'((?:\\'|[^'])*)'\s*$/i;

function parseSingleCondition(text: string): MatchCondNode | null {
  const m = text.trim().match(SINGLE_CONDITION);
  if (!m) return null;
  return {
    kind: 'cond',
    id: newId(),
    field: m[1].toLowerCase() as MatchField,
    op: m[2] as MatchOp,
    value: unescapeSingleQuoted(m[3]),
  };
}

function stripOuterParens(text: string): { inner: string; hadParens: boolean } {
  const t = text.trim();
  if (!t.startsWith('(') || !t.endsWith(')')) {
    return { inner: t, hadParens: false };
  }
  let depth = 0;
  for (let i = 0; i < t.length; i += 1) {
    if (t[i] === '(') depth += 1;
    else if (t[i] === ')') {
      depth -= 1;
      if (depth === 0 && i < t.length - 1) {
        return { inner: t, hadParens: false };
      }
    }
  }
  return { inner: t.slice(1, -1).trim(), hadParens: true };
}

function splitTopLevel(text: string, op: MatchCombineOp): string[] | null {
  const needle = op === 'and' ? ' and ' : ' or ';
  const lower = text.toLowerCase();
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') depth -= 1;
    else if (depth === 0 && lower.startsWith(needle, i)) {
      parts.push(text.slice(start, i).trim());
      i += needle.length;
      start = i;
      continue;
    }
    i += 1;
  }
  parts.push(text.slice(start).trim());
  return parts.filter(Boolean).length > 1 ? parts.filter(Boolean) : null;
}

function parseExprToNode(text: string, depth: number): MatchNode | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const { inner, hadParens } = stripOuterParens(trimmed);
  const body = hadParens ? inner : trimmed;

  const single = parseSingleCondition(body);
  if (single) return single;

  if (depth > MAX_GROUP_DEPTH) return null;

  const orParts = splitTopLevel(body, 'or');
  if (orParts) {
    const children = orParts
      .map((p) => parseExprToNode(p, depth + (hadParens ? 1 : 0)))
      .filter((n): n is MatchNode => n !== null);
    if (children.length === 0) return null;
    return { kind: 'group', id: newId(), op: 'or', children };
  }

  const andParts = splitTopLevel(body, 'and');
  if (andParts) {
    const children = andParts
      .map((p) => parseExprToNode(p, depth + (hadParens ? 1 : 0)))
      .filter((n): n is MatchNode => n !== null);
    if (children.length === 0) return null;
    return { kind: 'group', id: newId(), op: 'and', children };
  }

  if (hadParens) {
    return parseExprToNode(inner, depth + 1);
  }

  return null;
}

function legacyFlatAnd(text: string): MatchCondNode[] | null {
  const parts = text.split(/\s+and\s+/i).map((p) => p.trim()).filter(Boolean);
  const nodes: MatchCondNode[] = [];
  for (const part of parts) {
    const c = parseSingleCondition(part);
    if (!c) return null;
    nodes.push(c);
  }
  return nodes.length ? nodes : null;
}

export function parseMatchExpr(expr: string | null | undefined): MatchExprModel {
  const text = (expr ?? '').trim();
  if (!text) {
    return { mode: 'all', root: newDefaultRoot() };
  }

  if (text.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (Array.isArray(parsed) && parsed.length > 0) {
        const children: MatchNode[] = [];
        for (const item of parsed) {
          const node = parseExprToNode(String(item), 0);
          if (!node) {
            const legacy = legacyFlatAnd(String(item));
            if (legacy) {
              children.push({
                kind: 'group',
                id: newId(),
                op: 'and',
                children: legacy,
              });
            } else {
              return { mode: 'custom', root: newDefaultRoot(), raw: text };
            }
          } else {
            children.push(node);
          }
        }
        return {
          mode: 'custom',
          root: { kind: 'group', id: newId(), op: 'or', children },
        };
      }
    } catch {
      return { mode: 'custom', root: newDefaultRoot(), raw: text };
    }
  }

  const node = parseExprToNode(text, 0);
  if (node?.kind === 'group') {
    return { mode: 'custom', root: node };
  }
  if (node?.kind === 'cond') {
    return {
      mode: 'custom',
      root: { kind: 'group', id: newId(), op: 'and', children: [node] },
    };
  }

  const legacy = legacyFlatAnd(text);
  if (legacy) {
    return {
      mode: 'custom',
      root: { kind: 'group', id: newId(), op: 'and', children: legacy },
    };
  }

  return { mode: 'custom', root: newDefaultRoot(), raw: text };
}

function serializeCondition(c: MatchCondNode): string | null {
  if (!c.value.trim()) return null;
  return `${c.field} ${c.op} '${escapeSingleQuoted(c.value.trim())}'`;
}

function serializeNode(node: MatchNode, isRoot: boolean): string | null {
  if (node.kind === 'cond') {
    return serializeCondition(node);
  }
  const parts = node.children
    .map((child) => serializeNode(child, false))
    .filter((p): p is string => !!p);
  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  const joined = parts.join(` ${node.op} `);
  return isRoot ? joined : `(${joined})`;
}

export function serializeMatchExpr(model: MatchExprModel): string {
  if (model.mode === 'all') return '';
  if (model.raw?.trim()) return model.raw.trim();
  return serializeNode(model.root, true) ?? '';
}

export function treeHasFilledCondition(node: MatchNode): boolean {
  if (node.kind === 'cond') return !!node.value.trim();
  return node.children.some(treeHasFilledCondition);
}

/** 与后端 validate_match_expr 对齐的轻量语法检查；完整校验由 API 兜底。 */
export function validateMatchExprSyntax(expr: string | null | undefined): string | null {
  const text = (expr ?? '').trim();
  if (!text) return null;

  if (text.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (!Array.isArray(parsed)) return 'match_expr_invalid_syntax';
      for (const item of parsed) {
        const err = validateMatchExprSyntax(String(item));
        if (err) return err;
      }
      return null;
    } catch {
      return 'match_expr_invalid_syntax';
    }
  }

  if (/\$\{|===|!==|>=|<=|(?<![!=])>(?!=)|(?<!<|>)<(?!=)|\bservice_id\b|\bagent_id\b/i.test(text)) {
    return 'match_expr_invalid_syntax';
  }

  if (!text.includes('==') && !text.includes('!=')) {
    return null;
  }

  // 含比较符却无法被可视化解析，且也不像合法比较式时提示用户。
  const model = parseMatchExpr(text);
  if (model.raw?.trim()) {
    const single =
      /^\s*(group_id|user_id|bot_id)\s*(==|!=)\s*(['"])(?:\\.|(?!\3).)*\3\s*$/i;
    const combined = text
      .split(/\s+(?:and|or)\s+/i)
      .map((p) => p.trim())
      .filter(Boolean);
    // 带括号的复杂表达式交给后端；仅拦明显非法单行。
    if (!/[()]/.test(text) && !combined.every((p) => single.test(p))) {
      return 'match_expr_invalid_syntax';
    }
  }
  return null;
}

export function validateMatchExprModel(model: MatchExprModel): string | null {
  if (model.mode === 'all') return null;
  if (model.raw?.trim()) {
    return validateMatchExprSyntax(model.raw);
  }
  if (!treeHasFilledCondition(model.root)) {
    return 'match_expr_value_required';
  }
  return null;
}

export function getGroupDepth(root: MatchGroupNode, targetId: string): number | null {
  function walkGroup(group: MatchGroupNode, depth: number): number | null {
    if (group.id === targetId) return depth;
    for (const child of group.children) {
      if (child.kind === 'group') {
        const found = walkGroup(child, depth + 1);
        if (found !== null) return found;
      }
    }
    return null;
  }
  return walkGroup(root, 0);
}

export function canAddSubgroup(root: MatchGroupNode, parentId: string): boolean {
  const depth = getGroupDepth(root, parentId);
  return depth !== null && depth < MAX_GROUP_DEPTH;
}

export function updateNode(root: MatchGroupNode, nodeId: string, updater: (n: MatchNode) => MatchNode): MatchGroupNode {
  function walk(node: MatchNode): MatchNode {
    if (node.id === nodeId) return updater(node);
    if (node.kind === 'group') {
      return { ...node, children: node.children.map(walk) };
    }
    return node;
  }
  return walk(root) as MatchGroupNode;
}

export function removeNode(root: MatchGroupNode, nodeId: string): MatchGroupNode {
  if (root.id === nodeId) return root;

  const parent = findParentGroup(root, nodeId);
  if (
    parent &&
    parent.id !== root.id &&
    parent.children.length === 1 &&
    parent.children[0].id === nodeId &&
    parent.children[0].kind === 'cond'
  ) {
    return removeNode(root, parent.id);
  }

  function walkGroup(group: MatchGroupNode): MatchGroupNode {
    const children = group.children
      .filter((c) => c.id !== nodeId)
      .map((c) => (c.kind === 'group' ? walkGroup(c) : c));
    return {
      ...group,
      children: children.length ? children : [newCondNode()],
    };
  }
  return walkGroup(root);
}

function findParentGroup(root: MatchGroupNode, nodeId: string): MatchGroupNode | null {
  function walk(group: MatchGroupNode): MatchGroupNode | null {
    for (const child of group.children) {
      if (child.id === nodeId) return group;
      if (child.kind === 'group') {
        const found = walk(child);
        if (found) return found;
      }
    }
    return null;
  }
  return walk(root);
}
