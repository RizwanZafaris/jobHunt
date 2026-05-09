import type { Meta, StoryObj } from '@storybook/nextjs-vite'
import { Button } from './Button'
import { Icon } from './Icon'

const meta = {
  title: 'Primitives/Button',
  component: Button,
  tags: ['autodocs'],
  argTypes: {
    variant: {
      control: 'select',
      options: ['primary', 'secondary', 'ghost', 'danger', 'success', 'warning'],
    },
    size: {
      control: 'select',
      options: ['xs', 'sm', 'md', 'lg'],
    },
    loading: { control: 'boolean' },
    disabled: { control: 'boolean' },
    block: { control: 'boolean' },
  },
  args: {
    children: 'Run pipeline',
    variant: 'primary',
    size: 'md',
  },
} satisfies Meta<typeof Button>

export default meta
type Story = StoryObj<typeof meta>

export const Primary: Story = {}

export const Secondary: Story = {
  args: { variant: 'secondary', children: 'Boss audit' },
}

export const Ghost: Story = {
  args: { variant: 'ghost', children: 'Cancel' },
}

export const Danger: Story = {
  args: { variant: 'danger', children: 'Delete persona' },
}

export const Success: Story = {
  args: { variant: 'success', children: 'Mark accepted' },
}

export const Warning: Story = {
  args: { variant: 'warning', children: 'Force build anyway' },
}

export const WithIcon: Story = {
  args: {
    children: (
      <>
        <Icon name="play" size={12} />
        Run pipeline
      </>
    ),
  },
}

export const Loading: Story = {
  args: { loading: true, children: 'Generating' },
}

export const Disabled: Story = {
  args: { disabled: true, children: 'Disabled' },
}

export const Sizes: Story = {
  render: (args) => (
    <div className="flex items-center gap-2">
      <Button {...args} size="xs">Extra small</Button>
      <Button {...args} size="sm">Small</Button>
      <Button {...args} size="md">Medium</Button>
      <Button {...args} size="lg">Large</Button>
    </div>
  ),
}

export const AllVariants: Story = {
  render: () => (
    <div className="grid grid-cols-2 gap-2">
      <Button variant="primary">Primary</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="danger">Danger</Button>
      <Button variant="success">Success</Button>
      <Button variant="warning">Warning</Button>
    </div>
  ),
}
