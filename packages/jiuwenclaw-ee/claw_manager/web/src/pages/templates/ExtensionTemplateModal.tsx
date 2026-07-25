import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ExtensionTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { safeStringify } from '../../utils/format';
import { isValidHookSchedule } from '../../utils/schedule';
import type {
  ExtensionConfigTemplate,
  ExtensionConfigTemplateCreateBody,
  ExtensionConfigTemplateUpdateBody,
  HookConfig,
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
  hook_handler: string;
  hook_params: string;
  hook_schedule: string;
  hook_data: string;
  custom_config: string;
}

/** 与 extension_config_template 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  component: 32,
  hook_type: 32,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

const emptyHookFields = {
  hook_handler: '',
  hook_params: '',
  hook_schedule: '',
  hook_data: '',
};

const empty: FormState = {
  template_name: '',
  description: '',
  component: 'gateway',
  hook_type: 'pre_request',
  ...emptyHookFields,
  custom_config: '',
};

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

function hookConfigToForm(hookConfig: HookConfig | undefined) {
  const hc = hookConfig ?? { handler: '' };
  return {
    hook_handler: hc.handler ?? '',
    hook_params:
      hc.params != null && typeof hc.params === 'object' ? safeStringify(hc.params, 2) : '',
    hook_schedule: hc.schedule ?? '',
    hook_data: hc.data != null && typeof hc.data === 'object' ? safeStringify(hc.data, 2) : '',
  };
}

function buildHookConfig(form: FormState): HookConfig {
  const config: HookConfig = {
    handler: form.hook_handler.trim(),
  };
  if (form.hook_params.trim()) {
    config.params = tryParseJson(form.hook_params, {}) as Record<string, unknown>;
  }
  if (form.hook_schedule.trim()) {
    config.schedule = form.hook_schedule.trim();
  }
  if (form.hook_data.trim()) {
    config.data = tryParseJson(form.hook_data, {}) as Record<string, unknown>;
  }
  return config;
}

export function ExtensionTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: clipField(template.template_name, FIELD_MAX_LENGTH.template_name),
        description: clipField(template.description ?? '', FIELD_MAX_LENGTH.description),
        component: clipField(template.component, FIELD_MAX_LENGTH.component),
        hook_type: clipField(template.hook_type, FIELD_MAX_LENGTH.hook_type),
        ...hookConfigToForm(template.hook_config),
        custom_config: safeStringify(template.custom_config ?? {}, 2),
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('extensionTemplate.templateName'), invalid: !form.template_name.trim() },
      { label: t('extensionTemplate.component'), invalid: !form.component.trim() },
      { label: t('extensionTemplate.hookType'), invalid: !form.hook_type.trim() },
      { label: t('extensionTemplate.hookHandler'), invalid: !form.hook_handler.trim() },
    ];
    if (form.hook_type === 'schedule') {
      requiredChecks.push({
        label: t('extensionTemplate.hookSchedule'),
        invalid: !form.hook_schedule.trim(),
      });
    }
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('extensionTemplate.fieldRequired', { field: missing.label }));
      return;
    }
    if (form.hook_schedule.trim() && !isValidHookSchedule(form.hook_schedule)) {
      toast('warn', t('extensionTemplate.hookScheduleInvalid'));
      return;
    }
    const paramsErr = checkJson(form.hook_params);
    if (paramsErr) {
      toast('danger', paramsErr);
      return;
    }
    const hookDataErr = checkJson(form.hook_data);
    if (hookDataErr) {
      toast('danger', hookDataErr);
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
      hook_config: buildHookConfig(form),
      custom_config: form.custom_config.trim()
        ? (tryParseJson(form.custom_config, {}) as Record<string, unknown>)
        : undefined,
    };

    setSaving(true);
    try {
      if (template) {
        await ExtensionTemplateApi.update(template.template_id, body);
      } else {
        await ExtensionTemplateApi.create({ ...body, enabled: true } as ExtensionConfigTemplateCreateBody);
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
          <FieldLabel required>{t('extensionTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('extensionTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('extensionTemplate.component')}</FieldLabel>
          <select className="select" value={form.component} onChange={(e) => update('component', e.target.value)}>
            <option value="gateway">gateway</option>
            <option value="agent_server">agent_server</option>
          </select>
        </div>
        <div>
          <FieldLabel required>{t('extensionTemplate.hookType')}</FieldLabel>
          <select className="select" value={form.hook_type} onChange={(e) => update('hook_type', e.target.value)}>
            <option value="pre_request">pre_request</option>
            <option value="post_request">post_request</option>
            <option value="error">error</option>
            <option value="schedule">schedule</option>
          </select>
        </div>

        <div className="md:col-span-2 pt-1">
          <FieldLabel required>{t('extensionTemplate.hookConfig')}</FieldLabel>
          <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-bg-accent/30 p-3">
            <div>
              <FieldLabel required>{t('extensionTemplate.hookHandler')}</FieldLabel>
              <input
                className="input"
                placeholder="hooks.auth.pre_request"
                value={form.hook_handler}
                onChange={(e) => update('hook_handler', e.target.value)}
              />
              <div className="text-[11px] text-muted mt-1">{t('extensionTemplate.hookHandlerHint')}</div>
            </div>
            <JsonField
              label={t('extensionTemplate.hookParams')}
              hint={t('extensionTemplate.hookParamsHint')}
              value={form.hook_params}
              onChange={(v) => update('hook_params', v)}
              placeholder='{"log_level": "info"}'
              rows={4}
            />
            <div>
              <FieldLabel required={form.hook_type === 'schedule'}>{t('extensionTemplate.hookSchedule')}</FieldLabel>
              <input
                className="input"
                placeholder="0 */5 * * *"
                value={form.hook_schedule}
                onChange={(e) => update('hook_schedule', e.target.value)}
              />
              <div className="text-[11px] text-muted mt-1">{t('extensionTemplate.hookScheduleHint')}</div>
            </div>
            <JsonField
              label={t('extensionTemplate.hookData')}
              hint={t('extensionTemplate.hookDataHint')}
              value={form.hook_data}
              onChange={(v) => update('hook_data', v)}
              placeholder="{}"
              rows={3}
            />
          </div>
        </div>

        <div className="md:col-span-2">
          <JsonField
            label={t('extensionTemplate.customConfig')}
            hint={t('extensionTemplate.customConfigHint')}
            value={form.custom_config}
            onChange={(v) => update('custom_config', v)}
            placeholder='{"auth_header": "Authorization"}'
            rows={5}
          />
        </div>
      </div>
    </Modal>
  );
}
