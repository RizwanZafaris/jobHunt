import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { Card } from './Card'
import { Button } from './Button'
import { Stat } from './Stat'

const meta = {
  title: 'Primitives/Card',
  component: Card,
  tags: ['autodocs'],
  argTypes: {
    tone: { control: 'select', options: ['default', 'muted', 'success', 'warning', 'danger'] },
    padding: { control: 'select', options: ['none', 'sm', 'md', 'lg'] },
  },
} satisfies Meta<typeof Card>

export default meta
type Story = StoryObj<typeof meta>

export const Plain: Story = {
  args: {
    children: <p className="text-xs text-fg-muted">Plain card content with token-driven surface and border.</p>,
  },
}

export const WithTitle: Story = {
  args: {
    title: 'Pipeline analytics',
    description: 'Match score and status distribution across the active pipeline.',
    children: <p className="text-xs text-fg-muted">Body content sits beneath the header.</p>,
  },
}

export const WithActions: Story = {
  args: {
    title: 'Resume builds',
    description: 'Last 7 days',
    actions: <Button size="sm" variant="ghost">View all</Button>,
    children: <p className="text-xs text-fg-muted">Action slot is right-aligned in the header.</p>,
  },
}

export const StatGrid: Story = {
  args: {
    padding: 'lg',
    children: (
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
        <Stat label="Today" value="$12.83" hint="42 calls" />
        <Stat label="Last 7 days" value="$87.10" hint="318 calls" />
        <Stat label="Last 30 days" value="$394.40" hint="1,210 calls" tone="success" />
        <Stat label="Avg / build" value="$3.21" hint="across 27 builds" />
      </dl>
    ),
  },
}

export const ToneSuccess: Story = { args: { tone: 'success', title: 'Build converged', children: 'Polisher score 89.' } }
export const ToneWarning: Story = { args: { tone: 'warning', title: 'Persona quality low', children: 'Run synthesizer.' } }
export const ToneDanger:  Story = { args: { tone: 'danger',  title: 'Pipeline failed',  children: 'Check API connection.' } }
