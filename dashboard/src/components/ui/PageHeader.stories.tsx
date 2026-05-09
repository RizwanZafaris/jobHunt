import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { PageHeader } from './PageHeader'
import { Button } from './Button'
import { Pill } from './Pill'

const meta = {
  title: 'Primitives/PageHeader',
  component: PageHeader,
  tags: ['autodocs'],
  parameters: { layout: 'padded' },
} satisfies Meta<typeof PageHeader>

export default meta
type Story = StoryObj<typeof meta>

export const Basic: Story = {
  args: {
    eyebrow: 'Pipeline',
    title: "Today's job hunt",
    description: 'Live view of every job the agent network has discovered, scored, and prioritized.',
  },
}

export const WithActions: Story = {
  args: {
    eyebrow: 'Telemetry',
    title: 'LLM cost observability',
    description: 'Per-call cost and latency from agent_call_log.',
    actions: (
      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary">Export CSV</Button>
        <Button size="sm">Run report</Button>
      </div>
    ),
  },
}

export const WithPills: Story = {
  args: {
    eyebrow: 'Persona',
    title: 'Anthropic',
    description: 'v3 · last synthesized 3 days ago',
    actions: (
      <div className="flex gap-1.5">
        <Pill tone="success">High quality</Pill>
        <Pill>27 outcomes</Pill>
      </div>
    ),
  },
}
