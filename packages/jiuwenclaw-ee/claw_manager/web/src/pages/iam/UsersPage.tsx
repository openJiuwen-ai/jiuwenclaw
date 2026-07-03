import { ChangeEvent, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as XLSX from 'xlsx';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, IamUser, NO_ORG_GROUP_ID, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';

export function UsersPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => UserApi.list(), []);
  const { data: orgsData } = useAsync(() => OrgApi.list(), []);
  const [editing, setEditing] = useState<IamUser | null | undefined>(undefined);
  const [showBatch, setShowBatch] = useState(false);
  const users = data?.items ?? [];
  const orgs = orgsData?.items ?? [];

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

  return (
    <div className="page">
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">{t('iam.users')}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={() => setShowBatch(true)}>{t('iam.batchNewUser')}</button>
          <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newUser')}</button>
        </div>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead><tr><th>{t('iam.userId')}</th><th>{t('iam.displayName')}</th><th>{t('iam.role')}</th><th>{t('iam.status')}</th><th>{t('common.actions')}</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.user_id}>
                <td className="mono text-xs">{u.user_id}</td>
                <td>{u.display_name}</td>
                <td>{u.is_admin ? <span className="badge">{t('iam.roleAdmin')}</span> : t('iam.roleUser')}</td>
                <td>{u.status}</td>
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setEditing(u)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(u)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && users.length === 0 && <tr><td colSpan={5} className="text-muted">{t('iam.noUsers')}</td></tr>}
          </tbody>
        </table>
      </div>
      {editing !== undefined && (
        <UserModal user={editing} orgs={orgs} onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); reload(); }} />
      )}
      {showBatch && (
        <BatchImportModal onClose={() => setShowBatch(false)} onDone={() => reload()} />
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

function BatchImportModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
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
      if (res.summary.ok > 0) onDone();
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
