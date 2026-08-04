import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Modal } from '../../components/Modal';
import { ApiError, EmbeddingTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type {
  EmbeddingTemplate,
  EmbeddingTemplateCreateBody,
  EmbeddingTemplateUpdateBody,
} from '../../types';
import { fromCommaList, toCommaList } from '../../utils/format';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidHttpUrl } from '../../utils/url';

interface Props {
  open: boolean;
  template: EmbeddingTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  api_base: 512,
  api_key: 4096,
  model_id: 128,
} as const;

const MODEL_PROVIDER_OPTIONS = ['openai'] as const;
const DEFAULT_MODEL_PROVIDER = MODEL_PROVIDER_OPTIONS[0];

interface FormState {
  template_name: string;
  description: string;
  embed_tags: string;
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
}

const empty: FormState = {
  template_name: '',
  description: '',
  embed_tags: '',
  api_base: '',
  api_key: '',
  model_id: '',
  model_provider: DEFAULT_MODEL_PROVIDER,
};

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required ? <span className="text-danger ml-0.5" aria-hidden>*</span> : null}
    </label>
  );
}

export function EmbeddingTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(
      template
        ? {
            template_name: template.template_name,
            description: template.description ?? '',
            embed_tags: toCommaList(template.embed_tags),
            api_base: template.api_base,
            api_key: template.api_key,
            model_id: template.model_id,
            model_provider: DEFAULT_MODEL_PROVIDER,
          }
        : empty,
    );
  }, [open, template]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    const required = [
      ['templateName', form.template_name],
      ['apiBase', form.api_base],
      ['apiKey', form.api_key],
      ['modelId', form.model_id],
    ] as const;
    const missing = required.find(([, value]) => !value.trim());
    if (missing) {
      toast(
        'warn',
        t('embeddingTemplate.fieldRequired', { field: t(`embeddingTemplate.${missing[0]}`) }),
      );
      return;
    }
    if (!isValidHttpUrl(form.api_base)) {
      toast('warn', t('embeddingTemplate.apiBaseInvalid'));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('embeddingTemplate.templateName'), value: form.template_name },
      { label: t('embeddingTemplate.templateDescription'), value: form.description },
      { label: t('embeddingTemplate.modelId'), value: form.model_id },
      ...(fromCommaList(form.embed_tags) ?? []).map((tag) => ({
        label: t('embeddingTemplate.embedTags'),
        value: tag,
      })),
    ]);
    if (unsafeField) {
      toast('warn', t('embeddingTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const body: EmbeddingTemplateCreateBody | EmbeddingTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      embed_tags: fromCommaList(form.embed_tags),
      api_base: form.api_base.trim(),
      api_key: form.api_key,
      model_id: form.model_id.trim(),
      model_provider: DEFAULT_MODEL_PROVIDER,
      enabled: true,
    };

    setSaving(true);
    try {
      if (template) {
        await EmbeddingTemplateApi.update(template.template_id, body);
      } else {
        await EmbeddingTemplateApi.create(body as EmbeddingTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
      toast(
        'danger',
        t('errors.saveFailed', {
          detail: error instanceof ApiError ? error.detail : (error as Error).message,
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('embeddingTemplate.edit') : t('embeddingTemplate.new')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <FieldLabel required>{t('embeddingTemplate.templateName')}</FieldLabel>
          <LimitedTextInput value={form.template_name} maxLength={FIELD_MAX_LENGTH.template_name} onChange={(value) => update('template_name', value)} />
        </div>
        <div>
          <FieldLabel required>{t('embeddingTemplate.modelProvider')}</FieldLabel>
          <select
            className="select"
            value={form.model_provider}
            onChange={(event) => update('model_provider', event.target.value)}
          >
            {MODEL_PROVIDER_OPTIONS.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </div>
        <div className="md:col-span-2">
          <FieldLabel required>{t('embeddingTemplate.apiBase')}</FieldLabel>
          <LimitedTextInput value={form.api_base} maxLength={FIELD_MAX_LENGTH.api_base} onChange={(value) => update('api_base', value)} />
        </div>
        <div>
          <FieldLabel required>{t('embeddingTemplate.apiKey')}</FieldLabel>
          <LimitedTextInput type="password" value={form.api_key} maxLength={FIELD_MAX_LENGTH.api_key} onChange={(value) => update('api_key', value)} />
        </div>
        <div>
          <FieldLabel required>{t('embeddingTemplate.modelId')}</FieldLabel>
          <LimitedTextInput value={form.model_id} maxLength={FIELD_MAX_LENGTH.model_id} onChange={(value) => update('model_id', value)} />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('embeddingTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput value={form.description} maxLength={FIELD_MAX_LENGTH.description} onChange={(value) => update('description', value)} />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('embeddingTemplate.embedTags')}</FieldLabel>
          <input className="input" value={form.embed_tags} placeholder={t('embeddingTemplate.embedTagsHint')} onChange={(event) => update('embed_tags', event.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
