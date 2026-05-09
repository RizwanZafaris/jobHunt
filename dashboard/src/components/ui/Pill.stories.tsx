import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { useState } from 'react'
import { Pill } from './Pill'

const meta = {
  title: 'Primitives/Pill',
  component: Pill,
  tags: ['autodocs'],
  argTypes: {
    tone: { control: 'select', options: ['neutral', 'success', 'warning', 'danger', 'info', 'accent'] },
    size: { control: 'select', options: ['xs', 'sm'] },
  },
  args: {
    children: 'High match',
    tone: 'success',
  },
} satisfies Meta<typeof Pill>

export default meta
type Story = StoryObj<typeof meta>

export const Tones: Story = {
  render: () => (
    <div className="flex gap-1.5 flex-wrap">
      <Pill tone="neutral">Neutral</Pill>
      <Pill tone="info">Info</Pill>
      <Pill tone="success">Success</Pill>
      <Pill tone="warning">Warning</Pill>
      <Pill tone="danger">Danger</Pill>
      <Pill tone="accent">Accent</Pill>
    </div>
  ),
}

export const ToggleButton: Story = {
  render: function Render() {
    const [v, setV] = useState<boolean | null>(null)
    return (
      <div className="flex gap-1">
        <Pill as="button" tone="success" pressed={v === true} onClick={() => setV(v === true ? null : true)} size="sm">Yes</Pill>
        <Pill as="button" tone="danger"  pressed={v === false} onClick={() => setV(v === false ? null : false)} size="sm">No</Pill>
        <Pill as="button" tone="neutral" pressed={v === null} onClick={() => setV(null)} size="sm" aria-label="Unknown">?</Pill>
      </div>
    )
  },
}
