import i18n from './i18n';
import ReactDOM from 'react-dom/client';
import App from './App.tsx'
import { CronView } from './views/CronView'
import { SkillsView } from './views/SkillsView'
import { MemoryView } from './views/MemoryView'
import './index.css'

const params = new URLSearchParams(window.location.search);

// 内嵌到 claw_manager 用户面时：?lang= 设初始语言；并监听父窗口 postMessage 实时切换语言(跨源)。
const initLang = params.get('lang');
if (initLang === 'zh' || initLang === 'en') void i18n.changeLanguage(initLang);
window.addEventListener('message', (e: MessageEvent) => {
  const d = e.data as { type?: string; lang?: string } | null;
  if (d && d.type === 'jw-set-lang' && (d.lang === 'zh' || d.lang === 'en')) {
    void i18n.changeLanguage(d.lang);
  }
});

// ?view= 决定渲染哪个视图;默认(无 view)=聊天 App,行为不变。
const view = params.get('view');
const root = ReactDOM.createRoot(document.getElementById('root')!);
const wrap = (node: JSX.Element) => <div style={{ height: '100vh', overflow: 'hidden' }}>{node}</div>;
if (view === 'schedule') {
  root.render(wrap(<CronView />));
} else if (view === 'skills') {
  root.render(wrap(<SkillsView />));
} else if (view === 'memory') {
  root.render(wrap(<MemoryView />));
} else {
  root.render(<App />);
}
