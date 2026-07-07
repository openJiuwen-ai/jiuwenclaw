/** bot 在某实例上的模板绑定编辑器（7 槽位 → 选模板）。受控组件。
 *  单值槽位(默认/视频/音频/视觉模型、服务配置)= 下拉；多值槽位(skill_whitelist/extension_config)= 多选。
 *  底层落 config_default_template_mapping(scope_type=bot),每个 (槽位,模板) 一行。 */
import { useTranslation } from 'react-i18next';
import { TemplateOption } from '../../components/TemplateRefEditor';
import { TEMPLATE_REF_SLOTS, isSingleValueTemplateRefSlot } from '../../utils/templateRef';

export type TemplateRefValue = Record<string, string[]>;

export function BotTemplateBinding({ options, value, onChange }: {
  options: Record<string, TemplateOption[]>;
  value: TemplateRefValue;
  onChange: (v: TemplateRefValue) => void;
}) {
  const { t } = useTranslation();

  function setSlot(slot: string, ids: string[]) {
    onChange({ ...value, [slot]: ids });
  }
  function toggleMulti(slot: string, id: string) {
    const cur = value[slot] ?? [];
    setSlot(slot, cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  }

  return (
    <div>
      {TEMPLATE_REF_SLOTS.map((slot) => {
        const opts = options[slot] ?? [];
        const single = isSingleValueTemplateRefSlot(slot);
        const cur = value[slot] ?? [];
        return (
          <div key={slot} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
            <div style={{ width: 96, flexShrink: 0, paddingTop: 6 }} className="text-xs">{t(`iam.tpl_${slot}`, { defaultValue: slot })}</div>
            {single ? (
              <select
                className="input"
                style={{ flex: 1 }}
                value={cur[0] ?? ''}
                onChange={(e) => setSlot(slot, e.target.value ? [e.target.value] : [])}
              >
                <option value="">{t('iam.tplNone', { defaultValue: '无' })}</option>
                {opts.map((o) => <option key={o.template_id} value={o.template_id}>{o.label}</option>)}
              </select>
            ) : (
              <div style={{ flex: 1, maxHeight: 96, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 6 }}>
                {opts.length === 0 && <div className="text-xs text-muted">{t('iam.tplNoOptions', { defaultValue: '无可选模板' })}</div>}
                {opts.map((o) => (
                  <label key={o.template_id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1px 0' }}>
                    <input type="checkbox" checked={cur.includes(o.template_id)} onChange={() => toggleMulti(slot, o.template_id)} />
                    <span className="text-xs">{o.label}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
