import { useLayoutEffect, useRef, useState } from 'react';
import './ConnectorMarket.css';

interface TruncatedTextProps {
  text: string;
  className?: string;
}

// 卡片描述用 line-clamp 截断成最多 2 行（见 MarketCard.tsx/MyMarketCard.tsx）；只有真的被截断时
// 才需要 hover 提示完整文案，没截断的短描述不用弹一个多余的提示框。
//
// 用 scrollHeight > clientHeight 判断是否截断——line-clamp（-webkit-line-clamp）虽然把超出的
// 内容裁掉不显示，但浏览器仍然按"没裁"的完整内容计算 scrollHeight，这是检测 line-clamp 截断的
// 标准做法。用 ResizeObserver 而不是只在 mount 时判断一次，是因为卡片宽度会随浏览器窗口/侧边栏
// 展开收起变化，同一段文字可能从"能显示完"变成"被截断了"，需要跟着重新判断。
export function TruncatedText({ text, className }: TruncatedTextProps) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [truncated, setTruncated] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setTruncated(el.scrollHeight > el.clientHeight + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  return (
    <p
      ref={ref}
      className={`connector-market-desc-tooltip ${className ?? ''}`}
      data-tooltip={truncated ? text : undefined}
    >
      {text}
    </p>
  );
}
