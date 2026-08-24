import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown } from 'lucide-react';
import type { ModelEntry } from '../../../../types';
import { Button } from '../../../../components/ui';
import { Form, FormDialog, useForm, useFormState } from '../../../../components/form';
import {
  getSettingsProviderLogo48,
  getSettingsProviderLogo16,
  settingsActionIcons,
  settingsEmptyBoxIllustration,
} from '../../../../assets/settings';
import { buildModelValidationPayload, buildModelsSavePayload } from '../../services/settingsContract';
import { OpenAIAccountSettings } from './OpenAIAccountField';
import { SettingsConfirmDialog, SettingsSection } from '../../components';
import { useSettingsServices } from '../../services/SettingsServicesProvider';

type ModelDraft = Pick<ModelEntry, 'model_name' | 'api_base' | 'api_key' | 'model_provider'> & {
  alias: string;
  reasoning_level: string;
  is_default: boolean;
};

const providers = [
  'OpenAI',
  'OpenAIAccount',
  'OpenRouter',
  'DashScope',
  'SiliconFlow',
  'InferenceAffinity',
  'DeepSeek',
];
const reasoningLevels = ['', 'off', 'low', 'medium', 'high'];

type ProviderMenuPosition = {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
};

function ModelProviderSelect({
  id,
  value,
  disabled,
  invalid,
  onChange,
  onBlur,
}: {
  id: string;
  value: string;
  disabled: boolean;
  invalid: boolean;
  onChange: (value: string) => void;
  onBlur: () => void;
}) {
  const { t } = useTranslation();
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() => Math.max(0, providers.indexOf(value)));
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null);
  const [position, setPosition] = useState<ProviderMenuPosition | null>(null);
  const selectedLogo = getSettingsProviderLogo16(value);

  useLayoutEffect(() => {
    setPortalHost(rootRef.current?.closest('dialog') ?? document.body);
  }, []);

  const updatePosition = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const gap = 6;
    const viewportPadding = 16;
    const desiredHeight = 300;
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding - gap;
    const spaceAbove = rect.top - viewportPadding - gap;
    const openBelow = spaceBelow >= Math.min(desiredHeight, spaceAbove);
    const availableHeight = openBelow ? spaceBelow : spaceAbove;
    const base = {
      left: Math.max(viewportPadding, Math.min(rect.left, window.innerWidth - rect.width - viewportPadding)),
      width: rect.width,
      maxHeight: Math.max(120, Math.min(desiredHeight, availableHeight)),
    };
    setPosition(
      openBelow
        ? { ...base, top: rect.bottom + gap }
        : { ...base, bottom: window.innerHeight - rect.top + gap },
    );
  }, []);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(Math.max(0, providers.indexOf(value)));
    updatePosition();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setOpen(false);
        onBlur();
      }
    };
    const handleViewportChange = () => updatePosition();
    document.addEventListener('pointerdown', handlePointerDown, true);
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true);
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
    };
  }, [onBlur, open, updatePosition, value]);

  const selectProvider = (provider: string) => {
    onChange(provider);
    onBlur();
    setOpen(false);
    buttonRef.current?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) => (current + direction + providers.length) % providers.length);
      return;
    }
    if (event.key === 'Home' && open) {
      event.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (event.key === 'End' && open) {
      event.preventDefault();
      setActiveIndex(providers.length - 1);
      return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault();
      selectProvider(providers[activeIndex]);
      return;
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
      return;
    }
    if (event.key === 'Tab' && open) {
      setOpen(false);
      onBlur();
    }
  };

  return (
    <div className="settings-model-provider-select" ref={rootRef}>
      <button
        ref={buttonRef}
        id={id}
        type="button"
        className="settings-model-provider-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? `${listboxId}-${activeIndex}` : undefined}
        aria-invalid={invalid || undefined}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleKeyDown}
      >
        <span className="settings-model-provider-select__value">
          {selectedLogo ? <img src={selectedLogo} alt="" aria-hidden /> : null}
          <span>{value}</span>
        </span>
        <ChevronDown aria-hidden />
      </button>
      {open && portalHost && position
        ? createPortal(
            <div
              ref={menuRef}
              id={listboxId}
              className="settings-model-provider-select__menu"
              role="listbox"
              aria-label={t('settingsPanel.fields.model_provider.title')}
              style={position}
            >
              {providers.map((provider, index) => {
                const logo = getSettingsProviderLogo16(provider);
                const selected = provider === value;
                return (
                  <button
                    id={`${listboxId}-${index}`}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className="settings-model-provider-select__option"
                    data-active={activeIndex === index || undefined}
                    key={provider}
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => selectProvider(provider)}
                  >
                    <span>
                      {logo ? <img src={logo} alt="" aria-hidden /> : null}
                      {provider}
                    </span>
                    {selected ? <Check aria-hidden /> : null}
                  </button>
                );
              })}
            </div>,
            portalHost,
          )
        : null}
    </div>
  );
}

type ModelConfirmation =
  | { type: 'group-default'; model: ModelEntry; index: number; message: string }
  | { type: 'delete'; model: ModelEntry; index: number; message: string };

type ValidationToast = { success: boolean; message: string };

function modelIdentity(model: ModelEntry, index: number): string {
  return `${model.origin_index ?? index}:${model.model_name}:${model.alias ?? ''}:${index}`;
}

function initialModel(model?: ModelEntry): ModelDraft {
  if (!model) {
    return {
      model_name: '',
      alias: '',
      api_base: '',
      api_key: '',
      model_provider: 'OpenAI',
      reasoning_level: '',
      is_default: false,
    };
  }

  return {
    model_name: model.model_name,
    alias: model.alias ?? '',
    api_base: model.api_base,
    api_key: model.api_key,
    model_provider: model.model_provider,
    reasoning_level: model.reasoning_level ?? '',
    is_default: model.is_default ?? false,
  };
}

function validateModel(
  value: ModelDraft,
  models: ModelEntry[],
  index: number | null,
  t: (key: string, values?: Record<string, unknown>) => string,
): Partial<Record<keyof ModelDraft, string>> {
  const errors: Partial<Record<keyof ModelDraft, string>> = {};
  const modelName = value.model_name.trim();
  const alias = value.alias.trim();
  const apiBase = value.api_base.trim();
  const apiKey = value.api_key.trim();
  const provider = value.model_provider.trim();

  if (!modelName) errors.model_name = t('config.modelList.modelNameRequired');
  else if (modelName.length > 100) errors.model_name = t('config.modelList.modelNameTooLong');
  if (alias.length > 100) errors.alias = t('config.modelList.aliasTooLong');
  if (!provider) errors.model_provider = t('settingsPanel.models.validation.modelProviderRequired');
  if (!apiBase) errors.api_base = t('config.modelList.apiBaseRequired');
  else if (apiBase.length > 512) errors.api_base = t('config.modelList.apiBaseTooLong');
  else if (!/^https?:\/\//i.test(apiBase)) errors.api_base = t('config.modelList.apiBaseUrlInvalid');
  if (apiKey.length > 500) errors.api_key = t('config.modelList.apiKeyTooLong');
  else if (provider !== 'OpenAIAccount' && !apiKey) errors.api_key = t('config.modelList.apiKeyRequired');

  const otherModels = models.filter((_, currentIndex) => currentIndex !== index);
  if (
    !errors.alias &&
    alias &&
    otherModels.some((model) => model.alias?.trim() === alias || model.model_name.trim() === alias)
  ) {
    errors.alias = t('settingsPanel.models.validation.aliasConflict');
  }
  if (!errors.model_name && modelName && otherModels.some((model) => model.alias?.trim() === modelName)) {
    errors.model_name = t('settingsPanel.models.validation.modelNameConflict');
  }
  return errors;
}

function ModelDialog({
  model,
  index,
  models,
  onClose,
  onSave,
}: {
  model?: ModelEntry;
  index: number | null;
  models: ModelEntry[];
  onClose: () => void;
  onSave: (model: ModelEntry) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { isConnected, request } = useSettingsServices();
  const form = useForm({ initialValues: initialModel(model) });
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState('');
  const [saveError, setSaveError] = useState('');
  const [accountBlocking, setAccountBlocking] = useState(false);
  const validationRequestId = useRef(0);
  useFormState(form);
  const values = form.getValues();
  const errors = validateModel(values, models, index, t);
  const account = values.model_provider === 'OpenAIAccount';

  useEffect(
    () => () => {
      validationRequestId.current += 1;
    },
    [],
  );

  const save = async () => {
    if (Object.keys(errors).length) {
      form.validate();
      return;
    }
    setSubmitting(true);
    setSaveError('');
    try {
      await onSave({
        ...values,
        model_name: values.model_name.trim(),
        alias: values.alias.trim(),
        api_base: values.api_base.trim(),
        api_key: values.api_key.trim(),
        model_provider: values.model_provider.trim(),
        reasoning_level: values.reasoning_level.trim(),
      });
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : t('settingsPanel.feedback.saveFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  const test = async () => {
    if (Object.keys(errors).length) {
      form.validate();
      return;
    }
    const currentRequestId = ++validationRequestId.current;
    setTesting(true);
    setMessage('');
    try {
      await request('config.validate_model', buildModelValidationPayload(values), { timeoutMs: 60_000 });
      if (currentRequestId === validationRequestId.current) setMessage(t('settingsPanel.models.validationOk'));
    } catch (error) {
      if (currentRequestId === validationRequestId.current) {
        setMessage(error instanceof Error ? error.message : t('settingsPanel.models.validationFailed'));
      }
    } finally {
      if (currentRequestId === validationRequestId.current) setTesting(false);
    }
  };

  return (
    <FormDialog
      open
      title={t(index === null ? 'settingsPanel.models.addModel' : 'settingsPanel.models.editModel')}
      submitting={submitting}
      confirmDisabled={accountBlocking || !isConnected}
      confirmLabel={t('common.confirm')}
      cancelLabel={t('common.cancel')}
      secondaryAction={
        <Button disabled={testing || submitting || accountBlocking || !isConnected} onClick={() => void test()}>
          {testing ? t('common.loading') : t('settingsPanel.models.test')}
        </Button>
      }
      onConfirm={() => void save()}
      onCancel={onClose}
    >
      <Form
        form={form}
        optionalText={t('common.optional')}
        rules={{
          model_name: [{ validator: () => errors.model_name }],
          alias: [{ validator: () => errors.alias }],
          model_provider: [{ validator: () => errors.model_provider }],
          api_base: [{ validator: () => errors.api_base }],
          api_key: [{ validator: () => errors.api_key }],
        }}
        items={[
          {
            name: 'model_name',
            label: t('settingsPanel.fields.model_name.title'),
            helpTips: t('config.keyHelp.modelName'),
            component: 'input',
            required: true,
            placeholder: t('settingsPanel.fields.model_name.placeholder'),
            disabled: account,
          },
          {
            name: 'alias',
            label: t('settingsPanel.fields.alias.title'),
            helpTips: t('config.keyHelp.alias'),
            component: 'input',
            placeholder: t('settingsPanel.fields.alias.placeholder'),
          },
          {
            name: 'model_provider',
            label: t('settingsPanel.fields.model_provider.title'),
            helpTips: t('config.keyHelp.modelProvider'),
            component: 'custom',
            required: true,
            render: ({ id, value, error, disabled, onChange, onBlur }) => (
              <ModelProviderSelect
                id={id}
                value={String(value ?? '')}
                disabled={disabled}
                invalid={Boolean(error)}
                onChange={onChange}
                onBlur={onBlur}
              />
            ),
          },
          {
            name: 'reasoning_level',
            label: t('settingsPanel.fields.reasoning_level.title'),
            component: 'select',
            options: reasoningLevels.map((value) => ({
              value,
              label: value || t('config.modelList.reasoningDefault'),
            })),
          },
          {
            name: 'api_base',
            label: t('settingsPanel.fields.api_base.title'),
            helpTips: t('config.keyHelp.apiBase'),
            component: 'input',
            required: true,
            disabled: account,
            placeholder: account
              ? t('config.openaiAccount.apiBaseManaged')
              : t('settingsPanel.fields.api_base.placeholder'),
          },
          {
            name: 'api_key',
            label: t('settingsPanel.fields.api_key.title'),
            helpTips: t('config.keyHelp.apiKey'),
            component: 'input',
            type: 'password',
            required: !account,
            disabled: account,
            passwordVisibilityLabels: {
              show: t('settingsPanel.common.showValue'),
              hide: t('settingsPanel.common.hideValue'),
            },
            placeholder: account
              ? t('config.openaiAccount.apiKeyNotNeeded')
              : t('settingsPanel.fields.api_key.placeholder'),
          },
        ]}
      />
      {account ? (
        <OpenAIAccountSettings
          model={values as ModelEntry}
          connected={isConnected}
          disabled={submitting}
          request={request}
          onModelPatch={(patch) => form.setValues(patch)}
          onBlockingChange={setAccountBlocking}
        />
      ) : null}
      {message ? (
        <div className="settings-model-dialog__message" role="status">
          {message}
        </div>
      ) : null}
      {saveError ? (
        <div className="settings-page__error" role="alert">
          {saveError}
        </div>
      ) : null}
    </FormDialog>
  );
}

export function ModelsSettings() {
  const { t } = useTranslation();
  const { isConnected, request, saveQueue } = useSettingsServices();
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [dialog, setDialog] = useState<{ index: number | null; model?: ModelEntry } | null>(null);
  const [confirmation, setConfirmation] = useState<ModelConfirmation | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState('');
  const [validationStates, setValidationStates] = useState<Record<string, 'testing' | 'success'>>({});
  const [validationToast, setValidationToast] = useState<ValidationToast | null>(null);
  const loadRequestId = useRef(0);
  const validationRequestIds = useRef<Record<string, number>>({});
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const duplicateModelNames = useMemo(() => {
    const counts = new Map<string, number>();
    models.forEach((model) => counts.set(model.model_name, (counts.get(model.model_name) ?? 0) + 1));
    return counts;
  }, [models]);

  const load = useCallback(async () => {
    if (!isConnected) {
      setLoading(false);
      return;
    }
    const currentRequestId = ++loadRequestId.current;
    setLoading(true);
    setLoadError('');
    try {
      const modelPayload = await request<{ models?: ModelEntry[] }>('models.list');
      if (currentRequestId !== loadRequestId.current) return;
      setModels(modelPayload.models?.filter((model) => model.is_free !== true) ?? []);
    } catch (error) {
      if (currentRequestId === loadRequestId.current) {
        setLoadError(error instanceof Error ? error.message : t('settingsPanel.models.loadFailed'));
      }
    } finally {
      if (currentRequestId === loadRequestId.current) setLoading(false);
    }
  }, [isConnected, request, t]);

  useEffect(() => {
    void load();
    return () => {
      loadRequestId.current += 1;
      Object.keys(validationRequestIds.current).forEach((key) => {
        validationRequestIds.current[key] += 1;
      });
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [load]);

  const showValidationToast = (toast: ValidationToast) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setValidationToast(toast);
    toastTimer.current = setTimeout(() => {
      setValidationToast(null);
      toastTimer.current = null;
    }, 3000);
  };

  const saveModels = async (nextModels: ModelEntry[], operation: string) => {
    const previous = models;
    setModels(nextModels);
    try {
      await saveQueue.enqueue(operation, () =>
        request('config.save_all', buildModelsSavePayload(nextModels), { timeoutMs: 600_000 }),
      );
      await load();
    } catch (error) {
      setModels(previous);
      throw error;
    }
  };

  const requestGroupDefaultConfirmation = (model: ModelEntry, index: number) => {
    const isPrimaryGroup = model.model_name === models[0]?.model_name;
    const confirmationKey = isPrimaryGroup
      ? 'settingsPanel.models.setPrimaryGroupDefaultConfirm'
      : 'settingsPanel.models.setGroupDefaultConfirm';
    setConfirmError('');
    setConfirmation({
      type: 'group-default',
      model,
      index,
      message: t(confirmationKey, { model: model.model_name }),
    });
  };

  const confirmModelOperation = async () => {
    if (!confirmation || confirming) return;
    setConfirming(true);
    setConfirmError('');
    try {
      if (confirmation.type === 'delete') {
        await saveModels(
          models.filter((_, currentIndex) => currentIndex !== confirmation.index),
          'model.delete',
        );
      } else {
        const { model, index } = confirmation;
        const isPrimaryGroup = model.model_name === models[0]?.model_name;
        const updated = models.map((candidate, candidateIndex) =>
          candidate.model_name === model.model_name ? { ...candidate, is_default: candidateIndex === index } : candidate,
        );
        const nextModels = isPrimaryGroup
          ? [{ ...model, is_default: true }, ...updated.filter((_, candidateIndex) => candidateIndex !== index)]
          : updated;
        await saveModels(nextModels, 'model.group_default');
      }
      setConfirmation(null);
    } catch (error) {
      setConfirmError(error instanceof Error ? error.message : t('settingsPanel.feedback.saveFailed'));
    } finally {
      setConfirming(false);
    }
  };

  const testSavedModel = async (model: ModelEntry, index: number) => {
    const key = modelIdentity(model, index);
    const requestId = (validationRequestIds.current[key] ?? 0) + 1;
    validationRequestIds.current[key] = requestId;
    setValidationStates((current) => ({ ...current, [key]: 'testing' }));
    try {
      await request('config.validate_model', buildModelValidationPayload(model), { timeoutMs: 60_000 });
      if (validationRequestIds.current[key] !== requestId) return;
      setValidationStates((current) => ({ ...current, [key]: 'success' }));
      showValidationToast({ success: true, message: t('settingsPanel.models.validationOk') });
    } catch (error) {
      if (validationRequestIds.current[key] !== requestId) return;
      setValidationStates((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      showValidationToast({
        success: false,
        message: error instanceof Error ? error.message : t('settingsPanel.models.validationFailed'),
      });
    }
  };

  const setPrimary = async (model: ModelEntry, index: number) => {
    await saveModels(
      [
        { ...model, is_default: true },
        ...models
          .filter((_, currentIndex) => currentIndex !== index)
          .map((candidate) =>
            candidate.model_name === model.model_name ? { ...candidate, is_default: false } : candidate,
          ),
      ],
      'model.primary',
    );
  };

  return (
    <>
      <SettingsSection
        title={t('settingsPanel.models.primaryModels')}
        description={t('settingsPanel.models.primaryModelsDescription')}
        action={
          <>
            <Button
              icon={<settingsActionIcons.refresh aria-hidden />}
              aria-label={t(loading ? 'common.refreshing' : 'common.refresh')}
              title={t(loading ? 'common.refreshing' : 'common.refresh')}
              loading={loading}
              disabled={!isConnected}
              onClick={() => void load()}
            />
            <Button variant="primary" disabled={!isConnected || loading} onClick={() => setDialog({ index: null })}>
              {t('settingsPanel.models.addModel')}
            </Button>
          </>
        }
      >
        {loadError ? (
          <div className="settings-page__error" role="alert">
            {loadError}
          </div>
        ) : null}
        {!loadError && !loading && models.length === 0 ? (
          <div className="settings-models__empty">
            <img src={settingsEmptyBoxIllustration} alt="" aria-hidden />
            <strong>{t('settingsPanel.models.empty')}</strong>
            <p>{t('settingsPanel.models.emptyDescription')}</p>
            <Button variant="primary" disabled={!isConnected} onClick={() => setDialog({ index: null })}>
              {t('settingsPanel.models.addModel')}
            </Button>
          </div>
        ) : null}
        <div className="settings-models__list">
          {models.map((model, index) => {
            const key = modelIdentity(model, index);
            const logo = getSettingsProviderLogo48(model.model_provider);
            const validationState = validationStates[key];
            const isDuplicate = (duplicateModelNames.get(model.model_name) ?? 0) > 1;
            return (
              <article className={`settings-model-card${logo ? '' : ' settings-model-card--no-logo'}`} key={key}>
                {logo ? <img className="settings-model-card__logo" src={logo} alt="" aria-hidden /> : null}
                <div className="settings-model-card__copy">
                  <div className="settings-model-card__title-row">
                    <h3 title={model.alias || model.model_name}>{model.alias || model.model_name}</h3>
                    {validationState === 'success' ? (
                      <Check
                        className="settings-model-card__validated"
                        aria-label={t('settingsPanel.models.validationOk')}
                      />
                    ) : null}
                    {index === 0 ? (
                      <span className="settings-page__badge">{t('settingsPanel.models.primary')}</span>
                    ) : null}
                    {isDuplicate && model.is_default ? (
                      <span className="settings-model-card__group-default">{t('settingsPanel.models.groupDefault')}</span>
                    ) : null}
                  </div>
                  <p title={`${model.model_provider} · ${model.model_name}`}>
                    {model.model_provider} · {model.model_name}
                  </p>
                </div>
                <div className="settings-model-card__actions">
                  {index !== 0 ? (
                    <Button
                      variant="quiet"
                      size="sm"
                      disabled={!isConnected}
                      onClick={() => void setPrimary(model, index).catch(() => undefined)}
                    >
                      {t('settingsPanel.models.setPrimary')}
                    </Button>
                  ) : null}
                  {isDuplicate && !model.is_default ? (
                    <Button
                      variant="quiet"
                      size="sm"
                      disabled={!isConnected}
                      onClick={() => requestGroupDefaultConfirmation(model, index)}
                    >
                      {t('settingsPanel.models.setGroupDefault')}
                    </Button>
                  ) : null}
                  <Button
                    icon={<settingsActionIcons.refresh aria-hidden />}
                    aria-label={t('settingsPanel.models.testConnection')}
                    title={t('settingsPanel.models.testConnection')}
                    loading={validationState === 'testing'}
                    disabled={!isConnected}
                    onClick={() => void testSavedModel(model, index)}
                  />
                  <Button
                    icon={<settingsActionIcons.edit aria-hidden />}
                    aria-label={t('common.modify')}
                    title={t('common.modify')}
                    disabled={!isConnected}
                    onClick={() => setDialog({ index, model })}
                  />
                  <Button
                    variant="quiet"
                    icon={<settingsActionIcons.delete aria-hidden />}
                    aria-label={t('common.delete')}
                    title={t('common.delete')}
                    disabled={!isConnected || models.length <= 1}
                    onClick={() => {
                      setConfirmError('');
                      setConfirmation({
                        type: 'delete',
                        model,
                        index,
                        message: t('settingsPanel.models.deleteConfirm', { model: model.model_name }),
                      });
                    }}
                  />
                </div>
              </article>
            );
          })}
        </div>
      </SettingsSection>
      {dialog ? (
        <ModelDialog
          model={dialog.model}
          index={dialog.index}
          models={models}
          onClose={() => setDialog(null)}
          onSave={async (next) => {
            const nextModels =
              dialog.index === null
                ? [...models, next]
                : models.map((current, currentIndex) => (currentIndex === dialog.index ? next : current));
            await saveModels(nextModels, dialog.index === null ? 'model.add' : 'model.edit');
          }}
        />
      ) : null}
      <SettingsConfirmDialog
        open={confirmation !== null}
        title={t(
          confirmation?.type === 'delete'
            ? 'settingsPanel.models.deleteConfirmTitle'
            : 'settingsPanel.models.groupDefaultConfirmTitle',
        )}
        message={confirmation?.message ?? ''}
        confirming={confirming}
        error={confirmError}
        onCancel={() => {
          if (!confirming) setConfirmation(null);
        }}
        onConfirm={() => void confirmModelOperation()}
      />
      {validationToast ? (
        <div
          className={`settings-models__toast settings-models__toast--${validationToast.success ? 'success' : 'error'}`}
          role={validationToast.success ? 'status' : 'alert'}
          aria-live="polite"
        >
          {validationToast.success ? <Check aria-hidden /> : null}
          <span>{validationToast.message}</span>
        </div>
      ) : null}
    </>
  );
}
