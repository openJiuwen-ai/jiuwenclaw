import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ImagePlus, Trash2, Plus } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import { useConnectorStore } from '../../stores/connectorStore';
import { usePluginPackageStore } from '../../stores/pluginPackageStore';
import { getSkillAvatar } from '../../utils/skillAvatar';
import { EntityAvatar } from './EntityAvatar';
import { PickerModal, type PickerItem } from './PickerModal';

const DESCRIPTION_MAX = 226;

interface SkillItem {
  name: string;
  display_name?: string;
  description: string;
  // "我的技能"/"技能广场" 拆分用，跟 SkillPanel/index.tsx 的字段语义一致：source==='local' 是
  // 用户自己的技能，is_builtin(_source) 是内置/广场技能。
  source?: string;
  is_builtin?: boolean;
  is_builtin_source?: boolean;
}

interface CreatePluginPageProps {
  onBack: () => void;
  onCreated: () => void;
}

// 对应高保真 3.1 手动创建插件。"选择技能"复用现有 skills.list 接口（不需要问后端新增，
// 见 backend-requests.md 附注），"选择MCP"用 connectorStore 已加载的市场列表。
// 提交调 pluginPackageStore.create——2026-08-07 对齐专家与插件装备-前端接口(3).md §3.3 真实参数
// 形状 {id, name, description, skills}：id 是必填目录名，手动输入或按名称自动建议一个 slug，
// 用户可编辑；name/description 是纯字符串，不再是双语对象。
// mcpIds 选择仍保留在 UI 里（用户可以继续挑 MCP 卡片），但真实 create 接口完全没有承载这份
// 选择的参数位——提交时不会带上，纯本地展示，等后端定下插件包和 MCP 绑定关系怎么建（backend-
// requests.md 需求 2 遗留问题）才能接上。
// 头像选择：点击可以真的打开文件选择器并本地预览（上一轮这里只是个纯静态图标，点了没反应），
// 但 plugin_packages.* 完全没有图标字段（backend-requests.md 需求9），选中的图片选不进
// create() 的参数里，只能停留在本地预览——选好图片后额外提示一句，不让用户误以为真的保存了。
// 选择弹窗内的图标沿用 getSkillAvatar（跟技能面板同一套实心圆+白字母头像，2026-08-19 整合，
// 见 utils/skillAvatar.ts 头部注释），但用户要求整体放大——从原来的 h-6 w-6 提到 h-8 w-8。
function toSkillPickerItems(items: SkillItem[]): PickerItem[] {
  return items.map((skill): PickerItem => {
    const label = skill.display_name || skill.name;
    const avatar = getSkillAvatar(label);
    return {
      id: skill.name,
      name: label,
      description: skill.description,
      render: () => (
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[14px] font-semibold text-text-inverse ${avatar.color}`}>
          {avatar.firstChar}
        </span>
      ),
    };
  });
}

function toMcpPickerItem(mcp: { name: string; displayName: string; description: string; icon?: string | null }): PickerItem {
  const avatar = getSkillAvatar(mcp.displayName);
  return {
    id: mcp.name,
    name: mcp.displayName,
    // mcp.list 恒下发 description（需求14已解决，见 types/connector.ts），之前这里漏接、硬编码成
    // 空字符串，导致 MCP 选择卡片一直没有描述文字。
    description: mcp.description,
    // 2026-08-19：MCP 有真实图标时（connector.icon）要优先展示，之前这里没接 iconUrl，恒渲染成
    // 生成的字母头像——跟广场卡片/详情页（都走 EntityAvatar + iconUrl）不一致。改用 EntityAvatar
    // 复用同一套"有真图标优先、没有/加载失败才回退字母色块"的逻辑。
    render: () => (
      <EntityAvatar
        iconUrl={mcp.icon ?? undefined}
        avatar={avatar}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[13px] font-semibold"
      />
    ),
  };
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function CreatePluginPage({ onBack, onCreated }: CreatePluginPageProps) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [id, setId] = useState('');
  const [idTouched, setIdTouched] = useState(false);
  const [description, setDescription] = useState('');
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [mcpIds, setMcpIds] = useState<string[]>([]);
  const [picker, setPicker] = useState<'skill' | 'mcp' | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const connectors = useConnectorStore((s) => s.connectors);
  const myConnectors = useConnectorStore((s) => s.myConnectors);
  const builtinConnectors = useConnectorStore((s) => s.builtinConnectors);
  const loadConnectorList = useConnectorStore((s) => s.loadList);
  const createPlugin = usePluginPackageStore((s) => s.create);

  useEffect(() => {
    webRequest<{ skills?: SkillItem[] }>('skills.list', { with_installed: true })
      .then((payload) => setSkills(payload.skills ?? []))
      .catch(() => setSkills([]));
    // "选择MCP"picker 要能选到广场+我的的全部条目，builtin/local 单独一个 fetch 都不够全
    // （builtin 缺已连接的自定义 MCP，local 缺未连接的预置 MCP），两个都拉、合并视图 connectors
    // 会按 name 并起来（见 connectorStore.ts 头注释）。
    if (connectors.length === 0) {
      loadConnectorList('builtin');
      loadConnectorList('local');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  function handleAvatarSelect(file: File | undefined) {
    if (!file) return;
    setAvatarPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
  }

  const selectedSkills = skills.filter((s) => skillIds.includes(s.name));
  const selectedMcps = connectors.filter((c) => mcpIds.includes(c.name));

  // "我的技能" = 用户自己的（source==='local'）；"技能广场" = 内置/广场技能，两者不是互斥兜底
  // 关系而是各自独立的真实过滤条件，跟 SkillPanel 的 builtinSkills/我的 tab 语义对齐。
  const myPickerSkills = useMemo(() => skills.filter((s) => s.source === 'local'), [skills]);
  const plazaPickerSkills = useMemo(
    () => skills.filter((s) => s.is_builtin === true || s.is_builtin_source === true),
    [skills],
  );

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitError(null);
    const ok = await createPlugin({
      id,
      name,
      description,
      skills: skillIds,
    });
    setSubmitting(false);
    if (ok) {
      onCreated();
    } else {
      setSubmitError(t('connectorMarket.create.submitError'));
    }
  }

  return (
    <div className="relative h-full overflow-y-auto bg-card px-8 py-6">
      {/* 返回样式跟详情页（McpDetailPage.tsx/PluginDetailPage.tsx）保持一致：ChevronLeft
          纯尖角图标 + 黑色文字，用户明确要求这个页面也照这个样式改。 */}
      <button type="button" onClick={onBack} className="mb-4 flex items-center gap-1 text-[14px] leading-[22px] text-text hover:opacity-70">
        <ChevronLeft size={16} />
        {t('connectorMarket.common.back')}
      </button>

      <h1 className="mb-6 text-[18px] font-semibold leading-7 text-text">{t('connectorMarket.create.manual')}</h1>

      <Section title={t('connectorMarket.create.basicInfo')}>
        <div className="mb-4 flex items-center gap-3">
          <label className="flex h-14 w-14 shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-2xl bg-bg-muted text-text-muted hover:bg-bg">
            {avatarPreviewUrl ? (
              <img src={avatarPreviewUrl} alt="" className="h-full w-full object-cover" />
            ) : (
              <ImagePlus size={22} />
            )}
            <input
              type="file"
              accept="image/png,image/jpeg,image/gif"
              className="hidden"
              onChange={(event) => handleAvatarSelect(event.target.files?.[0])}
            />
          </label>
          <div>
            <p className="text-[12px] leading-[18px] text-text-muted">{t('connectorMarket.create.uploadHint')}</p>
            {avatarPreviewUrl && (
              <p className="mt-0.5 text-[11px] leading-4 text-[color:var(--color-text-placeholder)]">
                {t('connectorMarket.create.avatarNotPersisted')}
              </p>
            )}
          </div>
        </div>

        <label className="mb-1.5 block text-[13px] font-medium text-text">{t('connectorMarket.create.name')}</label>
        <input
          value={name}
          onChange={(event) => {
            const nextName = event.target.value;
            setName(nextName);
            if (!idTouched) setId(slugify(nextName));
          }}
          className="mb-4 h-9 w-full rounded-lg border border-border bg-card px-3 text-[13px] text-text outline-none focus:border-border-hover"
        />

        <label className="mb-1.5 block text-[13px] font-medium text-text">{t('connectorMarket.create.id')}</label>
        <input
          value={id}
          onChange={(event) => {
            setIdTouched(true);
            setId(event.target.value);
          }}
          placeholder={t('connectorMarket.create.idPlaceholder')}
          className="mb-1.5 h-9 w-full rounded-lg border border-border bg-card px-3 text-[13px] text-text outline-none focus:border-border-hover"
        />
        <p className="mb-4 text-[11px] leading-4 text-[color:var(--color-text-placeholder)]">{t('connectorMarket.create.idHint')}</p>

        <label className="mb-1.5 block text-[13px] font-medium text-text">{t('connectorMarket.create.description')}</label>
        <div className="relative">
          <textarea
            value={description}
            maxLength={DESCRIPTION_MAX}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            className="w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-[13px] leading-5 text-text outline-none focus:border-border-hover"
          />
          <span className="absolute bottom-2 right-3 text-[11px] text-text-muted">
            {description.length}/{DESCRIPTION_MAX}
          </span>
        </div>
      </Section>

      <Section
        title={t('connectorMarket.create.skillsOptional')}
        action={
          <button type="button" onClick={() => setPicker('skill')} className="flex items-center gap-1 rounded-full px-2.5 py-1 text-[13px] text-text hover:bg-connector-add-hover-surface hover:text-[color:var(--color-chat-accent)]">
            <Plus size={14} />
            {t('connectorMarket.create.addSkill')}
          </button>
        }
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {selectedSkills.map((skill) => {
            const label = skill.display_name || skill.name;
            const avatar = getSkillAvatar(label);
            return (
              <div key={skill.name} className="relative rounded-xl border border-border bg-card p-4">
                <button type="button" onClick={() => setSkillIds((prev) => prev.filter((id) => id !== skill.name))} className="absolute right-4 top-4 text-text-muted hover:text-danger">
                  <Trash2 size={15} />
                </button>
                <div className="mb-1.5 flex items-center gap-2.5 pr-6">
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] text-[16px] font-black text-text-inverse ${avatar.color}`}>
                    {avatar.firstChar}
                  </span>
                  <span className="text-[14px] font-semibold leading-[22px] text-text">{label}</span>
                </div>
                <p className="line-clamp-2 text-[13px] leading-5 text-[color:var(--color-text-placeholder)]">{skill.description}</p>
              </div>
            );
          })}
        </div>
      </Section>

      <Section
        title={t('connectorMarket.create.mcpOptional')}
        action={
          <button type="button" onClick={() => setPicker('mcp')} className="flex items-center gap-1 rounded-full px-2.5 py-1 text-[13px] text-text hover:bg-connector-add-hover-surface hover:text-[color:var(--color-chat-accent)]">
            <Plus size={14} />
            {t('connectorMarket.create.addMcp')}
          </button>
        }
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {selectedMcps.map((mcp) => {
            const avatar = getSkillAvatar(mcp.displayName);
            return (
              <div key={mcp.name} className="relative rounded-xl border border-border bg-card p-4">
                <button type="button" onClick={() => setMcpIds((prev) => prev.filter((id) => id !== mcp.name))} className="absolute right-4 top-4 text-text-muted hover:text-danger">
                  <Trash2 size={15} />
                </button>
                <div className="mb-1.5 flex items-center gap-2.5 pr-6">
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[13px] font-semibold text-text-inverse ${avatar.color}`}>
                    {avatar.firstChar}
                  </span>
                  <span className="text-[14px] font-semibold leading-[22px] text-text">{mcp.displayName}</span>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {submitError && <p className="mb-3 text-[12px] text-danger">{submitError}</p>}

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        <button type="button" onClick={onBack} className="rounded-lg border border-border px-4 py-1.5 text-[13px] text-text hover:border-border-hover">
          {t('connectorMarket.common.cancel')}
        </button>
        <button type="button" onClick={handleSubmit} disabled={submitting || !id.trim() || !name.trim()} className="rounded-lg bg-text px-4 py-1.5 text-[13px] text-text-inverse disabled:opacity-60">
          {t('connectorMarket.common.confirm')}
        </button>
      </div>

      {picker === 'skill' && (
        <PickerModal
          title={t('connectorMarket.create.pickSkillTitle')}
          myLabel={t('connectorMarket.create.mySkills')}
          plazaLabel={t('connectorMarket.create.skillPlaza')}
          initialSelectedIds={skillIds}
          myItems={toSkillPickerItems(myPickerSkills)}
          plazaItems={toSkillPickerItems(plazaPickerSkills)}
          onCancel={() => setPicker(null)}
          onConfirm={(ids) => {
            setSkillIds(ids);
            setPicker(null);
          }}
        />
      )}

      {picker === 'mcp' && (
        <PickerModal
          title={t('connectorMarket.create.pickMcpTitle')}
          myLabel={t('connectorMarket.create.myMcps')}
          plazaLabel={t('connectorMarket.create.mcpPlaza')}
          initialSelectedIds={mcpIds}
          myItems={myConnectors.map(toMcpPickerItem)}
          plazaItems={builtinConnectors.map(toMcpPickerItem)}
          onCancel={() => setPicker(null)}
          onConfirm={(ids) => {
            setMcpIds(ids);
            setPicker(null);
          }}
        />
      )}
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-semibold leading-[22px] text-text">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}
