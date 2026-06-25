import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from '@/components/chat/ChatInput.vue'

describe('ChatInput', () => {
  it('emits send event with text content', async () => {
    const wrapper = mount(ChatInput, { props: { loading: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('Hello world')
    await textarea.trigger('keydown.enter')
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')![0]).toEqual(['Hello world'])
  })

  it('does not send empty message', async () => {
    const wrapper = mount(ChatInput, { props: { loading: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('   ')
    await textarea.trigger('keydown.enter')
    expect(wrapper.emitted('send')).toBeFalsy()
  })

  it('disables input when loading', () => {
    const wrapper = mount(ChatInput, { props: { loading: true } })
    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('disabled')).toBeDefined()
  })

  it('send button click emits event', async () => {
    const wrapper = mount(ChatInput, { props: { loading: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('Click send')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('send')![0]).toEqual(['Click send'])
  })
})
