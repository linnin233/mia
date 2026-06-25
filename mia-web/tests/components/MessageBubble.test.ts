import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageBubble from '@/components/chat/MessageBubble.vue'

describe('MessageBubble', () => {
  it('renders user message correctly', () => {
    const msg = { id: '1', role: 'user' as const, content: 'Hello', timestamp: Date.now() }
    const wrapper = mount(MessageBubble, { props: { msg } })
    expect(wrapper.text()).toContain('Hello')
    expect(wrapper.text()).toContain('You')
  })

  it('renders assistant message correctly', () => {
    const msg = { id: '1', role: 'assistant' as const, content: 'Hi there', timestamp: Date.now() }
    const wrapper = mount(MessageBubble, { props: { msg } })
    expect(wrapper.text()).toContain('Hi there')
    expect(wrapper.text()).toContain('MIA')
  })

  it('renders multiline content', () => {
    const msg = { id: '1', role: 'assistant' as const, content: 'Line1\nLine2', timestamp: Date.now() }
    const wrapper = mount(MessageBubble, { props: { msg } })
    expect(wrapper.text()).toContain('Line1')
    expect(wrapper.text()).toContain('Line2')
  })
})
