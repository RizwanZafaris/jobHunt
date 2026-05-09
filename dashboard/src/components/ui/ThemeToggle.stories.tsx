import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { ThemeToggle } from './ThemeToggle'

const meta = {
  title: 'Primitives/ThemeToggle',
  component: ThemeToggle,
  tags: ['autodocs'],
  parameters: {
    docs: {
      description: {
        component:
          'Toggles `data-theme` on `<html>`. Persists to both `localStorage` and a `theme` cookie so SSR can render the right tokens on the next request.',
      },
    },
  },
} satisfies Meta<typeof ThemeToggle>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
