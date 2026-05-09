import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { EmptyState } from './EmptyState'
import { Button } from './Button'

const meta = {
  title: 'Primitives/EmptyState',
  component: EmptyState,
  tags: ['autodocs'],
  argTypes: {
    icon: { control: 'select', options: ['document', 'search', 'brain', 'alert-triangle', 'tag'] },
    tone: { control: 'select', options: ['default', 'warning'] },
  },
} satisfies Meta<typeof EmptyState>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    icon: 'search',
    title: 'No jobs match your filters',
    description: 'Loosen the score floor or clear the status filter to see more roles.',
  },
}

export const WithAction: Story = {
  args: {
    icon: 'document',
    title: 'No personas seeded yet',
    description: 'Run the seed script against Supabase to bootstrap from existing company_knowledge.',
    action: <Button size="sm">Read seed docs</Button>,
    hint: <code className="font-mono">db/seed_company_personas.sql</code>,
  },
}

export const Warning: Story = {
  args: {
    tone: 'warning',
    icon: 'alert-triangle',
    title: 'Outcome not tracked',
    description: 'Logging outcomes is what makes the persona synthesizer learn.',
    action: <Button size="sm" variant="warning">Start tracking</Button>,
  },
}
