import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, Bot, BotApi, IamUser, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';

export function BotsPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => BotApi.list(), []);
  const { data: orgsData } = useAsync(() => OrgApi.list(), []);
  const { data: usersData } = useAsync(() => UserApi.list(), []);
  const [editing, setEditing] = useState<Bot | null | undefined>(undefined);
  const bots = data?.items ?? [];

  async function onDelete(b: Bot) {
    if (!window.confirm(t('iam.confirmDeleteBot', { name: b.name }))) return;
    try {
      await BotApi.remove(b.bot_id);
      toast('success', t('success.deleted'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">{t('iam.bots')}</h2>
        <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newBot')}</button>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead><tr><th>{t('iam.botId')}</th><th>{t('iam.name')}</th><th>{t('iam.status')}</th><th>{t('common.actions')}</th></tr></thead>
          <tbody>
            {bots.map((b) => (
              <tr key={b.bot_id}>
                <td className="mono text-xs">{b.bot_id}</td>
                <td>{b.name}</td>
                <td>{b.status}</td>
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setEditing(b)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(b)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && bots.length === 0 && <tr><td colSpan={4} className="text-muted">{t('iam.noBots')}</td></tr>}
          </tbody>
        </table>
      </div>
      {editing !== undefined && (
        <BotModal
          bot={editing}
          orgs={orgsData?.items ?? []}
          users={usersData?.items ?? []}
          onClose={() => setEditing(undefined)}
          onSaved={() => { setEditing(undefined); reload(); }}
        />
      )}
    </div>
  );
}

function BotModal({ bot, orgs, users, onClose, onSaved }: {
  bot: Bot | null; orgs: Org[]; users: IamUser[]; onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = !!bot;
  const [name, setName] = useState(bot?.name ?? '');
  const [description, setDescription] = useState(bot?.description ?? '');
  const [status, setStatus] = useState(bot?.status ?? 'active');
  const [globalVisible, setGlobalVisible] = useState(false);
  const [orgIds, setOrgIds] = useState<Set<string>>(new Set());
  const [userIds, setUserIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  // 用户选择器（点添加 → 左选组织、右勾用户）
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerOrg, setPickerOrg] = useState('');
  const [members, setMembers] = useState<IamUser[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (bot) {
      BotApi.get(bot.bot_id).then((d) => {
        const vis = d.visibility ?? [];
        setGlobalVisible(vis.some((v) => v.scope_type === 'global'));
        setOrgIds(new Set(vis.filter((v) => v.scope_type === 'org').map((v) => v.scope_id)));
        setUserIds(new Set(vis.filter((v) => v.scope_type === 'user').map((v) => v.scope_id)));
      }).catch(() => undefined);
    }
  }, [bot]);

  // 选了组织 → 拉该组织的用户
  useEffect(() => {
    if (pickerOpen && pickerOrg) {
      OrgApi.listMembers(pickerOrg).then((d) => setMembers(d.users)).catch(() => setMembers([]));
      setPicked(new Set());
    } else {
      setMembers([]);
    }
  }, [pickerOpen, pickerOrg]);

  const userName = (id: string) => users.find((u) => u.user_id === id)?.display_name ?? id;

  function toggleOrg(gid: string) {
    setOrgIds((p) => { const n = new Set(p); n.has(gid) ? n.delete(gid) : n.add(gid); return n; });
  }
  function togglePicked(uid: string) {
    setPicked((p) => { const n = new Set(p); n.has(uid) ? n.delete(uid) : n.add(uid); return n; });
  }
  function removeUser(uid: string) {
    setUserIds((p) => { const n = new Set(p); n.delete(uid); return n; });
  }
  function commitAdd() {
    const checked = Array.from(picked);
    const dups = checked.filter((u) => userIds.has(u));
    const fresh = checked.filter((u) => !userIds.has(u));
    if (dups.length) toast('danger', t('iam.dupUsers', { names: dups.map(userName).join('、') }));
    if (fresh.length) setUserIds((p) => new Set([...p, ...fresh]));
    setPicked(new Set());
  }

  async function save() {
    setBusy(true);
    try {
      let bid = bot?.bot_id;
      if (isEdit && bot) await BotApi.update(bot.bot_id, { name, description, status });
      else { const created = await BotApi.create({ name, description }); bid = created.bot_id; }
      if (bid) {
        const scopes: { scope_type: string; scope_id: string | null }[] = [];
        if (globalVisible) scopes.push({ scope_type: 'global', scope_id: '' });
        orgIds.forEach((g) => scopes.push({ scope_type: 'org', scope_id: g }));
        userIds.forEach((u) => scopes.push({ scope_type: 'user', scope_id: u }));
        await BotApi.setVisibility(bid, scopes);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const dim = globalVisible ? 0.5 : 1;
  const boxStyle = { border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 } as const;

  return (
    <Modal
      open
      title={isEdit ? t('iam.editBot') : t('iam.newBot')}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || !name.trim()} onClick={save}>{t('common.save')}</button>
        </>
      }
    >
      <label className="label">{t('iam.name')}</label>
      <input className="input" value={name} onChange={(e) => setName(e.target.value)} />

      <label className="label" style={{ marginTop: 12 }}>{t('iam.description')}</label>
      <input className="input" value={description ?? ''} onChange={(e) => setDescription(e.target.value)} />

      {isEdit && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.status')}</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
        </>
      )}

      <label className="label" style={{ marginTop: 12 }}>{t('iam.visibility')}</label>
      <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.visibilityHint')}</div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <input type="checkbox" checked={globalVisible} onChange={(e) => setGlobalVisible(e.target.checked)} />
        {t('iam.globalAll')}
      </label>

      {/* 组织：平铺勾选 */}
      <div className="label" style={{ opacity: dim }}>{t('iam.visibleOrgs')}</div>
      <div style={{ ...boxStyle, maxHeight: 130, overflow: 'auto', opacity: dim, marginBottom: 10 }}>
        {orgs.map((o) => (
          <label key={o.group_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' }}>
            <input type="checkbox" disabled={globalVisible} checked={orgIds.has(o.group_id)} onChange={() => toggleOrg(o.group_id)} />
            {o.name} <span className="text-xs text-muted mono">{o.group_id}</span>
          </label>
        ))}
        {orgs.length === 0 && <div className="text-xs text-muted">{t('iam.noOrgs')}</div>}
      </div>

      {/* 用户：已选列表 + 点添加（左选组织、右勾用户） */}
      <div className="flex items-center justify-between" style={{ opacity: dim }}>
        <div className="label" style={{ margin: 0 }}>{t('iam.visibleUsers')}</div>
        <button className="btn sm" disabled={globalVisible} onClick={() => setPickerOpen((v) => !v)}>+ {t('iam.addUser')}</button>
      </div>
      <div style={{ ...boxStyle, opacity: dim, marginTop: 4 }}>
        {Array.from(userIds).length === 0 && <span className="text-xs text-muted">{t('iam.selectedUsers')}: —</span>}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Array.from(userIds).map((uid) => (
            <span key={uid} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {userName(uid)}
              <button className="btn ghost sm" disabled={globalVisible} title={t('iam.remove')} style={{ padding: '0 4px' }} onClick={() => removeUser(uid)}>✕</button>
            </span>
          ))}
        </div>
      </div>

      {pickerOpen && !globalVisible && (
        <div style={{ ...boxStyle, marginTop: 8 }}>
          <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.userPickerHint')}</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <select className="input" style={{ width: 160 }} value={pickerOrg} onChange={(e) => setPickerOrg(e.target.value)}>
              <option value="">{t('iam.pickOrg')}</option>
              {orgs.map((o) => <option key={o.group_id} value={o.group_id}>{o.name}</option>)}
            </select>
            <div style={{ flex: 1, maxHeight: 140, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 6 }}>
              {!pickerOrg && <div className="text-xs text-muted">{t('iam.pickOrg')}</div>}
              {pickerOrg && members.length === 0 && <div className="text-xs text-muted">{t('iam.noMembers')}</div>}
              {members.map((m) => (
                <label key={m.user_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' }}>
                  <input type="checkbox" checked={picked.has(m.user_id)} onChange={() => togglePicked(m.user_id)} />
                  {m.display_name} <span className="text-xs text-muted mono">{m.user_id}</span>
                  {userIds.has(m.user_id) && <span className="text-xs text-muted">({t('iam.selectedUsers')})</span>}
                </label>
              ))}
            </div>
          </div>
          <div style={{ textAlign: 'right', marginTop: 6 }}>
            <button className="btn sm primary" disabled={picked.size === 0} onClick={commitAdd}>{t('iam.add')}</button>
          </div>
        </div>
      )}
    </Modal>
  );
}
