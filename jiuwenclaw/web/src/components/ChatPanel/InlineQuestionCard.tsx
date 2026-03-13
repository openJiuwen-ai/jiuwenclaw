/**
 * InlineQuestionCard 组件
 *
 * 在聊天流内以内联卡片形式展示用户审批请求（接收/拒绝），
 * 替代全屏大弹窗（UserQuestionModal）。
 * 点击选项后立即提交，卡片切换为已确认状态保留在消息流中。
 */

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '../../stores';
import { UserAnswer } from '../../types';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface InlineQuestionCardProps {
  onSubmit: (requestId: string, answers: UserAnswer[]) => void;
}

export function InlineQuestionCard({ onSubmit }: InlineQuestionCardProps) {
  const { t } = useTranslation();
  const { pendingQuestion, setPendingQuestion } = useChatStore();
  const [submittedAnswers, setSubmittedAnswers] = useState<Map<string, string>>(new Map());

  const handleSelect = useCallback(
    (requestId: string, questionIndex: number, optionLabel: string, isMultiSelect: boolean) => {
      if (submittedAnswers.has(`${requestId}-${questionIndex}`)) return;

      if (!isMultiSelect) {
        // 单选：立即提交
        const allAnswers: UserAnswer[] = (pendingQuestion?.questions ?? []).map((q, idx) => {
          if (idx === questionIndex) {
            return { selected_options: [optionLabel] };
          }
          return { selected_options: q.options.length > 0 ? [q.options[0].label] : [] };
        });

        setSubmittedAnswers((prev) => {
          const next = new Map(prev);
          next.set(`${requestId}-${questionIndex}`, optionLabel);
          return next;
        });

        onSubmit(requestId, allAnswers);
        setPendingQuestion(null);
      }
    },
    [pendingQuestion, submittedAnswers, onSubmit, setPendingQuestion]
  );

  if (!pendingQuestion) {
    return null;
  }

  return (
    <div className="animate-rise mx-2 my-3">
      <div
        className="w-full rounded-xl overflow-hidden"
        style={{
          border: '1px solid var(--accent)',
          backgroundColor: 'var(--card)',
        }}
      >
        {/* 标题行 */}
        <div
          className="px-4 py-2.5 flex items-center gap-2"
          style={{
            borderBottom: '1px solid var(--border)',
            backgroundColor: 'var(--panel-strong)',
          }}
        >
          <svg
            className="w-3.5 h-3.5 flex-shrink-0"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={2}
            style={{ color: 'var(--accent)' }}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5"
            />
          </svg>
          <span
            className="text-xs font-semibold"
            style={{ color: 'var(--accent)' }}
          >
            {pendingQuestion.questions[0]?.header ?? t('chatUi.inlineQuestion.header')}
          </span>
        </div>

        {/* 问题内容 */}
        {pendingQuestion.questions.map((question, qIndex) => {
          const submittedKey = `${pendingQuestion.request_id}-${qIndex}`;
          const submittedLabel = submittedAnswers.get(submittedKey);

          return (
            <div key={qIndex}>
              {/* 问题正文 */}
              <div
                className="px-4 pt-3 pb-2 text-sm prose prose-sm max-w-none prose-headings:font-semibold prose-headings:text-sm prose-ul:my-1 prose-li:my-0 prose-li:pl-1"
                style={{ color: 'var(--text)' }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {question.question}
                </ReactMarkdown>
              </div>

              {/* 选项按钮或已提交状态 */}
              {submittedLabel ? (
                <div
                  className="px-4 pb-3 flex items-center gap-2 text-xs"
                  style={{ color: 'var(--muted)' }}
                >
                  <svg
                    className="w-3.5 h-3.5 flex-shrink-0"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2.5}
                    style={{ color: 'var(--ok)' }}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  {t('chatUi.inlineQuestion.selected')}: <span style={{ color: 'var(--text-strong)' }}>{submittedLabel}</span>
                </div>
              ) : (
                <div className="px-4 pb-3 flex flex-col gap-2">
                  {question.options.map((option) => {
                    const isAccept = option.label === t('chatUi.inlineQuestion.accept');
                    const isReject = option.label === t('chatUi.inlineQuestion.reject');
                    return (
                      <button
                        key={option.label}
                        onClick={() =>
                          handleSelect(
                            pendingQuestion.request_id,
                            qIndex,
                            option.label,
                            question.multi_select || false
                          )
                        }
                        className="w-full text-left px-4 py-2.5 text-sm font-medium rounded-lg transition-all"
                        style={{
                          backgroundColor: 'var(--bg-elevated)',
                          border: `1px solid var(--border)`,
                          color: 'var(--text)',
                        }}
                        onMouseOver={(e) => {
                          const el = e.currentTarget;
                          if (isAccept) {
                            el.style.backgroundColor = 'var(--ok-subtle, rgba(34,197,94,0.12))';
                            el.style.borderColor = 'var(--ok)';
                            el.style.color = 'var(--ok)';
                          } else if (isReject) {
                            el.style.backgroundColor = 'var(--danger-subtle, rgba(239,68,68,0.12))';
                            el.style.borderColor = 'var(--danger)';
                            el.style.color = 'var(--danger)';
                          } else {
                            el.style.backgroundColor = 'var(--bg-hover)';
                            el.style.borderColor = 'var(--border-strong)';
                          }
                        }}
                        onMouseOut={(e) => {
                          const el = e.currentTarget;
                          el.style.backgroundColor = 'var(--bg-elevated)';
                          el.style.borderColor = 'var(--border)';
                          el.style.color = 'var(--text)';
                        }}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span>{option.label}</span>
                          {option.description && (
                            <span className="text-xs font-normal" style={{ color: 'var(--muted)' }}>
                              {option.description}
                            </span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
