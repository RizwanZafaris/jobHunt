import type { Preview } from '@storybook/nextjs-vite'
import React, { useEffect } from 'react'
import '../src/app/globals.css'

/**
 * Sync the iframe <html data-theme=…> with Storybook's globalType so
 * the design tokens shift live when you flip the theme picker.
 */
function ThemeProvider({ children, theme }: { children: React.ReactNode; theme: string }) {
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])
  return <>{children}</>
}

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
    backgrounds: { disable: true },
    a11y: {
      // Run axe on every story render
      test: 'todo',
    },
    layout: 'centered',
  },

  globalTypes: {
    theme: {
      description: 'Design system theme',
      defaultValue: 'dark',
      toolbar: {
        title: 'Theme',
        icon: 'paintbrush',
        items: [
          { value: 'light', title: 'Light', icon: 'sun' },
          { value: 'dark',  title: 'Dark',  icon: 'moon' },
        ],
        dynamicTitle: true,
      },
    },
  },

  decorators: [
    (Story, ctx) => (
      <ThemeProvider theme={ctx.globals.theme}>
        <div className="bg-bg text-fg p-6 min-w-[320px]">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
}

export default preview
