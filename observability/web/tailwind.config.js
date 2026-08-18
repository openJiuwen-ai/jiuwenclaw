import typography from '@tailwindcss/typography';

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: 'var(--bg)',
          accent: 'var(--bg-accent)',
          elevated: 'var(--bg-elevated)',
          hover: 'var(--bg-hover)',
          muted: 'var(--bg-muted)',
          content: 'var(--bg-content)',
        },
        card: {
          DEFAULT: 'var(--card)',
          foreground: 'var(--card-foreground)',
        },
        panel: {
          DEFAULT: 'var(--panel)',
          strong: 'var(--panel-strong)',
          hover: 'var(--panel-hover)',
        },
        text: {
          DEFAULT: 'var(--text)',
          strong: 'var(--text-strong)',
          muted: 'var(--muted)',
        },
        border: {
          DEFAULT: 'var(--border)',
          strong: 'var(--border-strong)',
          hover: 'var(--border-hover)',
          accent: 'var(--border-accent)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          subtle: 'var(--accent-subtle)',
          foreground: 'var(--accent-foreground)',
        },
        secondary: {
          DEFAULT: 'var(--secondary)',
          foreground: 'var(--secondary-foreground)',
        },
        ok: {
          DEFAULT: 'var(--ok)',
          subtle: 'var(--ok-subtle)',
        },
        warn: {
          DEFAULT: 'var(--warn)',
          subtle: 'var(--warn-subtle)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          subtle: 'var(--danger-subtle)',
        },
        info: 'var(--info)',
        muted: {
          DEFAULT: 'var(--muted)',
          foreground: 'var(--muted-foreground)',
          strong: 'var(--muted-strong)',
        },
      },
      fontFamily: {
        body: ['var(--font-body)'],
        display: ['var(--font-display)'],
        mono: ['var(--mono)'],
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
        full: 'var(--radius-full)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
        glow: 'var(--shadow-glow)',
        focus: 'var(--focus-ring)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        'in-out': 'var(--ease-in-out)',
        spring: 'var(--ease-spring)',
      },
      transitionDuration: {
        fast: 'var(--duration-fast)',
        normal: 'var(--duration-normal)',
        slow: 'var(--duration-slow)',
      },
      animation: {
        'pulse-slow': 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        rise: 'rise 0.35s var(--ease-out) backwards',
        'fade-in': 'fade-in 0.2s ease-out forwards',
      },
      keyframes: {
        rise: {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
      },
      spacing: {
        'shell-pad': 'var(--shell-pad)',
        'shell-gap': 'var(--shell-gap)',
        'shell-nav': 'var(--shell-nav-width)',
        'shell-topbar': 'var(--shell-topbar-height)',
      },
    },
  },
  plugins: [typography],
}
