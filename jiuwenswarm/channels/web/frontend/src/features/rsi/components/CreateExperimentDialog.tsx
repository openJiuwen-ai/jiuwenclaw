/**
 * RSI 创建实验弹窗（三分支：Harness 优化 / 产物优化·论文 / 产物优化·程序）。
 * 字段对齐契约 §6.1 task.create 入参。数据集走 rsi.dataset.validate，
 * 数据集路径复用 path.select_files（单文件），产物路径沿用 path.select_directory。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { rsiDatasetValidate, rsiTaskCreate, rsiTaskList, rsiTrainingStart } from '../rsiApi';
import type { RsiArtifactType, RsiScenario, RsiTaskCreateParams, RsiTaskListItem, RsiTaskStatus } from '../types';
import { selectProjectDirectory } from '../../../features/workspace/projectDirectoryPicker';
import { selectLocalFiles } from '../../../features/workspace/localFilePicker';
import { useSessionStore } from '../../../stores/sessionStore';
import { ModelProviderIcon } from '../../../components/ModelProviderIcon';
import TipIcon from '../../../assets/tip.svg?react';
import type { ModelEntry } from '../../../types';

interface CreateExperimentDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (item: RsiTaskListItem) => void;
}

type Branch = 'HARNESS' | 'PAPER' | 'PROGRAM';

interface FormState {
  name: string;
  scenario: RsiScenario;
  artifactType: RsiArtifactType; // paper | program
  optimizer: string;
  tester: string;
  datasetFile: string;
  maxIterations: number;
  searchWidth: number;
  optimizationInstruction: string;
  artifactPath: string;
}

function defaultForm(): FormState {
  return {
    name: '',
    scenario: 'HARNESS',
    artifactType: 'PAPER',
    optimizer: '',
    tester: '',
    datasetFile: '',
    maxIterations: 2,
    searchWidth: 2,
    optimizationInstruction: '',
    artifactPath: '',
  };
}

export function CreateExperimentDialog({ open, onClose, onCreated }: CreateExperimentDialogProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(defaultForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [datasetValid, setDatasetValid] = useState<null | { valid: boolean; count: number | null }>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const branch: Branch = form.scenario === 'HARNESS' ? 'HARNESS' : form.artifactType;
  const isArtifact = form.scenario === 'ARTIFACT';

  // 选择数据集路径后自动校验
  useEffect(() => {
    if (!form.datasetFile) {
      setDatasetValid(null);
      return;
    }
    let cancelled = false;
    void rsiDatasetValidate({
      input_file: form.datasetFile,
      scenario: form.scenario,
      artifact_type: form.scenario === 'ARTIFACT' ? form.artifactType : undefined,
    })
      .then((res) => {
        if (!cancelled) setDatasetValid({ valid: res.valid, count: res.sample_count });
      })
      .catch(() => {
        if (!cancelled) setDatasetValid({ valid: false, count: null });
      })
      .finally(() => {});
    return () => {
      cancelled = true;
    };
  }, [form.datasetFile, form.scenario, form.artifactType]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // 切换实验场景（Harness 优化 vs 产物优化）
  const switchScenario = useCallback((s: RsiScenario) => {
    setDatasetValid(null);
    setErrors({});
    setForm((f) => {
      if (s === 'HARNESS')
        return {
          ...f,
          scenario: 'HARNESS',
          artifactType: 'PAPER',
          artifactPath: '',
          optimizationInstruction: '',
        };
      // 产物优化默认选论文
      return { ...f, scenario: 'ARTIFACT', artifactType: 'PAPER', tester: '' };
    });
  }, []);

  // 切换产物子类型（论文 / 程序）
  const switchArtifactType = useCallback((at: RsiArtifactType) => {
    setErrors({});
    setForm((f) => {
      if (at === 'PAPER') return { ...f, artifactType: 'PAPER' };
      return {
        ...f,
        artifactType: 'PROGRAM',
        optimizationInstruction: '',
        maxIterations: 3,
      };
    });
  }, []);

  const update = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
  }, []);

  const pickPath = useCallback(
    async (target: 'dataset' | 'artifact') => {
      if (target === 'dataset') {
        const fileResult = await selectLocalFiles(false);
        const file = fileResult.ok ? fileResult.files[0] : undefined;
        if (!file?.path) return;
        update('datasetFile', file.path);
        setDatasetValid(null);
      } else {
        const result = await selectProjectDirectory();
        if (!result.ok || !result.path) return;
        update('artifactPath', result.path);
      }
    },
    [update],
  );

  const validate = useCallback((): boolean => {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = t('rsi.createDialog.validation.nameRequired');
    if (branch === 'HARNESS' && !form.datasetFile) e.dataset = t('rsi.createDialog.validation.datasetRequired');
    if (!form.optimizer) e.optimizer = t('rsi.createDialog.validation.optimizerRequired');
    if (branch === 'HARNESS' && !form.tester) e.tester = t('rsi.createDialog.validation.testerRequired');
    if (branch === 'PAPER') {
      if (!form.optimizationInstruction.trim() && !form.artifactPath) {
        e.paper = t('rsi.createDialog.validation.paperOrInstruction');
      }
    }
    if (branch === 'PROGRAM') {
      if (!form.artifactPath) e.program = t('rsi.createDialog.validation.programRequired');
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }, [form, branch, t]);

  const handleSubmit = useCallback(async () => {
    if (!validate()) return;
    setSubmitting(true);
    try {
      const createParams: RsiTaskCreateParams =
        branch === 'HARNESS'
          ? {
              scenario: 'HARNESS',
              input_file: form.datasetFile,
              name: form.name.trim(),
              model_refs: {
                optimizer: form.optimizer,
                tester: form.tester,
              },
              max_iterations: form.maxIterations,
              search_width: form.searchWidth,
            }
          : {
              scenario: 'ARTIFACT',
              artifact_type: form.artifactType,
              name: form.name.trim(),
              model_refs: {
                optimizer: form.optimizer,
              },
              ...(branch !== 'PROGRAM' ? { max_iterations: form.maxIterations } : {}),
              ...(form.optimizationInstruction ? { optimization_instruction: form.optimizationInstruction } : {}),
              ...(form.artifactPath ? { artifact_path: form.artifactPath } : {}),
            };
      const res = await rsiTaskCreate(createParams);
      let nextStatus: RsiTaskStatus = res.status;
      try {
        const started = await rsiTrainingStart(res.task_id);
        nextStatus = started.status;
      } catch {
        /* 启动失败保留 CREATED，不阻断创建反馈 */
      }

      let item: RsiTaskListItem | undefined;
      try {
        item = (await rsiTaskList()).find((candidate) => candidate.task_id === res.task_id);
      } catch {
        // The create/start response is still useful when the follow-up list
        // snapshot is temporarily unavailable.
      }
      if (!item) {
        item = {
          task_id: res.task_id,
          name: form.name.trim(),
          scenario: form.scenario,
          artifact_type: form.scenario === 'ARTIFACT' ? form.artifactType : null,
          status: nextStatus,
          iter: { current: 0, total: form.maxIterations },
          score: null,
          best: null,
          base: null,
          gain: null,
          running: nextStatus === 'RUNNING',
          created_at: new Date().toISOString(),
        };
      }
      onCreated(item);
      onClose();
      setForm(defaultForm());
      setDatasetValid(null);
    } catch (e) {
      console.error('[rsi] create failed', e);
      setErrors((prev) => ({ ...prev, submit: e instanceof Error ? e.message : String(e) }));
    } finally {
      setSubmitting(false);
    }
  }, [form, branch, validate, onCreated, onClose]);

  // 程序优化无"最大迭代轮次"字段（样式概要明确）
  const showMaxIterations = branch !== 'PROGRAM';

  return (
    <dialog
      ref={dialogRef}
      className="rsi-config-dialog rsi-create-drawer"
      aria-labelledby="rsi-create-title"
      data-testid="rsi-create-dialog"
      onClick={(event) => {
        if (event.target === dialogRef.current) onClose();
      }}
    >
      <div className="rsi-create-dialog__inner">
        <div className="rsi-create-dialog__header">
          <h2 id="rsi-create-title" style={{ fontSize: 16, fontWeight: 600 }}>
            {t('rsi.createDialog.title')}
          </h2>
          <button
            type="button"
            className="rsi-create-dialog__close"
            onClick={onClose}
            aria-label="close"
          >
            ×
          </button>
        </div>

        {form.artifactType === 'PROGRAM' && <div className="rsi-create-dialog__info-bar">
          <TipIcon className="w-3.5 h-3.5 shrink-0" />
          <span>{t('rsi.createDialog.infoBar')}</span>
        </div>}

        {/* 基础字段 */}
        <Field label={t('rsi.createDialog.nameLabel')}>
          <input
            className="rsi-input"
            value={form.name}
            onChange={(e) => update('name', e.target.value)}
            placeholder={t('rsi.createDialog.namePlaceholder')}
          />
          {errors.name && <Err text={errors.name} />}
        </Field>

        <Field label={t('rsi.createDialog.typeLabel')}>
          <div className="rsi-create-dialog__type-row">
            <BranchButton
              active={form.scenario === 'HARNESS'}
              onClick={() => switchScenario('HARNESS')}
              label={t('rsi.createDialog.typeHarness')}
            />
            <BranchButton
              active={isArtifact}
              onClick={() => switchScenario('ARTIFACT')}
              label={t('rsi.createDialog.typeArtifact')}
            />
          </div>
        </Field>

        {isArtifact && (
          <Field label={t('rsi.createDialog.artifactSubtypeLabel')}>
            <div className="rsi-create-dialog__type-row">
              <BranchButton
                active={form.artifactType === 'PAPER'}
                onClick={() => switchArtifactType('PAPER')}
                label={t('rsi.createDialog.subtypePaper')}
              />
              <BranchButton
                active={form.artifactType === 'PROGRAM'}
                onClick={() => switchArtifactType('PROGRAM')}
                label={t('rsi.createDialog.subtypeProgram')}
              />
            </div>
          </Field>
        )}

        <Field label={t('rsi.createDialog.optimizerModelLabel')}>
          <ModelSelect value={form.optimizer} onChange={(v) => update('optimizer', v)} />
          {errors.optimizer && <Err text={errors.optimizer} />}
        </Field>

        {branch === 'HARNESS' && (
          <Field label={t('rsi.createDialog.testerModelLabel')}>
            <ModelSelect value={form.tester} onChange={(v) => update('tester', v)} />
            {errors.tester && <Err text={errors.tester} />}
          </Field>
        )}

        {branch === 'HARNESS' && (
          <Field
            label={t('rsi.createDialog.datasetLabel')}
            tip={t('rsi.createDialog.datasetTip')}
          >
            <PathInput
              value={form.datasetFile}
              placeholder={t('rsi.createDialog.datasetPlaceholder')}
              onPick={() => pickPath('dataset')}
              icon="file"
            />
            {datasetValid && (
              <div className="rsi-create-dialog__dataset-result">
                {datasetValid.valid
                  ? datasetValid.count != null
                    ? t('rsi.createDialog.valid', { count: datasetValid.count })
                    : t('rsi.createDialog.sampleUnknown')
                  : t('rsi.createDialog.invalid')}
              </div>
            )}
            {errors.dataset && <Err text={errors.dataset} />}
          </Field>
        )}

        {branch === 'PAPER' && (
          <>
            <Field label={t('rsi.createDialog.optimizationInstructionLabel')}>
              <textarea
                className="rsi-input"
                rows={4}
                maxLength={1000}
                value={form.optimizationInstruction}
                onChange={(e) => update('optimizationInstruction', e.target.value)}
                placeholder={t('rsi.createDialog.optimizationInstructionPlaceholder')}
              />
            </Field>
            <Field label={t('rsi.createDialog.paperLabel')}>
              <PathInput
                value={form.artifactPath}
                placeholder={t('rsi.createDialog.paperPlaceholder')}
                onPick={() => pickPath('artifact')}
              />
              {errors.paper && <Err text={errors.paper} />}
            </Field>
          </>
        )}

        {branch === 'PROGRAM' && (
          <>
            <Field label={t('rsi.createDialog.programLabel')}>
              <PathInput
                value={form.artifactPath}
                placeholder={t('rsi.createDialog.programPlaceholder')}
                onPick={() => pickPath('artifact')}
              />
              {errors.program && <Err text={errors.program} />}
            </Field>
          </>
        )}

        {showMaxIterations && (
          <Field label={t('rsi.createDialog.maxIterationsLabel')}>
            <SegmentedSlider value={form.maxIterations} min={1} max={5} onChange={(v) => update('maxIterations', v)} />
          </Field>
        )}

        {branch === 'HARNESS' && (
          <Field label={t('rsi.createDialog.searchWidthLabel')}>
            <SegmentedSlider value={form.searchWidth} min={1} max={5} onChange={(v) => update('searchWidth', v)} />
          </Field>
        )}

        {errors.submit && <Err text={errors.submit} />}

        <div className="rsi-create-dialog__actions">
          <button type="button" className="rsi-btn rsi-btn--ghost" onClick={onClose} disabled={submitting}>
            {t('rsi.createDialog.cancel')}
          </button>
          <button
            type="button"
            className="rsi-btn rsi-btn--primary rsi-create-dialog__submit"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {t('rsi.createDialog.submit')}
          </button>
        </div>
      </div>
    </dialog>
  );
}

function Field({
  label,
  tip,
  children,
}: {
  label: string;
  tip?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label style={{ display: 'block', fontSize: 13, marginBottom: 6, color: 'var(--color-text-primary)' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {label}
          {tip && (
            <span className="rsi-create-dialog__label-tip" title={tip} aria-label={tip}>
              <TipIcon className="w-3.5 h-3.5 shrink-0" />
            </span>
          )}
        </span>
      </label>
      {children}
    </div>
  );
}

function Err({ text }: { text: string }) {
  return <div style={{ fontSize: 12, marginTop: 4, color: 'var(--color-feedback-danger)' }}>{text}</div>;
}

function BranchButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      className={`rsi-create-dialog__branch-btn${active ? ' rsi-create-dialog__branch-btn--active' : ''}`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function PathInput({
  value,
  placeholder,
  onPick,
  icon = 'folder',
}: {
  value: string;
  placeholder: string;
  onPick: () => void;
  icon?: 'file' | 'folder';
}) {
  return (
    <div className="rsi-create-dialog__path-input">
      <input className="rsi-input" value={value} readOnly placeholder={placeholder} />
      <button type="button" className="rsi-create-dialog__path-btn" onClick={onPick} aria-label="browse">
        {icon === 'file' ? (
          <svg
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.5 3H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.5L15.5 3z"
            />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 3v5h5" />
          </svg>
        ) : (
          <svg
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"
            />
          </svg>
        )}
      </button>
    </div>
  );
}

function ModelSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  const availableModels = useSessionStore((s) => s.availableModels);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [open]);

  const selected = availableModels.find((m) => m.model_name === value) ?? null;

  const handleSelect = (modelName: string) => {
    setOpen(false);
    onChange(modelName);
  };

  const isFree = (m: ModelEntry) => m.is_free === true;
  const freeModels = availableModels.filter(isFree);
  const configuredModels = availableModels.filter((m) => !isFree(m));

  const renderGroup = (label: string, models: ModelEntry[]) =>
    models.length === 0 ? null : (
      <>
        <div className="model-select__section-header">{label}</div>
        {models.map((m, idx) => {
          const key = m.alias || m.model_name;
          const active = m.model_name === value;
          return (
            <button
              key={`${m.model_name}-${idx}`}
              type="button"
              onClick={() => handleSelect(m.model_name)}
              className={clsx('chat-mode-select__option', active && 'chat-mode-select__option--active')}
              role="menuitemradio"
              aria-checked={active}
            >
              <span className="chat-mode-select__option-main">
                <span className="chat-mode-select__icon" aria-hidden="true">
                  <ModelProviderIcon model={m} />
                </span>
                <span className="chat-mode-select__label">{key}</span>
              </span>
              {active && (
                <svg
                  className="chat-mode-select__check"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                </svg>
              )}
            </button>
          );
        })}
      </>
    );

  return (
    <div ref={rootRef} className="rsi-model-select">
      <button
        type="button"
        className="rsi-model-select__trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selected ? (
          <span className="rsi-model-select__value">
            <ModelProviderIcon model={selected} />
            <span className="rsi-model-select__label">{selected.alias || selected.model_name}</span>
          </span>
        ) : (
          <span className="rsi-model-select__label rsi-model-select__placeholder">
            {t('rsi.createDialog.modelPlaceholder')}
          </span>
        )}
        <svg
          className="rsi-model-select__chevron"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
        </svg>
      </button>

      {open && (
        <div className="chat-mode-select__menu model-select__menu rsi-model-select__menu" role="listbox">
          {availableModels.length === 0 ? (
            <div className="model-select__section-header">{t('rsi.createDialog.modelPlaceholder')}</div>
          ) : (
            <>
              {renderGroup(t('chat.modelSelector.configured'), configuredModels)}
              {renderGroup(t('chat.modelSelector.free'), freeModels)}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SegmentedSlider({
  value,
  min,
  max,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  const segments: number[] = [];
  for (let i = min; i <= max; i++) segments.push(i);
  const pct = ((value - min) / (max - min)) * 100;

  return (
    <div className="rsi-segmented-slider">
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rsi-segmented-slider__input"
        style={{ '--rsi-slider-pct': pct + '%' } as React.CSSProperties}
        aria-label="slider"
      />
      <div className="rsi-segmented-slider__labels">
        {segments.map((seg) => (
          <span
            key={seg}
            className={'rsi-segmented-slider__label' + (seg <= value ? ' rsi-segmented-slider__label--active' : '')}
          >
            {seg}
          </span>
        ))}
      </div>
    </div>
  );
}
