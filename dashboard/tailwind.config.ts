import type { Config } from 'tailwindcss'

/**
 * Tailwind config — semantic token bridge.
 *
 * Colors are defined as CSS variables in globals.css, then mapped here
 * as Tailwind utilities (e.g. `bg-surface`, `text-fg-muted`, `border-border`).
 * That gives us:
 *   - one source of truth for theming
 *   - automatic light/dark mode (no `dark:` prefixes needed)
 *   - real semantic naming (no `bg-gray-900` literals)
 */
const config: Config = {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // Body font — system stack (deliberate; not Inter).
        sans: [
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'Roboto',
          '"Helvetica Neue"',
          'Arial',
          'sans-serif',
        ],
        // Display font — high-quality system serif for headings.
        display: [
          'ui-serif',
          'Charter',
          '"Iowan Old Style"',
          '"Apple Garamond"',
          'Baskerville',
          'Georgia',
          'serif',
        ],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          '"JetBrains Mono"',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      colors: {
        bg: 'rgb(var(--bg) / <alpha-value>)',
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          raised: 'rgb(var(--surface-raised) / <alpha-value>)',
          overlay: 'rgb(var(--surface-overlay) / <alpha-value>)',
        },
        fg: {
          DEFAULT: 'rgb(var(--fg) / <alpha-value>)',
          muted: 'rgb(var(--fg-muted) / <alpha-value>)',
          subtle: 'rgb(var(--fg-subtle) / <alpha-value>)',
          inverse: 'rgb(var(--fg-inverse) / <alpha-value>)',
        },
        border: {
          DEFAULT: 'rgb(var(--border) / <alpha-value>)',
          strong: 'rgb(var(--border-strong) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          fg: 'rgb(var(--accent-fg) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'rgb(var(--success) / <alpha-value>)',
          bg: 'rgb(var(--success-bg) / <alpha-value>)',
          border: 'rgb(var(--success-border) / <alpha-value>)',
        },
        warning: {
          DEFAULT: 'rgb(var(--warning) / <alpha-value>)',
          bg: 'rgb(var(--warning-bg) / <alpha-value>)',
          border: 'rgb(var(--warning-border) / <alpha-value>)',
        },
        danger: {
          DEFAULT: 'rgb(var(--danger) / <alpha-value>)',
          bg: 'rgb(var(--danger-bg) / <alpha-value>)',
          border: 'rgb(var(--danger-border) / <alpha-value>)',
        },
        info: {
          DEFAULT: 'rgb(var(--info) / <alpha-value>)',
          bg: 'rgb(var(--info-bg) / <alpha-value>)',
          border: 'rgb(var(--info-border) / <alpha-value>)',
        },
      },
      fontSize: {
        '2xs': ['0.75rem',  { lineHeight: '1.4' }],   // 12px — floor
        xs:    ['0.8125rem',{ lineHeight: '1.4' }],   // 13px
        sm:    ['0.9375rem',{ lineHeight: '1.5' }],   // 15px
        base:  ['1rem',     { lineHeight: '1.55' }],  // 16px
        lg:    ['1.125rem', { lineHeight: '1.5' }],   // 18px
        xl:    ['1.375rem', { lineHeight: '1.35' }],  // 22px
        '2xl': ['1.75rem',  { lineHeight: '1.25' }],  // 28px
        '3xl': ['2.25rem',  { lineHeight: '1.15' }],  // 36px
      },
      spacing: {
        '4.5': '1.125rem',
        '11': '2.75rem',
      },
      minHeight: {
        '11': '2.75rem',  // 44px — WCAG AAA touch target
        '9':  '2.25rem',  // 36px — desktop comfort
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
      transitionTimingFunction: {
        'out-expo': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
}
export default config
