import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, PermissionsApi } from '../../../services/api';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal } from '../../../components/Modal';
import { JsonField, useInvalidJsonChecker } from '../../../components/JsonField';
import { toast } from '../../../stores/uiStore';
import { truncate } from '../../../utils/format';
import type {
  PermissionAction,
  PermissionRuleAction,
  PermissionRuleEntry,
  PermissionToolEntry,
  PermissionsFormState,
} from '../../../types';
import {
  createDefaultPermissionsFormState,
  permissionsBodyToFormState,
  permissionsFormStateToBody,
  stripExampleLabel,
} from './permissionsForm';

type SectionKey = 'general' | 'tools' | 'rules' | 'fileGuard' | 'advanced';

interface Props {
  instanceId: string;
}

const PERMISSION_ACTIONS: PermissionAction[] = ['allow', 'ask', 'deny'];
const RULE_ACTIONS: PermissionRuleAction[] = ['allow', 'deny'];

function emptyToolRow(): PermissionToolEntry {
  return { key: `tool-${Date.now()}-${Math.random()}`, name: '', action: 'ask' };
}

function emptyRuleRow(): PermissionRuleEntry {
  return {
    key: `rule-${Date.now()}-${Math.random()}`,
    id: '',
    description: '',
    pattern: '',
    action: 'allow',
  };
}

export function PermissionsTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();

  const [section, setSection] = useState<SectionKey>('general');
  const [form, setForm] = useState<PermissionsFormState>(() => createDefaultPermissionsFormState());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasRemoteConfig, setHasRemoteConfig] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<PermissionToolEntry | null>(null);
  const [toolForm, setToolForm] = useState<PermissionToolEntry>(emptyToolRow());

  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<PermissionRuleEntry | null>(null);
  const [ruleForm, setRuleForm] = useState<PermissionRuleEntry>(emptyRuleRow());

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await PermissionsApi.get(instanceId);
      setForm(permissionsBodyToFormState(data.body ?? {}));
      setHasRemoteConfig(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setForm(createDefaultPermissionsFormState());
        setHasRemoteConfig(false);
      } else {
        setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const updateForm = <K extends keyof PermissionsFormState>(key: K, value: PermissionsFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const validateJsonFields = (): string | null => {
    const fields: Array<[string, string]> = [
      [t('instanceConfig.permissions.fileGuardGlobal'), form.fileGuardGlobalJson],
      [t('instanceConfig.permissions.fileGuardTrustedExec'), form.fileGuardTrustedExecJson],
      [t('instanceConfig.permissions.fileGuardToolBindings'), form.fileGuardToolBindingsJson],
    ];
    for (const [label, value] of fields) {
      const err = checkJson(stripExampleLabel(value));
      if (err) return `${label}: ${err}`;
    }
    return null;
  };

  const save = async () => {
    const jsonErr = validateJsonFields();
    if (jsonErr) {
      toast('danger', jsonErr);
      return;
    }
    setSaving(true);
    try {
      await PermissionsApi.upsert(instanceId, permissionsFormStateToBody(form));
      setHasRemoteConfig(true);
      toast('success', t('success.saved'));
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    } finally {
      setSaving(false);
    }
  };

  const removeConfig = async () => {
    try {
      await PermissionsApi.remove(instanceId);
      setForm(createDefaultPermissionsFormState());
      setHasRemoteConfig(false);
      toast('success', t('instanceConfig.permissions.deleted'));
    } catch (e) {
      toast(
        'danger',
        t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message })
      );
    }
  };

  const openToolModal = (row?: PermissionToolEntry) => {
    if (row) {
      setEditingTool(row);
      setToolForm({ ...row });
    } else {
      setEditingTool(null);
      setToolForm(emptyToolRow());
    }
    setToolModalOpen(true);
  };

  const submitTool = () => {
    if (!toolForm.name.trim()) {
      toast('warn', t('instanceConfig.permissions.toolName'));
      return;
    }
    if (editingTool) {
      updateForm(
        'tools',
        form.tools.map((row) => (row.key === editingTool.key ? { ...toolForm, name: toolForm.name.trim() } : row))
      );
    } else {
      updateForm('tools', [...form.tools, { ...toolForm, name: toolForm.name.trim() }]);
    }
    setToolModalOpen(false);
  };

  const openRuleModal = (row?: PermissionRuleEntry) => {
    if (row) {
      setEditingRule(row);
      setRuleForm({ ...row });
    } else {
      setEditingRule(null);
      setRuleForm(emptyRuleRow());
    }
    setRuleModalOpen(true);
  };

  const submitRule = () => {
    if (!ruleForm.id.trim()) {
      toast('warn', t('instanceConfig.permissions.ruleId'));
      return;
    }
    if (!ruleForm.pattern.trim()) {
      toast('warn', t('instanceConfig.permissions.rulePattern'));
      return;
    }
    const next = {
      ...ruleForm,
      id: ruleForm.id.trim(),
      pattern: ruleForm.pattern.trim(),
      description: ruleForm.description.trim(),
    };
    if (editingRule) {
      updateForm(
        'rules',
        form.rules.map((row) => (row.key === editingRule.key ? next : row))
      );
    } else {
      updateForm('rules', [...form.rules, next]);
    }
    setRuleModalOpen(false);
  };

  const sections: { key: SectionKey; label: string }[] = [
    { key: 'general', label: t('instanceConfig.permissions.sections.general') },
    { key: 'tools', label: t('instanceConfig.permissions.sections.tools') },
    { key: 'rules', label: t('instanceConfig.permissions.sections.rules') },
    { key: 'fileGuard', label: t('instanceConfig.permissions.sections.fileGuard') },
    { key: 'advanced', label: t('instanceConfig.permissions.sections.advanced') },
  ];

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  if (loadError) {
    return (
      <div className="p-4 text-sm text-danger">
        {t('errors.loadFailed', { detail: loadError })}
        <button className="btn sm ml-2" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <button className="btn sm" onClick={() => void reload()}>
          {t('common.refresh')}
        </button>
        {hasRemoteConfig && (
          <button className="btn sm danger" onClick={() => setDeleteOpen(true)}>
            {t('instanceConfig.permissions.resetToYaml')}
          </button>
        )}
        <button className="btn primary sm" onClick={() => void save()} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <div className="tabs-bar overflow-x-auto">
        {sections.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => setSection(it.key)}
            className={`tab ${section === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>

      {section === 'general' && (
        <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => updateForm('enabled', e.target.checked)}
              />
              <span>{t('instanceConfig.permissions.enabled')}</span>
            </label>
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.defaults')}</label>
            <select
              className="select"
              value={form.defaults}
              onChange={(e) => updateForm('defaults', e.target.value as PermissionAction)}
            >
              {PERMISSION_ACTIONS.map((action) => (
                <option key={action} value={action}>
                  {action}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted mt-1">{t('instanceConfig.permissions.defaultsHint')}</p>
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.permissions.denyGuidanceMessage')}</label>
            <textarea
              className="textarea"
              rows={3}
              value={form.denyGuidanceMessage}
              onChange={(e) => updateForm('denyGuidanceMessage', e.target.value)}
              placeholder={t('instanceConfig.permissions.denyGuidanceMessageHint')}
            />
          </div>
        </div>
      )}

      {section === 'tools' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <button className="btn primary sm" type="button" onClick={() => openToolModal()}>
              + {t('instanceConfig.permissions.newTool')}
            </button>
            <span className="text-[11px] text-muted">{t('instanceConfig.permissions.toolsHint')}</span>
          </div>
          <div className="card !p-0">
            {form.tools.length === 0 ? (
              <Empty text={t('common.empty')} />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('instanceConfig.permissions.toolName')}</th>
                    <th>{t('instanceConfig.permissions.action')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {form.tools.map((row) => (
                    <tr key={row.key}>
                      <td className="mono text-sm">{row.name}</td>
                      <td>
                        <span className="tag">{row.action}</span>
                      </td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button className="btn sm ghost" type="button" onClick={() => openToolModal(row)}>
                            {t('common.edit')}
                          </button>
                          <button
                            className="btn sm danger"
                            type="button"
                            onClick={() => updateForm('tools', form.tools.filter((it) => it.key !== row.key))}
                          >
                            {t('common.delete')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {section === 'rules' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <button className="btn primary sm" type="button" onClick={() => openRuleModal()}>
              + {t('instanceConfig.permissions.newRule')}
            </button>
            <span className="text-[11px] text-muted">{t('instanceConfig.permissions.rulesHint')}</span>
          </div>
          <div className="card !p-0">
            {form.rules.length === 0 ? (
              <Empty text={t('common.empty')} />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('instanceConfig.permissions.ruleId')}</th>
                    <th>{t('instanceConfig.permissions.rulePattern')}</th>
                    <th>{t('instanceConfig.permissions.action')}</th>
                    <th>{t('common.detail')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {form.rules.map((row) => (
                    <tr key={row.key}>
                      <td className="mono text-xs">{row.id}</td>
                      <td className="mono text-[11px] text-muted" title={row.pattern}>
                        {truncate(row.pattern, 48)}
                      </td>
                      <td>
                        <span className="tag">{row.action}</span>
                      </td>
                      <td className="text-xs text-muted">{truncate(row.description, 40)}</td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button className="btn sm ghost" type="button" onClick={() => openRuleModal(row)}>
                            {t('common.edit')}
                          </button>
                          <button
                            className="btn sm danger"
                            type="button"
                            onClick={() => updateForm('rules', form.rules.filter((it) => it.key !== row.key))}
                          >
                            {t('common.delete')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {section === 'fileGuard' && (
        <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2 text-sm font-medium">{t('instanceConfig.permissions.fileGuardWorkspace')}</div>
          <div className="md:col-span-2">
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.fileGuardWorkspaceRwEnabled}
                onChange={(e) => updateForm('fileGuardWorkspaceRwEnabled', e.target.checked)}
              />
              <span>{t('instanceConfig.permissions.fileGuardRwEnabled')}</span>
            </label>
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('instanceConfig.permissions.fileGuardGlobal')}
              value={form.fileGuardGlobalJson}
              onChange={(v) => updateForm('fileGuardGlobalJson', v)}
              rows={6}
            />
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('instanceConfig.permissions.fileGuardTrustedExec')}
              value={form.fileGuardTrustedExecJson}
              onChange={(v) => updateForm('fileGuardTrustedExecJson', v)}
              rows={4}
            />
          </div>
          <div className="md:col-span-2">
            <JsonField
              label={t('instanceConfig.permissions.fileGuardToolBindings')}
              value={form.fileGuardToolBindingsJson}
              onChange={(v) => updateForm('fileGuardToolBindingsJson', v)}
              rows={6}
            />
          </div>
        </div>
      )}

      {section === 'advanced' && (
        <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2 text-sm font-medium">{t('instanceConfig.permissions.commandIntent')}</div>
          <div>
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.commandIntentEnabled}
                onChange={(e) => updateForm('commandIntentEnabled', e.target.checked)}
              />
              <span>{t('instanceConfig.permissions.commandIntentEnabled')}</span>
            </label>
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.commandIntentTimeout')}</label>
            <input
              className="input"
              type="number"
              min={1}
              value={form.commandIntentTimeout}
              onChange={(e) => updateForm('commandIntentTimeout', Number(e.target.value) || 15)}
            />
          </div>
        </div>
      )}

      <Modal
        open={toolModalOpen}
        title={editingTool ? t('instanceConfig.permissions.editTool') : t('instanceConfig.permissions.newTool')}
        onClose={() => setToolModalOpen(false)}
        footer={
          <>
            <button className="btn ghost" type="button" onClick={() => setToolModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" type="button" onClick={submitTool}>
              {t('common.ok')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-3">
          <div>
            <label className="label">{t('instanceConfig.permissions.toolName')}</label>
            <input
              className="input mono"
              value={toolForm.name}
              onChange={(e) => setToolForm((s) => ({ ...s, name: e.target.value }))}
              disabled={!!editingTool}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.action')}</label>
            <select
              className="select"
              value={toolForm.action}
              onChange={(e) => setToolForm((s) => ({ ...s, action: e.target.value as PermissionAction }))}
            >
              {PERMISSION_ACTIONS.map((action) => (
                <option key={action} value={action}>
                  {action}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Modal>

      <Modal
        open={ruleModalOpen}
        title={editingRule ? t('instanceConfig.permissions.editRule') : t('instanceConfig.permissions.newRule')}
        onClose={() => setRuleModalOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" type="button" onClick={() => setRuleModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" type="button" onClick={submitRule}>
              {t('common.ok')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label">{t('instanceConfig.permissions.ruleId')}</label>
            <input
              className="input mono"
              value={ruleForm.id}
              onChange={(e) => setRuleForm((s) => ({ ...s, id: e.target.value }))}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.action')}</label>
            <select
              className="select"
              value={ruleForm.action}
              onChange={(e) => setRuleForm((s) => ({ ...s, action: e.target.value as PermissionRuleAction }))}
            >
              {RULE_ACTIONS.map((action) => (
                <option key={action} value={action}>
                  {action}
                </option>
              ))}
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.permissions.rulePattern')}</label>
            <input
              className="input mono text-xs"
              value={ruleForm.pattern}
              onChange={(e) => setRuleForm((s) => ({ ...s, pattern: e.target.value }))}
              placeholder="ls *"
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('common.detail')}</label>
            <input
              className="input"
              value={ruleForm.description}
              onChange={(e) => setRuleForm((s) => ({ ...s, description: e.target.value }))}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteOpen}
        message={t('instanceConfig.permissions.deleteConfirm')}
        danger
        onConfirm={() => void removeConfig()}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}
