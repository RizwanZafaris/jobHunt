import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { Icon, type IconName } from './Icon'

const NAMES: IconName[] = [
  'search', 'star', 'mail', 'building', 'target',
  'rocket', 'moon', 'sun', 'refresh', 'sparkles',
  'download', 'plus', 'minus', 'check', 'alert-triangle',
  'info', 'x', 'eye', 'menu', 'pencil',
  'arrow-right', 'arrow-up-right', 'chevron-down', 'chevron-up', 'chevron-right',
  'document', 'sliders', 'brain', 'currency', 'coffee',
  'play', 'briefcase', 'note', 'graph', 'link',
  'shield-check', 'shield-warning', 'circle-half', 'tag',
]

const meta = {
  title: 'Primitives/Icon',
  component: Icon,
  tags: ['autodocs'],
  argTypes: {
    name: { control: 'select', options: NAMES },
    size: { control: { type: 'range', min: 8, max: 64, step: 2 } },
  },
  args: { name: 'sparkles', size: 20 },
} satisfies Meta<typeof Icon>

export default meta
type Story = StoryObj<typeof meta>

export const Single: Story = {}

export const All: Story = {
  args: { name: 'sparkles' },
  render: () => (
    <div className="grid grid-cols-6 sm:grid-cols-8 gap-3">
      {NAMES.map((n) => (
        <div key={n} className="flex flex-col items-center gap-1 text-fg-muted">
          <Icon name={n} size={20} />
          <span className="text-2xs text-fg-subtle font-mono">{n}</span>
        </div>
      ))}
    </div>
  ),
}

export const Sizes: Story = {
  args: { name: 'star' },
  render: ({ name }) => (
    <div className="flex items-end gap-3 text-fg">
      {[12, 14, 16, 20, 24, 32, 48].map((s) => (
        <div key={s} className="flex flex-col items-center gap-1">
          <Icon name={name!} size={s} />
          <span className="text-2xs text-fg-subtle font-mono">{s}</span>
        </div>
      ))}
    </div>
  ),
}
