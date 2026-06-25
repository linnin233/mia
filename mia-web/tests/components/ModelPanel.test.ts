import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ModelPanel from '@/components/config/ModelPanel.vue'

describe('ModelPanel', () => {
  it('renders component', () => {
    const wrapper = mount(ModelPanel)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays title', () => {
    const wrapper = mount(ModelPanel)
    expect(wrapper.text()).toContain('Model Registry')
  })
})
