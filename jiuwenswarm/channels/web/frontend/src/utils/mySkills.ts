/**
 * "我的技能"归属判定——技能管理页（SkillPanel/index.tsx）与手动创建插件的"添加技能"弹窗
 * （ConnectorMarket/CreatePluginPage.tsx）共用同一份规则，避免各自维护一份互相跑偏（2026-08-21
 * 用户明确指出手动创建插件里的"我的技能"比技能管理页少——根因是那边当时简化成了只看
 * source==='local' 一个条件，漏了"来自某个已安装插件的技能"和 is_builtin/is_builtin_source
 * 这两类，跟技能管理页"我的技能"tab 的真实口径不一致）。
 *
 * 判定规则照抄 SkillPanel/index.tsx 原来 filteredSkills + visibleSkills 里
 * activeTab === 'my' 分支的两步过滤，合并成一步（两步顺序可交换：第二步的排除条件不依赖搜索
 * 关键字，跟搜索过滤谁先谁后结果一样）：
 * 1. 候选集：已通过某个插件安装（installedSkillNames 命中）∪ source==='local' ∪
 *    is_builtin===true ∪ is_builtin_source===true。
 * 2. 从候选集里排除：is_builtin_source===true，但既没有通过插件安装、source 也不是 'local'
 *    的——这类是"内置技能的源码存在，但用户没真的装它"，不算"我的"。
 */
export function computeMySkills<
  T extends { name: string; source?: string; is_builtin?: boolean; is_builtin_source?: boolean },
>(skills: T[], installedSkillNames: Set<string>): T[] {
  return skills.filter((skill) => {
    const installed = installedSkillNames.has(skill.name);
    const isCandidate =
      installed || skill.source === 'local' || skill.is_builtin === true || skill.is_builtin_source === true;
    if (!isCandidate) return false;
    if (skill.is_builtin_source && !installed && skill.source !== 'local') return false;
    return true;
  });
}

/** 从"已安装插件"列表反推出"通过插件装进来的技能名"集合——同 SkillPanel/index.tsx 的
 * installedSkillNames 计算方式，供不需要 installedSkillMap 那份完整 Map（只要判断集合归属）的
 * 调用方直接用。 */
export function buildInstalledSkillNames(plugins: { skills: string[] }[]): Set<string> {
  const set = new Set<string>();
  for (const plugin of plugins) {
    for (const skill of plugin.skills) set.add(skill);
  }
  return set;
}
