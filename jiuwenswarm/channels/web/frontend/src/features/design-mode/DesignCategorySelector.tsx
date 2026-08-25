/**
 * 设计子类别选择条组件。
 *
 * 在 design 模式下显示在欢迎屏输入框上方，让用户选 PPT / 网站 / 文档 / 海报。
 * v1 仅 PPT 可点击，其余三项渲染为 disabled 占位（后续版本扩展）。
 */
import { DESIGN_CATEGORIES } from './constants';
import type { DesignCategory } from './types';

interface DesignCategorySelectorProps {
  value: DesignCategory;
  onChange: (category: DesignCategory) => void;
}

export function DesignCategorySelector({ value, onChange }: DesignCategorySelectorProps) {
  return (
    <div className="chat-design-category" role="tablist" aria-label="设计子类别">
      {DESIGN_CATEGORIES.map((cat) => {
        const active = cat.id === value;
        const disabled = !cat.enabled;
        return (
          <button
            key={cat.id}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            className={`chat-design-category__tab${active ? ' chat-design-category__tab--active' : ''}${disabled ? ' chat-design-category__tab--disabled' : ''}`}
            onClick={() => !disabled && onChange(cat.id)}
            title={cat.description}
          >
            {cat.label}
          </button>
        );
      })}
    </div>
  );
}
