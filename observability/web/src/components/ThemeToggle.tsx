import { useState } from 'react';

export function ThemeToggle() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light');

  const toggle = (next: 'dark' | 'light') => {
    setTheme(next);
    localStorage.setItem('theme', next);
    if (next === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  };

  const themeIndex = theme === 'dark' ? 0 : 1;

  return (
    <div className="theme-toggle">
      <div className="theme-toggle__track" style={{ '--theme-index': themeIndex } as React.CSSProperties}>
        <div className="theme-toggle__indicator" />
        <button
          className={`theme-toggle__button ${theme === 'dark' ? 'active' : ''}`}
          onClick={() => toggle('dark')}
          title="Dark"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        </button>
        <button
          className={`theme-toggle__button ${theme === 'light' ? 'active' : ''}`}
          onClick={() => toggle('light')}
          title="Light"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="12" cy="12" r="5" />
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
        </button>
      </div>
    </div>
  );
}
