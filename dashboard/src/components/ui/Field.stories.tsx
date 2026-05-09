import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { TextInput, Select, Textarea } from './Field'

const meta = {
  title: 'Primitives/Field',
  parameters: { layout: 'padded' },
} satisfies Meta

export default meta
type Story = StoryObj

export const TextInputBasic: Story = {
  render: () => (
    <div className="space-y-3 max-w-sm">
      <TextInput label="Email" type="email" placeholder="you@example.com" />
      <TextInput label="Search" type="search" placeholder="Search jobs…" hint="Use quotes for exact match." />
      <TextInput label="Phone" type="tel" placeholder="+971…" error="Invalid format" />
    </div>
  ),
}

export const SelectBasic: Story = {
  render: () => (
    <div className="space-y-3 max-w-sm">
      <Select label="Status">
        <option value="all">All statuses</option>
        <option value="new">New</option>
        <option value="evaluated">Evaluated</option>
        <option value="applied">Applied</option>
      </Select>
      <Select label="Min match score" hint="Filters out weaker matches.">
        <option value={0}>All scores</option>
        <option value={60}>60+</option>
        <option value={75}>75+</option>
        <option value={85}>85+</option>
      </Select>
    </div>
  ),
}

export const TextareaBasic: Story = {
  render: () => (
    <div className="space-y-3 max-w-sm">
      <Textarea label="Notes" placeholder="Anything noteworthy…" rows={3} />
    </div>
  ),
}
