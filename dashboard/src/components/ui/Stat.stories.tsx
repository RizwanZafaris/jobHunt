import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { Stat } from './Stat'
import { Card } from './Card'

const meta = {
  title: 'Primitives/Stat',
  component: Stat,
  tags: ['autodocs'],
  argTypes: {
    tone: { control: 'select', options: ['default', 'success', 'warning', 'danger'] },
  },
  args: { label: 'Today', value: '$12.83', hint: '42 calls' },
} satisfies Meta<typeof Stat>

export default meta
type Story = StoryObj<typeof meta>

export const Single: Story = {}

export const Tones: Story = {
  render: () => (
    <Card padding="lg">
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
        <Stat label="Total jobs" value="1,284" hint="38 today" />
        <Stat label="High match" value={63} hint="120 strong fits" tone="success" />
        <Stat label="Pending"   value={12} hint="needs review" tone="warning" />
        <Stat label="Failures"  value={3} hint="last 24h" tone="danger" />
      </dl>
    </Card>
  ),
}
