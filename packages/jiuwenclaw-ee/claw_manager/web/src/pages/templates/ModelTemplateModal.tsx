import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { ModelTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { fromCommaList, safeStringify, toCommaList } from '../../utils/format';
import type {
  ModelTemplate,
  ModelTemplateCreateBody,
  ModelTemplateUpdateBody,
} from '../../types';

interface Props {
  open: boolean;
  template: ModelTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

interface FormState {
  template_name: string;
  description: string;
  model_type: string;
  model_tags: string;
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters: string;
  timeout: number;
  retry_count: number;
  enable_streaming: boolean;
  enable_function_calling: boolean;
  verify_ssl: boolean;
  enabled: boolean;
}

const empty: FormState = {
  template_name: '',
  description: '',
  model_type: 'default',
  model_tags: '',
  api_base: '',
  api_key: '',
  model_id: '',
  model_provider: 'openai',
  parameters: '',
  timeout: 60,
  retry_count: 3,
  enable_streaming: true,
  enable_function_calling: true,
  verify_ssl: true,
  enabled: true,
};

export function ModelTemplateModal({ open, template, onClose, onSaved }: Props) {
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
        model_type: Array.isArray(template.model_type) ? template.model_type.join(',') : template.model_type,
        model_tags: toCommaList(template.model_tags),
        api_base: template.api_base,
        api_key: template.api_key,
        model_id: template.model_id,
        model_provider: template.model_provider,
        parameters: safeStringify(template.parameters ?? {}, 2),
        timeout: template.timeout,
        retry_count: template.retry_count,
        enable_streaming: template.enable_streaming,
        enable_function_calling: template.enable_function_calling,
        verify_ssl: template.verify_ssl,
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
      toast('warn', t('modelTemplate.templateName'));
      return;
    }
    const paramErr = checkJson(form.parameters);
    if (paramErr) {
      toast('danger', paramErr);
      return;
    }

    const modelTypeList = form.model_type
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);

    const body: ModelTemplateCreateBody | ModelTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      model_type: modelTypeList.length > 1 ? modelTypeList : modelTypeList[0] ?? 'default',
      model_tags: fromCommaList(form.model_tags),
      api_base: form.api_base.trim(),
      api_key: form.api_key,
      model_id: form.model_id.trim(),
      model_provider: form.model_provider.trim(),
      parameters: form.parameters.trim() ? tryParseJson(form.parameters, {}) : undefined,
      timeout: form.timeout,
      retry_count: form.retry_count,
      enable_streaming: form.enable_streaming,
      enable_function_calling: form.enable_function_calling,
      verify_ssl: form.verify_ssl,
      enabled: form.enabled,
    };

    setSaving(true);
    try {
      if (template) {
        await ModelTemplateApi.update(template.template_id, body);
      } else {
        await ModelTemplateApi.create(body as ModelTemplateCreateBody);
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
      title={template ? t('modelTemplate.edit') : t('modelTemplate.new')}
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
          <label className="label">{t('modelTemplate.templateName')}</label>
          <input
            className="input"
            value={form.template_name}
            onChange={(e) => update('template_name', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('common.detail')}</label>
          <input className="input" value={form.description} onChange={(e) => update('description', e.target.value)} />
        </div>
        <div>
          <label className="label">{t('modelTemplate.modelProvider')}</label>
          <input
            className="input"
            value={form.model_provider}
            onChange={(e) => update('model_provider', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('modelTemplate.modelId')}</label>
          <input className="input" value={form.model_id} onChange={(e) => update('model_id', e.target.value)} />
        </div>
        <div>
          <label className="label">{t('modelTemplate.modelType')}</label>
          <input
            className="input"
            placeholder={t('modelTemplate.modelTypeHint')}
            value={form.model_type}
            onChange={(e) => update('model_type', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('modelTemplate.modelTags')}</label>
          <input
            className="input"
            placeholder={t('modelTemplate.modelTagsHint')}
            value={form.model_tags}
            onChange={(e) => update('model_tags', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('modelTemplate.apiBase')}</label>
          <input className="input" value={form.api_base} onChange={(e) => update('api_base', e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('modelTemplate.apiKey')}</label>
          <input
            className="input"
            type="password"
            value={form.api_key}
            onChange={(e) => update('api_key', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('modelTemplate.timeout')}</label>
          <input
            className="input"
            type="number"
            min={1}
            value={form.timeout}
            onChange={(e) => update('timeout', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('modelTemplate.retryCount')}</label>
          <input
            className="input"
            type="number"
            min={0}
            value={form.retry_count}
            onChange={(e) => update('retry_count', Number(e.target.value))}
          />
        </div>
        <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          {[
            ['enable_streaming', t('modelTemplate.enableStreaming')],
            ['enable_function_calling', t('modelTemplate.enableFunctionCalling')],
            ['verify_ssl', t('modelTemplate.verifySsl')],
            ['enabled', t('common.enabled')],
          ].map(([k, label]) => (
            <label
              key={k}
              className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 hover:bg-bg-hover"
            >
              <input
                type="checkbox"
                checked={form[k as keyof FormState] as boolean}
                onChange={(e) => update(k as keyof FormState, e.target.checked as never)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('modelTemplate.parameters')}
            value={form.parameters}
            onChange={(v) => update('parameters', v)}
            placeholder='{"temperature": 0.7}'
            rows={5}
          />
        </div>
      </div>
    </Modal>
  );
}
