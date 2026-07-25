import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ModelTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { fromCommaList, safeStringify, toCommaList } from '../../utils/format';
import { isValidHttpUrl } from '../../utils/url';
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

const MODEL_TYPE_OPTIONS = ['default', 'video', 'audio', 'vision'] as const;

/** 与 model_template 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  api_base: 512,
  api_key: 4096,
  model_id: 128,
  model_provider: 64,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

/** 与 openjiuwen ProviderType / 运行时校验一致 */
const MODEL_PROVIDER_OPTIONS = [
  'OpenAI',
  'OpenRouter',
  'DashScope',
  'SiliconFlow',
  'InferenceAffinity',
] as const;

function normalizeModelProvider(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return 'OpenAI';
  const lookup = Object.fromEntries(
    MODEL_PROVIDER_OPTIONS.map((p) => [p.toLowerCase(), p]),
  ) as Record<string, string>;
  return lookup[trimmed.toLowerCase()] ?? trimmed;
}

function providerSelectOptions(current: string): string[] {
  const normalized = normalizeModelProvider(current);
  const known = new Set<string>(MODEL_PROVIDER_OPTIONS);
  if (normalized && !known.has(normalized)) {
    return [normalized, ...MODEL_PROVIDER_OPTIONS];
  }
  return [...MODEL_PROVIDER_OPTIONS];
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  template_name: string;
  description: string;
  model_type: string[];
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
}

const empty: FormState = {
  template_name: '',
  description: '',
  model_type: [],
  model_tags: '',
  api_base: '',
  api_key: '',
  model_id: '',
  model_provider: 'OpenAI',
  parameters: '',
  timeout: 60,
  retry_count: 3,
  enable_streaming: true,
  enable_function_calling: true,
  verify_ssl: false,
};

export function ModelTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);
  const [typeOpen, setTypeOpen] = useState(false);
  const typeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    if (template) {
      const types = template.model_type ?? [];
      setForm({
        template_name: clipField(template.template_name, FIELD_MAX_LENGTH.template_name),
        description: clipField(template.description ?? '', FIELD_MAX_LENGTH.description),
        model_type: types,
        model_tags: toCommaList(template.model_tags),
        api_base: clipField(template.api_base, FIELD_MAX_LENGTH.api_base),
        api_key: clipField(template.api_key, FIELD_MAX_LENGTH.api_key),
        model_id: clipField(template.model_id, FIELD_MAX_LENGTH.model_id),
        model_provider: clipField(
          normalizeModelProvider(template.model_provider),
          FIELD_MAX_LENGTH.model_provider,
        ),
        parameters: safeStringify(template.parameters ?? {}, 2),
        timeout: template.timeout,
        retry_count: template.retry_count,
        enable_streaming: template.enable_streaming,
        enable_function_calling: template.enable_function_calling,
        verify_ssl: template.verify_ssl,
      });
    } else {
      setForm(empty);
    }
    setTypeOpen(false);
  }, [open, template]);

  useEffect(() => {
    if (!typeOpen) return;
    const onClick = (e: MouseEvent) => {
      if (typeRef.current && !typeRef.current.contains(e.target as Node)) {
        setTypeOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [typeOpen]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const toggleModelType = (type: string) => {
    setForm((s) => ({
      ...s,
      model_type: s.model_type.includes(type)
        ? s.model_type.filter((x) => x !== type)
        : [...s.model_type, type],
    }));
  };

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('modelTemplate.templateName'), invalid: !form.template_name.trim() },
      { label: t('modelTemplate.modelProvider'), invalid: !form.model_provider.trim() },
      { label: t('modelTemplate.modelId'), invalid: !form.model_id.trim() },
      { label: t('modelTemplate.apiBase'), invalid: !form.api_base.trim() },
      { label: t('modelTemplate.apiKey'), invalid: !form.api_key.trim() },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('modelTemplate.fieldRequired', { field: missing.label }));
      return;
    }
    if (!isValidHttpUrl(form.api_base)) {
      toast('warn', t('modelTemplate.apiBaseInvalid'));
      return;
    }
    if (!Number.isFinite(form.timeout) || form.timeout < 1) {
      toast('warn', t('modelTemplate.timeoutMin'));
      return;
    }
    if (!Number.isFinite(form.retry_count) || form.retry_count < 0) {
      toast('warn', t('modelTemplate.retryCountMin'));
      return;
    }
    const paramErr = checkJson(form.parameters);
    if (paramErr) {
      toast('danger', paramErr);
      return;
    }

    const modelTypeList = form.model_type.filter(Boolean);

    const body: ModelTemplateCreateBody | ModelTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      model_type: modelTypeList,
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
    };

    setSaving(true);
    try {
      if (template) {
        await ModelTemplateApi.update(template.template_id, body);
      } else {
        await ModelTemplateApi.create({ ...body, enabled: true } as ModelTemplateCreateBody);
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
        <div>
          <FieldLabel required>{t('modelTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('modelTemplate.modelProvider')}</FieldLabel>
          <select
            className="select"
            value={form.model_provider}
            onChange={(e) => update('model_provider', e.target.value)}
          >
            {providerSelectOptions(form.model_provider).map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </div>
        <div className="md:col-span-2">
          <FieldLabel required>{t('modelTemplate.apiBase')}</FieldLabel>
          <LimitedTextInput
            value={form.api_base}
            maxLength={FIELD_MAX_LENGTH.api_base}
            onChange={(v) => update('api_base', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('modelTemplate.apiKey')}</FieldLabel>
          <LimitedTextInput
            type="password"
            value={form.api_key}
            maxLength={FIELD_MAX_LENGTH.api_key}
            onChange={(v) => update('api_key', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('modelTemplate.modelId')}</FieldLabel>
          <LimitedTextInput
            value={form.model_id}
            maxLength={FIELD_MAX_LENGTH.model_id}
            onChange={(v) => update('model_id', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('modelTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div>
          <FieldLabel>{t('modelTemplate.modelType')}</FieldLabel>
          <div className="relative" ref={typeRef}>
            <button
              type="button"
              className="select w-full text-left flex items-center justify-between gap-2"
              onClick={() => setTypeOpen((o) => !o)}
            >
              <span className={form.model_type.length ? 'truncate' : 'text-muted'}>
                {form.model_type.length
                  ? form.model_type.join(', ')
                  : t('modelTemplate.modelTypePlaceholder')}
              </span>
              <span className="text-muted text-xs shrink-0">{typeOpen ? '▲' : '▼'}</span>
            </button>
            {typeOpen && (
              <div className="absolute z-20 mt-1 w-full rounded-md border border-border bg-bg shadow-lg py-1">
                {MODEL_TYPE_OPTIONS.map((type) => (
                  <label
                    key={type}
                    className="flex items-center gap-2 px-3 py-2 hover:bg-bg-hover cursor-pointer text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={form.model_type.includes(type)}
                      onChange={() => toggleModelType(type)}
                    />
                    <span>{type}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
        <div>
          <FieldLabel>{t('modelTemplate.modelTags')}</FieldLabel>
          <input
            className="input"
            placeholder={t('modelTemplate.modelTagsHint')}
            value={form.model_tags}
            onChange={(e) => update('model_tags', e.target.value)}
          />
        </div>
        <div>
          <FieldLabel required>{t('modelTemplate.timeout')}</FieldLabel>
          <input
            className="input"
            type="number"
            min={1}
            value={form.timeout}
            onChange={(e) => update('timeout', Number(e.target.value))}
          />
        </div>
        <div>
          <FieldLabel required>{t('modelTemplate.retryCount')}</FieldLabel>
          <input
            className="input"
            type="number"
            min={0}
            value={form.retry_count}
            onChange={(e) => update('retry_count', Number(e.target.value))}
          />
        </div>
        <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
          {[
            ['enable_streaming', t('modelTemplate.enableStreaming')],
            ['enable_function_calling', t('modelTemplate.enableFunctionCalling')],
            ['verify_ssl', t('modelTemplate.verifySsl')],
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
