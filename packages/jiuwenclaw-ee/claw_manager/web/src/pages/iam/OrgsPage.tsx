import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, IamUser, NO_ORG_GROUP_ID, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';

export function OrgsPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => OrgApi.list(), []);
  const [editing, setEditing] = useState<Org | null | undefined>(undefined); // undefined=关闭, null=新建
  const [managing, setManaging] = useState<Org | null>(null); // 正在管理成员的组织
  const orgs = data?.items ?? [];

  async function onDelete(o: Org) {
    if (!window.confirm(t('iam.confirmDeleteOrg', { name: o.name }))) return;
    try {
      await OrgApi.remove(o.group_id);
      toast('success', t('success.deleted'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">{t('iam.orgs')}</h2>
        <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newOrg')}</button>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead><tr><th>{t('iam.groupId')}</th><th>{t('iam.name')}</th><th>{t('iam.status')}</th><th>{t('common.actions')}</th></tr></thead>
          <tbody>
            {orgs.map((o) => (
              <tr key={o.group_id}>
                <td className="mono text-xs">{o.group_id}</td>
                <td>{o.name}</td>
                <td>{o.status}</td>
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setManaging(o)}>{t('iam.members')}</button>
                  <button className="btn sm" style={{ marginLeft: 6 }} onClick={() => setEditing(o)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(o)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && orgs.length === 0 && (
              <tr><td colSpan={4} className="text-muted">{t('iam.noOrgs')}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {editing !== undefined && (
        <OrgModal org={editing} onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); reload(); }} />
      )}
      {managing && (
        <MembersModal org={managing} onClose={() => setManaging(null)} />
      )}
    </div>
  );
}

function MembersModal({ org, onClose }: { org: Org; onClose: () => void }) {
  const { t } = useTranslation();
  const readOnly = org.group_id === NO_ORG_GROUP_ID; // 无组织=自动归类,只读
  const { data: membersData, loading, reload } = useAsync(() => OrgApi.listMembers(org.group_id), [org.group_id]);
  const { data: allUsersData } = useAsync(() => UserApi.list(), []);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState('');

  const members: IamUser[] = membersData?.users ?? [];
  const memberIds = useMemo(() => new Set(members.map((u) => u.user_id)), [members]);
  const candidates = useMemo(() => {
    const all = allUsersData?.items ?? [];
    const q = search.trim().toLowerCase();
    return all.filter((u) =>
      !memberIds.has(u.user_id) &&
      (!q || u.user_id.toLowerCase().includes(q) || (u.display_name ?? '').toLowerCase().includes(q)),
    );
  }, [allUsersData, memberIds, search]);

  async function add(uid: string) {
    setBusy(uid);
    try {
      await OrgApi.addMembers(org.group_id, [uid]);
      toast('success', t('success.saved'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy('');
    }
  }

  async function remove(uid: string) {
    setBusy(uid);
    try {
      await OrgApi.removeMember(org.group_id, uid);
      toast('success', t('success.deleted'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy('');
    }
  }

  return (
    <Modal
      open
      title={`${t('iam.members')} · ${org.name}`}
      onClose={onClose}
      footer={<button className="btn primary" onClick={onClose}>{t('common.close')}</button>}
    >
      {/* 当前成员 */}
      <label className="label">{t('iam.currentMembers')} ({members.length})</label>
      <div style={{ maxHeight: 200, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {members.map((u) => (
          <div key={u.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 0' }}>
            <span>{u.display_name} <span className="text-xs text-muted mono">{u.user_id}</span></span>
            {!readOnly && (
              <button className="btn sm danger" disabled={busy === u.user_id} onClick={() => remove(u.user_id)}>{t('iam.removeMember')}</button>
            )}
          </div>
        ))}
        {!loading && members.length === 0 && <div className="text-xs text-muted">{t('iam.noMembers')}</div>}
      </div>

      {/* 添加成员（搜索全部用户） */}
      {readOnly ? (
        <div className="text-xs text-muted" style={{ marginTop: 12 }}>{t('iam.noOrgReadonly')}</div>
      ) : (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.addMember')}</label>
          <input className="input" placeholder={t('iam.searchUser')} value={search} onChange={(e) => setSearch(e.target.value)} />
          <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8, marginTop: 6 }}>
            {candidates.map((u) => (
              <div key={u.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 0' }}>
                <span>{u.display_name} <span className="text-xs text-muted mono">{u.user_id}</span></span>
                <button className="btn sm" disabled={busy === u.user_id} onClick={() => add(u.user_id)}>{t('iam.add')}</button>
              </div>
            ))}
            {candidates.length === 0 && <div className="text-xs text-muted">{t('iam.noCandidates')}</div>}
          </div>
        </>
      )}
    </Modal>
  );
}

function OrgModal({ org, onClose, onSaved }: { org: Org | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState(org?.name ?? '');
  const [status, setStatus] = useState(org?.status ?? 'active');
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      if (org) await OrgApi.update(org.group_id, { name, status });
      else await OrgApi.create({ name });
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={org ? t('iam.editOrg') : t('iam.newOrg')}
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
      {org && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.status')}</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
          <div className="text-xs text-muted" style={{ marginTop: 8 }}>{t('iam.groupId')}: <span className="mono">{org.group_id}</span></div>
        </>
      )}
    </Modal>
  );
}
