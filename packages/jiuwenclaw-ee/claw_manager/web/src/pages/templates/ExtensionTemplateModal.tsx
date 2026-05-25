import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { ExtensionTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { safeStringify } from '../../utils/format';
import type {
  ExtensionConfigTemplate,
  ExtensionConfigTemplateCreateBody,
  ExtensionConfigTemplateUpdateBody,
} from '../../types';

interface Props {
  open: boolean;
  template: ExtensionConfigTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

interface FormState {
  template_name: string;
  description: string;
  component: string;
  hook_type: string;
  hook_config: string;
  custom_config: string;
  enabled: boolean;
}

const empty: FormState = {
  template_name: '',
  description: '',
  component: 'gateway',
  hook_type: 'pre_request',
  hook_config: '{}',
  custom_config: '',
  enabled: true,
};

export function ExtensionTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: template.template_name,
        description: template.description ?? '',
        component: template.component,
        hook_type: template.hook_type,
        hook_config: safeStringify(template.hook_config ?? {}, 2),
        custom_config: safeStringify(template.custom_config ?? {}, 2),
        enabled: template.enabled,
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('extensionTemplate.new'));
      return;
    }
    const hookErr = checkJson(form.hook_config);
    if (hookErr) {
      toast('danger', hookErr);
      return;
    }
    const customErr = checkJson(form.custom_config);
    if (customErr) {
      toast('danger', customErr);
      return;
    }

    const body: ExtensionConfigTemplateCreateBody | ExtensionConfigTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      component: form.component.trim(),
      hook_type: form.hook_type.trim(),
      hook_config: tryParseJson(form.hook_config, {}) as Record<string, unknown>,
      custom_config: form.custom_config.trim()
        ? (tryParseJson(form.custom_config, {}) as Record<string, unknown>)
        : undefined,
      enabled: form.enabled,
    };

    setSaving(true);
    try {
      if (template) {
        await ExtensionTemplateApi.update(template.template_id, body);
      } else {
        await ExtensionTemplateApi.create(body as ExtensionConfigTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('extensionTemplate.edit') : t('extensionTemplate.new')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="md:col-span-2">
          <label className="label">name</label>
          <input
            className="input"
            value={form.template_name}
            onChange={(e) => update('template_name', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">description</label>
          <input className="input" value={form.description} onChange={(e) => update('description', e.target.value)} />
        </div>
        <div>
          <label className="label">{t('extensionTemplate.component')}</label>
          <select className="select" value={form.component} onChange={(e) => update('component', e.target.value)}>
            <option value="gateway">gateway</option>
            <option value="agent_server">agent_server</option>
          </select>
          <div className="text-[11px] text-muted mt-1">{t('extensionTemplate.componentHint')}</div>
        </div>
        <div>
          <label className="label">{t('extensionTemplate.hookType')}</label>
          <select className="select" value={form.hook_type} onChange={(e) => update('hook_type', e.target.value)}>
            <option value="pre_request">pre_request</option>
            <option value="post_request">post_request</option>
            <option value="error">error</option>
            <option value="schedule">schedule</option>
          </select>
          <div className="text-[11px] text-muted mt-1">{t('extensionTemplate.hookTypeHint')}</div>
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('extensionTemplate.hookConfig')}
            value={form.hook_config}
            onChange={(v) => update('hook_config', v)}
            rows={6}
          />
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('extensionTemplate.customConfig')}
            value={form.custom_config}
            onChange={(v) => update('custom_config', v)}
            rows={5}
          />
        </div>
        <div className="md:col-span-2">
          <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => update('enabled', e.target.checked)}
            />
            <span>{t('common.enabled')}</span>
          </label>
        </div>
      </div>
    </Modal>
  );
}
