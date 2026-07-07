import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, IamUser, InstanceBindingApi, NO_ORG_GROUP_ID, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { AddToInstanceModal, InstanceChips, InstanceFilter, instanceName, useInstances } from './instanceBinding';

export function OrgsPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => OrgApi.list(), []);
  const instances = useInstances();
  const [editing, setEditing] = useState<Org | null | undefined>(undefined); // undefined=关闭, null=新建
  const [managing, setManaging] = useState<Org | null>(null); // 正在管理成员的组织
  const [showAdd, setShowAdd] = useState(false);
  const [filterJid, setFilterJid] = useState('');
  const [roster, setRoster] = useState<Set<string> | null>(null);
  const [bindings, setBindings] = useState<Record<string, string[]>>({});
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const orgs = data?.items ?? [];
  const orgIdsKey = orgs.map((o) => o.group_id).join(',');

  useEffect(() => {
    setChecked(new Set());
    if (filterJid) {
      InstanceBindingApi.listOrgs(filterJid).then((r) => setRoster(new Set(r.group_ids))).catch(() => setRoster(new Set()));
    } else {
      setRoster(null);
      if (orgs.length) {
        InstanceBindingApi.orgGateways(orgs.map((o) => o.group_id)).then((r) => setBindings(r.bindings)).catch(() => setBindings({}));
      }
    }
  }, [filterJid, orgIdsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // 无组织(__none__)也是合法可见性范围,同普通组织一样可绑定实例
  const shown = filterJid && roster ? orgs.filter((o) => roster.has(o.group_id)) : orgs;

  function reloadRoster() {
    if (filterJid) InstanceBindingApi.listOrgs(filterJid).then((r) => setRoster(new Set(r.group_ids))).catch(() => undefined);
  }

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

  function toggleCheck(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setChecked((prev) => (prev.size === shown.length ? new Set() : new Set(shown.map((o) => o.group_id))));
  }

  async function onRemoveFromInstance() {
    const ids = Array.from(checked);
    if (!ids.length || !filterJid) return;
    const name = instanceName(instances, filterJid);
    if (!window.confirm(t('iam.confirmRemoveFromInstance', { defaultValue: '从实例「{{name}}」移除选中的 {{n}} 项？', name, n: ids.length }))) return;
    try {
      await InstanceBindingApi.unbindOrgs(filterJid, ids);
      toast('success', t('success.saved'));
      setChecked(new Set());
      reloadRoster();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  const inInstanceMode = !!filterJid;
  const cols = inInstanceMode ? 5 : 6;
  const instName = filterJid ? instanceName(instances, filterJid) : '';

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">{t('iam.orgs')}</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <InstanceFilter instances={instances} value={filterJid} onChange={setFilterJid} />
          {inInstanceMode && (
            <>
              <button className="btn danger" disabled={checked.size === 0} onClick={onRemoveFromInstance}>
                {t('iam.removeFromInstance', { defaultValue: '移除出 {{name}}', name: instName })}{checked.size ? `（${checked.size}）` : ''}
              </button>
              <button className="btn" onClick={() => setShowAdd(true)}>
                {t('iam.addToInstance', { defaultValue: '添加到 {{name}}', name: instName })}
              </button>
            </>
          )}
          <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newOrg')}</button>
        </div>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th style={{ width: 32 }}>
                <input type="checkbox" checked={shown.length > 0 && checked.size === shown.length} onChange={toggleAll} />
              </th>
              <th>{t('iam.groupId')}</th>
              <th>{t('iam.name')}</th>
              <th>{t('iam.status')}</th>
              {!inInstanceMode && <th>{t('iam.belongInstances', { defaultValue: '所属实例' })}</th>}
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((o) => (
              <tr key={o.group_id}>
                <td><input type="checkbox" checked={checked.has(o.group_id)} onChange={() => toggleCheck(o.group_id)} /></td>
                <td className="mono text-xs">{o.group_id}</td>
                <td>{o.name}</td>
                <td>{o.status}</td>
                {!inInstanceMode && <td><InstanceChips jids={bindings[o.group_id] ?? []} instances={instances} /></td>}
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setManaging(o)}>{t('iam.members')}</button>
                  <button className="btn sm" style={{ marginLeft: 6 }} onClick={() => setEditing(o)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(o)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && shown.length === 0 && (
              <tr><td colSpan={cols} className="text-muted">{t('iam.noOrgs')}</td></tr>
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
      {showAdd && filterJid && (
        <AddToInstanceModal
          title={t('iam.addToInstance', { defaultValue: '添加到 {{name}}', name: instName })}
          candidates={orgs
            .filter((o) => !roster?.has(o.group_id))
            .map((o) => ({ id: o.group_id, label: o.name, sub: o.group_id }))}
          onConfirm={async (ids) => {
            await InstanceBindingApi.bindOrgs(filterJid, ids);
            toast('success', t('success.saved'));
            setShowAdd(false);
            reloadRoster();
          }}
          onClose={() => setShowAdd(false)}
        />
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
