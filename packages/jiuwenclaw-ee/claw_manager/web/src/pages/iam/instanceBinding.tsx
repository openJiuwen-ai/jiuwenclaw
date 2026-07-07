/** 实例(gateway)绑定管理的共享 UI（用户/组织页复用）：实例下拉、所属实例 chips、添加选择器。 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { ApiError, InstanceApi } from '../../services/api';
import { InstanceSummary } from '../../types/instance';
import { toast } from '../../stores/uiStore';

/** 拉取实例列表（下拉候选 / id→名字映射）。 */
export function useInstances(): InstanceSummary[] {
  const [instances, setInstances] = useState<InstanceSummary[]>([]);
  useEffect(() => {
    InstanceApi.list({ page: 1, page_size: 200 })
      .then((r) => setInstances(r.items))
      .catch(() => undefined);
  }, []);
  return instances;
}

export function instanceName(instances: InstanceSummary[], jid: string): string {
  return instances.find((i) => i.jiuwenclaw_id === jid)?.jiuwenclaw_name || jid;
}

/** 实例筛选下拉：'' = 全部实例。 */
export function InstanceFilter({
  instances, value, onChange,
}: { instances: InstanceSummary[]; value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <select className="input" style={{ width: 200 }} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t('iam.allInstances', { defaultValue: '全部实例' })}</option>
      {instances.map((i) => (
        <option key={i.jiuwenclaw_id} value={i.jiuwenclaw_id}>
          {i.jiuwenclaw_name}（{i.k8s_namespace}）
        </option>
      ))}
    </select>
  );
}

/** "所属实例"列：chips + 溢出（+N，hover 看全）。 */
export function InstanceChips({ jids, instances }: { jids: string[]; instances: InstanceSummary[] }) {
  if (!jids || jids.length === 0) return <span className="text-xs text-muted">—</span>;
  const names = jids.map((j) => instanceName(instances, j));
  const shown = names.slice(0, 2);
  const rest = names.length - shown.length;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {shown.map((n, i) => <span key={i} className="badge">{n}</span>)}
      {rest > 0 && <span className="badge" title={names.join('、')}>+{rest}</span>}
    </span>
  );
}

export interface Candidate { id: string; label: string; sub?: string }

/** 通用"添加到实例"选择器：候选（已排除在册者）搜索 + 多选 → onConfirm(ids)。 */
export function AddToInstanceModal({
  title, candidates, onConfirm, onClose,
}: {
  title: string;
  candidates: Candidate[];
  onConfirm: (ids: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return kw ? candidates.filter((c) => c.label.toLowerCase().includes(kw) || c.id.toLowerCase().includes(kw)) : candidates;
  }, [candidates, q]);

  function toggle(id: string) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function submit() {
    setBusy(true);
    try {
      await onConfirm(Array.from(sel));
      onClose();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || sel.size === 0} onClick={submit}>
            {t('common.confirm', { defaultValue: '确定' })}{sel.size ? `（${sel.size}）` : ''}
          </button>
        </>
      }
    >
      <input
        className="input"
        placeholder={t('common.search', { defaultValue: '搜索' })}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: 8 }}
      />
      <div style={{ maxHeight: 300, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {filtered.map((c) => (
          <label key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
            <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
            <span>{c.label}</span>
            {c.sub && <span className="text-xs text-muted mono">{c.sub}</span>}
          </label>
        ))}
        {filtered.length === 0 && (
          <div className="text-xs text-muted">{t('iam.noCandidates', { defaultValue: '没有可添加的候选（都已在该实例）' })}</div>
        )}
      </div>
    </Modal>
  );
}
