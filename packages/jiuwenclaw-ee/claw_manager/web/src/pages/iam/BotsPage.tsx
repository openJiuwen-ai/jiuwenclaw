import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, Bot, BotApi, BotScope, BotVisibility, IamUser, InstanceBindingApi, MappingApi, Org, OrgApi, UserApi } from '../../services/api';
import { ConfigDefaultTemplateMapping } from '../../types/policy';
import { InstanceSummary } from '../../types/instance';
import { loadTemplateOptions, TemplateOption } from '../../components/TemplateRefEditor';
import { toast } from '../../stores/uiStore';
import { AddToInstanceModal, InstanceChips, InstanceFilter, instanceName, useInstances } from './instanceBinding';
import { BotTemplateBinding, TemplateRefValue } from './BotTemplateBinding';

export function BotsPage() {
  const { t } = useTranslation();
  const { data, loading, reload } = useAsync(() => BotApi.list(), []);
  const { data: orgsData } = useAsync(() => OrgApi.list(), []);
  const { data: usersData } = useAsync(() => UserApi.list(), []);
  const instances = useInstances();
  const [editing, setEditing] = useState<Bot | null | undefined>(undefined);
  const [showAdd, setShowAdd] = useState(false);
  const [filterJid, setFilterJid] = useState('');
  const [roster, setRoster] = useState<Set<string> | null>(null); // 该实例上的 bot_id
  const [bindings, setBindings] = useState<Record<string, string[]>>({}); // bot -> 所属实例
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const bots = data?.items ?? [];
  const orgs = orgsData?.items ?? [];
  const users = usersData?.items ?? [];
  const botIdsKey = bots.map((b) => b.bot_id).join(',');

  useEffect(() => {
    setChecked(new Set());
    if (filterJid) {
      InstanceBindingApi.listBots(filterJid)
        .then((r) => setRoster(new Set(r.bots.map((b) => b.bot_id))))
        .catch(() => setRoster(new Set()));
    } else {
      setRoster(null);
      if (bots.length) {
        InstanceBindingApi.botGateways(bots.map((b) => b.bot_id)).then((r) => setBindings(r.bindings)).catch(() => setBindings({}));
      }
    }
  }, [filterJid, botIdsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = filterJid && roster ? bots.filter((b) => roster.has(b.bot_id)) : bots;

  function reloadInstance() {
    if (filterJid) InstanceBindingApi.listBots(filterJid).then((r) => setRoster(new Set(r.bots.map((b) => b.bot_id)))).catch(() => undefined);
  }

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

  function toggleCheck(id: string) {
    setChecked((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleAll() {
    setChecked((prev) => (prev.size === shown.length ? new Set() : new Set(shown.map((b) => b.bot_id))));
  }

  async function onRemoveFromInstance() {
    const ids = Array.from(checked);
    if (!ids.length || !filterJid) return;
    const name = instanceName(instances, filterJid);
    if (!window.confirm(t('iam.confirmRemoveFromInstance', { defaultValue: '从实例「{{name}}」移除选中的 {{n}} 项？', name, n: ids.length }))) return;
    try {
      for (const bid of ids) await InstanceBindingApi.removeBot(filterJid, bid);
      toast('success', t('success.saved'));
      setChecked(new Set());
      reloadInstance();
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
        <h2 className="card-title">{t('iam.bots')}</h2>
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
          <button className="btn primary" onClick={() => setEditing(null)}>{t('iam.newBot')}</button>
        </div>
      </div>
      <div className="card">
        <table className="table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th style={{ width: 32 }}>
                <input type="checkbox" checked={shown.length > 0 && checked.size === shown.length} onChange={toggleAll} />
              </th>
              <th>{t('iam.botId')}</th>
              <th>{t('iam.name')}</th>
              <th>{t('iam.status')}</th>
              {!inInstanceMode && <th>{t('iam.belongInstances', { defaultValue: '所属实例' })}</th>}
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((b) => (
              <tr key={b.bot_id}>
                <td><input type="checkbox" checked={checked.has(b.bot_id)} onChange={() => toggleCheck(b.bot_id)} /></td>
                <td className="mono text-xs">{b.bot_id}</td>
                <td>{b.name}</td>
                <td>{b.status}</td>
                {!inInstanceMode && <td><InstanceChips jids={bindings[b.bot_id] ?? []} instances={instances} /></td>}
                <td style={{ textAlign: 'right' }}>
                  <button className="btn sm" onClick={() => setEditing(b)}>{t('common.edit')}</button>
                  <button className="btn sm danger" style={{ marginLeft: 6 }} onClick={() => onDelete(b)}>{t('common.delete')}</button>
                </td>
              </tr>
            ))}
            {!loading && shown.length === 0 && <tr><td colSpan={cols} className="text-muted">{t('iam.noBots')}</td></tr>}
          </tbody>
        </table>
      </div>
      {editing !== undefined && (
        <BotModal
          bot={editing}
          defaultJid={filterJid}
          instances={instances}
          orgs={orgs}
          users={users}
          onClose={() => setEditing(undefined)}
          onSaved={() => { setEditing(undefined); reload(); reloadInstance(); }}
        />
      )}
      {showAdd && filterJid && (
        <AddToInstanceModal
          title={t('iam.addToInstance', { defaultValue: '添加到 {{name}}', name: instName })}
          candidates={bots.filter((b) => !roster?.has(b.bot_id)).map((b) => ({ id: b.bot_id, label: b.name, sub: b.bot_id }))}
          onConfirm={async (ids) => {
            // 默认以「全局」加入,加完可在「编辑」里收窄可见范围。
            for (const bid of ids) await InstanceBindingApi.setBotVisibility(filterJid, bid, [{ scope_type: 'global', scope_id: '' }]);
            toast('success', t('success.saved'));
            setShowAdd(false);
            reloadInstance();
          }}
          onClose={() => setShowAdd(false)}
        />
      )}
    </div>
  );
}

/** 统一的 bot 编辑面板（两种模式完全一致）：name/desc/status + 可见范围。
 *  可见范围区里带一个「实例」下拉,选实例→编该实例的可见范围(全局/部门/个人)。
 *  从 Bot 管理顶部进来时默认选中那个实例;全部实例视图默认第一个实例。 */
function BotModal({ bot, defaultJid, instances, orgs, users, onClose, onSaved }: {
  bot: Bot | null; defaultJid: string; instances: InstanceSummary[];
  orgs: Org[]; users: IamUser[]; onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = !!bot;
  const [name, setName] = useState(bot?.name ?? '');
  const [description, setDescription] = useState(bot?.description ?? '');
  const [status, setStatus] = useState(bot?.status ?? 'active');
  const jid = defaultJid; // 可见范围锁定为顶部选中的实例；全部实例视图(空)下不编可见范围
  const instName = instances.find((i) => i.jiuwenclaw_id === jid)?.jiuwenclaw_name ?? jid;
  const [allVis, setAllVis] = useState<BotVisibility[]>([]); // 该 bot 跨所有实例的可见性行
  const [globalVisible, setGlobalVisible] = useState(false);
  const [orgIds, setOrgIds] = useState<Set<string>>(new Set());
  const [userIds, setUserIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  // 模板绑定（config_default_template_mapping, scope=bot, 本实例）
  const [tplOptions, setTplOptions] = useState<Record<string, TemplateOption[]>>({});
  const [tplValue, setTplValue] = useState<TemplateRefValue>({});
  const [tplExisting, setTplExisting] = useState<ConfigDefaultTemplateMapping[]>([]);

  // 用户选择器（左选组织、右勾用户）
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerOrg, setPickerOrg] = useState('');
  const [members, setMembers] = useState<IamUser[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  // 载入该 bot 全部实例的可见性
  useEffect(() => {
    if (bot) BotApi.get(bot.bot_id).then((d) => setAllVis(d.visibility ?? [])).catch(() => setAllVis([]));
  }, [bot]);

  // 用该实例的可见性回填编辑器
  useEffect(() => {
    const vis = allVis.filter((v) => v.jiuwenclaw_id === jid);
    setGlobalVisible(vis.some((v) => v.scope_type === 'global'));
    setOrgIds(new Set(vis.filter((v) => v.scope_type === 'org').map((v) => v.scope_id)));
    setUserIds(new Set(vis.filter((v) => v.scope_type === 'user').map((v) => v.scope_id)));
  }, [jid, allVis]);

  // 模板选项（一次）+ 该 bot 在本实例的模板绑定
  useEffect(() => {
    loadTemplateOptions().then(setTplOptions).catch(() => setTplOptions({}));
  }, []);
  useEffect(() => {
    if (bot && jid) {
      MappingApi.list(jid, { scope_type: 'bot', scope_id: bot.bot_id, page_size: 200 })
        .then((r) => {
          const rows = r.items ?? [];
          setTplExisting(rows);
          const m: TemplateRefValue = {};
          for (const row of rows) (m[row.template_type] ??= []).push(row.template_id);
          setTplValue(m);
        })
        .catch(() => { setTplExisting([]); setTplValue({}); });
    } else {
      setTplExisting([]);
      setTplValue({});
    }
  }, [bot, jid]);

  useEffect(() => {
    if (pickerOpen && pickerOrg) {
      OrgApi.listMembers(pickerOrg).then((d) => setMembers(d.users)).catch(() => setMembers([]));
      setPicked(new Set());
    } else {
      setMembers([]);
    }
  }, [pickerOpen, pickerOrg]);

  const userName = (id: string) => users.find((u) => u.user_id === id)?.display_name ?? id;
  function toggleOrg(gid: string) { setOrgIds((p) => { const n = new Set(p); n.has(gid) ? n.delete(gid) : n.add(gid); return n; }); }
  function togglePicked(uid: string) { setPicked((p) => { const n = new Set(p); n.has(uid) ? n.delete(uid) : n.add(uid); return n; }); }
  function removeUser(uid: string) { setUserIds((p) => { const n = new Set(p); n.delete(uid); return n; }); }
  function commitAdd() {
    const fresh = Array.from(picked).filter((u) => !userIds.has(u));
    if (fresh.length) setUserIds((p) => new Set([...p, ...fresh]));
    setPicked(new Set());
  }

  async function save() {
    setBusy(true);
    try {
      let bid = bot?.bot_id;
      if (isEdit && bot) await BotApi.update(bot.bot_id, { name, description, status });
      else { const created = await BotApi.create({ name, description }); bid = created.bot_id; }
      // 仅具体实例视图下保存可见范围（全部实例视图只改基本信息）
      if (bid && jid) {
        const scopes: BotScope[] = [];
        if (globalVisible) scopes.push({ scope_type: 'global', scope_id: '' });
        else {
          orgIds.forEach((g) => scopes.push({ scope_type: 'org', scope_id: g }));
          userIds.forEach((u) => scopes.push({ scope_type: 'user', scope_id: u }));
        }
        await InstanceBindingApi.setBotVisibility(jid, bid, scopes);

        // 模板绑定 reconcile：现有行 vs 当前选择，删多余、建新增（每 (槽位,模板) 一行）
        const SEP = ' ';
        const desired = new Set<string>();
        for (const [type, ids] of Object.entries(tplValue)) for (const tid of ids) if (tid) desired.add(`${type}${SEP}${tid}`);
        const existKeys = new Map<string, number>();
        for (const row of tplExisting) existKeys.set(`${row.template_type}${SEP}${row.template_id}`, row.id);
        for (const [key, id] of existKeys) if (!desired.has(key)) await MappingApi.remove(jid, id);
        for (const key of desired) if (!existKeys.has(key)) {
          const [type, tid] = key.split(SEP);
          await MappingApi.create(jid, {
            policy_name: `bot:${bid}:${type}`.slice(0, 128),
            scope_type: 'bot', scope_id: bid, template_type: type, template_id: tid,
            priority: 100, enabled: true,
          });
        }
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
      {!jid ? (
        <div className="text-xs text-muted">{t('iam.visibilityAllInstancesHint', { defaultValue: '可见范围按实例设置，请到具体实例选项卡编辑；此处仅改基本信息。' })}</div>
      ) : (
        <>
          {/* 可见范围锁定为顶部选中的实例：灰掉的下拉，仅示"正在编谁" */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span className="text-xs text-muted">{t('iam.visibilityScope', { defaultValue: '可见范围' })} ·</span>
            <select className="input" style={{ width: 220 }} value={jid} disabled>
              <option value={jid}>{instName}</option>
            </select>
          </div>
          <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.visibilityHint')}</div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <input type="checkbox" checked={globalVisible} onChange={(e) => setGlobalVisible(e.target.checked)} />
            {t('iam.globalAllOnInstance', { defaultValue: '全局（该实例全员可见）' })}
          </label>

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

          {/* 模板绑定（本实例，放在可见范围整块之后）——与"实例→策略→映射Tab"同一张表,双向同步 */}
          <label className="label" style={{ marginTop: 14 }}>{t('iam.tplBinding', { defaultValue: '模板绑定' })}</label>
          <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.tplBindingHint', { defaultValue: '给该 bot 在本实例上指定各槽位用哪个模板；留「无」则不绑（回落默认）。' })}</div>
          <BotTemplateBinding options={tplOptions} value={tplValue} onChange={setTplValue} />
        </>
      )}
    </Modal>
  );
}
