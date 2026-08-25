/**
 * 欢迎屏 design 模式专属内容。
 *
 * 在 design 模式下替代通用的 chat-suggestions，渲染：
 * 1. 子类别选择条（PPT / 网站 / 文档 / 海报，v1 仅 PPT 可选）
 * 2. 快捷能力 chip 行（6 项 PPT）
 * 3. 任务推荐卡片网格（2 项 PPT）
 *
 * 点击 chip 或卡片时调 onSendPrompt 发送构造好的 design prompt。
 */
import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { DesignCategorySelector } from './DesignCategorySelector';
import {
  DESIGN_QUICK_ACTIONS,
  DESIGN_TASK_SUGGESTIONS,
  buildDesignQuickActionPrompt,
} from './constants';
import type { DesignCategory } from './types';

interface DesignWelcomeContentProps {
  onSendPrompt: (prompt: string) => void;
}

export function DesignWelcomeContent({ onSendPrompt }: DesignWelcomeContentProps) {
  const [category, setCategory] = useState<DesignCategory>('ppt');
  const quickActions = DESIGN_QUICK_ACTIONS[category];
  const taskSuggestions = DESIGN_TASK_SUGGESTIONS[category];

  const handleQuickAction = (action: string) => {
    onSendPrompt(buildDesignQuickActionPrompt(action));
  };

  return (
    <div className="chat-design-welcome">
      <DesignCategorySelector value={category} onChange={setCategory} />

      <div className="chat-design-quick-actions">
        {quickActions.map((action) => (
          <button
            key={action}
            type="button"
            className="chat-design-quick-action"
            onClick={() => handleQuickAction(action)}
          >
            {action}
          </button>
        ))}
      </div>

      <div className="chat-design-task-cards">
        {taskSuggestions.map((suggestion) => (
          <button
            key={suggestion.title}
            type="button"
            className="chat-design-task-card"
            onClick={() => handleQuickAction(suggestion.title)}
          >
            <span className="chat-design-task-card__icon">{suggestion.icon}</span>
            <span className="chat-design-task-card__body">
              <span className="chat-design-task-card__title">{suggestion.title}</span>
              <span className="chat-design-task-card__desc">{suggestion.desc}</span>
            </span>
            <ArrowRight className="chat-design-task-card__arrow" strokeWidth={2} />
          </button>
        ))}
      </div>
    </div>
  );
}
