import { ChangeEvent, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as XLSX from 'xlsx';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, IamUser, InstanceBindingApi, NO_ORG_GROUP_ID, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { AddToInstanceModal, InstanceChips, InstanceFilter, instanceName, useInstances } from './instanceBinding';

export function UsersPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => UserApi.list(), []);
  const { data: orgsData } = useAsync(() => OrgApi.list(), []);
  const instances = useInstances();
  const [editing, setEditing] = useState<IamUser | null | undefined>(undefined);
  const [showBatch, setShowBatch] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [filterJid, setFilterJid] = useState('');
  const [roster, setRoster] = useState<Set<string> | null>(null); // 模式二：某实例花名册 user_ids
  const [bindings, setBindings] = useState<Record<string, string[]>>({}); // 模式一：所属实例
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const users = data?.items ?? [];
  const orgs = orgsData?.items ?? [];
  const userIdsKey = users.map((u) => u.user_id).join(',');

  // 切实例/换用户：载入 roster（模式二）或 所属实例（模式一）
  useEffect(() => {
    setChecked(new Set());
    if (filterJid) {
      InstanceBindingApi.listUsers(filterJid).then((r) => setRoster(new Set(r.user_ids))).catch(() => setRoster(new Set()));
    } else {
      setRoster(null);
      if (users.length) {
        InstanceBindingApi.userGateways(users.map((u) => u.user_id)).then((r) => setBindings(r.bindings)).catch(() => setBindings({}));
      }
    }
  }, [filterJid, userIdsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = filterJid && roster ? users.filter((u) => roster.has(u.user_id)) : users;

  function reloadRoster() {
    if (filterJid) InstanceBindingApi.listUsers(filterJid).then((r) => setRoster(new Set(r.user_ids))).catch(() => undefined);
  }

  async function onDelete(u: IamUser) {
    if (!window.confirm(t('iam.confirmDeleteUser', { name: u.display_name, id: u.user_id }))) return;
    try {
      await UserApi.remove(u.user_id);
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
    setChecked((prev) => (prev.size === shown.length ? new Set() : new Set(shown.map((u) => u.user_id))));
  }

  async function onRemoveFromInstance() {
    const ids = Array.from(checked);
    if (!ids.length || !filterJid) return;
    const name = instanceName(instances, filterJid);
    if (!window.confirm(t('iam.confirmRemoveFromInstance', { defaultValue: '从实例「{{name}}」移除选中的 {{n}} 项？', name, n: ids.length }))) return;
    try {
      await InstanceBindingApi.unbindUsers(filterJid, ids);
      toast('success', t('success.saved'));
      setChecked(new Set());
      reloadRoster();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  const inInstanceMode = !!filterJid;
  const cols = inInstanceMode ? 6 : 7;
  const instName = filterJid ? instanceName(instances, filterJid) : '';

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">{t('iam.users')}</h2>
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
          <button className="btn" onClick={() => setShowBatch(true)}>{t('iam.batchNewUser')}</button>
          <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newUser')}</button>
        </div>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th style={{ width: 32 }}>
                <input type="checkbox" checked={shown.length > 0 && checked.size === shown.length} onChange={toggleAll} />
              </th>
              <th>{t('iam.userId')}</th>
              <th>{t('iam.displayName')}</th>
              <th>{t('iam.role')}</th>
              <th>{t('iam.status')}</th>
              {!inInstanceMode && <th>{t('iam.belongInstances', { defaultValue: '所属实例' })}</th>}
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((u) => (
              <tr key={u.user_id}>
                <td><input type="checkbox" checked={checked.has(u.user_id)} onChange={() => toggleCheck(u.user_id)} /></td>
                <td className="mono text-xs">{u.user_id}</td>
                <td>{u.display_name}</td>
                <td>{u.is_admin ? <span className="badge">{t('iam.roleAdmin')}</span> : t('iam.roleUser')}</td>
                <td>{u.status}</td>
                {!inInstanceMode && <td><InstanceChips jids={bindings[u.user_id] ?? []} instances={instances} /></td>}
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setEditing(u)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(u)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && shown.length === 0 && <tr><td colSpan={cols} className="text-muted">{t('iam.noUsers')}</td></tr>}
          </tbody>
        </table>
      </div>
      {editing !== undefined && (
        <UserModal user={editing} orgs={orgs} onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); reload(); }} />
      )}
      {showBatch && (
        <BatchImportModal
          targetJid={filterJid}
          targetName={instName}
          onClose={() => setShowBatch(false)}
          onDone={() => { reload(); reloadRoster(); }}
        />
      )}
      {showAdd && filterJid && (
        <AddToInstanceModal
          title={t('iam.addToInstance', { defaultValue: '添加到 {{name}}', name: instName })}
          candidates={users.filter((u) => !roster?.has(u.user_id)).map((u) => ({ id: u.user_id, label: u.display_name, sub: u.user_id }))}
          onConfirm={async (ids) => {
            await InstanceBindingApi.bindUsers(filterJid, ids);
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

type BatchRow = { username: string; password: string; display_name?: string; is_admin?: boolean; orgs?: string[] };
type BatchResp = {
  summary: { total: number; ok: number; failed: number };
  results: Array<{ row: number; username: string; ok: boolean; user_id?: string; warnings?: string[]; error?: string }>;
};

function parseBool(v: unknown): boolean {
  const s = String(v ?? '').trim().toLowerCase();
  return s === 'true' || s === '1' || s === 'yes' || s === 'y' || s === '是';
}

function BatchImportModal({
  targetJid, targetName, onClose, onDone,
}: { targetJid: string; targetName: string; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [fileName, setFileName] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchResp | null>(null);

  function downloadTemplate() {
    const ws = XLSX.utils.aoa_to_sheet([
      ['username', 'password', 'display_name', 'is_admin', 'orgs'],
      ['zhangsan', 'Pass@123', '张三', 'false', '销售部,市场部'],
    ]);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'users');
    XLSX.writeFile(wb, 'users_template.xlsx');
  }

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileName(f.name);
    setResult(null);
    const isCsv = /\.csv$/i.test(f.name);
    const reader = new FileReader();
    reader.onload = () => {
      try {
        // CSV 按 UTF-8 文本读（避免无 BOM 时 SheetJS 用非 UTF-8 码页导致中文乱码）；xlsx 仍按二进制读
        const wb = isCsv
          ? XLSX.read(reader.result as string, { type: 'string' })
          : XLSX.read(reader.result, { type: 'array' });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const json = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: '' });
        setRows(json.map((r) => ({
          username: String(r.username ?? '').trim(),
          password: String(r.password ?? '').trim(),
          display_name: String(r.display_name ?? '').trim() || undefined,
          is_admin: parseBool(r.is_admin),
          orgs: String(r.orgs ?? '').split(/[,，]/).map((s) => s.trim()).filter(Boolean),
        })));
      } catch (err) {
        toast('danger', String(err));
      }
    };
    if (isCsv) reader.readAsText(f, 'UTF-8');
    else reader.readAsArrayBuffer(f);
  }

  const invalidCount = rows.filter((r) => !r.username || !r.password).length;

  async function submit() {
    setBusy(true);
    try {
      const res = await UserApi.batchCreate(rows);
      setResult(res);
      if (res.summary.ok > 0) {
        // 当前选中了某实例：把成功创建的用户补绑到该实例（跨服务两步：identity 建 → manager 绑）。
        if (targetJid) {
          const ids = res.results.filter((r) => r.ok && r.user_id).map((r) => r.user_id as string);
          if (ids.length) {
            try { await InstanceBindingApi.bindUsers(targetJid, ids); }
            catch (e) { toast('danger', `${t('iam.bindInstanceFailed', { defaultValue: '加入实例失败' })}: ${e instanceof ApiError ? e.detail : String(e)}`); }
          }
        }
        onDone();
      }
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={t('iam.batchNewUser')}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t('common.close')}</button>
          <button
            className="btn primary"
            style={{ marginLeft: 8 }}
            disabled={busy || rows.length === 0 || invalidCount > 0 || !!result}
            onClick={submit}
          >
            {t('iam.batchImport')}{rows.length ? ` (${rows.length})` : ''}
          </button>
        </>
      }
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
        <button className="btn sm" onClick={downloadTemplate}>{t('iam.batchDownloadTemplate')}</button>
        <input type="file" accept=".xlsx,.csv" onChange={onFile} />
      </div>
      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>{t('iam.batchHint')}</div>
      {targetJid
        ? <div className="text-xs" style={{ marginBottom: 8, color: '#2d7d46' }}>
            {t('iam.batchWillJoin', { defaultValue: '将同时加入实例：{{name}}', name: targetName })}
          </div>
        : <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
            {t('iam.batchNoInstance', { defaultValue: '未选实例：仅创建用户，暂不加入任何实例（可选实例后再添加）' })}
          </div>}
      {fileName && <div className="text-xs" style={{ marginBottom: 6 }}>{fileName}</div>}

      {rows.length > 0 && !result && (
        <>
          <div className="text-xs" style={{ marginBottom: 4 }}>
            {t('iam.batchPreview', { n: rows.length })}
            {invalidCount > 0 && <span style={{ color: '#c0392b' }}> · {t('iam.batchInvalid', { n: invalidCount })}</span>}
          </div>
          <div style={{ maxHeight: 220, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6 }}>
            <table className="table" style={{ width: '100%', fontSize: 12 }}>
              <thead><tr><th>{t('iam.username')}</th><th>{t('iam.displayName')}</th><th>{t('iam.admin')}</th><th>{t('iam.belongOrgs')}</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={!r.username || !r.password ? { background: 'rgba(192,57,43,0.08)' } : undefined}>
                    <td>{r.username || '—'}{!r.password && <span style={{ color: '#c0392b' }}> ·{t('iam.batchNoPwd')}</span>}</td>
                    <td>{r.display_name || r.username}</td>
                    <td>{r.is_admin ? '✓' : ''}</td>
                    <td className="mono text-xs">{(r.orgs || []).join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {result && (
        <div>
          <div style={{ marginBottom: 8 }}>
            {t('iam.batchSummary', { total: result.summary.total, ok: result.summary.ok, failed: result.summary.failed })}
          </div>
          <div style={{ maxHeight: 240, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6, padding: 8, fontSize: 12 }}>
            {result.results.map((r) => (
              <div key={r.row} style={{ padding: '2px 0' }}>
                {r.ok ? '✅' : '❌'} #{r.row} {r.username}
                {r.error && <span style={{ color: '#c0392b' }}> — {r.error}</span>}
                {r.warnings && r.warnings.length > 0 && <span style={{ color: '#b8860b' }}> — {r.warnings.join('；')}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}

function UserModal({ user, orgs, onClose, onSaved }: { user: IamUser | null; orgs: Org[]; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const isEdit = !!user;
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isAdmin, setIsAdmin] = useState(user?.is_admin ?? false);
  const [status, setStatus] = useState(user?.status ?? 'active');
  const [selectedOrgs, setSelectedOrgs] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  // 不展示"无组织":未勾选任何组织即自动归为无组织
  const realOrgs = orgs.filter((o) => o.group_id !== NO_ORG_GROUP_ID);

  useEffect(() => {
    if (user) {
      UserApi.get(user.user_id).then((d) => setSelectedOrgs(new Set(d.group_ids ?? []))).catch(() => undefined);
    }
  }, [user]);

  function toggleOrg(gid: string) {
    setSelectedOrgs((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid); else next.add(gid);
      return next;
    });
  }

  async function save() {
    setBusy(true);
    try {
      let uid = user?.user_id;
      if (isEdit && user) {
        await UserApi.update(user.user_id, {
          display_name: displayName, is_admin: isAdmin, status,
          ...(password ? { password } : {}),
        });
      } else {
        const created = await UserApi.create({ display_name: displayName, username, password, is_admin: isAdmin });
        uid = created.user_id;
      }
      if (uid) await UserApi.setOrgs(uid, Array.from(selectedOrgs));
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const canSave = displayName.trim() && (isEdit || (username.trim() && password));

  return (
    <Modal
      open
      title={isEdit ? t('iam.editUser') : t('iam.newUser')}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || !canSave} onClick={save}>{t('common.save')}</button>
        </>
      }
    >
      <label className="label">{t('iam.displayName')}</label>
      <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />

      {!isEdit && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.username')}</label>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </>
      )}

      <label className="label" style={{ marginTop: 12 }}>{isEdit ? t('iam.resetPassword') : t('iam.password')}</label>
      <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />

      <label className="label" style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} /> {t('iam.admin')}
      </label>

      {isEdit && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.status')}</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
        </>
      )}

      <label className="label" style={{ marginTop: 12 }}>{t('iam.belongOrgs')}</label>
      <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.noOrgHint')}</div>
      <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {realOrgs.map((o) => (
          <label key={o.group_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' }}>
            <input type="checkbox" checked={selectedOrgs.has(o.group_id)} onChange={() => toggleOrg(o.group_id)} />
            {o.name} <span className="text-xs text-muted mono">{o.group_id}</span>
          </label>
        ))}
        {realOrgs.length === 0 && <div className="text-xs text-muted">{t('iam.noOrgs')}</div>}
      </div>
    </Modal>
  );
}
